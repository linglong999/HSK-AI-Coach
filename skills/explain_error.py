# ============================================================
# Skill: explain_error（skills/explain_error.py）
# M1 · 薄壳包 Explainer.explain()，不改引擎
# - 入参：error（单个偏误）+ sentence + user_level/native_lang
# - 出参：讲解正文 + key_points + 修正解释（对齐 2.2 schema）
# ============================================================

from typing import Any, Dict, Optional

from skills.base import Skill, normalize_level_label


class ExplainErrorSkill(Skill):
    metadata: Dict[str, Any] = {
        "name": "explain_error",
        "version": "0.1.0",
        "summary": "对一个已识别的偏误生成费曼式讲解（指出错在哪、为什么错、正确句、类比举例），并给出核心要点。",
        "triggers": [
            "学习者问'这句话为什么不对/讲讲为什么/帮我解释一下'",
            "已识别出偏误，需要生成讲解（常与 identify_errors 连用，前置依赖其输出）",
        ],
        "guardrails": [
            "必须已有具体偏误（error 对象）才触发；不确定错在哪应先 identify_errors",
            "不应在纯求知识点定义时触发——那是 lookup_knowledge_point",
        ],
        "input": {
            "error": "必填，一个偏误对象，含 sentence/fragment/correction/type/knowledge_point_id",
            "user_level": "可选，学习者 HSK 等级，默认 HSK3",
            "native_lang": "可选，母语（影响讲解措辞）",
        },
        "output": "对齐 2.2 schema：explanation/key_points[]（正向知识）/keywords/uncertain_note/free_generated",
    }
    input_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "error": {"type": "object"},
            "user_level": {"type": "string"},
            "native_lang": {"type": "string"},
        },
        "required": ["error"],
    }
    output_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "explanation": {"type": "string"},
            "key_points": {"type": "array"},
            "keywords": {"type": "array"},
            "uncertain_note": {"type": "string"},
            "free_generated": {"type": "boolean"},
        },
    }

    def __init__(self, explainer=None, graph=None):
        super().__init__()
        self._explainer = explainer
        self._graph = graph

    def _get_explainer(self):
        if self._explainer is None:
            from engine.explainer import Explainer
            from engine.graph.error_graph import ErrorGraph
            self._graph = self._graph or ErrorGraph()
            self._explainer = Explainer(graph=self._graph)
        return self._explainer

    def run(self, context: Dict[str, Any]) -> Dict[str, Any]:
        error = context.get("error")
        if not error:
            return {"explanation": "", "key_points": [], "keywords": [],
                    "uncertain_note": "", "free_generated": False,
                    "_error": "缺少必填入参 error"}

        expl = self._get_explainer()
        try:
            result = expl.explain(
                error=error,
                user_level=normalize_level_label(context.get("user_level", "HSK3")),
                native_lang=context.get("native_lang", ""),
                graph=self._graph,
            )
        except Exception as e:  # 引擎级异常 → 降级为明确失败，不静默透传
            return {
                "explanation": "", "key_points": [], "keywords": [],
                "uncertain_note": "", "free_generated": False,
                "_degraded": True, "_error": f"讲解引擎失败: {e}",
            }

        return {
            "explanation": result.get("explanation", ""),
            "key_points": result.get("key_points", []),
            "keywords": result.get("keywords", []),
            "uncertain_note": result.get("uncertain_note", ""),
            "free_generated": bool(result.get("free_generated", False)),
            "_degraded": bool(result.get("_degraded", False)),
        }