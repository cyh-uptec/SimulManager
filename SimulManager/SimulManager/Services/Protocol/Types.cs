// 레지스터 정의 타입 — 프로토콜맵 v1.0 §4 (객체 유형·참조주소 규칙).
// backend/app/protocol/types.py 포팅 — 주소·타입·스케일은 이 SSOT 경유로만 접근한다.
using System;
using System.Collections.Generic;
using System.Linq;

namespace SimulManager.Services.Protocol
{
    /// <summary>프로토콜맵 시트 번호 = 문서의 ①②③ 접두.</summary>
    public enum Link
    {
        Link1 = 1, // ① EMS–매니저 (매니저=Server, EMS=Client)
        Link2 = 2, // ② RTDS–EMS  (RTDS=Server, EMS=Client)
        Link3 = 3, // ③ 매니저–RTDS (매니저=Server, RTDS=Client)
    }

    /// <summary>Modbus 객체 유형. 참조주소 접두: Coil 0 / DI 1 / IR 3 / HR 4.</summary>
    public enum ObjType
    {
        Coil, // 1-bit R/W  — FC01(R)/FC05·15(W)
        Di,   // 1-bit RO   — FC02(R), 서버가 채움
        Ir,   // 16-bit RO  — FC04(R), 서버가 채움
        Hr,   // 16-bit R/W — FC03(R)/FC06·16(W)
    }

    public enum DType { Bit, UInt16, Int16 }

    /// <summary>레지스터 1개 정의. scale: raw = round(물리값 × scale).</summary>
    public sealed class RegisterDef
    {
        public Link Link { get; }
        public ObjType Obj { get; }
        public int Address { get; }      // 0-base 주소 (객체 유형 내 오프셋)
        public string Key { get; }       // 코드용 영문 식별자 (snake_case)
        public string Name { get; }      // 설계 문서의 한글 명칭
        public DType DType { get; }
        public double Scale { get; }
        public string Unit { get; }
        public string Note { get; }

        public RegisterDef(Link link, ObjType obj, int address, string key, string name,
                           DType dtype = DType.UInt16, double scale = 1.0,
                           string unit = "", string note = "")
        {
            Link = link; Obj = obj; Address = address; Key = key; Name = name;
            DType = dtype; Scale = scale; Unit = unit; Note = note;
        }

        public static int RefBase(ObjType obj)
        {
            switch (obj)
            {
                case ObjType.Coil: return 0;
                case ObjType.Di: return 100000;
                case ObjType.Ir: return 300000;
                default: return 400000;
            }
        }

        /// <summary>0-base 6자리 참조주소 (예: HR 7 → '400007').</summary>
        public string Ref => (RefBase(Obj) + Address).ToString("D6");

        public override string ToString()
        {
            string prefix = Link == Link.Link1 ? "①" : Link == Link.Link2 ? "②" : "③";
            return prefix + Obj.ToString().ToUpperInvariant() + " " + Ref + " " + Name;
        }
    }

    /// <summary>한 링크(시트)의 레지스터 맵. (obj, address)와 key 양방향 조회.</summary>
    public sealed class LinkMap
    {
        public Link Link { get; }
        public string Server { get; }   // 객체 보유 노드
        public string Client { get; }   // 개시자 노드
        public IReadOnlyList<RegisterDef> Registers { get; }

        private readonly Dictionary<Tuple<ObjType, int>, RegisterDef> _byAddr =
            new Dictionary<Tuple<ObjType, int>, RegisterDef>();
        private readonly Dictionary<string, RegisterDef> _byKey =
            new Dictionary<string, RegisterDef>();

        public LinkMap(Link link, string server, string client, IEnumerable<RegisterDef> registers)
        {
            Link = link; Server = server; Client = client;
            Registers = registers.ToList();
            foreach (var reg in Registers)
            {
                var addrKey = Tuple.Create(reg.Obj, reg.Address);
                if (_byAddr.ContainsKey(addrKey)) throw new ArgumentException("주소 중복: " + reg);
                if (_byKey.ContainsKey(reg.Key)) throw new ArgumentException("key 중복: " + reg.Key);
                if (reg.Link != link) throw new ArgumentException("링크 불일치: " + reg);
                _byAddr[addrKey] = reg;
                _byKey[reg.Key] = reg;
            }
        }

        public RegisterDef At(ObjType obj, int address) => _byAddr[Tuple.Create(obj, address)];

        public RegisterDef AtOrNull(ObjType obj, int address)
        {
            RegisterDef reg;
            return _byAddr.TryGetValue(Tuple.Create(obj, address), out reg) ? reg : null;
        }

        public RegisterDef this[string key] => _byKey[key];

        public bool Contains(string key) => _byKey.ContainsKey(key);

        public List<RegisterDef> ByType(ObjType obj) =>
            Registers.Where(r => r.Obj == obj).OrderBy(r => r.Address).ToList();

        public int Count(ObjType obj) => Registers.Count(r => r.Obj == obj);
    }
}
