// 설정 — backend/app/config.py 포팅.
// 기본값은 코드에 두고, UI 설정 대시보드가 저장한 오버라이드를
// data/config/user_settings.json 에 병합 유지한다 (YAML → JSON 대체).
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Web.Script.Serialization;

namespace SimulManager.Services
{
    public sealed class NetworkConfig
    {
        public string Bind = "0.0.0.0";
        public string Mode = "per_link_port";  // single_port | per_link_port (OPEN_ISSUES #1)
        public int Port = 502;
        public int Link1Port = 5020;
        public int Link3Port = 5021;
        public string EmsIp = "127.0.0.1";
        public string RtdsIp = "127.0.0.1";
        public int UnitId = 1;
    }

    public sealed class SimulationConfig
    {
        public DateTime StartVirtualTime = new DateTime(2025, 7, 1, 0, 0, 0);
        public int Days = 30;
        public double AccelSecondsPer15Min = 10.0;   // 1~900
        public double InitialSoc = 50.0;
        public double SocMax = 90.0;
        public double SocMin = 10.0;
        public double ChargeLimitKw = 250.0;
        public double DischargeLimitKw = 250.0;

        /// <summary>가상 1분 = 실 (가속배율/15) 초.</summary>
        public double RealSecondsPerVirtualMinute => AccelSecondsPer15Min / 15.0;

        /// <summary>총 15분 구간 수 (30일 → 2,880구간).</summary>
        public int TotalIntervals => Days * 96;

        public SimulationConfig Clone() => (SimulationConfig)MemberwiseClone();

        public void Validate()
        {
            if (AccelSecondsPer15Min < 1.0 || AccelSecondsPer15Min > 900.0)
                throw new ArgumentException("시간 가속 배율은 1~900초 범위여야 합니다");
            if (Days < 1 || Days > 365)
                throw new ArgumentException("시뮬레이션 기간은 1~365일 범위여야 합니다");
            if (SocMin >= SocMax)
                throw new ArgumentException("SOC 하한은 상한보다 작아야 합니다");
            if (InitialSoc < SocMin || InitialSoc > SocMax)
                throw new ArgumentException("초기 SOC는 SOC 상하한 범위 안이어야 합니다");
        }
    }

    public sealed class TimingConfig
    {
        public int ResponseTimeoutMs = 1000;
        public int RetryCount = 3;
        public double HeartbeatPeriodS = 1.0;
        public double AliveTimeoutS = 3.0;
        public int DisconnectBudgetIntervals = 14;
    }

    public sealed class ScenarioConfig
    {
        public string Interpolation = "hold"; // hold | linear
    }

    public sealed class AppConfig
    {
        public NetworkConfig Network = new NetworkConfig();
        public SimulationConfig Simulation = new SimulationConfig();
        public TimingConfig Timing = new TimingConfig();
        public ScenarioConfig Scenario = new ScenarioConfig();

        /// <summary>데이터 루트 — 실행 파일 옆 data\ (uploads·historian·events 보관).</summary>
        public static string DataDir
        {
            get
            {
                var dir = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "data");
                Directory.CreateDirectory(dir);
                return dir;
            }
        }

        private static string UserSettingsPath => Path.Combine(DataDir, "config", "user_settings.json");

        /// <summary>기본값 + user_settings.json 병합 로드.</summary>
        public static AppConfig Load()
        {
            var config = new AppConfig();
            try
            {
                if (File.Exists(UserSettingsPath))
                {
                    var raw = new JavaScriptSerializer()
                        .Deserialize<Dictionary<string, object>>(File.ReadAllText(UserSettingsPath));
                    object simSection;
                    if (raw != null && raw.TryGetValue("simulation", out simSection))
                        ApplySimulation(config.Simulation, simSection as Dictionary<string, object>);
                    object netSection;
                    if (raw != null && raw.TryGetValue("network", out netSection))
                        ApplyNetwork(config.Network, netSection as Dictionary<string, object>);
                }
            }
            catch { /* 손상된 설정 파일 — 기본값으로 계속 */ }
            return config;
        }

        /// <summary>시뮬레이션 사용자 설정 저장 (UI 설정 대시보드 → user_settings.json).</summary>
        public static void SaveSimulation(SimulationConfig sim)
        {
            var ser = new JavaScriptSerializer();
            Dictionary<string, object> root = null;
            try
            {
                if (File.Exists(UserSettingsPath))
                    root = ser.Deserialize<Dictionary<string, object>>(File.ReadAllText(UserSettingsPath));
            }
            catch { }
            if (root == null) root = new Dictionary<string, object>();
            root["simulation"] = new Dictionary<string, object>
            {
                { "start_virtual_time", sim.StartVirtualTime.ToString("yyyy-MM-dd'T'HH:mm") },
                { "days", sim.Days },
                { "accel_seconds_per_15min", sim.AccelSecondsPer15Min },
                { "initial_soc", sim.InitialSoc },
                { "soc_max", sim.SocMax },
                { "soc_min", sim.SocMin },
                { "charge_limit_kw", sim.ChargeLimitKw },
                { "discharge_limit_kw", sim.DischargeLimitKw },
            };
            Directory.CreateDirectory(Path.GetDirectoryName(UserSettingsPath));
            File.WriteAllText(UserSettingsPath, ser.Serialize(root));
        }

        private static void ApplySimulation(SimulationConfig sim, Dictionary<string, object> d)
        {
            if (d == null) return;
            object v;
            if (d.TryGetValue("start_virtual_time", out v) && v is string)
            {
                DateTime dt;
                if (DateTime.TryParse((string)v, CultureInfo.InvariantCulture, DateTimeStyles.None, out dt))
                    sim.StartVirtualTime = dt;
            }
            if (d.TryGetValue("days", out v)) sim.Days = Convert.ToInt32(v, CultureInfo.InvariantCulture);
            if (d.TryGetValue("accel_seconds_per_15min", out v)) sim.AccelSecondsPer15Min = Convert.ToDouble(v, CultureInfo.InvariantCulture);
            if (d.TryGetValue("initial_soc", out v)) sim.InitialSoc = Convert.ToDouble(v, CultureInfo.InvariantCulture);
            if (d.TryGetValue("soc_max", out v)) sim.SocMax = Convert.ToDouble(v, CultureInfo.InvariantCulture);
            if (d.TryGetValue("soc_min", out v)) sim.SocMin = Convert.ToDouble(v, CultureInfo.InvariantCulture);
            if (d.TryGetValue("charge_limit_kw", out v)) sim.ChargeLimitKw = Convert.ToDouble(v, CultureInfo.InvariantCulture);
            if (d.TryGetValue("discharge_limit_kw", out v)) sim.DischargeLimitKw = Convert.ToDouble(v, CultureInfo.InvariantCulture);
        }

        private static void ApplyNetwork(NetworkConfig net, Dictionary<string, object> d)
        {
            if (d == null) return;
            object v;
            if (d.TryGetValue("bind", out v) && v is string) net.Bind = (string)v;
            if (d.TryGetValue("mode", out v) && v is string) net.Mode = (string)v;
            if (d.TryGetValue("port", out v)) net.Port = Convert.ToInt32(v, CultureInfo.InvariantCulture);
            if (d.TryGetValue("link1_port", out v)) net.Link1Port = Convert.ToInt32(v, CultureInfo.InvariantCulture);
            if (d.TryGetValue("link3_port", out v)) net.Link3Port = Convert.ToInt32(v, CultureInfo.InvariantCulture);
            if (d.TryGetValue("ems_ip", out v) && v is string) net.EmsIp = (string)v;
            if (d.TryGetValue("rtds_ip", out v) && v is string) net.RtdsIp = (string)v;
        }
    }
}
