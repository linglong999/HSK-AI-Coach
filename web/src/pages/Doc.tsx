// pages/Doc.tsx · 阅读材料：粘贴文本 / .txt .md / 图片 PDF(OCR) → 作为对话发给 AI 解析知识点
import { useRef, useState } from 'react'
import { api } from '../lib/api'
import { t } from '../lib/i18n'
import { setPendingMessage } from '../lib/outbox'

const CHAR_LIMIT = 6000

export default function Doc() {
  const [text, setText] = useState('')
  const [status, setStatus] = useState('')
  const txtRef = useRef<HTMLInputElement>(null)
  const ocrRef = useRef<HTMLInputElement>(null)
  const over = text.length > CHAR_LIMIT

  const toast = (m: string) => window.dispatchEvent(new CustomEvent('hsk:toast', { detail: m }))

  const loadTxt = (f: File) => {
    const rd = new FileReader()
    rd.onload = () => { setText(String(rd.result || '').slice(0, CHAR_LIMIT)); toast(t('docToastLoaded').replace('{name}', f.name)) }
    rd.onerror = () => toast(t('docToastFail'))
    rd.readAsText(f, 'utf-8')
  }

  const ocr = async (fs: File[]) => {
    setStatus(t('docOcrBusy'))
    const t0 = Date.now()
    try {
      const items = []
      for (const f of fs) {
        const b64 = await new Promise<string>((res, rej) => {
          const rd = new FileReader()
          rd.onload = () => { const s = String(rd.result || ''); res(s.slice(s.indexOf(',') + 1)) }
          rd.onerror = rej
          rd.readAsDataURL(f)
        })
        items.push({ name: f.name, data: b64 })
      }
      const d = await api<any>('/api/ocr', { list: items })
      const nxt = String(d?.text || '').trim()
      if (!nxt) { setStatus(t('docOcrEmpty')); return }
      setText((cur) => ((cur ? cur + '\n\n' : '') + nxt.slice(0, CHAR_LIMIT)))
      setStatus(t('docOcrDone').replace('{n}', String(d.files)).replace('{ms}', String(Date.now() - t0)).replace('{chars}', String(d.chars)))
    } catch (e: any) {
      const m = e?.message || ''
      if (/not supported|ocr_unavailable/i.test(m)) { setStatus(t('docOcrEmpty')); toast(t('docToastEmpty')) }
      else { setStatus(t('docToastFail')); toast(m) }
    } finally { if (ocrRef.current) ocrRef.current.value = '' }
  }

  const onFiles = (e: React.ChangeEvent<HTMLInputElement>, mode: 'txt' | 'ocr') => {
    const fs = Array.prototype.slice.call(e.target.files || []).filter(Boolean) as File[]
    if (!fs.length) return
    if (mode === 'txt') { loadTxt(fs[0]); if (txtRef.current) txtRef.current.value = '' }
    else void ocr(fs)
  }

  const parseSend = () => {
    let tx = text.trim()
    if (!tx) { toast(t('docToastEmpty')); return }
    if (tx.length > CHAR_LIMIT) tx = tx.slice(0, CHAR_LIMIT)
    setText('')
    setPendingMessage(t('docPrompt') + tx, t('docBadge'))
    location.hash = '#/'
  }

  return (
    <div className="pad">
      <h2>{t('docTitleModal')}</h2>
      <p className="sub" dangerouslySetInnerHTML={{ __html: t('docSubHtml') }} />
      <div className="frow" style={{ flexWrap: 'wrap', gap: 8 }}>
        <label className="filebtn">
          <span>{t('docFileBtn')}</span>
          <input ref={txtRef} type="file" accept=".txt,.md,.markdown,text/plain,text/markdown" onChange={(e) => onFiles(e, 'txt')} />
        </label>
        <label className="filebtn">
          <span>{t('docFileOcrBtn')}</span>
          <input ref={ocrRef} type="file" accept=".pdf,.png,.jpg,.jpeg,.bmp,.webp" onChange={(e) => onFiles(e, 'ocr')} multiple />
        </label>
      </div>
      <textarea
        className="doc-textarea"
        placeholder={t('docPh')}
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={14}
        style={{ width: '100%', boxSizing: 'border-box', fontFamily: 'inherit', fontSize: 15, lineHeight: 1.7, padding: 12 }}
      />
      <div className="frow" style={{ marginTop: 6 }}>
        <button className="btn primary" type="button" disabled={!text.trim()} onClick={parseSend}>{t('docParse')}</button>
        <span className={`hint ${over ? 'over' : ''}`}>{t('charCt').replace('{n}', String(text.length))}{over ? t('charOver') : ''}</span>
      </div>
      <p className="hint" style={{ marginTop: 10 }}>
        {status || t('docNote')}
      </p>
    </div>
  )
}