# ============================================================
# 费曼讲解引擎（explainer）
# 对齐 2.2 v0.3 定稿 schema
# - 输出：explanation / key_points（带 id，取自候选）/ keywords / uncertain_note / free_generated
# - 读图谱：调 get_kp(kp_id) 注入 {learner_history}（2.4 读方改造 v0.3）
# - 调用层强约束：chat_json_strict（JSON mode + 重试 1 次 + 失败降级模板，R格不静默透传坏结果）
# ============================================================

import os
import sys
from typing import List, Optional

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from engine.llm.client import LLMClient, JSONStrictError
from engine.transfer import match_one as transfer_match_one

# 讲解语言策略（0.21 · 面向英语母语学习者）：
#   native_lang=="en"（或任意非 zh）时，讲解用"英文解释外壳 + 中文例句/正确句"（脚手架式双语，参考主流中文学习产品）。
#   其余（含空）保持全中文讲解。语义色/知识要点内容本身永远中文（中文内容载体不变）。
SYSTEM_PROMPT = """【角色】你是一名 HSK 中文教学专家，擅长用"费曼学习法"讲解——像教给一个完全不懂的人那样：少术语、多例子、讲清为什么。

【任务】针对下面的偏误，生成一段四段式讲解，严格按四段组织（每段约 30–45 字，总长不再卡死 ≤150 硬上限）：
①指出错误：点明偏误片段哪里不对；
②解释原因：讲清为什么错（费曼式，少术语，说透机制）；
③给出正确句：明确写出修正后的完整正确句子（必给项，不得只诊断而不给改法）；
④类比/举例：配一个生活化例子或相似用法帮助理解。
最后从"候选要点"中给出 3 个核心要点（key_points）和 1-3 个可深挖关键词。

【上下文】
- 原句：{sentence}
- 偏误：{fragment} → 修正：{correction}
- 偏误类型：{type}
- 学习者：HSK {level} 级，母语 {native_language}
- 待确认标记：{uncertain}（true → 开头提示"这个判断可能不准确，仅供参考"）
- 超纲标记：{beyond_level}（true → 用更简单语言，并标注"超纲"）
- 图谱历史（可空，读取自 get_kp）：{learner_history}（如"该点已错 3 次"——若历史非空且该点反复出错，应加重"为什么又错"的针对性讲解）
- 候选要点（来自 HSK 知识树/领域规则；可能为空）：{candidate_points}

【约束】
1. 少术语、多例子；每个概念配一个生活化例子。
2. 讲清"为什么错"，不是只给正确答案。
3. 留白引导：结尾留一句引导学习者自己想的提示，不替学习者把话说完。
4. 不超纲：超纲内容用简单语言带过，不展开。
5. 字数按段分配：四段每段各约 30–45 字，总长不再设 ≤150 硬上限；整体仍须精炼，避免冗长啰嗦。
6. key_points 只从"候选要点"中选取并组织语言，不得自由新增与候选无关的点；候选为空时允许自行给出要点，但必须 free_generated=true。
7. 术语规范：一律用汉语语法表述（如"习惯性动作""完成义""量词""补语""语气词"），禁止把英语时态名/句法标签套用到汉语（如"一般现在时""现在完成时"等）；仅在做跨语言类比时可引用母语本身，但语法术语不得用英语时态名。
8. 正确句必给：第③段必须给出一个明确、完整的正确句；若偏误句语义有歧义（可能有不止一种合理解读或修正方向不唯一），先一句话点明你按哪种语义改，再给对应正确句。

【输出格式】严格 JSON（key_points 带 id，来自候选或编排层注入）：
{{"explanation": "四段式讲解正文", "key_points": [{{"id": "kp-1", "text": "要点1"}}], "keywords": ["关键词1", "关键词2"], "uncertain_note": "待确认提示（无则空字符串）", "free_generated": false}}"""

# 面向英语母语学习者的讲解骨架：英文解释外壳 + 中文例句/正确句（0.21）
SYSTEM_PROMPT_EN = """[Role] You are an HSK Chinese teaching expert. You use the Feynman method: explain to the learner as if from scratch — few terms, concrete examples, always the "why".

[Task] For the error below, produce a learner-facing four-part explanation, strictly in those four parts (about 20–30 words per part; drop the old ≤150-word hard cap):
① What is wrong: point out the problematic Chinese phrase.
② Why it is wrong: explain the mechanism the Chinese way (Feynman style — minimal jargon, say why).
③ Better way: give the corrected, complete Chinese sentence (required — never diagnose without giving the fix).
④ Example: give one natural Chinese example using the correct pattern.
Then pick 3 core key_points from the candidates and 1-3 deep-dive keywords.

LANGUAGE RULE: Write the explanation shell in English (the learner's native language). Keep all target-language content — the corrected sentence, the example, and the Chinese usage rule terms — in Chinese (with pinyin only if helpful). Never translate Chinese grammar into English tense labels.

[Context]
- Original sentence: {sentence}
- Error: {fragment} → correction: {correction}
- Error type: {type}
- Learner: HSK {level}, native language {native_language}
- Uncertain: {uncertain} (true → open with "This judgment is tentative.")
- Beyond level: {beyond_level} (true → keep it simpler, note "beyond your level")
- Graph history (may be empty): {learner_history} (if the point was missed repeatedly, explain more specifically WHY it keeps recurring)
- L1 transfer hypothesis (candidate, may be empty): {transfer_hint}
- Candidate points: {candidate_points}

[Constraints]
1. English explanation shell; Chinese for correct sentences, examples, and usage-rule terms.
2. Explain the "why", not just the right answer.
3. End with a light open-ended nudge to let the learner think — don't finish it for them.
4. Keep it simple for beyond-level content.
5. Always give a full corrected Chinese sentence; if the original is ambiguous, say which reading you corrected, then give the fix.
6. key_points must come only from the candidates; if candidates are empty you may propose your own points but must set free_generated=true.
7. Chinese grammar terms stay Chinese (量词, 补语, 语气词…); do not map them to English tense/syntax labels.
8. If an L1 transfer hypothesis is present, weave it into part ② as the likely cause: name the native-language habit in plain words (e.g. "in English, 'many' comes before the noun"), then contrast with how Chinese works. Mark it as a likely reason, not a certainty.

[Output] Strict JSON (key_points carry ids):
{{"explanation": "English explanation with Chinese examples.", "key_points": [{{"id": "kp-1", "text": "English positive point with Chinese example if helpful"}}], "keywords": ["keyword1", "keyword2"], "uncertain_note": "tentative note or empty", "free_generated": false}}"""


def _fallback_explanation(error: dict, native_lang: str = "") -> dict:
    """JSON 连续失败后的降级：返回模板文案（对齐 2.2 §三：降级模板，不静默透传坏结果）。
    0.21：native_lang 非 zh → 英文降级文案（中文修正句保留）。"""
    frag = error.get("fragment", "")
    corr = error.get("correction", "")
    if native_lang and native_lang.lower() != "zh":
        explanation = (
            f'The phrase "{frag}" is not quite natural here. '
            f'A more idiomatic Chinese way to say it is "{corr}". '
            f"Read and compare the corrected sentence a few times."
        )
    else:
        explanation = f"「{frag}」这里不太对，更地道的说法是「{corr}」。可以对照这句多练几遍。"
    return {
        "explanation": explanation,
        "key_points": [],
        "keywords": [],
        "uncertain_note": "",
        "free_generated": False,
        "_degraded": True,
    }


class Explainer:
    """费曼讲解引擎（对齐 2.2 v0.3）"""

    def __init__(self, client: LLMClient = None, graph=None):
        self.client = client or LLMClient()
        self.graph = graph   # 可选的图谱数据层（2.4 读方）；None 则历史为空

    @staticmethod
    def _learner_history(graph, kp_id: str) -> str:
        """读图谱 get_kp，生成 {learner_history} 摘要（2.2 §一 ① / §四·图谱历史）。未建图谱/无记录则空。"""
        if graph is None or not kp_id:
            return ""
        info = graph.get_kp(kp_id)
        if not info:
            return ""
        node = info.get("node", {})
        ec = node.get("error_count", 0)
        parts = []
        if ec:
            parts.append(f"该点已错 {ec} 次")
        # 关联混淆边提示
        if info.get("related"):
            rel = "、".join(r["kp_id"] for r in info["related"][:3])
            parts.append(f"与 {rel} 易混淆")
        return "；".join(parts)

    def explain(self, error: dict, user_level: str = "HSK3", native_lang: str = "",
                graph=None, candidate_points: Optional[List[dict]] = None) -> dict:
        """生成费曼讲解（2.2 v0.3 schema）。
        error 须含 2.1 字段：sentence/fragment/correction/type/knowledge_point_id。
        candidate_points: 编排层注入的候选要点 [{id, text}]；None/空 → 候选为空（free_generated 降级）。
        """
        graph = graph or self.graph
        sentence = error.get("sentence", "")
        fragment = error.get("fragment", "")
        correction = error.get("correction", "")
        etype = error.get("type", "")
        kp_id = error.get("knowledge_point_id", "")
        uncertain = "true" if error.get("uncertain") else "false"
        beyond = "true" if error.get("beyond_level") else "false"

        learner_history = self._learner_history(graph, kp_id)

        # 候选要点槽（可能有空 → 约束 6 free_generated 降级）
        if candidate_points:
            cand_str = "\n".join(f"- {p.get('id')}: {p.get('text')}"
                                 for p in candidate_points)
            free_generated = "false"
        else:
            cand_str = "（无候选要点，可选许自行给出，但须标注 free_generated=true）"
            free_generated = "true"

        user_prompt = (
            f"原句：{sentence}\n偏误：{fragment} → 修正：{correction}\n"
            f"偏误类型：{etype}\n学习者：HSK {user_level}级，母语{native_lang or '未知'}\n"
            f"待确认：{uncertain} | 超纲：{beyond}\n图谱历史：{learner_history or '（空）'}\n"
            f"候选要点：\n{cand_str}"
        )

        # 0.21：native_lang 非 zh → 英文讲解骨架（英壳+中例句）；否则中文。
        is_en = bool(native_lang and native_lang.lower() != "zh")
        filled_system = SYSTEM_PROMPT_EN if is_en else SYSTEM_PROMPT

        # 0.22 方向1 · D1.4：确定性迁移归因（零 LLM），命中则注入讲解——明示母语成因。
        # 假设只作 candidate：提示词约束 8 要求"标为可能原因，不当定论"。
        transfer_hint = ""
        if is_en:
            try:
                hyp = transfer_match_one(error, native_lang)
            except Exception:
                hyp = None
            if hyp:
                transfer_hint = (
                    f"rule={hyp['rule_id']} | native-language habit: {hyp['l1_anchor']} "
                    f"| typical Chinese result: {hyp['zh_signature']}"
                )

        if is_en:
            user_prompt = (
                f"Original sentence: {sentence}\n"
                f"Error: {fragment} → correction: {correction}\n"
                f"Error type: {etype}\nLearner: HSK {user_level}, "
                f"native language {native_lang}\n"
                f"Uncertain: {uncertain} | Beyond level: {beyond}\n"
                f"Graph history: {learner_history or '(empty)'}\n"
                f"Candidate points:\n{cand_str}"
            )

        filled_system = filled_system.format(
            sentence=sentence, fragment=fragment, correction=correction, type=etype,
            level=user_level, native_language=native_lang or "未知", uncertain=uncertain,
            beyond_level=beyond, learner_history=learner_history or "（空）",
            transfer_hint=transfer_hint or "(none)",
            candidate_points=cand_str,
        )

        try:
            raw = self.client.chat_json_strict(filled_system, user_prompt, temperature=0.4)
        except JSONStrictError:
            # 降级：模板文案，不静默透传坏结果（2.2 §三）
            return _fallback_explanation(error, native_lang)

        # key_points 必须带 id；候选非空但没选到候选内的点 → 记为 free_generated 供 3.3 评估
        kps = raw.get("key_points", [])
        if not isinstance(kps, list):
            kps = []
        result = {
            "explanation": raw.get("explanation", ""),
            "key_points": kps,
            "keywords": raw.get("keywords", []),
            "uncertain_note": raw.get("uncertain_note", ""),
            "free_generated": bool(raw.get("free_generated", False)) or not candidate_points,
        }
        # 候选存在但 key_points 为空 → 视为自由生成降级
        if candidate_points and len(kps) == 0:
            result["free_generated"] = True
        return result