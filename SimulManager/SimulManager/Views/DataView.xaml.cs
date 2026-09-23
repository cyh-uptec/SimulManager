using System;
using System.Collections.Specialized;
using System.Globalization;
using System.Windows.Controls;
using System.Windows.Data;
using System.Windows.Media;
using SciChart.Charting.Visuals.RenderableSeries;
using SimulManager.ViewModels;

namespace SimulManager.Views
{
    public partial class DataView : UserControl
    {
        private DataViewModel _vm;

        public DataView()
        {
            InitializeComponent();
            DataContextChanged += (s, e) =>
            {
                var oldVm = e.OldValue as DataViewModel;
                if (oldVm != null) oldVm.ChartSeries.CollectionChanged -= OnSeriesChanged;
                _vm = e.NewValue as DataViewModel;
                if (_vm != null)
                {
                    _vm.ChartSeries.CollectionChanged += OnSeriesChanged;
                    RebuildSeries();
                }
            };
        }

        /// <summary>동적 컬럼 선택 → SciChart 시리즈 재구성 (VM 컬렉션 → RenderableSeries).</summary>
        private void OnSeriesChanged(object sender, NotifyCollectionChangedEventArgs e) => RebuildSeries();

        private void RebuildSeries()
        {
            DataChart.RenderableSeries.Clear();
            if (_vm == null) return;
            foreach (var item in _vm.ChartSeries)
            {
                var solid = item.Stroke as SolidColorBrush;
                DataChart.RenderableSeries.Add(new FastLineRenderableSeries
                {
                    DataSeries = item.Series,
                    Stroke = solid != null ? solid.Color : Colors.SteelBlue,
                    StrokeThickness = 2,
                });
            }
        }

        /// <summary>표 헤더 클릭 정렬 — 전체 데이터 기준 정렬 후 페이지 재조회 (웹과 동일).</summary>
        private void RowsGrid_Sorting(object sender, DataGridSortingEventArgs e)
        {
            e.Handled = true;
            _vm?.SortByHeader(e.Column.Header?.ToString() ?? "");
        }
    }

    /// <summary>bool ↔ ComboBox SelectedIndex (false=0 폭염 / true=1 폭설).</summary>
    public sealed class BoolToIndexConverter : IValueConverter
    {
        public static readonly BoolToIndexConverter Instance = new BoolToIndexConverter();

        public object Convert(object value, Type targetType, object parameter, CultureInfo culture) =>
            value is bool b && b ? 1 : 0;

        public object ConvertBack(object value, Type targetType, object parameter, CultureInfo culture) =>
            value is int i && i == 1;
    }
}
