"""레지스터 정의 타입 — 프로토콜맵 v1.0 §4 (객체 유형·참조주소 규칙)."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, IntEnum


class Link(IntEnum):
    """프로토콜맵 시트 번호 = 문서의 ①②③ 접두."""

    LINK1 = 1  # ① EMS–매니저 (매니저=Server, EMS=Client)
    LINK2 = 2  # ② RTDS–EMS  (RTDS=Server, EMS=Client)
    LINK3 = 3  # ③ 매니저–RTDS (매니저=Server, RTDS=Client)


class ObjType(str, Enum):
    """Modbus 객체 유형. 참조주소 접두: Coil 0 / DI 1 / IR 3 / HR 4."""

    COIL = "coil"  # 1-bit R/W  — FC01(R)/FC05·15(W)
    DI = "di"      # 1-bit RO   — FC02(R), 서버가 채움
    IR = "ir"      # 16-bit RO  — FC04(R), 서버가 채움
    HR = "hr"      # 16-bit R/W — FC03(R)/FC06·16(W)


REF_BASE: dict[ObjType, int] = {
    ObjType.COIL: 0,
    ObjType.DI: 100000,
    ObjType.IR: 300000,
    ObjType.HR: 400000,
}


class DType(str, Enum):
    BIT = "bit"
    UINT16 = "uint16"
    INT16 = "int16"


@dataclass(frozen=True)
class RegisterDef:
    """레지스터 1개 정의. scale: raw = round(물리값 × scale)."""

    link: Link
    obj: ObjType
    address: int          # 0-base 주소 (객체 유형 내 오프셋)
    key: str              # 코드용 영문 식별자 (snake_case)
    name: str             # 설계 문서의 한글 명칭
    dtype: DType = DType.UINT16
    scale: float = 1.0
    unit: str = ""
    note: str = ""

    @property
    def ref(self) -> str:
        """0-base 6자리 참조주소 (예: HR 7 → '400007')."""
        return f"{REF_BASE[self.obj] + self.address:06d}"

    def __str__(self) -> str:
        prefix = {Link.LINK1: "①", Link.LINK2: "②", Link.LINK3: "③"}[self.link]
        return f"{prefix}{self.obj.name} {self.ref} {self.name}"


@dataclass
class LinkMap:
    """한 링크(시트)의 레지스터 맵. (obj, address)와 key 양방향 조회."""

    link: Link
    server: str   # 객체 보유 노드
    client: str   # 개시자 노드
    registers: list[RegisterDef] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._by_addr: dict[tuple[ObjType, int], RegisterDef] = {}
        self._by_key: dict[str, RegisterDef] = {}
        for reg in self.registers:
            addr_key = (reg.obj, reg.address)
            if addr_key in self._by_addr:
                raise ValueError(f"주소 중복: {reg}")
            if reg.key in self._by_key:
                raise ValueError(f"key 중복: {reg.key}")
            if reg.link != self.link:
                raise ValueError(f"링크 불일치: {reg}")
            self._by_addr[addr_key] = reg
            self._by_key[reg.key] = reg

    def at(self, obj: ObjType, address: int) -> RegisterDef:
        return self._by_addr[(obj, address)]

    def __getitem__(self, key: str) -> RegisterDef:
        return self._by_key[key]

    def __contains__(self, key: str) -> bool:
        return key in self._by_key

    def by_type(self, obj: ObjType) -> list[RegisterDef]:
        return sorted(
            (r for r in self.registers if r.obj == obj), key=lambda r: r.address
        )

    def count(self, obj: ObjType) -> int:
        return len(self.by_type(obj))
