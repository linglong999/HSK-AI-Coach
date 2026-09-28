// components/SettingsModal.tsx · 设置弹窗（复刻老壳 6 标签：语言/主题/模型密钥/persona/快捷键/关于）
import { useCallback, useEffect, useState } from 'react'
import { t, setLang as setI18nLang } from '../lib/i18n'
import { api } from '../lib/api'

const TABS: { id: string; label: string }[] = [
  { id: 'lang', label: t('tabLang') },
  { id: 'theme', label: t('tabTheme') },
  { id: 'llm', label: t('tabLLM') },
  { id: 'persona', label: t('tabPersona') },
  { id: 'keys', label: t('tabKeys') },
  { id: 'about', label: t('tabAbout') },
]

interface Props { tab: string; onClose: () => void }

// 语言/主题切换后通知全局重渲染（t() 读模块单例，需 App 层 force re-render）
export function notifyShellChanged(): void {
  window.dispatchEvent(new Event('hsk:shell-changed'))
}

export function applyLang(l: 'zh' | 'en'): void {
  setI18nLang(l)
  notifyShellChanged()
}

const THEMES = [
  { id: 'memphis', brand: '#df6b8c', bg: '#faf8f3' },
  { id: 'warm', brand: '#f45f28', bg: '#f2eadb' },
  { id: 'midnight', brand: '#d4a054', bg: '#241f1c' },
]
function themeName(id: string): string {
  if (id === 'memphis') return t('themeMemphis')
  if (id === 'warm') return t('themeWarm')
  return t('theme_midnight')
}
export function applyTheme(id: string): void {
  document.documentElement.dataset.theme = id
  try { localStorage.setItem('hsk_theme', id) } catch { /* 隐私模式忽略 */ }
  notifyShellChanged()
}

const PS_STYLES = ['default', 'rigorous', 'friendly', 'pragmatic', 'creative', 'socratic']

interface Provider { id: string; name?: string; model?: string; api_key?: string; env?: boolean }

export default function SettingsModal({ tab, onClose }: Props) {
  const [cur, setCur] = useState(tab)
  useEffect(() => { setCur(tab) }, [tab])
  return (
    <div className="overlay show" onClick={onClose} role="dialog" aria-modal="true">
      <div className="modal" style={{ width: 'min(860px, 92vw)' }} onClick={(e) => e.stopPropagation()}>
        <span className="close" onClick={onClose} aria-label="close">✕</span>
        <nav className="modal-nav">
          <h4>设置</h4>
          <ul style={{ margin: 0, padding: 0 }}>
            {TABS.map((tb) => (
              <li key={tb.id} className={tb.id === cur ? 'active' : ''} onClick={() => { setCur(tb.id); }}>{tb.label}</li>
            ))}
          </ul>
        </nav>
        <div className="modal-body">
          {cur === 'lang' && <LangTab />}
          {cur === 'theme' && <ThemeTab />}
          {cur === 'llm' && <LLMTab />}
          {cur === 'persona' && <PersonaTab />}
          {cur === 'keys' && <KeysTab />}
          {cur === 'about' && <AboutTab />}
          <div className="frow" style={{ marginTop: 18, paddingTop: 14, borderTop: '1px dashed rgb(var(--line))' }}>
            <button className="btn" onClick={onClose}>{t('cfgClose')}</button>
          </div>
        </div>
      </div>
    </div>
  )
}

function LangTab() {
  const cur = (localStorage.getItem('hsk-lang') || 'zh') as 'zh' | 'en'
  return (
    <>
      <h3>{t('langH')}</h3>
      <p className="sub">{t('langSub')}</p>
      <div className="theme-grid" style={{ gridTemplateColumns: '1fr', maxWidth: 360 }}>
        {(['en', 'zh'] as const).map((l) => (
          <div key={l} className={`theme-swatch ${cur === l ? 'active' : ''}`} data-lang={l} onClick={() => applyLang(l)}>
            <div className="nm">{l === 'en' ? t('langEn') : t('langZh')}</div>
          </div>
        ))}
      </div>
    </>
  )
}

function ThemeTab() {
  const cur = document.documentElement.dataset.theme || 'memphis'
  return (
    <>
      <h3>{t('themeH')}</h3>
      <p className="sub">{t('themeSub')}</p>
      <div className="theme-grid">
        {THEMES.map((th) => (
          <div key={th.id} className={`theme-swatch ${cur === th.id ? 'active' : ''}`} data-th={th.id} onClick={() => applyTheme(th.id)}>
            <div className="dot" style={{ background: th.bg, outline: `3px solid ${th.brand}`, outlineOffset: -3 }} />
            <div className="nm">{themeName(th.id)}</div>
          </div>
        ))}
      </div>
    </>
  )
}

function KeysTab() {
  return (
    <>
      <h3>{t('tabKeys')}</h3>
      <div className="row2"><span className="kbd">Ctrl+Enter</span><span>{t('keysSend')}</span></div>
      <div className="row2"><span className="kbd">Enter</span><span>{t('keysNewline')}</span></div>
      <div className="row2"><span className="kbd">↗ → ↓ ✕</span><span>{t('keysCards')}</span></div>
      <div className="row2"><span className="kbd">✍</span><span>{t('keysAnno')}</span></div>
      <div className="row2"><span className="kbd">?conversation=</span><span>{t('keysUrl')}</span></div>
    </>
  )
}

function AboutTab() {
  return (
    <>
      <h3>{t('tabAbout')}</h3>
      <ul className="about-li">
        <li>{t('aboutLi1')}</li>
        <li>{t('aboutLi2')}</li>
        <li>{t('aboutLi3')}</li>
        <li>{t('aboutLi4')}</li>
        <li>{t('aboutLi5')}</li>
      </ul>
    </>
  )
}

/* ---------------- 个性化（persona） ---------------- */
function PersonaTab() {
  const [persona, setPersona] = useState<any | null>(null)
  const [level, setLevel] = useState('HSK3')
  const [status, setStatus] = useState(t('psLoadingPersona'))
  const [saving, setSaving] = useState(false)

  const load = useCallback(async () => {
    try {
      const r = await api<any>('/api/profile')
      const p = (r?.profile && r.profile.persona) || {}
      setPersona(p)
      if (r?.profile?.user_level) setLevel(r.profile.user_level)
      setStatus('')
    } catch { setStatus('') }
  }, [])

  useEffect(() => { void load() }, [load])

  const save = async () => {
    setSaving(true)
    try {
      const p = await api<any>('/api/profile', {
        persona: {
          reply_style: persona?.reply_style || 'default',
          address: (persona?.address || '').trim(),
          identity: (persona?.identity || '').trim(),
          custom_instructions: (persona?.custom_instructions || '').trim(),
          interrupt_cap: parseInt(persona?.interrupt_cap ?? '3', 10),
        },
      })
      setPersona(p?.persona || persona)
      setStatus('')
      window.dispatchEvent(new CustomEvent('hsk:toast', { detail: t('psSaved') }))
    } catch (e: any) {
      setStatus(t('psSaveFail') + (e?.message || ''))
    } finally { setSaving(false) }
  }

  const saveLevel = async () => {
    try {
      await api('/api/profile', { user_level: level })
      window.dispatchEvent(new CustomEvent('hsk:toast', { detail: t('lsSaved') }))
    } catch (e: any) {
      setStatus(t('lsSaveFail') + (e?.message || ''))
    }
  }

  const set = (k: string, v: any) => setPersona((p: any) => ({ ...(p || {}), [k]: v }))

  return (
    <>
      <div className="prov-form" style={{ marginBottom: 14 }}>
        <h5>{t('lsH')}</h5>
        <p className="sub">{t('lsSub')}</p>
        <div className="frow">
          <select className="inp" style={{ width: 'auto', flex: 'none', padding: '6px 10px' }} value={level} onChange={(e) => setLevel(e.target.value)}>
            {[1, 2, 3, 4, 5, 6].map((n) => <option key={n} value={`HSK${n}`}>HSK{n}</option>)}
          </select>
          <button className="btn-brand" type="button" onClick={saveLevel}>{t('lsSave')}</button>
          {status && <span className="hint" style={{ color: 'rgb(var(--sem-bad))' }}>{status}</span>}
        </div>
      </div>
      <h3>{t('psH')}</h3>
      <p className="sub">{t('psSub')}</p>
      <div className="prov-form">
        <h5>{t('psStyle')}</h5>
        <div className="theme-grid styles">
          {PS_STYLES.map((s) => (
            <div key={s} className={`theme-swatch ${(persona?.reply_style || 'default') === s ? 'active' : ''}`}
              data-style={s}
              onClick={() => set('reply_style', s)}>
              <div className="nm">{t('psStyle_' + s)}</div>
              <div className="cd">{t('psStyleDesc_' + s)}</div>
            </div>
          ))}
        </div>
        <div className="fgrid" style={{ marginTop: 12 }}>
          <input className="inp" placeholder={t('psAddressPh')} maxLength={50} value={persona?.address || ''} onChange={(e) => set('address', e.target.value)} />
          <input className="inp" placeholder={t('psIdentityPh')} maxLength={200} value={persona?.identity || ''} onChange={(e) => set('identity', e.target.value)} />
          <input className="inp wide" placeholder={t('psInstrPh')} maxLength={500} value={persona?.custom_instructions || ''} onChange={(e) => set('custom_instructions', e.target.value)} />
        </div>
        <div className="frow">
          <label className="hint" style={{ flex: 'none', whiteSpace: 'nowrap' }}>{t('psCap')}</label>
          <input className="inp" type="number" min={0} max={10} style={{ width: 70, flex: 'none' }} value={persona?.interrupt_cap ?? 3} onChange={(e) => set('interrupt_cap', e.target.value)} />
          <span className="hint">{t('psCapHint')}</span>
        </div>
        <div className="frow">
          <button className="btn-brand" type="button" disabled={saving} onClick={save}>{saving ? t('psSaving') : t('psSave')}</button>
        </div>
      </div>
    </>
  )
}

/* ---------------- 模型密钥（LLM BYOK） ---------------- */
function LLMTab() {
  const [providers, setProviders] = useState<Provider[]>([])
  const [defaultId, setDefaultId] = useState<string | null>(null)
  const [loadMsg, setLoadMsg] = useState(t('llmLoading'))
  const [name, setName] = useState('')
  const [model, setModel] = useState('')
  const [url, setUrl] = useState('')
  const [key, setKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [testNote, setTestNote] = useState('')

  const refresh = useCallback(async () => {
    try {
      const d = await api<{ providers?: Provider[]; default_id?: string | null }>('/api/providers')
      setProviders(d?.providers || [])
      setDefaultId(d?.default_id ?? null)
      setLoadMsg('')
    } catch (e: any) { setLoadMsg(e?.message || t('llmLoading')) }
  }, [])
  useEffect(() => { void refresh() }, [refresh])

  const toast = (m: string) => window.dispatchEvent(new CustomEvent('hsk:toast', { detail: m }))

  const doTestNew = async () => {
    setTestNote('')
    setBusy(true)
    try {
      const r = await api<any>('/api/providers/test', { base_url: url.trim(), api_key: key.trim(), model: model.trim() })
      toast(r?.ok ? `✓ ${r.msg || 'ok'}` : (r?.msg || t('opFailed')))
      if (!r?.ok) setTestNote(r?.msg || '')
    } catch (e: any) { setTestNote(e?.message || t('opFailed')) }
    finally { setBusy(false) }
  }
  const doAdd = async () => {
    if (!(name.trim() && model.trim() && url.trim() && key.trim())) { setTestNote('name/base_url/api_key/model 均为必填'); return }
    setBusy(true)
    try {
      await api('/api/providers', { name: name.trim(), base_url: url.trim(), api_key: key.trim(), model: model.trim() })
      setName(''); setModel(''); setUrl(''); setKey(''); setTestNote('')
      await refresh(); toast(t('provToastAdded'))
    } catch (e: any) { setTestNote(e?.message || t('opFailed')) }
    finally { setBusy(false) }
  }
  const doTest = async (p: Provider) => {
    const r = await api<any>('/api/providers/test', { id: p.id }).catch((e: any) => ({ ok: false, msg: e?.message || '' }))
    toast(r?.ok ? `✓ ${r.msg || 'ok'}` : (r?.msg || 'fail'))
  }
  const doDefault = async (id: string) => {
    await api('/api/providers/default', { id }).catch(() => {})
    await refresh()
  }
  const doDelete = async (p: Provider) => {
    try {
      const r = await fetch('/api/providers?id=' + encodeURIComponent(p.id), { method: 'DELETE' })
      if (!r.ok) { const d = await r.json().catch(() => ({})); setTestNote(d?.error || t('opFailed')) }
      await refresh()
    } catch (e: any) { setTestNote(e?.message || t('opFailed')) }
  }

  return (
    <>
      <h3>{t('llmH')}</h3>
      <div className="prov-note" dangerouslySetInnerHTML={{ __html: t('llmNoteHtml') }} />
      {loadMsg && <div style={{ color: 'rgb(var(--tx3))' }}>{loadMsg}</div>}
      <div id="provList">
        {providers.length === 0 && !loadMsg ? <div className="clean-sub">{t('provEmpty')}</div> : null}
        {providers.map((p) => {
          const isDef = p.id === defaultId
          return (
            <div className="prov-row" data-pv={p.id} key={p.id}>
              <div style={{ minWidth: 0, flex: 1 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                  <strong>{p.name || p.id}</strong>
                  {isDef && <span className="prov-def">{t('provDefault')}</span>}
                </div>
                <div className="hint mono" style={{ whiteSpace: 'pre-wrap' }}>model={p.model}{p.api_key ? ` · key=${p.api_key}` : ''}</div>
              </div>
              <div style={{ flex: 'none', display: 'flex', gap: 6, alignItems: 'center' }}>
                {!isDef && <button className="prov-btn" type="button" onClick={() => doDefault(p.id)}>{t('provSetDef')}</button>}
                <button className="prov-btn" type="button" onClick={() => doTest(p)}>{t('provTest')}</button>
                <button className="prov-btn danger" type="button" title={t('provDel')} onClick={() => doDelete(p)}>✕</button>
              </div>
            </div>
          )
        })}
      </div>
      <div className="prov-form">
        <h5>{t('llmAdd')}</h5>
        <div className="fgrid">
          <input className="inp" placeholder={t('llmName')} value={name} onChange={(e) => setName(e.target.value)} />
          <input className="inp" placeholder={t('llmModel')} value={model} onChange={(e) => setModel(e.target.value)} />
          <input className="inp wide" placeholder={t('llmUrl')} value={url} onChange={(e) => setUrl(e.target.value)} />
          <input className="inp wide" placeholder={t('llmKey')} type="password" value={key} onChange={(e) => setKey(e.target.value)} />
        </div>
        <div className="frow">
          <button className="btn-brand" type="button" disabled={busy} onClick={doAdd}>{t('llmBtnAdd')}</button>
          <button className="prov-btn" type="button" disabled={busy} onClick={doTestNew}>{t('llmBtnTest')}</button>
          <span className="hint">{t('llmHint')}</span>
        </div>
        {testNote && <div className="hint" style={{ color: 'rgb(var(--sem-bad))' }}>{testNote}</div>}
      </div>
    </>
  )
}