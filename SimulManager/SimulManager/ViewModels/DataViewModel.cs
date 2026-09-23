// 데이터 — frontend/src/DataPage.tsx 포팅.
// 부하·기상 업로드 카드, 부하/기상 전환, 컬럼 체크 → SciChart 다중 시리즈,
// 기상 이벤트(폭염·폭설) 편집, 정렬·페이지네이션 표.
using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Data;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Windows.Media;
using DevExpress.Mvvm;
using Microsoft.Win32;
using SciChart.Charting.Model.DataSeries;
using SimulManager.Services;
using SimulManager.Services.Scenario;

namespace SimulManager.ViewModels
{
    public sealed class ColumnCheck : BindableBase
    {
        private readonly Action _onChanged;
        public ColumnCheck(string key, string label, Action onChanged)
        {
            Key = key; Label = label; _onChanged = onChanged;
            SetProperty(() => IsChecked, true);
        }
        public string Key { get; }
        public string Label { get; }
        public bool IsChecked
        {
            get => GetProperty(() => IsChecked);
            set => SetProperty(() => IsChecked, value, _onChanged);
        }
    }

    public sealed class ChartSeriesItem
    {
        public XyDataSeries<DateTime, double> Series { get; set; }
        public Brush Stroke { get; set; }
    }

    public sealed class WeatherEventItem
    {
        public string Id { get; set; }
        public string TypeLabel { get; set; }
        public bool IsHeatwave { get; set; }
        public string RangeText { get; set; }
    }

    public sealed class DataViewModel : ViewModelBase
    {
        private const int PageSize = 50;
        public static readonly string[] SeriesColors =
            { "#6366F1", "#38BDF8", "#2EC5B6", "#FFB020", "#F26D6D", "#8B5CF6", "#22C55E", "#64748B" };

        private readonly SimulatorHost _host;
        private string _kind = "load";
        private int _offset;
        private string _sortCol;
        private bool _sortDesc;

        public DataViewModel(SimulatorHost host)
        {
            _host = host;
            UploadLoadCommand = new DelegateCommand(() => Upload("load"));
            UploadWeatherCommand = new DelegateCommand(() => Upload("weather"));
            SelectLoadCommand = new DelegateCommand(() => SwitchKind("load"));
            SelectWeatherCommand = new DelegateCommand(() => SwitchKind("weather"));
            PrevPageCommand = new DelegateCommand(() => { _offset = Math.Max(0, _offset - PageSize); RefreshRows(); },
                () => _offset > 0);
            NextPageCommand = new DelegateCommand(() => { _offset += PageSize; RefreshRows(); },
                () => CanNextPage);
            AddEventCommand = new DelegateCommand(AddEvent);
            RemoveEventCommand = new DelegateCommand<WeatherEventItem>(RemoveEvent);
            EventTypeIsSnow = false;
            SnowCm = 5;
            RefreshAll();
        }

        // ── 업로드 카드 ─────────────────────────────────────────
        public string LoadMetaText { get => GetProperty(() => LoadMetaText); private set => SetProperty(() => LoadMetaText, value); }
        public string WeatherMetaText { get => GetProperty(() => WeatherMetaText); private set => SetProperty(() => WeatherMetaText, value); }
        public string LoadButtonText { get => GetProperty(() => LoadButtonText); private set => SetProperty(() => LoadButtonText, value); }
        public string WeatherButtonText { get => GetProperty(() => WeatherButtonText); private set => SetProperty(() => WeatherButtonText, value); }
        public string ErrorText { get => GetProperty(() => ErrorText); private set => SetProperty(() => ErrorText, value); }
        public bool HasError => !string.IsNullOrEmpty(ErrorText);

        public DelegateCommand UploadLoadCommand { get; }
        public DelegateCommand UploadWeatherCommand { get; }
        public DelegateCommand SelectLoadCommand { get; }
        public DelegateCommand SelectWeatherCommand { get; }
        public DelegateCommand PrevPageCommand { get; }
        public DelegateCommand NextPageCommand { get; }
        public DelegateCommand AddEventCommand { get; }
        public DelegateCommand<WeatherEventItem> RemoveEventCommand { get; }

        // ── 세그먼트·컬럼 체크·차트 ──────────────────────────────
        public bool IsLoadKind { get => GetProperty(() => IsLoadKind); private set => SetProperty(() => IsLoadKind, value); }
        public ObservableCollection<ColumnCheck> ColumnChecks { get; } = new ObservableCollection<ColumnCheck>();
        public ObservableCollection<ChartSeriesItem> ChartSeries { get; } = new ObservableCollection<ChartSeriesItem>();
        public string ChartNote { get => GetProperty(() => ChartNote); private set => SetProperty(() => ChartNote, value); }
        public string ChartEmptyText { get => GetProperty(() => ChartEmptyText); private set => SetProperty(() => ChartEmptyText, value); }
        public bool ChartEmpty => !string.IsNullOrEmpty(ChartEmptyText);

        // ── 표 ──────────────────────────────────────────────────
        public DataView TableView { get => GetProperty(() => TableView); private set => SetProperty(() => TableView, value); }
        public string PagerText { get => GetProperty(() => PagerText); private set => SetProperty(() => PagerText, value); }
        public bool TableVisible { get => GetProperty(() => TableVisible); private set => SetProperty(() => TableVisible, value); }
        private bool CanNextPage
        {
            get
            {
                var df = Frame();
                return df != null && _offset + PageSize < df.RowCount;
            }
        }

        // ── 기상 이벤트 ─────────────────────────────────────────
        public ObservableCollection<WeatherEventItem> Events { get; } = new ObservableCollection<WeatherEventItem>();
        public bool EventsEmpty => Events.Count == 0;
        public bool EventTypeIsSnow { get => GetProperty(() => EventTypeIsSnow); set => SetProperty(() => EventTypeIsSnow, value); }
        public string EventStart { get => GetProperty(() => EventStart); set => SetProperty(() => EventStart, value); }
        public string EventEnd { get => GetProperty(() => EventEnd); set => SetProperty(() => EventEnd, value); }
        public double SnowCm { get => GetProperty(() => SnowCm); set => SetProperty(() => SnowCm, value); }
        public string EventError { get => GetProperty(() => EventError); private set => SetProperty(() => EventError, value); }
        public bool HasEventError => !string.IsNullOrEmpty(EventError);

        // ── 동작 ────────────────────────────────────────────────
        private void RefreshAll()
        {
            RefreshStatus();
            RebuildColumns();
            RefreshChart();
            RefreshRows();
            RefreshEvents();
        }

        private DataFrame Frame()
        {
            try { return _host.Scenario.GetFrame(_kind); }
            catch (KeyNotFoundException) { return null; }
        }

        private void RefreshStatus()
        {
            var status = _host.Scenario.Status();
            LoadMetaText = MetaText(status["load"], "Timestamp, Load_kW (월 단위)");
            WeatherMetaText = MetaText(status["weather"], "일시, 기온, 풍속, 습도, 일조, 일사, 전운량, 중하층운량");
            LoadButtonText = status["load"] != null ? "다시 로드" : "파일 로드";
            WeatherButtonText = status["weather"] != null ? "다시 로드" : "파일 로드";
        }

        private static string MetaText(KindMeta meta, string hint)
        {
            if (meta == null) return hint;
            return "파일  " + meta.Filename + "\n기간  "
                + meta.Start.ToString("yyyy-MM-dd HH:mm") + " ~ " + meta.End.ToString("yyyy-MM-dd HH:mm")
                + "\n행수  " + meta.Rows.ToString("N0") + "행"
                + (meta.ResolutionMin.HasValue ? " · " + meta.ResolutionMin.Value.ToString("0.#") + "분 해상도" : "");
        }

        private void Upload(string kind)
        {
            var dlg = new OpenFileDialog
            {
                Filter = "데이터 파일 (*.csv;*.xlsx;*.xls)|*.csv;*.xlsx;*.xls|모든 파일 (*.*)|*.*",
                Title = kind == "load" ? "부하 데이터 선택" : "기상 데이터 선택",
            };
            if (dlg.ShowDialog() != true) return;
            ErrorText = null;
            RaisePropertyChanged(nameof(HasError));
            try
            {
                var meta = _host.Scenario.LoadUpload(kind, Path.GetFileName(dlg.FileName),
                                                     File.ReadAllBytes(dlg.FileName));
                _host.Bus.Publish("data.loaded", meta);
                if (kind == _kind) { _offset = 0; _sortCol = null; }
                RefreshAll();
            }
            catch (ArgumentException ex) { ErrorText = ex.Message; }
            catch (Exception ex) { ErrorText = "업로드 실패: " + ex.Message; }
            RaisePropertyChanged(nameof(HasError));
        }

        private void SwitchKind(string kind)
        {
            if (_kind == kind) return;
            _kind = kind;
            _offset = 0;
            _sortCol = null;
            IsLoadKind = kind == "load";
            RebuildColumns();
            RefreshChart();
            RefreshRows();
        }

        private void RebuildColumns()
        {
            IsLoadKind = _kind == "load";
            ColumnChecks.Clear();
            var df = Frame();
            if (df == null) return;
            foreach (var col in df.Columns)
            {
                string label;
                if (!ScenarioStore.ColumnLabels.TryGetValue(col, out label)) label = col;
                ColumnChecks.Add(new ColumnCheck(col, label, () => { RefreshChart(); RefreshRows(); }));
            }
        }

        private List<string> SelectedColumns() =>
            ColumnChecks.Where(c => c.IsChecked).Select(c => c.Key).ToList();

        private void RefreshChart()
        {
            ChartSeries.Clear();
            var df = Frame();
            if (df == null)
            {
                ChartEmptyText = "데이터가 로드되지 않았습니다 — 위에서 파일을 업로드하세요.";
                RaisePropertyChanged(nameof(ChartEmpty));
                ChartNote = "";
                return;
            }
            var selected = SelectedColumns();
            if (selected.Count == 0)
            {
                ChartEmptyText = "표시할 항목을 체크하세요.";
                RaisePropertyChanged(nameof(ChartEmpty));
                ChartNote = "";
                return;
            }
            ChartEmptyText = "";
            RaisePropertyChanged(nameof(ChartEmpty));

            int stride = Math.Max(1, df.RowCount / 1200); // 균등 다운샘플
            int colorIdx = 0, points = 0;
            foreach (var col in selected)
            {
                string label;
                if (!ScenarioStore.ColumnLabels.TryGetValue(col, out label)) label = col;
                var series = new XyDataSeries<DateTime, double> { SeriesName = label, AcceptsUnsortedData = false };
                var values = df.Values[col];
                double last = double.NaN;
                for (int i = 0; i < df.RowCount; i += stride)
                {
                    var v = values[i];
                    if (v.HasValue) last = v.Value;
                    if (!double.IsNaN(last)) series.Append(df.Timestamps[i], v ?? last); // connectNulls
                }
                points = Math.Max(points, series.Count);
                var brush = (SolidColorBrush)new BrushConverter()
                    .ConvertFromString(SeriesColors[colorIdx++ % SeriesColors.Length]);
                brush.Freeze();
                ChartSeries.Add(new ChartSeriesItem { Series = series, Stroke = brush });
            }
            ChartNote = "전체 " + df.RowCount.ToString("N0") + "점 중 " + points.ToString("N0") + "점 표시 (다운샘플)";
        }

        // ── 표 (정렬·페이지네이션 — 웹의 서버측 정렬과 동일하게 전체 정렬 후 페이지) ──
        public void ToggleSort(string col)
        {
            _offset = 0;
            if (_sortCol == col)
            {
                if (!_sortDesc) _sortDesc = true;
                else { _sortCol = null; _sortDesc = false; }
            }
            else { _sortCol = col; _sortDesc = false; }
            RefreshRows();
        }

        public string SortMark(string col) =>
            _sortCol != col ? "" : _sortDesc ? " ▼" : " ▲";

        private void RefreshRows()
        {
            var df = Frame();
            var selected = SelectedColumns();
            if (df == null)
            {
                TableVisible = false;
                TableView = null;
                PrevPageCommand.RaiseCanExecuteChanged();
                NextPageCommand.RaiseCanExecuteChanged();
                return;
            }
            TableVisible = true;

            // 정렬 인덱스 산출
            var indices = Enumerable.Range(0, df.RowCount);
            if (_sortCol == "timestamp")
                indices = _sortDesc
                    ? indices.OrderByDescending(i => df.Timestamps[i])
                    : indices.OrderBy(i => df.Timestamps[i]);
            else if (_sortCol != null && df.Values.ContainsKey(_sortCol))
            {
                var vals = df.Values[_sortCol];
                indices = _sortDesc
                    ? indices.OrderByDescending(i => vals[i] ?? double.MinValue)
                    : indices.OrderBy(i => vals[i] ?? double.MaxValue); // NaN 뒤로
            }
            var page = indices.Skip(_offset).Take(PageSize).ToList();

            var table = new DataTable();
            table.Columns.Add("일시" + SortMark("timestamp"));
            foreach (var col in selected)
            {
                string label;
                if (!ScenarioStore.ColumnLabels.TryGetValue(col, out label)) label = col;
                table.Columns.Add(label + SortMark(col));
            }
            foreach (int i in page)
            {
                var row = table.NewRow();
                row[0] = df.Timestamps[i].ToString("MM-dd HH:mm");
                for (int c = 0; c < selected.Count; c++)
                {
                    var v = df.Values[selected[c]][i];
                    row[c + 1] = v.HasValue ? v.Value.ToString("#,0.##") : "—";
                }
                table.Rows.Add(row);
            }
            TableView = table.DefaultView;

            int total = df.RowCount;
            PagerText = (total == 0 ? 0 : _offset + 1) + "–" + Math.Min(_offset + PageSize, total)
                + " / " + total.ToString("N0") + "행";
            PrevPageCommand.RaiseCanExecuteChanged();
            NextPageCommand.RaiseCanExecuteChanged();
        }

        /// <summary>표 헤더 클릭 → 원본 컬럼 키 복원 후 정렬 토글.</summary>
        public void SortByHeader(string header)
        {
            string clean = header.Replace(" ▲", "").Replace(" ▼", "");
            if (clean == "일시") { ToggleSort("timestamp"); return; }
            foreach (var c in ColumnChecks)
                if (c.Label == clean) { ToggleSort(c.Key); return; }
        }

        // ── 기상 이벤트 ─────────────────────────────────────────
        private void RefreshEvents()
        {
            Events.Clear();
            foreach (var e in _host.WeatherEvents.List())
            {
                Events.Add(new WeatherEventItem
                {
                    Id = e.Id,
                    TypeLabel = e.Type == "heatwave" ? "폭염" : "폭설",
                    IsHeatwave = e.Type == "heatwave",
                    RangeText = e.Start.ToString("yyyy-MM-dd HH:mm") + " ~ " + e.End.ToString("yyyy-MM-dd HH:mm")
                        + (e.Type == "snow" ? " · 적설 " + e.SnowCm + "cm" : ""),
                });
            }
            RaisePropertyChanged(nameof(EventsEmpty));
        }

        private void AddEvent()
        {
            EventError = null;
            RaisePropertyChanged(nameof(HasEventError));
            DateTime start, end;
            if (!TryParseLocal(EventStart, out start) || !TryParseLocal(EventEnd, out end))
            {
                EventError = "시작·종료 가상시각을 입력하세요 (예: 2025-07-03 12:00)";
                RaisePropertyChanged(nameof(HasEventError));
                return;
            }
            try
            {
                _host.WeatherEvents.Add(EventTypeIsSnow ? "snow" : "heatwave", start, end,
                                        EventTypeIsSnow ? SnowCm : 0);
                EventStart = "";
                EventEnd = "";
                RefreshEvents();
            }
            catch (ArgumentException ex)
            {
                EventError = ex.Message;
                RaisePropertyChanged(nameof(HasEventError));
            }
        }

        private static bool TryParseLocal(string text, out DateTime dt)
        {
            return DateTime.TryParse((text ?? "").Trim(), CultureInfo.InvariantCulture,
                                     DateTimeStyles.None, out dt) && text?.Trim().Length >= 10;
        }

        private void RemoveEvent(WeatherEventItem item)
        {
            if (item == null) return;
            _host.WeatherEvents.Remove(item.Id);
            RefreshEvents();
        }
    }
}
