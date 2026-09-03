# ============================================================
# Skill: verify_retell（skills/verify_retell.py）
# M1 · 薄壳包 Verifier.verify()，不改引擎
# - 入参：explanation/key_points/restatement/event_key
# - 出参：逐点判定 + pass/partial/fail 聚合 + 写回图谱（对齐 2.3 schema）
# - event_key 幂等写回；commit_graph 由上层控制（默认 True）
# ============================================================

from typing import Any, Dict, Literal, Optional

from skills.base import Skill


class VerifyRetellSkill(Skill):
    metadata: Dict[str, Any] = {
        "name": "verify_retell",
        "version": "0.1.0",
        "summary": "根据讲解的关键要点，判定学习者复述是否真正理解（pass/partial/fail 三级+逐点覆盖）。",
        "triggers": [
            "学习者学完/听完讲解后复述，需检验是否真懂（费曼学习法的检验环节）",
            "需要判断复述是落实了要点还是流利空洞（话题性提及）",
        ],
        "guardrails": [
            "必须在已有讲解和其 key_points 后才触发；无 key_points 时不宜做严格验证",
            "复述是学习者自发的学习输出；仅在学习者主动复述时触发",
        ],
        "input": {
            "explanation": "必填，讲解正文",
            "key_points": "必填，讲解产出的要点[{id,text}]，覆盖集唯一来源",
            "restatement": "必填，学习者复述文本",
            "event_key": "可选，幂等键（写回图谱用）",
            "native_lang": "可选，讲解反馈语言（planner 0.21 自动注入；非 zh → 英文反馈）",
        },
        "output": "对齐 2.3 schema：point_judgements[] + verdict(pass/partial/fail) + covered/total + 写回状态",
    }
    input_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "explanation": {"type": "string"},
            "key_points": {"type": "array"},
            "restatement": {"type": "string"},
            "event_key": {"type": "string"},
            "native_lang": {"type": "string"},
        },
        "required": ["explanation", "key_points", "restatement"],
    }
    output_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "point_judgements": {"type": "array"},
            "verdict": {"type": "string", "enum": ["pass", "partial", "fail"]},
            "covered_points": {"type": "integer"},
            "total_points": {"type": "integer"},
            "coverage_ratio": {"type": "number"},
            "degraded": {"type": "boolean"},
        },
    }

    def __init__(self, verifier=None, graph=None):
        super().__init__()
        self._verifier = verifier
        self._graph = graph

    def _get_verifier(self):
        if self._verifier is None:
            from engine.verifier import Verifier
            # graph=None 与 Router 行为一致：verify 无 bias_ref 不写图谱（语义统一，不自建旁路图谱）
            self._verifier = Verifier(graph=self._graph)
        return self._verifier

    def run(self, context: Dict[str, Any]) -> Dict[str, Any]:
        explanation = context.get("explanation", "")
        key_points = context.get("key_points", []) or []
        restatement = str(context.get("restatement", "")).strip()
        event_key = context.get("event_key")
        if not explanation:
            return {"_error": "缺少必填入参 explanation", "verdict": "partial"}
        if not restatement:
            return {"_error": "缺少必填入参 restatement（学习者未复述）", "verdict": "partial",
                    "point_judgements": [], "covered_points": 0,
                    "total_points": len(key_points), "coverage_ratio": 0.0, "degraded": False}
        if not key_points:
            # 无要点：引擎判 partial，不静默 pass（对齐 Verifier._aggregate 无要点判 partial）
            return {"point_judgements": [], "verdict": "partial",
                    "covered_points": 0, "total_points": 0, "coverage_ratio": 0.0,
                    "degraded": False, "_error": "key_points 为空（缺覆盖集）"}

        verifier = self._get_verifier()
        try:
            result = verifier.verify(
                explanation=explanation,
                key_points=key_points,
                restatement=restatement,
                event_key=event_key,
                commit_graph=True,
                native_lang=str(context.get("native_lang", "") or ""),
            )
        except Exception as e:
            return {"point_judgements": [], "verdict": "partial", "covered_points": 0,
                    "total_points": len(key_points), "coverage_ratio": 0.0,
                    "degraded": True, "_error": f"验证引擎失败: {e}"}

        # 归一输出（对齐契约：暴露 verdict/覆盖/写回状态）
        return {
            "point_judgements": result.get("point_judgements", []),
            "verdict": result.get("verdict", "partial"),
            "covered_points": result.get("covered_points", 0),
            "total_points": result.get("total_points", len(key_points)),
            "coverage_ratio": result.get("coverage_ratio", 0.0),
            "degraded": bool(result.get("degraded", False)),
            "next": result.get("next", ""),
        }