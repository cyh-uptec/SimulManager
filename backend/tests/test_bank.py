"""RegisterBank 테스트 — 물리값 접근·클라이언트 쓰기 후킹·링크① HR 미러."""
import asyncio

import pytest

from app.events import EventBus
from app.modbus.bank import RegisterBank
from app.protocol import LINK1, LINK3
from app.protocol.mirror import MIRROR_HR_BASE_DI, MIRROR_HR_BASE_IR
from app.protocol.types import ObjType


class TestInternalAccess:
    def test_set_get_physical(self):
        bank = RegisterBank(LINK3)
        bank.set("initial_soc", 50.0)          # ×100 → raw 5000
        assert bank.get("initial_soc") == 50.0
        assert bank.get_raw(ObjType.IR, 11) == [5000]

    def test_signed_value(self):
        bank = RegisterBank(LINK3)
        bank.set("ess_power", -123.4)          # INT16 ×10
        assert bank.get("ess_power") == -123.4
        assert bank.get_raw(ObjType.HR, 2) == [(-1234) & 0xFFFF]

    def test_coil(self):
        bank = RegisterBank(LINK1)
        bank.set("rtds_ready", 1)
        assert bank.get("rtds_ready") == 1
        assert bank.get_raw(ObjType.COIL, 4) == [1]

    def test_dump_contains_all(self):
        bank = RegisterBank(LINK1)
        dump = bank.dump()
        assert len(dump) == len(LINK1.registers)  # 41개
        by_key = {d["key"]: d for d in dump}
        assert by_key["scenario_id"]["ref"] == "400007"


class TestClientWriteHook:
    async def test_client_write_publishes_event(self):
        bus = EventBus()
        events: list[tuple[str, dict]] = []

        async def recorder(topic, payload):
            events.append((topic, payload))

        bus.subscribe("modbus.write", recorder)
        bank = RegisterBank(LINK3, bus)
        # 클라이언트 쓰기 시뮬레이션: FC16으로 HR 0 (RTDS Heartbeat)
        bank.ctx.setValues(16, 0, [42])
        await asyncio.sleep(0)  # create_task 소화
        assert len(events) == 1
        payload = events[0][1]
        assert payload["link"] == 3
        assert payload["key"] == "rtds_heartbeat"
        assert payload["values"] == [42]

    async def test_internal_write_no_event(self):
        bus = EventBus()
        events: list = []
        bus.subscribe("modbus.write", lambda t, p: events.append(p))
        bank = RegisterBank(LINK3, bus)
        bank.set("rtds_heartbeat", 7)  # 내부 쓰기 — 후킹 우회
        await asyncio.sleep(0)
        assert events == []
        assert bank.get("rtds_heartbeat") == 7


class TestLink1Mirror:
    async def test_ir_mirror_write(self):
        """EMS가 미러 HR(30000+n)에 FC16 기록 → IR n 반영 + 논리 객체로 이벤트."""
        bus = EventBus()
        events: list[dict] = []
        bus.subscribe("modbus.write", lambda t, p: events.append(p))
        bank = RegisterBank(LINK1, bus)

        bank.ctx.setValues(16, MIRROR_HR_BASE_IR + 0, [123])  # EMS Heartbeat
        await asyncio.sleep(0)
        assert bank.get("ems_heartbeat") == 123
        assert bank.get_raw(ObjType.IR, 0) == [123]
        assert events[0]["obj"] == "ir"
        assert events[0]["address"] == 0
        assert events[0]["key"] == "ems_heartbeat"

    async def test_di_mirror_write(self):
        """EMS가 미러 HR(10000+n)에 기록 → DI n 반영."""
        bus = EventBus()
        bank = RegisterBank(LINK1, bus)
        bank.ctx.setValues(16, MIRROR_HR_BASE_DI + 2, [1])  # 예측 완료
        await asyncio.sleep(0)
        assert bank.get("forecast_done") == 1
        assert bank.get_raw(ObjType.DI, 2) == [1]

    async def test_forecast_block_mirror(self):
        """예측 블록 미러 영역(IR 300003~300018) 16워드 일괄 기록 — 뱅크 매핑 경계 검증.

        v1.7에서 [2~4구간]은 예약(0 기록)이지만 뱅크는 전체 미러 영역 쓰기를 수용해야 한다.
        """
        bank = RegisterBank(LINK1)
        values = [100 * (i + 1) for i in range(16)]
        bank.ctx.setValues(16, MIRROR_HR_BASE_IR + 3, values)
        assert bank.get_raw(ObjType.IR, 3, 16) == values
        assert bank.get("pv_forecast_1") == pytest.approx(1.0)  # raw 100, ×100 → 1.00 kW

    async def test_mirror_out_of_range_rejected(self):
        """정의 범위 밖 미러 쓰기는 매핑하지 않는다 (뱅크 침묵 확장 방지)."""
        bank = RegisterBank(LINK1)
        ir_count = LINK1.count(ObjType.IR)  # 19
        before = bank.get_raw(ObjType.IR, 0, ir_count)
        bank.ctx.setValues(16, MIRROR_HR_BASE_IR + ir_count, [999])  # IR 19 — 미정의
        assert bank.get_raw(ObjType.IR, 0, ir_count) == before  # IR 뱅크 불변

    async def test_mirror_region_crossing_rejected(self):
        """DI 미러 영역에서 시작해 IR 영역까지 걸치는 FC16은 매핑하지 않는다."""
        bank = RegisterBank(LINK1)
        di_count = LINK1.count(ObjType.DI)  # 4
        # DI 미러 정의 범위(10000~10003)를 넘는 스팬
        bank.ctx.setValues(16, MIRROR_HR_BASE_DI + 2, [1] * (di_count + 3))
        assert bank.get_raw(ObjType.DI, 2, 1) == [0]  # DI 뱅크 불변
        assert bank.get_raw(ObjType.IR, 0, 1) == [0]  # IR 뱅크 불변

    async def test_multi_register_event_includes_registers(self):
        """FC16 다중 워드 이벤트에 스팬 내 전체 레지스터의 논리 정보 포함."""
        bus = EventBus()
        events: list[dict] = []
        bus.subscribe("modbus.write", lambda t, p: events.append(p))
        bank = RegisterBank(LINK1, bus)
        bank.ctx.setValues(16, MIRROR_HR_BASE_IR + 0, [7, 0, 12])  # HB·상태코드·기준구간
        await asyncio.sleep(0)
        regs = events[0]["registers"]
        assert [r["key"] for r in regs] == ["ems_heartbeat", "ems_status_code", "forecast_base_interval"]
        assert events[0]["key"] == "ems_heartbeat"
