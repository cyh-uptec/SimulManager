"""데이터 API — 부하·기상 실측 데이터 업로드·조회 (시나리오 DB 원천)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, UploadFile

from ..scenario.store import KINDS

router = APIRouter(prefix="/api/data")


def _store(request: Request):
    return request.app.state.scenario


def _check_kind(kind: str) -> None:
    if kind not in KINDS:
        raise HTTPException(404, f"지원하지 않는 데이터 종류: {kind} (가능: load, weather)")


@router.post("/upload/{kind}")
async def upload(kind: str, file: UploadFile, request: Request) -> dict:
    """부하(load)/기상(weather) 파일 업로드 — CSV·Excel."""
    _check_kind(kind)
    content = await file.read()
    if not content:
        raise HTTPException(422, "빈 파일입니다")
    try:
        meta = _store(request).load_upload(kind, file.filename or f"{kind}.csv", content)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    await request.app.state.bus.publish("data.loaded", {"kind": kind, **meta})
    return meta


@router.get("/status")
async def status(request: Request) -> dict:
    """로드된 데이터 현황 (파일·행수·기간·해상도)."""
    return _store(request).status()


@router.get("/{kind}/rows")
async def rows(kind: str, request: Request, offset: int = 0, limit: int = 100,
               sort: str | None = None, order: str = "asc") -> dict:
    """리스트 조회 — 페이지네이션·컬럼 정렬."""
    _check_kind(kind)
    limit = min(max(limit, 1), 1000)
    try:
        return _store(request).rows(kind, offset=max(offset, 0), limit=limit,
                                    sort=sort, order=order)
    except KeyError as e:
        raise HTTPException(404, str(e.args[0])) from e


@router.get("/{kind}/series")
async def series(kind: str, request: Request, columns: str | None = None,
                 max_points: int = 1500) -> dict:
    """차트용 시계열 — 균등 다운샘플. columns=쉼표구분 (생략 시 전체)."""
    _check_kind(kind)
    cols = [c.strip() for c in columns.split(",") if c.strip()] if columns else None
    try:
        return _store(request).series(kind, columns=cols,
                                      max_points=min(max(max_points, 100), 5000))
    except KeyError as e:
        raise HTTPException(404, str(e.args[0])) from e
