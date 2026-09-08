# ============================================================
# 偏误图谱（Error Graph）—— 记忆层 · 确定性数据层（非 Agent）
# 对齐 2.4 落地设计 v0.3（已签核）
# - 节点：KP 知识点（error_types/error_count/mastery/last_learnt_at）
# - aging：递增形态，越久未复习优先级越高
# - 写接口：ingest_error / ingest_verdict / confirm_item / touch_node
# - 读接口：get_kp / get_related / get_review_queue
# - 待确认队列 + 幂等键 + 冲突序（§二~§八）
# ============================================================

import json
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from engine.graph.error_kind_map import resolve
from engine.scheduler import fsrs

# aging 形态：线性 1 + LAMBDA*days（3.x 定案 T1：λ=0.1/天，敏感区间[0.05,0.2]）
LAMBDA_AGING = 0.1
MASTERY_K = 0.5            # pass 更新 k（3.x 定案 T2：连续3次pass≈0.88掌握）
MASTERY_FAIL_DECAY = 0.5   # fail 更新系数（3.x 定案 T4）
MASTERY_NEW_ERROR = 0.5    # 新错误 ingest_error 的 mastery 衰减（3.x 定案 T3）
CONFIRM_REVISE_C2 = 0.5    # 复合确认门槛 c2（3.x 定案 T5：保守收录）
ERROR_COUNT_CAP = 5        # 3.4 修复：priority 内 error_count 封顶，削弱"错误次数×(1-mastery)"双重计数致高频霸榜

# P0.4：priority 分类型加权 + 化石化
NATURE_WEIGHT = {
    "错序": 1.3,   # 纠错难度高、迁移性强 → 提权
    "误代": 1.3,
    # "误加"/"遗漏" → 1.0；未命中/.get 缺省 1.0（含 nature="" 与 "未知"）
}
FOSSIL_RULE = {"unfixed_streak": 3, "days_idle": 180}  # 连续≥3次复习未纠正 且 距上次学习≥180天
FOSSIL_BOOST = 1.5        # 化石化节点 priority 乘数（与 nature 权重互斥，不叠乘）

# 冲突优先级（§八）：跨类按类序（verdict 写 > 识别偏误写 > 图谱自身更新）
_CLASS_ORDER = {"verdict": 0, "error": 1, "graph": 2}


@dataclass
class Node:
    """KP 知识点节点（2.4 §2.1）"""
    id: str
    knowledge_point: str
    level: str
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


class ErrorGraph:
    """偏误图谱（2.4 v0.3）：确定性数据层，无 LLM。全局写锁串行化（§八）。"""

    def __init__(self, learner_id: str = "default"):
        self.learner_id = learner_id
        self._nodes: Dict[str, Node] = {}
        self._edges: Dict[str, Edge] = {}     # key = "from|to"
        self._queue: Dict[str, QueueItem] = {}
        self._lock = threading.RLock()
        self._seen_events = set()             # 全局事件幂等集（§三 event_key 判重）

    # ---------------- 内部工具 ----------------
    @staticmethod
    def _now() -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    @staticmethod
    def _days_since(ts: Optional[str]) -> float:
        """距 now 的天数；无法解析按 0（不阻断）"""
        if not ts:
            return 0.0
        try:
            struct = time.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")
            past = time.mktime(struct)
            return max(0.0, (time.time() - past) / 86400.0)
        except ValueError:
            return 0.0

    def _aging(self, node: Node) -> float:
        """2.4 §4.3：递增 aging。last_learnt_at 非空以它算；null（首建）以 created_at 起算。
        采用线性 1 + λ*days（3.x 标定选形态）。"""
        base_ts = node.last_learnt_at
        if base_ts is None:
            # null 节点以创建时间起算——需要 created_at，加在节点上无法解析时跳过
            base_ts = getattr(node, "created_at", None)
        days = self._days_since(base_ts) if base_ts else 0.0
        return 1.0 + LAMBDA_AGING * days

    def _refresh_fossilized(self, node: Node) -> None:
        """P0.4：现算化石化（不落盘）。连续≥FOSSIL_RULE[unfixed_streak]次复习未纠正
        且距上次成功学习(last_learnt_at)≥days_idle 天 → fossilized=True。
        last_learnt_at 为 None（从未成功学习）→ days=0 → 永不误标。"""
        days = self._days_since(node.last_learnt_at)
        node.fossilized = (node.unfixed_streak >= FOSSIL_RULE["unfixed_streak"]
                           and days >= FOSSIL_RULE["days_idle"])

    def _priority(self, node: Node) -> float:
        """P0.4：priority = base × 分类型加权（读前先 refresh 化石化）。
        base = min(error_count,5) × (1-mastery) × aging（3.4 封顶）。
        加权（A1/A3 拍板）：
          - 非化石 → × NATURE_WEIGHT.get(nature, 1.0)（错序/误代1.3；误加/遗漏/未知/"" → 1.0）
          - 化石   → × FOSSIL_BOOST(1.5)，**互斥**：替代 nature 权重，不叠乘
        fossilized 每次读取前现算，保证 streak 落盘后随时刷新。"""
        self._refresh_fossilized(node)
        base = min(node.error_count, ERROR_COUNT_CAP) * (1.0 - node.mastery) * self._aging(node)
        if node.fossilized:
            return base * FOSSIL_BOOST
        return base * NATURE_WEIGHT.get(node.nature, 1.0)

    def _edge_key(self, a: str, b: str) -> str:
        return "|".join(sorted([a, b]))

    @staticmethod
    def _apply_dimensions(node: Node) -> None:
        """P0.2：按节点当前 error_types/kp_id 计算并回填 error_kind/nature。
        在 error_types 变更后调用，保证双字段与主导类型同步。"""
        dims = resolve(node.error_types, node.id)
        node.error_kind = dims["error_kind"]
        node.nature = dims["nature"]

    # ---------------- 写接口（§三，幂等） ----------------
    def _event_seen(self, event_key: str) -> bool:
        if event_key in self._seen_events:
            return True
        self._seen_events.add(event_key)
        return False

    def ingest_error(self, bias: dict, event_key: str) -> dict:
        """2.4 §三 ingest_error：识别引擎写入偏误。
        ① uncertain=false 且 kp 命中 → 幂等 upsert 节点；② uncertain 或 kp 未命中 → 待确认队列。
        bias 需含：fragment/correction/type/type_confident/confidence/knowledge_point_id/uncertain
        """
        with self._lock:
            if event_key and self._event_seen(event_key):
                # 同事件重放 → 幂等，不重复计数、不重复建边
                return {"status": "idempotent_skip"}
            fragment = bias.get("fragment", "")
            etype = bias.get("type", "语法")
            kp_id = bias.get("knowledge_point_id", "")
            uncertain = bool(bias.get("uncertain", False))
            kp_hit = bool(kp_id)

            # ① 确认且命中 → upsert 节点
            if not uncertain and kp_hit:
                kp_name = bias.get("knowledge_point_name") or kp_id
                level = bias.get("level") or "未知"
                node = self._nodes.setdefault(
                    kp_id, Node(id=kp_id, knowledge_point=kp_name, level=level,
                                 created_at=self._now()))
                node.error_types[etype] = node.error_types.get(etype, 0) + 1
                node.error_count += 1
                # 新错误：mastery *= 0.5（§4.1）
                node.mastery *= MASTERY_NEW_ERROR
                self._apply_dimensions(node)
                return {"status": "node_upsert", "kp_id": kp_id, "node": node.to_dict()}

            # ② uncertain 或未命中 → 待确认队列
            src = "识别待确认" if uncertain else "KP 未命中"
            status = "pending_mapping" if not kp_hit else "pending"
            sig = {"fragment": fragment, "type": etype, "kp_candidate": kp_id}
            item_key = f"{fragment}|{etype}|{kp_id}"
            item = self._queue.get(item_key)
            if item is None:
                item = QueueItem(item_key=item_key, signature=sig, status=status,
                                 uncertain_src=src, created_at=self._now())
                self._queue[item_key] = item
            if event_key and event_key not in item.seen_event_keys:
                item.seen_event_keys.append(event_key)
            item.updated_at = self._now()
            return {"status": "to_queue", "item_key": item_key}

    def ingest_verdict(self, bias_ref: dict, verdict: str, uncertain: bool,
                       event_key: str, payload: Optional[dict] = None) -> dict:
        """2.4 §三 ingest_verdict：验证引擎写入。按 uncertain 分流——
        uncertain=true → 挂队列 pending_payload（不碰节点）；false → 落 KP 节点按 §4.1。
        """
        with self._lock:
            if event_key and self._event_seen(event_key):
                return {"status": "idempotent_skip"}
            kp_id = bias_ref.get("knowledge_point_id", "")
            if uncertain or not kp_id:
                # 挂待确认队列，payload 挂载，不动节点
                sig = {"fragment": bias_ref.get("fragment", ""),
                       "type": bias_ref.get("type", ""), "kp_candidate": kp_id}
                item_key = f"{sig['fragment']}|{sig['type']}|{kp_id}"
                item = self._queue.get(item_key)
                if item is None:
                    item = QueueItem(item_key=item_key, signature=sig,
                                     status="pending", uncertain_src="复核未过",
                                     created_at=self._now())
                    self._queue[item_key] = item
                if payload is not None:
                    item.pending_payload.append({"type": "verdict", "verdict": verdict,
                                                 **payload})
                if event_key and event_key not in item.seen_event_keys:
                    item.seen_event_keys.append(event_key)
                item.updated_at = self._now()
                return {"status": "verdict_to_queue", "item_key": item_key}

            node = self._nodes.get(kp_id)
            if node is None:
                node = Node(id=kp_id,
                            knowledge_point=bias_ref.get("knowledge_point_name") or kp_id,
                            level=bias_ref.get("level") or "未知", created_at=self._now())
                self._nodes[kp_id] = node
                self._apply_dimensions(node)
            # §4.1：pass 提升且 touch + streak 清零；fail 衰减且不 touch + streak 递增（P0.4）
            if verdict == "pass":
                node.mastery += (1.0 - node.mastery) * MASTERY_K
                node.last_learnt_at = self._now()
                node.unfixed_streak = 0            # P0.4：纠正成功 → 连续未纠正计数归零
            else:  # fail
                node.mastery *= MASTERY_FAIL_DECAY
                node.unfixed_streak += 1           # P0.4：未纠正 → streak 递增（化石化依赖）
                # 不 touch last_learnt_at —— aging 不清零，加速回队首
            return {"status": "node_update", "kp_id": kp_id, "node": node.to_dict(),
                    "verdict": verdict}

    def ingest_positive(self, knowledge_point_id: str, source: str,
                        event_key: str = "", confidence: float = 1.0,
                        ts: Optional[str] = None) -> dict:
        """P0.3 §P0.3 ingest_positive：把"正确用法"也写进图谱（平衡单向偏误信号）。
        只增 positive_count、按来源聚合 positive_sources、刷新 last_positive_at。
        不增 error_count、不动 mastery、不动 last_learnt_at、不触发偏误侧任何计数，
        因此 priority（只看偏误侧）保持**不变**——由单测锁死。
        - 幂等：event_key 入 _seen_events 判重（同一事件不重复计数）。
        - 独立建节点路径：正向节点不填 error_kind/nature（保持缺省""，作为"非偏误极性"信号，
          "未知"是 P0.2 评估语义、不兼职表达正负极性）。
        - confidence 仅事中判定，不持久化（positive_sources 只存 {source: count}）。"""
        with self._lock:
            if event_key and self._event_seen(event_key):
                return {"status": "idempotent_skip"}
            node = self._nodes.setdefault(
                knowledge_point_id,
                Node(id=knowledge_point_id, knowledge_point=knowledge_point_id,
                     level="未知", created_at=self._now()))
            node.positive_count += 1
            node.positive_sources[source] = node.positive_sources.get(source, 0) + 1
            node.last_positive_at = ts or self._now()
            return {"status": "node_positive", "kp_id": knowledge_point_id,
                    "node": node.to_dict()}

    def confirm_item(self, item_key: str, valid: bool, applied_valid: bool = True,
                     c2: Optional[float] = None, learner_marked: bool = False) -> dict:
        """2.4 §七 confirm_item：待确认提升/驳回。
        复合条件：pass ∧ (已复核 valid=true ∨ 未复核→触发复核且 c2>=0.5) 或 学习者标记正确。
        （本数据层只暴露接口，触发一次复核的调度由编排层承担：P2-4）
        """
        with self._lock:
            item = self._queue.get(item_key)
            if item is None:
                return {"status": "not_found"}
            # 确认条件（§七）
            passes = (valid and applied_valid) or (c2 is not None and c2 >= CONFIRM_REVISE_C2) \
                     or learner_marked
            if not passes:
                item.status = "pending"
                item.updated_at = self._now()
                return {"status": "still_pending"}
            # 提升：按签名合并计数入 error_count，mastery 减半应用单次（P2-6）
            sig = item.signature
            kp_id = sig.get("kp_candidate", "")
            etype = sig.get("type", "语法")
            if kp_id:
                node = self._nodes.setdefault(
                    kp_id, Node(id=kp_id, knowledge_point=kp_id,
                                level=bias_level_of(item), created_at=self._now()))
                merged_count = len(item.seen_event_keys) or 1   # 去重事件数（P2-D）
                node.error_count += merged_count
                node.error_types[etype] = node.error_types.get(etype, 0) + merged_count
                node.mastery *= MASTERY_NEW_ERROR  # 单次减半
                self._apply_dimensions(node)
            item.status = "confirmed"
            item.updated_at = self._now()
            return {"status": "confirmed", "item_key": item_key}

    def touch_node(self, kp_id: str) -> dict:
        """2.4 §三 touch_node：复习/重学调用，更新 last_learnt_at=now"""
        with self._lock:
            node = self._nodes.get(kp_id)
            if node is None:
                return {"status": "not_found"}
            node.last_learnt_at = self._now()
            return {"status": "touched", "kp_id": kp_id}

    def review_feedback(self, kp_id: str, correct: Optional[bool] = None,
                        rating: Optional[int] = None, event_key: str = "") -> dict:
        """P0.17：复习环节单点反馈，融合 FSRS 调度 + P0.4 化石化 streak。
        rating 直传优先（1忘记/2困难/3想起/4轻松）；未传则 correct 兜底
        （false→1 / true→3）。顺带同步 mastery？否——本接口只属"复习调度"层：
        - FSRS：last_review_at=now；next_state 更新 S/D；next_review_at 重算（≥1 天）
        - streak：rating==1 → unfixed_streak+1，否则 0（P0.4 规则）
        不 touch mastery / last_learnt_at / error_count（复习调度语义独立）。
        ingest_verdict 是唯一驱动 mastery 的教学主体，不驱动 FSRS（见边界）。"""
        with self._lock:
            if event_key and self._event_seen(event_key):
                return {"status": "idempotent_skip"}
            node = self._nodes.get(kp_id)
            if node is None:
                return {"status": "not_found"}
            if rating is None and correct is None:
                return {"status": "bad_request", "reason": "need rating or correct"}
            r = rating if rating is not None else (3 if correct else 1)

            now = self._now()
            # elapsed_days：FSRS 遗忘曲线需要"距上次复习的时间"
            base_ts = node.last_review_at or node.created_at or now
            elapsed_days = self._days_since(base_ts)

            # 冷启动（无复习史）：首次用 init_state；否则推进 next_state
            prev = fsrs.MemoryState(stability=node.fsrs_stability,
                                    difficulty=node.fsrs_difficulty or 5.0)
            if node.last_review_at is None and node.fsrs_stability <= 0:
                st = fsrs.init_state(r)
            else:
                st = fsrs.next_state(prev, r, elapsed_days)

            node.fsrs_stability = st.stability
            node.fsrs_difficulty = st.difficulty
            node.last_review_at = now
            days = max(1, round(fsrs.interval(st.stability)))
            node.next_review_at = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ",
                time.gmtime(time.time() + days * 86400))

            # P0.4 化石化 streak：rating==1 视为未纠正 → +1，否则 0
            node.unfixed_streak = (node.unfixed_streak + 1) if r == 1 else 0
            self._refresh_fossilized(node)
            return {"status": "review_feedback", "kp_id": kp_id,
                    "rating": r, "interval_days": days,
                    "node": node.to_dict(), "fossilized": node.fossilized}

    def reject_item(self, item_key: str) -> dict:
        """明确非偏误 → 驳回清除（§七）"""
        with self._lock:
            if item_key in self._queue:
                del self._queue[item_key]
                return {"status": "rejected"}
            return {"status": "not_found"}

    def _add_confusion_edge(self, kp_ids: List[str], event_key: str) -> None:
        """§6：同一句 ≥2 已确认偏误且 KP 均命中 → 建混淆边（同事件幂等，不同事件累计）"""
        if len(set(kp_ids)) < 2:
            return
        import itertools
        for a, b in itertools.combinations(sorted(set(kp_ids)), 2):
            key = self._edge_key(a, b)
            edge = self._edges.get(key)
            if edge is None:
                edge = Edge(from_node_id=a, to_node_id=b, created_at=self._now())
                self._edges[key] = edge
            if event_key and event_key not in edge.event_keys:
                edge.event_keys.append(event_key)
                edge.edge_weight = len(edge.event_keys)

    def link_errors_in_sentence(self, kp_ids: List[str], event_key: str) -> dict:
        """核实同一句已确认偏误的 KP 后建混淆边（外呼用，包装 §6）"""
        with self._lock:
            self._add_confusion_edge([k for k in kp_ids if k], event_key)
            return {"status": "links_created", "kp_ids": sorted(set(k for k in kp_ids if k))}

    # ---------------- 读接口（§五） ----------------
    def get_kp(self, kp_id: str) -> Optional[dict]:
        """§五 get_kp：节点 + 关联边 + 历史（供讲解/验证引擎组装上下文）。空则 None"""
        with self._lock:
            node = self._nodes.get(kp_id)
            if node is None:
                return None
            related = []
            for key, edge in self._edges.items():
                ends = key.split("|")
                if kp_id in ends:
                    other = ends[0] if ends[1] == kp_id else ends[1]
                    related.append({"kp_id": other,
                                    "relation": edge.relation,
                                    "edge_weight": edge.edge_weight})
            return {"node": node.to_dict(), "related": related,
                    "priority": round(self._priority(node), 4)}

    def get_related(self, kp_id: str) -> List[dict]:
        """§五 get_related：混淆/同源/递进边（供讲解扩展举例）"""
        with self._lock:
            info = self.get_kp(kp_id)
            return (info or {}).get("related", [])

    def get_review_queue(self, kp_ids: Optional[List[str]] = None, now: Optional[float] = None,
                         opts: Optional[dict] = None) -> List[dict]:
        """§五 get_review_queue：按 priority 降序出队；待确认/未确认项完全排除。
        kp_ids 为可选过滤（缺省返回全部可出队 KP）。"""
        with self._lock:
            confirmed_kp = set()
            for item in self._queue.values():
                if item.status in ("confirmed",):
                    confirmed_kp.add(item.signature.get("kp_candidate"))
            excluded = set(item.signature.get("kp_candidate")
                           for item in self._queue.values()
                           if item.status in ("pending", "pending_mapping"))
            result = []
            for node in self._nodes.values():
                if node.id in excluded:
                    continue  # 待确认/未确认完全排除
                if kp_ids and node.id not in kp_ids:
                    continue
                result.append({"kp_id": node.id, "priority": round(self._priority(node), 4),
                               "node": node.to_dict()})
            result.sort(key=lambda r: r["priority"], reverse=True)
            return result

    # ---------------- P0.17 间隔调度读取 ----------------
    def due_nodes(self, now: Optional[str] = None) -> List[dict]:
        """P0.17：到期筛选。严格口径——next_review_at IS NOT NULL AND ≤ now，
        返回按 priority 降序排（priority 管学什么，这里的顺序是复习先后）。
        冷启动（next_review_at=None）**不进 due**，走 unscheduled_topn（boost 段）。"""
        with self._lock:
            if now is None:
                now = self._now()
            result = []
            for node in self._nodes.values():
                if node.next_review_at is None:
                    continue                      # 冷启动未入调度，不进到期
                if node.next_review_at > now:
                    continue                      # 未到期
                result.append({"kp_id": node.id,
                               "priority": round(self._priority(node), 4),
                               "next_review_at": node.next_review_at,
                               "node": node.to_dict()})
            result.sort(key=lambda r: r["priority"], reverse=True)
            return result

    def unscheduled_topn(self, n: int = 10) -> List[dict]:
        """P0.17：未入调度的高优 TopN（供 P0.11 boost 段）。
        仅取 next_review_at=None 的"从未调度"节点（冷启动），按 priority 取前 n。"""
        with self._lock:
            pool = [node for node in self._nodes.values()
                    if node.next_review_at is None]
            pool.sort(key=lambda nd: self._priority(nd), reverse=True)
            return [{"kp_id": nd.id,
                     "priority": round(self._priority(nd), 4),
                     "node": nd.to_dict()}
                    for nd in pool[:n]]

    # ---------------- 持久化 ----------------
    def save(self, path: Optional[str] = None):
        path = path or f"data/graph_{self.learner_id}.json"
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with self._lock:
            data = {
                "learner_id": self.learner_id,
                "nodes": {nid: n.to_dict() for nid, n in self._nodes.items()},
                "edges": {k: e.to_dict() for k, e in self._edges.items()},
                "queue": {k: q.to_dict() for k, q in self._queue.items()},
                "_seen_events": sorted(self._seen_events),
            }
        # 原子写：先临时文件再 rename（§八）
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)

    def load(self, path: Optional[str] = None):
        path = path or f"data/graph_{self.learner_id}.json"
        if not os.path.exists(path):
            return
        with self._lock:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            self.learner_id = data.get("learner_id", self.learner_id)
            self._nodes = {}
            for nid, nd in data.get("nodes", {}).items():
                node = Node(**{k: nd.get(k) for k in
                               ["id", "knowledge_point", "level", "error_types",
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
                self._nodes[nid] = node
            self._edges = {}
            for key, ed in data.get("edges", {}).items():
                self._edges[key] = Edge(**{k: ed.get(k) for k in
                                           ["from_node_id", "to_node_id", "relation",
                                            "edge_weight", "created_at", "event_keys"]})
            self._queue = {}
            for key, qd in data.get("queue", {}).items():
                self._queue[key] = QueueItem(**{k: qd.get(k) for k in
                                                ["item_key", "signature", "status",
                                                 "pending_payload", "uncertain_src",
                                                 "confirm_req", "seen_event_keys",
                                                 "created_at", "updated_at"]})
            self._seen_events = set(data.get("_seen_events", []))

    # ---------------- M10 前端壳：图谱快照 ----------------
    def graph_snapshot(self) -> dict:
        """前端认知地图的一次性渲染数据：confirmed 节点 + 已确认边 + 复习队列。
        与 save() 数据同构，但**只含已确认**（pending/rejected 不进入画布，防自由发散）。
        """
        nodes = {nid: n.to_dict() for nid, n in self._nodes.items()}
        edges = [e.to_dict() for _k, e in self._edges.items() if e.relation == "混淆"]
        queue = [{"kp_id": r["kp_id"], "priority": r["priority"],
                  "node": r["node"]} for r in self.get_review_queue()]
        return {"learner_id": self.learner_id, "nodes": nodes, "edges": edges,
                "queue": queue}

    def __len__(self):
        return len(self._nodes)


def bias_level_of(item):
    """从 queue item 的 kp_candidate 尽力取等级；MVP 无权威映射，先置未知"""
    return "未知"