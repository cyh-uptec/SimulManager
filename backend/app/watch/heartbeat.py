"""Heartbeat 생존감시 — docs/PROTOCOL_RULES.md §6.

- EMS: ①IR 300000 (HR 미러 경유 기록) / RTDS: ③HR 400000 — 실 1 s 증가
- 값 '변화'를 생존 신호로 판정, alive_timeout_s(기본 3 s) 미변화 시 단절(lost)
- 이벤트: watch.alive / watch.lost (전이 시), watch.update (주기적 전체 상태)
- 가상시계는 단절과 무관하게 계속 진행한다 (자유진행 원칙 — 여기서 시계 제어 금지)
"""
from __future__ import annotations

import asyncio
import logging
import time
from enum import Enum
from typing import Any

from ..config import TimingConfig
from ..events import EventBus
from ..protocol.types import Link

logger = logging.getLogger(__name__)

# 감시 대상: (링크, 객체, 레지스터 key) → 노드명
_HEARTBEAT_SOURCES: dict[tuple[int, str], str] = {
    (int(Link.LINK1), "ems_heartbeat"): "ems",
    (int(Link.LINK3), "rtds_heartbeat"): "rtds",
}


class NodeState(str, Enum):
    UNKNOWN = "unknown"  # 기동 후 HB 미수신
    ALIVE = "alive"      # HB 정상 갱신 중
    LOST = "lost"        # 판정 시간 내 미갱신 → 단절


class _NodeWatch:
    def __init__(self, name: str) -> None:
        self.name = name
        self.state = NodeState.UNKNOWN
        self.last_value: int | None = None
        self.last_change_mono: float | None = None

    def on_write(self, value: int) -> bool:
        """HB 기록 처리. 값 변화 시 True."""
        if value != self.last_value:
            self.last_value = value
            self.last_change_mono = time.monotonic()
            return True
        return False

    def age_s(self) -> float | None:
        if self.last_change_mono is None:
            return None
        return time.monotonic() - self.last_change_mono

    def status(self) -> dict[str, Any]:
        age = self.age_s()
        return {
            "state": self.state.value,
            "heartbeat": self.last_value,
            "age_s": round(age, 2) if age is not None else None,
        }


class HeartbeatMonitor:
    def __init__(self, bus: EventBus, timing: TimingConfig) -> None:
        self._bus = bus
        self._timeout = timing.alive_timeout_s
        self._check_period = min(timing.heartbeat_period_s, timing.alive_timeout_s / 3)
        self._nodes = {"ems": _NodeWatch("ems"), "rtds": _NodeWatch("rtds")}
        self._task: asyncio.Task | None = None
        bus.subscribe("modbus.write", self._on_modbus_write)

    def start(self) -> None:
        self._task = asyncio.create_task(self._check_loop(), name="heartbeat-monitor")

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    def status(self) -> dict[str, Any]:
        return {name: watch.status() for name, watch in self._nodes.items()}

    # ── 이벤트 처리 ─────────────────────────────────────────
    async def _on_modbus_write(self, _topic: str, payload: dict[str, Any]) -> None:
        key = (payload.get("link"), payload.get("key"))
        node_name = _HEARTBEAT_SOURCES.get(key)  # type: ignore[arg-type]
        if node_name is None:
            return
        watch = self._nodes[node_name]
        changed = watch.on_write(int(payload["values"][0]))
        if changed and watch.state != NodeState.ALIVE:
            await self._transition(watch, NodeState.ALIVE)

    async def _check_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._check_period)
                for watch in self._nodes.values():
                    age = watch.age_s()
                    if watch.state == NodeState.ALIVE and age is not None and age > self._timeout:
                        await self._transition(watch, NodeState.LOST)
                await self._bus.publish("watch.update", self.status())
        except asyncio.CancelledError:
            pass

    async def _transition(self, watch: _NodeWatch, new_state: NodeState) -> None:
        old = watch.state
        watch.state = new_state
        topic = "watch.alive" if new_state == NodeState.ALIVE else "watch.lost"
        logger.info("노드 %s: %s → %s", watch.name, old.value, new_state.value)
        await self._bus.publish(topic, {"node": watch.name, "prev": old.value, **watch.status()})
