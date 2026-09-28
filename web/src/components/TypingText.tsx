// components/TypingText.tsx · 墨本·母题C「书写感」：助教回复逐字浮现 + 朱笔笔芯光标
// 落地验收点「SSE 流式打字=书写感」——AI 回复不是整块砸出，而是像落笔写下的节奏浮现。
// prefers-reduced-motion 下禁用动效，一次性呈现全文（与 styles.css 全局降级一致）。
import { useEffect, useRef, useState } from 'react'

const reduceMotion = () =>
  typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches

interface Props {
  text: string
  /** 每次落笔间隔 ms（书写节奏） */
  stepMs?: number
  /** 每落一次笔回调（供外层贴地滚动） */
  onProgress?: () => void
  /** 全文写完回调 */
  onDone?: () => void
}

export default function TypingText({ text, stepMs = 26, onProgress, onDone }: Props) {
  const [n, setN] = useState(0)
  const doneRef = useRef(false)
  const total = text.length

  useEffect(() => {
    setN(0)
    doneRef.current = false
    if (reduceMotion() || total === 0) {
      setN(total)
      onDone?.()
      return
    }
    // 中文「书写」节奏：全文约 40 步落完，避免长篇卡顿也不瞬跳
    const step = Math.max(1, Math.round(total / 40))
    const timer = setInterval(() => {
      setN((v) => {
        const next = Math.min(total, v + step)
        onProgress?.()
        if (next >= total && !doneRef.current) {
          doneRef.current = true
          clearInterval(timer)
          onDone?.()
        }
        return next
      })
    }, stepMs)
    return () => clearInterval(timer)
    // text 变化即视为新一段落笔，重新书写
  }, [text]) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <span className="typing">
      {text.slice(0, n)}
      {n < total && <span className="brush" aria-hidden />}
    </span>
  )
}