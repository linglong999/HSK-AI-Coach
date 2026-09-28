// lib/graph.ts · 偏误图谱类型 + 认知地图确定性算法（忠实移植 web_legacy）
// 布局 = HSK 等级分区(横轴) × 掌握度(纵轴)；节点三重编码。零泄漏给组件——纯函数。

export interface GraphNode {
  id: string
  knowledge_point: string
  level: string
  level_gf?: number | null
  error_types: Record<string, number>
  error_count: number
  mastery: number
  error_kind: string
  nature?: string
  positive_count?: number
  [k: string]: any
}
export interface GraphEdge { from_node_id: string; to_node_id: string; relation?: string; edge_weight?: number }
export interface QueueItem { kp_id: string; priority: number; node?: Partial<GraphNode> }
export interface Graph {
  learner_id?: string
  nodes: Record<string, GraphNode>
  edges: GraphEdge[]
  queue: QueueItem[]
}

export function levelNum(lv: string | number | null | undefined): number {
  const m = String(lv ?? '').match(/\d+/)
  return m ? parseInt(m[0], 10) : 3
}

/** 地图节点短名：取括号外核心词("量词（个/本/张…）"→"量词")再截 max 字 */
export function shortKpName(raw: string, max: number): string {
  const s = String(raw || '')
  const cut = s.split(/（|\(/)[0].trim()
  const base = cut || s
  const chars = [...base]
  return chars.length > max ? chars.slice(0, max - 1).join('') + '…' : base
}

export function neighborsOf(graph: Graph | null, kpId: string): string[] {
  const out: string[] = []
  ;(graph?.edges || []).forEach((e) => {
    if (e.from_node_id === kpId) out.push(e.to_node_id)
    else if (e.to_node_id === kpId) out.push(e.from_node_id)
  })
  return [...new Set(out)].filter((id) => graph?.nodes && graph.nodes[id])
}
export function inQueue(graph: Graph | null, kpId: string): boolean {
  return (graph?.queue || []).some((q) => q.kp_id === kpId)
}
export function nodeName(graph: Graph | null, kpId: string): string {
  const n = graph?.nodes && graph.nodes[kpId]
  return (n && n.knowledge_point) || kpId
}
export function prioOf(graph: Graph | null, kpId: string): number {
  const q = (graph?.queue || []).find((x) => x.kp_id === kpId)
  return q ? q.priority || 0 : 0
}

/* ----- 知识类别 → 语义色（与 legacy typeClass/typeColor 同源） ----- */
export function typeClass(t: string): string {
  t = t || ''
  if (t.includes('词汇')) return 'vocab'
  if (t.includes('语法')) return 'grammar'
  if (t.includes('语用')) return 'pragma'
  if (t.includes('汉字')) return 'char'
  return 'grammar'
}
export function typeColor(t: string): string {
  t = t || ''
  if (t.includes('词汇')) return 'rgb(var(--kp-vocab))'
  if (t.includes('语法')) return 'rgb(var(--kp-grammar))'
  if (t.includes('语用')) return 'rgb(var(--kp-pragma))'
  if (t.includes('汉字')) return 'rgb(var(--kp-char))'
  return 'rgb(var(--kp-grammar))'
}
export function nodeColor(errorTypes: Record<string, number> = {}): string {
  return typeColor(Object.keys(errorTypes || {}).join(''))
}

/* ----- 认知地图布局（layoutNodes 确定性移植） ----- */
export interface MapLayout {
  pos: Record<string, { x: number; y: number }>
  W: number; H: number; headTop: number; top: number; bottom: number
  maxLv: number; colW: number; xOf: (lv: string) => number; cols: Record<number, string[]>
}
export function layoutNodes(graph: Graph, W: number, H: number): MapLayout {
  W = W || 800; H = H || 520
  const headTop = 14, top = 56, bottom = H - 58
  const lvSet = new Set<number>([1, 2, 3, 4])
  Object.values(graph.nodes || {}).forEach((n) => {
    const v = levelNum(n.level)
    if (v >= 1 && v <= 6) lvSet.add(v)
  })
  const maxLv = Math.max(...lvSet)
  const colW = Math.max((W - 90) / maxLv, 70)
  const xOf = (lv: string) => 45 + (levelNum(lv) - 0.5) * colW
  const cols: Record<number, string[]> = {}
  Object.entries(graph.nodes || {}).forEach(([id, n]) => {
    const k = levelNum(n.level)
    ;(cols[k] = cols[k] || []).push(id)
  })
  const pos: MapLayout['pos'] = {}
  Object.entries(cols).forEach(([k, ids]) => {
    const lv = Number(k)
    const list = ids.slice().sort((a, b) => ((graph.nodes[a]?.mastery) || 0) - ((graph.nodes[b]?.mastery) || 0))
    const n = list.length
    const span = bottom - top
    list.forEach((id, i) => {
      const node = graph.nodes[id]
      const y = n === 1 ? top + span * (1 - (node.mastery || 0)) : top + span * (i / (n - 1))
      pos[id] = { x: xOf(String(lv)), y: Math.max(top, Math.min(bottom, y)) }
    })
  })
  return { pos, W, H, headTop, top, bottom, maxLv, colW, xOf, cols }
}

/** 下一步推荐：复习队列优先级最高项；无队列取错误>0 中掌握度最低 */
export function pickRecommendation(graph: Graph | null): { id: string; mode: 'due'; p: number } | { id: string; mode: 'weak'; m: number } | null {
  if (!graph) return null
  const q = (graph.queue || []).slice().sort((a, b) => (b.priority || 0) - (a.priority || 0))
  if (q.length && graph.nodes && graph.nodes[q[0].kp_id]) {
    return { id: q[0].kp_id, mode: 'due', p: q[0].priority || 0 }
  }
  const cand = Object.entries(graph.nodes || {})
    .filter(([, n]) => (n.error_count || 0) > 0)
    .sort((a, b) => (a[1].mastery || 0) - (b[1].mastery || 0))
  if (cand.length) return { id: cand[0][0], mode: 'weak', m: cand[0][1].mastery || 0 }
  return null
}