"""에뮬레이터 통합 스모크 테스트 — 매니저 + 간이 EMS·RTDS 풀 루프 (Phase 4 DoD).

검증: P1 연결중개 → P2 SOC 핸드셰이크 → P3 개시 → P4 루프에서
EMS 지령(CMD Seq)·RTDS 계측(RES Seq)·예측 블록·HB 생존이 실제 Modbus TCP로 왕복하는지.
실행 시간 약 20~25초 (가속배율 2 s → 가상 1분 = 실 0.133 s).
"""
import asyncio
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest
from pymodbus.client import AsyncModbusTcpClient

from app.config import AppConfig, NetworkConfig, SimulationConfig, TimingConfig
from app.events import EventBus
from app.modbus import ModbusServerManager, RegisterBank
from app.protocol import LINK1, LINK2, LINK3, decode
from app.protocol.types import Link
from app.scenario import ScenarioStore
from app.scenario.events import WeatherEventStore
from app.scenario.mapper import EnvironmentSampler
from app.sequence import SequenceEngine
from app.watch import HeartbeatMonitor

ROOT = Path(__file__).resolve().parents[2]
LINK1_PORT, LINK3_PORT, LINK2_PORT = 15120, 15121, 15122
START = datetime(2025, 7, 1, 0, 0)


def make_data(store: ScenarioStore) -> None:
    ts = pd.date_range("2025-10-01 00:00", periods=200, freq="15min")
    store.load_upload("load", "l.csv", pd.DataFrame(
        {"Timestamp": ts, "Load_kW": [30.0 + (i % 8) for i in range(200)]}).to_csv(index=False).encode())
    th = pd.date_range("2025-10-01 00:00", periods=50, freq="1h")
    store.load_upload("weather", "w.csv", pd.DataFrame({
        "일시": th, "기온": [18.0] * 50, "풍속": [1] * 50, "습도": [55] * 50,
        "일조": [0.5] * 50, "일사": [1.5] * 50, "전운량": [2] * 50, "중하층운량": [1] * 50,
    }).to_csv(index=False).encode())


def spawn(script: str, *args: str) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, str(ROOT / "emulators" / script), *args],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=str(ROOT),
    )


async def wait_for(predicate, timeout: float, what: str) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.3)
    raise AssertionError(f"시간 초과: {what}")


@pytest.mark.slow
async def test_full_loop_with_emulators(tmp_path):
    # ── 매니저 스택 ──
    config = AppConfig()
    config.network = NetworkConfig(bind="127.0.0.1", mode="per_link_port",
                                   link1_port=LINK1_PORT, link3_port=LINK3_PORT)
    config.simulation = SimulationConfig(start_virtual_time=START, days=2,
                                         accel_seconds_per_15min=2.0)  # 가상 1분=0.133s
    config.timing = TimingConfig()
    bus = EventBus()
    from app.clock import VirtualClock
    clock = VirtualClock(config.simulation, bus)
    banks = {Link.LINK1: RegisterBank(LINK1, bus), Link.LINK3: RegisterBank(LINK3, bus)}
    modbus = ModbusServerManager(config.network, banks)
    monitor = HeartbeatMonitor(bus, config.timing)
    store = ScenarioStore(tmp_path)
    make_data(store)
    sampler = EnvironmentSampler(store, WeatherEventStore(tmp_path), "hold")
    engine = SequenceEngine(config, bus, clock, banks, monitor, sampler, tmp_path)
    await modbus.start()
    monitor.start()

    rtds_proc = spawn("rtds_emulator.py", "--manager", "127.0.0.1",
                      "--manager-port", str(LINK3_PORT),
                      "--listen", "127.0.0.1", "--listen-port", str(LINK2_PORT))
    ems_proc = spawn("ems_emulator.py", "--manager", "127.0.0.1",
                     "--manager-port", str(LINK1_PORT),
                     "--rtds", "127.0.0.1", "--rtds-port", str(LINK2_PORT))
    try:
        # P1: 양 노드 접속·HB 생존 + 연결중개
        await wait_for(lambda: monitor.status()["rtds"]["state"] == "alive", 15, "RTDS HB 생존")
        await wait_for(lambda: monitor.status()["ems"]["state"] == "alive", 15, "EMS HB 생존")
        await wait_for(lambda: banks[Link.LINK1].get("rtds_ready") == 1, 5, "연결중개(①RTDS 준비됨)")

        # P2+P3: Run A 시작
        await engine.start_run(scenario_id=1)

        # P2 핸드셰이크 완결 (RTDS ack → 트리거 해제)
        await wait_for(lambda: engine.status()["soc_injection_pending"] is False, 10, "SOC 핸드셰이크")

        # P4: 약 실 12초 = 가상 90분 (6구간) 운전
        await asyncio.sleep(12)

        # EMS→매니저: 예측 블록이 미러로 기록되었는지
        assert banks[Link.LINK1].get("forecast_done") == 1
        assert banks[Link.LINK1].get("pv_forecast_1") >= 0

        # RTDS→매니저: 1분 결과(③HR)가 기록되었는지 (00시대 Run A 충전 → SOC 상승)
        soc_link3 = banks[Link.LINK3].get("ess_soc")
        assert soc_link3 > 50.0, f"야간 충전으로 SOC 상승 기대 (현재 {soc_link3})"

        # 링크② 직접 검증: 지령·계측 왕복
        probe = AsyncModbusTcpClient("127.0.0.1", port=LINK2_PORT, timeout=2)
        assert await probe.connect()
        hr = (await probe.read_holding_registers(0, count=12, slave=1)).registers
        assert hr[11] > 0, "CMD Seq가 증가해야 함 (EMS 지령)"
        assert hr[0] == 2, "Run A 제어모드=2(경제)"
        p_cmd = decode(hr[1], LINK2["p_command"])
        assert p_cmd == pytest.approx(-40.0), f"야간 충전 지령 −40 kW 기대 (실제 {p_cmd})"
        ir = (await probe.read_input_registers(0, count=37, slave=1)).registers
        assert ir[0] > 0, "RES Seq가 증가해야 함 (RTDS 계측)"
        assert decode(ir[32], LINK2["charge_cmd_echo"]) == pytest.approx(40.0)  # 충전 echo
        ess_p = decode(ir[4], LINK2["ess_power"])
        assert ess_p == pytest.approx(-40.0, abs=2.0), f"ESS 충전 중 기대 (실제 {ess_p})"
        di = (await probe.read_discrete_inputs(0, count=6, slave=1)).bits[:6]
        assert di[0] and di[1] and di[3] and di[4]  # 운전중·충전중·Ready·계통연계
        probe.close()

        # 단절 없음 확인 후 종료
        assert engine.status()["disconnect_intervals"] == 0
        await engine.stop_run()
    finally:
        for proc in (rtds_proc, ems_proc):
            proc.kill()
        try:
            out = rtds_proc.stdout.read().decode("utf-8", errors="replace")
            print("--- RTDS 출력(끝부분) ---\n", out[-1500:])
            out = ems_proc.stdout.read().decode("utf-8", errors="replace")
            print("--- EMS 출력(끝부분) ---\n", out[-1500:])
        except Exception:
            pass
        await monitor.stop()
        await modbus.stop()
        await clock.shutdown()
