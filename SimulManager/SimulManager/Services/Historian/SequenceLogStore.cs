// 시퀀스 로그 저장소 — backend/app/historian/seq_log.py 포팅 (SQLite → JSONL).
//
// 전체 시퀀스를 무제한 적재하되, 조회는 페이지 단위(기본 50건)로만 반환하여
// 장시간 운전에도 응답 크기를 일정하게 유지한다 (UI 이전/다음 버튼 대응).
// refs 컬럼: 프로토콜 시트번호(①②③)와 참조주소 — 검증 단계용.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Web.Script.Serialization;

namespace SimulManager.Services.Historian
{
    public sealed class SeqEntry
    {
        public int Id;
        public string RealTime;
        public string VirtualTime;
        public string Phase;
        public string Source = "manager"; // manager | ems | rtds
        public string Message;
        public string Refs = "";
    }

    public sealed class SeqLogPage
    {
        public int Total;
        public int Offset;
        public int Limit;
        public List<SeqEntry> Entries = new List<SeqEntry>();
    }

    public sealed class SequenceLogStore
    {
        private readonly string _path;
        private readonly object _lock = new object();
        private readonly List<SeqEntry> _entries = new List<SeqEntry>(); // id 오름차순
        private readonly JavaScriptSerializer _ser = new JavaScriptSerializer { MaxJsonLength = int.MaxValue };

        public SequenceLogStore(string dataDir)
        {
            _path = Path.Combine(dataDir, "historian", "seqlog.jsonl");
            Directory.CreateDirectory(Path.GetDirectoryName(_path));
            Load();
        }

        public int Append(SeqEntry entry)
        {
            lock (_lock)
            {
                entry.Id = _entries.Count > 0 ? _entries[_entries.Count - 1].Id + 1 : 1;
                _entries.Add(entry);
                var line = _ser.Serialize(new Dictionary<string, object>
                {
                    { "id", entry.Id },
                    { "real_time", entry.RealTime },
                    { "virtual_time", entry.VirtualTime },
                    { "phase", entry.Phase },
                    { "source", entry.Source },
                    { "message", entry.Message },
                    { "refs", entry.Refs },
                });
                File.AppendAllText(_path, line + "\n", Encoding.UTF8);
                return entry.Id;
            }
        }

        /// <summary>최신순 페이지 조회 — offset 0 = 최신 페이지.</summary>
        public SeqLogPage Page(int offset = 0, int limit = 50)
        {
            lock (_lock)
            {
                var page = new SeqLogPage { Total = _entries.Count, Offset = offset, Limit = limit };
                page.Entries = _entries
                    .AsEnumerable()
                    .Reverse()
                    .Skip(offset)
                    .Take(limit)
                    .ToList();
                return page;
            }
        }

        private void Load()
        {
            if (!File.Exists(_path)) return;
            try
            {
                foreach (var line in File.ReadAllLines(_path, Encoding.UTF8))
                {
                    if (string.IsNullOrWhiteSpace(line)) continue;
                    Dictionary<string, object> d;
                    try { d = _ser.Deserialize<Dictionary<string, object>>(line); }
                    catch { continue; }
                    if (d == null) continue;
                    object v;
                    var entry = new SeqEntry
                    {
                        Id = d.TryGetValue("id", out v) && v != null ? Convert.ToInt32(v) : _entries.Count + 1,
                        RealTime = d.TryGetValue("real_time", out v) ? v as string : null,
                        VirtualTime = d.TryGetValue("virtual_time", out v) ? v as string : null,
                        Phase = d.TryGetValue("phase", out v) ? v as string : null,
                        Source = (d.TryGetValue("source", out v) ? v as string : null) ?? "manager",
                        Message = (d.TryGetValue("message", out v) ? v as string : null) ?? "",
                        Refs = (d.TryGetValue("refs", out v) ? v as string : null) ?? "",
                    };
                    _entries.Add(entry);
                }
            }
            catch { }
        }
    }
}
