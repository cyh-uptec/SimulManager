"""가상시계 테스트 — 환산·구간 랩어라운드·일 경계·완료·상태 전이."""
from datetime import datetime

import pytest

from app.clock import ClockState, VirtualClock
from app.config import SimulationConfig
from app.events import EventBus


def make_clock(days: int = 1, accel: float = 10.0) -> tuple[VirtualClock, list]:
    sim = SimulationConfig(
        start_virtual_time=datetime(2025, 7, 1, 0, 0), days=days,
        accel_seconds_per_15min=accel,
    )
    bus = EventBus()
    events: list[tuple[str, dict]] = []

    async def recorder(topic: str, payload: dict) -> None:
        events.append((topic, payload))

    bus.subscribe("*", recorder)
    return VirtualClock(sim, bus), events


class TestConversion:
    def test_tick_period(self):
        # 가상 1분 = 실 배율/15 초 — 배율 10 s → 0.667 s
        clock, _ = make_clock(accel=10.0)
        assert clock.tick_period_s == pytest.approx(10.0 / 15.0)

    def test_total_minutes(self):
        clock, _ = make_clock(days=30)
        assert clock.total_minutes == 30 * 24 * 60  # 43,200분 = 2,880구간


class TestAdvance:
    async def test_interval_index_and_events(self):
        clock, events = make_clock()
        # 14분까지는 구간 0, 15분째에 구간 1 진입
        for _ in range(14):
            await clock.advance_minute()
        assert clock.interval_index == 0
        await clock.advance_minute()
        assert clock.interval_index == 1
        interval_events = [e for e in events if e[0] == "clock.interval"]
        assert len(interval_events) == 1
        assert interval_events[0][1]["interval_index"] == 1

    async def test_interval_wraparound_96(self):
        clock, events = make_clock(days=2)
        # 23:59까지 진행 → 구간 95, 다음 1분에 00:00 → 구간 0 + 일 경계
        for _ in range(24 * 60 - 1):
            await clock.advance_minute()
        assert clock.interval_index == 95
        assert clock.day_number == 1
        await clock.advance_minute()
        assert clock.interval_index == 0
        assert clock.day_number == 2
        day_events = [e for e in events if e[0] == "clock.day"]
        assert len(day_events) == 1

    async def test_run_completion(self):
        clock, events = make_clock(days=1)
        for _ in range(24 * 60):
            await clock.advance_minute()
        assert clock.state == ClockState.COMPLETED
        assert [e for e in events if e[0] == "clock.complete"]

    async def test_minute_event_payload(self):
        clock, events = make_clock()
        await clock.advance_minute()
        topic, payload = events[0]
        assert topic == "clock.minute"
        assert payload["virtual_time"] == "2025-07-01T00:01:00"
        assert payload["interval_index"] == 0


class TestStateTransitions:
    async def test_start_pause_resume_stop(self):
        clock, _ = make_clock(accel=900.0)  # 틱 주기 60 s — 테스트 중 자연 틱 없음
        await clock.start()
        assert clock.state == ClockState.RUNNING
        await clock.pause()
        assert clock.state == ClockState.PAUSED
        await clock.resume()
        assert clock.state == ClockState.RUNNING
        await clock.stop()
        assert clock.state == ClockState.STOPPED

    async def test_start_only_from_idle(self):
        clock, _ = make_clock(accel=900.0)
        await clock.start()
        with pytest.raises(RuntimeError):
            await clock.start()
        await clock.stop()

    async def test_rewind_resets_to_start(self):
        clock, _ = make_clock()
        await clock.advance_minute()
        await clock.advance_minute()
        await clock.rewind()
        assert clock.state == ClockState.IDLE
        assert clock.virtual_time == datetime(2025, 7, 1, 0, 0)
        assert clock.status()["elapsed_minutes"] == 0

    async def test_rewind_blocked_while_running(self):
        clock, _ = make_clock(accel=900.0)
        await clock.start()
        with pytest.raises(RuntimeError):
            await clock.rewind()
        await clock.stop()


class TestRealTimeLoop:
    async def test_loop_advances_with_acceleration(self):
        # 배율 1 s(최소) → 틱 주기 0.0667 s. 실 0.35 s 대기 → 약 5분 진행
        import asyncio
        clock, _ = make_clock(accel=1.0)
        await clock.start()
        await asyncio.sleep(0.35)
        await clock.stop()
        assert clock.status()["elapsed_minutes"] >= 3
