// 컴포지션 루트 — backend/app/main.py create_app 대응.
// 모든 서비스 인스턴스를 생성·배선하고 수명을 관리한다.
// (FastAPI REST/WebSocket 대신 WPF ViewModel이 이 객체를 통해 직접 접근한다.)
using System;
using System.Collections.Generic;
using SimulManager.Services.Clock;
using SimulManager.Services.Historian;
using SimulManager.Services.Modbus;
using SimulManager.Services.Protocol;
using SimulManager.Services.Scenario;
using SimulManager.Services.Sequence;
using SimulManager.Services.Watch;

namespace SimulManager.Services
{
    public sealed class SimulatorHost : IDisposable
    {
        public AppConfig Config { get; }
        public EventBus Bus { get; }
        public VirtualClock Clock { get; }
        public Dictionary<Link, RegisterBank> Banks { get; }
        public ModbusServerManager Modbus { get; }
        public HeartbeatMonitor Monitor { get; }
        public ScenarioStore Scenario { get; }
        public WeatherEventStore WeatherEvents { get; }
        public EnvironmentSampler Sampler { get; }
        public SequenceEngine Engine { get; }
        public HistorianStore Historian { get; }
        public SequenceLogStore SeqLog { get; }
        public HistorianRecorder Recorder { get; }

        public RegisterBank Bank1 => Banks[Link.Link1];
        public RegisterBank Bank3 => Banks[Link.Link3];

        public SimulatorHost()
        {
            Config = AppConfig.Load();
            Bus = new EventBus();
            Clock = new VirtualClock(Config.Simulation, Bus);
            Banks = new Dictionary<Link, RegisterBank>
            {
                { Link.Link1, new RegisterBank(Links.Link1Map, Bus) },
                { Link.Link3, new RegisterBank(Links.Link3Map, Bus) },
            };
            Modbus = new ModbusServerManager(Config.Network, Banks);
            Monitor = new HeartbeatMonitor(Bus, Config.Timing);
            string dataDir = AppConfig.DataDir;
            Scenario = new ScenarioStore(dataDir);
            WeatherEvents = new WeatherEventStore(dataDir);
            Sampler = new EnvironmentSampler(Scenario, WeatherEvents, Config.Scenario.Interpolation);
            Historian = new HistorianStore(dataDir);
            SeqLog = new SequenceLogStore(dataDir);
            Engine = new SequenceEngine(Config, Bus, Clock, Banks, Monitor, Sampler, dataDir,
                                        Historian, SeqLog);
            Recorder = new HistorianRecorder(Historian, Bus, Engine.RunContext);

            // 초기 정적 레지스터
            var sim = Config.Simulation;
            Bank1.Set("accel_factor", sim.AccelSecondsPer15Min);
            Bank3.Set("accel_factor", sim.AccelSecondsPer15Min);
            Bank3.Set("initial_soc", sim.InitialSoc);
        }

        public void Start()
        {
            Modbus.Start();
            Monitor.Start();
        }

        /// <summary>시뮬레이션 설정 반영 — 대기(IDLE) 상태에서만. user_settings.json에 영속화.</summary>
        public void ApplySettings(SimulationConfig sim)
        {
            sim.Validate();
            Bus.Invoke(() =>
            {
                Clock.ApplyConfig(sim);
                Config.Simulation = sim;
                Bank1.Set("accel_factor", sim.AccelSecondsPer15Min);
                Bank3.Set("accel_factor", sim.AccelSecondsPer15Min);
                Bank3.Set("initial_soc", sim.InitialSoc);
            });
            AppConfig.SaveSimulation(sim);
        }

        /// <summary>Home 대시보드용 핵심 신호·계측 스냅샷 (backend /api/signals 대응).</summary>
        public SignalsSnapshot Signals()
        {
            return new SignalsSnapshot
            {
                RunStart = (int)Bank1.Get("run_start"),
                RtdsReady = (int)Bank1.Get("rtds_ready"),        // ①Coil 000004
                EmsEnable = (int)Bank1.Get("ems_enable"),        // ①Coil 000005
                InitialSocTrigger = (int)Bank3.Get("initial_soc_trigger"), // ③DI 100000
                EssRunEnableSim = (int)Bank3.Get("ess_run_enable_sim"),    // ③DI 100001
                RtdsRunning = (int)Bank3.Get("rtds_running"),              // ③Coil 000000 (RTDS 씀)
                InitialSocAck = (int)Bank3.Get("initial_soc_ack"),         // ③Coil 000001 (RTDS 씀)
                RtdsStatusCode = (int)Bank3.Get("rtds_status_code"),       // ③HR 400001 (RTDS 씀)
                EssPower = Bank3.Get("ess_power"),                         // ③HR 400002 [kW]
                EssSoc = Bank3.Get("ess_soc"),                             // ③HR 400003 [%]
                PccImport = Bank3.Get("pcc_import"),                       // ③HR 400004 [kW]
                PccExport = Bank3.Get("pcc_export"),                       // ③HR 400005 [kW]
            };
        }

        public void Dispose()
        {
            Monitor.Stop();
            Modbus.Stop();
            Clock.Shutdown();
            Bus.Dispose();
        }
    }

    public sealed class SignalsSnapshot
    {
        public int RunStart;
        public int RtdsReady;
        public int EmsEnable;
        public int InitialSocTrigger;
        public int EssRunEnableSim;
        public int RtdsRunning;
        public int InitialSocAck;
        public int RtdsStatusCode;
        public double EssPower;
        public double EssSoc;
        public double PccImport;
        public double PccExport;
    }
}
