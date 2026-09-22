# -*- coding: utf-8 -*-
# engine/graph/store.py —— 偏误图谱·内存容器 + JSON 持久化（深化拆分·批次C）
# Repository/Store 角色（业界共识：持久化策略归独立层，领域对象不依赖
# JSON/文件路径/临时文件，见 Repository 模式）。保存原子写（.tmp + os.replace），
# 加载做旧字段安全回填/现算补齐（见 P0.2/P0.3/P0.4/P0.5 迁移注释）。
# 只碰内存容器与序列化，不含算法/状态变更（算法留在 ErrorGraph 服务层）。

import json
import os
from typing import Dict, Optional

from engine.graph.error_kind_map import resolve
from engine.graph.model import Edge, Node, QueueItem


class GraphStore:
    """节点的内存容器 + 落盘。

    持有 _nodes/_edges/_queue/_seen_events 四个状态容器与 save/load。
    算法/状态变更（ingest/verdict/priority/aged 等）不在此层。
    """

    def __init__(self):
        # 普通可写实例属性（服务层 load 复位、算法直接读写节点）
        self.nodes: Dict[str, Node] = {}
        self.edges: Dict[str, Edge] = {}       # key = "from|to"
        self.queue: Dict[str, QueueItem] = {}
        self.seen_events = set()
        # P0.5(修正)：created_at 缺失时用加载时缺省，避免丢 aging 起算点见 load。

    # ---------------- 持久化 ----------------
    def save(self, learner_id: str, path: Optional[str] = None):
        path = path or f"data/graph_{learner_id}.json"
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        data = {
            "learner_id": learner_id,
            "nodes": {nid: n.to_dict() for nid, n in self.nodes.items()},
            "edges": {k: e.to_dict() for k, e in self.edges.items()},
            "queue": {k: q.to_dict() for k, q in self.queue.items()},
            "_seen_events": sorted(self.seen_events),
        }
        # 原子写：先临时文件再 rename
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)

    def load(self, learner_id: str, path: Optional[str] = None, default_learner: str = "default"):
        """从 path 读入容器；缺文件惰性返回 False。旧字段安全回填/现算补齐。
        返回 load 到的 learner_id（调用方决定是否覆写）。"""
        path = path or f"data/graph_{learner_id}.json"
        if not os.path.exists(path):
            return default_learner
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self.nodes = {}
        for nid, nd in data.get("nodes", {}).items():
            node = Node(**{k: nd.get(k) for k in
                           ["id", "knowledge_point", "level", "level_gf",
                            "error_types",
                            "error_count", "mastery", "last_learnt_at",
                            "created_at", "error_kind", "nature",
                            "positive_count", "positive_sources",
                            "last_positive_at", "unfixed_streak",
                            "last_review_at", "next_review_at",
                            "fsrs_stability", "fsrs_difficulty"]})
            node.id = nid
            # P0.5(修正)：created_at 必须在白名单——旧数据含它，缺失会丢 aging 起算点
            if not node.created_at:
                node.created_at = ""
            # P0.2：旧数据缺双字段 → 按现有 error_types/kp 现算补齐（防静默丢字段）。
            # P0.3：缺正向字段 → 用安全缺省（0/空 dict/None）兜底。
            if not node.positive_sources:
                node.positive_sources = {}
            if node.positive_count is None:
                node.positive_count = 0
            if "last_positive_at" not in nd:
                node.last_positive_at = None
            if node.unfixed_streak is None:
                node.unfixed_streak = 0   # P0.4：旧数据缺 streak → 0
            if not node.error_kind or not node.nature:
                dims = resolve(node.error_types, node.id)
                if not node.error_kind:
                    node.error_kind = dims["error_kind"]
                if not node.nature:
                    node.nature = dims["nature"]
            self.nodes[nid] = node
        self.edges = {}
        for key, ed in data.get("edges", {}).items():
            self.edges[key] = Edge(**{k: ed.get(k) for k in
                                      ["from_node_id", "to_node_id", "relation",
                                       "edge_weight", "created_at", "event_keys"]})
        self.queue = {}
        for key, qd in data.get("queue", {}).items():
            self.queue[key] = QueueItem(**{k: qd.get(k) for k in
                                           ["item_key", "signature", "status",
                                            "pending_payload", "uncertain_src",
                                            "confirm_req", "seen_event_keys",
                                            "created_at", "updated_at"]})
        self.seen_events = set(data.get("_seen_events", []))
        return data.get("learner_id", default_learner)