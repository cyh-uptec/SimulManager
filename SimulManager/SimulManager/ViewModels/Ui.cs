// UI 스레드 마샬링 헬퍼 — 엔진 스레드 이벤트를 Dispatcher로 전달.
using System;
using System.Globalization;
using System.Windows;
using System.Windows.Data;

namespace SimulManager.ViewModels
{
    internal static class Ui
    {
        public static void Run(Action action)
        {
            var app = Application.Current;
            if (app == null) return;
            if (app.Dispatcher.CheckAccess()) action();
            else app.Dispatcher.BeginInvoke(action);
        }
    }

    /// <summary>진행률(%) × 부모 폭 → 픽셀 폭 (progress-fill 바인딩용).</summary>
    public sealed class PctWidthConverter : IMultiValueConverter
    {
        public static readonly PctWidthConverter Instance = new PctWidthConverter();

        public object Convert(object[] values, Type targetType, object parameter, CultureInfo culture)
        {
            if (values.Length < 2 || !(values[0] is double) || !(values[1] is double)) return 0.0;
            double pct = (double)values[0];
            double width = (double)values[1];
            return Math.Max(0, Math.Min(1, pct / 100.0)) * width;
        }

        public object[] ConvertBack(object value, Type[] targetTypes, object parameter, CultureInfo culture) =>
            throw new NotSupportedException();
    }
}
