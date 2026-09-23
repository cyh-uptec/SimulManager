"""환경 샘플러 — 실측 데이터를 가상시각 기준 1분 값으로 변환 (Phase 3 배포 원천).

시각 매핑 (오프셋 방식): 데이터의 달력 날짜와 무관하게, Run 시작으로부터의
가상 경과시간을 각 데이터셋 시작점에 더해 조회한다.
  데이터 시각 = 데이터 시작 + (가상시각 − Run 시작 가상시각)
요일 패턴 정합이 필요하면 설정의 가상 시작시각을 데이터 시작과 같은 요일로 맞출 것.

변환 규칙:
- 보간: hold(ZOH, 기본) | linear — 두 Run 동일 적용 (설계서 §8)
- 일사: 실측 MJ/m²(1시간 적산)를 **그대로 배포** — 프로토콜맵 v1.0에서 레지스터 단위가
  MJ/m²로 변경되어 환산 불필요 (docs/OPEN_ISSUES.md #7 종결). 노드측 W/m² 환산은
  각 노드(EMS 예측·RTDS PV 모델) 내부 책임
- 야간 일사 결측(NaN) → 0. 기타 컬럼 NaN → 직전 유효값 유지
- 적설량·기상 상태코드: 실측이 아닌 운영자 이벤트(WeatherEventStore)에서 산출
- 데이터 범위 초과 시 마지막 값 유지(경고 1회)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from ..protocol import LINK3
from ..protocol.codec import UINT16_MAX
from .events import WEATHER_CODE, WeatherEventStore
from .store import ScenarioStore

logger = logging.getLogger(__name__)

# 부하 상한 — ③IR 300008 UINT16 ×100 인코딩 한계 (655.35 kW). 초과 시 클램프+경고
LOAD_KW_MAX = UINT16_MAX / LINK3["load_profile_ref"].scale


@dataclass
class EnvSample:
    """가상 1분 배포값 (물리 단위 — 레지스터 인코딩은 protocol codec이 수행)."""

    load_kw: float          # ③IR 300008 부하 프로파일 기준값
    irradiance_mj: float    # ①HR 400008 / ③IR 300004 일사량 [MJ/m², 1시간 적산]
    temp_c: float           # ①HR 400009 / ③IR 300005 외기온도 [℃]
    snow_cm: float          # ①HR 400010 / ③IR 300006 적설량 [cm] — 이벤트 산출
    weather_code: int       # ①HR 400011 / ③IR 300007 — 0정상/1폭염/2폭설


class EnvironmentSampler:
    def __init__(self, store: ScenarioStore, events: WeatherEventStore,
                 interpolation: str = "hold") -> None:
        self._store = store
        self._events = events
        self._interpolation = interpolation
        self._warned_overrun: set[str] = set()
        self._last_valid: dict[str, float] = {}

    def ready(self) -> bool:
        try:
            self._store.get_frame("load")
            self._store.get_frame("weather")
            return True
        except KeyError:
            return False

    def sample(self, virtual_time: datetime, run_start_virtual: datetime) -> EnvSample:
        elapsed = virtual_time - run_start_virtual
        load = self._value_at("load", "load_kw", elapsed)
        if load > LOAD_KW_MAX or load < 0:
            # 배포 중단(원자성 훼손) 대신 레지스터 한계로 클램프 (encode 예외 방지)
            if "load_clamp" not in self._warned_overrun:
                logger.warning("부하 %skW가 레지스터 한계(0~%s)를 벗어나 클램프", load, LOAD_KW_MAX)
                self._warned_overrun.add("load_clamp")
            load = min(max(load, 0.0), LOAD_KW_MAX)
        temp = self._value_at("weather", "temp", elapsed)
        irr_mj = self._value_at("weather", "irradiance", elapsed, nan_as_zero=True)

        event = self._events.active_at(virtual_time)
        weather_code = WEATHER_CODE[event.type] if event else 0
        snow_cm = event.snow_cm if event and event.type == "snow" else 0.0

        return EnvSample(
            load_kw=round(load, 2),
            irradiance_mj=round(irr_mj, 2),  # 레지스터 ×1 인코딩 시 정수로 반올림됨
            temp_c=round(temp, 2),
            snow_cm=snow_cm,
            weather_code=weather_code,
        )

    # ── 내부 ────────────────────────────────────────────────
    def _value_at(self, kind: str, column: str, elapsed: timedelta,
                  nan_as_zero: bool = False) -> float:
        df = self._store.get_frame(kind)
        ts_target = df["timestamp"].iloc[0] + elapsed
        ts = df["timestamp"].to_numpy()
        values = df[column].to_numpy(dtype=float)

        pos = int(np.searchsorted(ts, np.datetime64(ts_target), side="right")) - 1
        if pos < 0:
            pos = 0
        if pos >= len(df) - 1:
            if kind not in self._warned_overrun and ts_target > df["timestamp"].iloc[-1]:
                logger.warning("%s 데이터 범위 초과 — 마지막 값 유지 (%s > %s)",
                               kind, ts_target, df["timestamp"].iloc[-1])
                self._warned_overrun.add(kind)
            raw = values[-1]
        elif self._interpolation == "linear":
            t0, t1 = ts[pos], ts[pos + 1]
            v0, v1 = values[pos], values[pos + 1]
            if np.isnan(v0) or np.isnan(v1):
                raw = v0
            else:
                frac = (np.datetime64(ts_target) - t0) / (t1 - t0)
                raw = float(v0 + (v1 - v0) * float(frac))
        else:  # hold (ZOH)
            raw = values[pos]

        key = f"{kind}.{column}"
        if np.isnan(raw):
            if nan_as_zero:
                return 0.0
            return self._last_valid.get(key, 0.0)
        self._last_valid[key] = float(raw)
        return float(raw)
