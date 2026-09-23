"""가상 노드 — 시험 모드에서 상대 노드(RTDS/EMS)의 프로토콜 행동을 매니저 내부에서 재현.

EMS 단독 시험 시 '가상 RTDS', RTDS 단독 시험 시 '가상 EMS'를 켜면
정상 노드가 하는 기록(Heartbeat·상태 비트·핸드셰이크 응답)을 자동 수행한다.

실운영 동일성: 기록은 서버 데이터스토어의 클라이언트 쓰기 후킹 경로
(HookedSlaveContext.setValues — 실 Modbus 요청과 같은 진입점)로 넣는다.
따라서 링크① HR 미러 매핑, modbus.write 이벤트, 생존감시, 시퀀스 엔진의
P1 연결중개·P2 핸드셰이크 반응이 전부 실제 클라이언트 접속과 같은 파이프라인을 탄다.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from ..events import EventBus
from ..modbus import RegisterBank
from ..protocol import LINK1, LINK3, encode
from ..protocol.mirror import MIRROR_HR_BASE_DI, MIRROR_HR_BASE_IR
from ..protocol.types import Link, ObjType

logger = logging.getLogger(__name__)

HEARTBEAT_PERIOD_S = 1.0  # 실 1 s 증가 (PROTOCOL_RULES §6)


class _VirtualNodeBase:
    """가상 노드 공통 — 활성/비활성 태스크 수명 관리."""

    name = ""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None

    @property
    def enabled(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.enabled:
            return
        self._task = asyncio.create_task(self._run(), name=f"virtual-{self.name}")
        logger.info("가상 %s 활성화", self.name.upper())

    async def stop(self) -> None:
        if self._task is None:
            return
        task, self._task = self._task, None
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        logger.info("가상 %s 비활성화", self.name.upper())

    async def _run(self) -> None:
        raise NotImplementedError


class VirtualRtds(_VirtualNodeBase):
    """정상 RTDS 재현 (EMS 단독 시험용) — 링크③ 클라이언트 몫.

    - 접속 시 ③Coil0 '운전중'=1 → 엔진 P1 연결중개가 ①Coil4 'RTDS 준비됨' 세트
    - ③HR 400000 Heartbeat 실 1 s 증가 → 생존감시 alive
    - P2 핸드셰이크: ③DI0 트리거 감지 → ③Coil1 ack 세트, 트리거 해제 시 ack 해제
    - 가상 1분마다 ③HR 400002~400005 간이 계측 기록(FC16 블록과 동일 형태)
      — ESS 0 kW·SOC=초기 SOC 유지·PCC 수전=부하 기준값·역송 0
    """

    name = "rtds"

    def __init__(self, bank3: RegisterBank, bus: EventBus) -> None:
        super().__init__()
        self._bank = bank3
        self._bus = bus

    async def _run(self) -> None:
        ctx = self._bank.ctx
        heartbeat = int(self._bank.get("rtds_heartbeat"))
        ctx.set_as_client(5, LINK3["rtds_running"].address, [1])
        ctx.set_as_client(6, LINK3["rtds_status_code"].address, [0])
        self._bus.subscribe("clock.minute", self._on_minute)
        try:
            while True:
                heartbeat = (heartbeat + 1) & 0xFFFF
                ctx.set_as_client(6, LINK3["rtds_heartbeat"].address, [heartbeat])
                # P2 레벨 핸드셰이크
                trigger = self._bank.get_raw(ObjType.DI, LINK3["initial_soc_trigger"].address)[0]
                ack = self._bank.get_raw(ObjType.COIL, LINK3["initial_soc_ack"].address)[0]
                if trigger and not ack:
                    ctx.set_as_client(5, LINK3["initial_soc_ack"].address, [1])
                elif not trigger and ack:
                    ctx.set_as_client(5, LINK3["initial_soc_ack"].address, [0])
                await asyncio.sleep(HEARTBEAT_PERIOD_S)
        finally:
            self._bus.unsubscribe("clock.minute", self._on_minute)
            # 정상 종료 통지 — 운전중 해제 (엔진이 ①'RTDS 준비됨' 해제)
            ctx.set_as_client(5, LINK3["rtds_running"].address, [0])

    async def _on_minute(self, _topic: str, _payload: dict[str, Any]) -> None:
        """가상 1분 계측 — 실 RTDS의 FC16 일괄 기록과 동일한 형태로 적재."""
        soc = float(self._bank.get("initial_soc"))
        load = float(self._bank.get("load_profile_ref"))
        values = [
            encode(0.0, LINK3["ess_power"]),
            encode(soc, LINK3["ess_soc"]),
            encode(load, LINK3["pcc_import"]),
            encode(0.0, LINK3["pcc_export"]),
        ]
        self._bank.ctx.set_as_client(16, LINK3["ess_power"].address, values)


class VirtualEms(_VirtualNodeBase):
    """정상 EMS 재현 (RTDS 단독 시험용) — 링크① 클라이언트 몫.

    - ①DI0 '준비완료'=1 (미러 HR 410000 경유 — 실 EMS와 동일 경로)
    - ①IR 300000 Heartbeat 실 1 s 증가 (미러 HR 430000 경유)
      → 생존감시 alive → 엔진이 ③IR 300003 'EMS 상태'=1 유지
    """

    name = "ems"

    def __init__(self, bank1: RegisterBank, bus: EventBus) -> None:
        super().__init__()
        self._bank = bank1
        self._bus = bus

    async def _run(self) -> None:
        ctx = self._bank.ctx
        heartbeat = int(self._bank.get("ems_heartbeat"))
        ctx.set_as_client(16, MIRROR_HR_BASE_DI + LINK1["ems_ready"].address, [1])
        ctx.set_as_client(16, MIRROR_HR_BASE_IR + LINK1["ems_status_code"].address, [0])
        try:
            while True:
                heartbeat = (heartbeat + 1) & 0xFFFF
                ctx.set_as_client(16, MIRROR_HR_BASE_IR + LINK1["ems_heartbeat"].address, [heartbeat])
                await asyncio.sleep(HEARTBEAT_PERIOD_S)
        finally:
            # 정상 종료 통지 — 준비완료 해제 (HB 정지는 생존감시가 단절로 판정)
            ctx.set_as_client(16, MIRROR_HR_BASE_DI + LINK1["ems_ready"].address, [0])


class VirtualNodeManager:
    """가상 노드 2종 수명 관리 — API에서 켜고 끈다."""

    NODES = ("rtds", "ems")

    def __init__(self, banks: dict[Link, RegisterBank], bus: EventBus) -> None:
        self.rtds = VirtualRtds(banks[Link.LINK3], bus)
        self.ems = VirtualEms(banks[Link.LINK1], bus)

    def status(self) -> dict[str, bool]:
        return {"rtds": self.rtds.enabled, "ems": self.ems.enabled}

    async def set(self, node: str, enabled: bool) -> dict[str, bool]:
        if node not in self.NODES:
            raise ValueError(f"가상 노드는 rtds/ems 중 하나여야 합니다: {node}")
        target = self.rtds if node == "rtds" else self.ems
        if enabled:
            await target.start()
        else:
            await target.stop()
        return self.status()

    async def shutdown(self) -> None:
        await self.rtds.stop()
        await self.ems.stop()
