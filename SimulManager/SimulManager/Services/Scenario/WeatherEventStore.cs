// 기상 이벤트(폭염·폭설) 저장소 — 운영자 임의 입력 (docs/OPEN_ISSUES.md #6 확정)
// (backend/app/scenario/events.py 포팅).
//
// 실측 기상 데이터와 무관하게 운영자가 가상 기간을 지정해 주입하며,
// 배포 시 기상 상태코드(0정상/1폭염/2폭설)와 적설량 레지스터를 오버라이드한다.
// 양 Run(A/B)에 동일 적용된다 (공정 비교). data/events.json에 영속화.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using System.Web.Script.Serialization;
using SimulManager.Services.Protocol;

namespace SimulManager.Services.Scenario
{
    public sealed class WeatherEvent
    {
        public string Id;
        public string Type;        // heatwave | snow
        public DateTime Start;     // 가상시각 (포함)
        public DateTime End;       // 가상시각 (미포함)
        public double SnowCm;      // snow일 때 적설량 [cm]
    }

    public sealed class WeatherEventStore
    {
        public static readonly string[] EventTypes = { "heatwave", "snow" }; // 1폭염 / 2폭설
        public static readonly Dictionary<string, int> WeatherCode = new Dictionary<string, int>
        {
            { "heatwave", 1 }, { "snow", 2 },
        };
        // 적설량 상한 — 레지스터 인코딩 한계에서 유도 (③IR 300006 UINT16 ×100 → 655.35 cm)
        public static readonly double SnowCmMax = Codec.UInt16Max / Links.Link3Map["snow_depth"].Scale;

        private readonly string _path;
        private readonly object _lock = new object();
        private List<WeatherEvent> _events = new List<WeatherEvent>();

        public WeatherEventStore(string dataDir)
        {
            _path = Path.Combine(dataDir, "events.json");
            Load();
        }

        public List<WeatherEvent> List()
        {
            lock (_lock) return _events.OrderBy(e => e.Start).ToList();
        }

        public WeatherEvent Add(string type, DateTime start, DateTime end, double snowCm = 0.0)
        {
            if (!EventTypes.Contains(type))
                throw new ArgumentException("이벤트 유형은 heatwave/snow 중 하나여야 합니다: " + type);
            if (end <= start)
                throw new ArgumentException("종료 시각은 시작 시각 이후여야 합니다");
            if (type == "snow" && (snowCm < 0 || snowCm > SnowCmMax))
                throw new ArgumentException("적설량은 0~" + SnowCmMax + " cm 범위여야 합니다 (레지스터 한계)");
            var evt = new WeatherEvent
            {
                Id = Guid.NewGuid().ToString("N").Substring(0, 8),
                Type = type, Start = start, End = end,
                SnowCm = type == "snow" ? snowCm : 0.0,
            };
            lock (_lock)
            {
                _events.Add(evt);
                Save();
            }
            Trace.TraceInformation("기상 이벤트 추가: " + type + " " + start + " ~ " + end);
            return evt;
        }

        public bool Remove(string eventId)
        {
            lock (_lock)
            {
                int before = _events.Count;
                _events = _events.Where(e => e.Id != eventId).ToList();
                if (_events.Count != before) { Save(); return true; }
                return false;
            }
        }

        /// <summary>해당 가상시각에 활성인 이벤트 (겹치면 먼저 시작한 것 우선).</summary>
        public WeatherEvent ActiveAt(DateTime virtualTime)
        {
            lock (_lock)
            {
                return _events
                    .Where(e => e.Start <= virtualTime && virtualTime < e.End)
                    .OrderBy(e => e.Start)
                    .FirstOrDefault();
            }
        }

        private void Save()
        {
            var ser = new JavaScriptSerializer();
            var list = _events.Select(e => new Dictionary<string, object>
            {
                { "id", e.Id }, { "type", e.Type },
                { "start", e.Start.ToString("yyyy-MM-dd'T'HH:mm:ss") },
                { "end", e.End.ToString("yyyy-MM-dd'T'HH:mm:ss") },
                { "snow_cm", e.SnowCm },
            }).ToList();
            File.WriteAllText(_path, ser.Serialize(list), Encoding.UTF8);
        }

        private void Load()
        {
            if (!File.Exists(_path)) return;
            try
            {
                var raw = new JavaScriptSerializer()
                    .Deserialize<List<Dictionary<string, object>>>(File.ReadAllText(_path));
                if (raw == null) return;
                _events = raw.Select(d => new WeatherEvent
                {
                    Id = (string)d["id"],
                    Type = (string)d["type"],
                    Start = DateTime.Parse((string)d["start"], CultureInfo.InvariantCulture),
                    End = DateTime.Parse((string)d["end"], CultureInfo.InvariantCulture),
                    SnowCm = d.ContainsKey("snow_cm") ? Convert.ToDouble(d["snow_cm"], CultureInfo.InvariantCulture) : 0.0,
                }).ToList();
            }
            catch (Exception ex)
            {
                Trace.TraceError("이벤트 파일 복원 실패: " + _path + " — " + ex.Message);
            }
        }
    }
}
