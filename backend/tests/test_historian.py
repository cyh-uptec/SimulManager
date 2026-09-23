"""Historian 테스트 — Run 태깅·계측/예측 적재·다운샘플·레코더·성능(DoD)."""
import asyncio
import time

import pytest

from app.events import EventBus
from app.historian import HistorianRecorder, HistorianStore
from app.modbus import RegisterBank
from app.protocol import LINK1, LINK3, encode
from app.protocol.mirror import MIRROR_HR_BASE_IR


@pytest.fixture
def store(tmp_path):
    return HistorianStore(tmp_path / "historian.db")


class TestStore:
    def test_run_lifecycle(self, store):
        run_id = store.start_run("A", 1, "2026-07-06T10:00:00", "2025-10-01T00:00", 50.0)
        store.end_run(run_id, "completed", "2026-07-06T18:00:00", "2025-10-31T23:59", 63.5, 2)
        runs = store.runs()
        assert runs[0]["run_id"] == run_id
        assert runs[0]["status"] == "completed"
        assert runs[0]["final_soc"] == 63.5
        assert runs[0]["disconnect_intervals"] == 2

    def test_measurements_and_downsample(self, store):
        run_id = store.start_run("A", 1, "t", "v", 50.0)
        for i in range(3000):
            store.add_measurement(run_id, f"2025-10-01T{i:05d}",
                                  {"ess_power_kw": float(i), "ess_soc": 50.0,
                                   "pcc_import_kw": 30.0, "pcc_export_kw": 0.0})
        result = store.measurements(run_id, max_points=500)
        assert result["total"] == 3000
        assert result["points"] <= 600
        assert result["stride"] >= 6

    def test_forecast_blocks(self, store):
        run_id = store.start_run("B", 2, "t", "v", 50.0)
        horizons = [{"pv_kw": 10.0 * h, "load_kw": 30.0, "schedule_kw": -40.0,
                     "target_soc": 60.0} for h in range(1, 5)]
        store.add_forecast_block(run_id, "2025-10-01T00:15", 1, horizons)
        h2 = store.forecasts(run_id, horizon=2)
        assert h2["points"] == 1
        assert h2["rows"][0]["pv_kw"] == 20.0
        assert h2["rows"][0]["base_interval"] == 1

    def test_query_performance_43200_points(self, store):
        """DoD: 1 Run 분량(43,200점) 다운샘플 조회 1초 이내."""
        run_id = store.start_run("A", 1, "t", "v", 50.0)
        with store._conn:  # 대량 삽입은 단일 트랜잭션으로
            store._conn.executemany(
                "INSERT INTO measurements_1m (run_id, virtual_time, ess_power_kw, ess_soc, "
                "pcc_import_kw, pcc_export_kw) VALUES (?, ?, ?, ?, ?, ?)",
                [(run_id, f"v{i}", 1.0, 50.0, 30.0, 0.0) for i in range(43200)],
            )
        t0 = time.perf_counter()
        result = store.measurements(run_id, max_points=2000)
        elapsed = time.perf_counter() - t0
        assert result["total"] == 43200
        assert elapsed < 1.0, f"조회 {elapsed:.2f}s — DoD(1s) 초과"


class TestRecorder:
    @pytest.fixture
    def setup(self, tmp_path):
        bus = EventBus()
        store = HistorianStore(tmp_path / "historian.db")
        ctx = {"run_id": None, "vt": "2025-10-01T00:05"}
        recorder = HistorianRecorder(store, bus, lambda: (ctx["run_id"], ctx["vt"]))
        banks = {"link1": RegisterBank(LINK1, bus), "link3": RegisterBank(LINK3, bus)}
        return bus, store, ctx, banks

    async def test_rtds_measurement_recorded(self, setup):
        bus, store, ctx, banks = setup
        ctx["run_id"] = store.start_run("A", 1, "t", "2025-10-01T00:00", 50.0)
        live: list[dict] = []
        bus.subscribe("historian.measurement", lambda t, p: live.append(p))
        # RTDS가 ③HR 400002~400005 FC16 일괄 기록
        raws = [encode(-40.0, LINK3["ess_power"]), encode(52.5, LINK3["ess_soc"]),
                encode(73.8, LINK3["pcc_import"]), encode(0.0, LINK3["pcc_export"])]
        banks["link3"].ctx.setValues(16, LINK3["ess_power"].address, raws)
        await asyncio.sleep(0.05)
        result = store.measurements(ctx["run_id"])
        assert result["total"] == 1
        row = result["rows"][0]
        assert row["ess_power_kw"] == -40.0 and row["ess_soc"] == 52.5
        assert row["virtual_time"] == "2025-10-01T00:05"
        assert live and live[0]["ess_soc"] == 52.5  # 실시간 이벤트 발행

    async def test_ems_forecast_recorded(self, setup):
        bus, store, ctx, banks = setup
        ctx["run_id"] = store.start_run("B", 2, "t", "2025-10-01T00:00", 50.0)
        # EMS 예측 블록(v1.7): 기준 구간 3 + [1구간]만 유효 — [2~4구간] 12워드는 예약(0)
        block = [3,
                 encode(10.0, LINK1["pv_forecast_1"]),
                 encode(30.0, LINK1["load_forecast_1"]),
                 encode(-40.0, LINK1["ess_schedule_1"]),
                 encode(60.0, LINK1["target_soc_1"])]
        block += [0] * 12  # [2~4구간] 예약
        banks["link1"].ctx.setValues(16, MIRROR_HR_BASE_IR + 2, block)
        await asyncio.sleep(0.05)
        fc = store.forecasts(ctx["run_id"], horizon=1)
        assert fc["points"] == 1
        assert fc["rows"][0]["pv_kw"] == 10.0
        assert fc["rows"][0]["load_kw"] == 30.0
        assert fc["rows"][0]["schedule_kw"] == -40.0
        # 예약(0) 워드는 h=2~4 유효 예측으로 적재되지 않는다 (v1.7)
        assert store.forecasts(ctx["run_id"], horizon=3)["points"] == 0

    async def test_no_recording_outside_run(self, setup):
        bus, store, ctx, banks = setup  # run_id None
        banks["link3"].ctx.setValues(16, LINK3["ess_power"].address, [100, 5000, 300, 0])
        await asyncio.sleep(0.05)
        assert store.runs() == []
