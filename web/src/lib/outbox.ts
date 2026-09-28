// lib/outbox.ts · 跨视图消息桥：Doc（阅读材料）→ Speak（对话）"解析并发送"
interface Pending { text: string; badge?: string }
let pending: Pending | null = null

/** Doc 视图写入：连同解析提示词作为待发对话，badge 是用户气泡上的标签（如 ▤ 阅读材料） */
export function setPendingMessage(text: string, badge?: string): void {
  pending = { text, badge }
}

/** Speak 挂载时取走（一次性）；取走即清空，避免历史遗留重复发送 */
export function takePendingMessage(): Pending | null {
  const p = pending
  pending = null
  return p
}