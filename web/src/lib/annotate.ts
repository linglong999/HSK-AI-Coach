// lib/annotate.ts · A 面·朱笔批注定位纯函数
// 输入：用户原句 + 契约 errors[]/uncertain[]（含服务端 offset 可选键）
// 输出：可渲染的批注段（Mark 列表 + 便捷 split）。无 UI、无状态，便于单测。
import { typeClass } from './graph'

export interface Mark {
  start: number        // 原句 0 基字符起点
  end: number          // 终点（不含）
  fragment: string     // 错点原文
  correction: string   // 矫正字（竹青浮出）
  type: string         // 词汇/语法/语用/汉字
  typeClass: string    // vocab|grammar|pragma|char（语义色）
  kpId: string
  confidence: number
  uncertain: boolean
  explanation: string  // 可展开解释（construction_diagnostics 有则带，无则空）
}

export function collectMarks(text: string, errors: any[], uncertain: any[]): Mark[] {
  const out: Mark[] = []
  for (const e of errors || []) { const m = markOf(text, e, false); if (m) out.push(m) }
  for (const u of uncertain || []) { const m = markOf(text, u, true); if (m) out.push(m) }
  return out.sort((a, b) => a.start - b.start || a.end - b.end)
}

function markOf(text: string, e: any, isUncertain: boolean): Mark | null {
  if (!e || typeof e !== 'object') return null
  const fragment = String(e.fragment || '').trim()
  if (!fragment) return null
  let start = typeof e.offset === 'number' ? e.offset : -1
  // 服务端 offset 越界/缺省/不吻合 → indexOf 兜底（fragment 是原句连续子串）
  if (start < 0 || start + fragment.length > text.length ||
      text.slice(start, start + fragment.length) !== fragment) {
    start = text.indexOf(fragment)
  }
  if (start < 0) return null
  const cd = e.construction_diagnostics
  return {
    start,
    end: start + fragment.length,
    fragment,
    correction: String(e.correction || ''),
    type: String(e.type || ''),
    typeClass: typeClass(e.type),
    kpId: String(e.knowledge_point_id || ''),
    confidence: Number(e.confidence) || 0,
    uncertain: isUncertain,
    explanation: (cd && typeof cd === 'object')
      ? String(cd.explanation || cd.reason || cd.note || '').trim()
      : '',
  }
}

/** 去重叠：按 start 排序后，后一个与前一个相交则丢弃（保留靠前的朱笔锚点）。 */
export function dedupeOverlap(marks: Mark[]): Mark[] {
  const out: Mark[] = []
  for (const m of marks) {
    if (out.length && m.start < out[out.length - 1].end) continue
    out.push(m)
  }
  return out
}

/** 切分原句为「plain | mark」交替段，供 AnnotatedSentence 按序渲染。 */
export interface Segment { kind: 'plain' | 'mark'; start: number; end: number; mark?: Mark }
export function splitSegments(text: string, marks: Mark[]): Segment[] {
  const segs: Segment[] = []
  let cursor = 0
  for (const m of dedupeOverlap(marks)) {
    if (m.start > cursor) segs.push({ kind: 'plain', start: cursor, end: m.start })
    segs.push({ kind: 'mark', start: m.start, end: m.end, mark: m })
    cursor = Math.max(cursor, m.end)
  }
  if (cursor < text.length) segs.push({ kind: 'plain', start: cursor, end: text.length })
  return segs.filter((s) => s.end > s.start)
}

/** 深攻选点：已确认偏误里取置信度最高的一条作为默认展开（单轮深攻 ≤1）。 */
export function pickDeepMark(marks: Mark[]): Mark | null {
  const confirmed = marks.filter((m) => !m.uncertain)
  if (!confirmed.length) return null
  return confirmed.reduce((a, b) => (b.confidence > a.confidence ? b : a))
}