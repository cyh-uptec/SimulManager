// Historian 저장소 — Run 태깅·1분 계측·15분 예측 블록 (설계서 §7.1 적재 스키마)
// (backend/app/historian/store.py 포팅 — SQLite → 파일 기반(JSON/JSONL) 리포지토리).
//
// | 저장 파일                  | 해상도      | 원천                                   |
// |----------------------------|-------------|----------------------------------------|
// | runs.json                  | Run 단위    | Run ID·시나리오·시작/종료·초기/최종 SOC |
// | measurements-{run}.jsonl   | 가상 1분    | ③HR 400002~400005 (RTDS 기록)          |
// | forecasts-{run}.jsonl      | 15분×h=1    | ①IR 300002~300006 (EMS 기록, v1.7)     |
//
// 조회는 다운샘플(stride)로 응답 크기를 제한한다 (1 Run 계측 43,200점 대응).
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using System.Web.Script.Serialization;

namespace SimulManager.Services.Historian
{
    public sealed class RunInfo
    {
        public int RunId;
        public string Label;
        public int ScenarioId;
        public string Status = "running";   // running/completed/stopped/invalid
        public string StartedReal;
        public string StartedVirtual;
        public string EndedReal;
        public string EndedVirtual;
        public double? InitialSoc;
        public double? FinalSoc;
        public int DisconnectIntervals;
        public int Points;                  // 계측 점수 (조회 시 채움)
    }

    public sealed class MeasurementRow
    {
        public string VirtualTime;
        public double? EssPowerKw;
        public double? EssSoc;
        public double? PccImportKw;
        public double? PccExportKw;
    }

    public sealed class ForecastRow
    {
        public string VirtualTime;
        public int BaseInterval;
        public double? PvKw;
        public double? LoadKw;
        public double? ScheduleKw;
        public double? TargetSoc;
    }

    public sealed class MeasurementsPage
    {
        public int RunId;
        public int Total;
        public int Stride;
        public List<MeasurementRow> Rows = new List<MeasurementRow>();
    }

    public sealed class HistorianStore
    {
        private readonly string _dir;
        private readonly string _runsPath;
        private readonly object _lock = new object();
        private readonly List<RunInfo> _runs = new List<RunInfo>();
        private readonly Dictionary<int, int> _pointCounts = new Dictionary<int, int>();
        private readonly JavaScriptSerializer _ser = new JavaScriptSerializer { MaxJsonLength = int.MaxValue };

        public HistorianStore(string dataDir)
        {
            _dir = Path.Combine(dataDir, "historian");
            Directory.CreateDirectory(_dir);
            _runsPath = Path.Combine(_dir, "runs.json");
            LoadRuns();
        }

        private string MeasPath(int runId) => Path.Combine(_dir, "measurements-" + runId + ".jsonl");
        private string ForecastPath(int runId) => Path.Combine(_dir, "forecasts-" + runId + ".jsonl");

        // ── Run 태깅 ────────────────────────────────────────────
        public int StartRun(string label, int scenarioId, string startedReal,
                            string startedVirtual, double initialSoc)
        {
            lock (_lock)
            {
                int id = _runs.Count > 0 ? _runs.Max(r => r.RunId) + 1 : 1;
                _runs.Add(new RunInfo
                {
                    RunId = id, Label = label, ScenarioId = scenarioId, Status = "running",
                    StartedReal = startedReal, StartedVirtual = startedVirtual, InitialSoc = initialSoc,
                });
                _pointCounts[id] = 0;
                SaveRuns();
                return id;
            }
        }

        public void EndRun(int runId, string status, string endedReal, string endedVirtual,
                           double finalSoc, int disconnectIntervals)
        {
            lock (_lock)
            {
                var run = _runs.FirstOrDefault(r => r.RunId == runId);
                if (run == null) return;
                run.Status = status;
                run.EndedReal = endedReal;
                run.EndedVirtual = endedVirtual;
                run.FinalSoc = finalSoc;
                run.DisconnectIntervals = disconnectIntervals;
                SaveRuns();
            }
        }

        public List<RunInfo> Runs()
        {
            lock (_lock)
            {
                return _runs
                    .OrderByDescending(r => r.RunId)
                    .Select(r => new RunInfo
                    {
                        RunId = r.RunId, Label = r.Label, ScenarioId = r.ScenarioId, Status = r.Status,
                        StartedReal = r.StartedReal, StartedVirtual = r.StartedVirtual,
                        EndedReal = r.EndedReal, EndedVirtual = r.EndedVirtual,
                        InitialSoc = r.InitialSoc, FinalSoc = r.FinalSoc,
                        DisconnectIntervals = r.DisconnectIntervals,
                        Points = CountPoints(r.RunId),
                    })
                    .ToList();
            }
        }

        // ── 적재 ────────────────────────────────────────────────
        public void AddMeasurement(int runId, string virtualTime, MeasurementRow row)
        {
            row.VirtualTime = virtualTime;
            var line = _ser.Serialize(new Dictionary<string, object>
            {
                { "virtual_time", row.VirtualTime },
                { "ess_power_kw", row.EssPowerKw },
                { "ess_soc", row.EssSoc },
                { "pcc_import_kw", row.PccImportKw },
                { "pcc_export_kw", row.PccExportKw },
            });
            lock (_lock)
            {
                File.AppendAllText(MeasPath(runId), line + "\n", Encoding.UTF8);
                int count;
                _pointCounts[runId] = (_pointCounts.TryGetValue(runId, out count) ? count : 0) + 1;
            }
        }

        public void AddForecastBlock(int runId, string virtualTime, int baseInterval, ForecastRow row)
        {
            var line = _ser.Serialize(new Dictionary<string, object>
            {
                { "virtual_time", virtualTime },
                { "base_interval", baseInterval },
                { "horizon", 1 }, // v1.7: 예측 블록은 [1구간]만 전달
                { "pv_kw", row.PvKw },
                { "load_kw", row.LoadKw },
                { "schedule_kw", row.ScheduleKw },
                { "target_soc", row.TargetSoc },
            });
            lock (_lock)
            {
                File.AppendAllText(ForecastPath(runId), line + "\n", Encoding.UTF8);
            }
        }

        // ── 조회 (다운샘플) ─────────────────────────────────────
        public MeasurementsPage Measurements(int runId, int maxPoints = 2000)
        {
            lock (_lock)
            {
                var page = new MeasurementsPage { RunId = runId };
                string path = MeasPath(runId);
                if (!File.Exists(path)) { page.Stride = 1; return page; }
                var lines = File.ReadAllLines(path, Encoding.UTF8);
                page.Total = lines.Length;
                page.Stride = Math.Max(1, lines.Length / maxPoints);
                for (int i = 0; i < lines.Length; i++)
                {
                    if (page.Stride > 1 && (i + 1) % page.Stride != 0) continue;
                    var d = TryParse(lines[i]);
                    if (d == null) continue;
                    page.Rows.Add(new MeasurementRow
                    {
                        VirtualTime = AsString(d, "virtual_time"),
                        EssPowerKw = AsDouble(d, "ess_power_kw"),
                        EssSoc = AsDouble(d, "ess_soc"),
                        PccImportKw = AsDouble(d, "pcc_import_kw"),
                        PccExportKw = AsDouble(d, "pcc_export_kw"),
                    });
                }
                return page;
            }
        }

        public List<ForecastRow> Forecasts(int runId, int maxPoints = 2000)
        {
            lock (_lock)
            {
                var result = new List<ForecastRow>();
                string path = ForecastPath(runId);
                if (!File.Exists(path)) return result;
                foreach (var line in File.ReadAllLines(path, Encoding.UTF8))
                {
                    if (result.Count >= maxPoints) break;
                    var d = TryParse(line);
                    if (d == null) continue;
                    result.Add(new ForecastRow
                    {
                        VirtualTime = AsString(d, "virtual_time"),
                        BaseInterval = (int)(AsDouble(d, "base_interval") ?? 0),
                        PvKw = AsDouble(d, "pv_kw"),
                        LoadKw = AsDouble(d, "load_kw"),
                        ScheduleKw = AsDouble(d, "schedule_kw"),
                        TargetSoc = AsDouble(d, "target_soc"),
                    });
                }
                return result;
            }
        }

        // ── 내부 ────────────────────────────────────────────────
        private int CountPoints(int runId)
        {
            int cached;
            if (_pointCounts.TryGetValue(runId, out cached)) return cached;
            string path = MeasPath(runId);
            int count = File.Exists(path) ? File.ReadLines(path).Count() : 0;
            _pointCounts[runId] = count;
            return count;
        }

        private Dictionary<string, object> TryParse(string line)
        {
            try { return _ser.Deserialize<Dictionary<string, object>>(line); }
            catch { return null; }
        }

        private static string AsString(Dictionary<string, object> d, string key)
        {
            object v;
            return d.TryGetValue(key, out v) ? v as string : null;
        }

        private static double? AsDouble(Dictionary<string, object> d, string key)
        {
            object v;
            if (!d.TryGetValue(key, out v) || v == null) return null;
            try { return Convert.ToDouble(v, CultureInfo.InvariantCulture); }
            catch { return null; }
        }

        private void SaveRuns()
        {
            var list = _runs.Select(r => new Dictionary<string, object>
            {
                { "run_id", r.RunId }, { "label", r.Label }, { "scenario_id", r.ScenarioId },
                { "status", r.Status }, { "started_real", r.StartedReal },
                { "started_virtual", r.StartedVirtual }, { "ended_real", r.EndedReal },
                { "ended_virtual", r.EndedVirtual }, { "initial_soc", r.InitialSoc },
                { "final_soc", r.FinalSoc }, { "disconnect_intervals", r.DisconnectIntervals },
            }).ToList();
            File.WriteAllText(_runsPath, _ser.Serialize(list), Encoding.UTF8);
        }

        private void LoadRuns()
        {
            if (!File.Exists(_runsPath)) return;
            try
            {
                var raw = _ser.Deserialize<List<Dictionary<string, object>>>(File.ReadAllText(_runsPath));
                if (raw == null) return;
                foreach (var d in raw)
                {
                    _runs.Add(new RunInfo
                    {
                        RunId = (int)(AsDouble(d, "run_id") ?? 0),
                        Label = AsString(d, "label"),
                        ScenarioId = (int)(AsDouble(d, "scenario_id") ?? 0),
                        Status = AsString(d, "status") ?? "stopped",
                        StartedReal = AsString(d, "started_real"),
                        StartedVirtual = AsString(d, "started_virtual"),
                        EndedReal = AsString(d, "ended_real"),
                        EndedVirtual = AsString(d, "ended_virtual"),
                        InitialSoc = AsDouble(d, "initial_soc"),
                        FinalSoc = AsDouble(d, "final_soc"),
                        DisconnectIntervals = (int)(AsDouble(d, "disconnect_intervals") ?? 0),
                    });
                }
                // 재기동 시 미종료(running) Run은 stopped로 마감 처리
                foreach (var r in _runs.Where(r => r.Status == "running"))
                    r.Status = "stopped";
            }
            catch { }
        }
    }
}
