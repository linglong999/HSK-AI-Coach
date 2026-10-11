import { useEffect, useRef, useState } from 'react'
import { api } from '../lib/api'
import { t, tf } from '../lib/i18n'
import { useSessions } from '../lib/session'
import { sceneTitle } from './ScenePicker'

export interface MemoryItem {
  memory_id: string; kind: string; content: { summary?: string; place?: string; when?: string }
  created_at: number; confirmed_at?: number | null
}
export interface Opening {
  enabled: boolean
  resume: { scene_id: string; title: string; title_zh: string; conversation_id: string } | null
  candidates: MemoryItem[]
}
export function memoryText(item: MemoryItem): string {
  const extra = [item.content.place, item.content.when].filter(Boolean).join(' / ')
  return (item.content.summary || '') + (extra ? ` (${extra})` : '')
}

export default function OpeningCard({ data, onChange, onResume, onManage, toast, disabled }: {
  data: Opening; onChange: (update: (prev: Opening) => Opening) => void
  onResume: (sceneId: string) => void; onManage: () => void; toast: (msg: string) => void; disabled: boolean
}) {
  const { notifyMemoryChanged } = useSessions()
  const [pending, setPending] = useState<string[]>([])
  const locks = useRef(new Set<string>())
  const active = useRef(true)
  useEffect(() => { active.current = true; return () => { active.current = false } }, [])
  // onChange is session-scoped by Speak; unmounted results cannot alter another conversation.
  const act = async (item: MemoryItem, action: 'confirm' | 'reject') => {
    if (locks.current.has(item.memory_id)) return
    locks.current.add(item.memory_id)
    setPending((ids) => [...ids, item.memory_id])
    try {
      await api('/api/memory/' + action, { memory_id: item.memory_id })
      onChange((prev) => ({ ...prev, candidates: prev.candidates.filter((m) => m.memory_id !== item.memory_id) }))
      notifyMemoryChanged()
    } catch { if (active.current) toast(t('opFailed')) }
    finally {
      locks.current.delete(item.memory_id)
      if (active.current) setPending((ids) => ids.filter((id) => id !== item.memory_id))
    }
  }
  if (!data.enabled || (!data.resume && !data.candidates.length)) return null
  return <section className="opening-card" role="region" aria-label={t('memoryOpening')}>
    {data.resume && <div className="memory-row">
      <span>{tf('memoryResume', { title: sceneTitle(data.resume) })}</span>
      <button type="button" className="btn" disabled={disabled} onClick={() => onResume(data.resume!.scene_id)}>{t('memoryContinue')}</button>
      <button type="button" className="btn" onClick={() => onChange((prev) => ({ ...prev, resume: null }))}>{t('memoryDecline')}</button>
    </div>}
    {data.candidates.map((item) => <div className="memory-row" key={item.memory_id}>
      <span>{memoryText(item)}</span>
      <button type="button" className="btn" disabled={pending.includes(item.memory_id)} onClick={() => void act(item, 'confirm')}>{t('memoryRemember')}</button>
      <button type="button" className="btn" disabled={pending.includes(item.memory_id)} onClick={() => void act(item, 'reject')}>{t('memoryDecline')}</button>
    </div>)}
    <p className="sub">{t('memoryOpeningNote')} <button type="button" className="btn" onClick={onManage}>{t('tabMemory')}</button></p>
  </section>
}
