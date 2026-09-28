// pages/Metrics.tsx · 效果度量面板（复刻 web_legacy metrics.html）
// 数据 /api/metrics（学习者四指标）+ /api/alignment（教材与考纲对齐），只读。
import { useCallback, useEffect, useState } from 'react'
import { getJson } from '../lib/api'
import { t, tf, lang } from '../lib/i18n'

interface MetricCell { value: number | null; n?: number; window?: string; caveats?: string }
interface LearnerMetrics {
  pass_rate_delta?: MetricCell
  interactions_to_pass?: MetricCell
  continue_learning_rate?: MetricCell
  satisfaction?: MetricCell
}
interface MetricsData { generated_at?: number; learners?: Record<string, LearnerMetrics> }
interface Align {
  active_book?: string | null
  units?: number | null
  syllabus_points?: number | null
  mapped_points?: number | null
  unmapped_points?: number | null
  cover_ratio?: number | null
  note?: string
}

const LABELS: Array<[keyof LearnerMetrics, string]> = [
  ['pass_rate_delta', 'mPass'],
  ['interactions_to_pass', 'mInteract'],
  ['continue_learning_rate', 'mContinue'],
  ['satisfaction', 'mSatisfaction'],
]

function fmtValue(key: string, c?: MetricCell): { cls: string; val: string } {
  if (!c || c.value == null) return { cls: 'void', val: tf('mNoData', { n: c?.n ?? 0 }) }
  const x = c.value
  let val = ''
  let cls = ''
  if (key === 'pass_rate_delta') {
    val = (x >= 0 ? '+' : '') + x.toFixed(4) + t('mDays')
    cls = x < 0 ? 'pos' : 'neg'
  } else if (key === 'interactions_to_pass') {
    val = x.toFixed(1) + t('mUnit')
  } else if (key === 'continue_learning_rate') {
    val = (x * 100).toFixed(0) + '%'
  } else if (key === 'satisfaction') {
    val = x.toFixed(2) + t('mPer')
    cls = x >= 4 ? 'pos' : (x <= 2.5 ? 'neg' : '')
  } else {
    val = String(x)
  }
  return { cls, val }
}

export default function Metrics() {
  const [data, setData] = useState<MetricsData | null>(null)
  const [align, setAlign] = useState<Align | null>(null)
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(true)

  const load = useCallback(() => {
    setLoading(true)
    setErr('')
    getJson<MetricsData>('/api/metrics')
      .then((d) => { setData(d) })
      .catch((e) => setErr(t('metricsFail') + e.message))
      .finally(() => setLoading(false))
    getJson<Align>('/api/alignment')
      .then(setAlign)
      .catch(() => setAlign(null))
  }, [])

  useEffect(load, [load])

  const learners = Object.entries(data?.learners || {})

  return (
    <div className="metrics-view pad">
      <h2>{t('metricsEntry')}</h2>
      <p className="sub">{t('metricsBody')}</p>
      <div className="toolbar">
        <button type="button" className="btn" onClick={load}>{t('metricsRefresh')}</button>
        <span className="meta">{data?.generated_at ? t('metricsGen') + '：' + new Date(data.generated_at * 1000).toLocaleString(lang() === 'zh' ? 'zh-CN' : 'en-US') : ''}</span>
      </div>

      {align && align.active_book != null && (
        <div className="alignCard">
          <div className="secLabel">{t('metricsAlignSec')}</div>
          <div className="alignRow">
            {alignItem(t('mBook'), align.active_book || '—')}
            {alignItem(t('mUnits'), align.units != null ? String(align.units) : '—')}
            {alignItem(t('mSyllabus'), align.syllabus_points != null ? String(align.syllabus_points) : '—')}
            {alignItem(t('mMapped'), align.mapped_points != null ? String(align.mapped_points) : '—')}
            {alignItem(t('mUnmapped'), align.unmapped_points != null ? String(align.unmapped_points) : '—')}
            {alignItem(t('mCoverage'), align.cover_ratio != null ? (align.cover_ratio * 100).toFixed(1) + '%' : '—')}
          </div>
          {align.note ? <div className="note">{align.note}</div> : null}
        </div>
      )}

      {err ? (
        <div className="err">{err}<br />{t('metricsServerHint')}</div>
      ) : loading ? (
        <div className="empty">{t('metricsLoading')}</div>
      ) : learners.length === 0 ? (
        <>
          <div className="secLabel">{t('metricsSec')}</div>
          <div className="empty">{t('metricsEmpty')}</div>
        </>
      ) : (
        <>
          <div className="secLabel">{t('metricsSec')}</div>
          <div className="learners">
            {learners.map(([name, m]) => (
              <div key={name} className="lcard">
                <div className="lhead"><span className="lname">{name}</span></div>
                <div className="mGrid">
                  {LABELS.map(([key, labelKey]) => {
                    const mm: MetricCell = m[key] ?? { value: null }
                    const fv = fmtValue(key, mm)
                    return (
                      <div key={key} className="m">
                        <h3>{t(labelKey)}</h3>
                        <span className={`val ${fv.cls}`}>{fv.val}</span>
                        <div className="win">{t('mWin')}{mm.window || '-'}</div>
                        {mm.caveats ? <div className="caveat">{mm.caveats}</div> : null}
                      </div>
                    )
                  })}
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  )
}

function alignItem(k: string, v: string) {
  return (
    <div className="it">
      <span className="k">{k}</span>
      <span className="v">{v}</span>
    </div>
  )
}