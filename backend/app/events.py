"""비동기 이벤트 버스 — 모듈 간 결합을 이벤트로 분리.

발행 이벤트 (Phase 1 시점):
  clock.minute   가상 1분 틱      {"virtual_time", "interval_index", "day_number"}
  clock.interval 15분 구간 경계    {"virtual_time", "interval_index", "day_number"}
  clock.day      일 경계(00:00)   {"virtual_time", "day_number"}
  clock.state    시계 상태 변화    {"state", ...status}
  clock.complete Run 완료         {...status}
이후 Phase에서 modbus.write / watch.disconnect 등 추가.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

Handler = Callable[[str, dict[str, Any]], Awaitable[None] | None]


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)

    def subscribe(self, topic: str, handler: Handler) -> None:
        """topic 구독. '*'는 전체 이벤트 구독."""
        self._handlers[topic].append(handler)

    def unsubscribe(self, topic: str, handler: Handler) -> None:
        if handler in self._handlers.get(topic, []):
            self._handlers[topic].remove(handler)

    async def publish(self, topic: str, payload: dict[str, Any]) -> None:
        for handler in [*self._handlers.get(topic, []), *self._handlers.get("*", [])]:
            try:
                result = handler(topic, payload)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                logger.exception("이벤트 핸들러 오류: topic=%s", topic)
