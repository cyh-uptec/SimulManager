"""SequenceEngine 테스트 — P1 연결중개·P2 핸드셰이크·P4 배포(원자성)·P5·P6·§6 예외."""
from datetime import datetime

import pandas as pd
import pytest

from app.clock import ClockState, VirtualClock
from app.config import AppConfig, SimulationConfig, TimingConfig
from app.events import EventBus
from app.modbus import RegisterBank
from app.protocol import LINK1, LINK3
from app.protocol.types import Link
from app.scenario import ScenarioStore
from app.scenario.events import WeatherEventStore
from app.scenario.mapper import EnvironmentSampler
from app.sequence import RunPhase, SequenceEngine
from app.watch import HeartbeatMonitor

START = datetime(2025, 7, 1, 0, 0)


def sample_data(store: ScenarioStore) -> None:
    ts = pd.date_range("2025-10-01 00:00", periods=200, freq="15min")
    store.load_upload("load", "l.csv", pd.DataFrame(
        {"Timestamp": ts, "Load_kW": [30.0 + i % 10 for i in range(200)]}).to_csv(index=False).encode())
    th = pd.date_range("2025-10-01 00:00", periods=60, freq="1h")
    store.load_upload("weather", "w.csv", pd.DataFrame({
        "일시": th, "기온": [15.0] * 60, "풍속": [1] * 60, "습도": [50] * 60,
        "일조": [0.5] * 60, "일사": [1.8] * 60, "전운량": [0] * 60, "중하층운량": [0] * 60,
    }).to_csv(index=False).encode())


async def make_clock_env(tmp_path):
    """다른 테스트 모듈에서 재사용하는 함수형 헬퍼 — (clock, banks, engine) 반환."""
    config = AppConfig()
    config.simulation = SimulationConfig(start_virtual_time=START, days=1,
                                         accel_seconds_per_15min=900.0)
    config.timing = TimingConfig(alive_timeout_s=0.5, heartbeat_period_s=0.1)
    bus = EventBus()
    clock = VirtualClock(config.simulation, bus)
    banks = {Link.LINK1: RegisterBank(LINK1, bus), Link.LINK3: RegisterBank(LINK3, bus)}
    monitor = HeartbeatMonitor(bus, config.timing)
    store = ScenarioStore(tmp_path)
    sample_data(store)
    sampler = EnvironmentSampler(store, WeatherEventStore(tmp_path), "hold")
    engine = SequenceEngine(config, bus, clock, banks, monitor, sampler, tmp_path)
    return clock, banks, engine


@pytest.fixture
def env(tmp_path):
    config = AppConfig()
    config.simulation = SimulationConfig(start_virtual_time=START, days=1,
                                         accel_seconds_per_15min=900.0)  # 틱 60s — 자연 틱 없음
    config.timing = TimingConfig(alive_timeout_s=0.5, heartbeat_period_s=0.1)
    bus = EventBus()
    clock = VirtualClock(config.simulation, bus)
    banks = {Link.LINK1: RegisterBank(LINK1, bus), Link.LINK3: RegisterBank(LINK3, bus)}
    monitor = HeartbeatMonitor(bus, config.timing)
    store = ScenarioStore(tmp_path)
    sample_data(store)
    events = WeatherEventStore(tmp_path)
    sampler = EnvironmentSampler(store, events, "hold")
    engine = SequenceEngine(config, bus, clock, banks, monitor, sampler, tmp_path)
    return config, bus, clock, banks, engine, tmp_path


class TestStartRun:
    async def test_p2_p3_registers(self, env):
        _, _, clock, banks, engine, _ = env
        await engine.start_run(scenario_id=1)
        b1, b3 = banks[Link.LINK1], banks[Link.LINK3]
        assert b1.get("scenario_id") == 1 and b3.get("scenario_id") == 1
        assert b3.get("initial_soc") == 50.0
        assert b3.get("initial_soc_trigger") == 1     # P2 주입 트리거
        assert b1.get("ems_enable") == 1 and b1.get("run_start") == 1  # P3
        assert b3.get("ess_run_enable_sim") == 1
        assert clock.state == ClockState.RUNNING
        assert engine.status()["phase"] == "P4"
        # t0 환경 선배포 확인 (부하 30.0, 일사 1.8 MJ/m² → ×1 인코딩 반올림 = 2)
        assert b3.get("load_profile_ref") == 30.0
        assert b1.get("irradiance") == 2
        await clock.stop()

    async def test_start_requires_data(self, env, tmp_path):
        config, bus, clock, banks, _, _ = env
        empty_store = ScenarioStore(tmp_path / "empty")
        sampler = EnvironmentSampler(empty_store, WeatherEventStore(tmp_path / "empty"))
        engine2 = SequenceEngine(config, EventBus(), clock, banks,
                                 HeartbeatMonitor(EventBus(), config.timing), sampler, tmp_path)
        with pytest.raises(RuntimeError, match="데이터"):
            await engine2.start_run(1)


class TestBrokering:
    async def test_rtds_running_sets_ready(self, env):
        _, _, _, banks, engine, _ = env
        banks[Link.LINK3].ctx.setValues(5, 0, [1])  # RTDS가 ③Coil0 '운전중'=1 (FC05)
        import asyncio; await asyncio.sleep(0)
        assert banks[Link.LINK1].get("rtds_ready") == 1
        banks[Link.LINK3].ctx.setValues(5, 0, [0])
        await asyncio.sleep(0)
        assert banks[Link.LINK1].get("rtds_ready") == 0

    async def test_soc_ack_clears_trigger(self, env):
        _, _, clock, banks, engine, _ = env
        await engine.start_run(1)
        assert banks[Link.LINK3].get("initial_soc_trigger") == 1
        banks[Link.LINK3].ctx.setValues(5, 1, [1])  # ③Coil1 ack=1
        import asyncio; await asyncio.sleep(0)
        assert banks[Link.LINK3].get("initial_soc_trigger") == 0
        assert engine.status()["soc_injection_pending"] is False
        await clock.stop()


class TestDistribution:
    async def test_minute_updates_environment(self, env):
        _, _, clock, banks, engine, _ = env
        await engine.start_run(1)
        for _ in range(16):  # 16분 진행 → 구간 1, 부하는 15분 경과값(31.0)
            await clock.advance_minute()
        b1, b3 = banks[Link.LINK1], banks[Link.LINK3]
        assert b1.get("sim_minute") == 16
        assert b1.get("interval_index") == 1 and b3.get("interval_index") == 1
        assert b3.get("load_profile_ref") == 31.0
        assert b1.get("ambient_temp") == 15.0 and b3.get("ambient_temp") == 15.0
        await clock.stop()

    async def test_boundary_atomicity_order(self, env):
        """구간 경계 원자성 — 시각·기상·부하 갱신 후 interval_index가 마지막."""
        _, _, clock, banks, engine, _ = env
        await engine.start_run(1)
        order: list[str] = []
        for bank in banks.values():
            original = bank.set
            def spy(key, value, _orig=original):
                order.append(key)
                _orig(key, value)
            bank.set = spy  # type: ignore[method-assign]
        for _ in range(15):  # 15분째 → 구간 경계
            order.clear()
            await clock.advance_minute()
        # 마지막 틱(경계)의 쓰기 순서: interval_index 2건이 맨 끝
        assert order[-2:] == ["interval_index", "interval_index"]
        env_keys = [k for k in order if k in ("irradiance", "load_profile_ref", "sim_minute")]
        assert env_keys, "환경 레지스터가 갱신되어야 함"
        assert all(order.index(k) < order.index("interval_index") for k in env_keys)
        await clock.stop()


class TestEndAndReset:
    async def test_complete_saves_persisted_soc(self, env):
        _, _, clock, banks, engine, tmp_path = env
        await engine.start_run(1)
        banks[Link.LINK3].set("ess_soc", 63.5)  # RTDS가 기록했다고 가정
        for _ in range(24 * 60):  # 1일 Run 완주
            await clock.advance_minute()
        assert clock.state == ClockState.COMPLETED
        assert engine.status()["phase"] == "P5"
        b1 = banks[Link.LINK1]
        assert b1.get("run_start") == 0 and b1.get("run_stop") == 1
        import json
        saved = json.loads((tmp_path / "persisted_soc.json").read_text(encoding="utf-8"))
        assert saved["soc"] == 63.5

    async def test_p6_reset_and_case_switch(self, env):
        _, _, clock, banks, engine, _ = env
        await engine.start_run(1)
        await engine.stop_run()
        await engine.reset_run(next_scenario_id=2)
        b1, b3 = banks[Link.LINK1], banks[Link.LINK3]
        assert b1.get("reset") == 1 and b3.get("reset") == 1
        assert b1.get("scenario_id") == 2 and b3.get("scenario_id") == 2
        assert clock.state == ClockState.IDLE
        assert engine.status()["phase"] == "P0"
        # Run B 시작 시 리셋 해제 + 초기 SOC 재주입 (공정 비교)
        await engine.start_run(2)
        assert b1.get("reset") == 0 and b3.get("reset") == 0
        assert b3.get("initial_soc_trigger") == 1
        await clock.stop()

    async def test_reset_blocked_while_running(self, env):
        _, _, clock, _, engine, _ = env
        await engine.start_run(1)
        with pytest.raises(RuntimeError):
            await engine.reset_run()
        await clock.stop()


class TestExceptions:
    async def test_ems_lost_sets_status(self, env):
        _, bus, clock, banks, engine, _ = env
        await engine.start_run(1)
        await bus.publish("watch.lost", {"node": "ems", "state": "lost"})
        assert banks[Link.LINK3].get("ems_status") == 0
        assert clock.state == ClockState.RUNNING  # 자유진행 — 시계는 계속
        await bus.publish("watch.alive", {"node": "ems", "state": "alive"})
        assert banks[Link.LINK3].get("ems_status") == 1
        await clock.stop()

    async def test_rtds_lost_clears_ready_and_counts(self, env):
        _, bus, clock, banks, engine, _ = env
        await engine.start_run(1)
        banks[Link.LINK1].set("rtds_ready", 1)
        await bus.publish("watch.lost", {"node": "rtds", "state": "lost"})
        assert banks[Link.LINK1].get("rtds_ready") == 0
        assert engine.status()["disconnect_intervals"] == 1
        assert clock.state == ClockState.RUNNING
        await clock.stop()

    async def test_sustained_disconnect_accumulates_per_interval(self, env):
        """장기 단절은 구간 경계마다 누적 (§6 — 전이 1회가 아닌 지속 구간 수 기준)."""
        from app.watch.heartbeat import NodeState
        _, bus, clock, banks, engine, _ = env
        await engine.start_run(1)
        await bus.publish("watch.lost", {"node": "rtds", "state": "lost"})  # 전이 → 구간 0
        engine._monitor._nodes["rtds"].state = NodeState.LOST  # 단절 지속 상태
        for _ in range(45):  # 3개 구간 경계 통과
            await clock.advance_minute()
        assert engine.status()["disconnect_intervals"] >= 3
        await clock.stop()

    async def test_end_run_clears_pending_trigger(self, env):
        """ack 미수신 상태로 종료 시 트리거 잔존 방지 — 종료된 Run의 SOC 지연 주입 차단."""
        _, _, clock, banks, engine, _ = env
        await engine.start_run(1)
        assert banks[Link.LINK3].get("initial_soc_trigger") == 1  # ack 미수신
        await engine.stop_run()
        assert banks[Link.LINK3].get("initial_soc_trigger") == 0
        assert engine.status()["soc_injection_pending"] is False
