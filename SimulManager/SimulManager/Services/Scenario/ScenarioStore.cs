// 시나리오 데이터 스토어 — 1개월 부하·기상 실측 데이터의 적재·조회
// (backend/app/scenario/store.py 포팅).
//
// 지원 포맷: CSV(utf-8/cp949 자동 판별)·Excel(xlsx — DevExpress Office File API 필요)
// - 부하: Timestamp, Load_kW (월 단위)
// - 기상: 일시, 기온, 풍속, 습도, 일조, 일사, 전운량, 중하층운량
// 업로드 파일은 data/uploads/ 에 보존되어 재기동 시 자동 복원된다.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using System.Web.Script.Serialization;

namespace SimulManager.Services.Scenario
{
    /// <summary>파싱된 시계열 프레임 — timestamp 오름차순 정렬 보장.</summary>
    public sealed class DataFrame
    {
        public List<DateTime> Timestamps = new List<DateTime>();
        public List<string> Columns = new List<string>();                // timestamp 제외
        public Dictionary<string, List<double?>> Values = new Dictionary<string, List<double?>>();
        public int RowCount => Timestamps.Count;
    }

    public sealed class KindMeta
    {
        public string Filename;
        public string Stored;
        public int Rows;
        public DateTime Start;
        public DateTime End;
        public double? ResolutionMin;
        public List<string> Columns = new List<string>();
        public string LoadedAt;
    }

    public sealed class ScenarioStore
    {
        public static readonly string[] Kinds = { "load", "weather" };

        // 원본 헤더 → 내부 컬럼 키 (부분 일치 허용: '기온(°C)' 등 단위 표기 대응)
        private static readonly KeyValuePair<string, string>[] LoadColumns =
        {
            new KeyValuePair<string, string>("timestamp", "timestamp"),
            new KeyValuePair<string, string>("load_kw", "load_kw"),
        };
        private static readonly KeyValuePair<string, string>[] WeatherColumns =
        {
            new KeyValuePair<string, string>("일시", "timestamp"),
            new KeyValuePair<string, string>("기온", "temp"),
            new KeyValuePair<string, string>("풍속", "wind_speed"),
            new KeyValuePair<string, string>("습도", "humidity"),
            new KeyValuePair<string, string>("일조", "sunshine"),
            new KeyValuePair<string, string>("일사", "irradiance"),
            new KeyValuePair<string, string>("전운량", "cloud_total"),
            new KeyValuePair<string, string>("중하층운량", "cloud_midlow"),
        };

        public static readonly Dictionary<string, string> ColumnLabels = new Dictionary<string, string>
        {
            { "load_kw", "부하 (kW)" },
            { "temp", "기온 (℃)" },
            { "wind_speed", "풍속 (m/s)" },
            { "humidity", "습도 (%)" },
            { "sunshine", "일조 (hr)" },
            { "irradiance", "일사 (MJ/m²)" },
            { "cloud_total", "전운량 (1/10)" },
            { "cloud_midlow", "중하층운량 (1/10)" },
        };

        private readonly string _uploadsDir;
        private readonly string _metaPath;
        private readonly object _lock = new object();
        private readonly Dictionary<string, DataFrame> _frames = new Dictionary<string, DataFrame>();
        private readonly Dictionary<string, KindMeta> _meta = new Dictionary<string, KindMeta>();

        public ScenarioStore(string dataDir)
        {
            _uploadsDir = Path.Combine(dataDir, "uploads");
            Directory.CreateDirectory(_uploadsDir);
            _metaPath = Path.Combine(_uploadsDir, "meta.json");
            Restore();
        }

        // ── 적재 ────────────────────────────────────────────────
        /// <summary>업로드 파일 파싱·적재·보존. 실패 시 ArgumentException.</summary>
        public KindMeta LoadUpload(string kind, string filename, byte[] content)
        {
            if (!Kinds.Contains(kind))
                throw new ArgumentException("지원하지 않는 데이터 종류: " + kind);
            var df = Parse(kind, filename, content);

            string suffix = (Path.GetExtension(filename) ?? "").ToLowerInvariant();
            if (string.IsNullOrEmpty(suffix)) suffix = ".csv";
            string stored = Path.Combine(_uploadsDir, kind + suffix);
            foreach (var old in Directory.GetFiles(_uploadsDir, kind + ".*"))
                try { File.Delete(old); } catch { }
            File.WriteAllBytes(stored, content);

            var meta = new KindMeta
            {
                Filename = filename,
                Stored = Path.GetFileName(stored),
                Rows = df.RowCount,
                Start = df.Timestamps[0],
                End = df.Timestamps[df.RowCount - 1],
                ResolutionMin = ResolutionMin(df),
                Columns = new List<string>(df.Columns),
                LoadedAt = DateTime.Now.ToString("yyyy-MM-dd'T'HH:mm:ss"),
            };
            lock (_lock)
            {
                _frames[kind] = df;
                _meta[kind] = meta;
                SaveMeta();
            }
            Trace.TraceInformation(kind + " 데이터 적재: " + filename + " (" + df.RowCount + "행)");
            return meta;
        }

        private DataFrame Parse(string kind, string filename, byte[] content)
        {
            string suffix = (Path.GetExtension(filename) ?? "").ToLowerInvariant();
            List<List<string>> raw = suffix == ".xlsx" || suffix == ".xls"
                ? XlsxReader.ReadFirstSheet(content)
                : ParseCsv(content);
            if (raw.Count < 2)
                throw new ArgumentException("유효한 데이터 행이 없습니다");

            var header = raw[0];
            var mapping = kind == "load" ? LoadColumns : WeatherColumns;
            // 헤더 매핑 — 부분 일치 (소문자·공백 제거)
            var colIndex = new List<Tuple<int, string>>(); // (원본 열 index, 내부 키)
            foreach (var pair in mapping)
            {
                int found = -1;
                for (int c = 0; c < header.Count; c++)
                {
                    string norm = (header[c] ?? "").Trim().ToLowerInvariant().Replace(" ", "");
                    if (norm.Contains(pair.Key.ToLowerInvariant())) { found = c; break; }
                }
                if (found < 0)
                    throw new ArgumentException("필수 컬럼을 찾을 수 없습니다: '" + pair.Key +
                                                "' (헤더: " + string.Join(", ", header) + ")");
                colIndex.Add(Tuple.Create(found, pair.Value));
            }

            int tsCol = colIndex.First(t => t.Item2 == "timestamp").Item1;
            var valueCols = colIndex.Where(t => t.Item2 != "timestamp").ToList();

            var rows = new List<Tuple<DateTime, double?[]>>();
            int badTs = 0;
            for (int r = 1; r < raw.Count; r++)
            {
                var line = raw[r];
                if (line.Count <= tsCol) continue;
                DateTime ts;
                if (!TryParseTimestamp(line[tsCol], out ts)) { badTs++; continue; }
                var vals = new double?[valueCols.Count];
                for (int i = 0; i < valueCols.Count; i++)
                {
                    int c = valueCols[i].Item1;
                    double v;
                    vals[i] = (c < line.Count &&
                               double.TryParse((line[c] ?? "").Trim(), NumberStyles.Any,
                                               CultureInfo.InvariantCulture, out v))
                        ? (double?)v : null;
                }
                rows.Add(Tuple.Create(ts, vals));
            }
            if (badTs > 0)
                Trace.TraceWarning(filename + ": 시각 파싱 실패 " + badTs + "행 제외");
            if (rows.Count == 0)
                throw new ArgumentException("유효한 데이터 행이 없습니다");
            rows.Sort((x, y) => x.Item1.CompareTo(y.Item1));

            var df = new DataFrame();
            foreach (var vc in valueCols)
            {
                df.Columns.Add(vc.Item2);
                df.Values[vc.Item2] = new List<double?>(rows.Count);
            }
            foreach (var row in rows)
            {
                df.Timestamps.Add(row.Item1);
                for (int i = 0; i < valueCols.Count; i++)
                    df.Values[valueCols[i].Item2].Add(row.Item2[i]);
            }
            return df;
        }

        private static bool TryParseTimestamp(string text, out DateTime ts)
        {
            text = (text ?? "").Trim();
            return DateTime.TryParse(text, CultureInfo.InvariantCulture, DateTimeStyles.None, out ts)
                || DateTime.TryParse(text, CultureInfo.GetCultureInfo("ko-KR"), DateTimeStyles.None, out ts);
        }

        /// <summary>CSV 파싱 — 한국 기상 데이터는 cp949가 흔함 (utf-8 → cp949 순 시도).</summary>
        private static List<List<string>> ParseCsv(byte[] content)
        {
            string text = null;
            try
            {
                text = new UTF8Encoding(false, true).GetString(
                    content.Length >= 3 && content[0] == 0xEF && content[1] == 0xBB && content[2] == 0xBF
                        ? content.Skip(3).ToArray() : content);
            }
            catch (DecoderFallbackException)
            {
                try { text = Encoding.GetEncoding(949).GetString(content); }
                catch { throw new ArgumentException("CSV 인코딩을 인식할 수 없습니다 (utf-8/cp949 지원)"); }
            }
            var rows = new List<List<string>>();
            foreach (var line in text.Split(new[] { "\r\n", "\n", "\r" }, StringSplitOptions.None))
            {
                if (line.Length == 0) continue;
                rows.Add(SplitCsvLine(line));
            }
            return rows;
        }

        private static List<string> SplitCsvLine(string line)
        {
            var fields = new List<string>();
            var sb = new StringBuilder();
            bool inQuotes = false;
            for (int i = 0; i < line.Length; i++)
            {
                char ch = line[i];
                if (inQuotes)
                {
                    if (ch == '"')
                    {
                        if (i + 1 < line.Length && line[i + 1] == '"') { sb.Append('"'); i++; }
                        else inQuotes = false;
                    }
                    else sb.Append(ch);
                }
                else if (ch == '"') inQuotes = true;
                else if (ch == ',') { fields.Add(sb.ToString()); sb.Clear(); }
                else sb.Append(ch);
            }
            fields.Add(sb.ToString());
            return fields;
        }

        private static double? ResolutionMin(DataFrame df)
        {
            if (df.RowCount < 2) return null;
            var diffs = new List<double>(df.RowCount - 1);
            for (int i = 1; i < df.RowCount; i++)
                diffs.Add((df.Timestamps[i] - df.Timestamps[i - 1]).TotalMinutes);
            diffs.Sort();
            return diffs[diffs.Count / 2]; // median
        }

        // ── 조회 ────────────────────────────────────────────────
        public Dictionary<string, KindMeta> Status()
        {
            lock (_lock)
            {
                var result = new Dictionary<string, KindMeta>();
                foreach (var kind in Kinds)
                {
                    KindMeta m;
                    result[kind] = _meta.TryGetValue(kind, out m) ? m : null;
                }
                return result;
            }
        }

        /// <summary>시뮬레이션 엔진용 원본 프레임 접근.</summary>
        public DataFrame GetFrame(string kind)
        {
            lock (_lock)
            {
                DataFrame df;
                if (!_frames.TryGetValue(kind, out df))
                    throw new KeyNotFoundException(kind + " 데이터가 로드되지 않았습니다");
                return df;
            }
        }

        public bool HasFrame(string kind)
        {
            lock (_lock) return _frames.ContainsKey(kind);
        }

        // ── 영속화 ──────────────────────────────────────────────
        private void SaveMeta()
        {
            var ser = new JavaScriptSerializer();
            var root = new Dictionary<string, object>();
            foreach (var kv in _meta)
            {
                root[kv.Key] = new Dictionary<string, object>
                {
                    { "filename", kv.Value.Filename },
                    { "stored", kv.Value.Stored },
                    { "rows", kv.Value.Rows },
                    { "start", kv.Value.Start.ToString("yyyy-MM-dd'T'HH:mm:ss") },
                    { "end", kv.Value.End.ToString("yyyy-MM-dd'T'HH:mm:ss") },
                    { "resolution_min", kv.Value.ResolutionMin },
                    { "columns", kv.Value.Columns },
                    { "loaded_at", kv.Value.LoadedAt },
                };
            }
            File.WriteAllText(_metaPath, ser.Serialize(root), Encoding.UTF8);
        }

        private void Restore()
        {
            if (!File.Exists(_metaPath)) return;
            Dictionary<string, object> meta;
            try
            {
                meta = new JavaScriptSerializer()
                    .Deserialize<Dictionary<string, object>>(File.ReadAllText(_metaPath));
            }
            catch { return; }
            if (meta == null) return;
            foreach (var kv in meta)
            {
                var entry = kv.Value as Dictionary<string, object>;
                if (entry == null || !Kinds.Contains(kv.Key)) continue;
                object storedName;
                if (!entry.TryGetValue("stored", out storedName)) continue;
                string storedPath = Path.Combine(_uploadsDir, (string)storedName);
                if (!File.Exists(storedPath)) continue;
                try
                {
                    object fname;
                    entry.TryGetValue("filename", out fname);
                    LoadUpload(kv.Key, (string)(fname ?? storedName), File.ReadAllBytes(storedPath));
                    Trace.TraceInformation(kv.Key + " 데이터 복원: " + fname);
                }
                catch (ArgumentException ex)
                {
                    Trace.TraceError(kv.Key + " 데이터 복원 실패: " + ex.Message);
                }
            }
        }
    }
}
