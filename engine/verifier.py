# ============================================================
# 复述验证引擎（verifier）
# 对齐 2.3 v0.3 定稿 schema
# - LLM 层：逐要点 is_covered 二元判定（不直接出三级）+ flag_flowery_but_empty
# - 规则层：coverage_ratio 聚合 → verdict（三级），确定性可测试
# - 写回图谱：ingest_verdict 按 uncertain 分流（2.4 v0.3 写方）
# - temperature=0；降级绝不静默 pass（判 partial）
# ============================================================

import os
import sys
from typing import Optional

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from engine.llm.client import LLMClient, JSONStrictError

# 规则层聚合阈值（2.3 §四，初值随 3.3 标定）
PASS_RATIO = 0.8

SYSTEM_PROMPT = """【角色】你是一个复述覆盖判定器。任务是对每条讲解要点，判断学习者复述是否覆盖/体现出该要点。不要评价对错好坏，只做逐点"是否覆盖"的二元判定。

【任务】逐要点判断"复述是否体现该要点"，输出每条要点的二元判定。

【上下文】
- 讲解：{explanation}
- 逐条要点（唯一来源，勿新增/勿修改）：{key_points}
- 学习者复述：{restatement}
- 该点验证历史：{history}（如"前2次均不通过"）
- 待确认标记：{uncertain}

【判定核心】对每条要点，问自己一个问题：复述者是不是"点出来并落实了"这个知识点的正确用法？
- is_covered = true：复述落回了该要点的核心用法/结构/语义，明确体现了它（哪怕表述略不完美）。
- is_covered = false：复述对该要点只是"话题性提及"——把要点里相关的字眼拿来当话题展开（讲感想、说常识、延伸建议），却没有落到该知识点本身的正确用法上。这类即使说得流畅动听、即使文本里恰好出现相关的词，也算未覆盖。

【硬规则】
1. 关键词出现 ≠ 覆盖：复述文本里出现要点用到的字眼（如"坐飞机""学习中文"），不代表落实了核心语法（如"乘交通工具用'坐'"）。要点看的是有没有"运用"该用法，不是有没有"碰见"该词。
2. 不因复述流畅/话多/有文采而放宽，也不因它简短就判错。空洞但漂亮 → 不覆盖。
3. 只依据"是否覆盖该知识点的用法"，不依据对错好坏或语气情感。

【判别锚点】（通用教学示例，用于校准覆盖与空洞的边界）
- 覆盖：要点「名词'星星'的量词用'颗'」，复述「天上有一颗星星」→ true（落实了"颗"的用法）。
- 空洞：同一要点，复述「星星很美，总爱抬头看，数也数不清」→ false（在谈星空/观感，不是在对"用什么量词"这条回应）。
- 覆盖：要点「表示目的用'为了'」，复述「为了健康，我每天运动」→ true。
- 空洞：同一要点，复述「运动很重要，要坚持，好处特别多」→ false（谈运动的好处，没有说"为了+目的"这个句型）。

【约束】
1. 只输出逐要点判定，不下三级总评——总评由规则层聚合，不由你下。
2. 待确认标记只用于说明，不因它放宽单点覆盖判定。

【输出格式】严格 JSON：
{{"point_judgements": [{{"id": 1, "text": "要点文本", "is_covered": true, "evidence": "复述中点出并落实该用法之处"}}], "flag_flowery_but_empty": false}}"""


class Verifier:
    """复述验证引擎（对齐 2.3 v0.3）"""

    def __init__(self, client: LLMClient = None, graph=None,
                 pass_ratio: float = PASS_RATIO,
                 consecutive_n_retry: int = 2, consecutive_n_teacher: int = 4):
        self.client = client or LLMClient()
        self.graph = graph                      # 可选的图谱数据层（2.4 写方）
        self.pass_ratio = pass_ratio            # 规则层 pass 阈值（3.3 标定）
        self.N_RETRY = consecutive_n_retry      # N=2 降难度重讲
        self.N_TEACHER = consecutive_n_teacher  # N=4 建议找老师
        # 连续失败计数（2.3 口径：**同一任务**连续 verdict∈{partial,fail} 的轮次）。
        # 按任务分键（kp 优先，fragment|type 兜底）——不同知识点/偏误的失败互不累计。
        self._fail_streaks = {}

    @staticmethod
    def _streak_key(bias_ref) -> str:
        if not bias_ref:
            return "adhoc"
        kp = bias_ref.get("knowledge_point_id", "")
        if kp:
            return f"kp:{kp}"
        return f"sig:{bias_ref.get('fragment', '')}|{bias_ref.get('type', '')}"

    @staticmethod
    def _aggregate(judgements: list) -> dict:
        """规则层确定性聚合（§四）：coverage_ratio → verdict"""
        total = len(judgements)
        covered = sum(1 for j in judgements if j.get("is_covered"))
        ratio = (covered / total) if total > 0 else 0.0
        if total == 0:
            verdict = "partial"   # 无要点判partial，不duplicate pass
        elif ratio >= PASS_RATIO:
            verdict = "pass"
        elif ratio > 0:
            verdict = "partial"
        else:
            verdict = "fail"
        return {"verdict": verdict, "covered_points": covered, "total_points": total,
                "coverage_ratio": round(ratio, 4)}

    def verify(self, explanation: str, key_points: list, restatement: str,
               history: str = "", uncertain: bool = False,
               bias_ref: Optional[dict] = None, event_key: Optional[str] = None,
               commit_graph: bool = True) -> dict:
        """验证复述覆盖度（2.3 v0.3）。
        key_points: 2.2 输出的显式要点（[{id,text}]），唯一来源。
        bias_ref + event_key: 写回图谱所需；commit_graph=False 时不写（纯评测）。
        返回：LLM 逐点判定 + 规则聚合 verdict + 降级建议 + 写回状态。
        """
        kp_str = "\n".join(f"- [{p.get('id')}] {p.get('text')}" for p in key_points) \
            if key_points else "（无）"
        user_prompt = (
            f"### 讲解\nexplanation\n\n"
            f"### 逐条要点\n{kp_str}\n\n"
            f"### 学习者复述\n{restatement}\n\n"
            f"### 该点验证历史\n{history or '（无）'}\n"
            f"### 待确认标记\n{'是' if uncertain else '否'}"
        )
        # 注意：原 prompt 里用了 literal "explanation" 占位，这里正确替换
        user_prompt = user_prompt.replace("### 讲解\nexplanation", f"### 讲解\n{explanation}")
        filled_system = SYSTEM_PROMPT.format(
            explanation=explanation,
            key_points=kp_str,
            restatement=restatement,
            history=history or "（无）",
            uncertain="是" if uncertain else "否",
        )

        try:
            raw = self.client.chat_json_strict(filled_system, user_prompt, temperature=0.0)
        except JSONStrictError:
            # 调用层失败 → 本轮判 partial（绝不静默 pass，P1-1）
            return self._wrap({"verdict": "partial", "covered_points": 0,
                               "total_points": len(key_points), "coverage_ratio": 0.0,
                               "degraded": True}, raw={}, key_points=key_points,
                              bias_ref=bias_ref, uncertain=uncertain,
                              event_key=event_key, commit_graph=commit_graph)

        judgements = raw.get("point_judgements", [])
        if not isinstance(judgements, list):
            judgements = []
        agg = self._aggregate(judgements)
        flowery = bool(raw.get("flag_flowery_but_empty", False))

        result = self._wrap(agg, raw=raw, key_points=key_points, bias_ref=bias_ref,
                            uncertain=uncertain, event_key=event_key,
                            commit_graph=commit_graph, flowery=flowery)
        return result

    def _wrap(self, agg: dict, raw: dict, key_points: list, bias_ref, uncertain,
              event_key, commit_graph, flowery: bool = False) -> dict:
        verdict = agg["verdict"]
        # 连续失败计数（§四 连续失败口径，按任务分键）：verdict∈{partial,fail} 计+1，pass 清零
        skey = self._streak_key(bias_ref)
        if verdict in ("partial", "fail"):
            self._fail_streaks[skey] = self._fail_streaks.get(skey, 0) + 1
        else:
            self._fail_streaks[skey] = 0
        consecutive_fail = self._fail_streaks[skey]
        # 降级触发（N=2 降难度重讲 / N=4 建议找老师）
        action = None
        if consecutive_fail >= self.N_TEACHER:
            action = "suggest_teacher"
        elif consecutive_fail >= self.N_RETRY:
            action = "retry_simpler"
        # 引导反馈（fail/partial → 提示重试；规则层不参与判定）
        if verdict == "pass":
            feedback = "理解到位。可以试着用这个知识点造一个新句子。"
        elif verdict == "partial":
            feedback = "意思对了一些，但还有要点没覆盖到。再看一眼下面的引导问题：\n" \
                       + self._prompt_for_missing(key_points, raw)
        else:
            feedback = "这次没有覆盖到要点。我们换个更简单的角度再讲一次。"
        # 写回图谱（§四 写回 / 2.4 写接口）：uncertain 分流由 ingest_verdict 内部处理
        write_status = None
        if commit_graph and self.graph is not None and bias_ref is not None:
            write_status = self.graph.ingest_verdict(
                bias_ref=bias_ref, verdict=verdict, uncertain=uncertain,
                event_key=event_key,
                payload={"coverage_ratio": agg.get("coverage_ratio")})
        return {
            "verdict": verdict,
            "covered_points": agg.get("covered_points"),
            "total_points": agg.get("total_points"),
            "coverage_ratio": agg.get("coverage_ratio"),
            "point_judgements": raw.get("point_judgements", []),
            "flowery_but_empty": flowery,
            "feedback": feedback,
            "consecutive_fail": consecutive_fail,
            "action": action,
            "write_status": write_status,
            "degraded": agg.get("degraded", False),
        }

    @staticmethod
    def _prompt_for_missing(key_points: list, raw: dict) -> str:
        """为未覆盖的要点生成引导问题（提示语二次生成归属后续；MVP 用规则模板）"""
        miss = []
        for j in raw.get("point_judgements", []):
            if not j.get("is_covered"):
                miss.append(j.get("text", ""))
        if miss:
            return "请想一想：" + "；".join(f"「{m}」这块怎么说？" for m in miss[:2])
        return "请试着用自己的话把刚才学的再说一遍。"