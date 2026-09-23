"""VirtualNodeManager 테스트 — 가상 RTDS/EMS가 실운영 파이프라인(후킹 경유)을 타는지 검증."""
import asyncio
from datetime import datetime

import pytest

from app.clock import VirtualClock
from app.config import AppConfig, SimulationConfig, TimingConfig
from app.events import EventBus
from app.modbus import RegisterBank
from app.protocol import LINK1, LINK3
from app.protocol.types import Link
from app.scenario import ScenarioStore
from app.scenario.events import WeatherEventStore
from app.scenario.mapper import EnvironmentSampler
from app.sequence import SequenceEngine
from app.testing import VirtualNodeManager
from app.watch import HeartbeatMonitor


@pytest.fixture
async def env(tmp_path):
    config = AppConfig()
    config.simulation = SimulationConfig(start_virtual_time=datetime(2025, 7, 1), days=1)
    config.timing = TimingConfig(alive_timeout_s=0.5, heartbeat_period_s=0.1)
    bus = EventBus()
    clock = VirtualClock(config.simulation, bus)
    banks = {Link.LINK1: RegisterBank(LINK1, bus), Link.LINK3: RegisterBank(LINK3, bus)}
    monitor = HeartbeatMonitor(bus, config.timing)
    sampler = EnvironmentSampler(ScenarioStore(tmp_path), WeatherEventStore(tmp_path), "hold")
    engine = SequenceEngine(config, bus, clock, banks, monitor, sampler, tmp_path)
    virtual = VirtualNodeManager(banks, bus)
    yield banks, virtual, engine
    await virtual.shutdown()


async def test_virtual_rtds_mediation_and_heartbeat(env):
    """가상 RTDS: 운전중 세트 → P1 연결중개(①rtds_ready), HB 증가, 종료 시 해제."""
    banks, virtual, _ = env
    b1, b3 = banks[Link.LINK1], banks[Link.LINK3]
    await virtual.set("rtds", True)
    await asyncio.sleep(0.2)
    assert b3.get("rtds_running") == 1
    assert b1.get("rtds_ready") == 1  # 엔진 P1 연결중개 반응 (후킹 경유 증거)
    hb0 = b3.get("rtds_heartbeat")
    await asyncio.sleep(1.2)
    assert b3.get("rtds_heartbeat") > hb0
    await virtual.set("rtds", False)
    await asyncio.sleep(0.2)
    assert b3.get("rtds_running") == 0
    assert b1.get("rtds_ready") == 0


async def test_virtual_rtds_soc_handshake(env):
    """가상 RTDS: ③DI0 트리거 세트 → ack 응답, 트리거 해제 → ack 해제."""
    banks, virtual, _ = env
    b3 = banks[Link.LINK3]
    await virtual.set("rtds", True)
    b3.set("initial_soc_trigger", 1)
    await asyncio.sleep(1.3)
    assert b3.get("initial_soc_ack") == 1
    b3.set("initial_soc_trigger", 0)
    await asyncio.sleep(1.3)
    assert b3.get("initial_soc_ack") == 0


async def test_client_activity_excludes_virtual_and_internal(env):
    """요청 활동 스탬프 — 실 클라이언트 경로만 기록, 가상 노드·내부 접근은 제외."""
    banks, virtual, _ = env
    b3 = banks[Link.LINK3]
    assert b3.client_activity_age() is None
    b3.get("accel_factor")                    # 내부 읽기 — 스탬프 없음
    b3.set("scenario_id", 1)                  # 내부 쓰기 — 스탬프 없음
    assert b3.client_activity_age() is None
    await virtual.set("rtds", True)           # 가상 노드 쓰기 — 스탬프 없음
    await asyncio.sleep(0.3)
    assert b3.client_activity_age() is None
    await virtual.set("rtds", False)
    b3.ctx.getValues(4, 0, 1)                 # 실 클라이언트 읽기 경로 — 스탬프
    assert b3.client_activity_age() is not None


async def test_virtual_ems_alive_and_status(env):
    """가상 EMS: 미러 경유 준비완료·HB → 생존감시 alive → 엔진이 ③'EMS 상태'=1."""
    banks, virtual, _ = env
    b1, b3 = banks[Link.LINK1], banks[Link.LINK3]
    await virtual.set("ems", True)
    await asyncio.sleep(1.3)
    assert b1.get("ems_ready") == 1        # ①DI0 — 미러 HR 410000 매핑 확인
    assert b1.get("ems_heartbeat") > 0     # ①IR 300000 — 미러 HR 430000 매핑 확인
    assert b3.get("ems_status") == 1       # watch.alive → 엔진 반응
    await virtual.set("ems", False)
    await asyncio.sleep(0.2)
    assert b1.get("ems_ready") == 0
