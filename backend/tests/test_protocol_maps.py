"""레지스터 맵 정합 테스트 — 프로토콜맵 v1.0 대비 개수·주소·참조 표기."""
from app.protocol import LINK1, LINK2, LINK3, LINKS
from app.protocol.types import ObjType


class TestRegisterCounts:
    """프로토콜맵 시트별 레지스터 개수 (docs/DEVELOPMENT_PLAN.md Phase 1)."""

    def test_link1_counts(self):
        assert LINK1.count(ObjType.COIL) == 6
        assert LINK1.count(ObjType.HR) == 12
        assert LINK1.count(ObjType.DI) == 4
        assert LINK1.count(ObjType.IR) == 19  # 3 + 4구간×4

    def test_link2_counts(self):
        assert LINK2.count(ObjType.COIL) == 6
        assert LINK2.count(ObjType.HR) == 12
        assert LINK2.count(ObjType.DI) == 6
        assert LINK2.count(ObjType.IR) == 37

    def test_link3_counts(self):
        assert LINK3.count(ObjType.IR) == 12
        assert LINK3.count(ObjType.DI) == 3
        assert LINK3.count(ObjType.HR) == 6
        assert LINK3.count(ObjType.COIL) == 2


class TestAddressIntegrity:
    def test_addresses_contiguous_from_zero(self):
        # 프로토콜맵의 모든 주소대는 0부터 연속
        for link_map in LINKS.values():
            for obj in ObjType:
                regs = link_map.by_type(obj)
                assert [r.address for r in regs] == list(range(len(regs))), \
                    f"{link_map.link} {obj} 주소 불연속"

    def test_ref_format(self):
        # 0-base 6자리 참조주소 (예: HR 7 → 400007)
        assert LINK1["scenario_id"].ref == "400007"
        assert LINK2["p_command"].ref == "400001"
        assert LINK2["charge_energy_lo"].ref == "300036"
        assert LINK3["initial_soc"].ref == "300011"
        assert LINK3["rtds_running"].ref == "000000"
        assert LINK1["ems_heartbeat"].ref == "300000"

    def test_same_address_differs_by_link(self):
        # 동일 주소가 링크마다 다른 객체 (①②③ 표기의 존재 이유)
        assert LINK1.at(ObjType.HR, 0).key == "sim_year"
        assert LINK2.at(ObjType.HR, 0).key == "ess_control_mode"
        assert LINK3.at(ObjType.HR, 0).key == "rtds_heartbeat"


class TestKeyRegisters:
    """시퀀스 핵심 레지스터의 존재·속성 (설계서 §2 매핑)."""

    def test_brokering_registers(self):
        # 연결중개: ③Coil 000000 → ①Coil 000004
        assert LINK3["rtds_running"].address == 0
        assert LINK1["rtds_ready"].address == 4

    def test_soc_handshake_registers(self):
        assert LINK3["initial_soc"].scale == 100
        assert LINK3["initial_soc_trigger"].obj == ObjType.DI
        assert LINK3["initial_soc_ack"].obj == ObjType.COIL

    def test_seq_registers(self):
        assert LINK2["cmd_seq"].address == 11
        assert LINK2["res_seq"].address == 0

    def test_heartbeat_registers(self):
        assert LINK1["ems_heartbeat"].obj == ObjType.IR
        assert LINK3["rtds_heartbeat"].obj == ObjType.HR
