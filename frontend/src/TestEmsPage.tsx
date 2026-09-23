// EMS 단독 시험 페이지 — RTDS 없이 EMS와만 시험할 때,
// RTDS 몫의 트리거(연결중개 등)를 운영자가 수동 조작하고 EMS 기록을 모니터링한다.
import {
  useTestPage, DeviceStatusCard, HexMonitor, RegValue, TestHeader, TriggerRow, VirtualNodeCard,
  fmtNum, refLabel,
} from './testShared'

const STATUS_CODE: Record<number, string> = { 0: '0 정상', 1: '1 주의', 2: '2 고장' }
const WEATHER_CODE: Record<number, string> = { 0: '0 정상', 1: '1 폭염', 2: '2 폭설' }

export default function TestEmsPage() {
  const { regs, regList, status, net, virtual, nodes, error, write, setVirtualNode } = useTestPage(1)
  const toggle = (key: string) => (next: boolean) => write(key, next ? 1 : 0)

  return (
    <main className="shell wide">
      <TestHeader
        title="EMS 시험" status={status} net={net} error={error}
        listenPort={(n) => n.link1_port}
      />

      {/* ── 실장비(EMS) 접속 상태 — 링크① 요청 활동 + HB 규약 판정 ── */}
      <DeviceStatusCard title="EMS (링크①)" node={nodes?.ems} virtualOn={virtual?.ems ?? false} />

      {/* ── 가상 RTDS: 정상 RTDS를 매니저가 재현 — 실운영과 동일 시퀀스 진행 ── */}
      <VirtualNodeCard
        node="rtds" on={virtual?.rtds ?? false}
        onChange={(next) => setVirtualNode('rtds', next)}
        hint="EMS 단독 시험용 — 정상 RTDS의 프로토콜 행동을 자동 재현하여 실운영과 동일하게 시퀀스가 진행됩니다 (Run 시작·핸드셰이크 모두 정상 동작)"
        detail="자동 수행: ③Coil0 운전중 세트 → ①'RTDS 준비됨' 연결중개 · Heartbeat 실 1s · 초기 SOC ack 응답 · 가상 1분 간이 계측(③HR) 기록"
      />

      {/* ── 트리거 제어: 매니저 → EMS 지령 (①Coil) ── */}
      <section className="card">
        <div className="node-head">
          <span className="node-title">트리거 제어 (수동 오버라이드) — EMS가 읽는 지령 (①Coil)</span>
          <span className="muted">
            정상 흐름은 가상 RTDS + Run 시작으로 자동 진행 — 특정 신호만 강제하고 싶을 때 사용
          </span>
        </div>
        <TriggerRow reg={regs.rtds_ready} onChange={toggle('rtds_ready')}
          hint="★ 연결중개 — 원래 RTDS 접속(③Coil0) 시 자동 세트. EMS 단독 시험에서는 여기서 수동 세트 → EMS가 링크② 접속 단계로 진행" />
        <TriggerRow reg={regs.run_start} onChange={toggle('run_start')}
          hint="P3 운전 개시 신호 — EMS 제어 루프 시작 트리거. ※비트만 세트됨 — 가상시계·시퀀스까지 도는 정상 Run은 Home의 ▶ Run 시작 사용" />
        <TriggerRow reg={regs.ems_enable} onChange={toggle('ems_enable')}
          hint="EMS 연산 활성 — run_start와 함께 세트되어야 지령 산출" />
        <TriggerRow reg={regs.run_stop} onChange={toggle('run_stop')}
          hint="P5 종료 신호 — EMS는 지령 중단·안전 상태로" />
        <TriggerRow reg={regs.pause} onChange={toggle('pause')}
          hint="일시정지 (운영자 조작 전용)" />
        <TriggerRow reg={regs.reset} onChange={toggle('reset')}
          hint="P6 리셋 — EMS 내부 상태 초기화 트리거" />
      </section>

      {/* ── EMS 모니터링: EMS → 매니저 기록 (①DI·IR 미러) ── */}
      <section className="card">
        <div className="node-head">
          <span className="node-title">EMS 모니터링 — EMS가 기록한 상태·예측 (①DI·IR, 미러 HR 경유)</span>
        </div>
        <div className="sig-group">
          <div className="label">상태 비트 (①DI ← 미러 HR 410000대)</div>
          <div className="sig-row">
            <Bit reg={regs.ems_ready} />
            <Bit reg={regs.ems_computing} />
            <Bit reg={regs.forecast_done} />
            <Bit reg={regs.ems_alarm} />
          </div>
        </div>
        <div className="sig-group">
          <div className="label">생존·상태 (①IR ← 미러 HR 430000대)</div>
          <div className="rtds-values">
            <RegValue reg={regs.ems_heartbeat} />
            <RegValue reg={regs.ems_status_code} format={(v) => STATUS_CODE[v] ?? String(v)} />
          </div>
        </div>
        <div className="sig-group">
          <div className="label">예측 블록 [1구간] (①IR 300002~300006 — [2~4구간]은 예약, v1.7)</div>
          <div className="rtds-values">
            <RegValue reg={regs.forecast_base_interval} />
            <RegValue reg={regs.pv_forecast_1} />
            <RegValue reg={regs.load_forecast_1} />
            <RegValue reg={regs.ess_schedule_1} />
            <RegValue reg={regs.target_soc_1} />
          </div>
        </div>
      </section>

      {/* ── 매니저 배포값: EMS가 읽는 시각·기상 (①HR) ── */}
      <section className="card">
        <div className="node-head">
          <span className="node-title">매니저 배포값 — EMS가 읽는 시각·기상 (①HR)</span>
          <span className="muted">Run 중에는 가상 1분마다 갱신됨</span>
        </div>
        <div className="rtds-values">
          <div className="meta">
            <div className="label">SIM 가상시각 (400000~400004)</div>
            <div className="value">
              {regs.sim_year
                ? `${regs.sim_year.value}-${pad(regs.sim_month?.value)}-${pad(regs.sim_day?.value)} ` +
                  `${pad(regs.sim_hour?.value)}:${pad(regs.sim_minute?.value)}`
                : '—'}
            </div>
          </div>
          <RegValue reg={regs.accel_factor} />
          <RegValue reg={regs.interval_index} />
          <RegValue reg={regs.scenario_id}
            format={(v) => (v === 1 ? '1 (Run A 규칙기반)' : v === 2 ? '2 (Run B 최적)' : String(v))} />
          <RegValue reg={regs.irradiance} />
          <RegValue reg={regs.ambient_temp} />
          <RegValue reg={regs.snow_depth} />
          <RegValue reg={regs.weather_code} format={(v) => WEATHER_CODE[v] ?? String(v)} />
        </div>
      </section>

      {/* ── Hex 모니터: 링크① 전체 레지스터 원시값 ── */}
      <section className="card">
        <div className="node-head">
          <span className="node-title">Hex 모니터 — 링크① 전체 레지스터</span>
          <span className="muted">1초 갱신 · 변화 시 하이라이트 — EMS와 주고받는 원시 데이터 검증용</span>
        </div>
        <HexMonitor rows={regList} />
      </section>
    </main>
  )
}

function pad(n?: number): string {
  return String(n ?? 0).padStart(2, '0')
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
