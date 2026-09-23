# -*- coding: utf-8 -*-
# engine/feedback_selector.py —— B2 反馈策略选择器（纯确定性，零 LLM）
# 对齐实施计划-v3 B2：Lyster & Ranta 六分类降为编码框架（非决策规则）、规则 A
# 降为软偏好表（类型只给低权重倾向，主决权=水平+情境+诊断器强信号）。
# 分层纪律：intervention.py 是时机层（分档 none/light/block/encourage），
# 本模块是策略层，只在其产出上追加 [FeedbackStrategy] 段、不碰时机层。
# 输入容错：errors 里的 B0 verdict / B1 construction_diagnostics 缺省 None 不炸。

# Lyster & Ranta 六分类枚举（编码框架，仅贴标签供 B6 D-4 评测，不进决策路径）
LR_TAGS = ("explicit_correction", "recast", "clarification_request",
           "metalinguistic", "elicitation", "repetition")

# 规则 A 软偏好表：error.type → (倾向方向, 首选 L&R 标签)
# 方向: "input"(给答案/示范) | "prompt"(逼产出/引导 self-repair)；
# 仅低权重倾向，水平调节与诊断器强信号可覆盖。
TYPE_PREFERENCE = {
    "语用":     ("input",  "explicit_correction"),
    "语法":     ("prompt", "metalinguistic"),
    "语音":     ("input",  "recast"),
    "词汇拼写": ("input",  "explicit_correction"),
    "词汇语义": ("prompt", "clarification_request"),
    "汉字":     ("input",  "explicit_correction"),
}
# 类型无专属偏好时的兜底（防 B0 后新类型无映射）
_DEFAULT_PREF = ("input", "explicit_correction")

_MAX_LIGHT = 2   # 轻标至多 2 条（B2 拍板：单句轻点 1 个最关键、其余静默进复习队列）


def _preference(error, hsk_level: int):
    """F3 软偏好合成：类型表 × 水平调节(规则B) × 诊断器强信号(规则③)。
    返回 (direction, lr_tag, reason)。主决权 = 水平+情境+诊断器信号 > 类型软偏好。"""
    etype = (error or {}).get("type") or ""
    direction, lr_tag = TYPE_PREFERENCE.get(etype, _DEFAULT_PREF)
    reason = ""

    # 规则 B 水平调力度（Krashen+Swain）：低水平→input 多示范；高水平→prompt push self-repair
    if isinstance(hsk_level, int) and hsk_level >= 1:
        if hsk_level <= 2 and direction == "prompt":
            direction, lr_tag = "input", LR_TAGS[0]  # explicit_correction
            reason = "低水平·多示范（调节为 input）"
        elif hsk_level >= 4 and direction == "input":
            direction, lr_tag = "prompt", "elicitation"
            reason = "高水平·push self-repair（调节为 prompt）"

    # 规则③ 诊断器强信号：construction_diagnostics.verdict=="error" → bias 到显式/元语言
    # （学界：语法类 recast repair 最低，需显式/prompt；B1 构造诊断皆语法类）
    diag = (error or {}).get("construction_diagnostics")
    if isinstance(diag, dict) and diag.get("verdict") == "error":
        lr_tag = "metalinguistic"
        direction = "prompt" if etype == "语法" else direction
        reason = ("诊断器强信号·bias 到元语言显式讲解" if etype == "语法"
                  else "诊断器强信号·保险取元语言提示")

    return direction, lr_tag, reason


def _diag_score(error):
    """F2 ①诊断器强信号：构造诊断 verdict=error 记最高优先。"""
    diag = (error or {}).get("construction_diagnostics")
    return 1 if (isinstance(diag, dict) and diag.get("verdict") == "error") else 0


def _confidence(error) -> float:
    try:
        return float((error or {}).get("confidence") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _severity(error) -> int:
    """F2 ③severity 档：B0 verdict=error > edge > 其余(None)。"""
    ver = (error or {}).get("verdict")
    return {"error": 2, "edge": 1}.get(ver, 0)


def _rank_errors(errors, recurrence_map=None):
    """F2 深攻选点排序（四级优先序）：诊断器强信号 > 错误复发 > severity/confidence > 稳定序(fragment)。
    纯函数返回按优先级降序的 error 实体列表。"""
    recurrence_map = recurrence_map or {}
    def key(e):
        kp = (e or {}).get("knowledge_point_id") or ""
        return (-_diag_score(e),
                -int(recurrence_map.get(kp, 0) or 0),
                -_severity(e),
                -_confidence(e),
                (e or {}).get("fragment") or "")
    return sorted(list(errors), key=key) if errors else []


def select_feedback_strategies(errors, hsk_level: int, intervention_level: str,
                               recurrence_map=None, deep_dived_kps=None):
    """B2 总入口（纯确定性）：软偏好不硬锁，返回三层去向 + 偏好 + 标签。
    errors：预扫 errors[]（type/confidence/knowledge_point_id/fragment/
            verdict/construction_diagnostics——B0/B1 字段缺省 None 容错）。
    intervention_level：none/light/block/encourage（时机层已定，策略层不越权——
            encourage 档深攻/轻标全空纯鼓励；none 档只轻点不深攻。）
    recurrence_map：{kp_id: 撞错次数}（由调用方从账本注入，驱动优先级 ②）。
    deep_dived_kps：跨回合已深攻 kp 集合（会话级去重，同一错误点最多深攻 1 次）。"""
    if intervention_level == "encourage" or not errors:
        return {"deep_dive": None, "light_marks": [], "silent_kps": [],
                "preferences": [], "deep_dive_count": 0,
                "note": "encourage 或无可选错误：不产策略" if intervention_level == "encourage"
                        else "无错误可策略"}

    dived = set(deep_dived_kps or [])
    ranked = _rank_errors(errors, recurrence_map)

    # 深攻只在 block 档（显式精讲+引导复述）；且命中跨回合未深攻点，命中则顺延次位
    deep = None
    if intervention_level == "block":
        for e in ranked:
            if (e or {}).get("knowledge_point_id") not in dived:
                deep = e
                break
    # light 档也可轻点（none/light/block 均轻标，仅 encourage 不轻标）
    light_marks = [e for e in ranked if e is not deep][:_MAX_LIGHT]
    silent = [e for e in ranked if e is not deep and e not in light_marks]

    preferences = [_preference(e, hsk_level) for e in ([deep] + light_marks) if e]
    return {
        "deep_dive": deep,
        "light_marks": light_marks,
        "silent_kps": [(e or {}).get("knowledge_point_id") for e in silent],
        "preferences": preferences,
        "deep_dive_count": 1 if deep else 0,
    }


# ---------------- [FeedbackStrategy] 段编译（双语） ----------------

_LR_LABEL = {
    "zh": {"explicit_correction": "明确纠正", "recast": "重述示范", "clarification_request": "澄清请求",
           "metalinguistic": "元语言提示", "elicitation": "引导产出", "repetition": "重复追问"},
    "en": {"explicit_correction": "explicit correction", "recast": "recast", "clarification_request": "clarification request",
           "metalinguistic": "metalinguistic cue", "elicitation": "elicitation", "repetition": "repetition"},
}


def compile_strategy_section(strategies, native_lang: str = ""):
    """把选择器产出编译成追加到 [Intervention] 之后的 [FeedbackStrategy] 段。
    文案只作指令注入（过 tutor-style 自然度），非模板拼接；silent 不进段（学习者不可见）。"""
    deep = strategies.get("deep_dive")
    light = strategies.get("light_marks") or []
    if not deep and not light:
        return ""
    is_zh = not (native_lang and str(native_lang).lower() not in
                 ("zh", "中文", "汉语", "chinese", "汉语官话"))
    tag = _LR_LABEL["zh" if is_zh else "en"]
    lines = []
    if is_zh:
        lines.append("[FeedbackStrategy] 本轮反馈策略（在 [Intervention] 之上细分形态）：")
        if deep:
            frag = (deep or {}).get("fragment", "")
            lines.append(
                f"- 深攻 1 点（{frag or '最关键的 1 个错误点'}）：显式精讲该错误的正确说法"
                f"（{tag.get(strategies['preferences'][0][1] if strategies['preferences'] else 'metalinguistic', '元语言提示')}式），"
                f"然后用一两个自然问题引导学习者自己复述一遍。")
        for i, mark in enumerate(light[:_MAX_LIGHT]):
            pref = (strategies["preferences"] or [None] * (i + 1))[i + (1 if deep else 0)]
            tag_name = tag.get(pref[1] if pref else "", "") if pref else ""
            lines.append(
                f"- 轻标：{mark.get('fragment', '一个错误点')} 简短标记一下（{tag_name or '不展开'}），不罗列不打断。")
        lines.append("- 其余错误静默进复习队列（后台记录，不向学习者揭示）。")
    else:
        lines.append("[FeedbackStrategy] Feedback granularity (refines [Intervention]):")
        if deep:
            frag = (deep or {}).get("fragment", "")
            pref = (strategies["preferences"] or [None])[0]
            tag_name = tag.get(pref[1] if pref else "", "") if pref else ""
            lines.append(
                f"- Deep-dive 1 point ({frag or 'the single most important error'}): "
                f"explicitly explain the correct form ({tag_name or 'metalinguistic'}), "
                "then guide the learner to restate it with one or two natural questions.")
        for i, mark in enumerate(light[:_MAX_LIGHT]):
            pref = (strategies["preferences"] or [None] * (i + 1))[i + (1 if deep else 0)]
            tag_name = tag.get(pref[1] if pref else "", "") if pref else ""
            lines.append(
                f"- Light mark: {mark.get('fragment', 'a spot')} — flag briefly "
                f"({tag_name or 'no expansion'}), don't list or interrupt.")
        lines.append("- Remaining errors go silently into the review queue (recorded "
                     "in the background, not disclosed to the learner).")
    return "\n".join(lines)