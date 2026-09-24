// pages/Home.tsx · 图谱首页：构式晋升 + 复习队列（消费 GET /api/graph nodes/queue）
import { useEffect, useState } from 'react'
import { getJson } from '../lib/api'
import QuotaBar from '../components/QuotaBar'

interface GNode {
  id: string
  knowledge_point?: string
  level?: string
  mastery?: number
  error_count?: number
  positive_count?: number
  next_review_at?: number | null
  status?: string
}

interface QueueItem { kp_id: string; priority: number; node?: GNode }

interface GraphSnapshot {
  nodes: Record<string, GNode>
  queue: QueueItem[]
}

export default function Home() {
  const [g, setG] = useState<GraphSnapshot | null>(null)
  const [err, setErr] = useState('')

  useEffect(() => {
    getJson<GraphSnapshot>('/api/graph')
      .then(setG)
      .catch((e) => setErr(String(e)))
  }, [])

  if (err) return <div className="panel warn">图谱加载失败：{err}</div>
  if (!g) return <div className="panel">载入中…</div>

  const nodes = Object.values(g.nodes || {})
  const promoted = [...nodes].sort(
    (a, b) => (b.mastery ?? 0) - (a.mastery ?? 0) || (a.error_count ?? 0) - (b.error_count ?? 0),
  )

  return (
    <div className="grid-2">
      <QuotaBar />
      <section className="panel">
        <h2>构式晋升</h2>
        {promoted.length === 0 && <p className="muted">还没掌握的知识点，先在「开口说」练两轮。</p>}
        <table className="tbl">
          <thead>
            <tr><th>知识点</th><th>等级</th><th>掌握度</th><th>错/对</th></tr>
          </thead>
          <tbody>
            {promoted.map((n) => (
              <tr key={n.id}>
                <td>{n.knowledge_point || n.id}</td>
                <td>{n.level || '—'}</td>
                <td>
                  <span className="bar"><span className="fill" style={{ width: `${Math.round((n.mastery ?? 0) * 100)}%` }} /></span>
                </td>
                <td>{n.error_count ?? 0} / {n.positive_count ?? 0}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="panel">
        <h2>复习队列</h2>
        {(g.queue || []).length === 0 && <p className="muted">复习队列是空的。</p>}
        <ol className="queue">
          {(g.queue || []).map((it) => (
            <li key={it.kp_id}>
              <a href="#/review" className="q-kp">{it.node?.knowledge_point || it.kp_id}</a>
              <span className="q-pri">优先级 {it.priority}</span>
              <span className="q-lv">{it.node?.level || '未知'}</span>
            </li>
          ))}
        </ol>
      </section>
    </div>
  )
}