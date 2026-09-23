"""시퀀스 로그 저장소·엔진 연동 테스트 — 영속화·페이지네이션·refs·source."""
import pytest

from app.historian import SequenceLogStore


def make_entry(i: int) -> dict:
    return {"real_time": f"2026-07-06T10:00:{i:02d}", "virtual_time": "2025-10-01T00:00",
            "phase": "P4", "source": "manager", "message": f"이벤트 {i}", "refs": "①HR 400006"}


class TestStore:
    def test_append_and_page(self, tmp_path):
        store = SequenceLogStore(tmp_path / "historian.db")
        for i in range(120):
            store.append(make_entry(i))
        page0 = store.page(0, 50)
        assert page0["total"] == 120
        assert len(page0["entries"]) == 50
        assert page0["entries"][0]["message"] == "이벤트 119"  # 최신순

        page2 = store.page(100, 50)  # 마지막 페이지 — 남은 20건
        assert len(page2["entries"]) == 20
        assert page2["entries"][-1]["message"] == "이벤트 0"

    def test_persistence_across_open(self, tmp_path):
        db = tmp_path / "historian.db"
        SequenceLogStore(db).append(make_entry(1))
        store2 = SequenceLogStore(db)  # 재기동 시뮬레이션
        assert store2.page()["total"] == 1

    def test_clear(self, tmp_path):
        """전체 삭제 — 건수 반환, id 자동증가 초기화 (재기동 후에도 빈 상태 유지)."""
        db = tmp_path / "historian.db"
        store = SequenceLogStore(db)
        for i in range(5):
            store.append(make_entry(i))
        assert store.clear() == 5
        assert store.page()["total"] == 0
        assert store.append(make_entry(0)) == 1  # id 1부터 재시작
        assert SequenceLogStore(db).page()["total"] == 1  # 영속 확인

    def test_refs_and_source_stored(self, tmp_path):
        store = SequenceLogStore(tmp_path / "historian.db")
        store.append({"real_time": "t", "message": "m", "source": "ems", "refs": "①IR 300002"})
        entry = store.page()["entries"][0]
        assert entry["source"] == "ems"
        assert entry["refs"] == "①IR 300002"


class TestEngineLogging:
    async def test_run_start_logs_p2_p3_steps(self, tmp_path):
        """Run 시작 시 P2 초기화·P3 운전 개시가 단계 라벨로 분리 기록."""
        from tests.test_sequence_engine import make_clock_env
        clock, banks, engine = await make_clock_env(tmp_path)
        await engine.start_run(1)
        page = engine.sequence_log_page(0, 10)
        p2 = next(e for e in page["entries"] if e["phase"] == "P2" and "초기화" in e["message"])
        p3 = next(e for e in page["entries"] if e["phase"] == "P3")
        assert "①" in p2["refs"] and "③" in p2["refs"]
        assert p2["source"] == "manager"
        assert "운전 개시" in p3["message"]
        await clock.stop()

    async def test_brokering_logged_as_p1(self, tmp_path):
        """연결중개는 P1 단계 라벨로 기록."""
        import asyncio
        from tests.test_sequence_engine import make_clock_env
        from app.protocol.types import Link
        clock, banks, engine = await make_clock_env(tmp_path)
        banks[Link.LINK3].ctx.setValues(5, 0, [1])  # RTDS 운전중
        await asyncio.sleep(0.05)
        page = engine.sequence_log_page(0, 10)
        entry = next(e for e in page["entries"] if "연결중개" in e["message"])
        assert entry["phase"] == "P1"

    async def test_ems_forecast_write_logged(self, tmp_path):
        import asyncio
        from app.protocol.mirror import MIRROR_HR_BASE_IR
        from app.protocol.types import Link
        from tests.test_sequence_engine import make_clock_env
        clock, banks, engine = await make_clock_env(tmp_path)
        # EMS가 예측 블록을 미러로 기록 (FC16, 기준구간 5 + 값 4개)
        banks[Link.LINK1].ctx.setValues(16, MIRROR_HR_BASE_IR + 2, [5, 100, 200, 300, 400])
        await asyncio.sleep(0.05)
        page = engine.sequence_log_page(0, 10)
        entry = next(e for e in page["entries"] if "예측 블록" in e["message"])
        assert entry["source"] == "ems"
        assert "300003~300006" in entry["refs"]  # v1.7: [1구간]만 유효, 300007~300018 예약
        assert "[1구간]" in entry["message"]
