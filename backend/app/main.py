"""SimulManager FastAPI 엔트리 — REST·WebSocket·Modbus 서버·frontend 정적 서빙.

실행: uvicorn app.main:app --host 0.0.0.0 --port 8100
설정: SIMUL_CONFIG 환경변수로 YAML 지정 (기본 config/default.yaml)
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket
from fastapi.staticfiles import StaticFiles
from starlette.types import Scope

from .api.data import router as data_router
from .api.routes import router
from .api.ws import WsBroadcaster
from .clock import VirtualClock
from .config import BACKEND_DIR, load_config
from .events import EventBus
from .historian import HistorianRecorder
from .modbus import ModbusServerManager, RegisterBank
from .protocol import LINK1, LINK3
from .protocol.types import Link
from .scenario import ScenarioStore
from .scenario.events import WeatherEventStore
from .scenario.mapper import EnvironmentSampler
from .sequence import SequenceEngine
from .testing import VirtualNodeManager
from .watch import HeartbeatMonitor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

FRONTEND_DIST = BACKEND_DIR.parent / "frontend" / "dist"


class SpaStaticFiles(StaticFiles):
    """frontend 정적 서빙 — index.html은 no-cache (배포 후 브라우저가 구버전 UI를 계속 보여주는
    휴리스틱 캐시 방지), 해시 파일명인 assets/*는 장기 캐시."""

    async def get_response(self, path: str, scope: Scope):
        response = await super().get_response(path, scope)
        content_type = response.headers.get("content-type", "")
        if content_type.startswith("text/html"):
            response.headers["Cache-Control"] = "no-cache"
        elif path.startswith("assets/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    await app.state.modbus.start()
    app.state.monitor.start()
    yield
    await app.state.virtual.shutdown()
    await app.state.monitor.stop()
    await app.state.modbus.stop()
    await app.state.clock.shutdown()


def create_app(config_path: str | Path | None = None) -> FastAPI:
    config = load_config(config_path)
    bus = EventBus()
    clock = VirtualClock(config.simulation, bus)
    banks = {
        Link.LINK1: RegisterBank(LINK1, bus),
        Link.LINK3: RegisterBank(LINK3, bus),
    }
    modbus = ModbusServerManager(config.network, banks)
    monitor = HeartbeatMonitor(bus, config.timing)
    data_dir = (BACKEND_DIR / config.scenario.data_dir).resolve()
    scenario = ScenarioStore(data_dir)
    weather_events = WeatherEventStore(data_dir)
    sampler = EnvironmentSampler(scenario, weather_events, config.scenario.interpolation)
    engine = SequenceEngine(config, bus, clock, banks, monitor, sampler, data_dir)
    recorder = HistorianRecorder(engine.historian, bus, engine.run_context)
    virtual = VirtualNodeManager(banks, bus)
    broadcaster = WsBroadcaster()
    bus.subscribe("*", broadcaster.on_event)

    # 초기 정적 레지스터 (Phase 3에서 시나리오·시퀀스 엔진이 본격 배포)
    sim = config.simulation
    banks[Link.LINK1].set("accel_factor", sim.accel_seconds_per_15min)
    banks[Link.LINK3].set("accel_factor", sim.accel_seconds_per_15min)
    banks[Link.LINK3].set("initial_soc", sim.initial_soc)

    app = FastAPI(title="SimulManager", version="0.2.0", lifespan=lifespan)
    app.state.config = config
    app.state.bus = bus
    app.state.clock = clock
    app.state.banks = banks
    app.state.modbus = modbus
    app.state.monitor = monitor
    app.state.scenario = scenario
    app.state.weather_events = weather_events
    app.state.engine = engine
    app.state.recorder = recorder
    app.state.virtual = virtual
    app.state.broadcaster = broadcaster
    app.include_router(router)
    app.include_router(data_router)

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket) -> None:
        await broadcaster.handle(ws, clock.status())

    if FRONTEND_DIST.is_dir():
        app.mount("/", SpaStaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")

    return app


app = create_app()
