// RTDS 단독 시험 페이지 — EMS 없이 RTDS와만 시험할 때,
// EMS 몫의 상태(③IR 'EMS 상태')와 매니저 트리거(③DI)를 수동 조작하고 RTDS 기록을 모니터링한다.
import { useState } from 'react'
import {
  useTestPage, DeviceStatusCard, HexMonitor, RegValue, TestHeader, TriggerRow, VirtualNodeCard,
  fmtNum, refLabel,
} from './testShared'

const STATUS_CODE: Record<number, string> = { 0: '0 정상', 1: '1 주의', 2: '2 고장' }
const WEATHER_CODE: Record<number, string> = { 0: '0 정상', 1: '1 폭염', 2: '2 폭설' }

export default function TestRtdsPage() {
  const { regs, regList, status, net, virtual, nodes, error, write, setVirtualNode } = useTestPage(3)
  const toggle = (key: string) => (next: boolean) => write(key, next ? 1 : 0)
  const [socInput, setSocInput] = useState('')

  const applySoc = () => {
    const v = Number(socInput)
    if (!Number.isFinite(v) || v < 0 || v > 100) return
    write('initial_soc', v)
    setSocInput('')
  }

  return (
    <main className="shell wide">
      <TestHeader
        title="RTDS 시험" status={status} net={net} error={error}
        listenPort={(n) => n.link3_port}
      />

      {/* ── 실장비(RTDS) 접속 상태 — 링크③ 요청 활동 + HB 규약 판정 ── */}
      <DeviceStatusCard title="RTDS (링크③)" node={nodes?.rtds} virtualOn={virtual?.rtds ?? false} />

      {/* ── 가상 EMS: 정상 EMS를 매니저가 재현 — 실운영과 동일 시퀀스 진행 ── */}
      <VirtualNodeCard
        node="ems" on={virtual?.ems ?? false}
        onChange={(next) => setVirtualNode('ems', next)}
        hint="RTDS 단독 시험용 — 정상 EMS의 프로토콜 행동을 자동 재현하여 실운영과 동일하게 시퀀스가 진행됩니다 ('EMS 상태'=1 유지)"
        detail="자동 수행: ①DI0 준비완료 세트(미러 경유) · Heartbeat 실 1s → 생존감시 alive → ③IR 'EMS 상태'=1"
      />

      {/* ── 트리거 제어: 매니저 → RTDS (③DI·IR) ── */}
      <section className="card">
        <div className="node-head">
          <span className="node-title">트리거 제어 (수동 오버라이드) — RTDS가 읽는 지령·상태 (③DI·IR)</span>
          <span className="muted">
            정상 흐름은 가상 EMS + Run 시작으로 자동 진행 — 특정 신호만 강제하고 싶을 때 사용
          </span>
        </div>
        <TriggerRow reg={regs.ems_status} onChange={toggle('ems_status')}
          hint="★ 원래 EMS Heartbeat 생존감시가 자동 세트(1=연결). RTDS 단독 시험에서는 수동 세트 → 0이면 RTDS는 EMS 지령 무시·안전 유지" />
        <TriggerRow reg={regs.initial_soc_trigger} onChange={toggle('initial_soc_trigger')}
          hint="P2 핸드셰이크 — 1로 세우면 RTDS가 ③IR 300011 초기 SOC를 주입하고 ③Coil1 ack를 올림" />
        <TriggerRow reg={regs.ess_run_enable_sim} onChange={toggle('ess_run_enable_sim')}
          hint="시뮬 차원 ESS 운전 허용 — EMS Enable과 AND 조건" />
        <TriggerRow reg={regs.reset} onChange={toggle('reset')}
          hint="P6 리셋 — RTDS 모델 상태 초기화 트리거" />
        <div className="trig-row">
          <div className="trig-info">
            <div className="trig-name">
              초기 SOC 값
              {regs.initial_soc && <span className="trig-ref">{refLabel(regs.initial_soc)}</span>}
            </div>
            <div className="trig-hint">주입 트리거 세트 전에 원하는 SOC를 기록 (0~100%)</div>
          </div>
          <span className="bit on">
            <span className="bit-dot" />현재 {regs.initial_soc ? fmtNum(regs.initial_soc.value) : '—'} %
          </span>
          <input
            type="number" min={0} max={100} step={0.1} className="soc-input"
            placeholder="%" value={socInput}
            onChange={(e) => setSocInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') applySoc() }}
          />
          <button className="btn" onClick={applySoc} disabled={socInput === ''}>적용</button>
        </div>
      </section>

      {/* ── RTDS 모니터링: RTDS → 매니저 기록 (③Coil·HR) ── */}
      <section className="card">
        <div className="node-head">
          <span className="node-title">RTDS 모니터링 — RTDS가 기록한 동작·계측 (③Coil·HR)</span>
        </div>
        <div className="sig-group">
          <div className="label">동작 비트 (③Coil)</div>
          <div className="sig-row">
            <Bit reg={regs.rtds_running} />
            <Bit reg={regs.initial_soc_ack} />
          </div>
        </div>
        <div className="sig-group">
          <div className="label">생존·1분 계측 (③HR 400000~400005)</div>
          <div className="rtds-values">
            <RegValue reg={regs.rtds_heartbeat} />
            <RegValue reg={regs.rtds_status_code} format={(v) => STATUS_CODE[v] ?? String(v)} />
            <RegValue reg={regs.ess_power} format={(v) => `${fmtNum(v)} kW${v > 0.05 ? ' (방전)' : v < -0.05 ? ' (충전)' : ''}`} />
            <RegValue reg={regs.ess_soc} />
            <RegValue reg={regs.pcc_import} />
            <RegValue reg={regs.pcc_export} />
          </div>
        </div>
      </section>

      {/* ── 매니저 배포값: RTDS가 읽는 환경 (③IR) ── */}
      <section className="card">
        <div className="node-head">
          <span className="node-title">매니저 배포값 — RTDS가 읽는 환경·시나리오 (③IR)</span>
          <span className="muted">Run 중에는 가상 1분마다 갱신됨</span>
        </div>
        <div className="rtds-values">
          <RegValue reg={regs.accel_factor} />
          <RegValue reg={regs.interval_index} />
          <RegValue reg={regs.scenario_id}
            format={(v) => (v === 1 ? '1 (Run A 규칙기반)' : v === 2 ? '2 (Run B 최적)' : String(v))} />
          <RegValue reg={regs.irradiance} />
          <RegValue reg={regs.ambient_temp} />
          <RegValue reg={regs.snow_depth} />
          <RegValue reg={regs.weather_code} format={(v) => WEATHER_CODE[v] ?? String(v)} />
          <RegValue reg={regs.load_profile_ref} />
          <RegValue reg={regs.pv_power} />
          <RegValue reg={regs.load_power} />
        </div>
      </section>

      {/* ── Hex 모니터: 링크③ 전체 레지스터 원시값 ── */}
      <section className="card">
        <div className="node-head">
          <span className="node-title">Hex 모니터 — 링크③ 전체 레지스터</span>
          <span className="muted">1초 갱신 · 변화 시 하이라이트 — RTDS와 주고받는 원시 데이터 검증용</span>
        </div>
        <HexMonitor rows={regList} />
      </section>
    </main>
  )
}

function Bit({ reg }: { reg?: import('./testShared').RegRow }) {
  if (!reg) return null
  const on = reg.value === 1
  return (
    <span className={`bit ${on ? 'on' : 'off'}`} title={refLabel(reg)}>
      <span className="bit-dot" />
      {reg.name} {fmtNum(reg.value)}
    </span>
  )
}
