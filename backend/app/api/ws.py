"""WebSocket 실시간 스트림 — 이벤트 버스의 clock.* 이벤트를 브로드캐스트."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


class WsBroadcaster:
    """이벤트 버스 '*' 구독 → 접속된 모든 WebSocket에 {topic, payload} 전송."""

    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()

    async def on_event(self, topic: str, payload: dict[str, Any]) -> None:
        if not self._clients:
            return
        message = {"topic": topic, "payload": payload}
        results = await asyncio.gather(
            *(ws.send_json(message) for ws in list(self._clients)),
            return_exceptions=True,
        )
        for ws, result in zip(list(self._clients), results):
            if isinstance(result, Exception):
                self._clients.discard(ws)

    async def handle(self, ws: WebSocket, initial_status: dict[str, Any]) -> None:
        await ws.accept()
        self._clients.add(ws)
        try:
            await ws.send_json({"topic": "clock.state", "payload": initial_status})
            while True:
                await ws.receive_text()  # keep-alive (클라이언트 ping 무시)
        except WebSocketDisconnect:
            pass
        finally:
            self._clients.discard(ws)
