# ============================================================
# Skill: retrieve_corpus（skills/retrieve_corpus.py）
# M7 · RAG 语料检索薄壳（0.18 接入项②：第 9 个注册技能）
# - 入参：query（必填）/ top_k（可选，默认 5，上限 10）/ kp_only / level（可选过滤）
# - 出参：top-k 片段 + 来源（chunk_id/source/kp_id/heading），供引用防编造
# - 三源：HSK 知识清单 + 词表 + 该生偏误图谱（graph 源每次 run 幂等刷新，
#   保证"该生老混的/地"等历史错法可被检索，对应 M8 画像联动）
# - 与 lookup_knowledge_point 分工：lookup 查单个 KP 定义（精确）；本技能搜
#   语料片段（含词表/图谱），用于需要"引用来源"的答疑场景
# ============================================================

from typing import Any, Dict, Optional

from skills.base import Skill, normalize_level_int

try:
    from engine.rag import build_hsk_index
    _RAG_AVAILABLE = True
except Exception:
    _RAG_AVAILABLE = False

DEFAULT_TOP_K = 5
MAX_TOP_K = 10


class RetrieveCorpusSkill(Skill):
    metadata: Dict[str, Any] = {
        "name": "retrieve_corpus",
        "version": "0.1.0",
        "summary": "检索权威语料（HSK 知识点/词表/该生偏误图谱），返回带来源的 top-k 片段，供讲解引用、防编造。",
        "triggers": [
            "学习者问知识性问题且需要权威依据（如'把字句是啥''给我个例句''这个词是几级词'）",
            "需要检索该生历史错法/偏误记录（个性化讲解时引用其过往错误）",
        ],
        "guardrails": [
            "查单个知识点的定义/等级/偏误类型用 lookup_knowledge_point（精确查询）；本技能是片段检索，面向'引用来源'的答疑",
            "检索结果不是答案本身：应以返回片段为依据组织讲解并引用来源（source/knowledge_point）",
            "检索未命中时不要编造：如实告知未找到，可改走 web_search 或直接讲解",
        ],
        "input": {
            "query": "必填，检索词/问题（中文，如 '把字句'/'例句 买东西'/'我最近老错的'）",
            "top_k": "可选，返回条数（默认 5，上限 10）",
            "kp_only": "可选，true 时只搜知识点清单（默认 false 搜全部三源）",
            "level": "可选，按 HSK 等级过滤（如 3）",
        },
        "output": "count + results[]（chunk_id/source/kp_id/knowledge_point/level/text/note/score/in_graph）",
    }
    input_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "top_k": {"type": "integer"},
            "kp_only": {"type": "boolean"},
            "level": {},
        },
        "required": ["query"],
    }
    output_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "count": {"type": "integer"},
            "results": {"type": "array"},
            "_error": {"type": "string"},
        },
    }

    def __init__(self, rag: Optional[Any] = None, graph=None):
        super().__init__()
        self._rag = rag
        self._graph = graph

    def _get_index(self):
        """惰性获取共享索引（build_registry 注入优先；直用时自建三源索引）。"""
        if self._rag is None:
            if not _RAG_AVAILABLE:
                return None
            try:
                self._rag = build_hsk_index(graph=self._graph)
            except Exception:
                self._rag = False
                return None
        return self._rag if self._rag else None

    def run(self, context: Dict[str, Any]) -> Dict[str, Any]:
        query = str(context.get("query", "")).strip()
        if not query:
            return {"count": 0, "results": [], "_error": "缺少必填入参 query"}

        idx = self._get_index()
        if idx is None:
            return {"count": 0, "results": [], "_error": "语料索引不可用（构建失败）"}

        # 图谱新鲜度：幂等增量 re-ingest（图谱节点只增不删，重复摄入语义一致），
        # 保证对话中新增的偏误（该生历史错法）可被检索。失败不阻塞 KB/词表检索。
        if self._graph is not None:
            try:
                idx.ingest_graph(self._graph)
            except Exception:
                pass

        top_k = self._normalize_top_k(context.get("top_k"))
        kp_only = bool(context.get("kp_only", False))
        # level 归一（'HSK3'/'3'/3 → 3）：KB 与词表 chunk 均存 int 等级
        raw_level = context.get("level")
        level = None if raw_level in (None, "") else normalize_level_int(raw_level)

        hits = idx.query(query, top_k=top_k, kp_only=kp_only, level=level)
        results = [self._format_hit(h) for h in hits]
        out: Dict[str, Any] = {"count": len(results), "results": results}
        if not results:
            out["_error"] = f"未检索到相关语料: {query}"
        return out

    @staticmethod
    def _normalize_top_k(raw) -> int:
        try:
            v = int(raw)
        except (TypeError, ValueError):
            return DEFAULT_TOP_K
        return max(1, min(v, MAX_TOP_K))

    @staticmethod
    def _format_hit(hit: Dict[str, Any]) -> Dict[str, Any]:
        chunk = hit["chunk"]
        return {
            "chunk_id": chunk["id"],
            "source": chunk["source"],
            "kp_id": chunk["kp_id"],
            "knowledge_point": chunk["knowledge_point"],
            "level": chunk["level"],
            "text": chunk["text"],
            "note": chunk.get("note", ""),
            "score": hit["score"],
            "in_graph": chunk.get("in_graph", False),
        }
