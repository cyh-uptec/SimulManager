// 시험 모드 공용 — 레지스터 폴링 훅·수동 쓰기·토글 스위치 (EMS/RTDS 시험 페이지 공용)
import { useCallback, useEffect, useRef, useState } from 'react'
import type { ClockStatus, NodesStatus, NodeStatus, RunStatus } from './types'

export interface RegRow {
  link: number
  obj: 'coil' | 'di' | 'ir' | 'hr'
  ref: string
  key: string
  name: string
  unit: string
  raw: number
  value: number
}

export interface NetworkSettings {
  bind: string
  web_port: number
  mode: 'per_link_port' | 'single_port'
  port: number
  link1_port: number
  link3_port: number
  ems_ip: string
  rtds_ip: string
  unit_id: number
}

export interface TestStatus {
  clock: ClockStatus | null
  run: RunStatus | null
}

/** 링크 레지스터 1초 폴링 + /api/status 2초 폴링 + 가상 노드 상태 + 수동 쓰기. */
export function useTestPage(link: number) {
  const [regs, setRegs] = useState<Record<string, RegRow>>({})
  const [regList, setRegList] = useState<RegRow[]>([])   // Hex 모니터용 원본 순서
  const [status, setStatus] = useState<TestStatus>({ clock: null, run: null })
  const [net, setNet] = useState<NetworkSettings | null>(null)
  const [virtual, setVirtual] = useState<{ rtds: boolean; ems: boolean } | null>(null)
  const [nodes, setNodes] = useState<NodesStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const pendingRef = useRef<Set<string>>(new Set()) // 쓰기 직후 폴링 덮어쓰기 방지

  const refreshNet = useCallback(() => {
    fetch('/api/settings/network').then((r) => r.json()).then(setNet).catch(() => {})
  }, [])

  useEffect(() => {
    let alive = true
    const pollRegs = async () => {
      try {
        const res = await fetch(`/api/registers/${link}`)
        if (!alive || !res.ok) return
        const rows: RegRow[] = await res.json()
        setRegList(rows)
        setRegs((prev) => {
          const next: Record<string, RegRow> = {}
          for (const row of rows) {
            next[row.key] = pendingRef.current.has(row.key) ? (prev[row.key] ?? row) : row
          }
          return next
        })
      } catch { /* 서버 재기동 중 — 다음 폴링에서 재시도 */ }
      try {
        const res = await fetch('/api/nodes')
        if (alive && res.ok) setNodes(await res.json())
      } catch { /* ignore */ }
    }
    const pollStatus = async () => {
      try {
        const res = await fetch('/api/status')
        if (!alive || !res.ok) return
        const s = await res.json()
        setStatus({ clock: s, run: s.run ?? null })
      } catch { /* ignore */ }
      try {
        const res = await fetch('/api/test/virtual')
        if (alive && res.ok) setVirtual(await res.json())
      } catch { /* ignore */ }
    }
    pollRegs()
    pollStatus()
    refreshNet()
    const t1 = setInterval(pollRegs, 1000)
    const t2 = setInterval(pollStatus, 2000)
    return () => { alive = false; clearInterval(t1); clearInterval(t2) }
  }, [link, refreshNet])

  /** 가상 노드(정상 상대 노드 재현) 켜기/끄기. */
  const setVirtualNode = useCallback(async (node: 'rtds' | 'ems', enabled: boolean) => {
    setError(null)
    try {
      const res = await fetch(`/api/test/virtual/${node}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled }),
      })
      const body = await res.json()
      if (res.ok) setVirtual(body)
      else setError(typeof body.detail === 'string' ? body.detail : '가상 노드 제어 실패')
    } catch {
      setError('서버 요청에 실패했습니다')
    }
  }, [])

  /** 매니저 보유 레지스터 수동 쓰기 (물리값). */
  const write = useCallback(async (key: string, value: number) => {
    setError(null)
    pendingRef.current.add(key)
    // 낙관적 갱신 — 다음 폴링 전까지 UI 즉시 반영
    setRegs((prev) => (prev[key] ? { ...prev, [key]: { ...prev[key], value } } : prev))
    try {
      const res = await fetch('/api/test/write', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ link, key, value }),
      })
      const body = await res.json()
      if (!res.ok) {
        setError(typeof body.detail === 'string' ? body.detail : `쓰기 실패 (${res.status})`)
      }
    } catch {
      setError('서버 요청에 실패했습니다')
    } finally {
      setTimeout(() => pendingRef.current.delete(key), 1200)
    }
  }, [link])

  return { regs, regList, status, net, virtual, nodes, error, write, setVirtualNode, refreshNet }
}

/** 실장비 접속 상태 카드 — TCP 접속 / 폴링(요청 수신) / Heartbeat 규약 판정 3단 구분. */
export function DeviceStatusCard({ title, node, virtualOn }: {
  title: string             // 예: "EMS"
  node: NodeStatus | undefined
  virtualOn: boolean        // 해당 노드의 가상 재현이 켜져 있는지
}) {
  const tcpOn = (node?.connections ?? 0) > 0
  const reqAge = node?.request_age_s
  const polling = reqAge != null && reqAge < 5
  const hbState = node?.state ?? 'unknown'
  // 배지: 폴링 중 > 접속만 됨(폴링 없음) > 요청 끊김 > 미접속
  const badge = polling
    ? { cls: 'on', text: '● 접속 · 폴링 중' }
    : tcpOn
      ? { cls: 'stale', text: '접속됨 · 폴링 없음' }
      : reqAge != null
        ? { cls: 'stale', text: '연결 끊김' }
        : { cls: 'none', text: '미접속' }
  return (
    <section className={`card device-card dev-${badge.cls}`}>
      <div className="node-head">
        <span className="node-title">실장비 접속 상태 — {title}</span>
        <span className={`badge conn-${badge.cls}`}>{badge.text}</span>
      </div>
      <div className="rtds-values">
        <div className="meta">
          <div className="label">① TCP 접속 (세션 열림 여부)</div>
          <div className="value">
            {tcpOn
              ? `접속됨 (${node!.connections}) — ${(node!.peers ?? []).join(', ')}`
              : '미접속'}
          </div>
        </div>
        <div className="meta">
          <div className="label">② Modbus 폴링 (읽기 포함 · 가상 노드 제외)</div>
          <div className="value">
            {reqAge == null ? '요청 없음'
              : polling ? `수신 중 — 마지막 요청 ${reqAge}s 전`
              : `중단됨 — 마지막 요청 ${reqAge}s 전`}
          </div>
        </div>
        <div className="meta">
          <div className="label">③ Heartbeat 규약 판정 (실 1s 증가·3s 타임아웃)</div>
          <div className="value">
            {hbState === 'alive' ? '정상' : hbState === 'lost' ? '단절' : '미수신'}
            {node?.heartbeat != null ? ` · HB ${node.heartbeat}` : ''}
            {node?.age_s != null ? ` (${node.age_s}s 전)` : ''}
          </div>
        </div>
      </div>
      {tcpOn && !polling && (
        <p className="muted device-note">
          TCP 세션은 열려 있지만 Modbus 요청이 오지 않고 있습니다 — 클라이언트에서
          읽기(폴링)를 시작했는지, 프로토콜이 'Modbus TCP'(RTU over TCP 아님)인지 확인하세요.
        </p>
      )}
      {virtualOn && (
        <p className="muted device-note">
          가상 노드 동작 중 — Heartbeat 판정에는 가상 기록이 포함됩니다.
          실장비 접속 여부는 위의 '요청 수신'(가상 제외)으로 판별하세요.
        </p>
      )}
    </section>
  )
}

/** 토글 스위치 — 레지스터 비트 수동 제어. */
export function Switch({ on, onChange, disabled }: {
  on: boolean
  onChange: (next: boolean) => void
  disabled?: boolean
}) {
  return (
    <button
      type="button"
      className={`switch ${on ? 'on' : ''}`}
      disabled={disabled}
      onClick={() => onChange(!on)}
      role="switch"
      aria-checked={on}
    >
      <span className="switch-knob" />
    </button>
  )
}

/** 트리거 행 — 이름·참조주소·설명 + 토글. */
export function TriggerRow({ reg, hint, onChange }: {
  reg: RegRow | undefined
  hint?: string
  onChange: (next: boolean) => void
}) {
  if (!reg) return null
  const on = reg.value === 1
  return (
    <div className="trig-row">
      <div className="trig-info">
        <div className="trig-name">
          {reg.name}
          <span className="trig-ref">{refLabel(reg)}</span>
        </div>
        {hint && <div className="trig-hint">{hint}</div>}
      </div>
      <span className={`bit ${on ? 'on' : 'off'}`}>
        <span className="bit-dot" />{on ? '1 (ON)' : '0 (OFF)'}
      </span>
      <Switch on={on} onChange={onChange} />
    </div>
  )
}

export function refLabel(reg: RegRow): string {
  const prefix = reg.link === 1 ? '①' : reg.link === 2 ? '②' : '③'
  return `${prefix}${reg.obj.toUpperCase()} ${reg.ref}`
}

/** 값 표시 셀. */
export function RegValue({ reg, format }: { reg: RegRow | undefined; format?: (v: number) => string }) {
  if (!reg) return <div className="meta"><div className="label">—</div><div className="value">—</div></div>
  const text = format ? format(reg.value) : `${fmtNum(reg.value)}${reg.unit ? ` ${reg.unit}` : ''}`
  return (
    <div className="meta" title={refLabel(reg)}>
      <div className="label">{reg.name}</div>
      <div className="value">{text}</div>
    </div>
  )
}

export function fmtNum(v: number): string {
  return Number.isInteger(v) ? v.toLocaleString() : v.toFixed(2)
}

/** 가상 노드 마스터 스위치 카드 — 정상 상대 노드 재현 (실운영 파이프라인). */
export function VirtualNodeCard({ node, on, hint, detail, onChange }: {
  node: 'rtds' | 'ems'
  on: boolean
  hint: string
  detail: string
  onChange: (next: boolean) => void
}) {
  const label = node === 'rtds' ? '가상 RTDS' : '가상 EMS'
  return (
    <section className={`card virtual-card ${on ? 'active' : ''}`}>
      <div className="trig-row" style={{ borderBottom: 'none', padding: 0 }}>
        <div className="trig-info">
          <div className="trig-name">
            {label}
            <span className={`badge ${on ? 'state-running' : 'state-idle'}`}>
              {on ? '동작 중' : '꺼짐'}
            </span>
          </div>
          <div className="trig-hint">{hint}</div>
          <div className="trig-hint muted">{detail}</div>
        </div>
        <Switch on={on} onChange={onChange} />
      </div>
    </section>
  )
}

/** Hex 모니터 — 링크 전체 레지스터의 물리값·raw·Hex를 표시, 변화 시 하이라이트. */
export function HexMonitor({ rows }: { rows: RegRow[] }) {
  const prevRef = useRef<Map<string, { raw: number; ts: number }>>(new Map())
  const now = Date.now()
  const changed = new Set<string>()
  for (const row of rows) {
    const prev = prevRef.current.get(row.key)
    if (prev && prev.raw !== row.raw) {
      prevRef.current.set(row.key, { raw: row.raw, ts: now })
      changed.add(row.key)
    } else if (!prev) {
      prevRef.current.set(row.key, { raw: row.raw, ts: 0 })
    } else if (now - prev.ts < 1600) {
      changed.add(row.key) // 최근 변경 잔광
    }
  }

  const groups: { obj: RegRow['obj']; title: string }[] = [
    { obj: 'coil', title: 'Coil (FC01/05/15)' },
    { obj: 'di', title: 'Discrete Input (FC02)' },
    { obj: 'ir', title: 'Input Register (FC04)' },
    { obj: 'hr', title: 'Holding Register (FC03/06/16)' },
  ]

  if (rows.length === 0) return <p className="muted empty">레지스터 조회 중…</p>
  return (
    <div className="hex-groups">
      {groups.map(({ obj, title }) => {
        const group = rows.filter((r) => r.obj === obj)
        if (group.length === 0) return null
        return (
          <div key={obj} className="hex-group">
            <div className="label">{title}</div>
            <div className="table-wrap">
              <table className="data-table hex-table">
                <thead>
                  <tr>
                    <th>참조주소</th><th>이름</th><th>물리값</th><th>Raw</th><th>Hex</th>
                  </tr>
                </thead>
                <tbody>
                  {group.map((r) => (
                    <tr key={r.key} className={changed.has(r.key) ? 'hex-changed' : ''}>
                      <td className="hex-ref">{refLabel(r)}</td>
                      <td>{r.name}</td>
                      <td>{fmtNum(r.value)}{r.unit ? ` ${r.unit}` : ''}</td>
                      <td className="hex-raw">{r.raw}</td>
                      <td className="hex-raw">0x{r.raw.toString(16).toUpperCase().padStart(4, '0')}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )
      })}
    </div>
  )
}

export const PHASE_LABEL: Record<string, string> = {
  P0: 'P0 대기', P4: 'P4 운전 루프', P5: 'P5 완료·정산', stopped: '수동 정지',
}

export const CLOCK_LABEL: Record<string, string> = {
  idle: '대기', running: '운전중', paused: '일시정지', completed: 'Run 완료', stopped: '정지됨',
}

/** 시험 페이지 상단 — 시퀀스 단계·가상시각·접속 안내. */
export function TestHeader({ title, status, net, listenPort, error }: {
  title: string
  status: TestStatus
  net: NetworkSettings | null
  listenPort: (n: NetworkSettings) => number
  error: string | null
}) {
  const clock = status.clock
  const run = status.run
  return (
    <>
      <header className="topbar">
        <h1 className="page-title">{title}</h1>
        <div className="badges">
          {run && (
            <span className="badge state-completed">
              {run.run_label ? `Run ${run.run_label} · ` : ''}{PHASE_LABEL[run.phase] ?? run.phase}
            </span>
          )}
          {clock && (
            <span className={`badge state-${clock.state}`}>
              {CLOCK_LABEL[clock.state] ?? clock.state}
              {' · '}{clock.virtual_time?.replace('T', ' ').slice(0, 16)}
              {' · 구간 '}{clock.interval_index}
            </span>
          )}
        </div>
      </header>
      <section className="card test-conn">
        <div className="label">접속 안내</div>
        {net ? (
          net.mode === 'per_link_port' ? (
            <p className="conn-text">
              이 노드는 시뮬매니저 <b>{'<서버 IP>'}:{listenPort(net)}</b> 로 접속합니다
              <span className="muted"> (per_link_port 모드 — 링크① {net.link1_port} / 링크③ {net.link3_port},
              포트 변경은 ⚙ 설정 → 네트워크)</span>
            </p>
          ) : (
            <p className="conn-text">
              이 노드는 시뮬매니저 <b>{'<서버 IP>'}:{net.port}</b> 로 접속합니다
              <span className="muted"> (single_port 모드 — 클라이언트 IP로 라우팅: EMS {net.ems_ip} /
              RTDS {net.rtds_ip}, 변경은 ⚙ 설정 → 네트워크)</span>
            </p>
          )
        ) : (
          <p className="muted">네트워크 설정 조회 중…</p>
        )}
        {error && <p className="error">{error}</p>}
      </section>
    </>
  )
}
