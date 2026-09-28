// components/ThemeSwitcher.tsx · memphis 三主题切换（玫红/暖橙/金暗），localStorage 持久化
import { useState } from 'react'

const THEMES = [
  { key: 'memphis', label: '孟菲斯 · 玫红' },
  { key: 'warm', label: '暖橙' },
  { key: 'midnight', label: '金暗 · 夜' },
]

export default function ThemeSwitcher() {
  const [cur, setCur] = useState(() => document.documentElement.dataset.theme || 'memphis')
  const pick = (t: string) => {
    document.documentElement.dataset.theme = t
    try { localStorage.setItem('hsk_theme', t) } catch { /* 隐私模式忽略 */ }
    setCur(t)
  }
  return (
    <div className="theme-switch" role="group" aria-label="主题切换">
      {THEMES.map((th) => (
        <button
          key={th.key}
          type="button"
          className={`tdot${cur === th.key ? ' on' : ''}`}
          data-t={th.key}
          onClick={() => pick(th.key)}
          aria-label={th.label}
          title={th.label}
        ><span /></button>
      ))}
    </div>
  )
}