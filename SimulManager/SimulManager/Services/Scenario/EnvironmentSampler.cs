// 환경 샘플러 — 실측 데이터를 가상시각 기준 1분 값으로 변환 (배포 원천)
// (backend/app/scenario/mapper.py 포팅).
//
// 시각 매핑 (오프셋 방식): 데이터의 달력 날짜와 무관하게, Run 시작으로부터의
// 가상 경과시간을 각 데이터셋 시작점에 더해 조회한다.
//   데이터 시각 = 데이터 시작 + (가상시각 − Run 시작 가상시각)
//
// 변환 규칙:
// - 보간: hold(ZOH, 기본) | linear — 두 Run 동일 적용 (설계서 §8)
// - 일사: 실측 MJ/m²(1시간 적산)를 그대로 배포 (프로토콜맵 v1.0 — 환산 불필요)
// - 야간 일사 결측(NaN) → 0. 기타 컬럼 NaN → 직전 유효값 유지
// - 적설량·기상 상태코드: 실측이 아닌 운영자 이벤트(WeatherEventStore)에서 산출
// - 데이터 범위 초과 시 마지막 값 유지(경고 1회)
using System;
using System.Collections.Generic;
using System.Diagnostics;
using SimulManager.Services.Protocol;

namespace SimulManager.Services.Scenario
{
    /// <summary>가상 1분 배포값 (물리 단위 — 레지스터 인코딩은 protocol Codec이 수행).</summary>
    public sealed class EnvSample
    {
        public double LoadKw;         // ③IR 300008 부하 프로파일 기준값
        public double IrradianceMj;   // ①HR 400008 / ③IR 300004 일사량 [MJ/m², 1시간 적산]
        public double TempC;          // ①HR 400009 / ③IR 300005 외기온도 [℃]
        public double SnowCm;         // ①HR 400010 / ③IR 300006 적설량 [cm] — 이벤트 산출
        public int WeatherCode;       // ①HR 400011 / ③IR 300007 — 0정상/1폭염/2폭설
    }

    public sealed class EnvironmentSampler
    {
        // 부하 상한 — ③IR 300008 UINT16 ×100 인코딩 한계 (655.35 kW). 초과 시 클램프+경고
        public static readonly double LoadKwMax =
            Codec.UInt16Max / Links.Link3Map["load_profile_ref"].Scale;

        private readonly ScenarioStore _store;
        private readonly WeatherEventStore _events;
        private readonly string _interpolation;
        private readonly HashSet<string> _warnedOverrun = new HashSet<string>();
        private readonly Dictionary<string, double> _lastValid = new Dictionary<string, double>();

        public EnvironmentSampler(ScenarioStore store, WeatherEventStore events, string interpolation = "hold")
        {
            _store = store;
            _events = events;
            _interpolation = interpolation;
        }

        public bool Ready() => _store.HasFrame("load") && _store.HasFrame("weather");

        public EnvSample Sample(DateTime virtualTime, DateTime runStartVirtual)
        {
            var elapsed = virtualTime - runStartVirtual;
            double load = ValueAt("load", "load_kw", elapsed);
            if (load > LoadKwMax || load < 0)
            {
                // 배포 중단(원자성 훼손) 대신 레지스터 한계로 클램프 (encode 예외 방지)
                if (!_warnedOverrun.Contains("load_clamp"))
                {
                    Trace.TraceWarning("부하 " + load + "kW가 레지스터 한계(0~" + LoadKwMax + ")를 벗어나 클램프");
                    _warnedOverrun.Add("load_clamp");
                }
                load = Math.Min(Math.Max(load, 0.0), LoadKwMax);
            }
            double temp = ValueAt("weather", "temp", elapsed);
            double irrMj = ValueAt("weather", "irradiance", elapsed, nanAsZero: true);

            var evt = _events.ActiveAt(virtualTime);
            int weatherCode = evt != null ? WeatherEventStore.WeatherCode[evt.Type] : 0;
            double snowCm = (evt != null && evt.Type == "snow") ? evt.SnowCm : 0.0;

            return new EnvSample
            {
                LoadKw = Math.Round(load, 2),
                IrradianceMj = Math.Round(irrMj, 2),  // 레지스터 ×1 인코딩 시 정수로 반올림됨
                TempC = Math.Round(temp, 2),
                SnowCm = snowCm,
                WeatherCode = weatherCode,
            };
        }

        // ── 내부 ────────────────────────────────────────────────
        private double ValueAt(string kind, string column, TimeSpan elapsed, bool nanAsZero = false)
        {
            var df = _store.GetFrame(kind);
            var tsTarget = df.Timestamps[0] + elapsed;
            var values = df.Values[column];

            // searchsorted(side='right') - 1
            int pos = UpperBound(df.Timestamps, tsTarget) - 1;
            if (pos < 0) pos = 0;

            double? raw;
            if (pos >= df.RowCount - 1)
            {
                if (!_warnedOverrun.Contains(kind) && tsTarget > df.Timestamps[df.RowCount - 1])
                {
                    Trace.TraceWarning(kind + " 데이터 범위 초과 — 마지막 값 유지 (" + tsTarget + ")");
                    _warnedOverrun.Add(kind);
                }
                raw = values[df.RowCount - 1];
            }
            else if (_interpolation == "linear")
            {
                var t0 = df.Timestamps[pos];
                var t1 = df.Timestamps[pos + 1];
                var v0 = values[pos];
                var v1 = values[pos + 1];
                if (!v0.HasValue || !v1.HasValue)
                    raw = v0;
                else
                {
                    double frac = (tsTarget - t0).TotalSeconds / (t1 - t0).TotalSeconds;
                    raw = v0.Value + (v1.Value - v0.Value) * frac;
                }
            }
            else // hold (ZOH)
            {
                raw = values[pos];
            }

            string key = kind + "." + column;
            if (!raw.HasValue)
            {
                if (nanAsZero) return 0.0;
                double last;
                return _lastValid.TryGetValue(key, out last) ? last : 0.0;
            }
            _lastValid[key] = raw.Value;
            return raw.Value;
        }

        /// <summary>timestamps에서 target보다 큰 첫 위치 (np.searchsorted side='right').</summary>
        private static int UpperBound(List<DateTime> list, DateTime target)
        {
            int lo = 0, hi = list.Count;
            while (lo < hi)
            {
                int mid = (lo + hi) / 2;
                if (list[mid] <= target) lo = mid + 1;
                else hi = mid;
            }
            return lo;
        }
    }
}
