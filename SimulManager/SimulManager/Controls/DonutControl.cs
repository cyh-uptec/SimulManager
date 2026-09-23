// 원형(도넛) 차트 — HomePage.tsx의 SVG Donut 포팅 (구간 인덱스·단절 누적 링).
using System;
using System.Globalization;
using System.Windows;
using System.Windows.Media;

namespace SimulManager.Controls
{
    public sealed class DonutControl : FrameworkElement
    {
        public static readonly DependencyProperty ValueProperty = DependencyProperty.Register(
            nameof(Value), typeof(double), typeof(DonutControl),
            new FrameworkPropertyMetadata(0.0, FrameworkPropertyMetadataOptions.AffectsRender));

        public static readonly DependencyProperty MaximumProperty = DependencyProperty.Register(
            nameof(Maximum), typeof(double), typeof(DonutControl),
            new FrameworkPropertyMetadata(100.0, FrameworkPropertyMetadataOptions.AffectsRender));

        public static readonly DependencyProperty RingBrushProperty = DependencyProperty.Register(
            nameof(RingBrush), typeof(Brush), typeof(DonutControl),
            new FrameworkPropertyMetadata(new SolidColorBrush(Color.FromRgb(0x63, 0x66, 0xF1)),
                FrameworkPropertyMetadataOptions.AffectsRender));

        public static readonly DependencyProperty CenterTextProperty = DependencyProperty.Register(
            nameof(CenterText), typeof(string), typeof(DonutControl),
            new FrameworkPropertyMetadata("", FrameworkPropertyMetadataOptions.AffectsRender));

        public static readonly DependencyProperty SubTextProperty = DependencyProperty.Register(
            nameof(SubText), typeof(string), typeof(DonutControl),
            new FrameworkPropertyMetadata("", FrameworkPropertyMetadataOptions.AffectsRender));

        public double Value { get => (double)GetValue(ValueProperty); set => SetValue(ValueProperty, value); }
        public double Maximum { get => (double)GetValue(MaximumProperty); set => SetValue(MaximumProperty, value); }
        public Brush RingBrush { get => (Brush)GetValue(RingBrushProperty); set => SetValue(RingBrushProperty, value); }
        public string CenterText { get => (string)GetValue(CenterTextProperty); set => SetValue(CenterTextProperty, value); }
        public string SubText { get => (string)GetValue(SubTextProperty); set => SetValue(SubTextProperty, value); }

        private static readonly Brush TrackBrush = Frozen(new SolidColorBrush(Color.FromRgb(0xED, 0xF0, 0xF8)));
        private static readonly Brush TextBrush = Frozen(new SolidColorBrush(Color.FromRgb(0x23, 0x2D, 0x47)));
        private static readonly Brush MutedBrush = Frozen(new SolidColorBrush(Color.FromRgb(0x8A, 0x94, 0xAD)));
        private static readonly Typeface CenterFace =
            new Typeface(new FontFamily("Segoe UI"), FontStyles.Normal, FontWeights.ExtraBold, FontStretches.Normal);
        private static readonly Typeface SubFace =
            new Typeface(new FontFamily("Segoe UI"), FontStyles.Normal, FontWeights.SemiBold, FontStretches.Normal);

        private static Brush Frozen(SolidColorBrush b) { b.Freeze(); return b; }

        protected override void OnRender(DrawingContext dc)
        {
            double size = Math.Min(ActualWidth, ActualHeight);
            if (size <= 0) return;
            var center = new Point(ActualWidth / 2, ActualHeight / 2);
            double stroke = size * 0.11;
            double radius = size / 2 - stroke / 2;

            // 트랙
            dc.DrawEllipse(null, new Pen(TrackBrush, stroke), center, radius, radius);

            // 진행 아크 (12시 방향 시작, 시계 방향)
            double pct = Maximum > 0 ? Math.Max(0, Math.Min(1, Value / Maximum)) : 0;
            if (pct > 0)
            {
                var pen = new Pen(RingBrush, stroke)
                {
                    StartLineCap = PenLineCap.Round,
                    EndLineCap = PenLineCap.Round,
                };
                if (pct >= 0.9999)
                {
                    dc.DrawEllipse(null, pen, center, radius, radius);
                }
                else
                {
                    double angle = pct * 360.0;
                    Point start = PointAt(center, radius, -90);
                    Point end = PointAt(center, radius, -90 + angle);
                    var geo = new StreamGeometry();
                    using (var ctx = geo.Open())
                    {
                        ctx.BeginFigure(start, false, false);
                        ctx.ArcTo(end, new Size(radius, radius), 0, angle > 180,
                                  SweepDirection.Clockwise, true, false);
                    }
                    geo.Freeze();
                    dc.DrawGeometry(null, pen, geo);
                }
            }

            // 중앙 텍스트
            double dpi = VisualTreeHelper.GetDpi(this).PixelsPerDip;
            var centerFt = new FormattedText(CenterText ?? "", CultureInfo.CurrentUICulture,
                FlowDirection.LeftToRight, CenterFace, size * 0.23, TextBrush, dpi);
            dc.DrawText(centerFt, new Point(center.X - centerFt.Width / 2, center.Y - centerFt.Height * 0.85));
            var subFt = new FormattedText(SubText ?? "", CultureInfo.CurrentUICulture,
                FlowDirection.LeftToRight, SubFace, size * 0.095, MutedBrush, dpi);
            dc.DrawText(subFt, new Point(center.X - subFt.Width / 2, center.Y + centerFt.Height * 0.18));
        }

        private static Point PointAt(Point center, double radius, double angleDeg)
        {
            double rad = angleDeg * Math.PI / 180.0;
            return new Point(center.X + radius * Math.Cos(rad), center.Y + radius * Math.Sin(rad));
        }
    }
}
