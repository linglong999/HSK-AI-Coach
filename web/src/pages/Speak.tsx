// pages/Speak.tsx · 叙事场景会话：fetch 流式消费 /api/dialog（POST SSE）
import { useEffect, useRef, useState } from 'react'
import { streamDialog } from '../lib/sse'
import QuotaBar from '../components/QuotaBar'

interface Line { role: 'user' | 'assistant'; text: string; meta?: string }

function textOf(payload: any): string {
  if (!payload) return ''
  if (typeof payload === 'string') return payload
  return payload.text || payload.content || payload.feedback || ''
}

function newConvId(): string {
  const saved = sessionStorage.getItem('hsk_conv')
  if (saved) return saved
  const id = `web-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
  sessionStorage.setItem('hsk_conv', id)
  return id
}

export default function Speak() {
  const [convId] = useState(newConvId)
  const [lines, setLines] = useState<Line[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [banner, setBanner] = useState<string | null>(null)
  const [err, setErr] = useState('')
  const bottomRef = useRef<HTMLDivElement>(null)
  const acRef = useRef<AbortController | null>(null)

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [lines])

  const send = async () => {
    const text = input.trim()
    if (!text || busy) return
    setInput('')
    setErr('')
    setBanner(null)
    setLines((ls) => [...ls, { role: 'user', text }])
    setBusy(true)

    acRef.current = streamDialog(
      '/api/dialog',
      { text, conversation_id: convId },
      {
        onIntercept: (d) => setBanner(d.msg || `能量提示：剩余 ${d.energy_left}`),
        onMessage: (card) => {
          const t = textOf(card?.payload)
          if (t) setLines((ls) => [...ls, { role: 'assistant', text: t, meta: `${card.kind}${card.ok ? ' ✓' : ''}` }])
        },
        onError: (d) => {
          setErr(d.msg || (d.error ? JSON.stringify(d.error) : '请求失败'))
          if (d.intercept) setBanner(d.intercept.msg)
          setBusy(false)
        },
        onDone: (d) => {
          const t = textOf(d)
          if (t && !lines.some((l) => l.text === t)) {
            setLines((ls) => [...ls, { role: 'assistant', text: t }])
          }
          if (d?.intercept) setBanner(d.intercept.msg)
          setBusy(false)
        },
      },
    )
  }

  const stop = () => { acRef.current?.abort(); setBusy(false) }

  return (
    <div className="speak">
      <QuotaBar sessionId={convId} />
      <div className="chat">
        <p className="muted">在一个场景里和你聊几句，教练逐句给反馈。</p>
        {banner && <div className="banner">{banner}</div>}
        {err && <div className="panel warn">{err}</div>}
        {lines.length === 0 && (
          <button className="btn" onClick={send}>来一次吧（说“你好”）</button>
        )}
        {lines.map((l, i) => (
          <div key={i} className={`line ${l.role}`}>
            <span className="who">{l.role === 'user' ? '你' : '教练'}</span>
            <span className="txt">{l.text}</span>
            {l.meta && <span className="meta">{l.meta}</span>}
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
      <div className="inputrow">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') send() }}
          placeholder={busy ? '教练在回复…' : '试着说一句中文'}
          disabled={busy}
        />
        {busy
          ? <button className="btn" onClick={stop}>停止</button>
          : <button className="btn primary" onClick={send} disabled={busy}>发送</button>}
      </div>
    </div>
  )
}