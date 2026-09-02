# ============================================================
# Skill: lookup_knowledge_point（skills/lookup_knowledge_point.py）
# M1 · 查询 HSK 知识点定义/等级/要点（纯数据层，无 LLM 依赖）
# - 入参：kp_id（精确 id）或 keyword（关键词模糊搜索）
# - 出参：匹配的 kp（id/name/level/error_types/note）
# - 查询逻辑在 skill 内，LLM 只需给高层级意图（'把字句'/'什么是量词'）
# ============================================================

import json
import os
from typing import Any, Dict, Optional

from skills.base import Skill

try:
    from engine.rag import build_hsk_index, KB_PATH
    _RAG_AVAILABLE = True
except Exception:
    _RAG_AVAILABLE = False


_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_KP_PATH = os.path.join(_PROJECT_ROOT, "datasets", "knowledge_points_v1_4.json")


class LookupKnowledgePointSkill(Skill):
    metadata: Dict[str, Any] = {
        "name": "lookup_knowledge_point",
        "version": "0.1.0",
        "summary": "查一个 HSK 知识点的定义、等级、常见偏误类型和简要说明。纯数据查询，不涉及 LLM。",
        "triggers": [
            "学习者问'把字句是什么/什么是量词/比较句用法'等知识点定义问题",
            "需要查阅某个知识点在清单中的官方描述/等级/偏误类型（供讲解引擎参考）",
        ],
        "guardrails": [
            "不应在已有具体偏误需讲解时触发（那是 explain_error 的职责）",
            "不应在求自由扩展/即兴教学时触发（知识点只查，不教整节课）",
        ],
        "input": {
            "kp_id": "可选，精确知识点 id，如 'kp-ba-sentence'",
            "keyword": "可选，关键词（中文，如 '把字句'/'量词'/'比较句'），与 kp_id 二选一",
        },
        "output": "匹配的 knowledge_point 对象（id/name/level/error_types/note）或空描述",
    }
    input_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "kp_id": {"type": "string"},
            "keyword": {"type": "string"},
        },
    }
    output_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "count": {"type": "integer"},
            "results": {"type": "array"},
            "_error": {"type": "string"},
        },
    }

    def __init__(self, path: Optional[str] = None, rag: Optional[Any] = None):
        super().__init__()
        self._path = path or _DEFAULT_KP_PATH
        self._data: Optional[dict] = None
        self._rag = rag

    def _load(self):
        if self._data is not None:
            return
        try:
            with open(self._path, encoding="utf-8") as f:
                raw = json.load(f)
            self._data = raw.get("knowledge_points", {})
        except Exception as e:
            self._data = {}
            self._load_error = str(e)

    def _get_rag_index(self):
        """惰性构建 RAG 三源索引（决策 D-乙）。失败降级为字符串遍历。"""
        if self._rag is None:
            try:
                self._rag = build_hsk_index()
            except Exception:
                self._rag = False
                return None
        return self._rag if self._rag else None

    def run(self, context: Dict[str, Any]) -> Dict[str, Any]:
        self._load()
        if not self._data:
            return {"count": 0, "results": [],
                    "_error": getattr(self, "_load_error", "知识点清单加载失败")}

        kp_id = context.get("kp_id", "").strip()
        keyword = context.get("keyword", "").strip()

        if kp_id:
            kp = self._data.get(kp_id)
            if kp:
                return {"count": 1, "results": [kp]}
            return {"count": 0, "results": [], "_error": f"未找到知识点 id: {kp_id}"}

        if keyword:
            # 主路径：RAG 倒排检索（排序，防拆散成词术语）
            hits = self._query_rag(keyword)
            if hits:
                return {"count": len(hits), "results": hits}
            # 兜底：字符串子串遍历（RAG 不可用或未命中时保持旧行为）
            hits = []
            kw_lower = keyword.lower()
            for kp in self._data.values():
                name = (kp.get("knowledge_point", "") or "").replace('"', "").replace("“", "").replace("”", "")
                note = kp.get("note", "")
                if kw_lower in name.lower() or kw_lower in note.lower():
                    hits.append(kp)
            if hits:
                return {"count": len(hits), "results": hits}
            return {"count": 0, "results": [], "_error": f"未找到知识点: {keyword}"}

        return {"count": 0, "results": [], "_error": "缺少查询条件（kp_id 或 keyword）"}

    def _query_rag(self, keyword: str) -> list:
        """走 RAG 检索，只返回知识清单源命中（kp_only=True），对用于教学。"""
        idx = self._get_rag_index()
        if not idx:
            return []
        hits = idx.query(keyword, top_k=6, kp_only=True)
        results = []
        for h in hits:
            chunk = h["chunk"]
            results.append({
                "id": chunk["kp_id"],
                "knowledge_point": chunk["knowledge_point"],
                "level": chunk["level"],
                "error_types": chunk["error_types"],
                "note": chunk["note"],
                "score": h["score"],
                "source": chunk["source"],
                "in_graph": chunk.get("in_graph", False),
            })
        return results