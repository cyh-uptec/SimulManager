import { useCallback, useEffect, useRef, useState } from 'react'
import type { SeqEntry, SeqLogPage, WsMessage } from './types'

const PAGE_SIZE = 50

const SOURCE_LABEL: Record<string, string> = {
  manager: '매니저',
  ems: 'EMS',
  rtds: 'RTDS',
}

/** EMS·RTDS·시뮬매니저 간 전체 시퀀스 로그 — SQLite 영속 저장, 페이지 단위 조회 */
export default function SequencePage() {
  const [page, setPage] = useState<SeqLogPage | null>(null)
  const [offset, setOffset] = useState(0)
  const offsetRef = useRef(0)
  offsetRef.current = offset

  const fetchPage = useCallback(async (off: number) => {
    const res = await fetch(`/api/sequence/log?offset=${off}&limit=${PAGE_SIZE}`)
    if (res.ok) setPage(await res.json())
  }, [])

  useEffect(() => { fetchPage(offset) }, [offset, fetchPage])

  // 최신 페이지(offset 0)를 보는 동안은 신규 이벤트 발생 시 자동 갱신
  useEffect(() => {
    const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`)
    let pending = false
    ws.onmessage = (e) => {
      const msg: WsMessage = JSON.parse(e.data)
      if (msg.topic === 'sequence.event' && offsetRef.current === 0 && !pending) {
        pending = true
        setTimeout(() => { pending = false; fetchPage(0) }, 400) // 폭주 시 병합
      } else if (msg.topic === 'sequence.cleared') {
        setOffset(0)
        fetchPage(0) // 다른 창에서 삭제해도 즉시 반영
      }
    }
    return () => ws.close()
  }, [fetchPage])

  const clearLog = useCallback(async () => {
    const count = page?.total ?? 0
    if (count === 0) {
      window.alert('삭제할 시퀀스 로그가 없습니다.')
      return
    }
    if (!window.confirm(`시퀀스 로그 ${count.toLocaleString()}건을 모두 삭제할까요?\n` +
      '(Run 계측 이력·평가 데이터는 삭제되지 않습니다)')) return
    const res = await fetch('/api/sequence/log', { method: 'DELETE' })
    if (res.ok) {
      setOffset(0)
      fetchPage(0)
    }
  }, [page, fetchPage])

  const total = page?.total ?? 0
  const pageNo = Math.floor(offset / PAGE_SIZE) + 1
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <main className="shell wide">
      <header className="topbar">
        <h1 className="page-title">시퀀스 로그</h1>
        <span className="muted">
          EMS · RTDS · 시뮬매니저 간 시퀀스 기록 — 총 {total.toLocaleString()}건 (SQLite 보존)
        </span>
        <button className="btn danger del" onClick={clearLog}
          title="시퀀스 로그 테이블만 비웁니다 — Run 계측 이력·평가 데이터는 유지">
          🗑 로그 삭제
        </button>
      </header>

      <section className="card">
        {!page || page.entries.length === 0 ? (
          <p className="muted empty">기록된 시퀀스가 없습니다 — Run을 시작하면 P1~P6 진행이 기록됩니다.</p>
        ) : (
          <ul className="seq-log seq-page">
            {page.entries.map((entry) => (
              <SeqRow key={entry.id ?? entry.real_time + entry.message} entry={entry} />
            ))}
          </ul>
        )}

        <div className="pager">
          <button className="btn" disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
            ← 이전 (최신쪽)
          </button>
          <span className="muted">
            {pageNo} / {pageCount} 페이지
            {offset === 0 && total > 0 && ' · 실시간 갱신 중'}
          </span>
          <button className="btn" disabled={offset + PAGE_SIZE >= total}
            onClick={() => setOffset(offset + PAGE_SIZE)}>
            다음 (과거쪽) →
          </button>
        </div>
      </section>
    </main>
  )
}

function SeqRow({ entry }: { entry: SeqEntry }) {
  return (
    <li className="seq-row">
      <div className="seq-row-head">
        <span className="seq-time" title={`실시각 ${entry.real_time.replace('T', ' ')}`}>
          {entry.virtual_time?.replace('T', ' ') ?? '—'}
        </span>
        <span className={`seq-phase phase-${entry.phase}`}>{entry.phase}</span>
        <span className={`seq-source src-${entry.source}`}>
          {SOURCE_LABEL[entry.source] ?? entry.source}
        </span>
        <span className="seq-msg">{entry.message}</span>
      </div>
      {entry.refs && <div className="seq-refs">{entry.refs}</div>}
    </li>
  )
}
