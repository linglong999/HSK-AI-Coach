// pages/Help.tsx · 帮助页：能量配额换算公式 + 三档拦截说明
// 公式费率 = engine/quota.py QUOTA_PARAMS 哨兵位（内测占位，上线按真实成本回填）。
// 实时额度读 GET /api/quota 现态（与 QuotaBar 同源）。
import { useEffect, useState } from 'react'
import { getJson } from '../lib/api'

const IN_RATE = 1 // QUOTA_PARAMS.energy_per_1k_input
const OUT_RATE = 2 // QUOTA_PARAMS.energy_per_1k_output

interface Quota {
  energy_left: number
  daily_total: number
  est_cost: number
  round_count: number
  reset_at: string
}

export default function Help() {
  const [q, setQ] = useState<Quota | null>(null)
  const [err, setErr] = useState('')

  useEffect(() => {
    getJson<Quota>('/api/quota')
      .then((d) => setQ(d))
      .catch((e) => setErr(String(e)))
  }, [])

  return (
    <div className="panel">
      <h2>能量配额说明</h2>
      {err && <p className="warn">额度读取失败：{err}</p>}

      <section>
        <h3>「能量分」是什么</h3>
        <p>
          每次开口会话，系统按真实模型用量折算成统一「能量分」，让你大概知道自己今天还剩多少预算，
          不用关心背后的 token 细节。
        </p>
      </section>

      <section>
        <h3>换算公式</h3>
        <p className="formula">
          能量分 = 输入token ÷ 1000 × {IN_RATE} ＋ 输出token ÷ 1000 × {OUT_RATE}
        </p>
        <ul className="help-list">
          <li>输入（你的话 + 上下文记忆）按每千 token {IN_RATE} 分计。</li>
          <li>输出（教练的回答）按每千 token {OUT_RATE} 分计 —— 输出更贵。</li>
          <li>失败的回合（零产出 / 出错）<b>不扣能量</b>，不委屈你。</li>
        </ul>
        <p className="muted">当前费率为内测占位值，正式定价后会同步更新。</p>
      </section>

      <section>
        <h3>今日实时额度</h3>
        {q ? (
          <div className="quota-help">
            <p>今日剩余 <b>{q.energy_left}</b> / {q.daily_total} 分 · 预计每回合 ≈ {q.est_cost} 分</p>
            <p className="muted">本会话已进行 {q.round_count} 回合 · 将于 {q.reset_at} 刷新</p>
          </div>
        ) : (
          <p className="muted">载入实时额度中…</p>
        )}
      </section>

      <section>
        <h3>三档拦截</h3>
        <ul className="help-list">
          <li><b>低位提醒</b>：剩余能量偏低时，会提醒你剩多少，不打断。</li>
          <li><b>耗尽拦截</b>：当日能量用完后无法再开口，等次日刷新。</li>
          <li><b>回合硬限</b>：单次会话最多 12 回合，防连续生成失控。</li>
        </ul>
      </section>
    </div>
  )
}