// 모니터링 — frontend/src/MonitorPage.tsx 포팅.
// Historian 이력 + 실시간(이벤트 버스) 계측 차트. 차트는 SciChart 이용.
using System;
using System.Collections.ObjectModel;
using System.Globalization;
using System.Linq;
using DevExpress.Mvvm;
using SciChart.Charting.Model.DataSeries;
using SimulManager.Services;
using SimulManager.Services.Historian;

namespace SimulManager.ViewModels
{
    public sealed class RunListItem
    {
        public RunInfo Info { get; set; }
        public string Display { get; set; }
        public int RunId => Info.RunId;
    }

    public sealed class MonitorViewModel : ViewModelBase
    {
        private const int MaxLivePoints = 4000;
        private readonly SimulatorHost _host;
        private bool _loading;

        public MonitorViewModel(SimulatorHost host)
        {
            _host = host;
            _host.Bus.Subscribe("run.state", (t, p) => Ui.Run(RefreshRuns));
            _host.Bus.Subscribe("historian.measurement", OnMeasurement);
            RefreshRuns();
        }

        public ObservableCollection<RunListItem> Runs { get; } = new ObservableCollection<RunListItem>();

        public RunListItem SelectedRun
        {
            get => GetProperty(() => SelectedRun);
            set => SetProperty(() => SelectedRun, value, OnSelectedRunChanged);
        }

        public string SummaryText { get => GetProperty(() => SummaryText); private set => SetProperty(() => SummaryText, value); }
        public string SummaryBadge { get => GetProperty(() => SummaryBadge); private set => SetProperty(() => SummaryBadge, value); }
        public bool HasSummary { get => GetProperty(() => HasSummary); private set => SetProperty(() => HasSummary, value); }
        public bool ChartEmpty { get => GetProperty(() => ChartEmpty); private set => SetProperty(() => ChartEmpty, value); }

        // SciChart 데이터 시리즈 — ESS/PCC 전력 · SOC
        public XyDataSeries<DateTime, double> EssSeries { get; } =
            new XyDataSeries<DateTime, double> { SeriesName = "ESS 전력" };
        public XyDataSeries<DateTime, double> ImportSeries { get; } =
            new XyDataSeries<DateTime, double> { SeriesName = "PCC 수전" };
        public XyDataSeries<DateTime, double> ExportSeries { get; } =
            new XyDataSeries<DateTime, double> { SeriesName = "PCC 역송" };
        public XyDataSeries<DateTime, double> SocSeries { get; } =
            new XyDataSeries<DateTime, double> { SeriesName = "SOC" };

        private static readonly System.Collections.Generic.Dictionary<string, string> StatusLabel =
            new System.Collections.Generic.Dictionary<string, string>
            {
                { "running", "운전중" }, { "completed", "완료" }, { "stopped", "수동 정지" }, { "invalid", "무효" },
            };

        public void RefreshRuns()
        {
            int? keep = SelectedRun?.RunId;
            var list = _host.Historian.Runs();
            _loading = true;
            Runs.Clear();
            foreach (var r in list)
            {
                string status;
                if (!StatusLabel.TryGetValue(r.Status, out status)) status = r.Status;
                Runs.Add(new RunListItem
                {
                    Info = r,
                    Display = "#" + r.RunId + " Run " + (r.Label ?? r.ScenarioId.ToString()) + " · " + status
                        + (r.StartedVirtual != null ? " · " + Left(r.StartedVirtual, 10) : ""),
                });
            }
            _loading = false;
            var select = Runs.FirstOrDefault(r => r.RunId == keep) ?? Runs.FirstOrDefault();
            if (!Equals(SelectedRun, select)) SelectedRun = select;
            else OnSelectedRunChanged();
        }

        private static string Left(string s, int n) => s.Length <= n ? s : s.Substring(0, n);

        private void OnSelectedRunChanged()
        {
            if (_loading) return;
            EssSeries.Clear();
            ImportSeries.Clear();
            ExportSeries.Clear();
            SocSeries.Clear();
            var sel = SelectedRun;
            if (sel == null)
            {
                HasSummary = false;
                ChartEmpty = true;
                return;
            }
            var page = _host.Historian.Measurements(sel.RunId, maxPoints: 1500);
            foreach (var row in page.Rows) Append(row);
            ChartEmpty = EssSeries.Count == 0;

            var r = sel.Info;
            string status;
            if (!StatusLabel.TryGetValue(r.Status, out status)) status = r.Status;
            SummaryBadge = status;
            SummaryText = "가상 " + (r.StartedVirtual?.Replace("T", " ") ?? "—") + " ~ "
                + (r.EndedVirtual?.Replace("T", " ") ?? "진행 중")
                + " · 계측 " + r.Points.ToString("N0") + "점"
                + " · SOC " + (r.InitialSoc?.ToString("0.#") ?? "—") + "% → " + (r.FinalSoc?.ToString("0.#") ?? "—") + "%"
                + " · 단절 " + r.DisconnectIntervals + "구간"
                + (page.Stride > 1 ? " · 차트 " + page.Rows.Count.ToString("N0") + "점 표시(1/" + page.Stride + " 다운샘플)" : "");
            HasSummary = true;
        }

        private void OnMeasurement(string topic, object payload)
        {
            var evt = payload as MeasurementEvent;
            if (evt == null) return;
            Ui.Run(() =>
            {
                if (SelectedRun == null || SelectedRun.RunId != evt.RunId) return;
                Append(evt.Row);
                TrimLive();
                ChartEmpty = false;
            });
        }

        private void Append(MeasurementRow row)
        {
            DateTime ts;
            if (!DateTime.TryParse(row.VirtualTime, CultureInfo.InvariantCulture, DateTimeStyles.None, out ts))
                return;
            if (row.EssPowerKw.HasValue) EssSeries.Append(ts, row.EssPowerKw.Value);
            if (row.PccImportKw.HasValue) ImportSeries.Append(ts, row.PccImportKw.Value);
            if (row.PccExportKw.HasValue) ExportSeries.Append(ts, row.PccExportKw.Value);
            if (row.EssSoc.HasValue) SocSeries.Append(ts, row.EssSoc.Value);
        }

        private void TrimLive()
        {
            Trim(EssSeries); Trim(ImportSeries); Trim(ExportSeries); Trim(SocSeries);
        }

        private static void Trim(XyDataSeries<DateTime, double> series)
        {
            while (series.Count > MaxLivePoints) series.RemoveAt(0);
        }
    }
}
