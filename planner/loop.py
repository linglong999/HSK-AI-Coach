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
    "其高频偏误（如'你最近常错X'）；对话开场或收尾时若有到期复习项，主动发起复习提醒"
    "（可调 get_review_queue 取详情）。无画像则忽略本条。"
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
            profile_summary: Optional[str] = None) -> Dict[str, Any]:
        """主循环。返回含 text / used_skills / steps / fallback 的结果。
        profile_summary（M8）：常错点/惯犯摘要，非空时拼进 system 供个性化教学；
        由上层 serve 从 build_profile_summary(graph, ledger) 计算，planner 不自取。"""
        history = history or []
        system = self._build_system(profile_summary or "")
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
            fb = fallback_reply(reason, partial_text=partial)
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
                    messages.append({
                        "role": "user",
                        "content": f"[format_error] 你的上一条输出无法解析为 JSON 数组"
                                   f"（{e}）。请严格按规则 2 的格式重新输出，不要输出"
                                   f"任何 JSON 以外的文字。",
                    })
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
    def _build_system(self, profile_summary: str = "") -> str:
        compact = "\n".join(
            f"- {s['name']}: {s['summary']}（触发: {s['triggers_hint']}）"
            for s in self.registry.list_all()
        )
        system = f"{GLOBAL_RULES}\n\n【可用技能清单】\n{compact}"
        if profile_summary:
            system += f"\n\n【学习者画像】（结合画像个性化教学）\n{profile_summary}"
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