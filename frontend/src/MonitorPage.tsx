import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import type { WsMessage } from './types'

interface RunInfo {
  run_id: number
  label: string | null
  scenario_id: number
  status: string
  started_virtual: string | null
  ended_virtual: string | null
  initial_soc: number | null
  final_soc: number | null
  disconnect_intervals: number
  points: number
}

interface MeasurementRow {
  virtual_time: string
  ess_power_kw: number | null
  ess_soc: number | null
  pcc_import_kw: number | null
  pcc_export_kw: number | null
}

const MAX_LIVE_POINTS = 4000
const RUN_STATUS_LABEL: Record<string, string> = {
  running: '운전중', completed: '완료', stopped: '수동 정지', invalid: '무효',
}

/** Run 시계열 모니터링 — Historian 이력 + 실시간(WS) 계측 차트 (Phase 5) */
export default function MonitorPage() {
  const [runs, setRuns] = useState<RunInfo[]>([])
  const [selected, setSelected] = useState<number | null>(null)
  const [rows, setRows] = useState<MeasurementRow[]>([])
  const [meta, setMeta] = useState<{ total: number; stride: number } | null>(null)
  const selectedRef = useRef<number | null>(null)
  selectedRef.current = selected

  const refreshRuns = useCallback(async () => {
    const list: RunInfo[] = await (await fetch('/api/history/runs')).json()
    setRuns(list)
    setSelected((prev) => prev ?? list[0]?.run_id ?? null)
    return list
  }, [])

  useEffect(() => { refreshRuns() }, [refreshRuns])

  const fetchSeries = useCallback(async (runId: number) => {
    const res = await fetch(`/api/history/measurements?run_id=${runId}&max_points=1500`)
    if (!res.ok) return
    const data = await res.json()
    setRows(data.rows)
    setMeta({ total: data.total, stride: data.stride })
  }, [])

  useEffect(() => {
    if (selected != null) fetchSeries(selected)
  }, [selected, fetchSeries])

  // 실시간: 선택 Run의 신규 계측을 WS로 append, Run 상태 변화 시 목록 갱신
  useEffect(() => {
    const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`)
    ws.onmessage = (e) => {
      const msg: WsMessage = JSON.parse(e.data)
      if (msg.topic === 'historian.measurement') {
        const p = msg.payload as unknown as MeasurementRow & { run_id: number }
        if (p.run_id === selectedRef.current) {
          setRows((prev) => {
            const next = [...prev, p]
            return next.length > MAX_LIVE_POINTS ? next.slice(-MAX_LIVE_POINTS) : next
          })
        }
      } else if (msg.topic === 'run.state') {
        refreshRuns()
      }
    }
    return () => ws.close()
  }, [refreshRuns])

  const chartData = useMemo(() => rows.map((r) => ({
    ts: r.virtual_time.replace('T', ' ').slice(5, 16),
    ess: r.ess_power_kw, soc: r.ess_soc,
    imp: r.pcc_import_kw, exp: r.pcc_export_kw,
  })), [rows])

  const run = runs.find((r) => r.run_id === selected)

  return (
    <main className="shell wide">
      <header className="topbar">
        <h1 className="page-title">모니터링</h1>
        <div className="badges">
          <select
            className="scenario-select" value={selected ?? ''}
            onChange={(e) => setSelected(Number(e.target.value))}
          >
            {runs.length === 0 && <option value="">Run 없음</option>}
            {runs.map((r) => (
              <option key={r.run_id} value={r.run_id}>
                #{r.run_id} Run {r.label ?? r.scenario_id} · {RUN_STATUS_LABEL[r.status] ?? r.status}
                {r.started_virtual ? ` · ${r.started_virtual.slice(0, 10)}` : ''}
              </option>
            ))}
          </select>
        </div>
      </header>

      {run && (
        <section className="run-summary">
          <span className={`badge ${run.status === 'running' ? 'state-running' : 'state-idle'}`}>
            {RUN_STATUS_LABEL[run.status] ?? run.status}
          </span>
          <span className="muted">
            가상 {run.started_virtual?.replace('T', ' ') ?? '—'} ~ {run.ended_virtual?.replace('T', ' ') ?? '진행 중'}
            {' · '}계측 {run.points.toLocaleString()}점
            {' · '}SOC {run.initial_soc ?? '—'}% → {run.final_soc ?? '—'}%
            {' · '}단절 {run.disconnect_intervals}구간
            {meta && meta.stride > 1 ? ` · 차트 ${rows.length.toLocaleString()}점 표시(1/${meta.stride} 다운샘플)` : ''}
          </span>
        </section>
      )}

      <section className="card">
        <div className="label seq-title">전력 [kW] — ESS 충방전(+방전/−충전) · PCC 수전/역송</div>
        {chartData.length === 0 ? (
          <p className="muted empty">계측 데이터가 없습니다 — Run을 시작하면 RTDS 1분 계측이 적재됩니다.</p>
        ) : (
          <ResponsiveContainer width="100%" height={280}>
            <LineChart data={chartData} margin={{ top: 8, right: 16, bottom: 4, left: 0 }} syncId="mon">
              <CartesianGrid stroke="#edf0f8" />
              <XAxis dataKey="ts" tick={{ fontSize: 11, fill: '#8a94ad' }} minTickGap={48} />
              <YAxis tick={{ fontSize: 11, fill: '#8a94ad' }} width={52} />
              <Tooltip />
              <Legend />
              <Line type="monotone" dataKey="ess" name="ESS 전력" stroke="#6366f1" dot={false} strokeWidth={1.6} />
              <Line type="monotone" dataKey="imp" name="PCC 수전" stroke="#f26d6d" dot={false} strokeWidth={1.6} />
              <Line type="monotone" dataKey="exp" name="PCC 역송" stroke="#2ec5b6" dot={false} strokeWidth={1.6} />
            </LineChart>
          </ResponsiveContainer>
        )}
      </section>

      <section className="card">
        <div className="label seq-title">SOC [%]</div>
        {chartData.length === 0 ? (
          <p className="muted empty">데이터 없음</p>
        ) : (
          <ResponsiveContainer width="100%" height={200}>
            <LineChart data={chartData} margin={{ top: 8, right: 16, bottom: 4, left: 0 }} syncId="mon">
              <CartesianGrid stroke="#edf0f8" />
              <XAxis dataKey="ts" tick={{ fontSize: 11, fill: '#8a94ad' }} minTickGap={48} />
              <YAxis domain={[0, 100]} tick={{ fontSize: 11, fill: '#8a94ad' }} width={52} />
              <Tooltip />
              <Line type="monotone" dataKey="soc" name="SOC" stroke="#38bdf8" dot={false} strokeWidth={1.8} />
            </LineChart>
          </ResponsiveContainer>
        )}
      </section>
    </main>
  )
}
