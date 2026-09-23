export type ClockState = 'idle' | 'running' | 'paused' | 'completed' | 'stopped'

export interface ClockStatus {
  state: ClockState
  virtual_time: string
  interval_index: number
  day_number: number
  total_days: number
  elapsed_minutes: number
  total_minutes: number
  progress: number
  accel_seconds_per_15min: number
  tick_period_s: number
}

export interface WsMessage {
  topic: string
  payload: Record<string, unknown>
}

export interface RunStatus {
  phase: 'P0' | 'P4' | 'P5' | 'stopped'
  scenario_id: number
  run_label: string | null
  soc_injection_pending: boolean
  disconnect_intervals: number
  disconnect_budget: number
  data_ready: boolean
}

export interface SeqEntry {
  id?: number
  real_time: string
  virtual_time: string
  phase: string
  source: 'manager' | 'ems' | 'rtds'
  message: string
  refs: string
}

export interface SeqLogPage {
  total: number
  offset: number
  limit: number
  entries: SeqEntry[]
}

export type NodeState = 'unknown' | 'alive' | 'lost'

export interface NodeStatus {
  state: NodeState
  heartbeat: number | null
  age_s: number | null
  /** 마지막 실 Modbus 요청(읽기 포함) 경과 초 — 가상 노드 제외, null=요청 없음 */
  request_age_s?: number | null
  /** 링크 서버에 열려 있는 실 TCP 세션 수 — 폴링 없이 접속만 해도 ≥1 */
  connections?: number
  /** TCP 세션 상대 주소 목록 ("ip:port") */
  peers?: string[]
}

export interface NodesStatus {
  ems: NodeStatus
  rtds: NodeStatus
}

export interface Signals {
  link1: {
    run_start: number
    rtds_ready: number
    ems_enable: number
  }
  link3: {
    initial_soc_trigger: number
    ess_run_enable_sim: number
    rtds_running: number
    initial_soc_ack: number
    rtds_status_code: number
    ess_power: number
    ess_soc: number
    pcc_import: number
    pcc_export: number
  }
}
