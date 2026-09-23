import { useCallback, useEffect, useRef, useState } from 'react'
import type { ClockStatus, NodesStatus, RunStatus, SeqEntry, WsMessage } from './types'

const WS_URL = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`
const SEQ_LOG_MAX = 30

/** WebSocket으로 시계·Run·노드·시퀀스 로그 실시간 수신 + REST 제어. 단절 시 2초 자동 재접속. */
export function useClock() {
  const [status, setStatus] = useState<ClockStatus | null>(null)
  const [run, setRun] = useState<RunStatus | null>(null)
  const [nodes, setNodes] = useState<NodesStatus | null>(null)
  const [seqLog, setSeqLog] = useState<SeqEntry[]>([])
  const [connected, setConnected] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const wsRef = useRef<WebSocket | null>(null)

  useEffect(() => {
    let disposed = false
    let retry: ReturnType<typeof setTimeout>

    const connect = () => {
      const ws = new WebSocket(WS_URL)
      wsRef.current = ws
      ws.onopen = () => setConnected(true)
      ws.onmessage = (e) => {
        const msg: WsMessage = JSON.parse(e.data)
        if (msg.topic === 'clock.state' || msg.topic === 'clock.complete') {
          setStatus((prev) => ({ ...(prev ?? {}), ...msg.payload }) as ClockStatus)
        } else if (msg.topic === 'clock.minute') {
          setStatus((prev) => (prev ? { ...prev, ...msg.payload } : prev))
        } else if (msg.topic === 'watch.update') {
          // WS 페이로드는 HB 판정만 담음 — REST 폴링으로 받은 TCP 접속·폴링 필드는 보존
          const p = msg.payload as unknown as NodesStatus
          setNodes((prev) => ({
            ems: { ...prev?.ems, ...p.ems },
            rtds: { ...prev?.rtds, ...p.rtds },
          }))
        } else if (msg.topic === 'run.state') {
          setRun(msg.payload as unknown as RunStatus)
        } else if (msg.topic === 'sequence.event') {
          setSeqLog((prev) => [msg.payload as unknown as SeqEntry, ...prev].slice(0, SEQ_LOG_MAX))
        } else if (msg.topic === 'sequence.cleared') {
          setSeqLog([]) // 시퀀스 로그 페이지에서 삭제 시 홈 '최근' 목록도 동기화
        }
      }
      ws.onclose = () => {
        setConnected(false)
        if (!disposed) retry = setTimeout(connect, 2000)
      }
    }
    connect()
    // 초기 상태는 REST로 확보 (WS 첫 메시지 이전 공백 대비)
    fetch('/api/status').then((r) => r.json()).then((s) => {
      setStatus(s)
      if (s.run) setRun(s.run)
    }).catch(() => {})
    fetch('/api/nodes').then((r) => r.json()).then(setNodes).catch(() => {})
    fetch(`/api/sequence/log?limit=${SEQ_LOG_MAX}`).then((r) => r.json())
      .then((page: { entries: SeqEntry[] }) => setSeqLog(page.entries ?? []))
      .catch(() => {})
    // TCP 접속·폴링 필드는 WS로 오지 않으므로 2초 주기 REST 폴링으로 갱신
    const nodesTimer = setInterval(() => {
      fetch('/api/nodes').then((r) => r.json()).then(setNodes).catch(() => {})
    }, 2000)

    return () => {
      disposed = true
      clearTimeout(retry)
      clearInterval(nodesTimer)
      wsRef.current?.close()
    }
  }, [])

  const post = useCallback(async (url: string, body?: object) => {
    setError(null)
    const res = await fetch(url, {
      method: 'POST',
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    })
    const payload = await res.json()
    if (!res.ok) {
      setError(typeof payload.detail === 'string' ? payload.detail : `요청 실패 (${res.status})`)
      return null
    }
    return payload
  }, [])

  const clockAction = useCallback(async (name: 'pause' | 'resume') => {
    const result = await post(`/api/clock/${name}`)
    if (result) setStatus((prev) => ({ ...(prev ?? {}), ...result }) as ClockStatus)
  }, [post])

  const runAction = useCallback(async (name: 'start' | 'stop' | 'reset', body?: object) => {
    const result = await post(`/api/run/${name}`, body)
    if (result) setRun(result as RunStatus)
  }, [post])

  // 화면 표시만 초기화 — DB(SQLite) 기록은 유지, 이후 신규 이벤트는 다시 쌓임
  const clearSeqLog = useCallback(() => setSeqLog([]), [])

  return { status, run, nodes, seqLog, connected, error, clockAction, runAction, clearSeqLog }
}
