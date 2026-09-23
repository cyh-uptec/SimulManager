import { useState } from 'react'
import HomePage from './HomePage'
import DataPage from './DataPage'
import SequencePage from './SequencePage'
import MonitorPage from './MonitorPage'
import TestEmsPage from './TestEmsPage'
import TestRtdsPage from './TestRtdsPage'

type Page = 'home' | 'monitor' | 'data' | 'sequence' | 'test-ems' | 'test-rtds'

const MENU: { id: Page; icon: string; label: string }[] = [
  { id: 'home', icon: '🏠', label: 'Home' },
  { id: 'monitor', icon: '📈', label: '모니터링' },
  { id: 'data', icon: '📊', label: '데이터' },
  { id: 'sequence', icon: '📜', label: '시퀀스' },
  { id: 'test-ems', icon: '🧪', label: 'EMS 시험' },
  { id: 'test-rtds', icon: '🔧', label: 'RTDS 시험' },
]

export default function App() {
  const [page, setPage] = useState<Page>('home')

  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="brand-row">
          <div className="brand">SimulManager</div>
        </div>
        <nav className="menu">
          {MENU.map((m) => (
            <button
              key={m.id}
              className={`menu-item ${page === m.id ? 'active' : ''}`}
              onClick={() => setPage(m.id)}
            >
              <span className="menu-icon">{m.icon}</span>
              {m.label}
            </button>
          ))}
        </nav>
        <div className="sidebar-foot muted">HILS 시뮬레이션 매니저</div>
      </aside>
      <div className="content">
        {page === 'home' && <HomePage />}
        {page === 'monitor' && <MonitorPage />}
        {page === 'data' && <DataPage />}
        {page === 'sequence' && <SequencePage />}
        {page === 'test-ems' && <TestEmsPage />}
        {page === 'test-rtds' && <TestRtdsPage />}
      </div>
    </div>
  )
}
