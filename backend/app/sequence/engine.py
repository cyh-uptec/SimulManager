"""시퀀스 엔진 — 설계서 §3 P0~P6 상태머신·연결중개·핸드셰이크·환경 배포·§6 예외.

매니저 원칙 (docs/PROTOCOL_RULES.md):
- 가상시계 자유진행: 어떤 예외도 시계를 멈추지 않는다 ('일시정지'는 운영자 전용)
- 운영 제어 비개입: 환경 전달·연결중개·기록만 수행
- 구간 경계 원자성: 시각·기상·부하 갱신 후 구간 인덱스를 마지막에 갱신

이벤트 구독:
  clock.minute   → P4(a) 가상 1분 환경 배포
  clock.complete → P5 종료·정산 (보존 SOC 저장)
  modbus.write   → P1 연결중개(③Coil0), P2 SOC ack(③Coil1)
  watch.alive/lost → ③IR 300003 'EMS 상태', ①Coil4 'RTDS 준비됨', 단절 구간 누적
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from ..clock import ClockState, VirtualClock
from ..config import AppConfig
from ..events import EventBus
from ..historian import HistorianStore, SequenceLogStore
from ..modbus import RegisterBank
from ..protocol import LINK1, LINK3
from ..protocol.types import Link
from ..scenario.mapper import EnvironmentSampler
from ..watch import HeartbeatMonitor

logger = logging.getLogger(__name__)

SCENARIO_RUN_LABEL = {1: "A", 2: "B"}


class RunPhase(str, Enum):
    P0_IDLE = "P0"        # 준비·대기
    P4_RUNNING = "P4"     # 정상 운전 루프 (P2·P3 절차는 시작 시 일괄 수행)
    P5_COMPLETED = "P5"   # 전 구간 완료·정산
    STOPPED = "stopped"   # 운영자 수동 정지


class SequenceEngine:
    def __init__(self, config: AppConfig, bus: EventBus, clock: VirtualClock,
                 banks: dict[Link, RegisterBank], monitor: HeartbeatMonitor,
                 sampler: EnvironmentSampler, data_dir: Path) -> None:
        self._config = config
        self._bus = bus
        self._clock = clock
        self._bank1 = banks[Link.LINK1]
        self._bank3 = banks[Link.LINK3]
        self._monitor = monitor
        self._sampler = sampler
        self._persisted_soc_path = data_dir / "persisted_soc.json"

        self._phase = RunPhase.P0_IDLE
        self._scenario_id = 0
        self._run_started_at_virtual: datetime | None = None
        self._soc_injection_pending = False
        self._disconnect_intervals: set[int] = set()
        self._seq_store = SequenceLogStore(data_dir / "historian.db")
        self.historian = HistorianStore(data_dir / "historian.db")  # Run 태깅·시계열 (Phase 5)
        self.current_run_id: int | None = None
        self._di_state: dict[str, int] = {}  # EMS DI 상태 변화 감지용 (반복 로그 방지)

        bus.subscribe("clock.minute", self._on_minute)
        bus.subscribe("clock.interval", self._on_interval)
        bus.subscribe("clock.complete", self._on_complete)
        bus.subscribe("modbus.write", self._on_modbus_write)
        bus.subscribe("watch.alive", self._on_watch)
        bus.subscribe("watch.lost", self._on_watch)

    # ── 상태 조회 ───────────────────────────────────────────
    def run_context(self) -> tuple[int | None, str]:
        """Historian 레코더용 — (현재 run_id, 가상시각 ISO)."""
        return self.current_run_id, self._clock.virtual_time.isoformat(timespec="minutes")

    def status(self) -> dict[str, Any]:
        return {
            "phase": self._phase.value,
            "run_id": self.current_run_id,
            "scenario_id": self._scenario_id,
            "run_label": SCENARIO_RUN_LABEL.get(self._scenario_id),
            "soc_injection_pending": self._soc_injection_pending,
            "disconnect_intervals": len(self._disconnect_intervals),
            "disconnect_budget": self._config.timing.disconnect_budget_intervals,
            "data_ready": self._sampler.ready(),
        }

    def sequence_log_page(self, offset: int = 0, limit: int = 50) -> dict[str, Any]:
        """시퀀스 로그 페이지 조회 (최신순, SQLite 영속화)."""
        return self._seq_store.page(offset=offset, limit=limit)

    async def sequence_log_clear(self) -> int:
        """시퀀스 로그 전체 삭제 (운영자 조작) — Run 계측 이력(historian)은 유지."""
        deleted = self._seq_store.clear()
        await self._bus.publish("sequence.cleared", {"deleted": deleted})
        return deleted

    # ── 운영자 제어 (REST) ──────────────────────────────────
    async def start_run(self, scenario_id: int) -> None:
        """P2 초기화 + P3 운전 개시 (설계서 §3.3~3.4)."""
        if self._clock.state != ClockState.IDLE:
            raise RuntimeError(f"Run 시작은 대기(IDLE) 상태에서만 가능 (현재: {self._clock.state.value})")
        if not self._sampler.ready():
            raise RuntimeError("부하·기상 데이터가 로드되지 않았습니다 — 데이터 메뉴에서 업로드하세요")

        sim = self._config.simulation
        self._scenario_id = scenario_id
        self._disconnect_intervals.clear()
        run_label = SCENARIO_RUN_LABEL.get(scenario_id, str(scenario_id))

        # 리셋 비트 해제 (P6 잔여) + 정지 비트 해제
        self._bank1.set("reset", 0)
        self._bank3.set("reset", 0)
        self._bank1.set("run_stop", 0)

        # P2-1: 초기 SOC 주입 트리거 (Run B도 재주입 — 공정 비교)
        self._bank3.set("initial_soc", sim.initial_soc)
        self._bank3.set("initial_soc_trigger", 1)
        self._soc_injection_pending = True

        # P2-5: 시나리오 ID·가속배율·시작시각
        self._bank1.set("scenario_id", scenario_id)
        self._bank3.set("scenario_id", scenario_id)
        self._bank1.set("accel_factor", sim.accel_seconds_per_15min)
        self._bank3.set("accel_factor", sim.accel_seconds_per_15min)
        self._run_started_at_virtual = sim.start_virtual_time
        self._distribute(sim.start_virtual_time)  # t0 환경 선배포
        await self._log(
            f"P2 초기화 — Run {run_label}: 시나리오 ID {scenario_id}·가속배율 "
            f"{sim.accel_seconds_per_15min}s 배포, 초기 SOC {sim.initial_soc}% 주입 트리거 세트",
            refs=[str(LINK1["scenario_id"]), str(LINK1["accel_factor"]),
                  str(LINK3["initial_soc"]), str(LINK3["initial_soc_trigger"])],
            step="P2",
        )

        # Historian Run 태깅 (Phase 5)
        self.current_run_id = self.historian.start_run(
            label=run_label, scenario_id=scenario_id,
            started_real=datetime.now().isoformat(timespec="seconds"),
            started_virtual=sim.start_virtual_time.isoformat(timespec="minutes"),
            initial_soc=sim.initial_soc,
        )

        # P3: 운전 개시 — Enable → 운전시작 → 가상시계 기동(자유진행)
        self._bank1.set("ems_enable", 1)
        self._bank3.set("ess_run_enable_sim", 1)
        self._bank1.set("run_start", 1)
        await self._clock.start()

        self._phase = RunPhase.P4_RUNNING
        await self._log(
            f"P3 운전 개시 — Run {run_label} 가상시계 기동 (자유진행, 종료까지 정지 없음)",
            refs=[str(LINK1["ems_enable"]), str(LINK1["run_start"]),
                  str(LINK3["ess_run_enable_sim"])],
            step="P3",
        )
        await self._publish_state()

    async def stop_run(self) -> None:
        """운영자 수동 정지."""
        if self._clock.state not in (ClockState.RUNNING, ClockState.PAUSED):
            raise RuntimeError("운전 중에만 정지할 수 있습니다")
        await self._clock.stop()
        await self._end_run(RunPhase.STOPPED, "운영자 수동 정지")

    async def reset_run(self, next_scenario_id: int | None = None) -> None:
        """P6 케이스 전환 — 리셋 비트·시계 되감기·시나리오 변경 (설계서 §3.7)."""
        if self._clock.state == ClockState.RUNNING:
            raise RuntimeError("운전 중에는 리셋할 수 없습니다 — 먼저 정지하세요")
        # 6-1: 양 노드 리셋 비트 세트 (다음 Run 시작 시 해제)
        self._bank1.set("reset", 1)
        self._bank3.set("reset", 1)
        # 6-2: 시계 되감기 + 운전 비트 정리
        await self._clock.rewind()
        self._bank1.set("run_start", 0)
        self._bank1.set("run_stop", 0)
        self._bank1.set("ems_enable", 0)
        self._bank3.set("ess_run_enable_sim", 0)
        self._bank3.set("initial_soc_trigger", 0)
        self._soc_injection_pending = False
        if next_scenario_id is not None:
            self._scenario_id = next_scenario_id
            self._bank1.set("scenario_id", next_scenario_id)
            self._bank3.set("scenario_id", next_scenario_id)
        self._phase = RunPhase.P0_IDLE
        self._disconnect_intervals.clear()
        await self._log(
            f"P6 리셋·케이스 전환 — 시계 되감기, 시나리오 ID "
            f"{next_scenario_id if next_scenario_id is not None else self._scenario_id}",
            refs=[str(LINK1["reset"]), str(LINK3["reset"]), str(LINK1["scenario_id"])],
            step="P6",
        )
        await self._publish_state()

    # ── 이벤트 핸들러 ───────────────────────────────────────
    async def _on_minute(self, _topic: str, payload: dict[str, Any]) -> None:
        """P4(a) 가상 1분 사이클 — 환경 배포."""
        if self._phase != RunPhase.P4_RUNNING:
            return
        virtual_time = datetime.fromisoformat(payload["virtual_time"])
        self._distribute(virtual_time)

    async def _on_interval(self, _topic: str, payload: dict[str, Any]) -> None:
        """구간 경계 — 단절 '지속' 구간 누적 (§6: 단절 상태로 경과한 구간 수 기준)."""
        if self._phase != RunPhase.P4_RUNNING:
            return
        states = self._monitor.status()
        if any(node["state"] == "lost" for node in states.values()):
            global_interval = (payload["day_number"] - 1) * 96 + payload["interval_index"]
            await self._count_disconnect(global_interval)

    async def _on_complete(self, _topic: str, _payload: dict[str, Any]) -> None:
        """P5 — 전 구간 완료."""
        await self._end_run(RunPhase.P5_COMPLETED, "전 구간 완료 (P5)")

    async def _on_modbus_write(self, _topic: str, payload: dict[str, Any]) -> None:
        link, key = payload.get("link"), payload.get("key")
        if link == int(Link.LINK3) and key == "rtds_running":
            # P1 연결중개: RTDS 운전중 → EMS에 'RTDS 준비됨' 통지
            running = bool(payload["values"][0])
            self._bank1.set("rtds_ready", 1 if running else 0)
            await self._log(
                f"연결중개: RTDS 운전중={int(running)} → ①'RTDS 준비됨'={int(running)}",
                refs=[str(LINK3["rtds_running"]), str(LINK1["rtds_ready"])],
                source="rtds", step="P1",
            )
        elif link == int(Link.LINK3) and key == "initial_soc_ack":
            # P2 핸드셰이크: ack 확인 → 트리거 해제 (레벨 핸드셰이크)
            if bool(payload["values"][0]) and self._soc_injection_pending:
                self._bank3.set("initial_soc_trigger", 0)
                self._soc_injection_pending = False
                await self._log(
                    "초기 SOC 반영완료(ack) 수신 — 주입 트리거 해제 (P2 핸드셰이크 완결)",
                    refs=[str(LINK3["initial_soc_ack"]), str(LINK3["initial_soc_trigger"])],
                    source="rtds", step="P2",
                )
        elif link == int(Link.LINK1) and key == "forecast_base_interval":
            # EMS 예측 블록 기록 (B-4) — 15분 구간마다
            await self._log(
                f"EMS 예측 블록 기록 — 기준 구간 {payload.get('physical')}, [1구간](PV·부하·스케줄·목표SOC, [2~4구간]은 예약)",
                refs=[str(LINK1["forecast_base_interval"]), "①IR 300003~300006 (미러 HR 430002~430006, 300007~300018 예약)"],
                source="ems",
            )
        elif link == int(Link.LINK1) and payload.get("obj") == "di" and key:
            # EMS 상태 DI 변화 (준비완료·연산중·예측완료·알람) — 변화 시에만
            value = 1 if payload["values"][0] else 0
            if self._di_state.get(key) != value:
                self._di_state[key] = value
                reg = LINK1[key]
                await self._log(f"EMS 상태 기록: {reg.name}={value}",
                                refs=[str(reg)], source="ems")

    async def _on_watch(self, topic: str, payload: dict[str, Any]) -> None:
        """§6 예외 — 시계는 계속 진행, 상태 레지스터·단절 집계만 갱신."""
        node = payload["node"]
        alive = topic == "watch.alive"
        if node == "ems":
            self._bank3.set("ems_status", 1 if alive else 0)  # ③IR 300003
            await self._log(
                f"EMS {'연결' if alive else '단절'} → ③'EMS 상태'={int(alive)}"
                + ("" if alive else " (RTDS는 워치독 정체로 자체 안전상태 P=0)"),
                refs=[str(LINK1["ems_heartbeat"]), str(LINK3["ems_status"])],
                step="P1" if alive else None,
            )
        else:  # rtds
            if not alive:
                self._bank1.set("rtds_ready", 0)  # 재접속 시엔 RTDS의 ③Coil0 기록으로 복구 (R-3)
            await self._log(
                f"RTDS {'연결' if alive else '단절'}"
                + ("" if alive else " → ①'RTDS 준비됨'=0, EMS 지령 중단 안내"),
                refs=[str(LINK3["rtds_heartbeat"])] + ([] if alive else [str(LINK1["rtds_ready"])]),
                step="P1" if alive else None,
            )
        if not alive and self._phase == RunPhase.P4_RUNNING:
            # 전이 시점 구간 즉시 누적 — 이후 지속 단절은 _on_interval이 구간마다 누적
            global_interval = (self._clock.day_number - 1) * 96 + self._clock.interval_index
            await self._count_disconnect(global_interval)
        await self._publish_state()

    async def _count_disconnect(self, global_interval: int) -> None:
        budget = self._config.timing.disconnect_budget_intervals
        was_within = len(self._disconnect_intervals) <= budget
        self._disconnect_intervals.add(global_interval)
        if was_within and len(self._disconnect_intervals) > budget:
            await self._log(f"⚠ 단절 누적 {len(self._disconnect_intervals)}구간 — "
                            f"허용 한도({budget}) 초과, Run 무효 검토 필요 (설계서 §6)")

    # ── 내부 ────────────────────────────────────────────────
    def _distribute(self, virtual_time: datetime) -> None:
        """가상 1분 환경 배포 — 시각·기상·부하 먼저, 구간 인덱스는 마지막(원자성)."""
        if self._run_started_at_virtual is None:
            return
        env = self._sampler.sample(virtual_time, self._run_started_at_virtual)

        # 1) 링크① 시각
        self._bank1.set("sim_year", virtual_time.year)
        self._bank1.set("sim_month", virtual_time.month)
        self._bank1.set("sim_day", virtual_time.day)
        self._bank1.set("sim_hour", virtual_time.hour)
        self._bank1.set("sim_minute", virtual_time.minute)
        # 2) 기상 (링크①·③ — 스케일은 protocol codec이 링크별 적용, 일사는 MJ/m² v1.0)
        self._bank1.set("irradiance", env.irradiance_mj)
        self._bank1.set("ambient_temp", env.temp_c)
        self._bank1.set("snow_depth", env.snow_cm)
        self._bank1.set("weather_code", env.weather_code)
        self._bank3.set("irradiance", env.irradiance_mj)
        self._bank3.set("ambient_temp", env.temp_c)
        self._bank3.set("snow_depth", env.snow_cm)
        self._bank3.set("weather_code", env.weather_code)
        # 3) 부하 프로파일 — RTDS로만 (EMS 블라인드)
        self._bank3.set("load_profile_ref", env.load_kw)
        # 4) 구간 인덱스 — 반드시 마지막 (EMS의 구간 변화 트리거 정합)
        interval = (virtual_time.hour * 60 + virtual_time.minute) // 15
        self._bank1.set("interval_index", interval)
        self._bank3.set("interval_index", interval)

    async def _end_run(self, phase: RunPhase, reason: str) -> None:
        """P5 종료·정산 공통 — 운전 비트 정리·최종 SOC 보존."""
        self._bank1.set("run_start", 0)
        self._bank1.set("run_stop", 1)
        self._bank3.set("ess_run_enable_sim", 0)
        # 주입 미완 트리거 잔존 방지 — 종료된 Run의 SOC를 늦게 주입하지 않도록
        self._bank3.set("initial_soc_trigger", 0)
        self._soc_injection_pending = False
        final_soc = self._bank3.get("ess_soc")  # ③HR 400003 — RTDS가 기록한 보존 SOC 갱신원
        self._save_persisted_soc(float(final_soc))
        if self.current_run_id is not None:  # Historian Run 마감
            self.historian.end_run(
                self.current_run_id,
                status="completed" if phase == RunPhase.P5_COMPLETED else "stopped",
                ended_real=datetime.now().isoformat(timespec="seconds"),
                ended_virtual=self._clock.virtual_time.isoformat(timespec="minutes"),
                final_soc=float(final_soc),
                disconnect_intervals=len(self._disconnect_intervals),
            )
            self.current_run_id = None
        self._phase = phase
        await self._log(
            f"P5 종료·정산({reason}) — 최종 SOC {final_soc}% 보존, "
            f"단절 누적 {len(self._disconnect_intervals)}구간",
            refs=[str(LINK1["run_stop"]), str(LINK3["ess_soc"])],
            step="P5",
        )
        await self._publish_state()

    def _save_persisted_soc(self, soc: float) -> None:
        self._persisted_soc_path.write_text(
            json.dumps({"soc": soc, "saved_at": datetime.now().isoformat(timespec="seconds"),
                        "scenario_id": self._scenario_id}, ensure_ascii=False),
            encoding="utf-8",
        )

    async def _log(self, message: str, refs: list[str] | None = None,
                   source: str = "manager", step: str | None = None) -> None:
        """시퀀스 로그 — refs: 프로토콜 레지스터 참조(①②③+주소, 검증용).

        step: 설계서 §3의 시퀀스 단계 라벨(P1~P6). 생략 시 엔진 현재 상태(P0/P4/P5).
        P1(연결중개)·P2(핸드셰이크)·P3(개시)·P6(리셋)은 상태가 아닌 '절차'라서
        발생 이벤트에 단계 라벨을 직접 부여한다.
        """
        entry = {
            "real_time": datetime.now().isoformat(timespec="seconds"),
            "virtual_time": self._clock.virtual_time.isoformat(timespec="minutes"),
            "phase": step or self._phase.value,
            "source": source,
            "message": message,
            "refs": " · ".join(refs) if refs else "",
        }
        entry["id"] = self._seq_store.append(entry)
        logger.info("[시퀀스] %s", message)
        await self._bus.publish("sequence.event", entry)

    async def _publish_state(self) -> None:
        await self._bus.publish("run.state", self.status())
