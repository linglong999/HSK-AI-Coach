// pages/Review.tsx · 复习答题（消费 GET /api/quiz queue + POST /api/quiz/answer）
import { useEffect, useState } from 'react'
import { getJson, postJson } from '../lib/api'

interface Question {
  type: 'cloze' | 'recall'
  kp_id: string
  grammar?: string
  sentence?: string
  answer?: string
  full_sentence?: string
  knowledge_point?: string
  note?: string
  level?: string
}

interface QuizResp { queue: unknown[]; questions: Question[] }

export default function Review() {
  const [q, setQ] = useState<QuizResp | null>(null)
  const [idx, setIdx] = useState(0)
  const [ans, setAns] = useState('')
  const [last, setLast] = useState<string | null>(null)
  const [err, setErr] = useState('')
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    getJson<QuizResp>('/api/quiz')
      .then(setQ)
      .catch((e) => setErr(String(e)))
  }, [])

  if (err) return <div className="panel warn">{err}</div>
  if (!q || !q.questions.length) return <div className="panel">复习队列没题了，去练几轮再回来。</div>

  const item = q.questions[idx]

  const submit = async (correct: boolean) => {
    if (submitting) return
    setSubmitting(true)
    setLast(null)
    try {
      await postJson('/api/quiz/answer', [{ kp_id: item.kp_id, correct }])
      setLast(`${correct ? '记得 ✓' : '忘了 ✗'} —— 已回写调度`)
      setAns('')
      if (idx + 1 < q.questions.length) setIdx(idx + 1)
      else setLast('本轮复习做完啦。')
      setQ(null) // 重新拉取让队列更新
      getJson<QuizResp>('/api/quiz').then((r) => { setQ(r); setIdx(r.questions.length ? Math.max(0, Math.min(r.questions.length - 1, idx)) : 0) }).catch(() => {})
    } catch (e) {
      setErr(String(e))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="panel">
      <h2>复习 {idx + 1} / {q.questions.length}</h2>
      {item.type === 'cloze' && (
        <>
          <p className="sentence">{item.sentence}</p>
          <p className="muted">填上缺的词：</p>
          <input value={ans} onChange={(e) => setAns(e.target.value)} placeholder="填空" />
          {ans.trim() !== '' && (
            <button className="btn" onClick={() => submit(ans.trim() === item.answer)}>检查</button>
          )}
        </>
      )}
      {item.type === 'recall' && (
        <>
          <p className="sentence">{item.knowledge_point || item.kp_id}</p>
          <p className="muted">{item.note || '凭印象回想这个词 / 这个语法点'}</p>
          <div className="btnrow">
            <button className="btn" onClick={() => submit(true)}>记得</button>
            <button className="btn" onClick={() => submit(false)}>忘了</button>
          </div>
        </>
      )}
      {last && <p className="ok">{last}</p>}
    </div>
  )
}