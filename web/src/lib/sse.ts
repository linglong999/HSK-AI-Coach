// lib/sse.ts · B7 S1/S4 · POST SSE 流式消费（/api/dialog 是 POST，EventSource 原生只支持 GET，
// 故用 fetch + ReadableStream 手写 SSE 帧解析）。
// 事件序（契约文档 v2）：intercept? → message* → done | error。

export interface DialogEvents {
  onIntercept?: (data: any) => void
  onMessage?: (data: any) => void
  onDone?: (data: any) => void
  onError?: (data: any) => void
}

/** 解析一帧 SSE（`event: x\ndata: y\n...`），派发给对应回调。 */
function dispatchFrame(text: string, events: DialogEvents) {
  let event = 'message'
  const dataLines: string[] = []
  for (const raw of text.split('\n')) {
    const line = raw.replace(/\r$/, '')
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
  }
  if (!dataLines.length) return
  let payload: any
  try {
    payload = JSON.parse(dataLines.join('\n'))
  } catch {
    payload = { raw: dataLines.join('\n') }
  }
  if (event === 'intercept') events.onIntercept?.(payload)
  else if (event === 'done') events.onDone?.(payload)
  else if (event === 'error') events.onError?.(payload)
  else events.onMessage?.(payload)
}

/** 发起一次叙事场景回合流式请求，返回 AbortController 以便断开/停止。 */
export function streamDialog(
  url: string,
  body: Record<string, unknown>,
  events: DialogEvents,
): AbortController {
  const ac = new AbortController()

  ;(async () => {
    let res: Response
    try {
      res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: ac.signal,
      })
    } catch (e: any) {
      if (e?.name === 'AbortError') return
      events.onError?.({ msg: String(e), partial: true })
      return
    }
    if (!res.ok || !res.body) {
      // 非 200 或非流式 → 一次性 JSON（空文本/无 Key/配额硬错）
      let data: any = { msg: `HTTP ${res.status}` }
      try {
        data = await res.json()
      } catch { /* ignore */ }
      events.onError?.(data)
      return
    }

    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    const flush = (text: string) => {
      for (const raw of text.replace(/\r/g, '').split('\n\n')) {
        if (raw.trim()) dispatchFrame(raw, events)
      }
    }

    try {
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        let idx: number
        while ((idx = buffer.indexOf('\n\n')) >= 0) {
          flush(buffer.slice(0, idx))
          buffer = buffer.slice(idx + 2)
        }
      }
      if (buffer.trim()) flush(buffer)
    } catch (e: any) {
      if (e?.name !== 'AbortError') events.onError?.({ msg: String(e), partial: true })
    }
  })()

  return ac
}