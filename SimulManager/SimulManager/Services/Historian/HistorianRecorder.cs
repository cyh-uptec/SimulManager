// Historian 적재 레코더 — modbus.write 이벤트를 구독해 Run 시계열을 적재
// (backend/app/historian/recorder.py 포팅).
//
// - RTDS 1분 계측: ③HR 400002~400005 일괄 쓰기(FC16, Registers 포함) → measurements
// - EMS 예측 블록: ①IR 300002~300018 미러 쓰기 → forecasts
//   ([1구간] h=1만 적재 — [2~4구간] 300007~300018은 예약(0 기록)이므로 무시, 설계서 v1.7)
// - 적재 시 'historian.measurement' 이벤트 발행 → 모니터링 차트 실시간 갱신
//
// Run 진행 중(run_id 존재)에만 적재한다 — Run 밖의 쓰기는 시퀀스 로그로만 남는다.
using System;
using System.Collections.Generic;
using System.Linq;
using SimulManager.Services.Modbus;
using SimulManager.Services.Protocol;

namespace SimulManager.Services.Historian
{
    public sealed class MeasurementEvent
    {
        public int RunId;
        public string VirtualTime;
        public MeasurementRow Row;
    }

    public sealed class HistorianRecorder
    {
        private readonly HistorianStore _store;
        private readonly EventBus _bus;
        private readonly Func<Tuple<int?, string>> _runContext; // (run_id, 가상시각 ISO) — 시퀀스 엔진 제공

        public HistorianRecorder(HistorianStore store, EventBus bus, Func<Tuple<int?, string>> runContext)
        {
            _store = store;
            _bus = bus;
            _runContext = runContext;
            bus.Subscribe("modbus.write", OnWrite);
        }

        private void OnWrite(string topic, object payload)
        {
            var evt = payload as ModbusWriteEvent;
            if (evt == null) return;
            var ctx = _runContext();
            if (!ctx.Item1.HasValue) return;
            int runId = ctx.Item1.Value;
            string virtualTime = ctx.Item2;

            // RTDS 1분 계측 블록 (③HR ess_power 시작 FC16 — Registers에 4항목)
            if (evt.Link == Link.Link3 && evt.Key == "ess_power" && evt.Registers != null)
            {
                var byKey = evt.Registers.ToDictionary(r => r.Key, r => r.Physical);
                double v;
                var row = new MeasurementRow
                {
                    EssPowerKw = byKey.TryGetValue("ess_power", out v) ? (double?)v : null,
                    EssSoc = byKey.TryGetValue("ess_soc", out v) ? (double?)v : null,
                    PccImportKw = byKey.TryGetValue("pcc_import", out v) ? (double?)v : null,
                    PccExportKw = byKey.TryGetValue("pcc_export", out v) ? (double?)v : null,
                };
                if (row.EssPowerKw.HasValue || row.EssSoc.HasValue ||
                    row.PccImportKw.HasValue || row.PccExportKw.HasValue)
                {
                    _store.AddMeasurement(runId, virtualTime, row);
                    _bus.Publish("historian.measurement", new MeasurementEvent
                    {
                        RunId = runId, VirtualTime = virtualTime, Row = row,
                    });
                }
            }
            // EMS 예측 블록 (①IR 미러 — 기준 구간 + [1구간] 4항목만 유효, [2~4구간]은 예약 0)
            else if (evt.Link == Link.Link1 && evt.Key == "forecast_base_interval" && evt.Registers != null)
            {
                var byKey = evt.Registers.ToDictionary(r => r.Key, r => r.Physical);
                double v;
                int baseInterval = (int)(byKey.TryGetValue("forecast_base_interval", out v)
                    ? v : (evt.Physical ?? 0));
                var row = new ForecastRow
                {
                    PvKw = byKey.TryGetValue("pv_forecast_1", out v) ? (double?)v : null,
                    LoadKw = byKey.TryGetValue("load_forecast_1", out v) ? (double?)v : null,
                    ScheduleKw = byKey.TryGetValue("ess_schedule_1", out v) ? (double?)v : null,
                    TargetSoc = byKey.TryGetValue("target_soc_1", out v) ? (double?)v : null,
                };
                if (row.PvKw.HasValue || row.LoadKw.HasValue ||
                    row.ScheduleKw.HasValue || row.TargetSoc.HasValue)
                    _store.AddForecastBlock(runId, virtualTime, baseInterval, row);
            }
        }
    }
}
