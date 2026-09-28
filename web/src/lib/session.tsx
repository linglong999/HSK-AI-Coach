// lib/session.tsx · 会话数据层（忠实移植 web_legacy 的会话管理）
// 数据源：/api/profile（列表+画像） + /api/conversation?id=（深链恢复）
//        + /api/dialog{conversation_id}（对话）+ /api/session/update|delete
// 会话是服务端权威，本地只做渲染与轻缓存。零泄漏给组件——靠 Context 下发。
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { api } from './api'

export interface Session {
  id: string
  title: string
  message_count?: number
  pinned?: boolean
  updated_at?: string
  _pending?: boolean
}
export interface Profile {
  persona?: string
  user_level?: string
  learner_l1?: string
}
export interface ConvMsg { role: string; content: string; [k: string]: any }

interface SessionCtx {
  sessions: Session[]
  profile: Profile
  conversationId: string
  refresh: () => Promise<void>
  newSession: () => void
  switchSession: (id: string) => Promise<void>
  renameSession: (id: string, title: string) => Promise<void>
  togglePin: (s: Session) => Promise<void>
  deleteSession: (id: string) => Promise<void>
}

const Ctx = createContext<SessionCtx | null>(null)

function newConvId(): string {
  const saved = sessionStorage.getItem('hsk_conv')
  if (saved) return saved
  const id = `web-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
  sessionStorage.setItem('hsk_conv', id)
  return id
}

function urlConversationParam(): string {
  try { return new URL(location.href).searchParams.get('conversation') || '' } catch { return '' }
}
function setUrlConversation(id: string): void {
  try {
    const u = new URL(location.href)
    if (id) u.searchParams.set('conversation', id); else u.searchParams.delete('conversation')
    history.replaceState(null, '', u)
  } catch { /* noop */ }
}

export function SessionProvider({ children }: { children: ReactNode }) {
  // 会话权威在服务端；conversationId 由 URL 深链优先，其次本壳当前会话
  const [sessions, setSessions] = useState<Session[]>([])
  const [profile, setProfile] = useState<Profile>({})
  const [conversationId, setConversationId] = useState<string>(() => urlConversationParam() || newConvId())

  const refresh = useCallback(async () => {
    try {
      const p = await api<{ profile?: Profile; sessions?: Session[] }>('/api/profile')
      setProfile(p.profile || {})
      setSessions(p.sessions || [])
    } catch { /* 列表失败不打断 */ }
  }, [])

  useEffect(() => { refresh() }, [refresh])

  const switchSession = useCallback(async (id: string) => {
    setConversationId(id)
    setUrlConversation(id)
    sessionStorage.setItem('hsk_conv', id)
    await refresh()
  }, [refresh])

  const newSession = useCallback(() => {
    const id = 'web-' + Date.now().toString(36)
    void switchSession(id)
  }, [switchSession])

  const renameSession = useCallback(async (id: string, title: string) => {
    await api('/api/session/update', { conversation_id: id, title })
    await refresh()
  }, [refresh])

  const togglePin = useCallback(async (s: Session) => {
    await api('/api/session/update', { conversation_id: s.id, pinned: !s.pinned })
    await refresh()
  }, [refresh])

  const deleteSession = useCallback(async (id: string) => {
    try {
      await api('/api/session/delete', { conversation_id: id })
    } catch (e: any) {
      if (e?.code !== 'session_not_found') throw e
    }
    if (id === conversationId) { const n = 'web-' + Date.now().toString(36); void switchSession(n) }
    else await refresh()
  }, [conversationId, refresh, switchSession])

  const value = useMemo<SessionCtx>(() => ({
    sessions, profile, conversationId,
    refresh, newSession, switchSession, renameSession, togglePin, deleteSession,
  }), [sessions, profile, conversationId, refresh, newSession, switchSession, renameSession, togglePin, deleteSession])

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useSessions(): SessionCtx {
  const v = useContext(Ctx)
  if (!v) throw new Error('useSessions must be used within SessionProvider')
  return v
}