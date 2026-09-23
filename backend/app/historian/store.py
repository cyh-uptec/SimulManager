"""Historian 저장소 — Run 태깅·1분 계측·15분 예측 블록 (설계서 §7.1 적재 스키마).

| 테이블               | 해상도      | 원천                                   |
|----------------------|-------------|----------------------------------------|
| runs                 | Run 단위    | Run ID·시나리오·시작/종료·초기/최종 SOC |
| measurements_1m      | 가상 1분    | ③HR 400002~400005 (RTDS 기록)          |
| forecast_blocks_15m  | 15분×h=1    | ①IR 300002~300006 (EMS 기록 — [2~4구간]은 예약, v1.7) |
| pairings             | (Phase 6)   | B-9 예측-실적 페어링 — 스키마만 선정의  |

조회는 다운샘플(stride)로 응답 크기를 제한한다 (1 Run 계측 43,200점 대응).
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    label             TEXT,
    scenario_id       INTEGER,
    status            TEXT DEFAULT 'running',   -- running/completed/stopped/invalid
    started_real      TEXT,
    started_virtual   TEXT,
    ended_real        TEXT,
    ended_virtual     TEXT,
    initial_soc       REAL,
    final_soc         REAL,
    disconnect_intervals INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS measurements_1m (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        INTEGER NOT NULL,
    virtual_time  TEXT NOT NULL,
    ess_power_kw  REAL,
    ess_soc       REAL,
    pcc_import_kw REAL,
    pcc_export_kw REAL
);
CREATE INDEX IF NOT EXISTS idx_meas_run ON measurements_1m (run_id, id);
CREATE TABLE IF NOT EXISTS forecast_blocks_15m (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        INTEGER NOT NULL,
    virtual_time  TEXT NOT NULL,
    base_interval INTEGER NOT NULL,
    horizon       INTEGER NOT NULL,             -- 1~4 구간 선행
    pv_kw         REAL,
    load_kw       REAL,
    schedule_kw   REAL,
    target_soc    REAL
);
CREATE INDEX IF NOT EXISTS idx_fc_run ON forecast_blocks_15m (run_id, base_interval, horizon);
CREATE TABLE IF NOT EXISTS pairings (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id         INTEGER NOT NULL,
    target_interval INTEGER NOT NULL,           -- 대상 구간 k (전역)
    horizon        INTEGER NOT NULL,            -- 선행 h (v1.7: h=1만 사용)
    actual_load_kw REAL,
    forecast_load_kw REAL,
    actual_pv_kw   REAL,
    forecast_pv_kw REAL,
    warmup         INTEGER DEFAULT 0
);
"""

MEASUREMENT_KEYS = ("ess_power_kw", "ess_soc", "pcc_import_kw", "pcc_export_kw")


class HistorianStore:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._lock = threading.Lock()

    # ── Run 태깅 ────────────────────────────────────────────
    def start_run(self, label: str, scenario_id: int, started_real: str,
                  started_virtual: str, initial_soc: float) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO runs (label, scenario_id, status, started_real, started_virtual, initial_soc) "
                "VALUES (?, ?, 'running', ?, ?, ?)",
                (label, scenario_id, started_real, started_virtual, initial_soc),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def end_run(self, run_id: int, status: str, ended_real: str, ended_virtual: str,
                final_soc: float, disconnect_intervals: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE runs SET status=?, ended_real=?, ended_virtual=?, final_soc=?, "
                "disconnect_intervals=? WHERE run_id=?",
                (status, ended_real, ended_virtual, final_soc, disconnect_intervals, run_id),
            )
            self._conn.commit()

    def runs(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT run_id, label, scenario_id, status, started_real, started_virtual, "
                "ended_real, ended_virtual, initial_soc, final_soc, disconnect_intervals, "
                "(SELECT COUNT(*) FROM measurements_1m m WHERE m.run_id = runs.run_id) AS points "
                "FROM runs ORDER BY run_id DESC"
            ).fetchall()
        cols = ["run_id", "label", "scenario_id", "status", "started_real", "started_virtual",
                "ended_real", "ended_virtual", "initial_soc", "final_soc",
                "disconnect_intervals", "points"]
        return [dict(zip(cols, r)) for r in rows]

    # ── 적재 ────────────────────────────────────────────────
    def add_measurement(self, run_id: int, virtual_time: str, values: dict[str, float]) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO measurements_1m (run_id, virtual_time, ess_power_kw, ess_soc, "
                "pcc_import_kw, pcc_export_kw) VALUES (?, ?, ?, ?, ?, ?)",
                (run_id, virtual_time, *(values.get(k) for k in MEASUREMENT_KEYS)),
            )
            self._conn.commit()

    def add_forecast_block(self, run_id: int, virtual_time: str, base_interval: int,
                           horizons: list[dict[str, float]]) -> None:
        """horizons: h=1부터 순서대로 {pv_kw, load_kw, schedule_kw, target_soc}.

        v1.7: 예측 블록은 [1구간]만 전달되므로 통상 h=1 한 건만 온다 (스키마는 h 확장 호환 유지).
        """
        with self._lock:
            self._conn.executemany(
                "INSERT INTO forecast_blocks_15m (run_id, virtual_time, base_interval, horizon, "
                "pv_kw, load_kw, schedule_kw, target_soc) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [(run_id, virtual_time, base_interval, h + 1,
                  row.get("pv_kw"), row.get("load_kw"), row.get("schedule_kw"), row.get("target_soc"))
                 for h, row in enumerate(horizons)],
            )
            self._conn.commit()

    # ── 조회 (다운샘플) ─────────────────────────────────────
    def measurements(self, run_id: int, max_points: int = 2000) -> dict[str, Any]:
        with self._lock:
            total = self._conn.execute(
                "SELECT COUNT(*) FROM measurements_1m WHERE run_id=?", (run_id,)
            ).fetchone()[0]
            stride = max(1, total // max_points)
            if stride > 1:
                rows = self._conn.execute(
                    "SELECT virtual_time, ess_power_kw, ess_soc, pcc_import_kw, pcc_export_kw "
                    "FROM measurements_1m WHERE run_id=? AND (id % ?) = 0 ORDER BY id",
                    (run_id, stride),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT virtual_time, ess_power_kw, ess_soc, pcc_import_kw, pcc_export_kw "
                    "FROM measurements_1m WHERE run_id=? ORDER BY id",
                    (run_id,),
                ).fetchall()
        return {
            "run_id": run_id, "total": total, "points": len(rows), "stride": stride,
            "rows": [
                {"virtual_time": r[0], "ess_power_kw": r[1], "ess_soc": r[2],
                 "pcc_import_kw": r[3], "pcc_export_kw": r[4]}
                for r in rows
            ],
        }

    def forecasts(self, run_id: int, horizon: int = 1, max_points: int = 2000) -> dict[str, Any]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT virtual_time, base_interval, pv_kw, load_kw, schedule_kw, target_soc "
                "FROM forecast_blocks_15m WHERE run_id=? AND horizon=? ORDER BY id LIMIT ?",
                (run_id, horizon, max_points),
            ).fetchall()
        return {
            "run_id": run_id, "horizon": horizon, "points": len(rows),
            "rows": [
                {"virtual_time": r[0], "base_interval": r[1], "pv_kw": r[2],
                 "load_kw": r[3], "schedule_kw": r[4], "target_soc": r[5]}
                for r in rows
            ],
        }

    def close(self) -> None:
        self._conn.close()
