"""REST API — 상태 조회·시계 제어·시뮬레이션 설정·시험 모드."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError

from ..config import NetworkConfig, SimulationConfig, save_user_settings
from ..modbus import ModbusServerManager
from ..protocol.types import Link

router = APIRouter(prefix="/api")

CLOCK_ACTIONS = ("start", "stop", "pause", "resume", "rewind")


@router.get("/status")
async def get_status(request: Request) -> dict:
    clock = request.app.state.clock
    return {**clock.status(), "run": request.app.state.engine.status()}


@router.get("/config")
async def get_config(request: Request) -> dict:
    cfg = request.app.state.config
    return {
        "simulation": cfg.simulation.model_dump(mode="json"),
        "network": {"mode": cfg.network.mode, "web_port": cfg.network.web_port},
    }


@router.get("/nodes")
async def get_nodes(request: Request) -> dict:
    """노드(EMS·RTDS) 생존 상태 + 링크 요청 활동.

    - state/heartbeat/age_s: Heartbeat 감시 결과 (프로토콜 규약 준수 여부)
    - request_age_s: 해당 링크에 마지막 실 Modbus 요청(읽기 포함)이 온 뒤 경과 초 —
      가상 노드·내부 접근은 제외되므로 실제 폴링 여부 판별용. None = 기동 후 요청 없음
    - connections/peers: 링크 서버에 열려 있는 실 TCP 세션 수·주소 —
      폴링 없이 접속만 한 상태도 감지 (single_port 모드는 프록시 라우팅 기준)
    """
    status = request.app.state.monitor.status()
    banks = request.app.state.banks
    conns = request.app.state.modbus.link_connections()
    for node, link in (("ems", Link.LINK1), ("rtds", Link.LINK3)):
        status[node]["request_age_s"] = banks[link].client_activity_age()
        status[node]["connections"] = conns[link]["count"]
        status[node]["peers"] = conns[link]["peers"]
    return status


@router.get("/settings")
async def get_settings(request: Request) -> dict:
    """시뮬레이션 사용자 설정 조회."""
    return request.app.state.config.simulation.model_dump(mode="json")


@router.put("/settings")
async def put_settings(sim: SimulationConfig, request: Request) -> dict:
    """시뮬레이션 설정 변경 — 대기(IDLE) 상태에서만. user_settings.yaml에 영속화."""
    if sim.soc_min >= sim.soc_max:
        raise HTTPException(422, "SOC 하한은 상한보다 작아야 합니다")
    if not (sim.soc_min <= sim.initial_soc <= sim.soc_max):
        raise HTTPException(422, "초기 SOC는 SOC 상하한 범위 안이어야 합니다")

    try:
        await request.app.state.clock.apply_config(sim)
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e

    request.app.state.config.simulation = sim
    banks = request.app.state.banks
    banks[Link.LINK1].set("accel_factor", sim.accel_seconds_per_15min)
    banks[Link.LINK3].set("accel_factor", sim.accel_seconds_per_15min)
    banks[Link.LINK3].set("initial_soc", sim.initial_soc)
    save_user_settings("simulation", sim.model_dump(mode="json"))
    return sim.model_dump(mode="json")


@router.get("/signals")
async def get_signals(request: Request) -> dict:
    """Home 대시보드용 핵심 신호·계측 스냅샷.

    link1: EMS가 읽어가는 지령·중개 비트 (①Coil)
    link3: RTDS에 주는 트리거(③DI)와 RTDS가 매니저에 쓰는 상태·계측 (③Coil·HR)
    """
    banks = request.app.state.banks
    b1, b3 = banks[Link.LINK1], banks[Link.LINK3]
    return {
        "link1": {
            "run_start": b1.get("run_start"),
            "rtds_ready": b1.get("rtds_ready"),       # ①Coil 000004
            "ems_enable": b1.get("ems_enable"),       # ①Coil 000005
        },
        "link3": {
            "initial_soc_trigger": b3.get("initial_soc_trigger"),  # ③DI 100000
            "ess_run_enable_sim": b3.get("ess_run_enable_sim"),    # ③DI 100001
            "rtds_running": b3.get("rtds_running"),                # ③Coil 000000 (RTDS 씀)
            "initial_soc_ack": b3.get("initial_soc_ack"),          # ③Coil 000001 (RTDS 씀)
            "rtds_status_code": b3.get("rtds_status_code"),        # ③HR 400001 (RTDS 씀)
            "ess_power": b3.get("ess_power"),                      # ③HR 400002 [kW]
            "ess_soc": b3.get("ess_soc"),                          # ③HR 400003 [%]
            "pcc_import": b3.get("pcc_import"),                    # ③HR 400004 [kW]
            "pcc_export": b3.get("pcc_export"),                    # ③HR 400005 [kW]
        },
    }


@router.get("/registers/{link_no}")
async def get_registers(link_no: int, request: Request) -> list[dict]:
    """링크별 전 레지스터 현재값 (모니터링·디버그용). 링크 1(EMS측)·3(RTDS측)."""
    banks = request.app.state.banks
    try:
        bank = banks[Link(link_no)]
    except (ValueError, KeyError):
        raise HTTPException(404, f"매니저가 보유한 링크가 아닙니다: {link_no} (가능: 1, 3)")
    return bank.dump()


@router.post("/clock/{action}")
async def clock_action(action: str, request: Request) -> dict:
    if action not in CLOCK_ACTIONS:
        raise HTTPException(404, f"지원하지 않는 동작: {action} (가능: {CLOCK_ACTIONS})")
    clock = request.app.state.clock
    try:
        await getattr(clock, action)()
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e
    return clock.status()


# ── Run 제어 (시퀀스 엔진 — P2·P3 / P5 / P6) ────────────────

@router.post("/run/start")
async def run_start(request: Request, body: dict | None = None) -> dict:
    """Run 시작 — P2 초기화(SOC 주입 트리거) + P3 운전 개시. body: {scenario_id: 1|2}"""
    scenario_id = int((body or {}).get("scenario_id", 1))
    if scenario_id not in (1, 2):
        raise HTTPException(422, "시나리오 ID는 1(Run A 규칙기반) 또는 2(Run B 최적운영)여야 합니다")
    try:
        await request.app.state.engine.start_run(scenario_id)
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e
    return request.app.state.engine.status()


@router.post("/run/stop")
async def run_stop(request: Request) -> dict:
    try:
        await request.app.state.engine.stop_run()
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e
    return request.app.state.engine.status()


@router.post("/run/reset")
async def run_reset(request: Request, body: dict | None = None) -> dict:
    """P6 케이스 전환 — 리셋·되감기. body: {scenario_id: 다음 Run 시나리오(선택)}"""
    raw = (body or {}).get("scenario_id")
    if raw is not None and int(raw) not in (1, 2):
        raise HTTPException(422, "시나리오 ID는 1(Run A) 또는 2(Run B)여야 합니다")
    try:
        await request.app.state.engine.reset_run(int(raw) if raw is not None else None)
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e
    return request.app.state.engine.status()


# ── Historian 이력 조회 (Phase 5) ───────────────────────────

@router.get("/history/runs")
async def history_runs(request: Request) -> list[dict]:
    """Run 목록 (최신순) — 태그·기간·계측 점수."""
    return request.app.state.engine.historian.runs()


@router.get("/history/measurements")
async def history_measurements(run_id: int, request: Request, max_points: int = 2000) -> dict:
    """1분 계측 시계열 (③HR 400002~400005) — 다운샘플 조회."""
    return request.app.state.engine.historian.measurements(
        run_id, max_points=min(max(max_points, 100), 10000))


@router.get("/history/forecasts")
async def history_forecasts(run_id: int, request: Request, horizon: int = 1,
                            max_points: int = 2000) -> dict:
    """15분 예측 블록 (①IR 300002~300006) — 선행 h=1 단일 조회 (설계서 v1.7)."""
    if horizon != 1:
        raise HTTPException(422, "horizon은 1만 지원 — 예측 블록은 [1구간]만 전달(설계서 v1.7)")
    return request.app.state.engine.historian.forecasts(
        run_id, horizon=horizon, max_points=min(max(max_points, 100), 10000))


@router.get("/sequence/log")
async def sequence_log(request: Request, offset: int = 0, limit: int = 50) -> dict:
    """시퀀스 로그 페이지 조회 (최신순, SQLite 영속화). offset 0 = 최신 페이지."""
    limit = min(max(limit, 1), 200)
    return request.app.state.engine.sequence_log_page(offset=max(offset, 0), limit=limit)


@router.delete("/sequence/log")
async def sequence_log_clear(request: Request) -> dict:
    """시퀀스 로그 전체 삭제 — Run 계측 이력(historian)은 삭제하지 않는다."""
    deleted = await request.app.state.engine.sequence_log_clear()
    return {"deleted": deleted}


# ── 네트워크 설정 (Modbus 서버 포트 — 시험 모드 지원) ────────

@router.get("/settings/network")
async def get_network_settings(request: Request) -> dict:
    """Modbus 서버 네트워크 설정 조회 — EMS·RTDS가 접속할 포트."""
    return request.app.state.config.network.model_dump(mode="json")


@router.put("/settings/network")
async def put_network_settings(request: Request, body: dict) -> dict:
    """네트워크 설정 변경 — Modbus 서버 ×2를 새 포트로 즉시 재기동.

    운전 중에도 허용하되 재기동 순간 EMS·RTDS 연결이 끊겼다 재접속된다.
    기동 실패(포트 점유 등) 시 이전 설정으로 자동 복구한다.
    """
    cfg = request.app.state.config
    try:
        net = NetworkConfig.model_validate({**cfg.network.model_dump(), **(body or {})})
    except ValidationError as e:
        raise HTTPException(422, f"네트워크 설정 오류: {e.errors()[0].get('msg', e)}") from e
    for name, port in (("포트", net.port), ("링크① 포트", net.link1_port), ("링크③ 포트", net.link3_port)):
        if not 1 <= port <= 65535:
            raise HTTPException(422, f"{name}는 1~65535 범위여야 합니다: {port}")
    if net.link1_port == net.link3_port:
        raise HTTPException(422, "링크①과 링크③ 포트는 서로 달라야 합니다")
    if net.mode == "single_port" and net.ems_ip == net.rtds_ip:
        raise HTTPException(422, "single_port 모드는 EMS·RTDS IP가 서로 달라야 라우팅됩니다")

    old_manager: ModbusServerManager = request.app.state.modbus
    await old_manager.stop()
    new_manager = ModbusServerManager(net, request.app.state.banks)
    try:
        await new_manager.start()
    except OSError as e:
        await new_manager.stop()
        rollback = ModbusServerManager(cfg.network, request.app.state.banks)
        await rollback.start()
        request.app.state.modbus = rollback
        raise HTTPException(409, f"Modbus 서버 기동 실패({e}) — 이전 설정으로 복구했습니다") from e
    request.app.state.modbus = new_manager
    cfg.network = net
    save_user_settings("network", net.model_dump(mode="json"))
    await request.app.state.bus.publish("network.changed", net.model_dump(mode="json"))
    return net.model_dump(mode="json")


# ── 시험 모드 — 매니저 보유 레지스터 수동 조작 ───────────────

@router.post("/test/write")
async def test_write(request: Request, body: dict) -> dict:
    """시험 모드: 매니저 보유 레지스터에 물리값을 직접 기록.

    EMS 단독 시험 시 RTDS 몫의 트리거(①Coil 'RTDS 준비됨' 등),
    RTDS 단독 시험 시 EMS 몫의 상태(③IR 'EMS 상태' 등)를 운영자가 대신 세운다.
    내부 쓰기(bank.set)라 modbus.write 후킹·시퀀스 로그를 타지 않는다 —
    Run 중에는 시퀀스 엔진이 같은 레지스터를 덮어쓸 수 있음에 유의.
    """
    try:
        link = Link(int(body.get("link", 0)))
        key = str(body["key"])
        value = float(body["value"])
    except (KeyError, TypeError, ValueError) as e:
        raise HTTPException(422, f"입력 오류: link·key·value가 필요합니다 ({e})") from e
    banks = request.app.state.banks
    if link not in banks:
        raise HTTPException(404, f"매니저가 보유한 링크가 아닙니다: {int(link)} (가능: 1, 3)")
    bank = banks[link]
    if key not in bank.map:
        raise HTTPException(404, f"링크{int(link)}에 없는 레지스터 key: {key}")
    try:
        bank.set(key, value)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return {"link": int(link), "key": key, "value": bank.get(key)}


# ── 시험 모드 — 가상 노드 (정상 상대 노드 재현) ───────────────

@router.get("/test/virtual")
async def virtual_status(request: Request) -> dict:
    """가상 노드 활성 상태 — {"rtds": bool, "ems": bool}."""
    return request.app.state.virtual.status()


@router.post("/test/virtual/{node}")
async def virtual_set(node: str, request: Request, body: dict | None = None) -> dict:
    """가상 노드 켜기/끄기 — EMS 시험 시 'rtds', RTDS 시험 시 'ems'.

    켜면 정상 노드의 프로토콜 행동(HB 1s·상태 비트·P1/P2 응답·간이 계측)을
    실운영과 동일한 쓰기 후킹 경로로 자동 수행한다.
    """
    enabled = bool((body or {}).get("enabled", True))
    try:
        return await request.app.state.virtual.set(node, enabled)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


# ── 기상 이벤트 (폭염·폭설 — 운영자 임의 입력) ───────────────

@router.get("/events")
async def list_events(request: Request) -> list[dict]:
    return [e.to_json() for e in request.app.state.weather_events.list()]


@router.post("/events")
async def add_event(request: Request, body: dict) -> dict:
    from datetime import datetime as dt
    try:
        event = request.app.state.weather_events.add(
            type_=str(body.get("type", "")),
            start=dt.fromisoformat(body["start"]),
            end=dt.fromisoformat(body["end"]),
            snow_cm=float(body.get("snow_cm", 0.0)),
        )
    except (KeyError, ValueError) as e:
        raise HTTPException(422, f"이벤트 입력 오류: {e}") from e
    return event.to_json()


@router.delete("/events/{event_id}")
async def delete_event(event_id: str, request: Request) -> dict:
    if not request.app.state.weather_events.remove(event_id):
        raise HTTPException(404, f"이벤트를 찾을 수 없습니다: {event_id}")
    return {"deleted": event_id}
