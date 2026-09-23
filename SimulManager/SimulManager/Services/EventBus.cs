// 이벤트 버스 — 모듈 간 결합을 이벤트로 분리 (backend/app/events.py 포팅).
//
// Python asyncio의 단일 스레드 의미론을 재현하기 위해 전용 '엔진 스레드' 1개가
// 모든 핸들러를 순차 실행한다. Modbus 소켓 스레드·시계 스레드·UI 스레드는
// Publish/Post로 작업을 큐에 넣고, 엔진 상태 변이는 항상 이 스레드에서만 일어난다.
//
// 발행 이벤트:
//   clock.minute / clock.interval / clock.day / clock.state / clock.complete
//   modbus.write / watch.alive / watch.lost / watch.update
//   sequence.event / run.state / historian.measurement / data.loaded
using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Diagnostics;
using System.Threading;

namespace SimulManager.Services
{
    public delegate void BusHandler(string topic, object payload);

    public sealed class EventBus : IDisposable
    {
        private readonly Dictionary<string, List<BusHandler>> _handlers =
            new Dictionary<string, List<BusHandler>>();
        private readonly BlockingCollection<Action> _queue = new BlockingCollection<Action>();
        private readonly Thread _thread;
        private volatile bool _disposed;

        public EventBus()
        {
            _thread = new Thread(Loop) { IsBackground = true, Name = "engine-bus" };
            _thread.Start();
        }

        public bool IsEngineThread => Thread.CurrentThread == _thread;

        /// <summary>topic 구독. '*'는 전체 이벤트 구독.</summary>
        public void Subscribe(string topic, BusHandler handler)
        {
            lock (_handlers)
            {
                List<BusHandler> list;
                if (!_handlers.TryGetValue(topic, out list))
                    _handlers[topic] = list = new List<BusHandler>();
                list.Add(handler);
            }
        }

        public void Unsubscribe(string topic, BusHandler handler)
        {
            lock (_handlers)
            {
                List<BusHandler> list;
                if (_handlers.TryGetValue(topic, out list)) list.Remove(handler);
            }
        }

        /// <summary>엔진 스레드에서 핸들러들을 순차 호출 (비동기 enqueue).</summary>
        public void Publish(string topic, object payload)
        {
            if (_disposed) return;
            if (IsEngineThread) { Dispatch(topic, payload); return; }
            try { _queue.Add(() => Dispatch(topic, payload)); }
            catch (InvalidOperationException) { /* 종료 중 */ }
        }

        /// <summary>임의 작업을 엔진 스레드에서 실행 (비동기).</summary>
        public void Post(Action action)
        {
            if (_disposed) return;
            if (IsEngineThread) { Run(action); return; }
            try { _queue.Add(action); }
            catch (InvalidOperationException) { }
        }

        /// <summary>엔진 스레드에서 실행하고 완료까지 대기. 예외는 호출자에게 전파.</summary>
        public void Invoke(Action action)
        {
            if (IsEngineThread) { action(); return; }
            if (_disposed) throw new ObjectDisposedException(nameof(EventBus));
            Exception captured = null;
            using (var done = new ManualResetEventSlim(false))
            {
                _queue.Add(() =>
                {
                    try { action(); }
                    catch (Exception ex) { captured = ex; }
                    finally { done.Set(); }
                });
                done.Wait();
            }
            if (captured != null) throw captured;
        }

        public T Invoke<T>(Func<T> func)
        {
            T result = default(T);
            Invoke(() => { result = func(); });
            return result;
        }

        private void Loop()
        {
            foreach (var action in _queue.GetConsumingEnumerable())
                Run(action);
        }

        private static void Run(Action action)
        {
            try { action(); }
            catch (Exception ex) { Trace.TraceError("엔진 작업 오류: " + ex); }
        }

        private void Dispatch(string topic, object payload)
        {
            List<BusHandler> exact = null, all = null;
            lock (_handlers)
            {
                List<BusHandler> list;
                if (_handlers.TryGetValue(topic, out list)) exact = new List<BusHandler>(list);
                if (_handlers.TryGetValue("*", out list)) all = new List<BusHandler>(list);
            }
            if (exact != null)
                foreach (var h in exact) Safe(h, topic, payload);
            if (all != null)
                foreach (var h in all) Safe(h, topic, payload);
        }

        private static void Safe(BusHandler h, string topic, object payload)
        {
            try { h(topic, payload); }
            catch (Exception ex) { Trace.TraceError("이벤트 핸들러 오류: topic=" + topic + " — " + ex); }
        }

        public void Dispose()
        {
            if (_disposed) return;
            _disposed = true;
            _queue.CompleteAdding();
        }
    }
}
