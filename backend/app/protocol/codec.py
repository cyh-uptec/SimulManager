"""스케일·부호 변환 코덱 — docs/PROTOCOL_RULES.md §2.

- 부호: 방전(PCS→계통) +, 충전 − (전 링크 공통)
- raw = round(물리값 × scale), INT16은 2의 보수
- 32-bit(누적 전력량): big-endian 상위워드 우선
"""
from __future__ import annotations

from .types import DType, RegisterDef

UINT16_MAX = 0xFFFF
INT16_MIN, INT16_MAX = -32768, 32767


def encode(value: float, reg: RegisterDef) -> int:
    """물리값 → 16-bit raw (레지스터에 쓸 값)."""
    if reg.dtype == DType.BIT:
        return 1 if value else 0
    scaled = round(value * reg.scale)
    if reg.dtype == DType.INT16:
        if not INT16_MIN <= scaled <= INT16_MAX:
            raise ValueError(f"{reg}: INT16 범위 초과 (scaled={scaled})")
        return scaled & UINT16_MAX  # 2의 보수
    if not 0 <= scaled <= UINT16_MAX:
        raise ValueError(f"{reg}: UINT16 범위 초과 (scaled={scaled})")
    return scaled


def decode(raw: int, reg: RegisterDef) -> float | int:
    """16-bit raw → 물리값. BIT/스케일 1은 int, 그 외 float."""
    if reg.dtype == DType.BIT:
        return 1 if raw else 0
    if not 0 <= raw <= UINT16_MAX:
        raise ValueError(f"{reg}: raw가 16-bit 범위 밖 (raw={raw})")
    signed = raw - 0x10000 if reg.dtype == DType.INT16 and raw > INT16_MAX else raw
    if reg.scale == 1.0:
        return signed
    return signed / reg.scale


def u32_from_words(hi: int, lo: int) -> int:
    """상위·하위 워드 → UINT32 (big-endian, 상위워드 우선)."""
    if not (0 <= hi <= UINT16_MAX and 0 <= lo <= UINT16_MAX):
        raise ValueError(f"워드가 16-bit 범위 밖 (hi={hi}, lo={lo})")
    return (hi << 16) | lo


def words_from_u32(value: int) -> tuple[int, int]:
    """UINT32 → (상위워드, 하위워드)."""
    if not 0 <= value <= 0xFFFFFFFF:
        raise ValueError(f"UINT32 범위 초과 (value={value})")
    return (value >> 16) & UINT16_MAX, value & UINT16_MAX


# 누적 전력량(②IR 300033~300036): UINT32 결합 후 ×10 kWh
ENERGY_U32_SCALE = 10.0


def decode_energy_kwh(hi: int, lo: int) -> float:
    """누적 충·방전 전력량 [kWh] = UINT32(hi,lo) / 10."""
    return u32_from_words(hi, lo) / ENERGY_U32_SCALE


def encode_energy_kwh(kwh: float) -> tuple[int, int]:
    """kWh → (상위워드, 하위워드)."""
    return words_from_u32(round(kwh * ENERGY_U32_SCALE))
