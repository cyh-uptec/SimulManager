"""EnvironmentSampler 테스트 — 오프셋 매핑·보간·일사 환산·NaN·이벤트 오버라이드."""
from datetime import datetime

import pandas as pd
import pytest

from app.scenario import ScenarioStore
from app.scenario.events import WeatherEventStore
from app.scenario.mapper import EnvironmentSampler

RUN_START = datetime(2025, 7, 1, 0, 0)  # 가상 시작 (데이터 달력과 무관 — 오프셋 매핑)


def load_csv() -> bytes:
    # 15분 해상도, 값 = 구간 순번 ×10
    ts = pd.date_range("2025-10-01 00:00", periods=8, freq="15min")
    return pd.DataFrame({"Timestamp": ts, "Load_kW": [10.0 * i for i in range(8)]}) \
        .to_csv(index=False).encode()


def weather_csv() -> bytes:
    ts = pd.date_range("2025-10-01 00:00", periods=4, freq="1h")
    return pd.DataFrame({
        "일시": ts,
        "기온": [10.0, 20.0, None, 40.0],   # 3번째 결측 → 직전 유효값
        "풍속": [1, 1, 1, 1], "습도": [50, 50, 50, 50],
        "일조": [0, 0.5, 1, 1],
        "일사": [None, 1.0, 2.0, 3.0],       # 야간 결측 → 0
        "전운량": [0, 0, 0, 0], "중하층운량": [0, 0, 0, 0],
    }).to_csv(index=False).encode()


@pytest.fixture
def stores(tmp_path):
    store = ScenarioStore(tmp_path)
    store.load_upload("load", "l.csv", load_csv())
    store.load_upload("weather", "w.csv", weather_csv())
    events = WeatherEventStore(tmp_path)
    return store, events


class TestSampling:
    def test_offset_mapping_and_zoh(self, stores):
        store, events = stores
        s = EnvironmentSampler(store, events, "hold")
        # 가상 +37분 → 데이터 00:37 → ZOH: 00:30 값(부하 20), 기상 00:00 값
        env = s.sample(RUN_START.replace(minute=37), RUN_START)
        assert env.load_kw == 20.0
        assert env.temp_c == 10.0

    def test_linear_interpolation(self, stores):
        store, events = stores
        s = EnvironmentSampler(store, events, "linear")
        # 부하: 00:07.5 → 10*(0→1) 중간 = 5.0
        env = s.sample(RUN_START.replace(minute=7, second=0) , RUN_START)
        assert env.load_kw == pytest.approx(10.0 * 7 / 15, abs=0.01)
        # 기온: +30분 → 10→20 중간 15.0
        env2 = s.sample(RUN_START.replace(minute=30), RUN_START)
        assert env2.temp_c == pytest.approx(15.0, abs=0.01)

    def test_irradiance_passthrough_and_nan(self, stores):
        """v1.0: 레지스터 단위가 MJ/m² — 환산 없이 실측값 그대로 배포."""
        store, events = stores
        s = EnvironmentSampler(store, events, "hold")
        # +0분: 일사 NaN → 0
        assert s.sample(RUN_START, RUN_START).irradiance_mj == 0.0
        # +60분: 1.0 MJ/m² 그대로
        env = s.sample(RUN_START.replace(hour=1), RUN_START)
        assert env.irradiance_mj == pytest.approx(1.0)

    def test_nan_carries_last_valid(self, stores):
        store, events = stores
        s = EnvironmentSampler(store, events, "hold")
        s.sample(RUN_START.replace(hour=1), RUN_START)        # 기온 20 캐시
        env = s.sample(RUN_START.replace(hour=2), RUN_START)  # 기온 NaN → 20 유지
        assert env.temp_c == 20.0

    def test_clamp_beyond_end(self, stores):
        store, events = stores
        s = EnvironmentSampler(store, events, "hold")
        env = s.sample(RUN_START.replace(hour=10), RUN_START)  # 데이터 끝 초과
        assert env.load_kw == 70.0  # 마지막 값 유지

    def test_ready(self, tmp_path, stores):
        store, events = stores
        assert EnvironmentSampler(store, events).ready()
        empty = ScenarioStore(tmp_path / "empty")
        assert not EnvironmentSampler(empty, events).ready()


class TestEventOverride:
    def test_default_normal(self, stores):
        store, events = stores
        s = EnvironmentSampler(store, events, "hold")
        env = s.sample(RUN_START, RUN_START)
        assert (env.weather_code, env.snow_cm) == (0, 0.0)

    def test_heatwave_and_snow(self, stores):
        store, events = stores
        events.add("heatwave", RUN_START, RUN_START.replace(hour=1))
        events.add("snow", RUN_START.replace(hour=2), RUN_START.replace(hour=3), snow_cm=12.5)
        s = EnvironmentSampler(store, events, "hold")
        assert s.sample(RUN_START.replace(minute=30), RUN_START).weather_code == 1
        env = s.sample(RUN_START.replace(hour=2, minute=10), RUN_START)
        assert (env.weather_code, env.snow_cm) == (2, 12.5)
        # 이벤트 종료 후 정상 복귀
        assert s.sample(RUN_START.replace(hour=3), RUN_START).weather_code == 0

    def test_event_validation(self, stores):
        _, events = stores
        with pytest.raises(ValueError):
            events.add("taepung", RUN_START, RUN_START.replace(hour=1))
        with pytest.raises(ValueError):
            events.add("snow", RUN_START.replace(hour=1), RUN_START)

    def test_snow_register_limit(self, stores):
        # ③IR 300006 UINT16 ×100 한계(655.35cm) 초과 거부 — 레지스터 인코딩 보호
        _, events = stores
        with pytest.raises(ValueError, match="레지스터 한계"):
            events.add("snow", RUN_START, RUN_START.replace(hour=1), snow_cm=700.0)

    def test_load_clamped_to_register_limit(self, tmp_path):
        # 부하가 인코딩 한계(655.35kW) 초과 시 배포 중단 대신 클램프 (원자성 보호)
        store = ScenarioStore(tmp_path)
        ts = pd.date_range("2025-10-01 00:00", periods=4, freq="15min")
        store.load_upload("load", "big.csv", pd.DataFrame(
            {"Timestamp": ts, "Load_kW": [700.0] * 4}).to_csv(index=False).encode())
        store.load_upload("weather", "w.csv", weather_csv())
        s = EnvironmentSampler(store, WeatherEventStore(tmp_path), "hold")
        assert s.sample(RUN_START, RUN_START).load_kw == pytest.approx(655.35)

    def test_event_persistence(self, tmp_path, stores):
        store, events = stores
        events.add("snow", RUN_START, RUN_START.replace(hour=1), snow_cm=5.0)
        restored = WeatherEventStore(events._path.parent)
        assert len(restored.list()) == 1
        assert restored.list()[0].snow_cm == 5.0
