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
import OpeningCard, { type Opening, type MemoryItem } from '../components/OpeningCard'
import ScenePicker, { sceneTitle, type Scene } from '../components/ScenePicker'
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
  const { conversationId: convId, refresh, profile, memoryVersion } = useSessions()
  const [sceneId, setSceneId] = useState('')
  const [incognito, setIncognito] = useState(false)
  const [ready, setReady] = useState(false)
  const [scenes, setScenes] = useState<Scene[]>([])
  const [opening, setOpening] = useState<Opening | null>(null)
  const openingRequests = useRef(new Map<string, Promise<Opening>>())
  const sessionRef = useRef({ id: convId, ready: false, sceneId: '', incognito: false, started: false })
  const currentId = useRef(convId)
  currentId.current = convId
  const sendLock = useRef(false)
  const sendIdentity = useRef(0)
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
    let alive = true
    api<{ scenes: Scene[] }>('/api/scenarios').then((d) => { if (alive) setScenes(d.scenes || []) }).catch(() => {})
    return () => { alive = false }
  }, [])

  useEffect(() => {
    let alive = true
    if (memoryVersion && opening) {
      api<{ items: MemoryItem[] }>('/api/memory?status=candidate').then((d) => {
        if (alive && currentId.current === convId) setOpening((prev) => prev ? { ...prev, candidates: d.items } : prev)
      }).catch(() => {})
    }
    return () => { alive = false }
  }, [memoryVersion, convId]) // 变更通知只刷新列表，不重新请求 opening

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

  // 会话切换 → 按实时顺序恢复 user / recog / trace / assistant+why
  // Doc 阅读材料 outbox 在恢复完成后消费，避免恢复 effect 覆盖刚 append 的用户气泡（badge 丢失）
  useEffect(() => {
    let alive = true
    acRef.current?.abort(); sendIdentity.current++; sendLock.current = false
    setBusy(false); setReady(false); setOpening(null); setSceneId('')
    const localIncognito = sessionStorage.getItem('incognito:' + convId) === 'true'
    setIncognito(localIncognito)
    sessionRef.current = { id: convId, ready: false, sceneId: '', incognito: localIncognito, started: false }
    setLines([]); setErr(''); setBanner(null); setShowFb(false)
    const fbKey = 'askedFeedback:' + convId
    if (!sessionStorage.getItem(fbKey)) setShowFb(true)
    ;(async () => {
      try {
        const c = await api<{ scene_id: string; incognito: boolean; messages?: Array<{ role?: string; content?: string; text?: string; why?: any[]; cards?: any[]; identified?: { errors?: any[]; uncertain?: any[] } }> }>('/api/conversation?id=' + encodeURIComponent(convId))
        if (!alive || currentId.current !== convId) return
        const msgs = c?.messages || []
        const restoredIncognito = c.incognito || (!msgs.length && localIncognito)
        const restoredScene = c.scene_id || ''
        if (restoredIncognito) sessionStorage.setItem('incognito:' + convId, 'true')
        else sessionStorage.removeItem('incognito:' + convId)
        setIncognito(restoredIncognito); setSceneId(restoredScene); setReady(true)
        sessionRef.current = { id: convId, ready: true, sceneId: restoredScene, incognito: restoredIncognito, started: !!msgs.length }
        if (alive && msgs.length) {
          const restored: Line[] = []
          const consumed = new Set<number>()
          for (let i = 0; i < msgs.length; i++) {
            const m = msgs[i]
            if (m.role !== 'assistant') continue
            let userIndex = -1
            for (let j = i - 1; j >= 0; j--) {
              if (msgs[j].role === 'user' && !consumed.has(j)) { userIndex = j; break }
            }
            if (userIndex >= 0) {
              consumed.add(userIndex)
              const userText = String(msgs[userIndex].content ?? msgs[userIndex].text ?? '')
              restored.push({ role: 'user', text: userText })
              const identified = m.identified || {}
              if ((identified.errors?.length || 0) || (identified.uncertain?.length || 0)) {
                restored.push({ role: 'recog', text: userText, errors: identified.errors || [], uncertain: identified.uncertain || [] })
              }
              for (const card of (Array.isArray(m.cards) ? m.cards : [])) {
                if (card?.name !== 'identify_errors') restored.push({ role: 'trace', name: card?.name, res: card?.result })
              }
            }
            const why = Array.isArray(m.why) ? m.why : []
            restored.push({ role: 'assistant', text: String(m.content ?? m.text ?? ''), why })
          }
          msgs.forEach((m, i) => { if (m.role === 'user' && !consumed.has(i)) restored.push({ role: 'user', text: String(m.content ?? m.text ?? '') }) })
          setLines(restored)
        }
        const p = takePendingMessage()
        if (p?.text) void send(p.text, p.badge, { sceneId: restoredScene, incognito: restoredIncognito })
        if (!msgs.length && !restoredIncognito && !p?.text) {
          let request = openingRequests.current.get(convId)
          if (!request) {
            request = api<Opening>('/api/memory/opening', { conversation_id: convId })
            openingRequests.current.set(convId, request)
          }
          request.then(async (data) => {
            if (!alive || currentId.current !== convId) return
            // 复用opening请求，但候选状态可能已在设置页改变；读取最新投影，不再触发提取。
            if (data.enabled) {
              const latest = await api<{ items: MemoryItem[] }>('/api/memory?status=candidate')
              data = { ...data, candidates: latest.items }
            }
            if (alive && currentId.current === convId) setOpening(data)
          }).catch(() => { if (alive && currentId.current === convId) showToast(t('opFailed')) })
        }
      } catch { if (alive && currentId.current === convId) setErr(t('memoryRestoreFailed')) }
    })()
    return () => { alive = false; acRef.current?.abort(); sendIdentity.current++ }
  }, [convId]) // eslint-disable-line react-hooks/exhaustive-deps

  const send = async (prefill?: string, badge?: string, context?: { sceneId: string; incognito: boolean }) => {
    const text = (prefill ?? input).trim()
    const session = sessionRef.current
    if (!text || sendLock.current || !session.ready || session.id !== convId || currentId.current !== convId) return
    sendLock.current = true
    session.started = true
    const identity = ++sendIdentity.current
    const valid = () => currentId.current === convId && sendIdentity.current === identity
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
      { text, conversation_id: convId, scene_id: context?.sceneId ?? session.sceneId, incognito: context?.incognito ?? session.incognito },
      {
        onIntercept: (d) => { if (valid()) setBanner(d.msg || `能量提示：剩余 ${d.energy_left}`) },
        onMessage: (card) => {
          if (!valid()) return
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
          if (!valid()) return
          setErr(d.msg || (d.error ? JSON.stringify(d.error) : '请求失败'))
          if (d.intercept) setBanner(d.intercept.msg)
          setBusy(false)
          sendLock.current = false
        },
        onDone: (d) => {
          if (!valid()) return
          const t = textOf(d)
          if (t) {
            setLines((ls) => [...ls, {
              role: 'assistant', text: t,
              why: Array.isArray(d?.why) ? d.why : undefined,
            }])
          }
          if (d?.intercept) setBanner(d.intercept.msg)
          setBusy(false)
          sendLock.current = false
          if (!fbPrompted && !sessionStorage.getItem('askedFeedback:' + convId)) {
            fbPrompted = true
            sessionStorage.setItem('askedFeedback:' + convId, '1')
            setShowFb(true)
          }
          void api('/api/graph').then((g) => { if (valid()) setGraph(g as Graph) }).catch(() => {})
          void refresh()
        },
      },
    )
  }

  const stop = () => { acRef.current?.abort(); sendIdentity.current++; sendLock.current = false; setBusy(false) }

  const startScene = (id: string) => {
    if (!sessionRef.current.ready || sessionRef.current.started || sendLock.current) return
    const scene = scenes.find((s) => s.scene_id === id)
    if (!scene?.seed) { showToast(t('memorySceneUnavailable')); return }
    setSceneId(id)
    sessionRef.current.sceneId = id
    void send(scene.seed, undefined, { sceneId: id, incognito: sessionRef.current.incognito })
  }
  const activeScene = scenes.find((s) => s.scene_id === sceneId)

  const fbSubmit = useCallback((score: number) => {
    api('/api/feedback', { conversation_id: convId, score }).catch(() => showToast(t('opFailed')))
    if (score === 1) showToast(t('fbLow'))
  }, [convId, showToast])

  return (
    <div className={`speak${incognito ? ' incognito-chat' : ''}`}>
      <QuotaBar sessionId={convId} />
      <div className="chat">
        <CfgBar providers={provCount} onConfigure={() => openSettings?.('llm')} />
        {incognito && <div className="incognito-label"><strong>{t('memoryIncognitoLabel')}</strong><small>{t('memoryIncognitoNote')}</small></div>}
        {activeScene && <p className="scene-label">{t('scenePickActive')} {sceneTitle(activeScene)}</p>}
        <p className="muted">在一个场景里和你聊几句，教练逐句给反馈。</p>
        {banner && <div className="banner">{banner}</div>}
        {err && <div className="panel warn">{err}</div>}
        {ready && lines.length === 0 && !busy && (
          <button className="btn" onClick={() => void send('你好')}>来一次吧（说“你好”）</button>
        )}
        {ready && !lines.length && !sceneId && <ScenePicker scenes={scenes} disabled={busy || sessionRef.current.started} onPick={(scene) => startScene(scene.scene_id)} />}
        {ready && !incognito && profile.memory_enabled !== false && opening && <OpeningCard key={convId}
          data={opening} disabled={busy || sessionRef.current.started} onResume={startScene} onManage={() => openSettings?.('memory')}
          toast={(msg) => { if (currentId.current === convId) showToast(msg) }}
          onChange={(update) => { if (currentId.current === convId) setOpening((prev) => prev ? update(prev) : prev) }} />}
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
          disabled={busy || !ready}
        />
        {busy
          ? <button className="btn" onClick={stop}>停止</button>
          : <button className="btn primary" onClick={() => void send()} disabled={busy || !ready}>发送</button>}
      </div>
      {toastMsg && <div className="toast show">{toastMsg}</div>}
    </div>
  )
}
