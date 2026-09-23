"""ScenarioStore 테스트 — 부하/기상 파싱·조회·정렬·다운샘플·복원."""
import io

import pandas as pd
import pytest

from app.scenario import ScenarioStore


def make_load_csv(rows: int = 96, encoding: str = "utf-8") -> bytes:
    ts = pd.date_range("2025-07-01 00:00", periods=rows, freq="15min")
    df = pd.DataFrame({"Timestamp": ts.strftime("%Y-%m-%d %H:%M"),
                       "Load_kW": [50 + i % 40 for i in range(rows)]})
    return df.to_csv(index=False).encode(encoding)


def make_weather_csv(rows: int = 24, encoding: str = "cp949") -> bytes:
    ts = pd.date_range("2025-07-01 00:00", periods=rows, freq="1h")
    df = pd.DataFrame({
        "일시": ts.strftime("%Y-%m-%d %H:%M"),
        "기온": [25 + i % 8 for i in range(rows)],
        "풍속": [1.5] * rows,
        "습도": [60] * rows,
        "일조": [0.5] * rows,
        "일사": [1.2] * rows,
        "전운량": [3] * rows,
        "중하층운량": [2] * rows,
    })
    return df.to_csv(index=False).encode(encoding)


@pytest.fixture
def store(tmp_path):
    return ScenarioStore(tmp_path)


class TestParse:
    def test_load_csv(self, store):
        meta = store.load_upload("load", "load_2025-07.csv", make_load_csv())
        assert meta["rows"] == 96
        assert meta["columns"] == ["load_kw"]
        assert meta["resolution_min"] == 15.0
        assert meta["start"] == "2025-07-01T00:00:00"

    def test_weather_csv_cp949(self, store):
        meta = store.load_upload("weather", "기상_202507.csv", make_weather_csv(encoding="cp949"))
        assert meta["rows"] == 24
        assert set(meta["columns"]) == {"temp", "wind_speed", "humidity", "sunshine",
                                        "irradiance", "cloud_total", "cloud_midlow"}
        assert meta["resolution_min"] == 60.0

    def test_excel(self, store):
        ts = pd.date_range("2025-07-01", periods=10, freq="15min")
        df = pd.DataFrame({"Timestamp": ts, "Load_kW": range(10)})
        buf = io.BytesIO()
        df.to_excel(buf, index=False)
        meta = store.load_upload("load", "load.xlsx", buf.getvalue())
        assert meta["rows"] == 10

    def test_column_with_unit_suffix(self, store):
        """'기온(°C)' 같은 단위 표기 헤더도 인식."""
        csv = ("일시,기온(°C),풍속(m/s),습도(%),일조(hr),일사(MJ/m2),전운량(10분위),중하층운량(10분위)\n"
               "2025-07-01 00:00,25.3,1.2,60,0,0,3,1\n").encode("utf-8")
        meta = store.load_upload("weather", "w.csv", csv)
        assert meta["rows"] == 1

    def test_missing_column_error(self, store):
        with pytest.raises(ValueError, match="필수 컬럼"):
            store.load_upload("load", "bad.csv", b"Time,Value\n2025-01-01,1\n")

    def test_unsorted_rows_sorted(self, store):
        csv = ("Timestamp,Load_kW\n"
               "2025-07-01 01:00,2\n2025-07-01 00:00,1\n2025-07-01 02:00,3\n").encode()
        store.load_upload("load", "l.csv", csv)
        rows = store.rows("load")["rows"]
        assert [r["load_kw"] for r in rows] == [1.0, 2.0, 3.0]


class TestQuery:
    def test_rows_pagination(self, store):
        store.load_upload("load", "l.csv", make_load_csv(96))
        page = store.rows("load", offset=90, limit=100)
        assert page["total"] == 96
        assert len(page["rows"]) == 6

    def test_rows_sort_desc(self, store):
        store.load_upload("load", "l.csv", make_load_csv(96))
        page = store.rows("load", limit=5, sort="load_kw", order="desc")
        values = [r["load_kw"] for r in page["rows"]]
        assert values == sorted(values, reverse=True)

    def test_series_downsample(self, store):
        store.load_upload("load", "l.csv", make_load_csv(3000))
        s = store.series("load", max_points=500)
        assert s["total"] == 3000
        assert s["points"] <= 600
        assert "load_kw" in s["series"]
        assert len(s["timestamps"]) == s["points"]

    def test_series_column_filter(self, store):
        store.load_upload("weather", "w.csv", make_weather_csv())
        s = store.series("weather", columns=["temp", "irradiance"])
        assert set(s["series"]) == {"temp", "irradiance"}

    def test_not_loaded_error(self, store):
        with pytest.raises(KeyError):
            store.rows("weather")


class TestPersistence:
    def test_restore_after_restart(self, tmp_path):
        store1 = ScenarioStore(tmp_path)
        store1.load_upload("load", "l.csv", make_load_csv(96))
        store2 = ScenarioStore(tmp_path)  # 재기동 시뮬레이션
        assert store2.status()["load"]["rows"] == 96
        assert store2.rows("load")["total"] == 96
