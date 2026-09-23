// 시퀀스 로그 — frontend/src/SequencePage.tsx 포팅.
// 페이지 단위 조회(50건), 최신 페이지(offset 0) 표시 중에는 신규 이벤트 시 자동 갱신(400ms 병합).
using System;
using System.Collections.ObjectModel;
using System.Windows.Threading;
using DevExpress.Mvvm;
using SimulManager.Services;
using SimulManager.Services.Historian;

namespace SimulManager.ViewModels
{
    public sealed class SequenceViewModel : ViewModelBase
    {
        private const int PageSize = 50;
        private readonly SimulatorHost _host;
        private readonly DispatcherTimer _mergeTimer;
        private int _offset;

        public SequenceViewModel(SimulatorHost host)
        {
            _host = host;
            PrevPageCommand = new DelegateCommand(
                () => { _offset = Math.Max(0, _offset - PageSize); FetchPage(); }, () => _offset > 0);
            NextPageCommand = new DelegateCommand(
                () => { _offset += PageSize; FetchPage(); }, () => _offset + PageSize < Total);

            _mergeTimer = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(400) };
            _mergeTimer.Tick += (s, e) => { _mergeTimer.Stop(); FetchPage(); };
            _host.Bus.Subscribe("sequence.event", (t, p) => Ui.Run(() =>
            {
                if (_offset == 0 && !_mergeTimer.IsEnabled) _mergeTimer.Start(); // 폭주 시 병합
            }));
            FetchPage();
        }

        public ObservableCollection<SeqLogItem> Entries { get; } = new ObservableCollection<SeqLogItem>();
        public int Total { get => GetProperty(() => Total); private set => SetProperty(() => Total, value); }
        public string HeaderText { get => GetProperty(() => HeaderText); private set => SetProperty(() => HeaderText, value); }
        public string PagerText { get => GetProperty(() => PagerText); private set => SetProperty(() => PagerText, value); }
        public bool IsEmpty { get => GetProperty(() => IsEmpty); private set => SetProperty(() => IsEmpty, value); }

        public DelegateCommand PrevPageCommand { get; }
        public DelegateCommand NextPageCommand { get; }

        private void FetchPage()
        {
            var page = _host.Engine.SequenceLogPage(_offset, PageSize);
            Entries.Clear();
            foreach (var e in page.Entries) Entries.Add(SeqLogItem.From(e));
            Total = page.Total;
            IsEmpty = page.Entries.Count == 0;
            HeaderText = "EMS · RTDS · 시뮬매니저 간 시퀀스 기록 — 총 " + Total.ToString("N0") + "건 (파일 보존)";
            int pageNo = _offset / PageSize + 1;
            int pageCount = Math.Max(1, (Total + PageSize - 1) / PageSize);
            PagerText = pageNo + " / " + pageCount + " 페이지"
                + (_offset == 0 && Total > 0 ? " · 실시간 갱신 중" : "");
            PrevPageCommand.RaiseCanExecuteChanged();
            NextPageCommand.RaiseCanExecuteChanged();
        }
    }
}
