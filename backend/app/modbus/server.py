"""Modbus TCP 서버 계층 — 링크①(EMS용)·링크③(RTDS용) 2식.

포트 모드 (docs/OPEN_ISSUES.md #1):
- per_link_port: 링크별 포트 분리 (기본 ①7000/③5021) — 개발·에뮬레이터·시험 환경
- single_port : 단일 포트(502)에서 클라이언트 IP로 라우팅 — 운영 환경.
  내부적으로 링크 서버는 127.0.0.1의 링크 포트에 뜨고, RoutingProxy가
  502 접속을 peer IP 기준으로 해당 링크 포트로 중계한다.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from pymodbus.datastore import ModbusServerContext
from pymodbus.server import ModbusTcpServer

from ..config import NetworkConfig
from ..protocol.types import Link
from .bank import RegisterBank

logger = logging.getLogger(__name__)


class RoutingProxy:
    """클라이언트 IP → 백엔드 포트 라우팅 TCP 프록시 (프로토콜 무관 바이트 중계)."""

    def __init__(self, bind: str, port: int, routes: dict[str, int]) -> None:
        self._bind = bind
        self._port = port
        self._routes = routes  # {클라이언트 IP: 백엔드 포트}
        self._server: asyncio.Server | None = None
        self._active: dict[int, list[str]] = {}  # {백엔드 포트: ["ip:port", ...]} — 중계 중 접속

    def active(self) -> dict[int, list[str]]:
        """백엔드 포트별 현재 중계 중인 클라이언트 목록."""
        return {port: list(peers) for port, peers in self._active.items()}

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, self._bind, self._port)
        logger.info("라우팅 프록시 기동: %s:%d → %s", self._bind, self._port, self._routes)

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peername = writer.get_extra_info("peername")
        peer_ip = peername[0]
        backend_port = self._routes.get(peer_ip)
        if backend_port is None:
            logger.warning("미등록 클라이언트 IP 접속 거부: %s (등록: %s)", peer_ip, list(self._routes))
            writer.close()
            return
        try:
            b_reader, b_writer = await asyncio.open_connection("127.0.0.1", backend_port)
        except OSError:
            logger.exception("백엔드 연결 실패: 127.0.0.1:%d", backend_port)
            writer.close()
            return

        async def pipe(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
            try:
                while data := await src.read(4096):
                    dst.write(data)
                    await dst.drain()
            except (ConnectionError, asyncio.IncompleteReadError):
                pass
            finally:
                dst.close()

        peer = f"{peer_ip}:{peername[1]}"
        self._active.setdefault(backend_port, []).append(peer)
        try:
            await asyncio.gather(pipe(reader, b_writer), pipe(b_reader, writer))
        finally:
            self._active[backend_port].remove(peer)


class ModbusServerManager:
    """링크①·③ Modbus 서버 수명 관리."""

    def __init__(self, net: NetworkConfig, banks: dict[Link, RegisterBank]) -> None:
        self._net = net
        self._banks = banks
        self._servers: dict[Link, ModbusTcpServer] = {}
        self._tasks: list[asyncio.Task] = []
        self._proxy: RoutingProxy | None = None

    @property
    def link_ports(self) -> dict[Link, int]:
        return {Link.LINK1: self._net.link1_port, Link.LINK3: self._net.link3_port}

    def link_connections(self) -> dict[Link, dict[str, Any]]:
        """링크별 실 TCP 접속 현황 — {link: {"count": n, "peers": ["ip:port", ...]}}.

        폴링(요청 수신)과 별개로 'TCP 세션이 열려 있는지'를 보여준다.
        - per_link_port: pymodbus 서버의 활성 연결에서 직접 수집
        - single_port  : 링크 서버에는 프록시(127.0.0.1)만 보이므로 프록시 라우팅 기록 사용
        """
        if self._net.mode == "single_port" and self._proxy:
            active = self._proxy.active()
            return {
                link: {"count": len(active.get(port, [])), "peers": active.get(port, [])}
                for link, port in self.link_ports.items()
            }
        result: dict[Link, dict[str, Any]] = {}
        for link in self.link_ports:
            peers: list[str] = []
            server = self._servers.get(link)
            for conn in list(server.active_connections.values()) if server else []:
                peername = getattr(conn, "transport", None) and conn.transport.get_extra_info("peername")
                if peername:
                    peers.append(f"{peername[0]}:{peername[1]}")
            result[link] = {"count": len(peers), "peers": peers}
        return result

    async def start(self) -> None:
        # single_port 모드에서는 링크 서버를 로컬 전용으로 숨기고 프록시가 외부를 받는다
        link_bind = "127.0.0.1" if self._net.mode == "single_port" else self._net.bind
        for link, port in self.link_ports.items():
            bank = self._banks[link]
            server = ModbusTcpServer(
                context=ModbusServerContext(slaves=bank.ctx, single=True),
                address=(link_bind, port),
            )
            self._servers[link] = server
            self._tasks.append(
                asyncio.create_task(server.serve_forever(), name=f"modbus-link{int(link)}")
            )
            logger.info("링크%d Modbus 서버 기동: %s:%d", int(link), link_bind, port)

        if self._net.mode == "single_port":
            self._proxy = RoutingProxy(
                self._net.bind, self._net.port,
                routes={
                    self._net.ems_ip: self._net.link1_port,
                    self._net.rtds_ip: self._net.link3_port,
                },
            )
            await self._proxy.start()
        await asyncio.sleep(0.05)  # 리스너 기동 대기

    async def stop(self) -> None:
        if self._proxy:
            await self._proxy.stop()
            self._proxy = None
        for server in self._servers.values():
            await server.shutdown()
        for task in self._tasks:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._servers.clear()
        self._tasks.clear()
