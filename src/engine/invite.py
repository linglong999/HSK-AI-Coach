# ============================================================
# engine/invite.py · B7 S5 · 0→1 邀请制 allowlist
# 演示账户为定向邀请内测（30–50 真实学习者），不公开注册。
#   - 白名单来源：config.settings.INVITE_ALLOWLIST（逗号分隔 open_id；空=闸门关闭）
#   - 判定：闸门开启(非空)时，请求须带有效 open_id（query `open_id=` 或 Cookie `hsk_openid`）
#   - 拦截：未授权 → 页面只出邀请页（不公开 speak/图谱入口）；/api/* → 403 invite_required
# 纯标准库，零 LLM/存储外部依赖；serve 在静态托管前加载守卫。
# 默认(白名单为空)守卫完全旁路——既有 13 条路由契约零变化、测试零影响。
# ============================================================

# 邀请页 HTML（内联常量，不依赖任何静态目录；serve 直接返给未授权页面请求）
INVITE_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>汉语 AI 教练 · 邀请制内测</title>
<style>
  :root { color-scheme: light dark; }
  body { margin:0; min-height:100vh; display:grid; place-items:center;
         font-family: system-ui, -apple-system, "Segoe UI", "PingFang SC",
                      "Microsoft YaHei", sans-serif; background:#0f172a; color:#e2e8f0; }
  .card { max-width:420px; padding:40px 32px; text-align:center;
          background:#1e293b; border:1px solid #334155; border-radius:16px; }
  h1 { font-size:20px; margin:0 0 12px; }
  p  { margin:0 0 20px; color:#94a3b8; line-height:1.7; font-size:14px; }
  .badge { display:inline-block; padding:4px 12px; border-radius:999px;
           background:#0ea5e9; color:#fff; font-size:12px; margin-bottom:16px; }
  .hint { font-size:12px; color:#64748b; }
</style>
</head>
<body>
  <div class="card">
    <span class="badge">邀请制内测中</span>
    <h1>汉语 AI 教练</h1>
    <p>当前为定向邀请内测阶段，尚未开放公开访问。<br/>已有邀请名额的学习者可从邀请链接进入。</p>
    <p class="hint">如需联系，请在邀请邮件中回复。</p>
  </div>
</body>
</html>
"""

OPENID_COOKIE = "hsk_openid"


def parse_allowlist(raw) -> set:
    """逗号分隔白名单 → 去空白/去空/去重。None/空 → 空集。"""
    if not raw:
        return set()
    return {item.strip() for item in str(raw).split(",") if item and item.strip()}


def is_open(allowlist: set) -> bool:
    """闸门状态：白名单为空 = 开放（不拦截）；非空 = 邀请制开启。"""
    return not allowlist


def check_openid(allowlist: set, open_id) -> bool:
    """闸门开启(白名单非空)时，open_id 须非空且在名单内 → True 放行。"""
    if not allowlist:
        return True
    return bool(open_id) and str(open_id).strip() in allowlist