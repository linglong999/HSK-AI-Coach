# -*- coding: utf-8 -*-
# engine/scaffold.py —— 脚手架渐褪（两层定档 + 档位模板）· 纯函数零 LLM
#
# **表述纪律（B5-A）**：四域四档 = ZPD 原理落地（contingency + fading +
# transfer of responsibility，van de Pol 2010）的**产品自有实现**，
# 非学界既定分级。模块内显式标注，勿引文献背书。
#
# 架构（总纲 §1 决策2 + B5 I1）：确定性守门——档位由调用层确定性评估决定
# （学习者表现→熟练度分→选档），**不由 LLM 自判**。LLM 只在档位内做自然讲解。
#
# 四域（总纲 L75）= 英文 · 拼音 · 中文 · 词性。永恒锚：**中文解包恒锚、
# 英文最先撤**（英文仅临时脚手架，不承担精确释义）。

from typing import Dict, List, Optional, Tuple

# ---- 第一层 · 等级定初始档（总纲 L78 直译）----
# full=英+拼+中+词性全开；mid=英文减、拼音保留；minimal=英文关、拼音按需
#（轻声/多音/形近）
LEVEL_TIERS: Dict[Tuple[int, int], str] = {
    (1, 2): "full",
    (3, 4): "mid",
    (5, 9): "minimal",
}
# 超出九级（HSK 现实 ≤6，but level_gf 理论 1-9）→ 兜底 minimal（最省脚手架）
_TIER_FALLBACK = "minimal"

# ---- 第二层 · 单点内表现渐褪（contingent fading，总纲 L79 直译）----
# 全脚手架 → 撤词性解释 → 只留关键词 → 最小提示；出错/犹豫 → 临时补回一档
PERF_LADDER = ("full_scaffold", "no_pos", "keyword_hint", "minimal_hint")

# pick_stage 确定性映射表（表驱动可测）：(mastery 区间, 近期表现) → 档位
# 熟练度分 = 长期 mastery（图谱 Node.mastery）与当次 verify 的确定性合成：
#   - mastery 高 且 近期 pass       → 深档（撤更多脚手架）
#   - mastery 低 或 近期 fail/partial → 回撤一档（临时补回）
# 档位语义（PERF_LADDER 下标）：0=全脚手架、1=撤词性、2=只留关键词、3=最小提示
# 数值：stage 档值越大 = 脚手架越少（minimal_hint 3 最省）。
_MASTERY_HIGH = 0.7      # mastery ≥0.7 视为"熟练"
_MASTERY_LOW = 0.3       # mastery <0.3 视为"生疏"


def _parse_verify(recent_verify: Optional[list]) -> str:
    """把近期 verify 结果序列合成一个表现标记。
    recent_verify: list[str]，元素 ∈ {pass, partial, fail}（或含 verdict 的 dict，
    取 .get('verdict')）。空/无 → 'neutral'。任一 fail → fail；有 partial 无 fail
    → partial；全 pass → pass。"""
    if not recent_verify:
        return "neutral"
    verdicts: List[str] = []
    for v in recent_verify:
        if isinstance(v, dict):
            v = v.get("verdict") or ""
        verdicts.append(str(v))
    if any(v == "fail" for v in verdicts):
        return "fail"
    if any(v == "partial" for v in verdicts):
        return "partial"
    if all(v == "pass" for v in verdicts) and verdicts:
        return "pass"
    return "neutral"


def pick_tier(level: int) -> str:
    """第一层：等级 → 初始档（full/mid/minimal）。纯函数。
    level: 学习者等级（整数 1-9）。非正/越界 → minimal 兜底（不抛）。"""
    try:
        ilv = int(level)
    except (TypeError, ValueError):
        return _TIER_FALLBACK
    for (lo, hi), tier in LEVEL_TIERS.items():
        if lo <= ilv <= hi:
            return tier
    return _TIER_FALLBACK


def pick_stage(level: int, kp_mastery: float, recent_verify: Optional[list] = None) -> str:
    """第二层：表现 → 熟练度分 → 档位（确定性，总纲 §1 决策2）。
    返回 PERF_LADDER 中的档位值（string）。
    合成规则（表驱动）：
      - tier=full（HSK1-2）：起步 low 档——首建全脚手架；熟练才缓慢撤
      - tier=mid  （HSK3-4）：起步中档；熟练→撤词性、生疏→全脚手架保留
      - tier=minimal（HSK5+）：起步 deep；熟练最小提示、生疏回撤一档
    统一判据：recent fail/partial → 回撤一档（临时补回）；mastery 高且近期全 pass
    → 撤一档。
    """
    tier = pick_tier(level)
    perf = _parse_verify(recent_verify)
    mastery = float(kp_mastery or 0.0)

    # 基准档（随 tier 不同起步位置）—— 档值越大脚手架越少
    baseline = {
        "full": 0,        # 全脚手架
        "mid": 1,         # 撤词性
        "minimal": 2,     # 只留关键词
    }.get(tier, 1)

    # 生疏/近期错 → 回撤一档（多给脚手架）；熟练且全 pass → 撤一档
    if mastery < _MASTERY_LOW or perf in ("fail", "partial"):
        stage = max(0, baseline - 1)
    elif mastery >= _MASTERY_HIGH and perf == "pass":
        stage = min(len(PERF_LADDER) - 1, baseline + 1)
    else:
        stage = baseline

    return PERF_LADDER[stage]


# 档位 → prompt 注入段（中英文两套模板）· 英文域在所有档最先撤、中文恒锚
_DIRECTIVE_SCAFFOLD = {
    "full_scaffold": (
        "全脚手架：给英文翻译、拼音、中文释义与词性说明全部保留，帮学习者跨过第一道坎。"
    ),
    "no_pos": (
        "已撤词性说明：保留英文+拼音+中文释义，但不再单独讲词性（避免信息过载）。"
    ),
    "keyword_hint": (
        "仅关键词提示：收起英文翻译（可给单字对应），保留拼音与中文，重点讲最容易混的地方。"
    ),
    "minimal_hint": (
        "最小提示：基本撤英文与常规拼音（仅在轻声/多音/形近字出现时给拼音），"
        "用最简中文把解包讲清，引导学习者主动说出来。"
    ),
}
_DIRECTIVE_SCAFFOLD_EN = {
    "full_scaffold": (
        "Full scaffold: keep English translation, pinyin, Chinese gloss and part-of-speech "
        "explanation — help the learner across the first hurdle."
    ),
    "no_pos": (
        "Scaffold reduced: keep English + pinyin + Chinese gloss, but drop the part-of-speech "
        "note to avoid overload."
    ),
    "keyword_hint": (
        "Keyword hint: put away the full English translation (single-character gloss only), "
        "keep pinyin and Chinese, focus on what is easiest to confuse."
    ),
    "minimal_hint": (
        "Minimal hint: drop most English and routine pinyin (only give pinyin for neutral-tone, "
        "multi-reading, or visually-similar characters), unpack in plainest Chinese and guide "
        "the learner to say it themselves."
    ),
}
# 中文恒锚说明（拼在 scaffold_directive 尾部，两语言通用语义）
_ANCHOR_NOTE = "中文解包恒锚：核心释义始终用学习者等级内已掌握词展开，英文仅为临时脚手架。"


def scaffold_directive(tier: str, stage: str, bilingual: bool = False) -> str:
    """档位 → prompt 注入段（模板字符串）。
    bilingual: True → 英文模板（非中母语讲解壳）；否则中文模板。
    英文域在所有档最先撤、中文解包恒锚（模块语义一致性。"""
    tbl = _DIRECTIVE_SCAFFOLD_EN if bilingual else _DIRECTIVE_SCAFFOLD
    directive = tbl.get(stage, tbl["full_scaffold"])
    return f"{directive} {_ANCHOR_NOTE}"