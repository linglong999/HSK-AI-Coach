# ============================================================
# Skill: web_search（skills/web_search.py）
# M9 · 薄 wrapper：包 tools.call_tool("web_search", ...)，无额外逻辑
# 供 planner 经 SkillRegistry 可见/调用（M1 预留位 #10 落地）
# - 未配 key → 透传 not_configured（不崩，planner 可提示用户）
# ============================================================

from typing import Any, Dict

from skills.base import Skill


class WebSearchSkill(Skill):
    metadata: Dict[str, Any] = {
        "name": "web_search",
        "version": "0.1.0",
        "summary": "网页搜索：根据查询词获取最新资料（title/link/snippet）。",
        "triggers": [
            "用户需要查最新/未知的中文资料或词义、文化背景",
            "用户明确要求'网上搜一下/查一下'某主题",
        ],
        "guardrails": [
            "未配置搜索 API key 时返回 not_configured，不应臆造搜索结果",
            "搜索结果仅作参考，不自动写入偏误图谱/画像",
        ],
        "input": {
            "query": "必填，搜索查询词（字符串）",
            "max_results": "可选，结果条数上限，默认 5",
        },
        "output": "List[dict]：results[]（title/link/snippet）；未配置返回 not_configured",
    }
    input_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "max_results": {"type": "integer"},
        },
        "required": ["query"],
    }
    output_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "ok": {"type": "boolean"},
            "status": {"type": "string"},
            "results": {"type": "array"},
            "message": {"type": "string"},
        },
    }

    def run(self, context: Dict[str, Any]) -> Dict[str, Any]:
        from tools import call_tool
        params = {
            "query": context.get("query", ""),
            "max_results": context.get("max_results", 5),
        }
        return call_tool("web_search", provider=context.get("provider"),
                         params=params, learner_id=context.get("learner_id", "default"),
                         role=context.get("role", "learner"))