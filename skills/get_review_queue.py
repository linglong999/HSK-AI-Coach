# ============================================================
# Skill: get_review_queue（skills/get_review_queue.py）
# M1 · ⭐ 差异化卖点：包 ErrorGraph.get_review_queue()
# - 个性化记忆：偏误图谱 → 按 priority 出队的待复习/低掌握知识点
# - OpenMAIC 没有的个性化能力（学习者自己的偏误记忆），单独立壳突出
# - 纯数据查询，无 LLM 依赖；无 Key 环境可测
# ============================================================

from typing import Any, Dict, Optional

from skills.base import Skill


class GetReviewQueueSkill(Skill):
    metadata: Dict[str, Any] = {
        "name": "get_review_queue",
        "version": "0.1.0",
        "summary": "拉取该学习者当前待复习/低掌握的知识点列表（按优先级降序），来自个性化偏误图谱记忆。",
        "triggers": [
            "学习者问'我该复习什么/今天复习什么/我哪些没掌握'",
            "需要根据个性化偏误历史安排复习内容（差异化：OpenMAIC 无此能力）",
        ],
        "guardrails": [
            "仅当存在偏误图谱/复习记录时才返回有效队列；学习者尚无记录时返回空并说明",
            "不应在询问单个知识点定义时触发——那是 lookup_knowledge_point",
        ],
        "input": {
            "learner_id": "可选，学习者标识，默认 'default'；由调用方注入，技能不自行获取上下文",
            "limit": "可选，返回条数上限",
            "opts": "可选，透传给图谱的筛选选项",
        },
        "output": "List[dict]：按 priority 降序的待复习 KP（id/name/mastery/error_count/priority），关联偏误",
    }
    input_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "learner_id": {"type": "string"},
            "limit": {"type": "integer"},
            "opts": {"type": "object"},
        },
    }
    output_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "count": {"type": "integer"},
            "items": {"type": "array"},
            "_error": {"type": "string"},
        },
    }

    def __init__(self, graph=None):
        super().__init__()
        self._graph = graph

    def _get_graph(self):
        if self._graph is None:
            from engine.graph.error_graph import ErrorGraph
            self._graph = ErrorGraph()
        return self._graph

    def run(self, context: Dict[str, Any]) -> Dict[str, Any]:
        graph = self._get_graph()
        opts = context.get("opts")
        try:
            queue = graph.get_review_queue(opts=opts)
        except Exception as e:
            return {"count": 0, "items": [], "_error": f"读取复习队列失败: {e}"}

        limit = context.get("limit")
        if limit is not None:
            try:
                queue = queue[: int(limit)]
            except (TypeError, ValueError):
                pass

        return {"count": len(queue), "items": queue}