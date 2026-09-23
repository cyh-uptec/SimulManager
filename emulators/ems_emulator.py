"""간이 EMS 에뮬레이터 — 시뮬매니저 단독 PC 검증용 (Phase 4).

역할 (프로토콜맵·시퀀스 설계서 준수, 레지스터는 backend protocol SSOT 사용):
- 링크① Client: 매니저에서 시각·기상·운전 Coil 폴링, HB·상태·예측 블록을 HR 미러로 기록.
- 링크② Client: ①'RTDS 준비됨' 확인 후 RTDS 접속(연결중개), 정적 제약 세트,
  구간 경계마다 스케줄 산정 → P지령 기록 후 CMD Seq +1 (원자성), 워치독 가상 1분 +1.
- Run A(시나리오 1): TOU 시간대 규칙 — 23~09시 충전 −40 kW / 10~12·13~17시 방전 / 그 외 대기 (§5.1)
- Run B(시나리오 2): 간이 피크저감 — 이동평균 초과 부하를 방전으로 상쇄 + 야간 충전 (§5.2 축약)

실행: python emulators/ems_emulator.py [--manager 127.0.0.1] [--manager-port 7000]
                                        [--rtds 127.0.0.1] [--rtds-port 5022]
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import time
from collections import deque

from pymodbus.client import AsyncModbusTcpClient

from app.protocol import LINK1, LINK2, decode, encode
from app.protocol.mirror import MIRROR_HR_BASE_DI, MIRROR_HR_BASE_IR

logging.basicConfig(level=logging.INFO, format="%(asctime)s EMS  %(levelname)s: %(message)s")
logger = logging.getLogger("ems")

REAL_TICK_S = 0.1
CHARGE_KW = 40.0            # Run A 야간 정출력 충전 (§5.1)
DISCHARGE_CAP_KW = 66.7     # Run A 최대부하 균등 방전 (§5.1)
PEAK_HOURS = {10, 11, 13, 14, 15, 16}   # 최대부하 시간대 (시 단위)
NIGHT_HOURS = {23, 0, 1, 2, 3, 4, 5, 6, 7, 8}  # 경부하 (23:00~09:00)


class EmsEmulator:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.hb = 0
        self.cmd_seq = 0
        self.watchdog = 0
        self.last_interval = -1
        self.last_vminute = -1
        self.res_seq_seen = -1
        self.load_history: deque[float] = deque(maxlen=96)
        self.last_load_kw = 0.0
        self.rtds: AsyncModbusTcpClient | None = None
        self.static_sent = False

    async def run(self) -> None:
        while True:
            try:
                await self._session()
            except (ConnectionError, asyncio.TimeoutError, OSError) as e:
                logger.warning("링크① 단절(%s) — 재접속 대기", e)
                self._drop_rtds()
            await asyncio.sleep(2.0)

    async def _session(self) -> None:
        mgr = AsyncModbusTcpClient(self.args.manager, port=self.args.manager_port, timeout=1)
        if not await mgr.connect():
            raise ConnectionError("매니저 접속 실패")
        logger.info("링크① 접속: %s:%d", self.args.manager, self.args.manager_port)
        # EMS 준비완료 (DI 미러)
        await mgr.write_register(MIRROR_HR_BASE_DI + LINK1["ems_ready"].address, 1, slave=1)
        last_hb_real = 0.0
        try:
            while True:
                now = time.monotonic()
                if now - last_hb_real >= 1.0:  # HB 실 1 s (IR 미러)
                    last_hb_real = now
                    self.hb = (self.hb + 1) & 0xFFFF
                    await mgr.write_register(MIRROR_HR_BASE_IR + LINK1["ems_heartbeat"].address,
                                             self.hb, slave=1)

                hr = (await mgr.read_holding_registers(0, count=12, slave=1)).registers
                coils = (await mgr.read_coils(0, count=6, slave=1)).bits[:6]
                run_start, run_stop, paused = coils[0], coils[1], coils[2]
                rtds_ready, ems_enable = coils[4], coils[5]
                scenario_id = hr[7]
                vminute, hour, interval = hr[4], hr[3], hr[6]

                if rtds_ready and self.rtds is None:
                    await self._connect_rtds()  # 연결중개: ①Coil4 확인 후 접속 (E-2)
                if not rtds_ready:
                    self._drop_rtds()

                operating = run_start and ems_enable and not run_stop and not paused
                if operating and self.rtds is not None:
                    if vminute != self.last_vminute:  # 가상 1분 사이클 (A-5~A-6)
                        self.last_vminute = vminute
                        await self._minute_cycle()
                    if interval != self.last_interval:  # 15분 구간 경계 (B-1~B-8)
                        self.last_interval = interval
                        await self._interval_cycle(mgr, hr, scenario_id, hour, interval)
                elif run_stop and self.rtds is not None and self.cmd_seq > 0:
                    await self._shutdown_commands()  # P5: P=0 → Enable 해제

                await asyncio.sleep(REAL_TICK_S)
        finally:
            mgr.close()
            self._drop_rtds()

    # ── 링크② ──────────────────────────────────────────────
    async def _connect_rtds(self) -> None:
        client = AsyncModbusTcpClient(self.args.rtds, port=self.args.rtds_port, timeout=1)
        if not await client.connect():
            logger.warning("RTDS 접속 실패 — 다음 폴링에서 재시도")
            return
        self.rtds = client
        self.static_sent = False
        logger.info("링크② 접속: %s:%d (연결중개 완료)", self.args.rtds, self.args.rtds_port)

    def _drop_rtds(self) -> None:
        if self.rtds is not None:
            self.rtds.close()
            self.rtds = None

    async def _send_static_constraints(self, scenario_id: int) -> None:
        """P3-4 정적 제약 일괄 세트."""
        r = self.rtds
        await r.write_coils(0, [True, True, False, True, True, False], slave=1)  # Enable·!E-Stop
        await r.write_register(LINK2["ess_control_mode"].address,
                               2 if scenario_id == 1 else 3, slave=1)  # A=2경제/B=3피크저감
        await r.write_register(LINK2["pf_command"].address, encode(1.0, LINK2["pf_command"]), slave=1)
        await r.write_register(LINK2["reactive_mode"].address, 1, slave=1)  # PF 고정
        await r.write_registers(LINK2["soc_max"].address, [
            encode(90.0, LINK2["soc_max"]), encode(10.0, LINK2["soc_min"]),
            encode(250.0, LINK2["discharge_limit"]), encode(250.0, LINK2["charge_limit"]),
        ], slave=1)
        self.static_sent = True
        logger.info("정적 제약 세트 완료 (모드 %d)", 2 if scenario_id == 1 else 3)

    async def _minute_cycle(self) -> None:
        """A-5 계측 스냅샷(RES Seq 게이트) + A-6 워치독."""
        r = self.rtds
        ir = (await r.read_input_registers(0, count=8, slave=1)).registers
        if ir[0] != self.res_seq_seen:  # RES Seq 변화 시에만 스냅샷 래치
            self.res_seq_seen = ir[0]
            self.last_load_kw = float(decode(ir[3], LINK2["load_power"]))
            self.load_history.append(self.last_load_kw)
        self.watchdog = (self.watchdog + 1) & 0xFFFF
        await r.write_register(LINK2["controller_watchdog"].address, self.watchdog, slave=1)

    async def _interval_cycle(self, mgr, hr: list[int], scenario_id: int,
                              hour: int, interval: int) -> None:
        """B-1~B-5: 예측 → 스케줄 → 예측 블록 기록 → 지령(CMD Seq 마지막)."""
        if not self.static_sent:
            await self._send_static_constraints(scenario_id)
        irradiance_mj = hr[8]  # ①HR 400008 ×1 MJ/m² (v1.0) — EMS 내부에서 W/m² 환산
        irradiance_wm2 = irradiance_mj * 1_000_000 / 3600
        pv_forecast = min(150.0, irradiance_wm2 / 1000.0 * 150.0)
        load_forecast = self.last_load_kw if self.load_history else 30.0

        if scenario_id == 2:
            p_cmd = self._schedule_run_b(hour, load_forecast)
        else:
            p_cmd = self._schedule_run_a(hour)

        # B-4: 예측 블록 (①IR 미러 — 기준 구간 + [1구간]만 유효 기록)
        # 예측 블록은 [1구간](300003~300006)만 전달 — [2~4구간](300007~300018)은 예약, 0 기록 (설계서 v1.7)
        block = [
            interval,
            encode(pv_forecast, LINK1["pv_forecast_1"]),
            encode(load_forecast, LINK1["load_forecast_1"]),
            encode(p_cmd, LINK1["ess_schedule_1"]),
            encode(60.0, LINK1["target_soc_1"]),
        ]
        block += [0] * 12  # [2~4구간] 예약
        await mgr.write_registers(MIRROR_HR_BASE_IR + LINK1["forecast_base_interval"].address,
                                  block, slave=1)
        await mgr.write_register(MIRROR_HR_BASE_DI + LINK1["forecast_done"].address, 1, slave=1)

        # B-5: 지령 기록 → CMD Seq 마지막 +1 (원자성)
        r = self.rtds
        await r.write_registers(LINK2["p_command"].address, [
            encode(p_cmd, LINK2["p_command"]),
            encode(0.0, LINK2["q_command"]),
        ], slave=1)
        await r.write_register(LINK2["target_soc"].address, encode(60.0, LINK2["target_soc"]), slave=1)
        self.cmd_seq = (self.cmd_seq + 1) & 0xFFFF
        await r.write_register(LINK2["cmd_seq"].address, self.cmd_seq, slave=1)
        logger.info("구간 %d (%02d시): P지령 %.1f kW (CMD Seq %d, %s)",
                    interval, hour, p_cmd, self.cmd_seq, "RunB" if scenario_id == 2 else "RunA")

    async def _shutdown_commands(self) -> None:
        """P5: 정지 지령."""
        r = self.rtds
        await r.write_register(LINK2["p_command"].address, encode(0.0, LINK2["p_command"]), slave=1)
        self.cmd_seq = (self.cmd_seq + 1) & 0xFFFF
        await r.write_register(LINK2["cmd_seq"].address, self.cmd_seq, slave=1)
        await r.write_coils(0, [False, False, False, False, False, False], slave=1)
        self.cmd_seq = 0
        logger.info("운전 정지 — P=0·Enable 해제 (P5)")

    # ── 스케줄 (§5.1 / §5.2 축약) ───────────────────────────
    def _schedule_run_a(self, hour: int) -> float:
        if hour in NIGHT_HOURS:
            return -CHARGE_KW
        if hour in PEAK_HOURS or hour == 12:  # 10~12·13~17 근사 (12시 중간부하 단순화 포함 안 함)
            if hour == 12:
                return 0.0
            return min(DISCHARGE_CAP_KW, max(self.last_load_kw, 0.0))
        return 0.0

    def _schedule_run_b(self, hour: int, load_forecast: float) -> float:
        if hour in NIGHT_HOURS:
            return -CHARGE_KW
        if len(self.load_history) >= 4:
            avg = sum(self.load_history) / len(self.load_history)
            if load_forecast > avg:  # 평균 초과분 피크저감
                return min(250.0, load_forecast - avg)
        return 0.0


def main() -> None:
    parser = argparse.ArgumentParser(description="간이 EMS 에뮬레이터")
    parser.add_argument("--manager", default="127.0.0.1")
    parser.add_argument("--manager-port", type=int, default=7000)
    parser.add_argument("--rtds", default="127.0.0.1")
    parser.add_argument("--rtds-port", type=int, default=5022)
    args = parser.parse_args()
    asyncio.run(EmsEmulator(args).run())


if __name__ == "__main__":
    main()
