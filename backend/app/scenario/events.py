"""기상 이벤트(폭염·폭설) 저장소 — 운영자 임의 입력 (docs/OPEN_ISSUES.md #6 확정).

실측 기상 데이터와 무관하게 운영자가 가상 기간을 지정해 주입하며,
배포 시 기상 상태코드(0정상/1폭염/2폭설)와 적설량 레지스터를 오버라이드한다.
양 Run(A/B)에 동일 적용된다 (공정 비교). data/events.json에 영속화.
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from ..protocol import LINK3
from ..protocol.codec import UINT16_MAX

logger = logging.getLogger(__name__)

EVENT_TYPES = ("heatwave", "snow")  # 1폭염 / 2폭설
WEATHER_CODE = {"heatwave": 1, "snow": 2}
# 적설량 상한 — 레지스터 인코딩 한계에서 유도 (③IR 300006 UINT16 ×100 → 655.35 cm)
SNOW_CM_MAX = UINT16_MAX / LINK3["snow_depth"].scale


@dataclass
class WeatherEvent:
    id: str
    type: str            # heatwave | snow
    start: datetime      # 가상시각 (포함)
    end: datetime        # 가상시각 (미포함)
    snow_cm: float = 0.0  # snow일 때 적설량 [cm]

    def to_json(self) -> dict:
        d = asdict(self)
        d["start"] = self.start.isoformat()
        d["end"] = self.end.isoformat()
        return d


class WeatherEventStore:
    def __init__(self, data_dir: Path) -> None:
        self._path = data_dir / "events.json"
        self._events: list[WeatherEvent] = []
        self._load()

    def list(self) -> list[WeatherEvent]:
        return sorted(self._events, key=lambda e: e.start)

    def add(self, type_: str, start: datetime, end: datetime, snow_cm: float = 0.0) -> WeatherEvent:
        if type_ not in EVENT_TYPES:
            raise ValueError(f"이벤트 유형은 {EVENT_TYPES} 중 하나여야 합니다: {type_}")
        if end <= start:
            raise ValueError("종료 시각은 시작 시각 이후여야 합니다")
        if type_ == "snow" and not 0 <= snow_cm <= SNOW_CM_MAX:
            raise ValueError(f"적설량은 0~{SNOW_CM_MAX} cm 범위여야 합니다 (레지스터 한계)")
        event = WeatherEvent(
            id=uuid.uuid4().hex[:8], type=type_, start=start, end=end,
            snow_cm=snow_cm if type_ == "snow" else 0.0,
        )
        self._events.append(event)
        self._save()
        logger.info("기상 이벤트 추가: %s %s ~ %s", type_, start, end)
        return event

    def remove(self, event_id: str) -> bool:
        before = len(self._events)
        self._events = [e for e in self._events if e.id != event_id]
        if len(self._events) != before:
            self._save()
            return True
        return False

    def active_at(self, virtual_time: datetime) -> WeatherEvent | None:
        """해당 가상시각에 활성인 이벤트 (겹치면 먼저 시작한 것 우선)."""
        active = [e for e in self._events if e.start <= virtual_time < e.end]
        return min(active, key=lambda e: e.start) if active else None

    def _save(self) -> None:
        self._path.write_text(
            json.dumps([e.to_json() for e in self._events], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            self._events = [
                WeatherEvent(
                    id=e["id"], type=e["type"],
                    start=datetime.fromisoformat(e["start"]),
                    end=datetime.fromisoformat(e["end"]),
                    snow_cm=float(e.get("snow_cm", 0.0)),
                )
                for e in raw
            ]
        except (json.JSONDecodeError, KeyError, ValueError):
            logger.exception("이벤트 파일 복원 실패: %s", self._path)
