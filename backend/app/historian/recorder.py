"""Historian 적재 레코더 — modbus.write 이벤트를 구독해 Run 시계열을 적재.

- RTDS 1분 계측: ③HR 400002~400005 일괄 쓰기(FC16, registers 포함) → measurements_1m
- EMS 예측 블록: ①IR 300002~300018 미러 쓰기 → forecast_blocks_15m
  ([1구간] h=1만 적재 — [2~4구간] 300007~300018은 예약(0 기록)이므로 무시, 설계서 v1.7)
- 적재 시 'historian.measurement' 이벤트 발행 → 모니터링 차트 실시간 갱신

Run 진행 중(run_id 존재)에만 적재한다 — Run 밖의 쓰기는 시퀀스 로그로만 남는다.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from ..events import EventBus
from ..protocol.types import Link
from .store import HistorianStore

logger = logging.getLogger(__name__)

# ③HR 계측 키 → measurements_1m 컬럼
_MEAS_COLUMN = {
    "ess_power": "ess_power_kw",
    "ess_soc": "ess_soc",
    "pcc_import": "pcc_import_kw",
    "pcc_export": "pcc_export_kw",
}
_FORECAST_KEYS = ("pv_forecast", "load_forecast", "ess_schedule", "target_soc")
_FORECAST_COLUMN = {"pv_forecast": "pv_kw", "load_forecast": "load_kw",
                    "ess_schedule": "schedule_kw", "target_soc": "target_soc"}


class HistorianRecorder:
    def __init__(self, store: HistorianStore, bus: EventBus,
                 run_context: Callable[[], tuple[int | None, str]]) -> None:
        """run_context: 현재 (run_id, 가상시각 ISO)를 반환 — 시퀀스 엔진이 제공."""
        self._store = store
        self._bus = bus
        self._run_context = run_context
        bus.subscribe("modbus.write", self._on_write)

    async def _on_write(self, _topic: str, payload: dict[str, Any]) -> None:
        run_id, virtual_time = self._run_context()
        if run_id is None:
            return
        link, key = payload.get("link"), payload.get("key")
        registers = payload.get("registers")

        # RTDS 1분 계측 블록 (③HR ess_power 시작 FC16 — registers에 4항목)
        if link == int(Link.LINK3) and key == "ess_power" and registers:
            values = {
                _MEAS_COLUMN[r["key"]]: float(r["physical"])
                for r in registers if r["key"] in _MEAS_COLUMN
            }
            if values:
                self._store.add_measurement(run_id, virtual_time, values)
                await self._bus.publish("historian.measurement",
                                        {"run_id": run_id, "virtual_time": virtual_time, **values})

        # EMS 예측 블록 (①IR 미러 — 기준 구간 + [1구간] 4항목만 유효, [2~4구간]은 예약 0)
        elif link == int(Link.LINK1) and key == "forecast_base_interval" and registers:
            by_key = {r["key"]: float(r["physical"]) for r in registers}
            base_interval = int(by_key.get("forecast_base_interval", payload.get("physical", 0)))
            row = {_FORECAST_COLUMN[k]: by_key.get(f"{k}_1") for k in _FORECAST_KEYS}
            if any(v is not None for v in row.values()):
                self._store.add_forecast_block(run_id, virtual_time, base_interval, [row])
