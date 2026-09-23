"""코덱 테스트 — 스케일·부호·INT16·UINT32 big-endian (docs/PROTOCOL_RULES.md §2)."""
import pytest

from app.protocol import LINK1, LINK2, LINK3, decode, encode, u32_from_words, words_from_u32
from app.protocol.codec import decode_energy_kwh, encode_energy_kwh


class TestScaleRoundTrip:
    def test_x100_power(self):
        # ②HR 400001 P지령 INT16 ×100 — 방전 +250.00 kW
        reg = LINK2["p_command"]
        assert encode(250.0, reg) == 25000
        assert decode(25000, reg) == 250.0

    def test_x100_negative_charge(self):
        # 충전 −250.00 kW → 2의 보수
        reg = LINK2["p_command"]
        raw = encode(-250.0, reg)
        assert raw == (-25000) & 0xFFFF == 40536
        assert decode(raw, reg) == -250.0

    def test_x10_link3_power(self):
        # ③HR 400002 ESS 전력 INT16 ×10 — 링크②(×100)와 다른 스케일
        reg = LINK3["ess_power"]
        assert encode(-123.4, reg) == (-1234) & 0xFFFF
        assert decode(encode(-123.4, reg), reg) == -123.4

    def test_x1000_pf(self):
        # ②HR 400003 역률 INT16 ×1000 — −1.000~1.000
        reg = LINK2["pf_command"]
        assert encode(1.0, reg) == 1000
        assert decode(encode(-0.95, reg), reg) == -0.95

    def test_x50_voltage(self):
        # ②IR 300020 DC 링크 전압 UINT16 ×50
        reg = LINK2["dc_link_voltage"]
        assert encode(750.0, reg) == 37500
        assert decode(37500, reg) == 750.0

    def test_soc_x100(self):
        # 요약 SOC ×100 — 50.00% = 5000
        reg = LINK3["initial_soc"]
        assert encode(50.0, reg) == 5000
        assert decode(5000, reg) == 50.0

    def test_bank_soc_x10(self):
        # ②IR 300021 Bank SOC ×10 — 0.1% 분해능
        reg = LINK2["bank_soc"]
        assert encode(85.5, reg) == 855
        assert decode(855, reg) == 85.5

    def test_temp_scale_differs_by_link(self):
        # 외기온도: 링크① ×10 vs 링크③ ×100 (동일 물리량, 링크별 스케일 상이)
        assert encode(25.5, LINK1["ambient_temp"]) == 255
        assert encode(25.5, LINK3["ambient_temp"]) == 2550

    def test_scale1_returns_int(self):
        reg = LINK1["interval_index"]
        assert encode(95, reg) == 95
        assert decode(95, reg) == 95
        assert isinstance(decode(95, reg), int)

    def test_bit(self):
        reg = LINK1["rtds_ready"]
        assert encode(1, reg) == 1
        assert encode(0, reg) == 0
        assert decode(1, reg) == 1


class TestRangeGuard:
    def test_int16_overflow(self):
        with pytest.raises(ValueError):
            encode(400.0, LINK2["p_command"])  # ×100 → 40000 > 32767

    def test_uint16_negative(self):
        with pytest.raises(ValueError):
            encode(-1.0, LINK2["discharge_limit"])


class TestU32BigEndian:
    def test_words_order(self):
        # big-endian 상위워드 우선
        hi, lo = words_from_u32(0x0001_86A0)  # 100000
        assert (hi, lo) == (0x0001, 0x86A0)
        assert u32_from_words(hi, lo) == 100000

    def test_energy_kwh(self):
        # 누적 전력량: UINT32 ×10 kWh — 12345.6 kWh → 123456
        hi, lo = encode_energy_kwh(12345.6)
        assert u32_from_words(hi, lo) == 123456
        assert decode_energy_kwh(hi, lo) == 12345.6

    def test_energy_over_16bit(self):
        # 하위워드 초과 값 (6553.6 kWh 이상)에서 상위워드 사용 확인
        hi, lo = encode_energy_kwh(700000.0)  # raw 7,000,000 > 65535
        assert hi > 0
        assert decode_energy_kwh(hi, lo) == 700000.0
