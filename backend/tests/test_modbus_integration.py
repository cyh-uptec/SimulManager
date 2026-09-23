"""Modbus 서버 통합 테스트 — 실제 AsyncModbusTcpClient로 읽기/쓰기·HB 단절 판정.

Phase 2 DoD: 두 링크 접속·읽기/쓰기 성공, HB 중단 시 3s(테스트: 0.6s) 내 단절 판정.
"""
import asyncio

import pytest
from pymodbus.client import AsyncModbusTcpClient

from app.config import NetworkConfig, TimingConfig
from app.events import EventBus
from app.modbus import ModbusServerManager, RegisterBank
from app.protocol import LINK1, LINK3
from app.protocol.mirror import MIRROR_HR_BASE_IR
from app.protocol.types import Link
from app.watch import HeartbeatMonitor, NodeState

LINK1_PORT, LINK3_PORT, PROXY_PORT = 15020, 15021, 15025


@pytest.fixture
async def stack():
    """매니저 서버 스택 (per_link_port 모드, 테스트 포트)."""
    net = NetworkConfig(
        bind="127.0.0.1", mode="per_link_port",
        link1_port=LINK1_PORT, link3_port=LINK3_PORT,
    )
    timing = TimingConfig(alive_timeout_s=0.6, heartbeat_period_s=0.1)
    bus = EventBus()
    banks = {Link.LINK1: RegisterBank(LINK1, bus), Link.LINK3: RegisterBank(LINK3, bus)}
    manager = ModbusServerManager(net, banks)
    monitor = HeartbeatMonitor(bus, timing)
    await manager.start()
    monitor.start()
    yield banks, monitor, bus
    await monitor.stop()
    await manager.stop()


async def connect(port: int) -> AsyncModbusTcpClient:
    client = AsyncModbusTcpClient("127.0.0.1", port=port)
    assert await client.connect()
    return client


class TestServerReadWrite:
    async def test_link1_read_manager_registers(self, stack):
        """EMS 역할: 매니저가 보유한 시각·가속 HR 읽기 (FC03)."""
        banks, _, _ = stack
        banks[Link.LINK1].set("accel_factor", 10)
        banks[Link.LINK1].set("scenario_id", 1)
        client = await connect(LINK1_PORT)
        rr = await client.read_holding_registers(5, count=3, slave=1)  # 400005~400007
        assert not rr.isError()
        assert rr.registers == [10, 0, 1]  # 가속배율, 구간 인덱스, 시나리오 ID
        client.close()

    async def test_link1_read_coils(self, stack):
        """EMS 역할: 운전 지령 Coil 읽기 (FC01) — 연결중개 비트 포함."""
        banks, _, _ = stack
        banks[Link.LINK1].set("rtds_ready", 1)
        client = await connect(LINK1_PORT)
        rr = await client.read_coils(0, count=6, slave=1)
        assert not rr.isError()
        assert rr.bits[4] is True  # ①Coil 000004 RTDS 준비됨
        client.close()

    async def test_link1_ems_writes_via_mirror(self, stack):
        """EMS 역할: HR 미러(FC16)로 IR 기록 → 매니저 뱅크의 IR 반영."""
        banks, _, _ = stack
        client = await connect(LINK1_PORT)
        wr = await client.write_registers(MIRROR_HR_BASE_IR + 2, [37], slave=1)  # 예측 기준 구간
        assert not wr.isError()
        await asyncio.sleep(0.05)
        assert banks[Link.LINK1].get("forecast_base_interval") == 37
        client.close()

    async def test_link3_rtds_write_hr_and_coil(self, stack):
        """RTDS 역할: HR(FC16 결과)·Coil(FC05 운전중) 쓰기 — 규격 정합 경로."""
        banks, _, _ = stack
        client = await connect(LINK3_PORT)
        wr = await client.write_registers(2, [(-1234) & 0xFFFF], slave=1)  # ESS 전력 −123.4kW(×10)
        assert not wr.isError()
        wc = await client.write_coil(0, True, slave=1)  # RTDS 운전중
        assert not wc.isError()
        await asyncio.sleep(0.05)
        assert banks[Link.LINK3].get("ess_power") == -123.4
        assert banks[Link.LINK3].get("rtds_running") == 1
        client.close()

    async def test_link3_rtds_reads_environment(self, stack):
        """RTDS 역할: 환경 IR 읽기 (FC04)."""
        banks, _, _ = stack
        banks[Link.LINK3].set("initial_soc", 50.0)
        client = await connect(LINK3_PORT)
        rr = await client.read_input_registers(11, count=1, slave=1)
        assert not rr.isError()
        assert rr.registers == [5000]
        client.close()


class TestHeartbeatWatch:
    async def test_alive_then_lost(self, stack):
        """HB 증가 → alive, 중단 → 0.6s 내 lost 판정 + 이벤트."""
        _, monitor, bus = stack
        transitions: list[tuple[str, dict]] = []
        bus.subscribe("watch.alive", lambda t, p: transitions.append((t, p)))
        bus.subscribe("watch.lost", lambda t, p: transitions.append((t, p)))

        client = await connect(LINK3_PORT)
        for i in range(1, 4):  # RTDS HB 1→2→3
            await client.write_register(0, i, slave=1)
            await asyncio.sleep(0.15)
        assert monitor.status()["rtds"]["state"] == NodeState.ALIVE.value

        await asyncio.sleep(1.0)  # HB 중단 → timeout(0.6s) 초과
        assert monitor.status()["rtds"]["state"] == NodeState.LOST.value
        topics = [t for t, _ in transitions]
        assert "watch.alive" in topics and "watch.lost" in topics
        client.close()

    async def test_ems_heartbeat_via_mirror(self, stack):
        """EMS HB는 링크① HR 미러 경유 — 감시자가 인식하는지."""
        _, monitor, _ = stack
        client = await connect(LINK1_PORT)
        await client.write_register(MIRROR_HR_BASE_IR + 0, 1, slave=1)
        await asyncio.sleep(0.1)
        assert monitor.status()["ems"]["state"] == NodeState.ALIVE.value
        client.close()


class TestRoutingProxy:
    async def test_single_port_routing(self):
        """single_port 모드: 프록시 경유로 링크 서버 접근 (127.0.0.1 → 링크①)."""
        net = NetworkConfig(
            bind="127.0.0.1", mode="single_port", port=PROXY_PORT,
            link1_port=LINK1_PORT + 10, link3_port=LINK3_PORT + 10,
            ems_ip="127.0.0.1", rtds_ip="192.0.2.99",  # 테스트: 로컬 IP는 EMS로 라우팅
        )
        bus = EventBus()
        banks = {Link.LINK1: RegisterBank(LINK1, bus), Link.LINK3: RegisterBank(LINK3, bus)}
        banks[Link.LINK1].set("scenario_id", 2)
        manager = ModbusServerManager(net, banks)
        await manager.start()
        try:
            client = await connect(PROXY_PORT)
            rr = await client.read_holding_registers(7, count=1, slave=1)
            assert not rr.isError()
            assert rr.registers == [2]  # 링크①의 시나리오 ID → IP 라우팅 성공
            client.close()
        finally:
            await manager.stop()


class TestLinkConnections:
    async def test_tcp_tracking_per_link_port(self):
        """TCP 접속 추적 — 폴링 없이 접속만 해도 count 반영, 종료 시 제거."""
        net = NetworkConfig(
            bind="127.0.0.1", mode="per_link_port",
            link1_port=15030, link3_port=15031,
        )
        bus = EventBus()
        banks = {Link.LINK1: RegisterBank(LINK1, bus), Link.LINK3: RegisterBank(LINK3, bus)}
        manager = ModbusServerManager(net, banks)
        await manager.start()
        try:
            assert manager.link_connections()[Link.LINK1]["count"] == 0
            client = await connect(15030)  # 접속만, 요청 없음
            await asyncio.sleep(0.1)
            conns = manager.link_connections()
            assert conns[Link.LINK1]["count"] == 1
            assert conns[Link.LINK1]["peers"][0].startswith("127.0.0.1:")
            assert conns[Link.LINK3]["count"] == 0
            assert banks[Link.LINK1].client_activity_age() is None  # 폴링과 독립
            client.close()
            await asyncio.sleep(0.3)
            assert manager.link_connections()[Link.LINK1]["count"] == 0
        finally:
            await manager.stop()

    async def test_tcp_tracking_single_port_proxy(self):
        """single_port 모드 — 프록시 라우팅 기준으로 링크별 접속 집계."""
        net = NetworkConfig(
            bind="127.0.0.1", mode="single_port", port=15035,
            link1_port=15036, link3_port=15037,
            ems_ip="127.0.0.1", rtds_ip="192.0.2.99",
        )
        bus = EventBus()
        banks = {Link.LINK1: RegisterBank(LINK1, bus), Link.LINK3: RegisterBank(LINK3, bus)}
        manager = ModbusServerManager(net, banks)
        await manager.start()
        try:
            client = await connect(15035)  # 로컬 IP → 링크①로 라우팅
            rr = await client.read_holding_registers(0, count=1, slave=1)
            assert not rr.isError()
            conns = manager.link_connections()
            assert conns[Link.LINK1]["count"] == 1
            assert conns[Link.LINK3]["count"] == 0
            client.close()
            await asyncio.sleep(0.3)
            assert manager.link_connections()[Link.LINK1]["count"] == 0
        finally:
            await manager.stop()
