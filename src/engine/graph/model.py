# -*- coding: utf-8 -*-
# engine/graph/model.py —— 偏误图谱·纯数据模型（深化拆分·批次B）
# 只承载数据定义与序列化（to_dict），不含任何算法/存储细节（业界共识：
# 领域对象不应依赖 JSON/文件路径/临时文件，见 Repository 模式）。算法与
# 状态变更留在服务层（ErrorGraph / 后续 store）。
# P0.2/P0.4/P0.17/P0.19 字段口径与 error_graph.py 保持一致，逐字迁入。

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class Node:
    """KP 知识点节点（2.4 §2.1）"""
    id: str
    knowledge_point: str
    level: str              # P0.6：旧粗分字符串化("HSK{n}"/"未知")；level_gf=换算后 GF 级
    level_gf: Optional[int] = None   # P0.6：GF 三等九级(1-9)；None=未定(显示旧 level)
    error_types: Dict[str, int] = field(default_factory=dict)
    error_count: int = 0
    mastery: float = 0.0          # 新建=0
    last_learnt_at: Optional[str] = None   # 首建可为 null → aging 按 created_at 起算
    created_at: str = ""          # 创建时间戳（首建 null 时 aging 按此起算）
    error_kind: str = ""          # P0.2：语言要素主导维度（argmax(error_types)，见 error_kind_map）
    nature: str = ""              # P0.2：鲁健骥四分法主倾向（kp 级映射，未命中回落 type 级/未知）
    positive_count: int = 0       # P0.3：正向证据计数（正确用法）。只增不碰偏误侧。
    positive_sources: Dict[str, int] = field(default_factory=dict)  # P0.3：{source: count} 按来源聚合
    last_positive_at: Optional[str] = None   # P0.3：最近一次正向证据时间戳（UTC）
    unfixed_streak: int = 0       # P0.4：连续复习未纠正次数（pass→0 / fail→+1，落盘）
    fossilized: bool = False      # P0.4：化石化标记（现算，不落盘；读取前须 refresh）
    # ---- P0.17 间隔调度字段（FSRS）----
    last_review_at: Optional[str] = None    # 最近一次复习动作时间(UTC)；FSRS elapsed_days 唯一来源
    next_review_at: Optional[str] = None    # 下次到期时间(UTC)；到期筛选唯一判据(IS NOT NULL AND ≤ now)
    fsrs_stability: float = 0.0             # S：记忆稳定性(天)，FSRS 回写
    fsrs_difficulty: float = 5.0            # D：难度[1,10]，FSRS 回写；0 读作首次用 D0(4) 兜底

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "knowledge_point": self.knowledge_point,
            "level": self.level,
            "level_gf": self.level_gf,
            "error_types": self.error_types,
            "error_count": self.error_count,
            "mastery": self.mastery,
            "last_learnt_at": self.last_learnt_at,
            "created_at": self.created_at,
            "error_kind": self.error_kind,
            "nature": self.nature,
            "positive_count": self.positive_count,
            "positive_sources": self.positive_sources,
            "last_positive_at": self.last_positive_at,
            "unfixed_streak": self.unfixed_streak,
            # P0.17 调度字段（fossilized 不落盘，现算）
            "last_review_at": self.last_review_at,
            "next_review_at": self.next_review_at,
            "fsrs_stability": self.fsrs_stability,
            "fsrs_difficulty": self.fsrs_difficulty,
            # fossilized 不落盘（现算），此处不写
        }


@dataclass
class Edge:
    """偏误关联边（2.4 §2.2）—— relation: 混淆/同源/递进；MVP 仅建混淆边"""
    from_node_id: str
    to_node_id: str
    relation: str = "混淆"
    edge_weight: int = 1            # 语义=同现次数
    created_at: str = ""
    event_keys: List[str] = field(default_factory=list)   # MVP 可承载判重（§6）

    def to_dict(self) -> dict:
        return {
            "from_node_id": self.from_node_id,
            "to_node_id": self.to_node_id,
            "relation": self.relation,
            "edge_weight": self.edge_weight,
            "created_at": self.created_at,
            "event_keys": self.event_keys,
        }


@dataclass
class QueueItem:
    """待确认队列记录（2.4 §2.3）"""
    item_key: str             # 聚合键：fragment+type+疑似KP
    signature: Dict           # {fragment, type, kp_candidate}
    status: str               # pending / pending_mapping / confirmed / rejected
    pending_payload: List = field(default_factory=list)   # 讲解 key_points / 验证 verdict
    uncertain_src: str = ""   # 识别待确认 / 复核未过 / KP 未命中
    confirm_req: bool = False
    seen_event_keys: List[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        return {
            "item_key": self.item_key,
            "signature": self.signature,
            "status": self.status,
            "pending_payload": self.pending_payload,
            "uncertain_src": self.uncertain_src,
            "confirm_req": self.confirm_req,
            "seen_event_keys": self.seen_event_keys,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }