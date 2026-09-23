"""시나리오 데이터 스토어 — 1개월 부하·기상 실측 데이터의 적재·조회.

시뮬레이션(Phase 3)의 시나리오 DB 원천: 매니저는 이 데이터를 가상 1분마다
링크①·③ 레지스터로 배포한다 (docs/DEVELOPMENT_PLAN.md §3 Phase 3).

지원 포맷: CSV(utf-8/cp949 자동 판별)·Excel(xlsx)
- 부하: Timestamp, Load_kW (월 단위)
- 기상: 일시, 기온, 풍속, 습도, 일조, 일사, 전운량, 중하층운량

업로드 파일은 data/uploads/ 에 보존되어 재기동 시 자동 복원된다.
"""
from __future__ import annotations

import io
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

KINDS = ("load", "weather")

# 원본 헤더 → 내부 컬럼 키 (부분 일치 허용: '기온(°C)' 등 단위 표기 대응)
LOAD_COLUMNS: dict[str, str] = {
    "timestamp": "timestamp",
    "load_kw": "load_kw",
}
WEATHER_COLUMNS: dict[str, str] = {
    "일시": "timestamp",
    "기온": "temp",
    "풍속": "wind_speed",
    "습도": "humidity",
    "일조": "sunshine",
    "일사": "irradiance",
    "전운량": "cloud_total",
    "중하층운량": "cloud_midlow",
}

COLUMN_LABELS: dict[str, str] = {
    "load_kw": "부하 (kW)",
    "temp": "기온 (℃)",
    "wind_speed": "풍속 (m/s)",
    "humidity": "습도 (%)",
    "sunshine": "일조 (hr)",
    "irradiance": "일사 (MJ/m²)",
    "cloud_total": "전운량 (1/10)",
    "cloud_midlow": "중하층운량 (1/10)",
}


class ScenarioStore:
    def __init__(self, data_dir: Path) -> None:
        self._uploads_dir = data_dir / "uploads"
        self._uploads_dir.mkdir(parents=True, exist_ok=True)
        self._meta_path = self._uploads_dir / "meta.json"
        self._frames: dict[str, pd.DataFrame] = {}
        self._meta: dict[str, dict[str, Any]] = {}
        self._restore()

    # ── 적재 ────────────────────────────────────────────────
    def load_upload(self, kind: str, filename: str, content: bytes) -> dict[str, Any]:
        """업로드 파일 파싱·적재·보존. 실패 시 ValueError."""
        if kind not in KINDS:
            raise ValueError(f"지원하지 않는 데이터 종류: {kind}")
        df = self._parse(kind, filename, content)

        suffix = Path(filename).suffix.lower() or ".csv"
        stored = self._uploads_dir / f"{kind}{suffix}"
        for old in self._uploads_dir.glob(f"{kind}.*"):
            old.unlink(missing_ok=True)
        stored.write_bytes(content)

        self._frames[kind] = df
        self._meta[kind] = {
            "filename": filename,
            "stored": stored.name,
            "rows": len(df),
            "start": df["timestamp"].iloc[0].isoformat(),
            "end": df["timestamp"].iloc[-1].isoformat(),
            "resolution_min": self._resolution_min(df),
            "columns": [c for c in df.columns if c != "timestamp"],
            "loaded_at": datetime.now().isoformat(timespec="seconds"),
        }
        self._save_meta()
        logger.info("%s 데이터 적재: %s (%d행, %s ~ %s)", kind, filename, len(df),
                    self._meta[kind]["start"], self._meta[kind]["end"])
        return self._meta[kind]

    def _parse(self, kind: str, filename: str, content: bytes) -> pd.DataFrame:
        suffix = Path(filename).suffix.lower()
        if suffix in (".xlsx", ".xls"):
            raw = pd.read_excel(io.BytesIO(content))
        else:  # CSV — 한국 기상 데이터는 cp949가 흔함
            raw = None
            for enc in ("utf-8-sig", "cp949", "euc-kr"):
                try:
                    raw = pd.read_csv(io.BytesIO(content), encoding=enc)
                    break
                except UnicodeDecodeError:
                    continue
            if raw is None:
                raise ValueError("CSV 인코딩을 인식할 수 없습니다 (utf-8/cp949 지원)")

        mapping = LOAD_COLUMNS if kind == "load" else WEATHER_COLUMNS
        resolved: dict[str, str] = {}
        for source_key, target in mapping.items():
            match = next(
                (c for c in raw.columns
                 if source_key in str(c).strip().lower().replace(" ", "")),
                None,
            )
            if match is None:
                raise ValueError(f"필수 컬럼을 찾을 수 없습니다: '{source_key}' (헤더: {list(raw.columns)})")
            resolved[match] = target

        df = raw[list(resolved)].rename(columns=resolved)
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
        bad_ts = int(df["timestamp"].isna().sum())
        if bad_ts:
            logger.warning("%s: 시각 파싱 실패 %d행 제외", filename, bad_ts)
        df = df.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
        if df.empty:
            raise ValueError("유효한 데이터 행이 없습니다")
        for col in df.columns:
            if col != "timestamp":
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df

    @staticmethod
    def _resolution_min(df: pd.DataFrame) -> float | None:
        if len(df) < 2:
            return None
        return float(df["timestamp"].diff().median().total_seconds() / 60)

    # ── 조회 ────────────────────────────────────────────────
    def status(self) -> dict[str, Any]:
        return {kind: self._meta.get(kind) for kind in KINDS}

    def get_frame(self, kind: str) -> pd.DataFrame:
        """시뮬레이션 엔진용 원본 프레임 접근 (Phase 3)."""
        if kind not in self._frames:
            raise KeyError(f"{kind} 데이터가 로드되지 않았습니다")
        return self._frames[kind]

    def rows(self, kind: str, offset: int = 0, limit: int = 100,
             sort: str | None = None, order: str = "asc") -> dict[str, Any]:
        df = self.get_frame(kind)
        if sort and sort in df.columns:
            df = df.sort_values(sort, ascending=(order != "desc"), na_position="last")
        page = df.iloc[offset:offset + limit]
        records = []
        for _, row in page.iterrows():
            rec = {"timestamp": row["timestamp"].isoformat()}
            for col in df.columns:
                if col != "timestamp":
                    value = row[col]
                    rec[col] = None if pd.isna(value) else float(value)
            records.append(rec)
        return {"total": len(df), "offset": offset, "limit": limit, "rows": records}

    def series(self, kind: str, columns: list[str] | None = None,
               max_points: int = 1500) -> dict[str, Any]:
        """차트용 시계열 — 균등 간격 다운샘플."""
        df = self.get_frame(kind)
        available = [c for c in df.columns if c != "timestamp"]
        selected = [c for c in (columns or available) if c in available]
        stride = max(1, len(df) // max_points)
        sampled = df.iloc[::stride]
        return {
            "timestamps": [ts.isoformat() for ts in sampled["timestamp"]],
            "series": {
                col: [None if pd.isna(v) else float(v) for v in sampled[col]]
                for col in selected
            },
            "labels": {col: COLUMN_LABELS.get(col, col) for col in selected},
            "points": len(sampled),
            "total": len(df),
        }

    # ── 영속화 ──────────────────────────────────────────────
    def _save_meta(self) -> None:
        self._meta_path.write_text(
            json.dumps(self._meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _restore(self) -> None:
        if not self._meta_path.exists():
            return
        try:
            meta = json.loads(self._meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return
        for kind, entry in meta.items():
            stored = self._uploads_dir / entry.get("stored", "")
            if kind in KINDS and stored.exists():
                try:
                    self._frames[kind] = self._parse(kind, stored.name, stored.read_bytes())
                    self._meta[kind] = entry
                    logger.info("%s 데이터 복원: %s (%d행)", kind, entry["filename"], entry["rows"])
                except ValueError:
                    logger.exception("%s 데이터 복원 실패", kind)
