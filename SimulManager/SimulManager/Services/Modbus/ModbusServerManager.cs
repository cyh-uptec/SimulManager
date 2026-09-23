// Modbus TCP 서버 계층 — 링크①(EMS용)·링크③(RTDS용) 2식 (backend/app/modbus/server.py 포팅).
//
// 포트 모드 (docs/OPEN_ISSUES.md #1):
// - per_link_port: 링크별 포트 분리 (기본 5020/5021) — 개발·에뮬레이터 환경
// - single_port : 단일 포트(502)에서 클라이언트 IP로 라우팅 — 운영 환경.
//   내부적으로 링크 서버는 127.0.0.1의 링크 포트에 뜨고, RoutingProxy가
//   502 접속을 peer IP 기준으로 해당 링크 포트로 중계한다.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Threading;
using SimulManager.Services.Protocol;

namespace SimulManager.Services.Modbus
{
    /// <summary>클라이언트 IP → 백엔드 포트 라우팅 TCP 프록시 (프로토콜 무관 바이트 중계).</summary>
    public sealed class RoutingProxy
    {
        private readonly string _bind;
        private readonly int _port;
        private readonly Dictionary<string, int> _routes; // {클라이언트 IP: 백엔드 포트}
        private TcpListener _listener;
        private volatile bool _running;

        public RoutingProxy(string bind, int port, Dictionary<string, int> routes)
        {
            _bind = bind; _port = port; _routes = routes;
        }

        public void Start()
        {
            _listener = new TcpListener(IPAddress.Parse(_bind == "0.0.0.0" ? "0.0.0.0" : _bind), _port);
            _listener.Start();
            _running = true;
            new Thread(AcceptLoop) { IsBackground = true, Name = "routing-proxy" }.Start();
            Trace.TraceInformation("라우팅 프록시 기동: " + _bind + ":" + _port);
        }

        public void Stop()
        {
            _running = false;
            try { _listener?.Stop(); } catch { }
        }

        private void AcceptLoop()
        {
            while (_running)
            {
                TcpClient client;
                try { client = _listener.AcceptTcpClient(); }
                catch { break; }
                new Thread(() => Handle(client)) { IsBackground = true }.Start();
            }
        }

        private void Handle(TcpClient client)
        {
            string peerIp = ((IPEndPoint)client.Client.RemoteEndPoint).Address.ToString();
            int backendPort;
            if (!_routes.TryGetValue(peerIp, out backendPort))
            {
                Trace.TraceWarning("미등록 클라이언트 IP 접속 거부: " + peerIp);
                try { client.Close(); } catch { }
                return;
            }
            TcpClient backend = null;
            try
            {
                backend = new TcpClient();
                backend.Connect("127.0.0.1", backendPort);
            }
            catch (SocketException)
            {
                Trace.TraceError("백엔드 연결 실패: 127.0.0.1:" + backendPort);
                try { client.Close(); } catch { }
                return;
            }
            var a = client.GetStream();
            var b = backend.GetStream();
            var t1 = new Thread(() => Pipe(a, b, client, backend)) { IsBackground = true };
            var t2 = new Thread(() => Pipe(b, a, backend, client)) { IsBackground = true };
            t1.Start(); t2.Start();
        }

        private static void Pipe(Stream src, Stream dst, TcpClient srcClient, TcpClient dstClient)
        {
            var buf = new byte[4096];
            try
            {
                int n;
                while ((n = src.Read(buf, 0, buf.Length)) > 0)
                {
                    dst.Write(buf, 0, n);
                    dst.Flush();
                }
            }
            catch { }
            finally
            {
                try { srcClient.Close(); } catch { }
                try { dstClient.Close(); } catch { }
            }
        }
    }

    /// <summary>링크①·③ Modbus 서버 수명 관리.</summary>
    public sealed class ModbusServerManager
    {
        private readonly NetworkConfig _net;
        private readonly Dictionary<Link, RegisterBank> _banks;
        private readonly List<ModbusTcpSlave> _servers = new List<ModbusTcpSlave>();
        private RoutingProxy _proxy;

        public ModbusServerManager(NetworkConfig net, Dictionary<Link, RegisterBank> banks)
        {
            _net = net; _banks = banks;
        }

        public Dictionary<Link, int> LinkPorts => new Dictionary<Link, int>
        {
            { Link.Link1, _net.Link1Port },
            { Link.Link3, _net.Link3Port },
        };

        public void Start()
        {
            // single_port 모드에서는 링크 서버를 로컬 전용으로 숨기고 프록시가 외부를 받는다
            string linkBind = _net.Mode == "single_port" ? "127.0.0.1" : _net.Bind;
            foreach (var kv in LinkPorts)
            {
                var server = new ModbusTcpSlave(_banks[kv.Key], linkBind, kv.Value);
                server.Start();
                _servers.Add(server);
            }
            if (_net.Mode == "single_port")
            {
                _proxy = new RoutingProxy(_net.Bind, _net.Port, new Dictionary<string, int>
                {
                    { _net.EmsIp, _net.Link1Port },
                    { _net.RtdsIp, _net.Link3Port },
                });
                _proxy.Start();
            }
        }

        public void Stop()
        {
            _proxy?.Stop();
            _proxy = null;
            foreach (var s in _servers) s.Stop();
            _servers.Clear();
        }
    }
}
