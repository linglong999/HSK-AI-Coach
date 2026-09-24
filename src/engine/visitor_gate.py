# ============================================================
# engine/visitor_gate.py
# P0.10 · 游客模式闸门（每日配额，BYOK 无限）→ B7 S3 · 能量日档位三档拦截
#   计量升级：按次 count → fixed-window 日档位（[B7-A] 聚焦 4：非 token bucket）
#     存储 {vid: {date, energy_used, session_id, round_count}}，UTC 跨天重置。
#   判定口径：未带供应商标识、或标识等于环境默认供应商（= 走 owner Key 的请求）→ 计入游客配额
#   首访下发 vid cookie（HttpOnly; SameSite=Lax; HTTPS 下加 Secure），不绑 learner_id
#   能量判据：check_start（入口校验，禁新/禁超回合）→ 产出回合 settle（扣能量+回合计数）
#   - energy_left 用尽 → tier="exhaust"（禁新会话）
#   - 单会话 round_count ≥ round_cap(12) → tier="round_cap"（强制收尾不截断已产出）
#   - energy_left 低于日额度 low_ratio → tier="low"（余量告急轻提示）
#   文案走设计总纲 §7 第三节草案（MSG_* 常量）。
# 并发安全：本类方法须在调用方持有的锁内执行（serve 复用 dialog 的 self._lock）。
# 纯标准库；文件落 data/（已被 .gitignore 整目录忽略，学习者配额不上库）。
# ============================================================

import datetime
import json
import os
import pathlib
import secrets

DAILY_ENERGY = float(os.environ.get("VISITOR_ENERGY", "100"))   # 每日能量分上限；≤0 关闭
ROUND_CAP = 12                                                  # 单会话回合硬上限
LOW_RATIO = 0.25                                                # 余量 < 25% 日额度 → low
COOKIE_NAME = "hsk_vid"

# 设计总纲 §7 第三节 · 三档文案（验证后回调，初值 2026-09-20 已锁）
MSG_LOW = "今天还能再聊一小段，随时回来继续。"
MSG_EXHAUST = ("今天的会话额度用完了。不过复习队列里还有几个小练习，"
               "可以接着练，不耽误你巩固今天的收获。")
MSG_ROUND_CAP = ("这次我们先聊到这，你今天这几句已经很有进步了。"
                 "想接着练，明天再来开一段新的。")


def _utc_today() -> str:
    """今日 UTC 日期（'YYYY-MM-DD'），配额跨天维度。"""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")


def _reset_in(next_day: str) -> str:
    return f"{next_day} 00:00 自动重置（UTC）"


def _norm_energy(v) -> float:
    try:
        v = float(v)
        return v if v > 0 else 0.0
    except (TypeError, ValueError):
        return 0.0


def _tier_for(energy_limit: float, energy_used: float, round_count: int,
              round_cap: int, low_ratio: float):
    """确定性档位判定（纯函数，可单测）：round_cap > exhaust > low > None。
    energy_limit ≤ 0（闸门关闭）→ 恒 None（不限）。"""
    if energy_limit <= 0:
        return None
    if round_count >= round_cap:
        return "round_cap"
    left = max(0.0, energy_limit - energy_used)
    if left <= 0:
        return "exhaust"
    if left < energy_limit * low_ratio:
        return "low"
    return None


class VisitorGate:
    """游客每日能量档位闸门。存储：{vid: {"date", "energy_used", "session_id",
    "round_count"}}。

    energy_limit: None → 取环境变量 VISITOR_ENERGY；≤0 → 闸门关闭（不限流）。
    round_cap: 单会话回合硬上限（默认 12）。low_ratio: 余量告急比例。
    每次写操作 read-modify-write 需外部持锁（避免并发超发）。
    两阶段语义：
      check_start(vid, session_id) —— 回合开始前校验，纯只读归位、不落盘。
      settle(vid, session_id, energy) —— 体面产出回合结算（扣能量+回合计数+1）。
    """

    def __init__(self, root: str = "data", energy_limit: float | None = None,
                 round_cap: int = ROUND_CAP, low_ratio: float = LOW_RATIO):
        self.root = str(root)
        self.quota_path = os.path.join(self.root, "visitor_quota.json")
        self.energy_limit = DAILY_ENERGY if energy_limit is None else float(energy_limit)
        self.round_cap = int(round_cap)
        self.low_ratio = float(low_ratio)
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
        相爱时制下发：暂存到 handler._vid_cookie，由 serve._send_json 统一冲刷。"""
        header = handler.headers.get("Cookie") or ""
        for part in header.split(";"):
            k, _, v = part.strip().partition("=")
            if k == COOKIE_NAME and v:
                return v
        vid = secrets.token_urlsafe(16)
        secure = "; Secure" if self._is_https(handler) else ""
        handler._vid_cookie = f"{COOKIE_NAME}={vid}; Path=/; HttpOnly; SameSite=Lax{secure}"
        return vid

    # ---------------- 档案归位（只读，不判别） ----------------
    def _entry(self, vid: str, session_id: str | None) -> dict:
        """取今日该 vid 的档位记录；跨天重置；换会话（session_id 不同）重置回合计数。
        返回新 dict（不写 self._data——写由 settle 负责）。"""
        today = _utc_today()
        e = self._data.get(vid)
        if not isinstance(e, dict) or e.get("date") != today:
            e = {"date": today, "energy_used": 0.0}
        energy_used = _norm_energy(e.get("energy_used"))
        if session_id is not None and e.get("session_id") != session_id:
            e = {"date": today, "energy_used": energy_used,
                 "session_id": session_id, "round_count": 0}
        return e

    def _energy_left(self, e: dict) -> float:
        if self.energy_limit <= 0:
            return -1.0
        return max(0.0, self.energy_limit - _norm_energy(e.get("energy_used")))

    def _state(self, e: dict) -> dict:
        """对前端/SSE 的现态字典（不含内部 session_id）。"""
        return {
            "energy_left": round(self._energy_left(e), 3),
            "daily_total": round(self.energy_limit, 3),
            "est_cost": self._est_cost(e),
            "round_count": int(e.get("round_count", 0)) if self.energy_limit > 0 else 0,
            "reset_at": _reset_in(_utc_today()),
        }

    def _est_cost(self, e: dict) -> float:
        """预计消耗：本会话最近一回合结算能量（有则回显，无则给一回合经验估计）。
        实参三缺上线后按真实汇率回填；此处为确定性哨兵。"""
        last = e.get("last_energy")
        if isinstance(last, (int, float)):
            return round(float(last), 3)
        # 经验回合估计：约 1k in + 0.5k out（对齐 QUOTA_PARAMS 哨兵费率 ≈ 2 分）
        return 2.0

    # ---------------- 入口校验 ----------------
    def check_start(self, vid: str, session_id: str | None = None):
        """回合开始前校验（在调用方锁内）：能否开新回合。只读归位、不落盘。
        返回 (allowed, tier_or_None, state)：
          - 能量用尽 → (False, "exhaust", state)
          - 本会话回合已达 cap → (False, "round_cap", state)
          - 闸门关闭（energy_limit≤0）→ (True, None, 不限 state)。"""
        e = self._entry(vid, session_id)
        if self.energy_limit <= 0:
            st = {"energy_left": -1, "daily_total": 0, "est_cost": self._est_cost(e),
                  "round_count": 0, "reset_at": "额度不限"}
            return True, None, st
        tier = _tier_for(self.energy_limit, _norm_energy(e.get("energy_used")),
                         int(e.get("round_count", 0)), self.round_cap, self.low_ratio)
        return (tier is None), tier, self._state(e)

    # ---------------- 产出回合结算 ----------------
    def settle(self, vid: str, session_id: str | None = None, energy: float = 0.0):
        """体面产出的回合结算：扣能量 + 回合计数 +1（跨天/换会话先归位）。
        仅参与方判定"已体面产出"的回合调用（失败/零产出不调 = 不扣）。
        返回 (tier_or_None, state)：tier ∈ low/exhaust/round_cap/None 供 SSE intercept。"""
        energy = _norm_energy(energy)
        e = self._entry(vid, session_id)
        if self.energy_limit <= 0:
            st = {"energy_left": -1, "daily_total": 0, "est_cost": self._est_cost(e),
                  "round_count": 0, "reset_at": "额度不限"}
            return None, st
        e["energy_used"] = _norm_energy(e.get("energy_used")) + energy
        e["round_count"] = int(e.get("round_count", 0)) + 1
        if energy:                      # 记住本回合实际消耗作 est_cost（0 则不覆盖）
            e["last_energy"] = energy
        if session_id is not None:
            e["session_id"] = session_id
        self._data[vid] = e
        self._save()
        tier = _tier_for(self.energy_limit, _norm_energy(e.get("energy_used")),
                         int(e.get("round_count", 0)), self.round_cap, self.low_ratio)
        return tier, self._state(e)

    # ---------------- 现态（GET /api/quota 首屏，只读不落盘） ----------------
    def get_state(self, vid: str, session_id: str | None = None) -> dict:
        e = self._entry(vid, session_id)
        return self._state(e)