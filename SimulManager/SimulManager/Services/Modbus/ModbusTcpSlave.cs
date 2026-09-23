// Modbus TCP 서버(슬레이브) — pymodbus ModbusTcpServer 대체 자체 구현.
//
// 지원 FC: 01/02 비트 읽기, 03/04 워드 읽기, 05/06 단일 쓰기, 15/16 다중 쓰기.
// 쓰기(FC05/06/15/16)는 RegisterBank.ClientWrite 경유 → 후킹·미러·이벤트 발행.
// MBAP: TID(2) PID(2) LEN(2) UID(1) + PDU. big-endian.
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
    public sealed class ModbusTcpSlave
    {
        private readonly RegisterBank _bank;
        private readonly string _bind;
        private readonly int _port;
        private TcpListener _listener;
        private volatile bool _running;
        private readonly List<TcpClient> _clients = new List<TcpClient>();

        public ModbusTcpSlave(RegisterBank bank, string bind, int port)
        {
            _bank = bank;
            _bind = bind;
            _port = port;
        }

        public void Start()
        {
            _listener = new TcpListener(IPAddress.Parse(_bind == "0.0.0.0" ? "0.0.0.0" : _bind), _port);
            _listener.Start();
            _running = true;
            var t = new Thread(AcceptLoop)
            {
                IsBackground = true,
                Name = "modbus-link" + (int)_bank.Link + "-accept",
            };
            t.Start();
            Trace.TraceInformation("링크" + (int)_bank.Link + " Modbus 서버 기동: " + _bind + ":" + _port);
        }

        public void Stop()
        {
            _running = false;
            try { _listener?.Stop(); } catch { }
            lock (_clients)
            {
                foreach (var c in _clients) { try { c.Close(); } catch { } }
                _clients.Clear();
            }
        }

        private void AcceptLoop()
        {
            while (_running)
            {
                TcpClient client;
                try { client = _listener.AcceptTcpClient(); }
                catch { break; }
                client.NoDelay = true;
                lock (_clients) _clients.Add(client);
                var t = new Thread(() => ClientLoop(client))
                {
                    IsBackground = true,
                    Name = "modbus-link" + (int)_bank.Link + "-client",
                };
                t.Start();
            }
        }

        private void ClientLoop(TcpClient client)
        {
            try
            {
                using (var stream = client.GetStream())
                {
                    var header = new byte[7];
                    while (_running)
                    {
                        if (!ReadExact(stream, header, 7)) break;
                        int tid = (header[0] << 8) | header[1];
                        int len = (header[4] << 8) | header[5];
                        byte uid = header[6];
                        if (len < 2 || len > 260) break; // 손상 프레임
                        var pdu = new byte[len - 1];
                        if (!ReadExact(stream, pdu, pdu.Length)) break;

                        byte[] response = ProcessPdu(pdu);
                        var frame = new byte[7 + response.Length];
                        frame[0] = (byte)(tid >> 8); frame[1] = (byte)tid;
                        frame[2] = 0; frame[3] = 0;
                        int rlen = response.Length + 1;
                        frame[4] = (byte)(rlen >> 8); frame[5] = (byte)rlen;
                        frame[6] = uid;
                        Buffer.BlockCopy(response, 0, frame, 7, response.Length);
                        stream.Write(frame, 0, frame.Length);
                    }
                }
            }
            catch (IOException) { }
            catch (ObjectDisposedException) { }
            catch (SocketException) { }
            finally
            {
                lock (_clients) _clients.Remove(client);
                try { client.Close(); } catch { }
            }
        }

        private static bool ReadExact(Stream stream, byte[] buf, int count)
        {
            int off = 0;
            while (off < count)
            {
                int n;
                try { n = stream.Read(buf, off, count - off); }
                catch { return false; }
                if (n <= 0) return false;
                off += n;
            }
            return true;
        }

        private byte[] ProcessPdu(byte[] pdu)
        {
            byte fc = pdu[0];
            try
            {
                switch (fc)
                {
                    case 1: return ReadBits(pdu, ObjType.Coil);
                    case 2: return ReadBits(pdu, ObjType.Di);
                    case 3: return ReadWords(pdu, ObjType.Hr);
                    case 4: return ReadWords(pdu, ObjType.Ir);
                    case 5: return WriteSingleCoil(pdu);
                    case 6: return WriteSingleRegister(pdu);
                    case 15: return WriteMultipleCoils(pdu);
                    case 16: return WriteMultipleRegisters(pdu);
                    default: return ExceptionPdu(fc, 1); // ILLEGAL FUNCTION
                }
            }
            catch (IndexOutOfRangeException) { return ExceptionPdu(fc, 2); }
            catch (ArgumentOutOfRangeException) { return ExceptionPdu(fc, 2); }
            catch (Exception ex)
            {
                Trace.TraceError("Modbus PDU 처리 오류 (FC" + fc + "): " + ex.Message);
                return ExceptionPdu(fc, 4); // SLAVE DEVICE FAILURE
            }
        }

        private static int U16(byte[] b, int i) => (b[i] << 8) | b[i + 1];

        private void CheckRange(ObjType obj, int addr, int qty)
        {
            if (qty < 1 || addr < 0 || addr + qty > _bank.SizeOf(obj))
                throw new ArgumentOutOfRangeException();
        }

        private byte[] ReadBits(byte[] pdu, ObjType obj)
        {
            int addr = U16(pdu, 1), qty = U16(pdu, 3);
            if (qty > 2000) return ExceptionPdu(pdu[0], 3);
            CheckRange(obj, addr, qty);
            var raws = _bank.GetRaw(obj, addr, qty);
            int byteCount = (qty + 7) / 8;
            var resp = new byte[2 + byteCount];
            resp[0] = pdu[0];
            resp[1] = (byte)byteCount;
            for (int i = 0; i < qty; i++)
                if (raws[i] != 0) resp[2 + i / 8] |= (byte)(1 << (i % 8));
            return resp;
        }

        private byte[] ReadWords(byte[] pdu, ObjType obj)
        {
            int addr = U16(pdu, 1), qty = U16(pdu, 3);
            if (qty > 125) return ExceptionPdu(pdu[0], 3);
            CheckRange(obj, addr, qty);
            var raws = _bank.GetRaw(obj, addr, qty);
            var resp = new byte[2 + qty * 2];
            resp[0] = pdu[0];
            resp[1] = (byte)(qty * 2);
            for (int i = 0; i < qty; i++)
            {
                resp[2 + i * 2] = (byte)(raws[i] >> 8);
                resp[3 + i * 2] = (byte)raws[i];
            }
            return resp;
        }

        private byte[] WriteSingleCoil(byte[] pdu)
        {
            int addr = U16(pdu, 1), value = U16(pdu, 3);
            if (value != 0x0000 && value != 0xFF00) return ExceptionPdu(pdu[0], 3);
            CheckRange(ObjType.Coil, addr, 1);
            _bank.ClientWrite(ObjType.Coil, addr, new[] { value == 0xFF00 ? 1 : 0 });
            return (byte[])pdu.Clone(); // echo
        }

        private byte[] WriteSingleRegister(byte[] pdu)
        {
            int addr = U16(pdu, 1), value = U16(pdu, 3);
            CheckRange(ObjType.Hr, addr, 1);
            _bank.ClientWrite(ObjType.Hr, addr, new[] { value });
            return (byte[])pdu.Clone(); // echo
        }

        private byte[] WriteMultipleCoils(byte[] pdu)
        {
            int addr = U16(pdu, 1), qty = U16(pdu, 3);
            int byteCount = pdu[5];
            if (qty < 1 || qty > 1968 || byteCount != (qty + 7) / 8) return ExceptionPdu(pdu[0], 3);
            CheckRange(ObjType.Coil, addr, qty);
            var values = new int[qty];
            for (int i = 0; i < qty; i++)
                values[i] = (pdu[6 + i / 8] >> (i % 8)) & 1;
            _bank.ClientWrite(ObjType.Coil, addr, values);
            return new[] { pdu[0], pdu[1], pdu[2], pdu[3], pdu[4] };
        }

        private byte[] WriteMultipleRegisters(byte[] pdu)
        {
            int addr = U16(pdu, 1), qty = U16(pdu, 3);
            int byteCount = pdu[5];
            if (qty < 1 || qty > 123 || byteCount != qty * 2) return ExceptionPdu(pdu[0], 3);
            CheckRange(ObjType.Hr, addr, qty);
            var values = new int[qty];
            for (int i = 0; i < qty; i++)
                values[i] = U16(pdu, 6 + i * 2);
            _bank.ClientWrite(ObjType.Hr, addr, values);
            return new[] { pdu[0], pdu[1], pdu[2], pdu[3], pdu[4] };
        }

        private static byte[] ExceptionPdu(byte fc, byte code) =>
            new[] { (byte)(fc | 0x80), code };
    }
}
