"""시퀀스 로그 저장소 — Historian(SQLite)의 첫 테이블 (Phase 5 events 계열의 선행 구현).

전체 시퀀스를 무제한 적재하되, 조회는 페이지 단위(기본 50건)로만 반환하여
장시간 운전에도 메모리·응답 크기를 일정하게 유지한다 (UI 이전/다음 버튼 대응).

refs 컬럼: 프로토콜 시트번호(①②③)와 참조주소 — 검증 단계용.
로그 양이 커지면 메시지만 남기는 운영 모드로 전환 예정 (컬럼은 유지, 값만 생략).
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sequence_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    real_time    TEXT NOT NULL,
    virtual_time TEXT,
    phase        TEXT,
    source       TEXT DEFAULT 'manager',
    message      TEXT NOT NULL,
    refs         TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_sequence_log_id ON sequence_log (id DESC);
"""


class SequenceLogStore:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._lock = threading.Lock()

    def append(self, entry: dict[str, Any]) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO sequence_log (real_time, virtual_time, phase, source, message, refs) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (entry["real_time"], entry.get("virtual_time"), entry.get("phase"),
                 entry.get("source", "manager"), entry["message"], entry.get("refs", "")),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def page(self, offset: int = 0, limit: int = 50) -> dict[str, Any]:
        """최신순 페이지 조회 — offset 0 = 최신 페이지."""
        with self._lock:
            total = self._conn.execute("SELECT COUNT(*) FROM sequence_log").fetchone()[0]
            rows = self._conn.execute(
                "SELECT id, real_time, virtual_time, phase, source, message, refs "
                "FROM sequence_log ORDER BY id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        entries = [
            {"id": r[0], "real_time": r[1], "virtual_time": r[2], "phase": r[3],
             "source": r[4], "message": r[5], "refs": r[6]}
            for r in rows
        ]
        return {"total": total, "offset": offset, "limit": limit, "entries": entries}

    def clear(self) -> int:
        """전체 삭제 (운영자 조작) — 삭제 건수 반환. id 자동증가도 초기화."""
        with self._lock:
            cur = self._conn.execute("DELETE FROM sequence_log")
            self._conn.execute("DELETE FROM sqlite_sequence WHERE name = 'sequence_log'")
            self._conn.commit()
            return cur.rowcount

    def close(self) -> None:
        self._conn.close()
