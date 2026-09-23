"""가상시계·가속엔진 — 시퀀스 설계서 §1.3·§3 (자유진행 원칙).

- Run 시작(P3)~종료(P5)까지 노드 접속 여부와 무관하게 연속 진행 (free-running)
- 가상 1분 = 실 (가속배율/15) 초. 예: 배율 10 s → 실 0.667 s
- '일시정지'는 운영자의 명시적 조작에 한해서만 사용 (자동 예외처리 금지)
- 30일차 마지막 구간(인덱스 95) 완료 시점에 정지 (P5) → COMPLETED
- P6 케이스 전환 시 rewind()로 시작시각 되감기

틱 이벤트 순서(구간 경계 원자성의 기반): 1분 진행 → clock.minute →
(구간 변화 시) clock.interval → (자정 시) clock.day → (완료 시) clock.complete.
시나리오 배포자는 clock.minute에서 시각·기상을 먼저 갱신한 뒤 구간 인덱스를 갱신한다.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from ..config import SimulationConfig
from ..events import EventBus

logger = logging.getLogger(__name__)

MINUTES_PER_INTERVAL = 15
INTERVALS_PER_DAY = 96


class ClockState(str, Enum):
    IDLE = "idle"            # 시작 전 / 리셋(되감기) 후
    RUNNING = "running"
    PAUSED = "paused"        # 운영자 일시정지
    COMPLETED = "completed"  # 전 구간 완료 (P5)
    STOPPED = "stopped"      # 운영자 수동 정지


class VirtualClock:
    def __init__(self, sim: SimulationConfig, bus: EventBus) -> None:
        self._sim = sim
        self._bus = bus
        self._state = ClockState.IDLE
        self._virtual_time = sim.start_virtual_time
        self._elapsed_minutes = 0
        self._task: asyncio.Task | None = None
        self._resume_event = asyncio.Event()
        self._resume_event.set()

    # ── 조회 ──────────────────────────────────────────────
    @property
    def state(self) -> ClockState:
        return self._state

    @property
    def virtual_time(self) -> datetime:
        return self._virtual_time

    @property
    def interval_index(self) -> int:
        """하루 내 15분 구간 인덱스 0~95."""
        return (self._virtual_time.hour * 60 + self._virtual_time.minute) // MINUTES_PER_INTERVAL

    @property
    def day_number(self) -> int:
        """Run 내 일차 (1부터)."""
        return (self._virtual_time - self._sim.start_virtual_time).days + 1

    @property
    def total_minutes(self) -> int:
        return self._sim.days * 24 * 60

    @property
    def tick_period_s(self) -> float:
        return self._sim.real_seconds_per_virtual_minute

    def status(self) -> dict[str, Any]:
        return {
            "state": self._state.value,
            "virtual_time": self._virtual_time.isoformat(),
            "interval_index": self.interval_index,
            "day_number": self.day_number,
            "total_days": self._sim.days,
            "elapsed_minutes": self._elapsed_minutes,
            "total_minutes": self.total_minutes,
            "progress": round(self._elapsed_minutes / self.total_minutes, 4),
            "accel_seconds_per_15min": self._sim.accel_seconds_per_15min,
            "tick_period_s": round(self.tick_period_s, 4),
        }

    # ── 제어 (REST → P3/P5/P6) ────────────────────────────
    async def start(self) -> None:
        """P3 운전 개시 — IDLE에서만 시작."""
        if self._state != ClockState.IDLE:
            raise RuntimeError(f"IDLE 상태에서만 시작 가능 (현재: {self._state.value})")
        self._state = ClockState.RUNNING
        self._resume_event.set()
        self._task = asyncio.create_task(self._run_loop(), name="virtual-clock")
        await self._publish_state()
        logger.info("가상시계 시작: %s, 배율 %ss/15분", self._virtual_time, self._sim.accel_seconds_per_15min)

    async def stop(self) -> None:
        """운영자 수동 정지."""
        if self._state not in (ClockState.RUNNING, ClockState.PAUSED):
            raise RuntimeError(f"RUNNING/PAUSED 상태에서만 정지 가능 (현재: {self._state.value})")
        self._state = ClockState.STOPPED
        self._resume_event.set()
        await self._cancel_task()
        await self._publish_state()
        logger.info("가상시계 수동 정지: %s", self._virtual_time)

    async def pause(self) -> None:
        """운영자 일시정지 — 자동 예외처리로 호출 금지 (자유진행 원칙)."""
        if self._state != ClockState.RUNNING:
            raise RuntimeError(f"RUNNING 상태에서만 일시정지 가능 (현재: {self._state.value})")
        self._state = ClockState.PAUSED
        self._resume_event.clear()
        await self._publish_state()

    async def resume(self) -> None:
        if self._state != ClockState.PAUSED:
            raise RuntimeError(f"PAUSED 상태에서만 재개 가능 (현재: {self._state.value})")
        self._state = ClockState.RUNNING
        self._resume_event.set()
        await self._publish_state()

    async def rewind(self) -> None:
        """P6 케이스 전환 — 시작시각으로 되감기 (RUNNING 중에는 불가)."""
        if self._state == ClockState.RUNNING:
            raise RuntimeError("RUNNING 중에는 되감기 불가 — 먼저 정지하세요")
        await self._cancel_task()
        self._virtual_time = self._sim.start_virtual_time
        self._elapsed_minutes = 0
        self._state = ClockState.IDLE
        self._resume_event.set()
        await self._publish_state()
        logger.info("가상시계 되감기: %s", self._virtual_time)

    async def apply_config(self, sim: SimulationConfig) -> None:
        """설정 대시보드 반영 — IDLE(대기) 상태에서만. 가상시각을 새 시작시각으로 재설정."""
        if self._state != ClockState.IDLE:
            raise RuntimeError(
                f"설정 변경은 대기(IDLE) 상태에서만 가능 (현재: {self._state.value}) — 정지 후 되감기 하세요"
            )
        self._sim = sim
        self._virtual_time = sim.start_virtual_time
        self._elapsed_minutes = 0
        await self._publish_state()
        logger.info("시뮬레이션 설정 반영: 시작 %s, %d일, 배율 %ss/15분",
                    sim.start_virtual_time, sim.days, sim.accel_seconds_per_15min)

    async def shutdown(self) -> None:
        await self._cancel_task()

    # ── 진행 ──────────────────────────────────────────────
    async def advance_minute(self) -> None:
        """가상 1분 진행 + 이벤트 발행. 루프와 테스트가 공용."""
        prev_interval = self.interval_index
        self._virtual_time += timedelta(minutes=1)
        self._elapsed_minutes += 1

        payload = {
            "virtual_time": self._virtual_time.isoformat(),
            "interval_index": self.interval_index,
            "day_number": self.day_number,
            "elapsed_minutes": self._elapsed_minutes,
        }
        await self._bus.publish("clock.minute", payload)
        if self.interval_index != prev_interval:
            await self._bus.publish("clock.interval", payload)
        if self._virtual_time.hour == 0 and self._virtual_time.minute == 0:
            await self._bus.publish("clock.day", payload)

        if self._elapsed_minutes >= self.total_minutes:
            self._state = ClockState.COMPLETED
            await self._bus.publish("clock.complete", self.status())
            logger.info("Run 완료: 전 %d구간 종료", self._sim.days * INTERVALS_PER_DAY)

    async def _run_loop(self) -> None:
        """드리프트 보정 틱 루프 — 기준 실시각 + n×주기로 스케줄."""
        loop = asyncio.get_running_loop()
        period = self.tick_period_s
        base = loop.time()
        n = 0
        try:
            while self._state in (ClockState.RUNNING, ClockState.PAUSED):
                await self._resume_event.wait()
                if self._state != ClockState.RUNNING:
                    break
                n += 1
                delay = base + n * period - loop.time()
                if delay > 0:
                    await asyncio.sleep(delay)
                elif delay < -period * 10:
                    # 일시정지 등으로 크게 뒤처짐 — 기준 재설정 (몰아치기 방지)
                    base = loop.time() - n * period
                if self._state != ClockState.RUNNING:
                    continue
                await self.advance_minute()
            await self._publish_state()
        except asyncio.CancelledError:
            pass

    async def _publish_state(self) -> None:
        await self._bus.publish("clock.state", self.status())

    async def _cancel_task(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
