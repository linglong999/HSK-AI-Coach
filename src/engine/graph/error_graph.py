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
from typing import Dict, List, Optional

from config.paths import PROJECT_ROOT
from engine.graph.error_kind_map import resolve
from engine.graph.model import Edge, Node, QueueItem
from engine.graph.store import GraphStore
from engine.scheduler.fsrs_adapter import FsrsSchedulerAdapter, MemoryState

# aging 形态：线性 1 + LAMBDA*days（3.x 定案 T1：λ=0.1/天，敏感区间[0.05,0.2]）
LAMBDA_AGING = 0.1
MASTERY_K = 0.5            # pass 更新 k（3.x 定案 T2：连续3次pass≈0.88掌握）
MASTERY_FAIL_DECAY = 0.5   # fail 更新系数（3.x 定案 T4）
MASTERY_NEW_ERROR = 0.5    # 新错误 ingest_error 的 mastery 衰减（3.x 定案 T3）
CONFIRM_REVISE_C2 = 0.5    # 复合确认门槛 c2（3.x 定案 T5：保守收录）
ERROR_COUNT_CAP = 5        # 3.4 修复：priority 内 error_count 封顶，削弱"错误次数×(1-mastery)"双重计数致高频霸榜

# P0.19 根因A修复：kp 名称查表（knowledge_point_id → 人读名称）。
# 识别器/复述验证只回传 id，不传 knowledge_point_name，导致图谱节点 knowledge_point 退化
# 为 kp_id，前端认知地图/复习安排/集中复习满屏裸显示 kp-liangci 之类。这里统一查表兜底：
# 优先级 = bias 显式名 > 查表名 > kp_id（避免 id 裸显）。懒加载，加载失败静默降级 kp_id。
_KNOWLEDGE_POINTS_NAME = None


def _load_kp_names() -> Dict[str, str]:
    """加载 knowledge_points_v1_4.json 的 {id: knowledge_point} 名称映射。失败返回 {}。"""
    global _KNOWLEDGE_POINTS_NAME
    if _KNOWLEDGE_POINTS_NAME is not None:
        return _KNOWLEDGE_POINTS_NAME
    try:
        path = PROJECT_ROOT / "datasets" / "knowledge_points_v1_4.json"
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        names = {}
        for kpid, kp in (raw.get("knowledge_points") or {}).items():
            if kp.get("knowledge_point"):
                names[kpid] = kp["knowledge_point"]
        _KNOWLEDGE_POINTS_NAME = names
    except Exception:  # noqa: BLE001 查表不可用不阻断核心流程
        _KNOWLEDGE_POINTS_NAME = {}
    return _KNOWLEDGE_POINTS_NAME

# P0.4：priority 分类型加权 + 化石化
NATURE_WEIGHT = {
    "错序": 1.3,   # 纠错难度高、迁移性强 → 提权
    "误代": 1.3,
    # "误加"/"遗漏" → 1.0；未命中/.get 缺省 1.0（含 nature="" 与 "未知"）
}
FOSSIL_RULE = {"unfixed_streak": 3, "days_idle": 180}  # 连续≥3次复习未纠正 且 距上次学习≥180天
FOSSIL_BOOST = 1.5        # 化石化节点 priority 乘数（与 nature 权重互斥，不叠乘）
# B4 H4：构式晋升（FOSSIL 同机制一规则两用：错侧石化 / 对侧晋升）
PROMOTE_RULE = {"positive_reps": 3}   # 复现阈值：正向证据 positive_count≥3 且近期无未纠正
PROMOTE_BOOST = 1.5        # 晋升节点 priority 乘数（仿 FOSSIL_BOOST，与 nature 权重互斥）
# B4 H4-2：learned 迁移判据（B3 拍板2 阈值归 B4；attempt_ok 计数源自 B3 avoidance_observer）
LEARNED_RULE = {"attempt_ok_reps": 3}   # 推荐值=3（镜像 PROMOTE_RULE 对称，拍板点 4）

# B4：py-fsrs 调度内核模块级单例（Scheduler 无状态可共享；每 learner 一份 graph 实例共用）
_FSRS = FsrsSchedulerAdapter()

# B4 H5：字子层（图谱独立 char 节点）。字级超纲判定不受影响
# （recognizer.detect_beyond_level 读 lexicon char_level，与图谱字节点无关）。
# 这里仅在需要字级时懒加载 char_level；加载失败静默降级 char-HSK0。
CHAR_NODE_PREFIX = "char:"
_CHAR_LEVEL = None


def _load_char_level() -> Dict[str, int]:
    """懒加载 lexicon char_level（{字: HSK 级}）；失败静默返回 {}（字节点回退 char-HSK0）。"""
    global _CHAR_LEVEL
    if _CHAR_LEVEL is not None:
        return _CHAR_LEVEL
    try:
        path = PROJECT_ROOT / "datasets" / "lexicon_hsk1_4.json"
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        _CHAR_LEVEL = data.get("char_level") or {}
    except Exception:  # noqa: BLE001 数据源不可用不阻断建点流程
        _CHAR_LEVEL = {}
    return _CHAR_LEVEL

# 冲突优先级（§八）：跨类按类序（verdict 写 > 识别偏误写 > 图谱自身更新）
_CLASS_ORDER = {"verdict": 0, "error": 1, "graph": 2}


# 数据模型 Node/Edge/QueueItem 已迁至 engine/graph/model.py（纯数据 + to_dict，
# 不含算法/存储）。ErrorGraph 直接复用不重复定义。


class ErrorGraph:
    """偏误图谱（2.4 v0.3）：确定性数据层，无 LLM。全局写锁串行化（§八）。

    深化拆分后本类为**服务/facade**：组合 GraphStore（engine/graph/store.py：
    内存容器 + save/load 持久化）与 model 数据模型（engine/graph/model.py）；
    本类只承载算法、状态变更写回与只读查询组装。公开 20+ 方法签名冻结，
    _nodes/_edges/_queue/_seen_events 作为兼容代理映射到 store 的对应容器。
    """

    def __init__(self, learner_id: str = "default"):
        self.learner_id = learner_id
        self._store = GraphStore()
        self._lock = threading.RLock()

    # ---- 兼容代理：既有测试/调用方直访问 _nodes/_edges/_queue/_seen_events ----
    # 容器已迁至 GraphStore（engine/graph/store.py），保留 get/set 做向后兼容。
    @property
    def _nodes(self):
        return self._store.nodes

    @_nodes.setter
    def _nodes(self, v):
        self._store.nodes = v

    @property
    def _edges(self):
        return self._store.edges

    @_edges.setter
    def _edges(self, v):
        self._store.edges = v

    @property
    def _queue(self):
        return self._store.queue

    @_queue.setter
    def _queue(self, v):
        self._store.queue = v

    @property
    def _seen_events(self):
        return self._store.seen_events

    @_seen_events.setter
    def _seen_events(self, v):
        self._store.seen_events = v

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

    @classmethod
    def _kp_name(cls, kp_id: str, fallback: str = "") -> str:
        """kp_id → 人读名称：查表兜底，避免 id 裸显。fallback 优先（caller 传的显式名）。"""
        if fallback:
            return fallback
        return _load_kp_names().get(kp_id, kp_id)

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

    def _refresh_promoted(self, node: Node) -> None:
        """B4 H4：现算构式晋升（不落盘，与 _refresh_fossilized 同构）。
        positive_count ≥ PROMOTE_RULE[positive_reps] 且 unfixed_streak == 0
        → promoted=True（达复现阈值、近期无错 → 该构式从挂靠升独立复合卡）。"""
        node.promoted = (node.positive_count >= PROMOTE_RULE["positive_reps"]
                         and node.unfixed_streak == 0)

    def _refresh_learned(self, node: Node) -> None:
        """B4 H4-2：现算 learned 迁移（不落盘，同构）。承接 B3 拍板2"阈值归 B4"。
        effort: 待 B3 avoidance_observer 落地后，attempt_ok 计数挂 Node 字段读取。
        B3 未落地前缺字段 → 容错为 0（不误判 learned，避免越界造 B3 字段）。
        判据：attempt_ok 累积 ≥ LEARNED_RULE[attempt_ok_reps] 且期间无未纠正 attempt_err
        → avoidance_state 迁 learned（"该用未用"节点经反复正确使用退出回避监控）。"""
        ok = node.__dict__.get("attempt_ok_reps", 0) or 0
        err = node.__dict__.get("attempt_err_unresolved", 0) or 0
        node.learned = (ok >= LEARNED_RULE["attempt_ok_reps"] and err == 0)

    def _priority(self, node: Node) -> float:
        """P0.4+B4H4：priority = base × 分类型加权（读前先 refresh 化石化/晋升）。
        base = min(error_count,5) × (1-mastery) × aging（3.4 封顶）。
        加权（A1/A3 拍板，互斥不叠乘）：
          - 化石   → × FOSSIL_BOOST(1.5)（正负对立，优先于晋升）
          - 晋升   → × PROMOTE_BOOST(1.5)（构式达复现阈值无未纠正 → 独立复合卡提权）
          - 其余   → × NATURE_WEIGHT（错序/误代1.3；误加/遗漏/未知/"" → 1.0）
        fossilized/promoted 每次读取前现算，保证落盘字段随时刷新。"""
        self._refresh_fossilized(node)
        self._refresh_promoted(node)
        base = min(node.error_count, ERROR_COUNT_CAP) * (1.0 - node.mastery) * self._aging(node)
        if node.fossilized:
            return base * FOSSIL_BOOST
        if node.promoted:
            return base * PROMOTE_BOOST
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
        if event_key in self._store.seen_events:
            return True
        self._store.seen_events.add(event_key)
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
                kp_name = self._kp_name(kp_id, bias.get("knowledge_point_name") or "")
                level = bias.get("level") or "未知"
                node = self._store.nodes.setdefault(
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
            item = self._store.queue.get(item_key)
            if item is None:
                item = QueueItem(item_key=item_key, signature=sig, status=status,
                                 uncertain_src=src, created_at=self._now())
                self._store.queue[item_key] = item
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
                item = self._store.queue.get(item_key)
                if item is None:
                    item = QueueItem(item_key=item_key, signature=sig,
                                     status="pending", uncertain_src="复核未过",
                                     created_at=self._now())
                    self._store.queue[item_key] = item
                if payload is not None:
                    item.pending_payload.append({"type": "verdict", "verdict": verdict,
                                                 **payload})
                if event_key and event_key not in item.seen_event_keys:
                    item.seen_event_keys.append(event_key)
                item.updated_at = self._now()
                return {"status": "verdict_to_queue", "item_key": item_key}

            node = self._store.nodes.get(kp_id)
            if node is None:
                node = Node(id=kp_id,
                            knowledge_point=self._kp_name(
                                kp_id, bias_ref.get("knowledge_point_name") or ""),
                            level=bias_ref.get("level") or "未知", created_at=self._now())
                self._store.nodes[kp_id] = node
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
            node = self._store.nodes.setdefault(
                knowledge_point_id,
                Node(id=knowledge_point_id, knowledge_point=self._kp_name(knowledge_point_id),
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
            item = self._store.queue.get(item_key)
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
                node = self._store.nodes.setdefault(
                    kp_id, Node(id=kp_id, knowledge_point=self._kp_name(kp_id),
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
            node = self._store.nodes.get(kp_id)
            if node is None:
                return {"status": "not_found"}
            node.last_learnt_at = self._now()
            return {"status": "touched", "kp_id": kp_id}

    def review_feedback(self, kp_id: str, correct: Optional[bool] = None,
                        rating: Optional[int] = None, event_key: str = "",
                        sense_id: Optional[str] = None) -> dict:
        """P0.17：复习环节单点反馈，融合 FSRS 调度 + P0.4 化石化 streak。
        rating 直传优先（1忘记/2困难/3想起/4轻松）；未传则 correct 兜底
        （false→1 / true→3）。顺带同步 mastery？否——本接口只属"复习调度"层：
        - FSRS：last_review_at=now；next_state 更新 S/D；next_review_at 重算（≥1 天）
        - streak：rating==1 → unfixed_streak+1，否则 0（P0.4 规则）
        不 touch mastery / last_learnt_at / error_count（复习调度语义独立）。
        ingest_verdict 是唯一驱动 mastery 的教学主体，不驱动 FSRS（见边界）。
        B4 H3 义项路由：sense_id 指定且命中 node.senses[] 非空子卡 → 该义项子卡
        独立 FSRS 状态更新（s_* 四字段），词级四字段同步更新=词形整体记忆；
        未指定/未命中 → 现行词级单卡路径（零行为变化）。"""
        with self._lock:
            if event_key and self._event_seen(event_key):
                return {"status": "idempotent_skip"}
            node = self._store.nodes.get(kp_id)
            if node is None:
                return {"status": "not_found"}
            if rating is None and correct is None:
                return {"status": "bad_request", "reason": "need rating or correct"}
            r = rating if rating is not None else (3 if correct else 1)

            now = self._now()
            # 旧持久化数据可能落 None → 归一化（FSRS 读取需数字）
            node.fsrs_stability = node.fsrs_stability or 0.0
            node.fsrs_difficulty = node.fsrs_difficulty or 5.0
            # elapsed_days：FSRS 遗忘曲线需要"距上次复习的时间"
            base_ts = node.last_review_at or node.created_at or now
            elapsed_days = self._days_since(base_ts)

            # 冷启动（无复习史）：首次用 init_state；否则推进 next_state
            prev = MemoryState(stability=node.fsrs_stability,
                               difficulty=node.fsrs_difficulty)
            if node.last_review_at is None and node.fsrs_stability <= 0:
                st = _FSRS.init_state(r)
            else:
                st = _FSRS.next_state(prev, r, elapsed_days)

            node.fsrs_stability = st.stability
            node.fsrs_difficulty = st.difficulty
            node.last_review_at = now
            days = max(1, round(_FSRS.interval(st.stability)))
            node.next_review_at = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ",
                time.gmtime(time.time() + days * 86400))

            # P0.4 化石化 streak：rating==1 视为未纠正 → +1，否则 0
            node.unfixed_streak = (node.unfixed_streak + 1) if r == 1 else 0
            self._refresh_fossilized(node)

            # B4 H3 义项路由：sense_id 命中 node.senses[] 非空子卡 → 更新该子卡独立 FSRS
            sense_routed = None
            if sense_id and node.senses:
                for s in node.senses:
                    if s.get("sense_id") == sense_id:
                        st_old = MemoryState(stability=s.get("s_stability") or 0.0,
                                             difficulty=s.get("s_difficulty") or 5.0)
                        base = s.get("s_last_review_at") or node.created_at or now
                        if s.get("s_last_review_at") is None and not (s.get("s_stability") or 0):
                            st_s = _FSRS.init_state(r)
                        else:
                            st_s = _FSRS.next_state(st_old, r, self._days_since(base))
                        s["s_stability"] = st_s.stability
                        s["s_difficulty"] = st_s.difficulty
                        s["s_last_review_at"] = now
                        s_days = max(1, round(_FSRS.interval(st_s.stability)))
                        s["s_next_review_at"] = time.strftime(
                            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + s_days * 86400))
                        sense_routed = {"sense_id": sense_id, "interval_days": s_days}
                        break
            return {"status": "review_feedback", "kp_id": kp_id,
                    "rating": r, "interval_days": days,
                    "node": node.to_dict(), "fossilized": node.fossilized,
                    "sense": sense_routed}

    def reject_item(self, item_key: str) -> dict:
        """明确非偏误 → 驳回清除（§七）"""
        with self._lock:
            if item_key in self._store.queue:
                del self._store.queue[item_key]
                return {"status": "rejected"}
            return {"status": "not_found"}

    def _add_confusion_edge(self, kp_ids: List[str], event_key: str) -> None:
        """§6：同一句 ≥2 已确认偏误且 KP 均命中 → 建混淆边（同事件幂等，不同事件累计）"""
        if len(set(kp_ids)) < 2:
            return
        import itertools
        for a, b in itertools.combinations(sorted(set(kp_ids)), 2):
            key = self._edge_key(a, b)
            edge = self._store.edges.get(key)
            if edge is None:
                edge = Edge(from_node_id=a, to_node_id=b, created_at=self._now())
                self._store.edges[key] = edge
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
            node = self._store.nodes.get(kp_id)
            if node is None:
                return None
            related = []
            for key, edge in self._store.edges.items():
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
            for item in self._store.queue.values():
                if item.status in ("confirmed",):
                    confirmed_kp.add(item.signature.get("kp_candidate"))
            excluded = set(item.signature.get("kp_candidate")
                           for item in self._store.queue.values()
                           if item.status in ("pending", "pending_mapping"))
            result = []
            for node in self._store.nodes.values():
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
            for node in self._store.nodes.values():
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
            pool = [node for node in self._store.nodes.values()
                    if node.next_review_at is None]
            pool.sort(key=lambda nd: self._priority(nd), reverse=True)
            return [{"kp_id": nd.id,
                     "priority": round(self._priority(nd), 4),
                     "node": nd.to_dict()}
                    for nd in pool[:n]]

    # ---------------- B4 H5 字子层 ----------------
    def ensure_char_node(self, word: str) -> List[str]:
        """B4 H5：跟词带出字节点（幂等）。词形 word → 逐字 ensure char Node
        （id="char:璃"、knowledge_point="璃"、level="char-HSK{n}"）。
        只建当前字，不预建全量字表（MVP 随学习事件增量生长）。
        返回本次实际新建的字（已存在 → 不重复建、不进返回）。
        """
        with self._lock:
            created = []
            char_level = _load_char_level()
            for ch in word:
                if not ch.strip():
                    continue
                cid = f"{CHAR_NODE_PREFIX}{ch}"
                if cid in self._store.nodes:
                    continue                       # 幂等：已存在不重建
                lv = char_level.get(ch, 0) or 0
                node = Node(id=cid, knowledge_point=ch,
                            level=f"char-HSK{lv}", created_at=self._now())
                self._store.nodes[cid] = node
                created.append(cid)
            return created

    def get_char_node(self, ch: str) -> Optional[dict]:
        """§五 get_char_node：按字查字子层节点（undef→None）。"""
        with self._lock:
            node = self._store.nodes.get(f"{CHAR_NODE_PREFIX}{ch}")
            return node.to_dict() if node else None

    # ---------------- B5 I2：已掌握词集（脚手架事前约束唯一数据源） ----------------
    @staticmethod
    def _level_grade(level: str) -> Optional[int]:
        """解析节点 level 字符串得整数等级。
        "HSK3"→3、"char-HSK2"→2；"未知"/空/非数字 → None（不参与等级过滤）。
        注：char 节点回归根（避免字级混入词集），负责过滤；此处仅取数值。"""
        if not level:
            return None
        digits = "".join(ch for ch in str(level) if ch.isdigit())
        return int(digits) if digits else None

    def mastered_words(self, threshold: float = 0.7, max_level: Optional[int] = None,
                       limit: int = 200) -> list:
        """B5 I2：已掌握词集（脚手架事前约束的唯一数据源，不另造）。
        取 mastery≥threshold 的词节点，按 priority 降序取 limit（防 prompt 膨胀，
        只喂等级段高频已掌握词——B5-A 已拍）。
        排除项（本轮 review 补）：①char: 前缀节点（B4 字子层自有 mastery，
        字不进"词集"——防污染 allowed_vocab 注入）；②level 形如 char-HSK* 的字级节点。
        max_level: 只含 level 数字≤max_level 的词（None → 不限）。
        返回 list[str]：词形粒度（knowledge_point 值）；senses[] 子卡不单列
        （白名单词整体按词形出）。"""
        with self._lock:
            excluded = set()
            for node in self._store.nodes.values():
                if node.id.startswith(CHAR_NODE_PREFIX) \
                        or (node.level or "").startswith(f"{CHAR_NODE_PREFIX}HSK"):
                    excluded.add(node.id)
            pool = []
            for node in self._store.nodes.values():
                if node.id in excluded:
                    continue                       # 字子层不入词集
                if node.mastery < threshold:
                    continue
                grade = self._level_grade(node.level)
                if max_level is not None and grade is not None and grade > max_level:
                    continue                       # 超出等级段 → 排除
                if not node.knowledge_point:        # 无词形 → 跳过（无注入价值）
                    continue
                pool.append((self._priority(node), node.knowledge_point))
            pool.sort(key=lambda t: t[0], reverse=True)
            return [kp for _prio, kp in pool[:limit]]

    # ---------------- 持久化 ----------------
    def save(self, path: Optional[str] = None):
        with self._lock:
            self._store.save(self.learner_id, path=path)

    def load(self, path: Optional[str] = None):
        with self._lock:
            loaded = self._store.load(self.learner_id, path=path)
            self.learner_id = loaded  # 原实现以数据内 learner_id 覆写现有值

    # ---------------- M10 前端壳：图谱快照 ----------------
    def graph_snapshot(self) -> dict:
        """前端认知地图的一次性渲染数据：confirmed 节点 + 已确认边 + 复习队列。
        与 save() 数据同构，但**只含已确认**（pending/rejected 不进入画布，防自由发散）。
        """
        nodes = {nid: n.to_dict() for nid, n in self._store.nodes.items()}
        edges = [e.to_dict() for _k, e in self._store.edges.items() if e.relation == "混淆"]
        queue = [{"kp_id": r["kp_id"], "priority": r["priority"],
                  "node": r["node"]} for r in self.get_review_queue()]
        return {"learner_id": self.learner_id, "nodes": nodes, "edges": edges,
                "queue": queue}

    def __len__(self):
        return len(self._store.nodes)


def bias_level_of(item):
    """从 queue item 的 kp_candidate 尽力取等级；MVP 无权威映射，先置未知"""
    return "未知"
