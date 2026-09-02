# ============================================================
# Engine: RAG 检索增强（engine/rag.py）
# M7 · 轻量倒排索引（对齐 OpenMAIC InMemoryLexicalIndex 思想，但偏离并行法）
# - 决策：A-乙 倒排索引 / B-乙 单字+术语白名单 / C-乙 打分加权 / D-乙 三源合并
# - 零第三方依赖，纯 stdlib
# ============================================================

import hashlib
import json
import os
import re
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

KB_PATH = os.path.join(_PROJECT_ROOT, "datasets", "knowledge_points_v1_4.json")
LEXICON_PATH = os.path.join(_PROJECT_ROOT, "datasets", "lexicon_hsk1_4.json")

_CJK_CHAR = re.compile(r'[\u4e00-\u9fff]')
_LATIN = re.compile(r'[A-Za-z0-9_]+')

# 术语白名单（决策 B）：知识清单语法点去标点 + 常用成词语法词。
# 命中即以整词索引，避免单字拆散把字句/结果补语等成词术语。
_TERM_WHITELIST = [
    "把字句", "被字句", "比较句", "量词", "能愿动词", "结果补语", "程度补语",
    "动助词", "动态助词", "了着过", "复合趋向补语", "兼语", "双宾语", "把", "被", "比",
    "得", "地", "的", "很", "了", "着", "过",
]


def _strip_kp_name(name: str) -> str:
    """去掉知识点名里的引号/括号占位（如「"把"字句」→「把字句」），用术语匹配。"""
    for ch in ('"', "“", "”", "“", "（）", "(", ")", " ", "…"):
        name = name.replace(ch, "")
    return name or name


def _tokenize(text: str) -> list:
    """决策 B-乙：术语白名单整词 + CJK 单字 + 拉丁/数字。"""
    tokens = []
    for t in _TERM_WHITELIST:
        if t in text:
            tokens.append(t)
    tokens += _CJK_CHAR.findall(text)
    tokens += _LATIN.findall(text)
    return tokens


class HSKLexicalIndex:
    """轻量倒排索引。source 名字节，供防编造/降级。saw：两路 ingest。"""

    def __init__(self):
        self._chunks: dict = {}          # chunk_id -> record
        self._posting: dict = {}         # token -> set[chunk_id]
        self._source_post: dict = {}     # source -> set[chunk_id]
        self._version: str = ""

    # ---------------- 摄入 ----------------
    def ingest_knowledge_points(self, path: str = KB_PATH) -> int:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        pts = raw.get("knowledge_points", {})
        for kp_id, kp in pts.items():
            self._add_chunk({
                "id": kp_id,
                "source": "knowledge_point",
                "text": " ".join([kp.get("knowledge_point", ""), kp.get("note", "")]),
                "kp_id": kp_id,
                "knowledge_point": kp.get("knowledge_point", ""),
                "level": kp.get("level", ""),
                "error_types": kp.get("error_types", []),
                "heading": _strip_kp_name(kp.get("knowledge_point", "")),
                "note": kp.get("note", ""),
                "in_graph": False,
            })
        return len(pts)

    def ingest_lexicon(self, path: str = LEXICON_PATH,
                       max_words: int = 4000) -> int:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        wl = data.get("word_level", {})
        n = 0
        for word, level in wl.items():
            if n >= max_words:
                break
            self._add_chunk({
                "id": f"lex:{word}",
                "source": "lexicon",
                "text": word,
                "kp_id": None,
                "knowledge_point": word,
                "level": level,
                "error_types": [],
                "heading": word,
                "note": f"HSK{level} 级词汇",
                "in_graph": False,
            })
            n += 1
        return n

    def ingest_graph(self, graph) -> int:
        """决策 D-乙：图谱中待复习/已确认的 KP 节点入索引（差异化卖点）。
        graph 无节点时返回 0，不报错。"""
        try:
            queue = graph.get_review_queue()
        except Exception:
            return 0
        graph_kp_ids = set()
        for item in queue:
            node = item.get("node", {})
            kp_id = node.get("id")
            if not kp_id:
                continue
            graph_kp_ids.add(kp_id)
            if kp_id in self._chunks:
                continue  # 已由知识清单覆盖，仅补 in_graph 标记
            self._add_chunk({
                "id": kp_id,
                "source": "graph",
                "text": " ".join([node.get("knowledge_point", kp_id), "待复习"]),
                "kp_id": kp_id,
                "knowledge_point": node.get("knowledge_point", kp_id),
                "level": node.get("level", ""),
                "error_types": list(node.get("error_types", {}).keys()),
                "heading": _strip_kp_name(node.get("knowledge_point", kp_id)),
                "note": "学习中待复习",
                "in_graph": True,
                "mastery": node.get("mastery", 0.0),
            })
        # 对已在知识清单覆盖的图谱 KP 补 in_graph 标记
        for chunk in self._chunks.values():
            if chunk["kp_id"] in graph_kp_ids:
                chunk["in_graph"] = True
        return len(queue)

    def _add_chunk(self, record: dict):
        cid = record["id"]
        self._chunks[cid] = record
        src = record["source"]
        self._source_post.setdefault(src, set()).add(cid)
        for t in set(_tokenize(record["text"])):
            self._posting.setdefault(t, set()).add(cid)

    # ---------------- 版本 ----------------
    def compute_version(self) -> str:
        """版本指纹：源内容+ingest 顺序。供重建判断。"""
        keys = sorted(self._chunks)
        srcs = sorted(self._source_post)
        h = hashlib.sha256()
        h.update(json.dumps([keys, srcs], ensure_ascii=False).encode("utf-8"))
        return h.hexdigest()[:16]

    def set_version(self, v: str):
        self._version = v

    @property
    def version(self) -> str:
        return self._version

    # ---------------- 检索 ----------------
    def query(self, text: str, top_k: int = 8, kp_only: bool = False,
              level: object = None) -> list:
        """倒排：先缩小候选（posting 并集），再打分排序。返回 Hit 列表。"""
        if not text:
            return []
        q_tokens = set(_tokenize(text))
        if not q_tokens:
            return []
        # 倒排缩小候选
        cands: set = set()
        for t in q_tokens:
            cands |= self._posting.get(t, set())
        # 过滤
        if kp_only:
            cands = {c for c in cands if self._chunks[c]["source"] == "knowledge_point"}
        if level is not None:
            cands = {c for c in cands if str(self._chunks[c]["level"]) == str(level)}
        if not cands:
            return []

        hits = []
        q_text = text
        for c in cands:
            s = self._score(self._chunks[c], q_tokens, q_text, level)
            if s > 0:
                hits.append({"chunk": self._chunks[c], "score": s, "method": "lexical"})
        hits.sort(key=lambda h: (-h["score"], h["chunk"]["id"]))
        # preferred_kp_first：同分下知识清单优先
        hits = sorted(hits, key=lambda h: (
            -h["score"], 0 if h["chunk"]["source"] == "knowledge_point" else 1,
            h["chunk"]["id"]))
        return hits[:top_k]

    def _score(self, chunk, q_tokens, q_text, level) -> float:
        ct = set(_tokenize(chunk["text"]))
        matched = q_tokens & ct
        if not matched:
            return 0.0
        coverage = len(matched) / max(1, len(q_tokens))
        s = coverage
        if q_text in chunk["text"]:
            s += 0.25
        heading = chunk.get("heading")
        if heading and heading in q_text:
            s += 0.10
        if chunk.get("in_graph"):
            s += 0.10
        if chunk.get("kp_id") and q_tokens & {chunk["kp_id"]}:
            s += 0.05
        return round(s, 3)

    def get_chunk(self, cid: str):
        return self._chunks.get(cid)

    def __len__(self):
        return len(self._chunks)


def build_hsk_index(include_lexicon: bool = True, max_lexicon_words: int = 4000,
                    graph=None) -> HSKLexicalIndex:
    """一键构建三源索引（决策 D）。graph 可注入（偏误图谱实例或 None）。"""
    idx = HSKLexicalIndex()
    idx.ingest_knowledge_points()
    if include_lexicon:
        idx.ingest_lexicon(max_words=max_lexicon_words)
    if graph is not None:
        idx.ingest_graph(graph)
    idx.set_version(idx.compute_version())
    return idx