# ============================================================
# planner/loop.py
# M4 Planner Loop · ReAct 主循环（自由对话助教核心）
# run(user_input, history, learner_id)：
#   system = 技能紧凑清单(list_all) + 全局规则
#   for step in range(MAX_STEPS):  llm输出JSON数组 → 解析 → 分发text/action
#   action → REGISTRY 调技能，结果喂回 tool_result；text → 累积
# 终止：出现 text 且本轮无待续 action；或到达 6 步上限 → fallback
# history 来源（M5 接入后语义）：由上层 serve 从 LearnerMemory 注入
#   （to_llm_history(conversation_id)，服务端记忆为唯一权威源），planner 只消费不落盘
# profile_summary 来源（M8 接入后语义）：由上层 serve 从 build_profile_summary
#   （graph 复习队列 + ledger 惯犯计数）计算注入 system，planner 不自取
# 注入点：llm_call 默认用 LLMClient.chat，可注入 mock（无 Key 可测确定性分支）
# ============================================================

import json
from typing import Any, Callable, Dict, List, Optional

from planner.parser import parse_json_array, ParseError
from planner.fallback import fallback_reply, skill_error_reply

MAX_STEPS = 6

TOOL_RESULT_PREFIX = "[tool_result]"


def is_tool_result_message(msg: Dict[str, Any]) -> bool:
    """判断一条消息是否为工具结果回喂（mock LLM / 测试判定用）。"""
    return (isinstance(msg, dict) and msg.get("role") == "user"
            and str(msg.get("content", "")).startswith(TOOL_RESULT_PREFIX))

# 全局规则（system 常驻）——覆盖注册表全部 9 技能（0.17 §2）
GLOBAL_RULES = (
    "你是 HSK 中文学习助教，专注偏误教学与费曼式理解。规则：\n"
    "1) 只输出一个 JSON 数组文本（不要任何额外解释文字，不要代码块围栏）。\n"
    "2) 数组每项是对象，仅两种合法形态，字段名必须一字不差：\n"
    '   调用技能: {"type": "action", "name": "技能名", "params": {参数}}\n'
    '   对用户说话: {"type": "text", "content": "自然中文"}\n'
    '   示例: [{"type": "action", "name": "identify_errors", "params": {"text": "我想买苹果很多。"}}, '
    '{"type": "text", "content": "我来看看这句有没有偏误。"}]\n'
    "3) 技能选用：\n"
    "   - 判断句子偏误 → identify_errors（params: text=原句）\n"
    "   - 解释已识别的偏误 → explain_error（params: error=识别返回的偏误对象）\n"
    "   - 学习者复述讲解内容求检验 → verify_retell（params: explanation/key_points 取自"
    "上文讲解结果，restatement=复述原文）\n"
    "   - 查单个知识点定义/等级 → lookup_knowledge_point（params: keyword）\n"
    "   - 检索权威语料片段/例句/词汇（含该生历史错法）→ retrieve_corpus（params: query, "
    "top_k 可选）；回答知识性问题优先检索并引用返回来源（如'根据知识点清单·把字句'），"
    "而非凭记忆编；检索未命中就如实说明，不要编造来源\n"
    "   - 查看复习安排 → get_review_queue\n"
    "   - 生成费曼讲解单元 → generate_unit（params: unit_type=explain, context={for_keypoint}）\n"
    "   - 生成巩固练习 → generate_unit（params: unit_type=practice, "
    "context={for_keypoints, targets_errors}）\n"
    "   - 联网补充资料 → web_search（params: query）；解析学习者提供的材料 → "
    "parse_document（params: text）\n"
    "4) 技能结果以 tool_result 喂回，你可据此继续调用或给最终 text。\n"
    "5) 讲解完成后，主动邀请学习者用自己的话复述一遍（费曼式检验，等其复述后调 verify_retell）。\n"
    "6) 不确定或无需任何技能时就只给 text 直接回答。\n"
    "7) 系统提示若附【学习者画像】（该生常错点/惯犯/复习提醒）：讲到相关知识点时主动点出"
    "其高频偏误（如'你最近常错X'）；对话开场或收尾时若复习队列有高优先级待复习项，"
    "可主动发起复习提醒"
    "（可调 get_review_queue 取详情）。无画像则忽略本条。"
)

# 0.21 英文版全局规则：native_lang 非 zh 时生效。
# 语言策略（调研主流中文学习产品 + 教学法共识"结构化双语、母语脚手架渐褪出"）：
#   教学层（对学习者说的话、讲解说理）用英文；内容载体（被检查的中文句、修正句、
#   例句、词汇）永远保持中文——英文是"帮助理解中文的外壳"，不翻译掉要学的中文。
GLOBAL_RULES_EN = (
    "You are an HSK Chinese tutor for English-native learners, focused on "
    "error-driven teaching and Feynman-style understanding. Rules:\n"
    "1) Output only one JSON array as plain text (no extra prose, no code fences).\n"
    "2) Each array item is an object with exactly two legal shapes, field names verbatim:\n"
    '   Call a skill: {"type": "action", "name": "skill_name", "params": {params}}\n'
    '   Speak to the user: {"type": "text", "content": "natural English"}\n'
    '   Example: [{"type": "action", "name": "identify_errors", "params": {"text": "我想买苹果很多。"}}, '
    '{"type": "text", "content": "Let me check this sentence for errors."}]\n'
    "3) Skill selection:\n"
    "   - Judge a sentence for errors → identify_errors (params: text=the sentence)\n"
    "   - Explain an identified error → explain_error (params: error=the error object "
    "returned by identification)\n"
    "   - Learner restates an explanation for checking → verify_retell (params: "
    "explanation/key_points taken from the preceding explanation result, "
    "restatement=the restatement text)\n"
    "   - Look up a knowledge point definition/level → lookup_knowledge_point "
    "(params: keyword)\n"
    "   - Retrieve authoritative corpus snippets / example sentences / vocabulary "
    "(incl. this learner's past error patterns) → retrieve_corpus (params: query, "
    "top_k optional); for knowledge questions prefer retrieval and cite the returned "
    "source (e.g. \"per the knowledge-point list · 把字句\") instead of answering from "
    "memory; if retrieval misses, say so honestly — never fabricate sources\n"
    "   - View review schedule → get_review_queue\n"
    "   - Generate a Feynman explanation unit → generate_unit (params: "
    "unit_type=explain, context={for_keypoint})\n"
    "   - Generate consolidation practice → generate_unit (params: unit_type=practice, "
    "context={for_keypoints, targets_errors})\n"
    "   - Supplement with web material → web_search (params: query); parse "
    "learner-provided material → parse_document (params: text)\n"
    "4) Skill results are fed back as tool_result; you may keep calling skills or give "
    "the final text.\n"
    "5) After an explanation, invite the learner to restate it in their own words "
    "(Feynman check; call verify_retell once they do).\n"
    "6) When unsure or no skill is needed, just give text directly.\n"
    "7) If the system prompt includes a [Learner profile] (common errors / repeat "
    "offenders / review reminders): when touching a related knowledge point, point out "
    "their high-frequency errors (e.g. \"you often get X wrong lately\"); at the opening "
    "or closing of the conversation, if the review queue has high-priority items, you "
    "may raise a review reminder (you may call get_review_queue for details). Ignore "
    "this rule if no "
    "profile is attached.\n"
    "8) LANGUAGE POLICY (critical): speak to the learner in natural English — the "
    "teaching layer. All target-language content — the sentence being checked, error "
    "fragments, corrections, corrected sentences, examples, and vocabulary — stays in "
    "Chinese. English is the explanation shell; Chinese is the content being learned. "
    "Never replace the Chinese with English-only text."
)

# 0.21：native_lang 由系统确定性注入这些技能参数（语言是系统约束，不依赖 LLM 自觉传参）
# generate_unit 走 context.self_language/language_directive（引擎既有字段），其余走 native_lang
# 0.22：identify_errors 加入——识别器迁移假设（hypotheses[]）依赖 native_lang，
# 漏注入会让 L1 归因在对话主链静默失效（与 Router.process 漏传同类问题）
LANG_INJECTED_SKILLS = ("identify_errors", "explain_error", "verify_retell",
                        "generate_unit")

# 0.25 起点分层：HSK 等级由系统确定性注入（等价 native_lang 的"系统约束不靠 LLM"原则）。
# identify 用 level（超纲宽容判定 + 【学习者】难度），explain 用 user_level（讲解难度）。
# 两者参数名各异，由 _inject_level 按技能名写入；无级则保持技能默认（HSK3）。
LEVEL_INJECTED_SKILLS = ("identify_errors", "explain_error")

# 0.21 EN 模式生成单元的语言指令（英壳 + 中文内容载体，与规则 8 同口径）
_LANG_DIRECTIVE_EN = (
    "Teaching layer (explanations, instructions, feedback) in natural English; "
    "all target-language content (examples, sentences, vocabulary) stays in Chinese."
)


class Planner:
    def __init__(self, registry, llm_call: Optional[Callable[[List[Dict]], str]] = None,
                 max_steps: int = MAX_STEPS):
        """registry: skills 注册表；llm_call: None→默认 LLMClient.chat"""
        self.registry = registry
        self.max_steps = max_steps
        if llm_call is None:
            self._llm_call = self._default_llm_call
        else:
            self._llm_call = llm_call

    def _default_llm_call(self, messages: List[Dict]) -> str:
        from engine.llm.client import LLMClient
        client = LLMClient()
        # 拼接 messages 为系统+用户两层（LLMClient.chat(messages) 已支持列表）
        return client.chat(messages, temperature=0.3)

    def run(self, user_input: str, history: Optional[List[Dict]] = None,
            learner_id: str = "default",
            profile_summary: Optional[str] = None,
            native_lang: str = "",
            user_level: str = "",
            scene_brief: str = "",
            persona_brief: str = "",
            intervention_directive: str = "") -> Dict[str, Any]:
        """主循环。返回含 text / used_skills / steps / fallback 的结果。
        profile_summary（M8）：常错点/惯犯摘要，非空时拼进 system 供个性化教学；
        由上层 serve 从 build_profile_summary(graph, ledger) 计算，planner 不自取。
        native_lang（0.21）：非 zh → 英文全局规则/降级文案，并确定性注入
        explain_error / verify_retell 参数（教学层英文，中文内容载体不变）。
        user_level（0.25 起点分层）：学习者 HSK 等级（如 'HSK3'/'3'），由上层 serve
        从画像读取注入 identify_errors / explain_error（识别难度 + 讲解难度），空则技能默认。
        scene_brief（0.22 方向2）：场景对话 [Scene] 段（serve 组好传入），非空时
        追加进 system——让学习者开口习得、不逐句纠错（纠错介入交给方向3）。
        persona_brief（0.22 方向3）：个性化栏 [Persona] 段（风格/称呼/人设/自定义指令）。
        intervention_directive（0.22 方向3）：本轮介入判定 [Intervention] 段
        （serve 确定性分档后传入：none 静默/light 引导/block 讲解）。"""
        history = history or []
        system = self._build_system(profile_summary or "", native_lang=native_lang,
                                    scene_brief=scene_brief,
                                    persona_brief=persona_brief,
                                    intervention_directive=intervention_directive)
        messages: List[Dict] = [{"role": "system", "content": system}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_input})

        text_acc: List[str] = []
        used: List[str] = []
        # trace：技能调用轨迹（name/params/ok/result），供前端渲染"学习成果卡"（A1：转译而非日志）
        trace: List[Dict[str, Any]] = []
        protocol_degraded = False   # 协议降级标记（裸文本兜底时置 True，不静默）
        parse_retries = 0

        def _fail(reason: str, partial: str = "") -> Dict[str, Any]:
            fb = fallback_reply(reason, partial_text=partial, native_lang=native_lang)
            fb["used_skills"] = used
            fb["trace"] = trace
            fb["steps"] = step + 1
            if protocol_degraded:
                fb["protocol_degraded"] = True
            return fb

        for step in range(self.max_steps):
            raw = self._llm_call(messages)
            try:
                items = parse_json_array(raw)
            except ParseError as e:
                # 自纠一次（与 ④a 参数校验失败喂回同构）：LLM 偶发跳过 JSON
                # 协议直答（实测多轮长讲解场景），喂回格式错误给一次重试机会
                if parse_retries < 1:
                    parse_retries += 1
                    if native_lang and native_lang.lower() != "zh":
                        retry_msg = (f"[format_error] Your previous output could not be "
                                     f"parsed as a JSON array ({e}). Re-output strictly in "
                                     f"the rule-2 format, with no text outside the JSON.")
                    else:
                        retry_msg = (f"[format_error] 你的上一条输出无法解析为 JSON 数组"
                                     f"（{e}）。请严格按规则 2 的格式重新输出，不要输出"
                                     f"任何 JSON 以外的文字。")
                    messages.append({"role": "user", "content": retry_msg})
                    continue
                # 二次仍失败：若为自然语言文本（非 JSON 残片），当 text 兜底
                # （显式标记 protocol_degraded，不静默透传；JSON 残片则 fallback）
                stripped = (raw or "").strip()
                if stripped and stripped[0] not in "[{" and len(stripped) > 10:
                    text_acc.append(stripped)
                    protocol_degraded = True
                    break
                return _fail("parse", str(e))

            # 本轮是否还有待续 action（决定是否终止）
            has_pending_action = False
            for it in items:
                t = it.get("type")
                if t == "text":
                    text_acc.append(str(it.get("content", "")))
                elif t == "action":
                    name = it.get("name", "")
                    params = it.get("params", {}) or {}
                    # 0.21 语言注入：教学层语言是系统约束，确定性写入参数而非靠
                    # LLM 自觉传参（在 trace 记录前注入，保证轨迹反映实际下发参数）
                    if (native_lang and name in LANG_INJECTED_SKILLS
                            and isinstance(params, dict)):
                        params = self._inject_lang(name, params, native_lang)
                    # 0.25 起分层：HSK 等级确定性注入识别/讲解难度（同"系统约束不靠 LLM"）
                    if (user_level and name in LEVEL_INJECTED_SKILLS
                            and isinstance(params, dict)):
                        params = self._inject_level(name, params, user_level)
                    res = self._dispatch_action(name, params)
                    used.append(name)
                    trace.append({
                        "name": name,
                        "params": params,
                        "ok": bool(res.get("ok", False)),
                        "result": res,
                    })
                    messages.append(self._format_tool_result(name, res))
                    has_pending_action = True

            # 终止判据：产生了 text 且本轮没有动作挂起；或无任何输出但也没动作
            if not has_pending_action:
                if text_acc:
                    break
                # 无 text 也无 action（空数组）→ 不健康，走 fallback
                return _fail("parse", raw)

        # 达步数上限仍未以 text 收尾
        if not text_acc:
            return _fail("max_steps")

        out = {
            "text": "\n".join(text_acc),
            "used_skills": used,
            "steps": step + 1,
            "fallback": False,
            "trace": trace,
        }
        if protocol_degraded:
            out["protocol_degraded"] = True
        return out

    # ---------------- 内部 ----------------
    @staticmethod
    def _inject_lang(name: str, params: Dict[str, Any],
                     native_lang: str) -> Dict[str, Any]:
        """0.21 语言注入：教学层语言由系统确定性写入技能参数，不依赖 LLM 自觉。
        identify_errors/explain_error/verify_retell → 顶层 native_lang；generate_unit → EN 时注入
        context.self_language + language_directive（引擎既有语言字段，zh 是引擎默认不动）。"""
        if name == "generate_unit":
            if native_lang.lower() == "zh":
                return params   # 引擎默认即中文，无需注入
            ctx = params.get("context")
            if isinstance(ctx, dict):
                new_ctx = dict(ctx)
                if not new_ctx.get("self_language"):
                    new_ctx["self_language"] = native_lang
                if not new_ctx.get("language_directive"):
                    new_ctx["language_directive"] = _LANG_DIRECTIVE_EN
                return {**params, "context": new_ctx}
            # LLM 平铺 context 字段的变体（skill.run 兼容平铺形态）：顶层补
            if not params.get("self_language"):
                return {**params, "self_language": native_lang,
                        "language_directive": _LANG_DIRECTIVE_EN}
            return params
        if not params.get("native_lang"):
            return {**params, "native_lang": native_lang}
        return params

    @staticmethod
    def _inject_level(name: str, params: Dict[str, Any], user_level: str) -> Dict[str, Any]:
        """0.25 起点分层：HSK 等级确定性写入技能参数。
        identify_errors 用 level（超纲宽容判定 + 识别难度），explain_error 用
        user_level（讲解难度）。仅在该技能未自传参数时注入，不覆盖 LLM 已有值。"""
        key = "level" if name == "identify_errors" else "user_level"
        if params.get(key):
            return params
        return {**params, key: user_level}

    def _build_system(self, profile_summary: str = "",
                      native_lang: str = "",
                      scene_brief: str = "",
                      persona_brief: str = "",
                      intervention_directive: str = "") -> str:
        is_en = bool(native_lang and native_lang.lower() != "zh")
        rules = GLOBAL_RULES_EN if is_en else GLOBAL_RULES
        compact = "\n".join(
            f"- {s['name']}: {s['summary']}（触发: {s['triggers_hint']}）"
            for s in self.registry.list_all()
        )
        system = f"{rules}\n\n【可用技能清单】\n{compact}"
        if profile_summary:
            header = ("[Learner profile] (personalize the teaching accordingly)"
                      if is_en else "【学习者画像】（结合画像个性化教学）")
            system += f"\n\n{header}\n{profile_summary}"
        if persona_brief:
            # 0.22 方向3：个性化段（风格/称呼/人设/自定义指令），与画像/场景并列
            system += f"\n\n{persona_brief}"
        if scene_brief:
            # 0.22 方向2：场景段与规则/画像并列（独立 [Scene] 标记，不与 persona 冲突）
            system += f"\n\n{scene_brief}"
        if intervention_directive:
            # 0.22 方向3：本轮介入判定段——最具体（逐轮），置于末尾
            system += f"\n\n{intervention_directive}"
        return system

    def _dispatch_action(self, name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        skill = self.registry.get(name)
        if skill is None:
            return {"error": f"未知技能: {name}", "ok": False}
        # 0.18 接入项④a：按 input_schema 校验（含安全纠正）。
        # 校验失败 → 技能不执行，结构化错误喂回 LLM 下一轮自纠（多数模型二轮
        # 可修正），不打 fallback——对齐 Anthropic is_error tool_result 模式
        corrected, errors = skill.validate_params(params)
        if errors:
            return skill_error_reply(name, "参数校验失败: " + "; ".join(errors))
        try:
            result = skill.run(corrected)
            result["ok"] = True
            return result
        except Exception as e:  # noqa: BLE001
            return skill_error_reply(name, str(e))

    def _format_tool_result(self, name: str, res: Dict[str, Any]) -> Dict[str, Any]:
        # 用 role=user 回喂（不用 role=tool）：DeepSeek/OpenAI 协议要求 tool
        # 消息必须紧跟带 tool_calls 的 assistant 消息，本项目纯文本 ReAct 协议
        # 无 tool_calls 结构，实测 role=tool 直接 HTTP 400（M6 真实实跑发现）
        return {
            "role": "user",
            "content": TOOL_RESULT_PREFIX + " " + json.dumps(
                {"name": name, "result": res}, ensure_ascii=False),
        }