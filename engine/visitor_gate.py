# ============================================================
# engine/visitor_gate.py
# P0.10 · 游客模式闸门（每日配额，BYOK 无限）
#   - 判定口径：未带供应商标识、或标识等于环境默认供应商（= 走 owner Key 的请求）→ 计入游客配额
#   - 首次访问下发 vid cookie（HttpOnly; SameSite=Lax; HTTPS 下加 Secure），不绑 learner_id
#   - 落盘式配额：{vid: {date, count}}，UTC 日期维度，跨天自动重置
#   - 阈值可配（VISITOR_QUOTA，0 = 闸门整体关闭）
# 并发安全：check_and_consume 须在调用方持有的锁内执行（serve 复用 dialog 的 self._lock）。
# 纯标准库；文件落 data/（已被 .gitignore 整目录忽略，学习者配额不上库）。
# ============================================================

import datetime
import json
import os
import pathlib
import secrets

DAILY_QUOTA = int(os.environ.get("VISITOR_QUOTA", "10"))     # 0 = 闸门整体关闭（不限流）
COOKIE_NAME = "hsk_vid"


def _utc_today() -> str:
    """今日 UTC 日期（'YYYY-MM-DD'），配额跨天维度。"""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")


def _reset_in(next_day: str) -> str:
    return f"{next_day} 00:00 自动重置（UTC）"


class VisitorGate:
    """游客每日配额闸门。存储结构：{vid: {"date": "YYYY-MM-DD", "count": int}}。

    daily_quota: None → 取环境变量 VISITOR_QUOTA；0 → 关闭计数但保留存储结构。
    每次调用 read-modify-write 需外部持锁（避免并发超发）。
    """

    def __init__(self, root: str = "data", daily_quota: int | None = None):
        self.root = str(root)
        self.quota_path = os.path.join(self.root, "visitor_quota.json")
        self.daily_quota = DAILY_QUOTA if daily_quota is None else daily_quota
        self._data = self._load()

    # ---------------- 存储 ----------------
    def _load(self) -> dict:
        try:
            with open(self.quota_path, encoding="utf-8") as f:
                raw = json.load(f)
                return raw if isinstance(raw, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        try:
            os.makedirs(self.root, exist_ok=True)
            tmp = self.quota_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False)
            os.replace(tmp, self.quota_path)   # 原子替换，防并发写坏
        except OSError:
            pass   # 落盘失败不阻断对话（宁可放行，配额数据尽力而为）

    # ---------------- cookie ----------------
    @staticmethod
    def _is_https(handler) -> bool:
        xff = (handler.headers.get("X-Forwarded-Proto") or "").lower()
        return xff == "https"

    def ensure_vid(self, handler) -> str:
        """读 Cookie 头；无则生成 vid 并暂存待下发（首次访问）。不绑 learner_id。

        下发时机：暂存到 handler._vid_cookie，由 serve._send_json 统一冲刷
        （Send-Cookie 必须在 send_response 之后、end_headers 之前写）。"""
        header = handler.headers.get("Cookie") or ""
        for part in header.split(";"):
            k, _, v = part.strip().partition("=")
            if k == COOKIE_NAME and v:
                return v
        vid = secrets.token_urlsafe(16)
        secure = "; Secure" if self._is_https(handler) else ""
        handler._vid_cookie = f"{COOKIE_NAME}={vid}; Path=/; HttpOnly; SameSite=Lax{secure}"
        return vid

    # ---------------- 配额 ----------------
    def check_and_consume(self, vid: str):
        """在调用方锁内执行（原子）。返回 (放行?, 今日剩余, 重置说明)。

        - 当日计数 >= daily_quota → 拒（不计数、不落盘 = "次数未增"）
        - 未满 → 计数 +1、落盘、放行
        - daily_quota <= 0 → 恒放行（闸门关闭，不计数）
        """
        if self.daily_quota <= 0:
            return True, -1, "额度不限"

        today = _utc_today()
        entry = self._data.get(vid)
        if not isinstance(entry, dict):
            entry = {}
        if entry.get("date") != today:
            entry = {"date": today, "count": 0}   # 跨天自动重置
        if entry["count"] >= self.daily_quota:
            return False, 0, _reset_in(today)

        entry["count"] += 1
        self._data[vid] = entry
        self._save()
        remaining = self.daily_quota - entry["count"]
        return True, remaining, _reset_in(today)