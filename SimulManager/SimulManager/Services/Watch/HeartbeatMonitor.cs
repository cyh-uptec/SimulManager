// Heartbeat 생존감시 — docs/PROTOCOL_RULES.md §6 (backend/app/watch/heartbeat.py 포팅).
//
// - EMS: ①IR 300000 (HR 미러 경유 기록) / RTDS: ③HR 400000 — 실 1 s 증가
// - 값 '변화'를 생존 신호로 판정, alive_timeout_s(기본 3 s) 미변화 시 단절(lost)
// - 이벤트: watch.alive / watch.lost (전이 시), watch.update (주기적 전체 상태)
// - 가상시계는 단절과 무관하게 계속 진행한다 (자유진행 원칙 — 여기서 시계 제어 금지)
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Threading;
using SimulManager.Services.Modbus;
using SimulManager.Services.Protocol;

namespace SimulManager.Services.Watch
{
    public enum NodeState
    {
        Unknown, // 기동 후 HB 미수신
        Alive,   // HB 정상 갱신 중
        Lost,    // 판정 시간 내 미갱신 → 단절
    }

    public sealed class NodeStatus
    {
        public NodeState State;
        public int? Heartbeat;
        public double? AgeS;

        public string StateName => State.ToString().ToLowerInvariant();
    }

    public sealed class WatchTransition
    {
        public string Node;      // ems | rtds
        public string Prev;
        public NodeStatus Status;
    }

    public sealed class HeartbeatMonitor
    {
        private sealed class NodeWatch
        {
            public readonly string Name;
            public NodeState State = NodeState.Unknown;
            public int? LastValue;
            public long? LastChangeTicks;   // Stopwatch ticks

            public NodeWatch(string name) { Name = name; }

            public bool OnWrite(int value)
            {
                if (value != LastValue)
                {
                    LastValue = value;
                    LastChangeTicks = Stopwatch.GetTimestamp();
                    return true;
                }
                return false;
            }

            public double? AgeS()
            {
                if (!LastChangeTicks.HasValue) return null;
                return (Stopwatch.GetTimestamp() - LastChangeTicks.Value) / (double)Stopwatch.Frequency;
            }

            public NodeStatus Status()
            {
                var age = AgeS();
                return new NodeStatus
                {
                    State = State,
                    Heartbeat = LastValue,
                    AgeS = age.HasValue ? (double?)Math.Round(age.Value, 2) : null,
                };
            }
        }

        private readonly EventBus _bus;
        private readonly double _timeout;
        private readonly double _checkPeriod;
        private readonly Dictionary<string, NodeWatch> _nodes;
        private Timer _timer;

        public HeartbeatMonitor(EventBus bus, TimingConfig timing)
        {
            _bus = bus;
            _timeout = timing.AliveTimeoutS;
            _checkPeriod = Math.Min(timing.HeartbeatPeriodS, timing.AliveTimeoutS / 3);
            _nodes = new Dictionary<string, NodeWatch>
            {
                { "ems", new NodeWatch("ems") },
                { "rtds", new NodeWatch("rtds") },
            };
            bus.Subscribe("modbus.write", OnModbusWrite);
        }

        public void Start()
        {
            var period = TimeSpan.FromSeconds(_checkPeriod);
            _timer = new Timer(_ => _bus.Post(CheckOnce), null, period, period);
        }

        public void Stop()
        {
            _timer?.Dispose();
            _timer = null;
        }

        public Dictionary<string, NodeStatus> Status()
        {
            var result = new Dictionary<string, NodeStatus>();
            foreach (var kv in _nodes) result[kv.Key] = kv.Value.Status();
            return result;
        }

        // ── 이벤트 처리 (엔진 스레드) ─────────────────────────────
        private void OnModbusWrite(string topic, object payload)
        {
            var evt = payload as ModbusWriteEvent;
            if (evt == null || evt.Key == null) return;
            string nodeName =
                evt.Link == Link.Link1 && evt.Key == "ems_heartbeat" ? "ems" :
                evt.Link == Link.Link3 && evt.Key == "rtds_heartbeat" ? "rtds" : null;
            if (nodeName == null) return;
            var watch = _nodes[nodeName];
            bool changed = watch.OnWrite(evt.Values[0]);
            if (changed && watch.State != NodeState.Alive)
                Transition(watch, NodeState.Alive);
        }

        private void CheckOnce()
        {
            foreach (var watch in _nodes.Values)
            {
                var age = watch.AgeS();
                if (watch.State == NodeState.Alive && age.HasValue && age.Value > _timeout)
                    Transition(watch, NodeState.Lost);
            }
            _bus.Publish("watch.update", Status());
        }

        private void Transition(NodeWatch watch, NodeState newState)
        {
            var old = watch.State;
            watch.State = newState;
            string topic = newState == NodeState.Alive ? "watch.alive" : "watch.lost";
            Trace.TraceInformation("노드 " + watch.Name + ": " + old + " → " + newState);
            _bus.Publish(topic, new WatchTransition
            {
                Node = watch.Name,
                Prev = old.ToString().ToLowerInvariant(),
                Status = watch.Status(),
            });
        }
    }
}
