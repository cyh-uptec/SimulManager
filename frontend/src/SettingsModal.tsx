import { useEffect, useState } from 'react'

export interface SimSettings {
  start_virtual_time: string
  days: number
  accel_seconds_per_15min: number
  initial_soc: number
  soc_max: number
  soc_min: number
  charge_limit_kw: number
  discharge_limit_kw: number
}

interface NetSettings {
  mode: 'per_link_port' | 'single_port'
  port: number
  link1_port: number
  link3_port: number
  ems_ip: string
  rtds_ip: string
}

interface Props {
  editable: boolean
  onClose: () => void
}

export default function SettingsModal({ editable, onClose }: Props) {
  const [form, setForm] = useState<SimSettings | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    fetch('/api/settings')
      .then((r) => r.json())
      .then((s: SimSettings) => setForm({ ...s, start_virtual_time: s.start_virtual_time.slice(0, 16) }))
      .catch(() => setError('설정을 불러오지 못했습니다'))
  }, [])

  const set = (key: keyof SimSettings, value: string) =>
    setForm((prev) => (prev ? { ...prev, [key]: key === 'start_virtual_time' ? value : Number(value) } : prev))

  const save = async () => {
    if (!form) return
    setSaving(true)
    setError(null)
    setSaved(false)
    try {
      const res = await fetch('/api/settings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(form),
      })
      const body = await res.json()
      if (!res.ok) {
        setError(typeof body.detail === 'string' ? body.detail : '입력값을 확인하세요')
        return
      }
      setSaved(true)
      setTimeout(onClose, 700)
    } catch {
      setError('서버 요청에 실패했습니다')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal card" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2>시뮬레이션 설정</h2>
          <button className="btn icon-btn" onClick={onClose} aria-label="닫기">✕</button>
        </div>

        {!editable && (
          <p className="notice">운전 중에는 설정을 변경할 수 없습니다 — 정지 후 되감기 하세요.</p>
        )}

        {!form ? (
          <p className="muted">불러오는 중…</p>
        ) : (
          <div className="form-grid">
            <Field label="가상 시작시각">
              <input type="datetime-local" value={form.start_virtual_time}
                onChange={(e) => set('start_virtual_time', e.target.value)} />
            </Field>
            <Field label="시뮬레이션 기간 (일)">
              <input type="number" min={1} max={365} value={form.days}
                onChange={(e) => set('days', e.target.value)} />
            </Field>
            <Field label="시간 가속 배율 (실 초 = 가상 15분)" hint="1~900초 — 예: 10이면 1 Run ≈ 실 8시간">
              <input type="number" min={1} max={900} value={form.accel_seconds_per_15min}
                onChange={(e) => set('accel_seconds_per_15min', e.target.value)} />
            </Field>
            <Field label="초기 SOC (%)" hint="양 Run 동일 주입 (공정 비교)">
              <input type="number" min={0} max={100} step={0.1} value={form.initial_soc}
                onChange={(e) => set('initial_soc', e.target.value)} />
            </Field>
            <Field label="SOC 운전 상한 (%)">
              <input type="number" min={0} max={100} step={0.1} value={form.soc_max}
                onChange={(e) => set('soc_max', e.target.value)} />
            </Field>
            <Field label="SOC 운전 하한 (%)">
              <input type="number" min={0} max={100} step={0.1} value={form.soc_min}
                onChange={(e) => set('soc_min', e.target.value)} />
            </Field>
            <Field label="충전량 제한 (kW)">
              <input type="number" min={0} max={655} step={0.1} value={form.charge_limit_kw}
                onChange={(e) => set('charge_limit_kw', e.target.value)} />
            </Field>
            <Field label="방전량 제한 (kW)">
              <input type="number" min={0} max={655} step={0.1} value={form.discharge_limit_kw}
                onChange={(e) => set('discharge_limit_kw', e.target.value)} />
            </Field>
          </div>
        )}

        <div className="modal-foot">
          {error && <span className="error">{error}</span>}
          {saved && <span className="saved">저장되었습니다</span>}
          <div className="modal-actions">
            <button className="btn" onClick={onClose}>취소</button>
            <button className="btn primary" disabled={!form || !editable || saving} onClick={save}>
              {saving ? '저장 중…' : '저장'}
            </button>
          </div>
        </div>

        <NetworkSection />
      </div>
    </div>
  )
}

/** 네트워크(Modbus 서버 포트) 설정 — 저장 시 서버 즉시 재기동 (시뮬레이션 설정과 별도 저장). */
function NetworkSection() {
  const [form, setForm] = useState<NetSettings | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    fetch('/api/settings/network')
      .then((r) => r.json())
      .then(setForm)
      .catch(() => setError('네트워크 설정을 불러오지 못했습니다'))
  }, [])

  const set = (key: keyof NetSettings, value: string) =>
    setForm((prev) => {
      if (!prev) return prev
      const numeric = key === 'port' || key === 'link1_port' || key === 'link3_port'
      return { ...prev, [key]: numeric ? Number(value) : value }
    })

  const save = async () => {
    if (!form) return
    setSaving(true)
    setError(null)
    setSaved(false)
    try {
      const res = await fetch('/api/settings/network', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(form),
      })
      const body = await res.json()
      if (!res.ok) {
        setError(typeof body.detail === 'string' ? body.detail : '입력값을 확인하세요')
        return
      }
      setForm(body)
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    } catch {
      setError('서버 요청에 실패했습니다')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="net-section">
      <div className="modal-head">
        <h2>네트워크 (Modbus 서버 포트)</h2>
      </div>
      <p className="notice">
        저장 시 링크①·③ Modbus 서버가 새 포트로 즉시 재기동됩니다 — EMS·RTDS 연결이 잠시 끊겼다 재접속됩니다.
      </p>
      {!form ? (
        <p className="muted">불러오는 중…</p>
      ) : (
        <>
          <div className="form-grid">
            <label className="field">
              <span className="label">포트 모드</span>
              <select value={form.mode} onChange={(e) => set('mode', e.target.value)}>
                <option value="per_link_port">per_link_port — 링크별 포트 분리 (개발·시험)</option>
                <option value="single_port">single_port — 단일 포트, 클라이언트 IP 라우팅 (운영)</option>
              </select>
            </label>
            {form.mode === 'per_link_port' ? (
              <>
                <label className="field">
                  <span className="label">링크① 포트 (EMS 접속)</span>
                  <input type="number" min={1} max={65535} value={form.link1_port}
                    onChange={(e) => set('link1_port', e.target.value)} />
                </label>
                <label className="field">
                  <span className="label">링크③ 포트 (RTDS 접속)</span>
                  <input type="number" min={1} max={65535} value={form.link3_port}
                    onChange={(e) => set('link3_port', e.target.value)} />
                </label>
              </>
            ) : (
              <>
                <label className="field">
                  <span className="label">단일 포트</span>
                  <input type="number" min={1} max={65535} value={form.port}
                    onChange={(e) => set('port', e.target.value)} />
                </label>
                <label className="field">
                  <span className="label">EMS IP (링크①로 라우팅)</span>
                  <input type="text" value={form.ems_ip}
                    onChange={(e) => set('ems_ip', e.target.value)} />
                </label>
                <label className="field">
                  <span className="label">RTDS IP (링크③으로 라우팅)</span>
                  <input type="text" value={form.rtds_ip}
                    onChange={(e) => set('rtds_ip', e.target.value)} />
                </label>
              </>
            )}
          </div>
          <div className="modal-foot">
            {error && <span className="error">{error}</span>}
            {saved && <span className="saved">적용되었습니다 — Modbus 서버 재기동 완료</span>}
            <div className="modal-actions">
              <button className="btn primary" disabled={saving} onClick={save}>
                {saving ? '적용 중…' : '네트워크 저장·재기동'}
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  )
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="field">
      <span className="label">{label}</span>
      {children}
      {hint && <span className="hint">{hint}</span>}
    </label>
  )
}
