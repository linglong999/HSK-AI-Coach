# ============================================================
# engine/intervention.py
# 0.22 方向3 · 介入时机（确定性函数，MAX_STEPS 经验类比）
# 设计稿 §3.1：组合信号（主动求助/输出空/含义不清/连续错率）分三档：
#   none  → 流利产出：静默记录（图谱照常喂入），不打扰
#   light → 轻介入：引导提问，不下结论
#   block → 阻断讲解：直接讲解（含母语迁移归因，标"可能原因"）
# 升级规则（D3.2 轻介入起步，视情况升级）：light 后下一句仍触发 struggling
#   信号（empty/streak）→ 自动升 block。
# 默认阈值：cap=3（每场对话打断上限）、max_conf<0.55、近5句≥3错（计数制，
#   不足5句时按"满3条错误才触发"，防单句误伤）。
# 纯标准库、零 LLM；判定完全确定性可测。
# ============================================================

import json
import re
from typing import Any, Dict, List, Optional, Tuple

# 预扫上限：超过视为"学习材料/文档"而非产出句，不做识别与介入（交给 parse_document）
MAX_SCAN_CHARS = 120

# 打断上限默认（persona.interrupt_cap 可覆盖）
DEFAULT_CAP = 3

# 含义不清阈值：识别置信（errors∪uncertain 的最大 confidence）低于此值
UNCLEAR_CONF = 0.55

# 连续错率：近 5 句中 ≥3 句有偏误 → streak（"近5句≥3错"字面实现）
STREAK_WINDOW = 5
STREAK_MIN_ERRORS = 3

# 输出空/犹豫：去标点空白后内容字符 < 2（"你好"不算空；"嗯…"算）
_EMPTY_MIN_CHARS = 2

# ---------------- 主动求助（meta 语言求助，非场景内容） ----------------
# 精确匹配"问语言本身"的句式，避开场景产出（"坐这里对吗""帮我看看菜单"不是求助）
_HELP_PATTERNS = [
    # 怎么说/怎么讲/怎么写/怎么用/怎么表达/怎么读
    re.compile(r"怎么(说|讲|写|用|表达|读)"),
    re.compile(r"(应该|可以)怎么说"),
    # 词义/辨析
    re.compile(r"什么意思|什么区别|有什么区别"),
    # 偏误自查
    re.compile(r"有错|有语病|错在哪|哪儿错了|错了吗"),
    re.compile(r"这句(话|子)?[^\n]{0,8}(对|错|吗|行|自然)"),
    re.compile(r"(说|写|讲|用)得?(对|错|好|自然|地道)"),
    re.compile(r"语法[^\n]{0,6}(对|错|吗|用)"),
    re.compile(r"(更|怎么|怎么才)[^\n]{0,2}(自然|地道)"),
    re.compile(r"帮我(改|检查|纠正)|帮我看看这句|帮我看看这个"),
    re.compile(r"教我(怎么|说|用|写)"),
    # 英文（教学层求助，场景产出是中文）
    re.compile(r"how (do i|do you|to|should i|can i) (say|write|use|express|ask)", re.I),
    re.compile(r"what('| i)?s? (the )?(meaning|difference)", re.I),
    re.compile(r"what does .{0,24}(mean|differ)", re.I),
    re.compile(r"is (this|it|that) (correct|right|natural|wrong)", re.I),
    re.compile(r"does (this|it|that) make sense", re.I),
    re.compile(r"any (mistakes?|errors?)", re.I),
    re.compile(r"(correct|fix|check) (this|it|my)", re.I),
    re.compile(r"why is (this|it|that) wrong", re.I),
    re.compile(r"translate", re.I),
]

# 中英标点与空白（内容长度统计时剥离）
_STRIP_RE = re.compile(
    r"[\s，。！？；：、""''（）《》【】…—·~,\.!\?;:'\"()\[\]<>#*_`~@\$%\^&\+\-=\|\\/]+")


def _content_len(text: str) -> int:
    """去标点/空白后的内容字符数（判断犹豫空句）。"""
    return len(_STRIP_RE.sub("", text or ""))


def detect_help_intent(text: str) -> bool:
    """主动求助意图：问"怎么说/什么意思/对不对"等 meta 语言问题。
    场景产出句（"坐这里对吗""帮我看看菜单"）不命中——宁漏勿错。"""
    t = str(text or "").strip()
    if not t:
        return False
    return any(p.search(t) for p in _HELP_PATTERNS)


def decide_intervention(text: str,
                        max_conf: float = 1.0,
                        recent_error_flags: Optional[List[bool]] = None,
                        interrupt_used: int = 0,
                        cap: int = DEFAULT_CAP,
                        last_level: str = "none") -> Tuple[str, str]:
    """介入分档（纯函数）。返回 (level, reason)。

    level: none|light|block；reason: fluent|help|empty|unclear|streak|upgrade|cap
    - 打断上限优先：已用满 → 静默（none/cap）
    - ask_help / unclear → block（语义阻断：讲解含归因）
    - empty / streak → light（引导提问）；light 后再触发 struggling → 升 block
    - 其余 → none（静默记录）
    """
    if interrupt_used >= cap:
        return ("none", "cap")
    ask_help = detect_help_intent(text)
    empty = _content_len(text) < _EMPTY_MIN_CHARS
    unclear = float(max_conf) < UNCLEAR_CONF
    flags = [bool(f) for f in (recent_error_flags or [])][-STREAK_WINDOW:]
    streak = sum(1 for f in flags if f) >= STREAK_MIN_ERRORS

    if ask_help:
        return ("block", "help")
    if unclear:
        return ("block", "unclear")
    if empty or streak:
        # 升级规则：上一句已轻介入、本句仍在挣扎（empty/streak）→ 升阻断
        if last_level == "light":
            return ("block", "upgrade")
        return ("light", "empty" if empty else "streak")
    return ("none", "fluent")


class InterventionTracker:
    """按会话（conversation_id）跟踪介入状态：打断计数 / 近错窗口 / 上轮档位。
    进程内存态（serve 单例），重启即重置——打断上限是"每场对话"的软约束，可接受。"""

    def __init__(self):
        self.interrupt_used = 0
        self.flags: List[bool] = []       # 近 STREAK_WINDOW 句"有无偏误"
        self.last_level = "none"

    def observe(self, text: str, max_conf: float = 1.0,
                error_flag: Optional[bool] = None,
                cap: int = DEFAULT_CAP) -> Tuple[str, str]:
        """一轮产出句的介入判定（含状态更新）。
        error_flag：本轮预扫是否发现偏误；None=未预扫（求助句/材料），不入窗口。"""
        if error_flag is not None:
            self.flags = (self.flags + [bool(error_flag)])[-STREAK_WINDOW:]
        level, reason = decide_intervention(
            text, max_conf=max_conf, recent_error_flags=self.flags,
            interrupt_used=self.interrupt_used, cap=cap,
            last_level=self.last_level)
        if level in ("light", "block"):
            self.interrupt_used += 1
        self.last_level = level
        return level, reason

    def snapshot(self, cap: int = DEFAULT_CAP) -> Dict[str, Any]:
        return {"interrupt_used": self.interrupt_used, "cap": cap,
                "last_level": self.last_level}


# ---------------- [Intervention] 注入段（双语） ----------------

_LEVEL_LABEL = {
    "zh": {"none": "静默记录", "light": "轻介入", "block": "阻断讲解"},
    "en": {"none": "stay silent", "light": "light touch", "block": "step in"},
}
_REASON_LABEL = {
    "zh": {"fluent": "流利产出", "help": "主动求助", "empty": "输出犹豫",
           "unclear": "含义不清", "streak": "连续出错", "upgrade": "持续卡壳·升级",
           "cap": "已达打断上限", "material": "学习材料"},
    "en": {"fluent": "fluent output", "help": "asked for help", "empty": "hesitating",
           "unclear": "unclear meaning", "streak": "repeated errors",
           "upgrade": "still stuck after light touch", "cap": "interrupt cap reached",
           "material": "learning material"},
}


def _compact_recognition(recognition: Optional[Dict[str, Any]]) -> str:
    """预扫结果压缩为注入用摘要（errors 前3 + hypotheses 前3，截长防 prompt 膨胀）。"""
    if not isinstance(recognition, dict):
        return ""
    errors = [
        {"fragment": e.get("fragment", ""), "correction": e.get("correction", ""),
         "type": e.get("type", ""), "confidence": e.get("confidence")}
        for e in (recognition.get("errors") or [])[:3]
        if isinstance(e, dict)
    ]
    hypotheses = [
        {"l1_anchor": h.get("l1_anchor", ""), "correction": h.get("correction", "")}
        for h in (recognition.get("hypotheses") or [])[:3]
        if isinstance(h, dict)
    ]
    payload: Dict[str, Any] = {"errors": errors}
    if hypotheses:
        payload["l1_hypotheses"] = hypotheses
    raw = json.dumps(payload, ensure_ascii=False)
    return raw[:800]


def build_intervention_directive(level: str, reason: str, native_lang: str = "",
                                 recognition: Optional[Dict[str, Any]] = None) -> str:
    """把本轮判定编译成注入主链的 [Intervention] 段。
    recognition：预扫识别结果（None=未预扫，如求助句/材料——不限制 identify_errors）。
    返回空串 = 不注入（无判定/材料句）。"""
    if not level or level not in ("none", "light", "block"):
        return ""
    is_zh = not (native_lang and str(native_lang).lower() not in
                 ("zh", "中文", "汉语", "chinese", "汉语官话"))
    scanned = isinstance(recognition, dict)
    lvl = _LEVEL_LABEL["zh" if is_zh else "en"].get(level, level)
    rsn = _REASON_LABEL["zh" if is_zh else "en"].get(reason, reason)

    if is_zh:
        head = f"[Intervention] 本轮介入判定：{lvl}（{rsn}）。本段优先于全局规则3的技能选用建议。"
        body_map = {
            "none": "学习者本轮流利产出、无需介入：自然回应对话内容即可；"
                    "不要主动纠错、不要讲解偏误（后台已静默记入学习记录）。"
                    + ("识别已在后台完成，本轮无需再调 identify_errors。" if scanned else ""),
            "light": "学习者本轮犹豫或连续出错：请轻介入——用一两个引导性提问带学习者"
                     "自己说对，不直接下结论、不讲解规则。"
                     + ("识别已在后台完成，本轮无需再调 identify_errors。" if scanned else ""),
            "block": ("学习者主动求助：请直接回应其问题并讲清楚"
                      "（可按需用 lookup_knowledge_point / retrieve_corpus / explain_error）。"
                      if reason == "help" else
                      "学习者本轮含义不清或持续卡壳：请直接介入讲解——指出问题、给出正确说法"
                      "（若疑似母语迁移影响，说明可能原因，不下定论）。")
                     + ("识别已在后台完成，结果见下，无需再调 identify_errors。" if scanned else ""),
        }
    else:
        head = (f"[Intervention] This turn: {lvl} ({rsn}). This section takes "
                "precedence over rule 3's skill-selection advice.")
        body_map = {
            "none": "The learner is producing fluently this turn — no intervention "
                    "needed: respond naturally to the conversation; do NOT correct "
                    "or explain errors (already recorded silently in the background)."
                    + ("Recognition already ran in the background — do not call "
                       "identify_errors again this turn." if scanned else ""),
            "light": "The learner is hesitating or repeating errors: intervene "
                     "lightly — guide with one or two leading questions so they "
                     "say it right themselves; do not state conclusions or rules. "
                     + ("Recognition already ran in the background — do not call "
                       "identify_errors again this turn." if scanned else ""),
            "block": ("The learner asked for help: answer their question directly "
                      "and clearly (use lookup_knowledge_point / retrieve_corpus / "
                      "explain_error as needed)."
                      if reason == "help" else
                      "The learner's meaning is unclear or they keep getting stuck: "
                      "step in and explain — point out the problem and give the "
                      "correct way to say it (if L1 transfer is suspected, mention "
                      "the likely cause without asserting it).")
                     + (" Recognition already ran in the background — see below; "
                        "do not call identify_errors again this turn." if scanned else ""),
        }
    parts = [head, body_map[level]]
    if scanned and level in ("light", "block"):
        compact = _compact_recognition(recognition)
        if compact:
            label = ("本轮识别结果（已记入学习记录）：" if is_zh
                     else "Recognition result for this turn (already recorded): ")
            parts.append(label + compact)
    return "\n".join(parts)


__all__ = [
    "MAX_SCAN_CHARS", "DEFAULT_CAP", "UNCLEAR_CONF",
    "detect_help_intent", "decide_intervention", "InterventionTracker",
    "build_intervention_directive",
]
