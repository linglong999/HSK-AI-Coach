// components/AnnotatedSentence.tsx · A 面·朱笔批注
// 你发的原句为正文，错点用朱笔逐点下划 + 内联浮出矫正字；
// 每点一行摘要点击展开；单轮默认仅展开最高价值 1 处（深攻≤1），其余轻标折叠；
// 低置信候选独立琥珀条、不混入已确认。
import { useState } from 'react'
import { collectMarks, splitSegments, pickDeepMark, type Mark, type Segment } from '../lib/annotate'

const esc = (s: any) => String(s == null ? '' : s)
  .replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]!))

function Tag({ mark }: { mark: Mark }) {
  return <span className={`atag ${mark.typeClass}`}>{esc(mark.type || '偏误')}</span>
}

export default function AnnotatedSentence({
  text, errors, uncertain, onLocate,
}: { text: string; errors: any[]; uncertain: any[]; onLocate?: (kpId: string) => void }) {
  const marks = collectMarks(text, errors, uncertain)
  if (!marks.length) return null
  const inline = marks.filter((m) => !m.uncertain)
  const uncertainMarks = marks.filter((m) => m.uncertain)
  // 防刷屏：深攻 ≤1 —— 默认仅展开已确认里置信度最高的一处
  const [deep] = useState<Mark | null>(() => pickDeepMark(inline))
  const [open, setOpen] = useState<Set<number>>(() => deep ? new Set([deep.start]) : new Set())

  const segs: Segment[] = splitSegments(text, inline)
  if (!segs.length && !uncertainMarks.length) return null

  const toggle = (m: Mark) => {
    setOpen((prev) => {
      const n = new Set(prev)
      if (n.has(m.start)) n.delete(m.start)
      else n.add(m.start)
      return n
    })
  }

  return (
    <div className="annot">
      {segs.length > 0 && (
        <>
          <div className="annot-label">原句校读 <i>· 朱笔批注</i></div>
          <div className="annot-sent" lang="zh">
            {segs.map((s, i) => {
              if (s.kind === 'plain') return <span key={i}>{esc(text.slice(s.start, s.end))}</span>
              const m = s.mark!
              const isOpen = open.has(m.start)
              return (
                <span key={i} className="awrap">
                  <button
                    type="button"
                    className={`amark ${m.typeClass}${isOpen ? ' open' : ''}`}
                    onClick={() => toggle(m)}
                    title="点击展开解释"
                  >
                    <span className="acorr">{esc(m.fragment)}<b>→</b>{esc(m.correction || '?')}</span>
                    {esc(m.fragment)}
                  </button>
                </span>
              )
            })}
          </div>
        </>
      )}

      {/* 每点解释：默认一行摘要，点击展开 */}
      {inline.length > 0 && (
        <div className="annot-sums">
          {inline.map((m) => (
            <div key={m.start} className={`arow ${open.has(m.start) ? 'open' : ''}`}>
              <button type="button" className="asum" onClick={() => toggle(m)}>
                <Tag mark={m} />
                <span className="afrag">{esc(m.fragment)}<b>→</b>{esc(m.correction || '?')}</span>
              </button>
              {open.has(m.start) && (
                <div className="adetail">
                  {m.explanation
                    ? <p>{esc(m.explanation)}</p>
                    : <p className="muted-note">详见教练批语。</p>}
                  {m.kpId ? (
                    <span className="akp" title="在地图上定位" onClick={() => onLocate?.(m.kpId)}>{esc(m.kpId)}</span>
                  ) : null}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* 低置信待确认：独立琥珀条，不混入已确认 */}
      {uncertainMarks.length > 0 && (
        <div className="annot-uc">
          <span className="uc-lbl">待确认</span>
          {uncertainMarks.map((m) => (
            <span key={m.start} className="uc-item">{esc(m.fragment)}{m.correction ? ` → ${esc(m.correction)}` : ''}</span>
          ))}
        </div>
      )}
    </div>
  )
}