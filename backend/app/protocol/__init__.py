"""레지스터 맵 SSOT — 프로토콜맵 v1.0의 유일한 코드 표현.

모든 모듈(매니저 Modbus 서버·에뮬레이터·테스트)은 반드시 이 패키지를 통해
레지스터 주소·타입·스케일에 접근한다. 주소·스케일 하드코딩 금지.
규칙 상세: docs/PROTOCOL_RULES.md
"""
from .codec import decode, encode, u32_from_words, words_from_u32
from .link1 import LINK1
from .link2 import LINK2
from .link3 import LINK3
from .types import DType, Link, LinkMap, ObjType, RegisterDef

LINKS: dict[Link, LinkMap] = {Link.LINK1: LINK1, Link.LINK2: LINK2, Link.LINK3: LINK3}

__all__ = [
    "DType", "Link", "LinkMap", "ObjType", "RegisterDef",
    "LINK1", "LINK2", "LINK3", "LINKS",
    "encode", "decode", "u32_from_words", "words_from_u32",
]
