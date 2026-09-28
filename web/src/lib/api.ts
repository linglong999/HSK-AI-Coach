// lib/api.ts · HTTP 封装（忠实对齐 web_legacy /api 契约）
import { t } from './i18n'

/** GET 或标准 JSON POST；自定义状态码返回 {ok, msg, code} 形状由调用方判别 */
export async function api<T = any>(url: string, body?: object): Promise<T> {
  let r: Response
  try {
    r = await fetch(url, body
      ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
      : undefined)
  } catch {
    throw new Error(t('errConn'))
  }
  let data: any
  try {
    data = await r.json()
  } catch {
    data = { msg: `HTTP ${r.status}` }
  }
  if (!r.ok) {
    const e: any = new Error(data?.msg || data?.message || `${t('errReq')}${r.status}`)
    e.code = data?.code || data?.error || ''
    e.data = data
    throw e
  }
  return data as T
}

/** GET 快捷封装（保持泛型，供页面直接调用） */
export async function getJson<T = any>(url: string): Promise<T> {
  return api<T>(url)
}

/** 标准 JSON POST 快捷封装 */
export async function postJson<T = any>(url: string, body?: object): Promise<T> {
  return api<T>(url, body)
}