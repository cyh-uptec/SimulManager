// 메인 — HamburgerMenu 네비게이션 + 페이지 ViewModel 보유 (frontend App.tsx 대응).
using DevExpress.Mvvm;
using SimulManager.Services;

namespace SimulManager.ViewModels
{
    public sealed class MainViewModel : ViewModelBase
    {
        public MainViewModel(SimulatorHost host)
        {
            Host = host;
            Home = new HomeViewModel(host);
            Monitor = new MonitorViewModel(host);
            Data = new DataViewModel(host);
            Sequence = new SequenceViewModel(host);
            CurrentPage = "home";
        }

        public SimulatorHost Host { get; }
        public HomeViewModel Home { get; }
        public MonitorViewModel Monitor { get; }
        public DataViewModel Data { get; }
        public SequenceViewModel Sequence { get; }

        /// <summary>home | monitor | data | sequence</summary>
        public string CurrentPage
        {
            get => GetProperty(() => CurrentPage);
            set => SetProperty(() => CurrentPage, value);
        }
    }
}
