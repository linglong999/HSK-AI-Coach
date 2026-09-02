# ============================================================
# Skill: parse_document（skills/parse_document.py）
# M9 · 薄 wrapper：包 tools.call_tool("parse_document", ...)，无额外逻辑
# 供 planner 经 SkillRegistry 可见/调用（M1 预留位 #11 落地）
# - 本地解析 text/markdown/html；复杂格式 → not_configured（不崩）
# ============================================================

from typing import Any, Dict

from skills.base import Skill


class ParseDocumentSkill(Skill):
    metadata: Dict[str, Any] = {
        "name": "parse_document",
        "version": "0.1.0",
        "summary": "解析文档：本地提取文本/Markdown/简单HTML 的正文内容。",
        "triggers": [
            "用户上传/粘贴文本或文档求知、求讲解",
            "用户给出文本/TXT/Markdown/简单HTML 希望转化为可读文本",
        ],
        "guardrails": [
            "复杂格式（PDF/Office/OCR）当前默认关，返回 not_configured",
            "解析结果仅作内容读取，不自动写入偏误图谱/画像",
        ],
        "input": {
            "text": "必填，文档原文（字符串）",
            "format": "可选，text|markdown|html，默认 text",
        },
        "output": "dict：blocks[]（type/text）；复杂格式返回 not_configured",
    }
    input_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "format": {"type": "string"},
        },
        "required": ["text"],
    }
    output_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "ok": {"type": "boolean"},
            "status": {"type": "string"},
            "blocks": {"type": "array"},
            "message": {"type": "string"},
        },
    }

    def run(self, context: Dict[str, Any]) -> Dict[str, Any]:
        from tools import call_tool
        params = {
            "text": context.get("text", ""),
            "format": context.get("format", "text"),
        }
        return call_tool("parse_document", provider=context.get("provider"),
                         params=params, learner_id=context.get("learner_id", "default"),
                         role=context.get("role", "learner"))