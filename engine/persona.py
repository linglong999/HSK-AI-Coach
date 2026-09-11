# ============================================================
# engine/persona.py
# 0.22 方向3 · 个性化栏（设计稿 §3.2，用户拍板单字段 reply_style 六档）
#   - normalize_persona：校验/纠正/白名单合并（POST /api/profile 写入前）
#   - build_persona_brief：persona → [Persona] 注入段（双语，与 0.21 语言指令并行）
# 存储位：LearnerMemory profile.persona（data/memory_<learner>.json）
# 共处规则（设计稿 §3.3）：identity 优先于 scene role；default 回退 0.21 默认。
# ============================================================

import re
from typing import Any, Dict, Optional

REPLY_STYLES = ("default", "rigorous", "friendly", "pragmatic", "creative", "socratic")

DEFAULT_PERSONA: Dict[str, Any] = {
    "reply_style": "default",
    "address": "",
    "identity": "",
    "custom_instructions": "",
    "interrupt_cap": 3,
}

# 自由文本字段 → 最大长度（防 prompt 膨胀/注入面）
_TEXT_LIMITS = {"address": 50, "identity": 200, "custom_instructions": 500}

# 六档回复风格 → system 措辞（执行时定稿，设计稿残留点3授权）
_STYLE_DIRECTIVES_ZH = {
    "rigorous": "回复风格：专业严谨——术语准确、条理清晰、直指关键，不寒暄。",
    "friendly": "回复风格：亲和友善——多肯定学习者的尝试，语气温暖鼓励，纠错先肯定再指正。",
    "pragmatic": "回复风格：高效务实——直给能用的说法，最短路径帮学习者说对，少讲理论。",
    "creative": "回复风格：天马行空——用生动类比和有趣例子讲中文，允许幽默。",
    "socratic": "回复风格：启发引导——引而不答，多用提问带学习者自己发现规律，非必要不直接给答案。",
}
_STYLE_DIRECTIVES_EN = {
    "rigorous": "Reply style: rigorous — precise terminology, structured points, "
                "no small talk.",
    "friendly": "Reply style: friendly — warm and encouraging; acknowledge attempts "
                "before correcting.",
    "pragmatic": "Reply style: pragmatic — give usable Chinese directly, shortest "
                 "path, minimal theory.",
    "creative": "Reply style: creative — vivid analogies and playful examples; "
                "humor welcome.",
    "socratic": "Reply style: socratic — guide with questions instead of answers; "
                "let the learner notice the rule themselves.",
}

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _clean_text(value: Any, limit: int) -> str:
    """自由文本清洗：str 化、去控制字符、去首尾空白、截长。"""
    s = _CONTROL_CHARS.sub("", str(value or "")).strip()
    return s[:limit]


def normalize_persona(raw: Dict[str, Any],
                      current: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """校验并归一 persona（部分合并：只更新给出的字段，其余沿用 current）。
    - reply_style：六档枚举，非法 → ValueError（前端下拉控制，fail-loud）
    - interrupt_cap：int 纠正（'3'→3），钳制 0-10
    - 文本字段：清洗截长；未知键丢弃（白名单）
    非法输入（非对象）→ ValueError。"""
    if not isinstance(raw, dict):
        raise ValueError("persona 应为对象")
    merged = {k: v for k, v in (current or {}).items() if k in DEFAULT_PERSONA}
    merged.update({k: v for k, v in raw.items() if k in DEFAULT_PERSONA})

    style = str(merged.get("reply_style") or "default").strip().lower()
    if style not in REPLY_STYLES:
        raise ValueError(f"reply_style 仅支持: {', '.join(REPLY_STYLES)}")

    cap = merged.get("interrupt_cap")
    try:
        cap = int(cap)
    except (TypeError, ValueError):
        cap = DEFAULT_PERSONA["interrupt_cap"]
    cap = max(0, min(10, cap))

    return {
        "reply_style": style,
        "address": _clean_text(merged.get("address"), _TEXT_LIMITS["address"]),
        "identity": _clean_text(merged.get("identity"), _TEXT_LIMITS["identity"]),
        "custom_instructions": _clean_text(
            merged.get("custom_instructions"), _TEXT_LIMITS["custom_instructions"]),
        "interrupt_cap": cap,
    }


def _l1_is_zh(native_lang: str) -> bool:
    """判断 UI 语言是否为中文（控制 persona 措辞语言）。
    注：此函数原本含义为"学习者母语"，v0.3 P0.1 起语义重正为"UI 语言"；
    新增 _learner_l1_is_zh 处理真正的 L1 判定（C4 多母语迁移用）。
    v0.3 S2 拆分：字段语义剥离——ui_lang 与 learner_l1 互不混淆。
    """
    v = str(native_lang or "").strip().lower()
    return v in ("", "zh", "中文", "汉语", "chinese", "汉语官话")


def _learner_l1_is_zh(learner_l1: str) -> bool:
    """判断学习者母语是否为中文。
    v0.3 P0.1 新增：与 _l1_is_zh 完全独立——只看学习者 L1 字段（learner_l1）。
    用于 C4 多母语迁移、知识图谱 L1 归因等需要真实 L1 而非 UI 语言的场景。
    "" / "unknown" → False（无法判定时**不**默认中文，避免污染统计）。"""
    v = str(learner_l1 or "").strip().lower()
    if not v or v == "unknown":
        return False
    return v in ("zh", "中文", "汉语", "chinese", "汉语官话", "zh-cn", "zh-hans", "zh-hant")


def build_persona_brief(persona: Optional[Dict[str, Any]],
                        native_lang: str = "",
                        *,
                        learner_l1: str = "") -> str:
    """persona → [Persona] 注入段。全默认（default 风格 + 无自由文本）→ 空串，
    回退 0.21 语言指令默认（设计稿：default 不加额外约束）。
    interrupt_cap 由 serve 介入判定确定性执行（不依赖 LLM），此处仅作告知。

    v0.3 P0.1 签名扩展：
    - native_lang：UI/讲解语言（兼容旧调用），决定措辞中/英
    - learner_l1：学习者母语（新字段），目前 persona 措辞不依赖此字段；
      留作未来 C4 类 L1 个性化用。同一函数同时支持两种调用形态。"""
    if not isinstance(persona, dict) or not persona:
        return ""
    try:
        p = normalize_persona(persona)
    except ValueError:
        return ""
    if (p["reply_style"] == "default" and not p["address"]
            and not p["identity"] and not p["custom_instructions"]
            and p["interrupt_cap"] == DEFAULT_PERSONA["interrupt_cap"]):
        return ""

    is_zh = _l1_is_zh(native_lang)
    lines = ["[Persona]"]
    if p["reply_style"] != "default":
        style = (_STYLE_DIRECTIVES_ZH if is_zh else _STYLE_DIRECTIVES_EN)[p["reply_style"]]
        lines.append(f"- {style}")
    if p["address"]:
        lines.append(f"- 称呼我：{p['address']}" if is_zh
                     else f"- Address me as: {p['address']}")
    if p["identity"]:
        note = ("（优先于 [Scene] 中的角色设定）" if is_zh
                else " (takes precedence over the [Scene] role)")
        lines.append(f"- 你的身份：{p['identity']}{note}" if is_zh
                     else f"- Your identity: {p['identity']}{note}")
    if p["custom_instructions"]:
        lines.append(f"- 自定义指令：{p['custom_instructions']}" if is_zh
                     else f"- Custom instructions: {p['custom_instructions']}")
    lines.append(f"- 打断频率上限：{p['interrupt_cap']} 次/场" if is_zh
                 else f"- Interruption cap: {p['interrupt_cap']} per conversation")
    return "\n".join(lines)


# ---------------- P0.16 · 去 AI 味措辞规范（精简注入版） ----------------
# 规范管措辞、persona 管人设：本段独立于 reply_style/identity，对默认 persona 同样生效
# （决策 D1=A：无条件全局注入，不因 learner 未配 persona 而缺失）。
# 运行注入的精简要点抄录自 skills/tutor-style/SKILL.md（全量规范/正反例以该文件为准）。
# 教学准确性一票否决：去味手法不得牺牲内容正确，准确与自然冲突时保住准确，用短话消歧。
_STYLE_DIRECTIVE_ZH = (
    "[Style] 措辞（P0.16 规范）：短句为主、长短交错，单句尽量 ≤30 字；称呼自然，并回引"
    "学习者刚说过的具体词（让他感到被听见）；鼓励要具体到点——指出做对在哪、为什么对，"
    "不空洞夸；纠错先肯定对的、再只点最关键的一处，聚焦模式不逐条批斗；能引导学习者自己"
    "说出来就不直接给完整答案，一次只问一个问题；讲解按段、每段 30-45 字，段间用自然过渡。"
    "禁止：排比式连问、『首先/其次/最后』式套话、空洞『你真棒』、术语堆砌、一口气喂完整"
    "答案。教学准确性优先于篇幅与华丽。"
)
_STYLE_DIRECTIVE_EN = (
    "[Style] Wording (P0.16 spec): use short, mixed-length sentences (≤30 chars most "
    "of the time); address the learner naturally and echo a specific word they just said "
    "so they feel heard; praise specifically — name what was right and why, never hollow "
    "'good job'; when correcting, affirm what works first, then fix only the single most "
    "important spot, focusing on the pattern rather than a list of faults; if you can "
    "guide the learner to say it themselves, do not hand over the full answer, and ask "
    "ONE question at a time; break explanations into 30-45 char chunks with natural "
    "transitions. Avoid: rhetorical question stacking, 'first/second/finally' clichés, "
    "hollow praise, jargon piles, and dumping complete answers. Accuracy outranks polish."
)


def build_tutor_style_directive(native_lang: str = "") -> str:
    """P0.16：tutor 措辞规范精简注入段（[Style]，中英双语）。
    无条件返回非空——与 persona 是否配置无关（D1=A），由 serve 常驻注入。
    native_lang 语义与 build_persona_brief 一致：决定措辞语言（zh 其余=中/英文）。"""
    return _STYLE_DIRECTIVE_ZH if _l1_is_zh(native_lang) else _STYLE_DIRECTIVE_EN


__all__ = ["REPLY_STYLES", "DEFAULT_PERSONA", "normalize_persona",
           "build_persona_brief", "build_tutor_style_directive"]
