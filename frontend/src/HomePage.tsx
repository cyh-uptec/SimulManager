import { useEffect, useState } from 'react'
import { useClock } from './useClock'
import SettingsModal from './SettingsModal'
import type { ClockState, NodeState, NodeStatus, Signals } from './types'

const STATE_LABEL: Record<ClockState, string> = {
  idle: '대기',
  running: '운전중',
  paused: '일시정지',
  completed: 'Run 완료',
  stopped: '정지됨',
}

const PHASE_LABEL: Record<string, string> = {
  P0: 'P0 대기',
  P4: 'P4 운전 루프',
  P5: 'P5 완료·정산',
  stopped: '수동 정지',
}

const NODE_STATE_LABEL: Record<NodeState, string> = {
  unknown: '미접속',
  alive: '정상',
  lost: '단절',
}

function fmtVirtual(iso: string): { date: string; time: string } {
  const d = new Date(iso)
  const pad = (n: number) => String(n).padStart(2, '0')
  return {
    date: `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`,
    time: `${pad(d.getHours())}:${pad(d.getMinutes())}`,
  }
}

export default function HomePage() {
  const { status, run, nodes, seqLog, connected, error, clockAction, runAction, clearSeqLog } = useClock()
  const [showSettings, setShowSettings] = useState(false)
  const [scenario, setScenario] = useState(1)
  const [signals, setSignals] = useState<Signals | null>(null)

  // 링크 신호·RTDS 기록 데이터 — 1.5초 폴링 (Home 표시 중에만)
  useEffect(() => {
    let alive = true
    const poll = async () => {
      try {
        const res = await fetch('/api/signals')
        if (alive && res.ok) setSignals(await res.json())
      } catch { /* 서버 재기동 중 등 — 다음 폴링에서 재시도 */ }
    }
    poll()
    const timer = setInterval(poll, 1500)
    return () => { alive = false; clearInterval(timer) }
  }, [])

  if (!status) {
    return <main className="shell"><p className="muted">서버 연결 중…</p></main>
  }

  const { date, time } = fmtVirtual(status.virtual_time)
  const s = status.state
  const disconnects = run?.disconnect_intervals ?? 0
  const budget = run?.disconnect_budget ?? 14

  return (
    <main className="shell">
      <header className="topbar">
        <div className="title-row">
          <img
            className="corp-logo" src="/logo_wide.png" alt="회사 로고"
            onError={(e) => { e.currentTarget.style.display = 'none' }}
          />
          <h1 className="page-title">운전 현황</h1>
        </div>
        <div className="badges">
          <span
            className={`badge ws-${connected ? 'on' : 'off'}`}
            title="브라우저↔매니저 대시보드 실시간 연결(WebSocket) — EMS·RTDS Modbus 통신은 별도 TCP/IP(502)"
          >
            {connected ? '대시보드 연결' : '대시보드 끊김'}
          </span>
          {run?.run_label && (
            <span className="badge state-completed">
              Run {run.run_label} · {PHASE_LABEL[run.phase] ?? run.phase}
            </span>
          )}
          <span className={`badge state-${s}`}>{STATE_LABEL[s]}</span>
          <button className="btn gear" onClick={() => setShowSettings(true)}>⚙ 설정</button>
        </div>
      </header>

      {showSettings && (
        <SettingsModal editable={s === 'idle'} onClose={() => setShowSettings(false)} />
      )}

      <section className="stat-grid">
        <StatCard icon="🕐" title="가상 시각">
          <div className="vtime stat-time">{time}</div>
          <div className="stat-sub">{date}</div>
          <div className="mini-progress">
            <div className="progress-track">
              <div
                className="progress-fill"
                style={{ width: `${(status.elapsed_minutes / status.total_minutes) * 100}%` }}
              />
            </div>
            <div className="stat-sub">
              {status.elapsed_minutes.toLocaleString()} / {status.total_minutes.toLocaleString()}분
            </div>
          </div>
        </StatCard>

        <StatCard icon="⏱" title="구간 인덱스">
          <Donut value={status.interval_index} max={95} color="#6366f1"
            center={String(status.interval_index)} sub="/ 95" />
        </StatCard>

        <StatCard icon="📅" title="일차">
          <div className="stat-value">{status.day_number}<span className="stat-unit"> / {status.total_days}일</span></div>
          <div className="stat-sub">가상 {status.total_days}일 Run</div>
        </StatCard>

        <StatCard icon="⚡" title="가속 배율">
          <div className="stat-value">{status.accel_seconds_per_15min}<span className="stat-unit">s</span></div>
          <div className="stat-sub">실 {status.accel_seconds_per_15min}초 = 가상 15분</div>
          <div className="stat-sub">틱 {status.tick_period_s}s / 가상 1분</div>
        </StatCard>

        <StatCard icon="🔌" title="단절 누적">
          <Donut value={disconnects} max={budget}
            color={disconnects > budget * 0.7 ? '#f26d6d' : '#2ec5b6'}
            center={String(disconnects)} sub={`/ ${budget} 구간`} />
        </StatCard>
      </section>

      <section className="nodes">
        <NodeCard title="EMS" node={nodes?.ems}>
          <div className="sig-group">
            <div className="label">EMS가 읽는 지령 (①Coil)</div>
            <div className="sig-row">
              <Bit label="운전 시작" on={signals?.link1.run_start === 1} />
              <Bit label="RTDS 준비됨" on={signals?.link1.rtds_ready === 1} />
              <Bit label="EMS Enable" on={signals?.link1.ems_enable === 1} />
            </div>
          </div>
        </NodeCard>

        <NodeCard title="RTDS" node={nodes?.rtds}>
          <div className="sig-group">
            <div className="label">트리거·상태 (③DI / ③Coil)</div>
            <div className="sig-row">
              <Bit label="SOC 트리거" on={signals?.link3.initial_soc_trigger === 1} />
              <Bit label="SOC 반영완료" on={signals?.link3.initial_soc_ack === 1} />
              <Bit label="ESS Enable(시뮬)" on={signals?.link3.ess_run_enable_sim === 1} />
              <Bit label="운전중" on={signals?.link3.rtds_running === 1} />
            </div>
          </div>
          <div className="sig-group">
            <div className="label">RTDS → 매니저 기록 (③HR, 1분 시계열)</div>
            <div className="rtds-values">
              <Meta label="ESS 전력" value={fmtPower(signals?.link3.ess_power)} />
              <Meta label="SOC" value={signals ? `${signals.link3.ess_soc.toFixed(1)} %` : '—'} />
              <Meta label="PCC 수전" value={signals ? `${signals.link3.pcc_import.toFixed(1)} kW` : '—'} />
              <Meta label="PCC 역송" value={signals ? `${signals.link3.pcc_export.toFixed(1)} kW` : '—'} />
              <Meta label="상태코드" value={signals ? STATUS_CODE_LABEL[signals.link3.rtds_status_code] ?? String(signals.link3.rtds_status_code) : '—'} />
            </div>
          </div>
        </NodeCard>
      </section>

      <section className="card controls">
        <select
          className="scenario-select" value={scenario} disabled={s !== 'idle'}
          onChange={(e) => setScenario(Number(e.target.value))}
        >
          <option value={1}>Run A — 규칙기반 (시나리오 1)</option>
          <option value={2}>Run B — 최적운영 (시나리오 2)</option>
        </select>
        <button
          className="btn primary" disabled={s !== 'idle' || run?.data_ready === false}
          onClick={() => runAction('start', { scenario_id: scenario })}
        >
          ▶ Run 시작
        </button>
        <button className="btn" disabled={s !== 'running'} onClick={() => clockAction('pause')}>
          ⏸ 일시정지
        </button>
        <button className="btn" disabled={s !== 'paused'} onClick={() => clockAction('resume')}>
          ⏵ 재개
        </button>
        <button
          className="btn danger"
          disabled={s !== 'running' && s !== 'paused'}
          onClick={() => runAction('stop')}
        >
          ⏹ 정지
        </button>
        <button
          className="btn" disabled={s === 'running'}
          onClick={() => runAction('reset', { scenario_id: scenario })}
          title="P6 케이스 전환 — 리셋·시작시각으로 되감기"
        >
          ⏮ 리셋
        </button>
        {run?.data_ready === false && (
          <span className="error">부하·기상 데이터 미로드 — 데이터 메뉴에서 업로드하세요</span>
        )}
        {error && <span className="error">{error}</span>}
      </section>

      <section className="card">
        <div className="label seq-title">
          시퀀스 로그 (최근)
          <button className="btn del" onClick={clearSeqLog}
            title="화면 표시만 지웁니다 (기록은 유지, 새 이벤트는 다시 표시) — 전체 기록 삭제는 시퀀스 메뉴의 '🗑 로그 삭제'">
            ✕ 화면 지우기
          </button>
        </div>
        {seqLog.length === 0 ? (
          <p className="muted">아직 이벤트가 없습니다 — Run을 시작하면 P1~P6 진행이 기록됩니다.</p>
        ) : (
          <ul className="seq-log">
            {seqLog.map((entry, i) => (
              <li key={`${entry.real_time}-${i}`}>
                <span className="seq-time">{entry.virtual_time.replace('T', ' ')}</span>
                <span className={`seq-phase phase-${entry.phase}`}>{entry.phase}</span>
                <span className="seq-msg">{entry.message}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </main>
  )
}

function StatCard({ icon, title, children }: {
  icon: string; title: string; children: React.ReactNode
}) {
  return (
    <div className="stat-card">
      <div className="stat-head">
        <span className="stat-icon">{icon}</span>
        {title}
      </div>
      <div className="stat-body">{children}</div>
    </div>
  )
}

/** 원형(도넛) 차트 — 첨부 레퍼런스의 SOC 링 스타일 (SVG, 라이브러리 불필요) */
function Donut({ value, max, color, center, sub }: {
  value: number; max: number; color: string; center: string; sub: string
}) {
  const r = 40
  const circumference = 2 * Math.PI * r
  const pct = Math.min(1, Math.max(0, max > 0 ? value / max : 0))
  return (
    <div className="donut-wrap">
      <svg viewBox="0 0 100 100" className="donut">
        <circle cx="50" cy="50" r={r} fill="none" stroke="#edf0f8" strokeWidth="11" />
        <circle
          cx="50" cy="50" r={r} fill="none" stroke={color} strokeWidth="11"
          strokeLinecap="round"
          strokeDasharray={`${circumference * pct} ${circumference}`}
          transform="rotate(-90 50 50)"
          style={{ transition: 'stroke-dasharray 0.4s' }}
        />
        <text x="50" y="49" textAnchor="middle" className="donut-center">{center}</text>
        <text x="50" y="65" textAnchor="middle" className="donut-sub">{sub}</text>
      </svg>
    </div>
  )
}

const STATUS_CODE_LABEL: Record<number, string> = { 0: '0 정상', 1: '1 주의', 2: '2 고장' }

function fmtPower(kw?: number): string {
  if (kw == null) return '—'
  const dir = kw > 0.05 ? ' (방전)' : kw < -0.05 ? ' (충전)' : ''
  return `${kw.toFixed(1)} kW${dir}`
}

function Bit({ label, on }: { label: string; on: boolean }) {
  return (
    <span className={`bit ${on ? 'on' : 'off'}`}>
      <span className="bit-dot" />
      {label}
    </span>
  )
}

function NodeCard({ title, node, children }: {
  title: string; node?: NodeStatus; children?: React.ReactNode
}) {
  const state: NodeState = node?.state ?? 'unknown'
  const tcpOn = (node?.connections ?? 0) > 0
  const reqAge = node?.request_age_s
  const polling = reqAge != null && reqAge < 5
  // 배지: HB 규약 판정이 우선 (정상/단절), 그 외에는 TCP 접속 여부로 구분
  const badge = state !== 'unknown'
    ? { cls: `node-badge-${state}`, text: NODE_STATE_LABEL[state] }
    : tcpOn
      ? { cls: 'conn-stale', text: '접속됨' }
      : { cls: 'node-badge-unknown', text: '미접속' }
  return (
    <div className={`card node-card node-${state}`}>
      <div className="node-head">
        <span className="node-title">{title}</span>
        <span className={`badge ${badge.cls}`}>{badge.text}</span>
      </div>
      <div className="node-body">
        <Meta label="TCP 접속" value={tcpOn ? `접속 (${node!.connections})` : '미접속'} />
        <Meta label="폴링" value={reqAge == null ? '—' : polling ? `${reqAge}s 전` : `중단 ${reqAge}s 전`} />
        <Meta label="Heartbeat" value={node?.heartbeat != null ? String(node.heartbeat) : '—'} />
        <Meta label="마지막 갱신" value={node?.age_s != null ? `${node.age_s}s 전` : '—'} />
      </div>
      {children}
    </div>
  )
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div className="meta">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
    </div>
  )
}
