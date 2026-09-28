// components/Cards.tsx · 学习成果卡（忠实复刻 web_legacy renderTrace 卡片组）
// 驯化语义：卡片分支动作只沿图谱权威边（↗深挖 / →混淆边 / ↓待复习），无方向置灰。
import { t, tf, lang } from '../lib/i18n'
import { Graph, typeClass, neighborsOf, inQueue, nodeName } from '../lib/graph'

export interface CardCtx {
  graph: Graph | null
  onLocate: (kpId: string) => void
  onBranch: (prompt: string, badge?: string) => void
  toast: (msg: string) => void
}

const COLON = () => (lang() === 'zh' ? '：' : ': ')
const esc = (s: any) => String(s == null ? '' : s)
  .replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]!))

function safeUrl(u: any): string {
  const s = String(u || '').trim()
  return /^https?:\/\//i.test(s) ? s : '#'
}

/* ---- 分支动作条（↗→↓，锚定图谱权威边） ---- */
export function BranchTools({ kpId, cls, ctx }: { kpId: string; cls?: string; ctx: CardCtx }) {
  cls = cls || 'card-tools'
  const hasNode = !!(ctx.graph?.nodes && ctx.graph.nodes[kpId])
  const nbs = neighborsOf(ctx.graph, kpId)
  const queued = inQueue(ctx.graph, kpId)
  return (
    <div className={cls}>
      {[
        { g: '↗', on: hasNode, title: hasNode ? t('digTitle') : t('digOff'), act: () => ctx.onBranch(tf('digPrompt', { name: nodeName(ctx.graph, kpId) }), t('digBadge') + nodeName(ctx.graph, kpId)) },
        { g: '→', on: nbs.length > 0, title: nbs.length ? t('adjTitle') : t('adjOff'), act: () => ctx.toast(t('poNeighbors')) },
        { g: '↓', on: queued, title: queued ? t('rvTitle') : t('rvOff'), act: () => ctx.onBranch(tf('rvPrompt', { name: nodeName(ctx.graph, kpId) }), t('rvBadge') + nodeName(ctx.graph, kpId)) },
      ].map((b) => (
        <button key={b.g} className={`tool ${b.on ? '' : 'off'}`} title={b.title} onClick={(ev) => { ev.stopPropagation(); if (b.on) b.act() }}>{b.g}</button>
      ))}
    </div>
  )
}

/* ---- 偏误识别卡 / 正确卡 + 低置信条 ---- */
export function IdentifyCard({ res, ctx }: { res: any; ctx: CardCtx }) {
  const errors = Array.isArray(res.errors) ? res.errors : []
  const uncertain = Array.isArray(res.uncertain) ? res.uncertain : []
  return (
    <>
      {errors.map((err: any, i: number) => {
        const kpId = err.knowledge_point_id || ''
        const gw = err.graph_write || {}
        const inGraph = kpId && ctx.graph?.nodes && ctx.graph.nodes[kpId]
        return (
          <div className="card" key={i}>
            <div className="cardlabel">{t('lblIdentify')}</div>
            <div className="head">
              <span className={`tag ${typeClass(err.type)}`}>{esc(err.type || t('errTag'))}</span>
              <span className="frag">{esc(err.fragment || '')}</span>
              <span className="corr">→ {esc(err.correction || '')}</span>
            </div>
            <div className="meta-row">
              <span>{t('graphQ')}{COLON()}{esc(gw.status || '?')}{gw.kp_id ? ' · ' + esc(gw.kp_id) : ''}</span>
              <span>{t('conf')} {err.confidence != null ? Number(err.confidence).toFixed(2) : '?'}</span>
            </div>
            {inGraph && (
              <div className="kp-row">
                <span className="kp" onClick={() => ctx.onLocate(kpId)}>· {esc(ctx.graph!.nodes[kpId].knowledge_point || kpId)}</span>
              </div>
            )}
            {kpId && <BranchTools kpId={kpId} ctx={ctx} />}
          </div>
        )
      })}
      {!errors.length && (
        <div className="card clean">
          <div className="clean-title"><span>✓</span><span>{t('cleanTitle')}</span></div>
          <div className="clean-sub">{t('cleanSub')}{uncertain.length ? tf('cleanSubUc', { n: uncertain.length }) : ''}。</div>
        </div>
      )}
      {!!errors.length && uncertain.length ? (
        <div className="uc-strip">{tf('ucStrip', { n: uncertain.length })}{uncertain.map((u: any) => esc(u.fragment || '')).join('、')}</div>
      ) : null}
      {(Array.isArray(res.degraded) ? res.degraded : []).map((d: string, i: number) => (
        <div className="note warn" key={'d' + i}>{t('degradedIdentify')}{esc(d)}</div>
      ))}
    </>
  )
}

/* ---- 费曼讲解卡 ---- */
export function ExplainCard({ res, ctx }: { res: any; ctx: CardCtx }) {
  const kps = Array.isArray(res.key_points) ? res.key_points : []
  if (!res.explanation && !kps.length) {
    if (res._error) return <div className="note warn">{t('explainFail')}{esc(res._error)}</div>
    return null
  }
  return (
    <div className="card">
      <div className="cardlabel">{t('lblExplain')}</div>
      <div className="expl-text"><Annotated text={res.explanation || ''} ctx={ctx} /></div>
      {kps.length ? (
        <div className="kp-row">{kps.map((k: any, i: number) => <span className="kp-plain" key={i}>{esc(typeof k === 'string' ? k : (k.text || ''))}</span>)}</div>
      ) : null}
      {res._degraded ? <div className="note warn" style={{ marginTop: 8 }}>{t('explainDegraded')}</div> : null}
    </div>
  )
}

/* ---- 复述验证卡 ---- */
export function VerifyCard({ v }: { v: any }) {
  v = v || {}
  const verdict = v.verdict || 'partial'
  const pts = Array.isArray(v.point_judgements) ? v.point_judgements : []
  const total = v.total_points != null ? v.total_points : pts.length
  const covered = v.covered_points != null ? v.covered_points : pts.filter((p: any) => p.is_covered).length
  const ratio = v.coverage_ratio != null ? Number(v.coverage_ratio) : 0
  return (
    <div className={`card verify v-${verdict}`}>
      <div className="cardlabel">{t('lblVerify')}</div>
      <div className="v-head">
        <span className={`v-badge ${verdict}`}>{verdict === 'pass' ? t('vPass') : verdict === 'partial' ? t('vPartial') : t('vFail')}</span>
        <span className="v-cov">{t('vCov')} {covered}/{total} · {t('vRatio')} {(ratio * 100).toFixed(0)}%</span>
      </div>
      <div className="v-bar"><div style={{ width: `${Math.min(ratio * 100, 100)}%` }} /></div>
      {pts.length ? (
        <div className="v-points">
          {pts.map((p: any, i: number) => (
            <div className={`v-point ${p.is_covered ? 'hit' : 'miss'}`} key={i}>
              <span className="mk">{p.is_covered ? '✓' : '✗'}</span>
              <span>{esc(p.text || '')}
                {p.is_covered && p.evidence ? <div className="ev">{t('evidenceAt')}{esc(p.evidence)}</div> : null}
              </span>
            </div>
          ))}
        </div>
      ) : null}
      {v.flowery_but_empty ? <div className="v-hollow">{t('vHollow')}</div> : null}
      {v.degraded ? <div className="v-hollow">{t('vDegraded')}</div> : null}
      {v.feedback ? <div className="v-feedback">{esc(v.feedback)}</div> : null}
      {v.action === 'retry_simpler' ? <div className="v-action a-retry">{t('aRetry')}</div> : null}
      {v.action === 'suggest_teacher' ? <div className="v-action a-teacher">{tf('aTeacher', { n: v.consecutive_fail || '?' })}</div> : null}
    </div>
  )
}

/* ---- 知识点卡 ---- */
export function LookupCard({ res }: { res: any }) {
  const results = Array.isArray(res.results) ? res.results : []
  if (!results.length) { if (res._error) return <div className="note plain">{t('lookupErr')}{esc(res._error)}</div>; return null }
  return (
    <div className="card">
      <div className="cardlabel">{t('lblLookup')} · {results.length}</div>
      {results.map((r: any, i: number) => {
        const nm = r.knowledge_point || r.title || r.name || r.kp_id || r.id || t('unnamed')
        const sm = r.summary || r.description || r.definition || r.explanation || r.point || ''
        return (
          <div className="kp-li" key={i}>
            <span className="nm">{esc(nm)}</span>
            {r.level ? <span className="lv">{esc(r.level)}</span> : null}
            {sm ? <div className="sm">{esc(String(sm).slice(0, 160))}</div> : null}
          </div>
        )
      })}
    </div>
  )
}

/* ---- 复习卡 ---- */
export function ReviewCard({ res, ctx }: { res: any; ctx: CardCtx }) {
  const items = Array.isArray(res.items) ? res.items : []
  return (
    <div className="card">
      <div className="cardlabel">{t('lblReview')} · {res.count != null ? res.count : items.length} {t('items')}</div>
      {items.length ? (
        <div>
          {items.slice(0, 8).map((it: any, i: number) => {
            const n = it.node || {}
            return (
              <div className="rv-li" key={i} onClick={() => ctx.onLocate(it.kp_id || '')}>
                <span style={{ minWidth: 118, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{esc(n.knowledge_point || it.kp_id || '')}</span>
                <div className="bar"><div style={{ width: `${Math.min((it.priority || 0) * 10, 100)}%` }} /></div>
                <span className="rv-prio">{(it.priority || 0).toFixed(1)}</span>
              </div>
            )
          })}
        </div>
      ) : <div className="clean-sub">{t('noReview')}</div>}
    </div>
  )
}

/* ---- 生成单元卡（费曼讲解单元 / 巩固练习） ---- */
export function UnitCard({ out, label }: { out: any; label?: string }) {
  if (!out || out.ok !== true || !out.unit) {
    const diags = Array.isArray(out && out.diagnostics) ? out.diagnostics : []
    let body = (
      <>
        <div className="cardlabel" style={{ color: 'rgb(var(--sem-err))' }}>{t('genFail')}{label ? ' · ' + esc(label) : ''}</div>
        <div className="note err">{esc((out && out.message) || t('genNA'))}</div>
      </>
    )
    if (diags.length) {
      body = (
        <>
          {body}
          <div className="unit-sec">{tf('diagnostics', { n: (out && out.attempts) || 0 })}</div>
          <ul className="unit-li">{diags.map((d: any, i: number) => <li key={i}>{esc(d.code)} · {esc(d.subject)}{COLON()}{esc(d.message)}</li>)}</ul>
        </>
      )
    }
    return <div className="card">{body}</div>
  }
  const u = out.unit
  const kps = Array.isArray(u.keyPoints) ? u.keyPoints : []
  const forb = Array.isArray(u.forbidden_errors) ? u.forbidden_errors : []
  return (
    <div className="card unit">
      <div className="cardlabel">{esc(label || (u.type === 'practice' ? t('lblPractice') : t('lblUnit')))}</div>
      <div className="unit-title">{esc(u.title || '')}</div>
      {kps.length ? <><div className="unit-th">{t('unitKps')}</div><div className="unit-kp">{kps.map((k: string, i: number) => <span className="kp-plain" key={i}>{esc(k)}</span>)}</div></> : null}
      {u.task_kind ? <div className="unit-sec">{t('taskKind')}{COLON()}{esc(u.task_kind)} · {t('qCount')} {u.questionCount != null ? esc(u.questionCount) : '?'}</div> : null}
      {forb.length ? <>
        <div className="unit-th" style={{ color: 'rgb(var(--sem-warn))', marginTop: 10 }}>{t('forbidden')}</div>
        <ul className="unit-forbid">{forb.map((f: string, i: number) => <li key={i}>{esc(f)}</li>)}</ul>
      </> : null}
      {u.estimatedDuration ? <div className="unit-sec">{t('duration')} {esc(u.estimatedDuration)}s</div> : null}
    </div>
  )
}

/* ---- 参考来源卡 / 语料检索卡 / 材料解析卡（折叠） ---- */
export function SearchCard({ res }: { res: any }) {
  const results = Array.isArray(res.results) ? res.results : []
  if (!results.length) return null
  return (
    <details className="src">
      <summary style={{ color: 'rgb(var(--tx2))' }} dangerouslySetInnerHTML={{ __html: tf('lblSources', { n: results.length }) }} />
      <div className="src-list">
        {results.map((r: any, i: number) => (
          <div className="src-item" key={i}>
            <a href={safeUrl(r.link || r.url)} target="_blank" rel="noopener noreferrer">{esc(r.title || r.link || r.url || t('noTitle'))}</a>
            {r.snippet ? <span className="sn">{esc(String(r.snippet).slice(0, 200))}</span> : null}
          </div>
        ))}
      </div>
    </details>
  )
}
export function RetrieveCard({ res }: { res: any }) {
  const results = Array.isArray(res.results) ? res.results : []
  if (!results.length) return null
  const srcLabel = (): Record<string, string> => ({ knowledge_point: t('srcKp'), lexicon: t('srcLex'), graph: t('srcGraph') })
  return (
    <details className="src">
      <summary style={{ color: 'rgb(var(--tx2))' }} dangerouslySetInnerHTML={{ __html: tf('lblCorpus', { n: results.length }) }} />
      <div className="src-list">
        {results.map((r: any, i: number) => (
          <div className="src-item" key={i}>
            <span>{esc(r.knowledge_point || r.chunk_id || '')} · {esc(srcLabel()[r.source as string] || r.source || '')}{r.in_graph ? t('inGraph') : ''}</span>
            {r.text ? <span className="sn">{esc(String(r.text).slice(0, 200))}</span> : null}
          </div>
        ))}
      </div>
    </details>
  )
}
export function ParseCard({ res }: { res: any }) {
  const blocks = Array.isArray(res.blocks) ? res.blocks : []
  if (!blocks.length) return null
  return (
    <details className="src">
      <summary style={{ color: 'rgb(var(--tx2))' }} dangerouslySetInnerHTML={{ __html: tf('lblParsed', { n: blocks.length }) }} />
      {blocks.slice(0, 3).map((b: any, i: number) => (
        <div className="blk-pre" key={i}>{`[${esc(b.type || 'text')}] ${esc(String(b.text || '').slice(0, 200))}`}</div>
      ))}
      {blocks.length > 3 ? <div className="unit-sec">{tf('moreBlocks', { n: blocks.length - 3 })}</div> : null}
    </details>
  )
}

/* ---- Smart Annotation：图谱节点名下划线，点击→地图定位 ---- */
export function Annotated({ text, ctx }: { text: string; ctx: CardCtx }) {
  const graph = ctx.graph
  const vocab = Object.entries(graph?.nodes || {})
    .map(([id, n]) => ({ id, name: String((n && n.knowledge_point) || id) }))
    .filter((v) => v.name && v.name.length >= 2)
    .sort((a, b) => b.name.length - a.name.length)
  if (!vocab.length || !text) return <>{text}</>
  const re = new RegExp('(' + vocab.map((v) => v.name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|') + ')', 'g')
  const parts = text.split(re)
  return (
    <>
      {parts.map((part, i) => {
        const v = vocab.find((x) => x.name === part)
        if (!v) return <span key={i}>{part}</span>
        return (
          <span key={i} className="note-c" title={t('annotateTitle')} onClick={(e) => { e.stopPropagation(); ctx.onLocate(v.id) }}>{part}</span>
        )
      })}
    </>
  )
}