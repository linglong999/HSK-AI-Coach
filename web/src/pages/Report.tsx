// pages/Report.tsx · AI 学习报告（复刻 web_legacy renderReport）
// 数据源：/api/profile —— stats（图谱/复习/惯犯计数）+ common_errors（live facts）
// 只读事实，非 LLM 生成；rf-row 点击 → 认知地图视图。
import { useEffect, useState } from 'react'
import { getJson } from '../lib/api'
import { t, tf, lang } from '../lib/i18n'

interface Fact {
  kp_id?: string
  knowledge_point?: string
  level?: string
  repeat_offender?: boolean
  repeated?: number
  priority?: number
  latest_evidence?: string
}
interface Stats {
  graph_nodes?: number
  graph_edges?: number
  review_count?: number
  repeat_offenders?: string[]
}
interface ProfileData {
  common_errors?: Fact[]
  profile?: { common_errors?: Fact[] }
  stats?: Stats
}

export default function Report() {
  const [p, setP] = useState<ProfileData | null>(null)
  const [err, setErr] = useState('')

  useEffect(() => {
    getJson<ProfileData>('/api/profile').then(setP).catch((e) => setErr(t('reportFail') + e.message))
  }, [])

  if (err) return <div className="pad"><h2>◎ {t('repTitle')}</h2><div className="panel warn">{err}</div></div>
  if (!p) return <div className="pad"><h2>◎ {t('repTitle')}</h2><div className="panel">载入中…</div></div>

  const st = p.stats || {}
  const ce = p.common_errors || []
  const facts = ce.length ? ce : (p.profile?.common_errors || [])
  const isStored = !ce.length && facts.length
  const offenders = st.repeat_offenders || []
  const stamp = new Intl.DateTimeFormat(lang() === 'zh' ? 'zh-CN' : 'en-US',
    { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }).format(new Date())

  return (
    <div className="pad">
      <div className="card report">
        <div className="cardlabel">{t('repTitle')} · {stamp}</div>
        <div className="rep-stats">
          <span>{t('repGraph')} <b>{st.graph_nodes != null ? st.graph_nodes : '?'}</b> {t('repNodes')}</span>
          <span>{t('repReview')} <b>{st.review_count != null ? st.review_count : '?'}</b> {t('repItems')}</span>
          <span>{t('repEdges')} <b>{st.graph_edges != null ? st.graph_edges : '?'}</b></span>
          <span>{t('repOffenders')} <b style={{ color: offenders.length ? 'rgb(var(--sem-err))' : 'inherit' }}>{offenders.length}</b></span>
        </div>

        {offenders.length ? <div className="uc-strip">{tf('repWarn', { list: offenders.join('、') })}</div> : null}

        {facts.length ? (
          facts.slice(0, 8).map((f, i) => (
            <div key={f.kp_id || i} className="rf-row" data-kp={f.kp_id || ''} onClick={() => { location.hash = '#/map' }}>
              <span className="nm">{f.knowledge_point || f.kp_id || ''}</span>
              {f.level ? <span className="lv">{f.level}</span> : null}
              {f.repeat_offender
                ? <span className="rf-off">{tf('repOff', { n: f.repeated ?? 0 })}</span>
                : <span className="rf-rep">{tf('repRep', { n: f.repeated ?? 0 })}</span>}
              <span className="bar" style={{ minWidth: 60, maxWidth: 120 }}><div style={{ width: `${Math.min((f.priority || 0) * 10, 100)}%` }} /></span>
              <span className="rv-prio">{(f.priority || 0).toFixed(1)}</span>
              {f.latest_evidence ? <span className="rf-ev">{t('repLatest')}「{String(f.latest_evidence).slice(0, 60)}」</span> : null}
            </div>
          ))
        ) : (
          <div className="clean-sub">{t('repEmpty')}</div>
        )}

        {isStored ? <div className="meta-row"><span>{t('repStored')}</span></div> : null}
        <div className="meta-row"><span>{t('repSrc')}</span></div>
      </div>
    </div>
  )
}