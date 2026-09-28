// pages/Speak.tsx · 叙事场景会话：fetch 流式消费 /api/dialog（POST SSE）
// 批次2·对话富交互：trace 成果卡（note-c 知识点链接）→ why 折叠 → 星级评分 → cfgbar。
// 母题C「书写感」：assistant 纯文本经 TypingText 逐笔浮现，打完原位替换为 note-c 静态链接。
// A 面·朱笔批注：identify_errors 卡到来即在原句上落批注（role='recog'），防刷屏分层。
import { useCallback, useEffect, useRef, useState } from 'react'
import { streamDialog } from '../lib/sse'
import { api } from '../lib/api'
import { useSessions } from '../lib/session'
import { type Graph } from '../lib/graph'
import { t } from '../lib/i18n'
import { takePendingMessage } from '../lib/outbox'
import QuotaBar from '../components/QuotaBar'
import TypingText from '../components/TypingText'
import AnnotatedSentence from '../components/AnnotatedSentence'
import {
  IdentifyCard, ExplainCard, VerifyCard, LookupCard, ReviewCard,
  UnitCard, SearchCard, RetrieveCard, ParseCard, Annotated, BranchTools, type CardCtx,
} from '../components/Cards'

type Line =
  | { role: 'assistant'; text: string; meta?: string; why?: any[] }
  | { role: 'user'; text: string; badge?: string }
  | { role: 'recog'; text: string; errors: unknown; uncertain: unknown }
  | { role: 'trace'; name: string; res: any }

/* ---- cfgBar 顶部提示（去配置 → 打开设置弹窗 llm 标签） ---- */
function CfgBar({ providers, onConfigure }: { providers: number; onConfigure: () => void }) {
  const [hidden, setHidden] = useState(false)
  if (providers > 0 || hidden) return null
  return (
    <div className="cfgbar">
      <span className="cb-ico">🔑</span>
      <span className="cb-t">{t('cfgTitle')}</span>
      <span className="cb-sub">{t('cfgSub')}</span>
      <button className="cb-go" onClick={onConfigure}>{t('cfgBtn')}</button>
      <button className="cb-x" aria-label="close" onClick={() => setHidden(true)}>✕</button>
    </div>
  )
}

/* ---- why 折叠块：assistant 回复尾部的"为什么？"折叠（fragment→correction→reason→l1） ---- */
function WhyBlock({ items, ctx }: { items: any[]; ctx: CardCtx }) {
  const [open, setOpen] = useState(false)
  if (!items || !items.length) return null
  return (
    <div className="why-block">
      <button className={`why-toggle${open ? ' open' : ''}`} type="button" onClick={() => setOpen((o) => !o)}>
        <span className="chev">▾</span><span>{t('whyBtn')}</span>
      </button>
      {open && (
        <div className="why-body">
          {items.map((it, i) => (
            <div className="why-item" key={i}>
              <div className="why-q">
                <span>{it?.fragment || ''}</span>
                <span className="why-ar">→</span>
                <span>{it?.correction || ''}</span>
              </div>
              {it?.reason ? <div className="why-p">{it.reason}</div> : null}
              {it?.l1 ? <div className="why-l1">{t('whyL1')}{it.l1}</div> : null}
              {it?.kp_id ? <BranchTools kpId={it.kp_id} cls="why-tools" ctx={ctx} /> : null}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

/* ---- 星级评分卡：每会话低频一次（sessionStorage），POST /api/feedback ---- */
function FeedbackCard({ onSubmit }: { onSubmit: (s: number) => void }) {
  const [done, setDone] = useState(0)
  return (
    <div className={`fb-card${done ? ' fb-submitted' : ''}`}>
      {done ? (
        <span className="fb-done">{t('fbDone').replace('{n}', '★'.repeat(done))}</span>
      ) : (
        <div className="fb-row">
          <span className="fb-ask">{t('fbAsk')}  {t('fbFirm')}</span>
          {[1, 2, 3, 4, 5].map((s) => (
            <button key={s} className="fb-star" type="button" title={`${s}★`} aria-label={`${s}★`} onClick={() => { onSubmit(s); setDone(s) }}>★</button>
          ))}
          <button className="fb-skip" type="button" onClick={() => setDone(-1)}>{t('fbSkip')}</button>
        </div>
      )}
    </div>
  )
}

function textOf(payload: any): string {
  if (!payload) return ''
  if (typeof payload === 'string') return payload
  return payload.text || payload.content || payload.feedback || ''
}

/* ---- trace.name → Cards 组件（隐藏内部工具） ---- */
function TraceCard({ name, res, ctx }: { name: string; res: any; ctx: CardCtx }) {
  switch (name) {
    case 'identify_errors': return <IdentifyCard res={res} ctx={ctx} />
    case 'explain_error': return <ExplainCard res={res} ctx={ctx} />
    case 'verify_retell': return <VerifyCard v={res} />
    case 'lookup_knowledge_point': return <LookupCard res={res} />
    case 'get_review_queue': return <ReviewCard res={res} ctx={ctx} />
    case 'generate_unit': return <UnitCard out={res} />
    case 'web_search': return <SearchCard res={res} />
    case 'retrieve_corpus': return <RetrieveCard res={res} />
    case 'parse_document': return <ParseCard res={res} />
    default: return null
  }
}

/* ---- assistant 文本行：先书写感打字，打完原位替换为 note-c 静态链接 ---- */
function AssistantText({ text, ctx, onProgress }: { text: string; ctx: CardCtx; onProgress?: () => void }) {
  const [done, setDone] = useState(false)
  if (!text) return null
  if (done) return <span className="txt"><Annotated text={text} ctx={ctx} /></span>
  return (
    <TypingText text={text} onProgress={onProgress} onDone={() => setDone(true)} />
  )
}

export default function Speak({ openSettings }: { openSettings?: (tab?: string) => void }) {
  const { conversationId: convId, refresh } = useSessions()
  const [lines, setLines] = useState<Line[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [banner, setBanner] = useState<string | null>(null)
  const [err, setErr] = useState('')
  const [graph, setGraph] = useState<Graph | null>(null)
  const [provCount, setProvCount] = useState(0)
  const [toastMsg, setToastMsg] = useState<string | null>(null)
  const [showFb, setShowFb] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)
  const acRef = useRef<AbortController | null>(null)
  const lastSentRef = useRef('')
  const recogPushedRef = useRef(false)
  const sendRef = useRef<((prefill?: string) => void) | null>(null)
  const toastTimer = useRef<number | null>(null)

  const showToast = useCallback((msg: string) => {
    setToastMsg(msg)
    if (toastTimer.current) window.clearTimeout(toastTimer.current)
    toastTimer.current = window.setTimeout(() => setToastMsg(null), 2200)
  }, [])

  // 图谱 + providers（note-c 定位 & cfgbar）
  useEffect(() => {
    api<Graph>('/api/graph').then(setGraph).catch(() => setGraph(null))
    api<{ providers?: any[] }>('/api/providers')
      .then((d) => setProvCount(Array.isArray(d.providers) ? d.providers.length : 0))
      .catch(() => setProvCount(0))
  }, [])

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [lines])

  const onLocate = useCallback(() => { location.hash = '#/map' }, [])

  useEffect(() => {
    sendRef.current = (prefill?: string, badge?: string) => { void send(prefill, badge) }
  })

  const cardCtx: CardCtx = {
    graph,
    onLocate,
    onBranch: (prompt) => sendRef.current?.(prompt),
    toast: showToast,
  }

  // 会话切换 → 重置画布并从 /api/conversation 深链恢复文本/why（卡片不随会话持久，why 折叠可重建）
  // Doc 阅读材料 outbox 在恢复完成后消费，避免恢复 effect 覆盖刚 append 的用户气泡（badge 丢失）
  useEffect(() => {
    let alive = true
    setLines([]); setErr(''); setBanner(null); setShowFb(false)
    const fbKey = 'askedFeedback:' + convId
    if (!sessionStorage.getItem(fbKey)) setShowFb(true)
    ;(async () => {
      try {
        await api('/api/graph').then(setGraph).catch(() => {})
        const c = await api<{ messages?: Array<{ role?: string; content?: string; text?: string; why?: any[] }> }>('/api/conversation?id=' + encodeURIComponent(convId))
        const msgs = c?.messages || []
        if (alive && msgs.length) {
          const restored: Line[] = []
          for (const m of msgs) {
            if (m.role === 'user') { restored.push({ role: 'user', text: String(m.content ?? '') }); continue }
            const why = Array.isArray(m.why) ? m.why : []
            restored.push({ role: 'assistant', text: String(m.content ?? m.text ?? ''), why })
          }
          setLines(restored)
        }
      } catch { /* 恢复失败不阻塞 */ }
      const p = takePendingMessage()
      if (alive && p?.text) void send(p.text, p.badge)
    })()
    return () => { alive = false }
  }, [convId]) // eslint-disable-line react-hooks/exhaustive-deps

  const send = async (prefill?: string, badge?: string) => {
    const text = (prefill ?? input).trim()
    if (!text || busy) return
    setInput('')
    setErr('')
    setBanner(null)
    lastSentRef.current = text
    recogPushedRef.current = false
    setLines((ls) => [...ls, { role: 'user', text, badge }])
    setBusy(true)
    let fbPrompted = false

    acRef.current = streamDialog(
      '/api/dialog',
      { text, conversation_id: convId },
      {
        onIntercept: (d) => setBanner(d.msg || `能量提示：剩余 ${d.energy_left}`),
        onMessage: (card) => {
          const name = card?.payload?.name || ''
          // A 面：identify 卡 → 朱笔批注（卡片独立下发，不重复走 trace）
          if (!recogPushedRef.current && name === 'identify_errors' && lastSentRef.current) {
            recogPushedRef.current = true
            const res = card.payload.result || {}
            setLines((ls) => [...ls, { role: 'recog', text: lastSentRef.current, errors: res.errors, uncertain: res.uncertain }])
            return
          }
          const maybeText = textOf(card?.payload)
          // 成果卡：非纯文本且 ok → trace 卡；(名前缀为 trace 技能名但不混入识别卡)
          if (name && name !== 'identify_errors' && !maybeText && card?.ok !== false) {
            const res = card.payload.result ?? card.payload
            setLines((ls) => [...ls, { role: 'trace', name, res }])
            return
          }
          if (maybeText) {
            setLines((ls) => [...ls, { role: 'assistant', text: maybeText, meta: `${card?.kind || ''}${card?.ok ? ' ✓' : ''}` }])
          }
        },
        onError: (d) => {
          setErr(d.msg || (d.error ? JSON.stringify(d.error) : '请求失败'))
          if (d.intercept) setBanner(d.intercept.msg)
          setBusy(false)
        },
        onDone: (d) => {
          const t = textOf(d)
          if (t) {
            setLines((ls) => [...ls, {
              role: 'assistant', text: t,
              why: Array.isArray(d?.why) ? d.why : undefined,
            }])
          }
          if (d?.intercept) setBanner(d.intercept.msg)
          setBusy(false)
          if (!fbPrompted && !sessionStorage.getItem('askedFeedback:' + convId)) {
            fbPrompted = true
            sessionStorage.setItem('askedFeedback:' + convId, '1')
            setShowFb(true)
          }
          void api('/api/graph').then(setGraph).catch(() => {})
          void refresh()
        },
      },
    )
  }

  const stop = () => { acRef.current?.abort(); setBusy(false) }

  const fbSubmit = useCallback((score: number) => {
    api('/api/feedback', { conversation_id: convId, score }).catch(() => showToast(t('opFailed')))
    if (score === 1) showToast(t('fbLow'))
  }, [convId, showToast])

  return (
    <div className="speak">
      <QuotaBar sessionId={convId} />
      <div className="chat">
        <CfgBar providers={provCount} onConfigure={() => openSettings?.('llm')} />
        <p className="muted">在一个场景里和你聊几句，教练逐句给反馈。</p>
        {banner && <div className="banner">{banner}</div>}
        {err && <div className="panel warn">{err}</div>}
        {lines.length === 0 && !busy && (
          <button className="btn" onClick={() => void send()}>来一次吧（说“你好”）</button>
        )}
        {lines.map((l, i) => (
          <div key={i} className={`line ${l.role === 'user' ? 'user' : l.role === 'recog' ? 'recog' : l.role === 'trace' ? 'assistant' : 'assistant'}`}>
            {l.role === 'recog' ? (
              <AnnotatedSentence text={l.text} errors={l.errors as any[]} uncertain={l.uncertain as any[]} onLocate={cardCtx.onLocate} />
            ) : l.role === 'trace' ? (
              <TraceCard name={l.name} res={l.res} ctx={cardCtx} />
            ) : (
              <>
                <span className="who">{l.role === 'user' ? '你' : '教练'}</span>
                {l.role === 'assistant'
                  ? <AssistantText text={l.text} ctx={cardCtx} onProgress={() => bottomRef.current?.scrollIntoView({ behavior: 'smooth' })} />
                  : <span className="txt">{l.text}{l.badge ? <span className="badge">{l.badge}</span> : null}</span>}
                {l.role === 'assistant' && l.meta && <span className="meta">{l.meta}</span>}
                {l.role === 'assistant' && l.why && l.why.length ? <WhyBlock items={l.why} ctx={cardCtx} /> : null}
              </>
            )}
          </div>
        ))}
        {showFb && <div className="line assistant"><FeedbackCard onSubmit={fbSubmit} /></div>}
        <div ref={bottomRef} />
      </div>
      <div className="inputrow">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') void send() }}
          placeholder={busy ? '教练在回复…' : '试着说一句中文'}
          disabled={busy}
        />
        {busy
          ? <button className="btn" onClick={stop}>停止</button>
          : <button className="btn primary" onClick={() => void send()} disabled={busy}>发送</button>}
      </div>
      {toastMsg && <div className="toast show">{toastMsg}</div>}
    </div>
  )
}