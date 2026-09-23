// 레지스터 뱅크 — 한 링크의 Modbus 데이터스토어 + 물리값 접근 + 클라이언트 쓰기 후킹
// (backend/app/modbus/bank.py 포팅).
//
// - 내부(매니저) 쓰기: Set(key, 물리값) — protocol SSOT의 스케일로 인코딩
// - 클라이언트 쓰기: Modbus 서버 경유 → 후킹 → 미러 변환 → 'modbus.write' 이벤트 발행
// - 링크① 미러: EMS의 FC16 HR 미러 기록을 DI/IR 뱅크로 내부 매핑 (Mirror 상수)
using System;
using System.Collections.Generic;
using System.Diagnostics;
using SimulManager.Services.Protocol;

namespace SimulManager.Services.Modbus
{
    /// <summary>modbus.write 이벤트 payload — 레지스터 물리값 포함 (SSOT 경유 디코드).</summary>
    public sealed class RegisterValue
    {
        public string Key;
        public string Ref;
        public string Name;
        public double Physical;
    }

    public sealed class ModbusWriteEvent
    {
        public Link Link;
        public ObjType Obj;
        public int Address;
        public int[] Values;          // raw
        public string Ref;            // 첫 레지스터 (정의된 경우)
        public string Key;
        public string Name;
        public double? Physical;
        public List<RegisterValue> Registers; // 다중 쓰기 시 정의된 레지스터 전개
    }

    public sealed class RegisterBank
    {
        public LinkMap Map { get; }
        public Link Link { get; }

        private readonly EventBus _bus;
        private readonly bool _mirror;   // 링크①만 DI/IR HR 미러 사용
        private readonly object _lock = new object();
        private readonly bool[] _coils;
        private readonly bool[] _di;
        private readonly ushort[] _ir;
        private readonly ushort[] _hr;

        public RegisterBank(LinkMap map, EventBus bus)
        {
            Map = map;
            Link = map.Link;
            _bus = bus;
            _mirror = map.Link == Protocol.Link.Link1;
            _coils = new bool[SizeFor(ObjType.Coil)];
            _di = new bool[SizeFor(ObjType.Di)];
            _ir = new ushort[SizeFor(ObjType.Ir)];
            _hr = new ushort[SizeFor(ObjType.Hr)];
        }

        private int SizeFor(ObjType obj)
        {
            var regs = Map.ByType(obj);
            int size = regs.Count > 0 ? regs[regs.Count - 1].Address + 1 : 1;
            if (_mirror && obj == ObjType.Hr)
                size = Mirror.HrBaseIr + Map.Count(ObjType.Ir); // 미러 영역까지 확보
            return size;
        }

        // ── 내부(매니저) 접근 — 물리값 ───────────────────────────
        public void Set(string key, double value)
        {
            var reg = Map[key];
            int raw = Codec.Encode(value, reg);
            lock (_lock) { StoreRaw(reg.Obj, reg.Address, new[] { raw }); }
        }

        public double Get(string key)
        {
            var reg = Map[key];
            int raw;
            lock (_lock) { raw = LoadRaw(reg.Obj, reg.Address); }
            return Codec.Decode(raw, reg);
        }

        public void SetRaw(ObjType obj, int address, int[] values)
        {
            lock (_lock) { StoreRaw(obj, address, values); }
        }

        public int[] GetRaw(ObjType obj, int address, int count)
        {
            var result = new int[count];
            lock (_lock)
            {
                for (int i = 0; i < count; i++) result[i] = LoadRaw(obj, address + i);
            }
            return result;
        }

        public int SizeOf(ObjType obj)
        {
            switch (obj)
            {
                case ObjType.Coil: return _coils.Length;
                case ObjType.Di: return _di.Length;
                case ObjType.Ir: return _ir.Length;
                default: return _hr.Length;
            }
        }

        /// <summary>전 레지스터 현재값 (모니터링용).</summary>
        public List<Dictionary<string, object>> Dump()
        {
            var result = new List<Dictionary<string, object>>();
            foreach (var reg in Map.Registers)
            {
                int raw = GetRaw(reg.Obj, reg.Address, 1)[0];
                result.Add(new Dictionary<string, object>
                {
                    { "link", (int)Link }, { "obj", reg.Obj.ToString().ToLowerInvariant() },
                    { "ref", reg.Ref }, { "key", reg.Key }, { "name", reg.Name },
                    { "unit", reg.Unit }, { "raw", raw }, { "value", Codec.Decode(raw, reg) },
                });
            }
            return result;
        }

        // ── 클라이언트(Modbus 요청) 쓰기 — 서버가 호출 ───────────
        /// <summary>클라이언트 쓰기 저장 + 미러 매핑 + modbus.write 발행.
        /// 저장·미러는 락 안에서 원자적으로, 이벤트는 엔진 스레드로 enqueue.</summary>
        public void ClientWrite(ObjType obj, int address, int[] raws)
        {
            ObjType effObj = obj;
            int effAddr = address;
            lock (_lock)
            {
                StoreRaw(obj, address, raws);
                if (_mirror && obj == ObjType.Hr)
                {
                    var mapped = MapMirror(address, raws);
                    if (mapped != null) { effObj = mapped.Item1; effAddr = mapped.Item2; }
                }
            }

            var evt = new ModbusWriteEvent
            {
                Link = Link, Obj = effObj, Address = effAddr, Values = (int[])raws.Clone(),
            };
            var reg = Map.AtOrNull(effObj, effAddr);
            if (reg != null)
            {
                evt.Ref = reg.Ref;
                evt.Key = reg.Key;
                evt.Name = reg.Name;
                evt.Physical = reg.DType == DType.Bit ? (raws[0] != 0 ? 1 : 0) : Codec.Decode(raws[0], reg);
            }
            if (raws.Length > 1)
            {
                evt.Registers = new List<RegisterValue>();
                for (int i = 0; i < raws.Length; i++)
                {
                    var r = Map.AtOrNull(effObj, effAddr + i);
                    if (r == null) continue;
                    evt.Registers.Add(new RegisterValue
                    {
                        Key = r.Key, Ref = r.Ref, Name = r.Name,
                        Physical = r.DType == DType.Bit ? (raws[i] != 0 ? 1 : 0) : Codec.Decode(raws[i], r),
                    });
                }
            }
            _bus.Publish("modbus.write", evt);
        }

        /// <summary>미러 HR 쓰기를 DI/IR로 매핑. 한 요청은 한 미러 영역 안에 완전히 포함되어야 한다.
        /// 범위 밖·영역 경계 걸침은 매핑하지 않고 경고만 남긴다 (뱅크 오염 방지).</summary>
        private Tuple<ObjType, int> MapMirror(int address, int[] raws)
        {
            int end = address + raws.Length;
            int irCount = Map.Count(ObjType.Ir);
            int diCount = Map.Count(ObjType.Di);

            if (address >= Mirror.HrBaseIr)
            {
                if (end <= Mirror.HrBaseIr + irCount)
                {
                    int target = address - Mirror.HrBaseIr;
                    for (int i = 0; i < raws.Length; i++) _ir[target + i] = (ushort)raws[i];
                    return Tuple.Create(ObjType.Ir, target);
                }
                Trace.TraceWarning("링크① IR 미러 범위 초과 쓰기 무시: HR " + address + "~" + (end - 1)
                                   + " (IR 정의 " + irCount + "개)");
                return null;
            }
            if (address >= Mirror.HrBaseDi)
            {
                if (end <= Mirror.HrBaseDi + diCount)
                {
                    int target = address - Mirror.HrBaseDi;
                    for (int i = 0; i < raws.Length; i++) _di[target + i] = raws[i] != 0;
                    return Tuple.Create(ObjType.Di, target);
                }
                Trace.TraceWarning("링크① DI 미러 범위 초과·영역 걸침 쓰기 무시: HR " + address + "~" + (end - 1)
                                   + " (DI 정의 " + diCount + "개)");
                return null;
            }
            if (end > Mirror.HrBaseDi)
                Trace.TraceWarning("링크① HR 쓰기가 미러 영역을 침범 (미매핑): HR " + address + "~" + (end - 1));
            return null;
        }

        // ── 저장소 원시 접근 (호출자가 락 보유) ───────────────────
        private void StoreRaw(ObjType obj, int address, int[] values)
        {
            for (int i = 0; i < values.Length; i++)
            {
                int a = address + i;
                switch (obj)
                {
                    case ObjType.Coil: _coils[a] = values[i] != 0; break;
                    case ObjType.Di: _di[a] = values[i] != 0; break;
                    case ObjType.Ir: _ir[a] = (ushort)values[i]; break;
                    default: _hr[a] = (ushort)values[i]; break;
                }
            }
        }

        private int LoadRaw(ObjType obj, int address)
        {
            switch (obj)
            {
                case ObjType.Coil: return _coils[address] ? 1 : 0;
                case ObjType.Di: return _di[address] ? 1 : 0;
                case ObjType.Ir: return _ir[address];
                default: return _hr[address];
            }
        }
    }
}
