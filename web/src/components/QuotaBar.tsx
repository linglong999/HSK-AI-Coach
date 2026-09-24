// components/QuotaBar.tsx · 日能量条 + 会话回合计数（GET /api/quota，置首屏）
import { useEffect, useState } from 'react'
import { getJson } from '../lib/api'

interface Quota {
  energy_left: number
  daily_total: number
  est_cost: number
  round_count: number
  reset_at: string
}

export default function QuotaBar({ sessionId }: { sessionId?: string }) {
  const [q, setQ] = useState<Quota | null>(null)
  const [err, setErr] = useState('')

  useEffect(() => {
    let live = true
    const load = () => {
      const qs = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ''
      getJson<Quota>(`/api/quota${qs}`)
        .then((d) => { if (live) setQ(d) })
        .catch(() => { if (live) setErr('额度读取失败') })
    }
    load()
    return () => { live = false }
  }, [sessionId])

  if (err) return <div className="quota quota-err">{err}</div>
  if (!q) return <div className="quota quota-loading">载入中…</div>
  if (q.daily_total <= 0 || q.energy_left < 0) return <div className="quota">额度不限</div>

  const pct = Math.max(0, Math.min(100, (q.energy_left / q.daily_total) * 100))
  return (
    <div className="quota">
      <span className="quota-label">今日能量</span>
      <span className="quota-bar">
        <span className="quota-fill" style={{ width: `${pct}%` }} />
      </span>
      <span className="quota-num">{q.energy_left} / {q.daily_total}</span>
      <span className="quota-extra">
        预计每回合 ~{q.est_cost} · 本会话 {q.round_count} 回合
      </span>
      <span className="quota-reset">{q.reset_at}</span>
    </div>
  )
}