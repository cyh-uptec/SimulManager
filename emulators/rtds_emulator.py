"""간이 RTDS 에뮬레이터 — 시뮬매니저 단독 PC 검증용 (Phase 4).

역할 (프로토콜맵·시퀀스 설계서 준수, 레지스터는 backend protocol SSOT 사용):
- 링크② Server: EMS의 지령(Coil·HR) 수신, 계측·상태(DI·IR) 제공. CMD Seq 변화 시에만 지령 래치.
- 링크③ Client: 매니저에서 환경(가속·구간·기상·부하·초기SOC) 읽기, HB·결과(HR)·운전중(Coil) 기록.
- 간이 모델: PV 150 kW(일사·온도·적설), 배터리 500 kWh SOC 적분(효율 95%), P지령 램프 추종,
  PCC 수전/역송 = 부하 − PV − P_ESS, 워치독 3가상분 정체 시 안전상태(P=0).

실행: python emulators/rtds_emulator.py [--manager 127.0.0.1] [--manager-port 5021] [--listen-port 5022]
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import time

from pymodbus.client import AsyncModbusTcpClient
from pymodbus.datastore import ModbusServerContext
from pymodbus.server import ModbusTcpServer

from app.modbus.bank import RegisterBank
from app.protocol import LINK2, LINK3
from app.protocol.codec import words_from_u32
from app.protocol.types import ObjType

logging.basicConfig(level=logging.INFO, format="%(asctime)s RTDS %(levelname)s: %(message)s")
logger = logging.getLogger("rtds")

REAL_TICK_S = 0.1          # 모델 스텝 주기 (실시간)
PV_RATED_KW = 150.0
BATT_KWH = 500.0
EFFICIENCY = 0.95
RAMP_KW_PER_VMIN = 250.0   # P지령 램프 (가상 1분당)
WATCHDOG_STALE_VMIN = 3.0


class RtdsModel:
    """PV·배터리·PCC 간이 모델 — 가상시간 적분."""

    def __init__(self) -> None:
        self.soc = 50.0
        self.p_actual_kw = 0.0       # +방전/−충전
        self.discharge_kwh = 0.0
        self.charge_kwh = 0.0

    def pv_output(self, irradiance_mj: float, temp_c: float, snow_cm: float) -> float:
        # ③IR 300004 일사량은 MJ/m²(1시간 적산, v1.0) — 모델 내부에서 W/m² 평균으로 환산
        irradiance_wm2 = irradiance_mj * 1_000_000 / 3600
        pv = PV_RATED_KW * min(irradiance_wm2 / 1000.0, 1.2)
        if temp_c > 25:  # 온도 디레이팅 −0.4%/℃
            pv *= max(0.7, 1 - 0.004 * (temp_c - 25))
        if snow_cm > 0:  # 적설 시 출력 저감
            pv *= max(0.0, 1 - snow_cm / 10.0)
        return max(0.0, min(pv, PV_RATED_KW))

    def step(self, dt_h: float, p_cmd_kw: float, soc_min: float, soc_max: float,
             discharge_limit: float, charge_limit: float, enabled: bool) -> None:
        target = p_cmd_kw if enabled else 0.0
        target = min(max(target, -charge_limit), discharge_limit)
        # SOC 한계 인터록
        if target > 0 and self.soc <= soc_min:
            target = 0.0
        if target < 0 and self.soc >= soc_max:
            target = 0.0
        # 램프 추종
        max_step = RAMP_KW_PER_VMIN * dt_h * 60
        delta = target - self.p_actual_kw
        self.p_actual_kw += min(max(delta, -max_step), max_step)
        # SOC 적분 (1% = 5 kWh), 효율 반영
        energy = self.p_actual_kw * dt_h
        if energy > 0:      # 방전
            self.soc -= energy / EFFICIENCY / (BATT_KWH / 100)
            self.discharge_kwh += energy
        elif energy < 0:    # 충전
            self.soc -= energy * EFFICIENCY / (BATT_KWH / 100)
            self.charge_kwh += -energy
        self.soc = min(max(self.soc, 0.0), 100.0)


class RtdsEmulator:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.bank = RegisterBank(LINK2)   # 링크② 데이터스토어 (RTDS=Server)
        self.model = RtdsModel()
        self.hb = 0
        self.res_seq = 0
        self.cmd_seq_seen = 0
        self.cmd: dict[str, float] = {"p_command": 0.0, "target_soc": 50.0, "soc_max": 90.0,
                                      "soc_min": 10.0, "discharge_limit": 250.0, "charge_limit": 250.0}
        self.watchdog_value = -1
        self.watchdog_stale_vmin = 0.0
        self.injected_ack = False
        self.vmin_accum = 0.0      # 가상 분 누적 (분 단위 작업 트리거)
        self.reset_seen = 0

    async def run(self) -> None:
        server = ModbusTcpServer(
            context=ModbusServerContext(slaves=self.bank.ctx, single=True),
            address=(self.args.listen, self.args.listen_port),
        )
        asyncio.create_task(server.serve_forever())
        logger.info("링크② Server 기동: %s:%d (EMS 접속 대기)", self.args.listen, self.args.listen_port)
        while True:
            try:
                await self._session()
            except (ConnectionError, asyncio.TimeoutError, OSError) as e:
                logger.warning("링크③ 단절(%s) — 재접속 대기", e)
            await asyncio.sleep(2.0)  # 재접속 간격 1~5 s

    async def _session(self) -> None:
        client = AsyncModbusTcpClient(self.args.manager, port=self.args.manager_port, timeout=1)
        if not await client.connect():
            raise ConnectionError("매니저 접속 실패")
        logger.info("링크③ 접속: %s:%d", self.args.manager, self.args.manager_port)
        await client.write_coil(LINK3["rtds_running"].address, True, slave=1)  # ★연결중개 출발점
        last_real = time.monotonic()
        last_hb_real = 0.0
        try:
            while True:
                now = time.monotonic()
                dt_real = now - last_real
                last_real = now

                env = (await client.read_input_registers(0, count=12, slave=1)).registers
                di = (await client.read_discrete_inputs(0, count=3, slave=1)).bits[:3]
                accel = env[0] or self.args.accel_fallback
                dt_vmin = dt_real * 900 / accel / 60  # 실초 → 가상분
                dt_h = dt_vmin / 60

                await self._handle_reset(di[2])
                await self._handle_soc_injection(client, di[0], env[11])
                self._latch_commands()
                self._check_watchdog(dt_vmin)

                # 환경 → 모델 (스케일: protocol SSOT 기준 수동 복원 대신 decode 사용)
                irr = env[4]                       # ③IR 300004 ×1 MJ/m² (v1.0)
                temp = self._dec("ambient_temp", env[5])
                snow = self._dec("snow_depth", env[6])
                load_kw = self._dec("load_profile_ref", env[8])
                pv_kw = self.model.pv_output(irr, temp, snow)

                enabled = bool(di[1]) and self._ess_enabled() and not self._watchdog_stale()
                self.model.step(dt_h, self.cmd["p_command"], self.cmd["soc_min"],
                                self.cmd["soc_max"], self.cmd["discharge_limit"],
                                self.cmd["charge_limit"], enabled)

                self.vmin_accum += dt_vmin
                if self.vmin_accum >= 1.0:  # 가상 1분 경계 — 계측 블록·링크③ 결과 기록
                    self.vmin_accum %= 1.0
                    self._update_link2_measurements(env[1], pv_kw, load_kw)
                    await self._write_link3_results(client, pv_kw, load_kw)

                if now - last_hb_real >= 1.0:  # HB 실 1 s
                    last_hb_real = now
                    self.hb = (self.hb + 1) & 0xFFFF
                    await client.write_register(LINK3["rtds_heartbeat"].address, self.hb, slave=1)

                await asyncio.sleep(REAL_TICK_S)
        finally:
            client.close()

    # ── 링크③ 상호작용 ─────────────────────────────────────
    async def _handle_soc_injection(self, client, trigger: bool, initial_soc_raw: int) -> None:
        if trigger and not self.injected_ack:
            self.model.soc = initial_soc_raw / 100.0
            await client.write_coil(LINK3["initial_soc_ack"].address, True, slave=1)
            self.injected_ack = True
            logger.info("초기 SOC 주입: %.1f%% → ack 기록", self.model.soc)
        elif not trigger and self.injected_ack:
            await client.write_coil(LINK3["initial_soc_ack"].address, False, slave=1)
            self.injected_ack = False
            logger.info("SOC 핸드셰이크 완결 (ack 해제)")

    async def _handle_reset(self, reset: bool) -> None:
        if reset and not self.reset_seen:
            self.model.discharge_kwh = self.model.charge_kwh = 0.0
            self.model.p_actual_kw = 0.0
            self.cmd_seq_seen = 0
            logger.info("리셋 감지 — 누적 전력량·지령 초기화 (P6)")
        self.reset_seen = reset

    async def _write_link3_results(self, client, pv_kw: float, load_kw: float) -> None:
        """③HR 400002~400005 — Historian 1분 시계열 원천 (스케일 ×10)."""
        pcc_import, pcc_export = self._pcc(pv_kw, load_kw)
        regs = [
            self._enc3("ess_power", self.model.p_actual_kw),
            self._enc3("ess_soc", self.model.soc),
            self._enc3("pcc_import", pcc_import),
            self._enc3("pcc_export", pcc_export),
        ]
        await client.write_registers(LINK3["ess_power"].address, regs, slave=1)

    # ── 링크② (자기 뱅크) ──────────────────────────────────
    def _latch_commands(self) -> None:
        """CMD Seq 변화 시에만 지령 블록 일괄 래치 (원자성)."""
        seq = self.bank.get_raw(ObjType.HR, LINK2["cmd_seq"].address)[0]
        if seq == self.cmd_seq_seen:
            return
        self.cmd_seq_seen = seq
        for key in ("p_command", "target_soc", "soc_max", "soc_min",
                    "discharge_limit", "charge_limit"):
            self.cmd[key] = float(self.bank.get(key))
        # 지령 echo (②IR 300031/300032 — 적용값)
        p = self.cmd["p_command"]
        self.bank.set("discharge_cmd_echo", max(p, 0.0))
        self.bank.set("charge_cmd_echo", max(-p, 0.0))
        logger.info("지령 래치 (CMD Seq %d): P=%.2f kW, 목표 SOC %.1f%%",
                    seq, p, self.cmd["target_soc"])

    def _check_watchdog(self, dt_vmin: float) -> None:
        wd = self.bank.get_raw(ObjType.HR, LINK2["controller_watchdog"].address)[0]
        if wd != self.watchdog_value:
            self.watchdog_value = wd
            self.watchdog_stale_vmin = 0.0
        else:
            self.watchdog_stale_vmin += dt_vmin

    def _watchdog_stale(self) -> bool:
        stale = self.cmd_seq_seen > 0 and self.watchdog_stale_vmin > WATCHDOG_STALE_VMIN
        return stale

    def _ess_enabled(self) -> bool:
        coils = self.bank.get_raw(ObjType.COIL, 0, 6)
        return bool(coils[0] and coils[1] and not coils[2])  # run·control enable, !E-Stop

    def _update_link2_measurements(self, interval_index: int, pv_kw: float, load_kw: float) -> None:
        """②DI·IR 계측 갱신 — 블록 기록 후 RES Seq 마지막 +1 (원자성)."""
        m = self.model
        pcc_import, pcc_export = self._pcc(pv_kw, load_kw)
        safe_state = self._watchdog_stale()
        self.bank.set("rtds_running", 1)
        self.bank.set("ess_charging", 1 if m.p_actual_kw < -0.05 else 0)
        self.bank.set("ess_discharging", 1 if m.p_actual_kw > 0.05 else 0)
        self.bank.set("ess_ready", 1)
        self.bank.set("grid_tied", 1)
        self.bank.set("rtds_pcs_fault", 0)

        self.bank.set("result_interval", interval_index)
        self.bank.set("pv_power", pv_kw)
        self.bank.set("load_power", load_kw)
        self.bank.set("ess_power", m.p_actual_kw)
        self.bank.set("ess_soc", m.soc)
        self.bank.set("pcc_import", pcc_import)
        self.bank.set("pcc_export", pcc_export)
        self.bank.set("pcc_voltage", 380.0)
        self.bank.set("grid_freq", 60.0)
        self.bank.set("pcs_p", m.p_actual_kw)
        self.bank.set("pcs_s", abs(m.p_actual_kw))
        self.bank.set("pcs_pf", 1.0 if m.p_actual_kw >= 0 else -1.0)
        self.bank.set("dc_link_voltage", 750.0)
        self.bank.set("bank_soc", m.soc)
        self.bank.set("bank_soh", 100.0)
        self.bank.set("pcs_temp", 35.0)
        self.bank.set("battery_temp", 28.0)
        self.bank.set("pcs_state_word", 1 if safe_state else 3)  # 1대기/3계통연계
        self.bank.set("fault_word", 0)
        self.bank.set("rtds_status_code", 0)
        for prefix, kwh in (("discharge", m.discharge_kwh), ("charge", m.charge_kwh)):
            hi, lo = words_from_u32(round(kwh * 10))  # big-endian, ×10 kWh
            self.bank.set_raw(ObjType.IR, LINK2[f"{prefix}_energy_hi"].address, [hi, lo])
        self.res_seq = (self.res_seq + 1) & 0xFFFF
        self.bank.set("res_seq", self.res_seq)  # ★블록 완료 후 마지막

    # ── 헬퍼 ────────────────────────────────────────────────
    def _pcc(self, pv_kw: float, load_kw: float) -> tuple[float, float]:
        net = load_kw - pv_kw - self.model.p_actual_kw
        return (max(net, 0.0), max(-net, 0.0))

    @staticmethod
    def _dec(key: str, raw: int) -> float:
        from app.protocol import decode
        return float(decode(raw, LINK3[key]))

    @staticmethod
    def _enc3(key: str, value: float) -> int:
        from app.protocol import encode
        return encode(value, LINK3[key])


def main() -> None:
    parser = argparse.ArgumentParser(description="간이 RTDS 에뮬레이터")
    parser.add_argument("--manager", default="127.0.0.1")
    parser.add_argument("--manager-port", type=int, default=5021)
    parser.add_argument("--listen", default="0.0.0.0")
    parser.add_argument("--listen-port", type=int, default=5022)
    parser.add_argument("--accel-fallback", type=int, default=10)
    args = parser.parse_args()
    asyncio.run(RtdsEmulator(args).run())


if __name__ == "__main__":
    main()
