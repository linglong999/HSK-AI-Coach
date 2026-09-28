// components/Sidebar.tsx · 左侧栏（复刻 web_legacy：resident + 会话列表 + 学习动线 + 折叠 + 右键菜单）
// 会话数据来自 SessionProvider；视图经 hash 单一事实源。
import { useEffect, useRef, useState } from 'react'
import { t } from '../lib/i18n'
import { useSessions, type Session } from '../lib/session'
import ThemeSwitcher from './ThemeSwitcher'

export type View = 'dialog' | 'map' | 'review' | 'help' | 'report' | 'doc' | 'metrics'

interface Props {
  view: View
  onNav: (v: View) => void
  onOpenSettings: () => void
}

function timeShort(iso?: string): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (isNaN(d.getTime())) return ''
  return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

export default function Sidebar({ view, onNav, onOpenSettings }: Props) {
  const { sessions, conversationId, newSession, switchSession, togglePin, renameSession, deleteSession } = useSessions()
  const [closed, setClosed] = useState(false)
  // 右键菜单
  const [menu, setMenu] = useState<{ x: number; y: number; s: Session } | null>(null)
  const [armed, setArmed] = useState<string | null>(null)
  // 行内改名的会话 id
  const [editing, setEditing] = useState<string | null>(null)
  const renRef = useRef<HTMLInputElement>(null)

  useEffect(() => { if (editing) renRef.current?.focus() }, [editing])

  useEffect(() => {
    if (!menu) return
    const close = () => { setMenu(null); setArmed(null) }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') close() }
    document.addEventListener('mousedown', close)
    document.addEventListener('keydown', onKey)
    window.addEventListener('scroll', close, true)
    window.addEventListener('resize', close)
    return () => {
      document.removeEventListener('mousedown', close)
      document.removeEventListener('keydown', onKey)
      window.removeEventListener('scroll', close, true)
      window.removeEventListener('resize', close)
    }
  }, [menu])

  // 当前会话若不在列表（如深链新会话未落盘），补一个占位项
  const items: Session[] = [...sessions]
  if (conversationId && !items.some((s) => s.id === conversationId)) {
    items.unshift({ id: conversationId, title: '', message_count: 0, _pending: true })
  }

  const openCtx = (e: React.MouseEvent, s: Session) => {
    if (s._pending) return
    e.preventDefault(); e.stopPropagation()
    setMenu({ x: e.clientX, y: e.clientY, s })
  }

  const ctxAction = (act: string, s: Session) => {
    if (act === 'pin') { void togglePin(s).then(() => { setMenu(null); setArmed(null) }) }
    else if (act === 'rename') { setMenu(null); setArmed(null); setEditing(s.id) }
    else if (act === 'delete') {
      if (armed !== s.id) { setArmed(s.id); return }
      void deleteSession(s.id).then(() => { setMenu(null); setArmed(null) })
    }
  }

  const finishRename = async (save: boolean) => {
    const s = editing ? sessions.find((x) => x.id === editing) : null
    const v = renRef.current?.value.trim() ?? ''
    setEditing(null)
    if (!s || !save || !v || v === (s.title || '')) return
    try { await renameSession(s.id, v) } catch { /* opFailed */ }
  }

  return (
    <div className={`side${closed ? ' closed' : ''}`}>
      <div className="side-top">
        <span className="logo">汉语 <b>AI 教练</b></span>
        <button className="fold" onClick={() => setClosed((c) => !c)} title={closed ? t('expandSide') : t('foldSide')}>
          {closed ? '»' : '«'}
        </button>
      </div>

      <div className="side-scroll">
        {/* resident 助教卡 */}
        <div className="resident">
          <span className="av">师</span>
          <span className="mt">
            <span className="t">AI 助教</span>
            <span className="s">本地图谱感知 · 偏误驱动</span>
          </span>
          <span className="chip">HSK3</span>
        </div>

        {/* 会话 */}
        <div className="hd"><span>{t('sessions')}</span><span className="sep" /><span className="ct">{sessions.length ? String(sessions.length) : ''}</span></div>
        <button className="sbtn add" onClick={() => { onNav('dialog'); newSession() }}>{t('newSession')}</button>
        {items.slice(0, 30).map((s) => {
          const isCur = s.id === conversationId
          return (
            <button
              key={s.id}
              type="button"
              data-id={s.id}
              className={`sbtn${isCur ? ' cur' : ''}`}
              onClick={() => { onNav('dialog'); void switchSession(s.id) }}
              onContextMenu={(e) => openCtx(e, s)}
              title={`${s.title || s.id}${s.updated_at ? ' · ' + timeShort(s.updated_at) : ''}`}
            >
              <span className="ic">{s.pinned ? '📌' : isCur ? '●' : '○'}</span>
              {editing === s.id ? (
                <input
                  ref={renRef}
                  className="sren"
                  maxLength={60}
                  defaultValue={s.title || ''}
                  onClick={(e) => e.stopPropagation()}
                  onKeyDown={(e) => {
                    e.stopPropagation()
                    if (e.key === 'Enter') void finishRename(true)
                    else if (e.key === 'Escape') void finishRename(false)
                  }}
                  onBlur={() => void finishRename(true)}
                />
              ) : (
                <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>
                  {s.title || (s._pending ? t('newSessPending') : s.id)}
                </span>
              )}
              {!s._pending && editing !== s.id && (
                <span className="srow-sub">{tf2(t('nMsgs'), { n: s.message_count || 0 })}</span>
              )}
            </button>
          )
        })}

        {/* 学习动线 */}
        <div className="hd"><span>{t('learnPath')}</span><span className="sep" /></div>
        <button type="button" className={`sbtn${view === 'map' ? ' cur' : ''}`} onClick={() => onNav('map')}><span className="ic">◈</span>{t('cogMap')}</button>
        <button type="button" className={`sbtn${view === 'review' ? ' cur' : ''}`} onClick={() => onNav('review')}><span className="ic">▶</span>{t('startReview')}</button>
        <button type="button" className={`sbtn${view === 'report' ? ' cur' : ''}`} onClick={() => onNav('report')}><span className="ic">◎</span>{t('aiReport')}</button>
        <button type="button" className={`sbtn${view === 'doc' ? ' cur' : ''}`} onClick={() => onNav('doc')}><span className="ic">▤</span>{t('readingMat')}</button>

        {/* 系统 */}
        <div className="hd"><span>{t('settings')}</span><span className="sep" /></div>
        <button type="button" className="sbtn" onClick={onOpenSettings}><span className="ic">⚙</span>{t('settings')}</button>
        <button type="button" className={`sbtn${view === 'help' ? ' cur' : ''}`} onClick={() => onNav('help')}><span className="ic">?</span>{t('howto')}</button>
        <button type="button" className={`sbtn${view === 'metrics' ? ' cur' : ''}`} onClick={() => onNav('metrics')}><span className="ic">◎</span>{t('metricsEntry')}</button>
      </div>

      <div className="side-bot">
        <div className="status on"><span className="dot" />教练在线</div>
        <ThemeSwitcher />
      </div>

      {menu && (
        <div className="ctx-menu show" style={{ left: menu.x, top: menu.y }} onMouseDown={(e) => e.stopPropagation()}>
          <div className="ctx-it" data-act="pin" onClick={() => ctxAction('pin', menu.s)}>
            <span className="ci">📌</span>{menu.s.pinned ? t('ctxUnpin') : t('ctxPin')}
          </div>
          <div className="ctx-it" data-act="rename" onClick={() => ctxAction('rename', menu.s)}>
            <span className="ci">✏️</span>{t('ctxRename')}
          </div>
          <div className="ctx-sep" />
          <div className={`ctx-it danger${armed === menu.s.id ? ' armed' : ''}`} data-act="delete" onClick={() => ctxAction('delete', menu.s)}>
            <span className="ci">🗑</span>{armed === menu.s.id ? t('ctxDeleteConfirm') : t('ctxDelete')}
          </div>
        </div>
      )}
    </div>
  )
}

function tf2(s: string, map: Record<string, any>): string {
  return s.replace(/\{(\w+)\}/g, (m, n) => (n in map ? String(map[n]) : m))
}