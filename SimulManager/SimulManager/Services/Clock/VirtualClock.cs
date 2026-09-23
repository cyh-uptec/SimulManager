// 가상시계·가속엔진 — 시퀀스 설계서 §1.3·§3 (자유진행 원칙)
// (backend/app/clock/virtual_clock.py 포팅).
//
// - Run 시작(P3)~종료(P5)까지 노드 접속 여부와 무관하게 연속 진행 (free-running)
// - 가상 1분 = 실 (가속배율/15) 초. 예: 배율 10 s → 실 0.667 s
// - '일시정지'는 운영자의 명시적 조작에 한해서만 사용 (자동 예외처리 금지)
// - 30일차 마지막 구간(인덱스 95) 완료 시점에 정지 (P5) → COMPLETED
// - P6 케이스 전환 시 Rewind()로 시작시각 되감기
//
// 틱 이벤트 순서(구간 경계 원자성의 기반): 1분 진행 → clock.minute →
// (구간 변화 시) clock.interval → (자정 시) clock.day → (완료 시) clock.complete.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Threading;

namespace SimulManager.Services.Clock
{
    public enum ClockState
    {
        Idle,       // 시작 전 / 리셋(되감기) 후
        Running,
        Paused,     // 운영자 일시정지
        Completed,  // 전 구간 완료 (P5)
        Stopped,    // 운영자 수동 정지
    }

    public sealed class ClockTickPayload
    {
        public DateTime VirtualTime;
        public int IntervalIndex;
        public int DayNumber;
        public int ElapsedMinutes;
    }

    public sealed class ClockStatus
    {
        public ClockState State;
        public DateTime VirtualTime;
        public int IntervalIndex;
        public int DayNumber;
        public int TotalDays;
        public int ElapsedMinutes;
        public int TotalMinutes;
        public double Progress;
        public double AccelSecondsPer15Min;
        public double TickPeriodS;

        public string StateName => State.ToString().ToLowerInvariant();
    }

    public sealed class VirtualClock
    {
        public const int MinutesPerInterval = 15;
        public const int IntervalsPerDay = 96;

        private SimulationConfig _sim;
        private readonly EventBus _bus;
        private readonly object _lock = new object();
        private ClockState _state = ClockState.Idle;
        private DateTime _virtualTime;
        private int _elapsedMinutes;
        private Thread _thread;
        private readonly ManualResetEventSlim _resume = new ManualResetEventSlim(true);
        private volatile bool _loopCancel;

        public VirtualClock(SimulationConfig sim, EventBus bus)
        {
            _sim = sim;
            _bus = bus;
            _virtualTime = sim.StartVirtualTime;
        }

        // ── 조회 ──────────────────────────────────────────────
        public ClockState State { get { lock (_lock) return _state; } }
        public DateTime VirtualTime { get { lock (_lock) return _virtualTime; } }

        /// <summary>하루 내 15분 구간 인덱스 0~95.</summary>
        public int IntervalIndex
        {
            get { lock (_lock) return IntervalOf(_virtualTime); }
        }

        private static int IntervalOf(DateTime t) => (t.Hour * 60 + t.Minute) / MinutesPerInterval;

        /// <summary>Run 내 일차 (1부터).</summary>
        public int DayNumber
        {
            get { lock (_lock) return (int)(_virtualTime.Date - _sim.StartVirtualTime.Date).TotalDays + 1; }
        }

        public int TotalMinutes => _sim.Days * 24 * 60;
        public double TickPeriodS => _sim.RealSecondsPerVirtualMinute;

        public ClockStatus Status()
        {
            lock (_lock)
            {
                return new ClockStatus
                {
                    State = _state,
                    VirtualTime = _virtualTime,
                    IntervalIndex = IntervalOf(_virtualTime),
                    DayNumber = (int)(_virtualTime.Date - _sim.StartVirtualTime.Date).TotalDays + 1,
                    TotalDays = _sim.Days,
                    ElapsedMinutes = _elapsedMinutes,
                    TotalMinutes = TotalMinutes,
                    Progress = Math.Round((double)_elapsedMinutes / TotalMinutes, 4),
                    AccelSecondsPer15Min = _sim.AccelSecondsPer15Min,
                    TickPeriodS = Math.Round(TickPeriodS, 4),
                };
            }
        }

        // ── 제어 (UI → P3/P5/P6) ─────────────────────────────
        /// <summary>P3 운전 개시 — IDLE에서만 시작.</summary>
        public void Start()
        {
            lock (_lock)
            {
                if (_state != ClockState.Idle)
                    throw new InvalidOperationException("IDLE 상태에서만 시작 가능 (현재: " + _state + ")");
                _state = ClockState.Running;
            }
            _resume.Set();
            _loopCancel = false;
            _thread = new Thread(RunLoop) { IsBackground = true, Name = "virtual-clock" };
            _thread.Start();
            PublishState();
            Trace.TraceInformation("가상시계 시작: " + VirtualTime + ", 배율 " + _sim.AccelSecondsPer15Min + "s/15분");
        }

        /// <summary>운영자 수동 정지.</summary>
        public void Stop()
        {
            lock (_lock)
            {
                if (_state != ClockState.Running && _state != ClockState.Paused)
                    throw new InvalidOperationException("RUNNING/PAUSED 상태에서만 정지 가능 (현재: " + _state + ")");
                _state = ClockState.Stopped;
            }
            _resume.Set();
            CancelLoop();
            PublishState();
        }

        /// <summary>운영자 일시정지 — 자동 예외처리로 호출 금지 (자유진행 원칙).</summary>
        public void Pause()
        {
            lock (_lock)
            {
                if (_state != ClockState.Running)
                    throw new InvalidOperationException("RUNNING 상태에서만 일시정지 가능 (현재: " + _state + ")");
                _state = ClockState.Paused;
            }
            _resume.Reset();
            PublishState();
        }

        public void Resume()
        {
            lock (_lock)
            {
                if (_state != ClockState.Paused)
                    throw new InvalidOperationException("PAUSED 상태에서만 재개 가능 (현재: " + _state + ")");
                _state = ClockState.Running;
            }
            _resume.Set();
            PublishState();
        }

        /// <summary>P6 케이스 전환 — 시작시각으로 되감기 (RUNNING 중에는 불가).</summary>
        public void Rewind()
        {
            lock (_lock)
            {
                if (_state == ClockState.Running)
                    throw new InvalidOperationException("RUNNING 중에는 되감기 불가 — 먼저 정지하세요");
            }
            CancelLoop();
            lock (_lock)
            {
                _virtualTime = _sim.StartVirtualTime;
                _elapsedMinutes = 0;
                _state = ClockState.Idle;
            }
            _resume.Set();
            PublishState();
            Trace.TraceInformation("가상시계 되감기: " + VirtualTime);
        }

        /// <summary>설정 대시보드 반영 — IDLE(대기) 상태에서만. 가상시각을 새 시작시각으로 재설정.</summary>
        public void ApplyConfig(SimulationConfig sim)
        {
            lock (_lock)
            {
                if (_state != ClockState.Idle)
                    throw new InvalidOperationException(
                        "설정 변경은 대기(IDLE) 상태에서만 가능 (현재: " + _state + ") — 정지 후 되감기 하세요");
                _sim = sim;
                _virtualTime = sim.StartVirtualTime;
                _elapsedMinutes = 0;
            }
            PublishState();
        }

        public void Shutdown() => CancelLoop();

        // ── 진행 ──────────────────────────────────────────────
        /// <summary>가상 1분 진행 + 이벤트 발행 — 엔진 스레드에서 실행된다.</summary>
        public void AdvanceMinute()
        {
            ClockTickPayload payload;
            bool intervalChanged, dayBoundary, completed;
            lock (_lock)
            {
                int prevInterval = IntervalOf(_virtualTime);
                _virtualTime = _virtualTime.AddMinutes(1);
                _elapsedMinutes += 1;
                payload = new ClockTickPayload
                {
                    VirtualTime = _virtualTime,
                    IntervalIndex = IntervalOf(_virtualTime),
                    DayNumber = (int)(_virtualTime.Date - _sim.StartVirtualTime.Date).TotalDays + 1,
                    ElapsedMinutes = _elapsedMinutes,
                };
                intervalChanged = payload.IntervalIndex != prevInterval;
                dayBoundary = _virtualTime.Hour == 0 && _virtualTime.Minute == 0;
                completed = _elapsedMinutes >= TotalMinutes;
                if (completed) _state = ClockState.Completed;
            }
            _bus.Publish("clock.minute", payload);
            if (intervalChanged) _bus.Publish("clock.interval", payload);
            if (dayBoundary) _bus.Publish("clock.day", payload);
            if (completed)
            {
                _bus.Publish("clock.complete", Status());
                Trace.TraceInformation("Run 완료: 전 " + (_sim.Days * IntervalsPerDay) + "구간 종료");
            }
        }

        /// <summary>드리프트 보정 틱 루프 — 기준 실시각 + n×주기로 스케줄.</summary>
        private void RunLoop()
        {
            double period = TickPeriodS;
            var sw = Stopwatch.StartNew();
            double baseS = 0;
            long n = 0;
            while (!_loopCancel)
            {
                var state = State;
                if (state != ClockState.Running && state != ClockState.Paused) break;
                _resume.Wait();
                if (_loopCancel || State != ClockState.Running) { if (State == ClockState.Paused) continue; break; }
                n++;
                double delay = baseS + n * period - sw.Elapsed.TotalSeconds;
                if (delay > 0)
                    Thread.Sleep(TimeSpan.FromSeconds(delay));
                else if (delay < -period * 10)
                    baseS = sw.Elapsed.TotalSeconds - n * period; // 일시정지 등으로 크게 뒤처짐 — 기준 재설정
                if (_loopCancel || State != ClockState.Running) continue;
                _bus.Invoke(AdvanceMinute); // 엔진 스레드에서 원자적으로 처리 (backlog 방지 대기)
            }
            if (!_loopCancel) PublishState();
        }

        private void CancelLoop()
        {
            _loopCancel = true;
            _resume.Set();
            var t = _thread;
            if (t != null && t.IsAlive && t != Thread.CurrentThread)
                t.Join(2000);
            _thread = null;
        }

        private void PublishState() => _bus.Publish("clock.state", Status());
    }
}
