import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'

type Kind = 'load' | 'weather'

interface KindMeta {
  filename: string
  rows: number
  start: string
  end: string
  resolution_min: number | null
  columns: string[]
  loaded_at: string
}

interface DataStatus {
  load: KindMeta | null
  weather: KindMeta | null
}

interface SeriesResponse {
  timestamps: string[]
  series: Record<string, (number | null)[]>
  labels: Record<string, string>
  points: number
  total: number
}

interface RowsResponse {
  total: number
  offset: number
  rows: Record<string, number | string | null>[]
}

const COLUMN_LABELS: Record<string, string> = {
  load_kw: '부하 (kW)',
  temp: '기온 (℃)',
  wind_speed: '풍속 (m/s)',
  humidity: '습도 (%)',
  sunshine: '일조 (hr)',
  irradiance: '일사 (MJ/m²)',
  cloud_total: '전운량',
  cloud_midlow: '중하층운량',
}

const SERIES_COLORS = ['#6366f1', '#38bdf8', '#2ec5b6', '#ffb020', '#f26d6d', '#8b5cf6', '#22c55e', '#64748b']

const PAGE_SIZE = 50

interface WeatherEvent {
  id: string
  type: 'heatwave' | 'snow'
  start: string
  end: string
  snow_cm: number
}

export default function DataPage() {
  const [status, setStatus] = useState<DataStatus | null>(null)
  const [kind, setKind] = useState<Kind>('load')
  const [checked, setChecked] = useState<Record<string, boolean>>({})
  const [series, setSeries] = useState<SeriesResponse | null>(null)
  const [rows, setRows] = useState<RowsResponse | null>(null)
  const [offset, setOffset] = useState(0)
  const [sort, setSort] = useState<{ col: string; order: 'asc' | 'desc' } | null>(null)
  const [uploading, setUploading] = useState<Kind | null>(null)
  const [error, setError] = useState<string | null>(null)

  const meta = status?.[kind] ?? null
  const availableColumns = useMemo(() => meta?.columns ?? [], [meta])
  const selectedColumns = useMemo(
    () => availableColumns.filter((c) => checked[c] !== false),
    [availableColumns, checked],
  )

  const refreshStatus = useCallback(async () => {
    const s: DataStatus = await (await fetch('/api/data/status')).json()
    setStatus(s)
    return s
  }, [])

  useEffect(() => { refreshStatus() }, [refreshStatus])

  // 종류 전환 시 초기화
  useEffect(() => {
    setChecked({})
    setOffset(0)
    setSort(null)
    setSeries(null)
    setRows(null)
  }, [kind])

  // 차트 시리즈
  useEffect(() => {
    if (!meta || selectedColumns.length === 0) { setSeries(null); return }
    const q = new URLSearchParams({ columns: selectedColumns.join(','), max_points: '1200' })
    fetch(`/api/data/${kind}/series?${q}`)
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then(setSeries)
      .catch(() => setSeries(null))
  }, [kind, meta, selectedColumns])

  // 리스트
  useEffect(() => {
    if (!meta) { setRows(null); return }
    const q = new URLSearchParams({ offset: String(offset), limit: String(PAGE_SIZE) })
    if (sort) { q.set('sort', sort.col); q.set('order', sort.order) }
    fetch(`/api/data/${kind}/rows?${q}`)
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then(setRows)
      .catch(() => setRows(null))
  }, [kind, meta, offset, sort])

  const upload = async (target: Kind, file: File) => {
    setUploading(target)
    setError(null)
    try {
      const body = new FormData()
      body.append('file', file)
      const res = await fetch(`/api/data/upload/${target}`, { method: 'POST', body })
      const payload = await res.json()
      if (!res.ok) {
        setError(typeof payload.detail === 'string' ? payload.detail : '업로드 실패')
        return
      }
      await refreshStatus()
      if (target === kind) { setOffset(0); setSort(null) }
    } catch {
      setError('서버 요청에 실패했습니다')
    } finally {
      setUploading(null)
    }
  }

  const toggleSort = (col: string) => {
    setOffset(0)
    setSort((prev) =>
      prev?.col === col
        ? prev.order === 'asc' ? { col, order: 'desc' } : null
        : { col, order: 'asc' },
    )
  }

  const chartData = useMemo(() => {
    if (!series) return []
    return series.timestamps.map((ts, i) => {
      const point: Record<string, string | number | null> = { ts: fmtTs(ts) }
      for (const col of Object.keys(series.series)) point[col] = series.series[col][i]
      return point
    })
  }, [series])

  return (
    <main className="shell wide">
      <header className="topbar">
        <h1 className="page-title">데이터</h1>
        {error && <span className="error">{error}</span>}
      </header>

      <section className="upload-grid">
        <UploadCard
          title="부하 데이터" hint="Timestamp, Load_kW (월 단위)"
          meta={status?.load ?? null} busy={uploading === 'load'}
          onFile={(f) => upload('load', f)}
        />
        <UploadCard
          title="기상 데이터" hint="일시, 기온, 풍속, 습도, 일조, 일사, 전운량, 중하층운량"
          meta={status?.weather ?? null} busy={uploading === 'weather'}
          onFile={(f) => upload('weather', f)}
        />
      </section>

      <section className="card">
        <div className="data-toolbar">
          <div className="segmented">
            <button className={kind === 'load' ? 'on' : ''} onClick={() => setKind('load')}>부하</button>
            <button className={kind === 'weather' ? 'on' : ''} onClick={() => setKind('weather')}>기상</button>
          </div>
          <div className="checks">
            {availableColumns.map((col) => (
              <label key={col} className="check">
                <input
                  type="checkbox"
                  checked={checked[col] !== false}
                  onChange={(e) => setChecked((prev) => ({ ...prev, [col]: e.target.checked }))}
                />
                {COLUMN_LABELS[col] ?? col}
              </label>
            ))}
          </div>
        </div>

        {!meta ? (
          <p className="muted empty">데이터가 로드되지 않았습니다 — 위에서 파일을 업로드하세요.</p>
        ) : selectedColumns.length === 0 ? (
          <p className="muted empty">표시할 항목을 체크하세요.</p>
        ) : (
          <div className="chart-box">
            <ResponsiveContainer width="100%" height={300}>
              <LineChart data={chartData} margin={{ top: 8, right: 16, bottom: 4, left: 0 }}>
                <CartesianGrid stroke="#edf0f8" />
                <XAxis dataKey="ts" tick={{ fontSize: 11, fill: '#8a94ad' }} minTickGap={48} />
                <YAxis tick={{ fontSize: 11, fill: '#8a94ad' }} width={56} />
                <Tooltip />
                <Legend />
                {selectedColumns.map((col, i) => (
                  <Line
                    key={col} type="monotone" dataKey={col}
                    name={COLUMN_LABELS[col] ?? col}
                    stroke={SERIES_COLORS[i % SERIES_COLORS.length]}
                    dot={false} strokeWidth={1.6} connectNulls
                  />
                ))}
              </LineChart>
            </ResponsiveContainer>
            {series && (
              <p className="muted chart-note">
                전체 {series.total.toLocaleString()}점 중 {series.points.toLocaleString()}점 표시 (다운샘플)
              </p>
            )}
          </div>
        )}
      </section>

      <WeatherEventsCard />

      {meta && rows && (
        <section className="card">
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <SortableTh col="timestamp" label="일시" sort={sort} onSort={toggleSort} />
                  {selectedColumns.map((col) => (
                    <SortableTh key={col} col={col} label={COLUMN_LABELS[col] ?? col}
                      sort={sort} onSort={toggleSort} />
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.rows.map((row, i) => (
                  <tr key={i}>
                    <td>{fmtTs(String(row.timestamp))}</td>
                    {selectedColumns.map((col) => (
                      <td key={col}>{row[col] == null ? '—' : Number(row[col]).toLocaleString()}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="pager">
            <button className="btn" disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>← 이전</button>
            <span className="muted">
              {rows.total === 0 ? 0 : offset + 1}–{Math.min(offset + PAGE_SIZE, rows.total)} / {rows.total.toLocaleString()}행
            </span>
            <button className="btn" disabled={offset + PAGE_SIZE >= rows.total}
              onClick={() => setOffset(offset + PAGE_SIZE)}>다음 →</button>
          </div>
        </section>
      )}
    </main>
  )
}

function fmtTs(iso: string): string {
  return iso.replace('T', ' ').slice(5, 16)  // MM-DD HH:mm
}

function SortableTh({ col, label, sort, onSort }: {
  col: string; label: string
  sort: { col: string; order: 'asc' | 'desc' } | null
  onSort: (col: string) => void
}) {
  const mark = sort?.col === col ? (sort.order === 'asc' ? ' ▲' : ' ▼') : ''
  return <th onClick={() => onSort(col)} className="sortable">{label}{mark}</th>
}

function UploadCard({ title, hint, meta, busy, onFile }: {
  title: string; hint: string; meta: KindMeta | null; busy: boolean
  onFile: (f: File) => void
}) {
  const inputRef = useRef<HTMLInputElement>(null)
  return (
    <div className="card upload-card">
      <div className="node-head">
        <span className="node-title">{title}</span>
        <button className="btn" disabled={busy} onClick={() => inputRef.current?.click()}>
          {busy ? '업로드 중…' : meta ? '다시 로드' : '파일 로드'}
        </button>
        <input
          ref={inputRef} type="file" accept=".csv,.xlsx,.xls" hidden
          onChange={(e) => {
            const f = e.target.files?.[0]
            if (f) onFile(f)
            e.target.value = ''
          }}
        />
      </div>
      {meta ? (
        <div className="upload-meta">
          <div><span className="label">파일</span> {meta.filename}</div>
          <div><span className="label">기간</span> {fmtRange(meta.start, meta.end)}</div>
          <div>
            <span className="label">행수</span> {meta.rows.toLocaleString()}행
            {meta.resolution_min ? ` · ${meta.resolution_min}분 해상도` : ''}
          </div>
        </div>
      ) : (
        <p className="muted">{hint}</p>
      )}
    </div>
  )
}

function fmtRange(start: string, end: string): string {
  return `${start.replace('T', ' ').slice(0, 16)} ~ ${end.replace('T', ' ').slice(0, 16)}`
}

function WeatherEventsCard() {
  const [events, setEvents] = useState<WeatherEvent[]>([])
  const [type, setType] = useState<'heatwave' | 'snow'>('heatwave')
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [snowCm, setSnowCm] = useState(5)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(() => {
    fetch('/api/events').then((r) => r.json()).then(setEvents).catch(() => {})
  }, [])

  useEffect(() => { refresh() }, [refresh])

  const add = async () => {
    setError(null)
    if (!start || !end) { setError('시작·종료 가상시각을 입력하세요'); return }
    const res = await fetch('/api/events', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type, start, end, snow_cm: type === 'snow' ? snowCm : 0 }),
    })
    const body = await res.json()
    if (!res.ok) { setError(typeof body.detail === 'string' ? body.detail : '추가 실패'); return }
    setStart(''); setEnd('')
    refresh()
  }

  const remove = async (id: string) => {
    await fetch(`/api/events/${id}`, { method: 'DELETE' })
    refresh()
  }

  return (
    <section className="card">
      <div className="node-head">
        <span className="node-title">기상 이벤트 (폭염·폭설)</span>
        <span className="muted">운영자 임의 입력 — 가상 기간 지정, 양 Run 동일 적용</span>
      </div>

      <div className="event-form">
        <select value={type} onChange={(e) => setType(e.target.value as 'heatwave' | 'snow')}>
          <option value="heatwave">폭염 (코드 1)</option>
          <option value="snow">폭설 (코드 2)</option>
        </select>
        <input type="datetime-local" value={start} onChange={(e) => setStart(e.target.value)}
          title="시작 가상시각" />
        <span className="muted">~</span>
        <input type="datetime-local" value={end} onChange={(e) => setEnd(e.target.value)}
          title="종료 가상시각" />
        {type === 'snow' && (
          <label className="snow-input">
            적설 <input type="number" min={0} max={99} step={0.5} value={snowCm}
              onChange={(e) => setSnowCm(Number(e.target.value))} /> cm
          </label>
        )}
        <button className="btn primary" onClick={add}>+ 추가</button>
        {error && <span className="error">{error}</span>}
      </div>

      {events.length === 0 ? (
        <p className="muted">등록된 이벤트가 없습니다 — 기본은 정상(코드 0)입니다.</p>
      ) : (
        <ul className="event-list">
          {events.map((ev) => (
            <li key={ev.id}>
              <span className={`badge event-${ev.type}`}>
                {ev.type === 'heatwave' ? '폭염' : '폭설'}
              </span>
              <span className="seq-time">
                {fmtRange(ev.start, ev.end)}
                {ev.type === 'snow' ? ` · 적설 ${ev.snow_cm}cm` : ''}
              </span>
              <button className="btn del" onClick={() => remove(ev.id)}>삭제</button>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
