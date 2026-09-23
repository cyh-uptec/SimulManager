// Home(운전 현황) — frontend/src/HomePage.tsx + useClock.ts 포팅.
// WebSocket 대신 이벤트 버스 구독, /api/signals 폴링 대신 1.5초 타이머로 뱅크 직접 조회.
using System;
using System.Collections.ObjectModel;
using System.Linq;
using System.Windows.Media;
using System.Windows.Threading;
using DevExpress.Mvvm;
using SimulManager.Services;
using SimulManager.Services.Clock;
using SimulManager.Services.Historian;
using SimulManager.Services.Sequence;
using SimulManager.Services.Watch;

namespace SimulManager.ViewModels
{
    public sealed class BitItem : BindableBase
    {
        public string Label { get; set; }
        public bool IsOn { get => GetProperty(() => IsOn); set => SetProperty(() => IsOn, value); }
    }

    public sealed class SeqLogItem
    {
        public string VirtualTime { get; set; }
        public string RealTime { get; set; }
        public string Phase { get; set; }
        public string Source { get; set; }
        public string SourceLabel { get; set; }
        public string Message { get; set; }
        public string Refs { get; set; }
        public bool HasRefs => !string.IsNullOrEmpty(Refs);

        public static SeqLogItem From(SeqEntry e)
        {
            return new SeqLogItem
            {
                VirtualTime = (e.VirtualTime ?? "—").Replace("T", " "),
                RealTime = (e.RealTime ?? "").Replace("T", " "),
                Phase = e.Phase,
                Source = e.Source,
                SourceLabel = e.Source == "ems" ? "EMS" : e.Source == "rtds" ? "RTDS" : "매니저",
                Message = e.Message,
                Refs = e.Refs ?? "",
            };
        }
    }

    public sealed class HomeViewModel : ViewModelBase, IDisposable
    {
        private const int SeqLogMax = 30;
        private readonly SimulatorHost _host;
        private readonly DispatcherTimer _signalTimer;

        public HomeViewModel(SimulatorHost host)
        {
            _host = host;
            ScenarioId = 1;

            StartRunCommand = new DelegateCommand(() => RunGuard(() =>
                _host.Bus.Invoke(() => _host.Engine.StartRun(ScenarioId))), () => CanStart);
            StopRunCommand = new DelegateCommand(() => RunGuard(() =>
                _host.Bus.Invoke(() => _host.Engine.StopRun())), () => CanStop);
            PauseCommand = new DelegateCommand(() => RunGuard(() => _host.Clock.Pause()), () => CanPause);
            ResumeCommand = new DelegateCommand(() => RunGuard(() => _host.Clock.Resume()), () => CanResume);
            ResetCommand = new DelegateCommand(() => RunGuard(() =>
                _host.Bus.Invoke(() => _host.Engine.ResetRun(ScenarioId))), () => CanReset);
            OpenSettingsCommand = new DelegateCommand(OpenSettings);

            _host.Bus.Subscribe("clock.state", OnClockEvent);
            _host.Bus.Subscribe("clock.minute", OnClockEvent);
            _host.Bus.Subscribe("clock.complete", OnClockEvent);
            _host.Bus.Subscribe("watch.update", OnWatchUpdate);
            _host.Bus.Subscribe("run.state", OnRunState);
            _host.Bus.Subscribe("sequence.event", OnSequenceEvent);

            ApplyClock(_host.Clock.Status());
            ApplyRun(_host.Bus.Invoke(() => _host.Engine.Status()));
            ApplyNodes(_host.Monitor.Status());
            foreach (var e in _host.SeqLog.Page(0, SeqLogMax).Entries)
                SeqLog.Add(SeqLogItem.From(e));
            RefreshSignals();

            _signalTimer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(1.5) };
            _signalTimer.Tick += (s, e) => RefreshSignals();
            _signalTimer.Start();
        }

        // ── 시계·Run 상태 ──────────────────────────────────────
        public string TimeText { get => GetProperty(() => TimeText); private set => SetProperty(() => TimeText, value); }
        public string DateText { get => GetProperty(() => DateText); private set => SetProperty(() => DateText, value); }
        public double ProgressPct { get => GetProperty(() => ProgressPct); private set => SetProperty(() => ProgressPct, value); }
        public string ProgressText { get => GetProperty(() => ProgressText); private set => SetProperty(() => ProgressText, value); }
        public double IntervalIndex { get => GetProperty(() => IntervalIndex); private set => SetProperty(() => IntervalIndex, value); }
        public string IntervalText { get => GetProperty(() => IntervalText); private set => SetProperty(() => IntervalText, value); }
        public string DayText { get => GetProperty(() => DayText); private set => SetProperty(() => DayText, value); }
        public string DaySubText { get => GetProperty(() => DaySubText); private set => SetProperty(() => DaySubText, value); }
        public string AccelText { get => GetProperty(() => AccelText); private set => SetProperty(() => AccelText, value); }
        public string AccelSubText { get => GetProperty(() => AccelSubText); private set => SetProperty(() => AccelSubText, value); }
        public string TickSubText { get => GetProperty(() => TickSubText); private set => SetProperty(() => TickSubText, value); }
        public double Disconnects { get => GetProperty(() => Disconnects); private set => SetProperty(() => Disconnects, value); }
        public double DisconnectBudget { get => GetProperty(() => DisconnectBudget); private set => SetProperty(() => DisconnectBudget, value); }
        public string DisconnectText { get => GetProperty(() => DisconnectText); private set => SetProperty(() => DisconnectText, value); }
        public string DisconnectSub { get => GetProperty(() => DisconnectSub); private set => SetProperty(() => DisconnectSub, value); }
        public Brush DisconnectBrush { get => GetProperty(() => DisconnectBrush); private set => SetProperty(() => DisconnectBrush, value); }

        public string StateLabel { get => GetProperty(() => StateLabel); private set => SetProperty(() => StateLabel, value); }
        public Brush StateBadgeBg { get => GetProperty(() => StateBadgeBg); private set => SetProperty(() => StateBadgeBg, value); }
        public Brush StateBadgeFg { get => GetProperty(() => StateBadgeFg); private set => SetProperty(() => StateBadgeFg, value); }
        public string RunBadgeText { get => GetProperty(() => RunBadgeText); private set => SetProperty(() => RunBadgeText, value); }
        public bool HasRunBadge { get => GetProperty(() => HasRunBadge); private set => SetProperty(() => HasRunBadge, value); }

        public string ErrorText { get => GetProperty(() => ErrorText); private set => SetProperty(() => ErrorText, value); }
        public bool HasError => !string.IsNullOrEmpty(ErrorText);
        public bool DataNotReady { get => GetProperty(() => DataNotReady); private set => SetProperty(() => DataNotReady, value); }

        // ── 노드 카드 ──────────────────────────────────────────
        public string EmsStateLabel { get => GetProperty(() => EmsStateLabel); private set => SetProperty(() => EmsStateLabel, value); }
        public Brush EmsBadgeBg { get => GetProperty(() => EmsBadgeBg); private set => SetProperty(() => EmsBadgeBg, value); }
        public Brush EmsBadgeFg { get => GetProperty(() => EmsBadgeFg); private set => SetProperty(() => EmsBadgeFg, value); }
        public Brush EmsTopBrush { get => GetProperty(() => EmsTopBrush); private set => SetProperty(() => EmsTopBrush, value); }
        public string EmsHeartbeat { get => GetProperty(() => EmsHeartbeat); private set => SetProperty(() => EmsHeartbeat, value); }
        public string EmsAge { get => GetProperty(() => EmsAge); private set => SetProperty(() => EmsAge, value); }
        public string RtdsStateLabel { get => GetProperty(() => RtdsStateLabel); private set => SetProperty(() => RtdsStateLabel, value); }
        public Brush RtdsBadgeBg { get => GetProperty(() => RtdsBadgeBg); private set => SetProperty(() => RtdsBadgeBg, value); }
        public Brush RtdsBadgeFg { get => GetProperty(() => RtdsBadgeFg); private set => SetProperty(() => RtdsBadgeFg, value); }
        public Brush RtdsTopBrush { get => GetProperty(() => RtdsTopBrush); private set => SetProperty(() => RtdsTopBrush, value); }
        public string RtdsHeartbeat { get => GetProperty(() => RtdsHeartbeat); private set => SetProperty(() => RtdsHeartbeat, value); }
        public string RtdsAge { get => GetProperty(() => RtdsAge); private set => SetProperty(() => RtdsAge, value); }

        // 링크 신호 비트
        public BitItem BitRunStart { get; } = new BitItem { Label = "운전 시작" };
        public BitItem BitRtdsReady { get; } = new BitItem { Label = "RTDS 준비됨" };
        public BitItem BitEmsEnable { get; } = new BitItem { Label = "EMS Enable" };
        public BitItem BitSocTrigger { get; } = new BitItem { Label = "SOC 트리거" };
        public BitItem BitSocAck { get; } = new BitItem { Label = "SOC 반영완료" };
        public BitItem BitEssEnableSim { get; } = new BitItem { Label = "ESS Enable(시뮬)" };
        public BitItem BitRtdsRunning { get; } = new BitItem { Label = "운전중" };

        // RTDS → 매니저 기록 값 (③HR)
        public string EssPowerText { get => GetProperty(() => EssPowerText); private set => SetProperty(() => EssPowerText, value); }
        public string EssSocText { get => GetProperty(() => EssSocText); private set => SetProperty(() => EssSocText, value); }
        public string PccImportText { get => GetProperty(() => PccImportText); private set => SetProperty(() => PccImportText, value); }
        public string PccExportText { get => GetProperty(() => PccExportText); private set => SetProperty(() => PccExportText, value); }
        public string RtdsStatusCodeText { get => GetProperty(() => RtdsStatusCodeText); private set => SetProperty(() => RtdsStatusCodeText, value); }

        // Run 제어
        public int ScenarioId { get => GetProperty(() => ScenarioId); set => SetProperty(() => ScenarioId, value, () => RaisePropertyChanged(nameof(ScenarioIndex))); }
        /// <summary>ComboBox SelectedIndex 바인딩용 (0=Run A / 1=Run B).</summary>
        public int ScenarioIndex
        {
            get => ScenarioId <= 1 ? 0 : 1;
            set => ScenarioId = value + 1;
        }
        public bool CanSelectScenario { get => GetProperty(() => CanSelectScenario); private set => SetProperty(() => CanSelectScenario, value); }
        public bool CanStart { get => GetProperty(() => CanStart); private set => SetProperty(() => CanStart, value, RaiseCanExecute); }
        public bool CanStop { get => GetProperty(() => CanStop); private set => SetProperty(() => CanStop, value, RaiseCanExecute); }
        public bool CanPause { get => GetProperty(() => CanPause); private set => SetProperty(() => CanPause, value, RaiseCanExecute); }
        public bool CanResume { get => GetProperty(() => CanResume); private set => SetProperty(() => CanResume, value, RaiseCanExecute); }
        public bool CanReset { get => GetProperty(() => CanReset); private set => SetProperty(() => CanReset, value, RaiseCanExecute); }

        public ObservableCollection<SeqLogItem> SeqLog { get; } = new ObservableCollection<SeqLogItem>();
        public bool SeqLogEmpty => SeqLog.Count == 0;

        public DelegateCommand StartRunCommand { get; }
        public DelegateCommand StopRunCommand { get; }
        public DelegateCommand PauseCommand { get; }
        public DelegateCommand ResumeCommand { get; }
        public DelegateCommand ResetCommand { get; }
        public DelegateCommand OpenSettingsCommand { get; }

        private ClockState _clockState = ClockState.Idle;
        private RunStatus _run;

        private void RaiseCanExecute()
        {
            StartRunCommand?.RaiseCanExecuteChanged();
            StopRunCommand?.RaiseCanExecuteChanged();
            PauseCommand?.RaiseCanExecuteChanged();
            ResumeCommand?.RaiseCanExecuteChanged();
            ResetCommand?.RaiseCanExecuteChanged();
        }

        private void RunGuard(Action action)
        {
            ErrorText = null;
            RaisePropertyChanged(nameof(HasError));
            try { action(); }
            catch (InvalidOperationException ex) { ErrorText = ex.Message; }
            catch (Exception ex) { ErrorText = "요청 실패: " + ex.Message; }
            RaisePropertyChanged(nameof(HasError));
        }

        private void OpenSettings()
        {
            var dlg = new Views.SettingsDialog(_host, _clockState == ClockState.Idle)
            {
                Owner = System.Windows.Application.Current.MainWindow,
            };
            dlg.ShowDialog();
        }

        // ── 이벤트 (엔진 스레드 → UI) ───────────────────────────
        private void OnClockEvent(string topic, object payload)
        {
            var status = payload as ClockStatus;
            if (status != null) { Ui.Run(() => ApplyClock(status)); return; }
            var tick = payload as ClockTickPayload;
            if (tick != null) Ui.Run(() => ApplyClock(_host.Clock.Status()));
        }

        private void OnWatchUpdate(string topic, object payload)
        {
            var nodes = payload as System.Collections.Generic.Dictionary<string, NodeStatus>;
            if (nodes != null) Ui.Run(() => ApplyNodes(nodes));
        }

        private void OnRunState(string topic, object payload)
        {
            var run = payload as RunStatus;
            if (run != null) Ui.Run(() => ApplyRun(run));
        }

        private void OnSequenceEvent(string topic, object payload)
        {
            var entry = payload as SeqEntry;
            if (entry == null) return;
            Ui.Run(() =>
            {
                SeqLog.Insert(0, SeqLogItem.From(entry));
                while (SeqLog.Count > SeqLogMax) SeqLog.RemoveAt(SeqLog.Count - 1);
                RaisePropertyChanged(nameof(SeqLogEmpty));
            });
        }

        // ── 상태 반영 (UI 스레드) ───────────────────────────────
        private void ApplyClock(ClockStatus s)
        {
            _clockState = s.State;
            TimeText = s.VirtualTime.ToString("HH:mm");
            DateText = s.VirtualTime.ToString("yyyy-MM-dd");
            ProgressPct = s.TotalMinutes > 0 ? (double)s.ElapsedMinutes / s.TotalMinutes * 100.0 : 0;
            ProgressText = s.ElapsedMinutes.ToString("N0") + " / " + s.TotalMinutes.ToString("N0") + "분";
            IntervalIndex = s.IntervalIndex;
            IntervalText = s.IntervalIndex.ToString();
            DayText = s.DayNumber + " / " + s.TotalDays + "일";
            DaySubText = "가상 " + s.TotalDays + "일 Run";
            AccelText = s.AccelSecondsPer15Min.ToString("0.##") + "s";
            AccelSubText = "실 " + s.AccelSecondsPer15Min.ToString("0.##") + "초 = 가상 15분";
            TickSubText = "틱 " + s.TickPeriodS.ToString("0.####") + "s / 가상 1분";

            switch (s.State)
            {
                case ClockState.Idle: SetStateBadge("대기", "chip"); break;
                case ClockState.Running: SetStateBadge("운전중", "teal"); break;
                case ClockState.Paused: SetStateBadge("일시정지", "amber"); break;
                case ClockState.Completed: SetStateBadge("Run 완료", "indigo"); break;
                default: SetStateBadge("정지됨", "chip"); break;
            }
            UpdateCommands();
        }

        private void SetStateBadge(string label, string kind)
        {
            StateLabel = label;
            switch (kind)
            {
                case "teal": StateBadgeBg = Brush("#E7F9F6"); StateBadgeFg = Brush("#0D9F92"); break;
                case "amber": StateBadgeBg = Brush("#FFF6E3"); StateBadgeFg = Brush("#C07F00"); break;
                case "indigo": StateBadgeBg = Brush("#EEF0FE"); StateBadgeFg = Brush("#4F46E5"); break;
                default: StateBadgeBg = Brush("#F1F3FA"); StateBadgeFg = Brush("#8A94AD"); break;
            }
        }

        private void ApplyRun(RunStatus run)
        {
            _run = run;
            double budget = run.DisconnectBudget > 0 ? run.DisconnectBudget : 14;
            Disconnects = run.DisconnectIntervals;
            DisconnectBudget = budget;
            DisconnectText = run.DisconnectIntervals.ToString();
            DisconnectSub = "/ " + (int)budget + " 구간";
            DisconnectBrush = run.DisconnectIntervals > budget * 0.7 ? Brush("#F26D6D") : Brush("#2EC5B6");
            string phaseLabel;
            switch (run.PhaseName)
            {
                case "P0": phaseLabel = "P0 대기"; break;
                case "P4": phaseLabel = "P4 운전 루프"; break;
                case "P5": phaseLabel = "P5 완료·정산"; break;
                default: phaseLabel = "수동 정지"; break;
            }
            HasRunBadge = run.RunLabel != null;
            RunBadgeText = run.RunLabel != null ? "Run " + run.RunLabel + " · " + phaseLabel : "";
            DataNotReady = !run.DataReady;
            UpdateCommands();
        }

        private void ApplyNodes(System.Collections.Generic.Dictionary<string, NodeStatus> nodes)
        {
            NodeStatus ems, rtds;
            if (nodes.TryGetValue("ems", out ems))
            {
                EmsStateLabel = NodeLabel(ems.State);
                EmsHeartbeat = ems.Heartbeat.HasValue ? ems.Heartbeat.ToString() : "—";
                EmsAge = ems.AgeS.HasValue ? ems.AgeS + "s 전" : "—";
                ApplyNodeBadge(ems.State, v => EmsBadgeBg = v, v => EmsBadgeFg = v, v => EmsTopBrush = v);
            }
            if (nodes.TryGetValue("rtds", out rtds))
            {
                RtdsStateLabel = NodeLabel(rtds.State);
                RtdsHeartbeat = rtds.Heartbeat.HasValue ? rtds.Heartbeat.ToString() : "—";
                RtdsAge = rtds.AgeS.HasValue ? rtds.AgeS + "s 전" : "—";
                ApplyNodeBadge(rtds.State, v => RtdsBadgeBg = v, v => RtdsBadgeFg = v, v => RtdsTopBrush = v);
            }
        }

        private static string NodeLabel(NodeState state) =>
            state == NodeState.Alive ? "정상" : state == NodeState.Lost ? "단절" : "미접속";

        private static void ApplyNodeBadge(NodeState state, Action<Brush> bg, Action<Brush> fg, Action<Brush> top)
        {
            switch (state)
            {
                case NodeState.Alive: bg(Brush("#E7F9F6")); fg(Brush("#0D9F92")); top(Brush("#2EC5B6")); break;
                case NodeState.Lost: bg(Brush("#FDEEEE")); fg(Brush("#D64545")); top(Brush("#F26D6D")); break;
                default: bg(Brush("#F1F3FA")); fg(Brush("#8A94AD")); top(Brush("#E8ECF5")); break;
            }
        }

        private void RefreshSignals()
        {
            var s = _host.Signals();
            BitRunStart.IsOn = s.RunStart == 1;
            BitRtdsReady.IsOn = s.RtdsReady == 1;
            BitEmsEnable.IsOn = s.EmsEnable == 1;
            BitSocTrigger.IsOn = s.InitialSocTrigger == 1;
            BitSocAck.IsOn = s.InitialSocAck == 1;
            BitEssEnableSim.IsOn = s.EssRunEnableSim == 1;
            BitRtdsRunning.IsOn = s.RtdsRunning == 1;
            EssPowerText = FmtPower(s.EssPower);
            EssSocText = s.EssSoc.ToString("0.0") + " %";
            PccImportText = s.PccImport.ToString("0.0") + " kW";
            PccExportText = s.PccExport.ToString("0.0") + " kW";
            RtdsStatusCodeText = s.RtdsStatusCode == 0 ? "0 정상"
                : s.RtdsStatusCode == 1 ? "1 주의"
                : s.RtdsStatusCode == 2 ? "2 고장" : s.RtdsStatusCode.ToString();
        }

        private static string FmtPower(double kw)
        {
            string dir = kw > 0.05 ? " (방전)" : kw < -0.05 ? " (충전)" : "";
            return kw.ToString("0.0") + " kW" + dir;
        }

        private void UpdateCommands()
        {
            bool idle = _clockState == ClockState.Idle;
            CanSelectScenario = idle;
            CanStart = idle && (_run == null || _run.DataReady);
            CanPause = _clockState == ClockState.Running;
            CanResume = _clockState == ClockState.Paused;
            CanStop = _clockState == ClockState.Running || _clockState == ClockState.Paused;
            CanReset = _clockState != ClockState.Running;
        }

        private static Brush Brush(string hex)
        {
            var b = (SolidColorBrush)new BrushConverter().ConvertFromString(hex);
            b.Freeze();
            return b;
        }

        public void Dispose() => _signalTimer.Stop();
    }
}
