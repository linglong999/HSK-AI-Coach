import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './styles.css'

try { document.documentElement.dataset.theme = localStorage.getItem('hsk_theme') || 'memphis' } catch { /* 隐私模式走默认 */ }

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)