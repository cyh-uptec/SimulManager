using System;
using System.IO;
using System.Windows.Controls;
using System.Windows.Media.Imaging;

namespace SimulManager.Views
{
    public partial class HomeView : UserControl
    {
        public HomeView()
        {
            InitializeComponent();
            TryLoadLogo();
        }

        /// <summary>회사 로고 (logo_wide.png) — 실행 폴더에 있으면 표시 (웹 onError 숨김과 동일).</summary>
        private void TryLoadLogo()
        {
            try
            {
                string path = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "logo_wide.png");
                if (!File.Exists(path)) return;
                var bmp = new BitmapImage();
                bmp.BeginInit();
                bmp.CacheOption = BitmapCacheOption.OnLoad;
                bmp.UriSource = new Uri(path);
                bmp.EndInit();
                CorpLogo.Source = bmp;
                CorpLogo.Visibility = System.Windows.Visibility.Visible;
            }
            catch { /* 로고 없음 — 숨김 유지 */ }
        }
    }
}
