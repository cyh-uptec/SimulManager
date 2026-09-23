// 시퀀스 엔진 — 설계서 §3 P0~P6 상태머신·연결중개·핸드셰이크·환경 배포·§6 예외
// (backend/app/sequence/engine.py 포팅).
//
// 매니저 원칙 (docs/PROTOCOL_RULES.md):
// - 가상시계 자유진행: 어떤 예외도 시계를 멈추지 않는다 ('일시정지'는 운영자 전용)
// - 운영 제어 비개입: 환경 전달·연결중개·기록만 수행
// - 구간 경계 원자성: 시각·기상·부하 갱신 후 구간 인덱스를 마지막에 갱신
//
// 이벤트 구독:
//   clock.minute   → P4(a) 가상 1분 환경 배포
//   clock.complete → P5 종료·정산 (보존 SOC 저장)
//   modbus.write   → P1 연결중개(③Coil0), P2 SOC ack(③Coil1)
//   watch.alive/lost → ③IR 300003 'EMS 상태', ①Coil4 'RTDS 준비됨', 단절 구간 누적
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Web.Script.Serialization;
using SimulManager.Services.Clock;
using SimulManager.Services.Historian;
using SimulManager.Services.Modbus;
using SimulManager.Services.Protocol;
using SimulManager.Services.Scenario;
using SimulManager.Services.Watch;

namespace SimulManager.Services.Sequence
{
    public enum RunPhase
    {
        P0Idle,       // 준비·대기
        P4Running,    // 정상 운전 루프 (P2·P3 절차는 시작 시 일괄 수행)
        P5Completed,  // 전 구간 완료·정산
        Stopped,      // 운영자 수동 정지
    }

    public sealed class RunStatus
    {
        public RunPhase Phase;
        public int? RunId;
        public int ScenarioId;
        public string RunLabel;
        public bool SocInjectionPending;
        public int DisconnectIntervals;
        public int DisconnectBudget;
        public bool DataReady;

        public string PhaseName
        {
            get
            {
                switch (Phase)
                {
                    case RunPhase.P0Idle: return "P0";
                    case RunPhase.P4Running: return "P4";
                    case RunPhase.P5Completed: return "P5";
                    default: return "stopped";
                }
            }
        }
    }

    public sealed class SequenceEngine
    {
        private static readonly Dictionary<int, string> ScenarioRunLabel =
            new Dictionary<int, string> { { 1, "A" }, { 2, "B" } };

        private readonly AppConfig _config;
        private readonly EventBus _bus;
        private readonly VirtualClock _clock;
        private readonly RegisterBank _bank1;
        private readonly RegisterBank _bank3;
        private readonly HeartbeatMonitor _monitor;
        private readonly EnvironmentSampler _sampler;
        private readonly string _persistedSocPath;
        private readonly SequenceLogStore _seqStore;

        public HistorianStore Historian { get; }

        private RunPhase _phase = RunPhase.P0Idle;
        private int _scenarioId;
        private DateTime? _runStartedAtVirtual;
        private bool _socInjectionPending;
        private readonly HashSet<int> _disconnectIntervals = new HashSet<int>();
        private int? _currentRunId;
        private readonly Dictionary<string, int> _diState = new Dictionary<string, int>(); // 반복 로그 방지

        public SequenceEngine(AppConfig config, EventBus bus, VirtualClock clock,
                              Dictionary<Link, RegisterBank> banks, HeartbeatMonitor monitor,
                              EnvironmentSampler sampler, string dataDir,
                              HistorianStore historian, SequenceLogStore seqStore)
        {
            _config = config;
            _bus = bus;
            _clock = clock;
            _bank1 = banks[Link.Link1];
            _bank3 = banks[Link.Link3];
            _monitor = monitor;
            _sampler = sampler;
            _persistedSocPath = Path.Combine(dataDir, "persisted_soc.json");
            _seqStore = seqStore;
            Historian = historian;

            bus.Subscribe("clock.minute", OnMinute);
            bus.Subscribe("clock.interval", OnInterval);
            bus.Subscribe("clock.complete", OnComplete);
            bus.Subscribe("modbus.write", OnModbusWrite);
            bus.Subscribe("watch.alive", OnWatch);
            bus.Subscribe("watch.lost", OnWatch);
        }

        // ── 상태 조회 ───────────────────────────────────────────
        /// <summary>Historian 레코더용 — (현재 run_id, 가상시각 ISO).</summary>
        public Tuple<int?, string> RunContext() =>
            Tuple.Create(_currentRunId, _clock.VirtualTime.ToString("yyyy-MM-dd'T'HH:mm"));

        public RunStatus Status()
        {
            return new RunStatus
            {
                Phase = _phase,
                RunId = _currentRunId,
                ScenarioId = _scenarioId,
                RunLabel = ScenarioRunLabel.ContainsKey(_scenarioId) ? ScenarioRunLabel[_scenarioId] : null,
                SocInjectionPending = _socInjectionPending,
                DisconnectIntervals = _disconnectIntervals.Count,
                DisconnectBudget = _config.Timing.DisconnectBudgetIntervals,
                DataReady = _sampler.Ready(),
            };
        }

        public SeqLogPage SequenceLogPage(int offset = 0, int limit = 50) =>
            _seqStore.Page(offset, limit);

        // ── 운영자 제어 (UI — 엔진 스레드에서 실행할 것: bus.Invoke 경유) ──
        /// <summary>P2 초기화 + P3 운전 개시 (설계서 §3.3~3.4).</summary>
        public void StartRun(int scenarioId)
        {
            if (_clock.State != ClockState.Idle)
                throw new InvalidOperationException(
                    "Run 시작은 대기(IDLE) 상태에서만 가능 (현재: " + _clock.State.ToString().ToLowerInvariant() + ")");
            if (!_sampler.Ready())
                throw new InvalidOperationException("부하·기상 데이터가 로드되지 않았습니다 — 데이터 메뉴에서 업로드하세요");

            var sim = _config.Simulation;
            _scenarioId = scenarioId;
            _disconnectIntervals.Clear();
            string runLabel = ScenarioRunLabel.ContainsKey(scenarioId)
                ? ScenarioRunLabel[scenarioId] : scenarioId.ToString();

            // 리셋 비트 해제 (P6 잔여) + 정지 비트 해제
            _bank1.Set("reset", 0);
            _bank3.Set("reset", 0);
            _bank1.Set("run_stop", 0);

            // P2-1: 초기 SOC 주입 트리거 (Run B도 재주입 — 공정 비교)
            _bank3.Set("initial_soc", sim.InitialSoc);
            _bank3.Set("initial_soc_trigger", 1);
            _socInjectionPending = true;

            // P2-5: 시나리오 ID·가속배율·시작시각
            _bank1.Set("scenario_id", scenarioId);
            _bank3.Set("scenario_id", scenarioId);
            _bank1.Set("accel_factor", sim.AccelSecondsPer15Min);
            _bank3.Set("accel_factor", sim.AccelSecondsPer15Min);
            _runStartedAtVirtual = sim.StartVirtualTime;
            Distribute(sim.StartVirtualTime); // t0 환경 선배포
            Log("P2 초기화 — Run " + runLabel + ": 시나리오 ID " + scenarioId + "·가속배율 "
                + sim.AccelSecondsPer15Min + "s 배포, 초기 SOC " + sim.InitialSoc + "% 주입 트리거 세트",
                refs: new[]
                {
                    Links.Link1Map["scenario_id"].ToString(), Links.Link1Map["accel_factor"].ToString(),
                    Links.Link3Map["initial_soc"].ToString(), Links.Link3Map["initial_soc_trigger"].ToString(),
                },
                step: "P2");

            // Historian Run 태깅
            _currentRunId = Historian.StartRun(
                label: runLabel, scenarioId: scenarioId,
                startedReal: DateTime.Now.ToString("yyyy-MM-dd'T'HH:mm:ss"),
                startedVirtual: sim.StartVirtualTime.ToString("yyyy-MM-dd'T'HH:mm"),
                initialSoc: sim.InitialSoc);

            // P3: 운전 개시 — Enable → 운전시작 → 가상시계 기동(자유진행)
            _bank1.Set("ems_enable", 1);
            _bank3.Set("ess_run_enable_sim", 1);
            _bank1.Set("run_start", 1);
            _clock.Start();

            _phase = RunPhase.P4Running;
            Log("P3 운전 개시 — Run " + runLabel + " 가상시계 기동 (자유진행, 종료까지 정지 없음)",
                refs: new[]
                {
                    Links.Link1Map["ems_enable"].ToString(), Links.Link1Map["run_start"].ToString(),
                    Links.Link3Map["ess_run_enable_sim"].ToString(),
                },
                step: "P3");
            PublishState();
        }

        /// <summary>운영자 수동 정지.</summary>
        public void StopRun()
        {
            if (_clock.State != ClockState.Running && _clock.State != ClockState.Paused)
                throw new InvalidOperationException("운전 중에만 정지할 수 있습니다");
            _clock.Stop();
            EndRun(RunPhase.Stopped, "운영자 수동 정지");
        }

        /// <summary>P6 케이스 전환 — 리셋 비트·시계 되감기·시나리오 변경 (설계서 §3.7).</summary>
        public void ResetRun(int? nextScenarioId = null)
        {
            if (_clock.State == ClockState.Running)
                throw new InvalidOperationException("운전 중에는 리셋할 수 없습니다 — 먼저 정지하세요");
            // 6-1: 양 노드 리셋 비트 세트 (다음 Run 시작 시 해제)
            _bank1.Set("reset", 1);
            _bank3.Set("reset", 1);
            // 6-2: 시계 되감기 + 운전 비트 정리
            _clock.Rewind();
            _bank1.Set("run_start", 0);
            _bank1.Set("run_stop", 0);
            _bank1.Set("ems_enable", 0);
            _bank3.Set("ess_run_enable_sim", 0);
            _bank3.Set("initial_soc_trigger", 0);
            _socInjectionPending = false;
            if (nextScenarioId.HasValue)
            {
                _scenarioId = nextScenarioId.Value;
                _bank1.Set("scenario_id", nextScenarioId.Value);
                _bank3.Set("scenario_id", nextScenarioId.Value);
            }
            _phase = RunPhase.P0Idle;
            _disconnectIntervals.Clear();
            Log("P6 리셋·케이스 전환 — 시계 되감기, 시나리오 ID " + (nextScenarioId ?? _scenarioId),
                refs: new[]
                {
                    Links.Link1Map["reset"].ToString(), Links.Link3Map["reset"].ToString(),
                    Links.Link1Map["scenario_id"].ToString(),
                },
                step: "P6");
            PublishState();
        }

        // ── 이벤트 핸들러 (엔진 스레드) ───────────────────────────
        /// <summary>P4(a) 가상 1분 사이클 — 환경 배포.</summary>
        private void OnMinute(string topic, object payload)
        {
            if (_phase != RunPhase.P4Running) return;
            var tick = (ClockTickPayload)payload;
            Distribute(tick.VirtualTime);
        }

        /// <summary>구간 경계 — 단절 '지속' 구간 누적 (§6: 단절 상태로 경과한 구간 수 기준).</summary>
        private void OnInterval(string topic, object payload)
        {
            if (_phase != RunPhase.P4Running) return;
            var tick = (ClockTickPayload)payload;
            var states = _monitor.Status();
            foreach (var node in states.Values)
            {
                if (node.State == NodeState.Lost)
                {
                    int globalInterval = (tick.DayNumber - 1) * 96 + tick.IntervalIndex;
                    CountDisconnect(globalInterval);
                    break;
                }
            }
        }

        /// <summary>P5 — 전 구간 완료.</summary>
        private void OnComplete(string topic, object payload)
        {
            EndRun(RunPhase.P5Completed, "전 구간 완료 (P5)");
        }

        private void OnModbusWrite(string topic, object payload)
        {
            var evt = payload as ModbusWriteEvent;
            if (evt == null || evt.Key == null) return;

            if (evt.Link == Link.Link3 && evt.Key == "rtds_running")
            {
                // P1 연결중개: RTDS 운전중 → EMS에 'RTDS 준비됨' 통지
                bool running = evt.Values[0] != 0;
                _bank1.Set("rtds_ready", running ? 1 : 0);
                Log("연결중개: RTDS 운전중=" + (running ? 1 : 0) + " → ①'RTDS 준비됨'=" + (running ? 1 : 0),
                    refs: new[] { Links.Link3Map["rtds_running"].ToString(), Links.Link1Map["rtds_ready"].ToString() },
                    source: "rtds", step: "P1");
            }
            else if (evt.Link == Link.Link3 && evt.Key == "initial_soc_ack")
            {
                // P2 핸드셰이크: ack 확인 → 트리거 해제 (레벨 핸드셰이크)
                if (evt.Values[0] != 0 && _socInjectionPending)
                {
                    _bank3.Set("initial_soc_trigger", 0);
                    _socInjectionPending = false;
                    Log("초기 SOC 반영완료(ack) 수신 — 주입 트리거 해제 (P2 핸드셰이크 완결)",
                        refs: new[]
                        {
                            Links.Link3Map["initial_soc_ack"].ToString(),
                            Links.Link3Map["initial_soc_trigger"].ToString(),
                        },
                        source: "rtds", step: "P2");
                }
            }
            else if (evt.Link == Link.Link1 && evt.Key == "forecast_base_interval")
            {
                // EMS 예측 블록 기록 (B-4) — 15분 구간마다
                Log("EMS 예측 블록 기록 — 기준 구간 " + evt.Physical +
                    ", [1구간](PV·부하·스케줄·목표SOC, [2~4구간]은 예약)",
                    refs: new[]
                    {
                        Links.Link1Map["forecast_base_interval"].ToString(),
                        "①IR 300003~300006 (미러 HR 430002~430006, 300007~300018 예약)",
                    },
                    source: "ems");
            }
            else if (evt.Link == Link.Link1 && evt.Obj == ObjType.Di)
            {
                // EMS 상태 DI 변화 (준비완료·연산중·예측완료·알람) — 변화 시에만
                int value = evt.Values[0] != 0 ? 1 : 0;
                int prev;
                if (!_diState.TryGetValue(evt.Key, out prev) || prev != value)
                {
                    _diState[evt.Key] = value;
                    var reg = Links.Link1Map[evt.Key];
                    Log("EMS 상태 기록: " + reg.Name + "=" + value,
                        refs: new[] { reg.ToString() }, source: "ems");
                }
            }
        }

        /// <summary>§6 예외 — 시계는 계속 진행, 상태 레지스터·단절 집계만 갱신.</summary>
        private void OnWatch(string topic, object payload)
        {
            var tr = (WatchTransition)payload;
            bool alive = topic == "watch.alive";
            if (tr.Node == "ems")
            {
                _bank3.Set("ems_status", alive ? 1 : 0); // ③IR 300003
                Log("EMS " + (alive ? "연결" : "단절") + " → ③'EMS 상태'=" + (alive ? 1 : 0)
                    + (alive ? "" : " (RTDS는 워치독 정체로 자체 안전상태 P=0)"),
                    refs: new[] { Links.Link1Map["ems_heartbeat"].ToString(), Links.Link3Map["ems_status"].ToString() },
                    step: alive ? "P1" : null);
            }
            else // rtds
            {
                if (!alive)
                    _bank1.Set("rtds_ready", 0); // 재접속 시엔 RTDS의 ③Coil0 기록으로 복구 (R-3)
                var refs = new List<string> { Links.Link3Map["rtds_heartbeat"].ToString() };
                if (!alive) refs.Add(Links.Link1Map["rtds_ready"].ToString());
                Log("RTDS " + (alive ? "연결" : "단절")
                    + (alive ? "" : " → ①'RTDS 준비됨'=0, EMS 지령 중단 안내"),
                    refs: refs.ToArray(), step: alive ? "P1" : null);
            }
            if (!alive && _phase == RunPhase.P4Running)
            {
                // 전이 시점 구간 즉시 누적 — 이후 지속 단절은 OnInterval이 구간마다 누적
                int globalInterval = (_clock.DayNumber - 1) * 96 + _clock.IntervalIndex;
                CountDisconnect(globalInterval);
            }
            PublishState();
        }

        private void CountDisconnect(int globalInterval)
        {
            int budget = _config.Timing.DisconnectBudgetIntervals;
            bool wasWithin = _disconnectIntervals.Count <= budget;
            _disconnectIntervals.Add(globalInterval);
            if (wasWithin && _disconnectIntervals.Count > budget)
                Log("⚠ 단절 누적 " + _disconnectIntervals.Count + "구간 — 허용 한도(" + budget
                    + ") 초과, Run 무효 검토 필요 (설계서 §6)");
        }

        // ── 내부 ────────────────────────────────────────────────
        /// <summary>가상 1분 환경 배포 — 시각·기상·부하 먼저, 구간 인덱스는 마지막(원자성).</summary>
        private void Distribute(DateTime virtualTime)
        {
            if (!_runStartedAtVirtual.HasValue) return;
            var env = _sampler.Sample(virtualTime, _runStartedAtVirtual.Value);

            // 1) 링크① 시각
            _bank1.Set("sim_year", virtualTime.Year);
            _bank1.Set("sim_month", virtualTime.Month);
            _bank1.Set("sim_day", virtualTime.Day);
            _bank1.Set("sim_hour", virtualTime.Hour);
            _bank1.Set("sim_minute", virtualTime.Minute);
            // 2) 기상 (링크①·③ — 스케일은 protocol Codec이 링크별 적용, 일사는 MJ/m² v1.0)
            _bank1.Set("irradiance", env.IrradianceMj);
            _bank1.Set("ambient_temp", env.TempC);
            _bank1.Set("snow_depth", env.SnowCm);
            _bank1.Set("weather_code", env.WeatherCode);
            _bank3.Set("irradiance", env.IrradianceMj);
            _bank3.Set("ambient_temp", env.TempC);
            _bank3.Set("snow_depth", env.SnowCm);
            _bank3.Set("weather_code", env.WeatherCode);
            // 3) 부하 프로파일 — RTDS로만 (EMS 블라인드)
            _bank3.Set("load_profile_ref", env.LoadKw);
            // 4) 구간 인덱스 — 반드시 마지막 (EMS의 구간 변화 트리거 정합)
            int interval = (virtualTime.Hour * 60 + virtualTime.Minute) / 15;
            _bank1.Set("interval_index", interval);
            _bank3.Set("interval_index", interval);
        }

        /// <summary>P5 종료·정산 공통 — 운전 비트 정리·최종 SOC 보존.</summary>
        private void EndRun(RunPhase phase, string reason)
        {
            _bank1.Set("run_start", 0);
            _bank1.Set("run_stop", 1);
            _bank3.Set("ess_run_enable_sim", 0);
            // 주입 미완 트리거 잔존 방지 — 종료된 Run의 SOC를 늦게 주입하지 않도록
            _bank3.Set("initial_soc_trigger", 0);
            _socInjectionPending = false;
            double finalSoc = _bank3.Get("ess_soc"); // ③HR 400003 — RTDS가 기록한 보존 SOC 갱신원
            SavePersistedSoc(finalSoc);
            if (_currentRunId.HasValue) // Historian Run 마감
            {
                Historian.EndRun(
                    _currentRunId.Value,
                    status: phase == RunPhase.P5Completed ? "completed" : "stopped",
                    endedReal: DateTime.Now.ToString("yyyy-MM-dd'T'HH:mm:ss"),
                    endedVirtual: _clock.VirtualTime.ToString("yyyy-MM-dd'T'HH:mm"),
                    finalSoc: finalSoc,
                    disconnectIntervals: _disconnectIntervals.Count);
                _currentRunId = null;
            }
            _phase = phase;
            Log("P5 종료·정산(" + reason + ") — 최종 SOC " + finalSoc + "% 보존, 단절 누적 "
                + _disconnectIntervals.Count + "구간",
                refs: new[] { Links.Link1Map["run_stop"].ToString(), Links.Link3Map["ess_soc"].ToString() },
                step: "P5");
            PublishState();
        }

        private void SavePersistedSoc(double soc)
        {
            var ser = new JavaScriptSerializer();
            File.WriteAllText(_persistedSocPath, ser.Serialize(new Dictionary<string, object>
            {
                { "soc", soc },
                { "saved_at", DateTime.Now.ToString("yyyy-MM-dd'T'HH:mm:ss") },
                { "scenario_id", _scenarioId },
            }), Encoding.UTF8);
        }

        /// <summary>시퀀스 로그 — refs: 프로토콜 레지스터 참조(①②③+주소, 검증용).
        /// step: 설계서 §3의 시퀀스 단계 라벨(P1~P6). 생략 시 엔진 현재 상태(P0/P4/P5).</summary>
        private void Log(string message, string[] refs = null, string source = "manager", string step = null)
        {
            var entry = new SeqEntry
            {
                RealTime = DateTime.Now.ToString("yyyy-MM-dd'T'HH:mm:ss"),
                VirtualTime = _clock.VirtualTime.ToString("yyyy-MM-dd'T'HH:mm"),
                Phase = step ?? Status().PhaseName,
                Source = source,
                Message = message,
                Refs = refs != null ? string.Join(" · ", refs) : "",
            };
            _seqStore.Append(entry);
            Trace.TraceInformation("[시퀀스] " + message);
            _bus.Publish("sequence.event", entry);
        }

        private void PublishState() => _bus.Publish("run.state", Status());
    }
}
