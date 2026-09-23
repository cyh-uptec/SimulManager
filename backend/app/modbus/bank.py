"""레지스터 뱅크 — 한 링크의 Modbus 데이터스토어 + 물리값 접근 + 클라이언트 쓰기 후킹.

- 내부(매니저) 쓰기: `set(key, 물리값)` — protocol SSOT의 스케일로 인코딩
- 클라이언트 쓰기: pymodbus 서버 경유 → 후킹 → 미러 변환 → `modbus.write` 이벤트 발행
- 링크① 미러: EMS의 FC16 HR 미러 기록을 DI/IR 뱅크로 내부 매핑 (protocol/mirror.py)
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable

from pymodbus.datastore import ModbusSequentialDataBlock, ModbusSlaveContext

from ..events import EventBus
from ..protocol import LinkMap, decode, encode
from ..protocol.mirror import MIRROR_HR_BASE_DI, MIRROR_HR_BASE_IR
from ..protocol.types import DType, Link, ObjType, RegisterDef

logger = logging.getLogger(__name__)

# ModbusSlaveContext 내부 store 키 ↔ 객체 유형
_STORE_KEY = {ObjType.COIL: "c", ObjType.DI: "d", ObjType.IR: "i", ObjType.HR: "h"}
# 내부 쓰기/읽기용 대표 FC
_WRITE_FC = {ObjType.COIL: 5, ObjType.DI: 2, ObjType.IR: 4, ObjType.HR: 6}
_READ_FC = {ObjType.COIL: 1, ObjType.DI: 2, ObjType.IR: 4, ObjType.HR: 3}

ClientWriteHandler = Callable[[Link, ObjType, int, list[int]], None]


class HookedSlaveContext(ModbusSlaveContext):
    """클라이언트(Modbus 요청) 읽기·쓰기를 후킹하는 SlaveContext.

    - setValues()/getValues(): 서버가 실 클라이언트 요청 처리 시 호출 — 쓰기 후킹 + 활동 스탬프
    - set_internal()/get_internal(): 매니저 내부 접근 — 후킹·활동 스탬프 우회
    - set_as_client(): 가상 노드용 — 쓰기 후킹(이벤트·미러)은 타되 활동 스탬프는 남기지 않아
      '요청 수신' 표시가 실장비 접속만 반영하게 한다
    """

    def __init__(self, on_client_write: Callable[[int, int, list], None],
                 on_client_activity: Callable[[], None], **blocks: Any) -> None:
        super().__init__(**blocks)
        self._on_client_write = on_client_write
        self._on_client_activity = on_client_activity

    def getValues(self, fc_as_hex: int, address: int, count: int = 1) -> list:
        self._on_client_activity()
        return super().getValues(fc_as_hex, address, count)

    def setValues(self, fc_as_hex: int, address: int, values: list) -> None:
        self._on_client_activity()
        super().setValues(fc_as_hex, address, values)
        self._on_client_write(fc_as_hex, address, list(values))

    def set_as_client(self, fc_as_hex: int, address: int, values: list) -> None:
        super().setValues(fc_as_hex, address, values)
        self._on_client_write(fc_as_hex, address, list(values))

    def set_internal(self, fc_as_hex: int, address: int, values: list) -> None:
        super().setValues(fc_as_hex, address, values)

    def get_internal(self, fc_as_hex: int, address: int, count: int = 1) -> list:
        return super().getValues(fc_as_hex, address, count)


class RegisterBank:
    """한 링크의 레지스터 뱅크. 주소·스케일은 protocol SSOT 경유만."""

    def __init__(self, link_map: LinkMap, bus: EventBus | None = None) -> None:
        self.map = link_map
        self.link = link_map.link
        self._bus = bus
        self._mirror = link_map.link == Link.LINK1  # 링크①만 DI/IR HR 미러 사용

        sizes = {obj: self._size_for(obj) for obj in ObjType}
        # ModbusSlaveContext는 내부적으로 주소 +1 오프셋 → 블록은 size+1 확보
        blocks = {
            _STORE_KEY[obj]: ModbusSequentialDataBlock(0, [0] * (sizes[obj] + 1))
            for obj in ObjType
        }
        self._last_client_request_mono: float | None = None
        self.ctx = HookedSlaveContext(
            self._handle_client_write,
            self._stamp_client_activity,
            di=blocks["d"], co=blocks["c"], ir=blocks["i"], hr=blocks["h"],
        )

    def _size_for(self, obj: ObjType) -> int:
        regs = self.map.by_type(obj)
        size = (regs[-1].address + 1) if regs else 1
        if self._mirror and obj == ObjType.HR:
            # 미러 영역까지 확보: 마지막 미러 주소 = 30000 + (IR 개수 − 1)
            size = MIRROR_HR_BASE_IR + self.map.count(ObjType.IR)
        return size

    # ── 내부(매니저) 접근 — 물리값 ───────────────────────────
    def set(self, key: str, value: float | int) -> None:
        reg = self.map[key]
        self.ctx.set_internal(_WRITE_FC[reg.obj], reg.address, [encode(value, reg)])

    def get(self, key: str) -> float | int:
        reg = self.map[key]
        raw = self.ctx.get_internal(_READ_FC[reg.obj], reg.address, 1)[0]
        return decode(int(raw), reg)

    def set_raw(self, obj: ObjType, address: int, values: list[int]) -> None:
        self.ctx.set_internal(_WRITE_FC[obj], address, values)

    def get_raw(self, obj: ObjType, address: int, count: int = 1) -> list[int]:
        return [int(v) for v in self.ctx.get_internal(_READ_FC[obj], address, count)]

    # ── 클라이언트 접속 활동 (실 Modbus 요청 — 읽기 포함) ────────
    def _stamp_client_activity(self) -> None:
        self._last_client_request_mono = time.monotonic()

    def client_activity_age(self) -> float | None:
        """마지막 실 클라이언트 요청 후 경과 초 — 요청이 없었으면 None.

        가상 노드·매니저 내부 접근은 스탬프하지 않으므로 실장비 접속 판별에 쓸 수 있다.
        """
        if self._last_client_request_mono is None:
            return None
        return round(time.monotonic() - self._last_client_request_mono, 1)

    def dump(self) -> list[dict[str, Any]]:
        """전 레지스터 현재값 (모니터링 API용)."""
        result = []
        for reg in self.map.registers:
            raw = self.get_raw(reg.obj, reg.address, 1)[0]
            result.append({
                "link": int(self.link), "obj": reg.obj.value, "ref": reg.ref,
                "key": reg.key, "name": reg.name, "unit": reg.unit,
                "raw": raw, "value": decode(raw, reg),
            })
        return result

    # ── 클라이언트 쓰기 후킹 ─────────────────────────────────
    def _handle_client_write(self, fc_as_hex: int, address: int, values: list) -> None:
        obj = {"c": ObjType.COIL, "d": ObjType.DI, "i": ObjType.IR, "h": ObjType.HR}[
            self.ctx.decode(fc_as_hex)
        ]
        raws = [int(v) for v in values]

        # 링크① HR 미러 → DI/IR 내부 매핑 후 논리 객체로 변환
        if self._mirror and obj == ObjType.HR:
            mapped = self._map_mirror(address, raws)
            if mapped is not None:
                obj, address = mapped

        payload: dict[str, Any] = {
            "link": int(self.link), "obj": obj.value, "address": address, "values": raws,
        }
        reg = self._lookup(obj, address)
        if reg is not None:
            payload.update({
                "ref": reg.ref, "key": reg.key, "name": reg.name,
                "physical": (1 if raws[0] else 0) if reg.dtype == DType.BIT
                else decode(raws[0], reg),
            })
        if len(raws) > 1:
            payload["registers"] = [
                {"key": r.key, "ref": r.ref, "name": r.name,
                 "physical": (1 if raw else 0) if r.dtype == DType.BIT else decode(raw, r)}
                for i, raw in enumerate(raws)
                if (r := self._lookup(obj, address + i)) is not None
            ]
        self._publish_soon("modbus.write", payload)

    def _map_mirror(self, address: int, raws: list[int]) -> tuple[ObjType, int] | None:
        """미러 HR 쓰기를 DI/IR로 매핑. 한 요청은 한 미러 영역 안에 완전히 포함되어야 한다.

        범위 밖·영역 경계 걸침은 매핑하지 않고 경고만 남긴다 (뱅크 오염 방지 —
        pymodbus 데이터블록은 범위 밖 슬라이스 대입 시 침묵 확장되므로 사전 차단).
        """
        end = address + len(raws)
        ir_count = self.map.count(ObjType.IR)
        di_count = self.map.count(ObjType.DI)

        if address >= MIRROR_HR_BASE_IR:
            if end <= MIRROR_HR_BASE_IR + ir_count:
                target = address - MIRROR_HR_BASE_IR
                self.set_raw(ObjType.IR, target, raws)
                return ObjType.IR, target
            logger.warning("링크① IR 미러 범위 초과 쓰기 무시: HR %d~%d (IR 정의 %d개)",
                           address, end - 1, ir_count)
            return None
        if address >= MIRROR_HR_BASE_DI:
            if end <= MIRROR_HR_BASE_DI + di_count:
                target = address - MIRROR_HR_BASE_DI
                self.set_raw(ObjType.DI, target, [1 if v else 0 for v in raws])
                return ObjType.DI, target
            logger.warning("링크① DI 미러 범위 초과·영역 걸침 쓰기 무시: HR %d~%d (DI 정의 %d개)",
                           address, end - 1, di_count)
            return None
        if end > MIRROR_HR_BASE_DI:
            logger.warning("링크① HR 쓰기가 미러 영역을 침범 (미매핑): HR %d~%d", address, end - 1)
        return None

    def _lookup(self, obj: ObjType, address: int) -> RegisterDef | None:
        try:
            return self.map.at(obj, address)
        except KeyError:
            return None

    def _publish_soon(self, topic: str, payload: dict[str, Any]) -> None:
        if self._bus is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # 이벤트 루프 밖(동기 테스트) — 발행 생략
        loop.create_task(self._bus.publish(topic, payload))
