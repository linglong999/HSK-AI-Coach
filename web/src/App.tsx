// App.tsx · React 壳：SessionProvider 包裹 + 侧栏 + 单主区多视图互斥（复刻老壳学习动线）
// 视图 hash 导航：dialog(默认) / map / review / report / doc / metrics / help。
// 会话数据层经 SessionProvider 下发（会话列表/切换/深链）。设置=弹窗（批次3补齐）。
import { useEffect, useState } from 'react'
import Sidebar, { type View } from './components/Sidebar'
import { SessionProvider } from './lib/session'
import Speak from './pages/Speak'
import Home from './pages/Home'
import Review from './pages/Review'
import Help from './pages/Help'
import Report from './pages/Report'
import Doc from './pages/Doc'
import Metrics from './pages/Metrics'
import SettingsModal from './components/SettingsModal'

const HASH_TO_VIEW: Record<string, View> = {
  map: 'map', review: 'review', help: 'help', report: 'report', doc: 'doc', metrics: 'metrics',
}

function parseHash(): View {
  const key = location.hash.replace(/^#\/?/, '').split(/[?#]/)[0]
  return HASH_TO_VIEW[key] ?? 'dialog'
}

function Shell() {
  const [view, setView] = useState<View>(parseHash)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [settingsTab, setSettingsTab] = useState<string>('lang')
  // 语言/主题切换（SettingsModal applyLang/applyTheme）→ 全局重渲染，t() 立即更新
  const [, bump] = useState(0)
  useEffect(() => {
    const f = () => bump((x) => x + 1)
    window.addEventListener('hsk:shell-changed', f)
    return () => window.removeEventListener('hsk:shell-changed', f)
  }, [])

  useEffect(() => {
    const onChange = () => setView(parseHash())
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])

  const nav = (v: View) => {
    location.hash = v === 'dialog' ? '#/' : `#/${v}`
  }
  const openSettings = (tab = 'lang') => { setSettingsTab(tab); setSettingsOpen(true) }

  return (
    <div className="app">
      <Sidebar view={view} onNav={nav} onOpenSettings={() => openSettings()} />
      <main className={`stage view-${view}`}>
        {view === 'dialog' && <Speak openSettings={openSettings} />}
        {view === 'map' && <Home />}
        {view === 'review' && <Review />}
        {view === 'help' && <Help />}
        {view === 'report' && <Report />}
        {view === 'doc' && <Doc />}
        {view === 'metrics' && <Metrics />}
      </main>
      {settingsOpen && <SettingsModal tab={settingsTab} onClose={() => setSettingsOpen(false)} />}
      <ToastHost />
    </div>
  )
}

// 全局 toast 宿主：消费 SettingsModal 等组件的 'hsk:toast' 事件（复用 .toast 样式）
function ToastHost() {
  const [msg, setMsg] = useState('')
  useEffect(() => {
    let timer: any
    const f = (e: Event) => {
      const detail = (e as CustomEvent).detail
      if (detail == null) return
      setMsg(String(detail))
      window.clearTimeout(timer)
      timer = window.setTimeout(() => setMsg(''), 2200)
    }
    window.addEventListener('hsk:toast', f)
    return () => { window.removeEventListener('hsk:toast', f); window.clearTimeout(timer) }
  }, [])
  return <div className={`toast${msg ? ' show' : ''}`}>{msg}</div>
}

export default function App() {
  return (
    <SessionProvider>
      <Shell />
    </SessionProvider>
  )
}