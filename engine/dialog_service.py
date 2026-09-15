# ============================================================
# 对话运营服务层（0.30 · 从 serve.py 抽离，Controller-Service 分层）
# 纯业务编排：不 import HTTP、不写响应；所有入口方法返回 (status, payload)。
# HTTP 职责（路由/JSON 读写/Cookie）留在 engine.serve 的 H；
# 本模块持有共享实例缓存（planner/memory/writeback/intervention/
# identify/generation），未来 CLI / 定时任务等非 HTTP 入口可直接复用。
# 迁移语义零改动（2026-09-15 拆分第 1 步）：
#   - serve 的 _do_api_* → 本类入口方法：self._send_json(x, s) → return (s, x)
#   - 游客配额：vid 由 HTTP 层提取（owner-key 判定 + Cookie），扣减判定在本层
#   - H 类缓存（__class__._planners 等）→ 本类实例缓存（service 进程级单例）
# 0.33 深化批次B/C/D：dialog() 主链阶段化 + ProviderResolver 注入缝 +
# DialogRun 编排协作器（engine/dialog_run.py，阶段方法迁入；本类保留入口
# 阶段、锁、实例缓存与纯静态助手稳定挂点，见 CONTEXT.md）。
# ============================================================

import threading
from typing import Optional

from engine.dialog_run import DialogRun


def _default_dialog_llm(messages):
    """planner 的默认 LLM 调用（无静默降级：网络/Key 异常如实上抛，由上层报错）。"""
    from engine.llm.client import LLMClient
    return LLMClient().chat(messages, temperature=0.3)


def _provider_llm(provider):
    """绑定供应商配置的 llm_call（0.20 BYOK）：请求级覆盖 settings 全局配置。"""
    from engine.llm.client import LLMClient
    cfg = {"base_url": provider["base_url"], "api_key": provider["api_key"],
           "model": provider["model"]}
    client = LLMClient()
    return lambda messages: client.chat(messages, temperature=0.3, config=cfg)


class ProviderResolver:
    """ResolveProvider 注入缝（深化批次C · 候选03 第一块）。

    职责收敛：fail-loud 供应商解析 + planner llm_call 工厂。
    - 默认（生产）实现：engine.providers 解析 + LLMClient 绑定配置（BYOK 请求级覆盖）；
    - 注入实现（测试）：injected_llm 给定时跳过解析，llm_call 恒为注入值。
    对外语义与收编前逐字相同（400 unknown_provider / llm_not_configured 文案不变）。"""

    def __init__(self, memory_root: str, injected_llm=None):
        self._memory_root = memory_root
        self._injected = injected_llm

    @property
    def injected(self) -> bool:
        """测试注入路径标识（planner 缓存 __mock__ 键 + 解析跳过的依据）。"""
        return self._injected is not None

    def resolve(self, provider_id: str):
        """fail-loud 解析：(provider, error)。
        - 注入路径：恒 (None, None)（不解析、不触 providers 存储）；
        - provider_id 给定但未命中 → (None, 400 unknown_provider)；
        - 无可用供应商 / 缺 api_key → (None, 400 llm_not_configured)。"""
        if self._injected is not None:
            return None, None
        from engine import providers as prov
        provider = None
        if provider_id:
            provider = next(
                (p for p in prov.effective_providers(self._memory_root)
                 if p["id"] == provider_id), None)
            if provider is None:
                return None, (400, {
                    "error": "未知模型供应商（可能已被删除），"
                             "请在输入框左上角重新选择",
                    "code": "unknown_provider"})
        else:
            provider = prov.resolve_provider(self._memory_root, None)
        if provider is None or not provider.get("api_key"):
            return None, (400, {
                "error": "自由对话需要 LLM API Key：在「设置 → 模型密钥」添加供应商，"
                         "或复制 .env.example 为 .env 填入 DEEPSEEK_API_KEY 后重启服务",
                "code": "llm_not_configured"})
        return provider, None

    def bind_llm_call(self, provider):
        """planner llm_call 工厂：注入路径恒注入值；生产路径 provider → 绑定配置
        的 LLMClient.chat（0.20 BYOK 请求级覆盖），None → settings 默认配置。"""
        if self._injected is not None:
            return self._injected
        if provider:
            return _provider_llm(provider)
        return _default_dialog_llm


class DialogService:
    """对话运营编排：process / verify / generate / dialog / session 管理。
    线程模型：与 HTTP 层共享同一把 RLock（make_handler 注入）——process/verify
    是多步读写组合，仅靠图谱内部锁防不住交错，需整段串行化。
    实例缓存维度：planner×provider、memory×learner、writeback×learner、
    intervention×conversation；service 进程级单例（缓存即权威）。"""

    # 前端 renderTrace 会渲染成成果卡的技能白名单（0.27 随会话持久）
    _CARD_SKILLS = frozenset({
        "identify_errors", "explain_error", "verify_retell",
        "lookup_knowledge_point", "get_review_queue", "generate_unit",
        "web_search", "parse_document", "retrieve_corpus",
    })

    def __init__(self, router, lock=None, generation=None, dialog_llm=None,
                 memory_root: str = "data", gate=None, provider_resolver=None):
        self._router = router
        self._lock = lock if lock is not None else threading.RLock()
        self._generation = generation
        self._memory_root = memory_root
        self._gate = gate               # P0.10 游客配额闸门（None = 关闭）
        # ResolveProvider 注入缝（批次C）：显式注入优先；legacy dialog_llm
        # （测试 mock llm_call，serve.make_handler 透传）包装为注入式 resolver，
        # 两条注入路径统一到同一对象。
        self._resolver = (provider_resolver if provider_resolver is not None
                          else ProviderResolver(memory_root,
                                                injected_llm=dialog_llm))
        self._planners = {}             # provider_id -> Planner（"__mock__"=注入路径）
        self._memories = {}             # learner_id -> LearnerMemory（M5）
        self._writebacks = {}           # learner_id -> Writeback（M8）
        self._interventions = {}        # conversation_id -> InterventionTracker
        self._identify_skill = None     # 预扫识别技能（共享 router.recognizer+graph）

    # ---------- 对外只读：HTTP 层遗留 handler 借用共享实例 ----------

    @property
    def gate(self):
        return self._gate

    def get_memory(self, learner_id: str):
        return self._get_memory(learner_id)

    def get_writeback(self, learner_id: str):
        return self._get_writeback(learner_id)

    # ---------- 入口方法（全部返回 (status, payload)） ----------

    def process(self, payload: dict):
        text = str((payload.get("text") or "")).strip()
        if not text:
            return 400, {"error": "empty text"}
        with self._lock:
            # 同一 Router 实例 → 图谱随学习者在服务内持久累积
            result = self._router.process(text)
            # 补充图谱快照，供前端一次性渲染（无需二次 GET）
            result["graph"] = self._router.graph.graph_snapshot()
        return 200, result

    def verify(self, payload: dict):
        # 契约：key_points 是复述验证唯一点来源（前端从讲解卡回传）
        restatement = str((payload.get("restatement") or "")).strip()
        key_points = payload.get("key_points") or []
        explanation = str((payload.get("explanation") or "")).strip()
        if not restatement:
            return 400, {"error": "empty restatement"}
        # 边界校验：key_points 契约是 dict 列表（含 text）；畸形入参给 400，
        # 不把它透传给 verifier 炸成内部 500（此前实测畸形输入会 AttributeError）
        if (not isinstance(key_points, list)
                or not all(isinstance(kp, dict) and str(kp.get("text") or "").strip()
                           for kp in key_points)):
            return 400, {"error": "invalid key_points: 需 [{text}, …]"}
        with self._lock:
            out = self._router.verify_rephrase(
                explanation, key_points, restatement,
                event_key=f"web#{restatement}")  # 稳定 key：实体本身（原则4）
        return 200, out

    def generate(self, payload: dict):
        # 生成费曼单元（逻辑统一，错误由 generate_unit 结构化为 degraded，非 500）
        unit_type = str((payload.get("unit_type") or "")).strip()
        if unit_type not in ("explain", "practice"):
            return 400, {
                "ok": False, "status": "degraded",
                "message": f"unsupported unit_type: {unit_type}（仅 explain/practice 首发）",
                "unit": None, "diagnostics": [], "attempts": 0}

        kp = str((payload.get("knowledge_point_id") or "")).strip()
        fragment = str((payload.get("fragment") or "")).strip()
        targets_errors = payload.get("targets_errors") or []
        # practice：命中已确认偏误 → 两段式写回图谱（write_back=True）
        if unit_type == "practice" and kp and not targets_errors:
            targets_errors = [{"fragment": fragment,
                               "knowledge_point_id": kp}]

        context = {
            "for_keypoint": str(payload.get("for_keypoint") or ""),
            "teaching_objective": str(payload.get("teaching_objective") or ""),
            "curriculum_at": payload.get("curriculum_at") or (("kp:" + kp) if kp else ""),
            "previous_speech": str(payload.get("previous_speech") or ""),
            "all_titles": payload.get("all_titles") or [],
            "language_directive": str(payload.get("language_directive") or ""),
            "self_language": str(payload.get("self_language") or "zh"),
            "for_keypoints": payload.get("for_keypoints") or ([kp] if kp else []),
            "targets_errors": targets_errors,
            "task_kind": str(payload.get("task_kind") or "mcq"),
        }
        with self._lock:  # 与 process/verify 同锁，串行化图谱读写
            out = self._get_generation().generate_unit(
                unit_type, context, max_repairs=1,
                write_back=(unit_type == "practice"))
            out["graph"] = self._router.graph.graph_snapshot()
        return 200, out

    def dialog(self, payload: dict, vid=None):
        # 唯一对话入口：Planner 自由 ReAct（0.17）+ M5 多轮记忆（0.18 接入项①）
        #   + M8 画像/惯犯（0.18 接入项③）。
        # A1：trace 转译为学习成果卡；A3：无 Key 直接报错（fail-loud），不静默降级。
        # 记忆策略：服务端 LearnerMemory 为多轮上下文唯一权威源——
        #   读：to_llm_history(conversation_id) 注入 planner（前端透传 history 不再使用）；
        #   写：本轮 user + assistant 回复（含 fallback 文案）追加落盘。
        # 画像策略（M8）：build_profile_summary 进 system；trace 编排 ledger 事件
        #   （识别命中→observation_error，复述 pass→concept_confirmed）；
        #   build_profile_facts 写回 mem.profile.common_errors（图谱×账本×记忆三线汇合）。
        # conversation_id 语义（已定决策 2026-09-01）：同一次对话复用同一 id 延续上下文，
        #   切换知识点/隔天开新 id；不传 → "default"。
        # vid（P0.10 游客配额）：HTTP 层提取的访客 id；None = BYOK/闸门关闭，豁免配额。
        # ---- 主链协作器化（2026-09-15 · 深化批次D）：入口阶段（LoadSessionContext/
        # 游客闸门/ResolveProvider）留本类；其余阶段迁入 DialogRun。锁在本入口
        # 统一持有（R4），实例缓存由本类解析后注入（R2 纯接收）。----
        with self._lock:
            ctx, err = self._load_session_context(payload)
            if err is not None:
                return err
            err = self._consume_visitor_quota(vid)
            if err is not None:
                return err
            provider, err = self._resolve_dialog_provider(ctx["provider_id"])
            if err is not None:
                return err
            return DialogRun(
                router=self._router,
                planner=self._get_planner(provider),
                identify_skill=self._get_identify_skill(),
                mem=self._get_memory(ctx["learner_id"]),
                wb=self._get_writeback(ctx["learner_id"]),
                tracker=self._get_tracker(ctx["conversation_id"]),
                # 纯静态助手注入（构造时取类属性：测试 patch DialogService._xxx
                # 对 dialog 主链同样生效，稳定挂点不破坏）
                normalize_user_level=DialogService._normalize_user_level,
                writeback_ledger_events=DialogService._writeback_ledger_events,
                assistant_payload=DialogService._assistant_payload,
            ).run(ctx, provider)

    # ---------- dialog 入口阶段（深化批次B 保留；其余阶段见 engine/dialog_run.py） ----------

    def _load_session_context(self, payload: dict):
        """阶段 · LoadSessionContext：入参归一（text/learner/conversation/lang/l1/scene_id）。"""
        user_input = str((payload.get("text") or "")).strip()
        if not user_input:
            return None, (400, {"error": "empty text"})
        learner_id = str((payload.get("learner_id") or "")).strip() or \
            self._router.learner_id
        conversation_id = str((payload.get("conversation_id") or "")).strip() or "default"
        provider_id = str((payload.get("provider_id") or "")).strip()
        # 0.21 教学层语言：前端语言开关上送；缺省回落启动参数 --lang（Router.native_lang）
        # v0.3 P0.1 S2 拆分：新增 ui_lang（新）与 learner_l1（新）两个字段；
        # native_lang 保留作为 ui_lang 的兼容别名（旧前端仍可用，旧测试不破坏）。
        ui_lang = str((payload.get("ui_lang") or payload.get("native_lang") or "")).strip().lower() or \
            str(getattr(self._router, "native_lang", "") or "")
        learner_l1 = str((payload.get("learner_l1") or "unknown")).strip().lower()
        # ui_lang 与 learner_l1 独立：选中文界面 ≠ 中文母语；
        # learner_l1 缺省 = "unknown"（不污染 L1 统计）。
        return {
            "user_input": user_input,
            "learner_id": learner_id,
            "conversation_id": conversation_id,
            "provider_id": provider_id,
            "native_lang": ui_lang,  # 局部别名：下游调用兼容旧字段
            "learner_l1": learner_l1,
            "scene_id": str((payload.get("scene_id") or "")).strip(),
        }, None

    def _consume_visitor_quota(self, vid):
        """阶段 · 游客配额闸门（P0.10，放在供应商解析之前：满额直接 429，不调模型、
        不计次、不深校验）。owner-key 判定与 vid 提取在 HTTP 层；此处只做扣减。"""
        if self._gate is None or vid is None:
            return None
        with self._lock:
            ok, remaining, reset = self._gate.check_and_consume(vid)
        if ok:
            return None
        return 429, {
            "error": "今日游客额度已用尽，次日 UTC 00:00 重置；"
                     "配置你自己的 API Key 可无限使用。",
            "code": "visitor_quota_exceeded",
            "message": "配置你自己的 API Key 可无限使用",
            "remaining": 0, "reset_at": reset}

    def _resolve_dialog_provider(self, provider_id: str):
        """阶段 · ResolveProvider：委托 ProviderResolver 注入缝（默认生产实现，
        测试可注入替身）。fail-loud 语义与 400 文案见 ProviderResolver.resolve。"""
        return self._resolver.resolve(provider_id)

    def session_update(self, payload: dict):
        """会话元信息管理：置顶/取消置顶、重命名。
        - bump=False：管理操作不改变 updated_at（排序只反映对话活跃度）
        - title 非空截 60 字；pinned 必须 bool；至少给一个字段
        - 会话不存在 → 404 session_not_found（前端刷新列表自愈）"""
        learner_id = str(payload.get("learner") or "").strip() or \
            self._router.learner_id
        cid = str(payload.get("conversation_id") or "").strip()
        if not cid:
            return 400, {"error": "缺少 conversation_id",
                         "code": "missing_conversation_id"}
        title = payload.get("title")
        pinned = payload.get("pinned")
        if title is not None:
            title = str(title).strip()[:60]
            if not title:
                return 400, {"error": "标题不能为空",
                             "code": "invalid_title"}
        if pinned is not None and not isinstance(pinned, bool):
            return 400, {"error": "pinned 应为布尔值",
                         "code": "invalid_pinned"}
        if title is None and pinned is None:
            return 400, {"error": "无可更新字段（title/pinned）",
                         "code": "nothing_to_update"}
        with self._lock:
            mem = self._get_memory(learner_id)
            if not mem.has_session(cid):
                return 404, {"error": f"会话不存在: {cid}",
                             "code": "session_not_found"}
            mem.touch(cid, title=title, pinned=pinned, bump=False)
        return 200, {"ok": True, "conversation_id": cid,
                     "title": title, "pinned": pinned}

    def session_delete(self, payload: dict):
        """删除整个会话（消息+元信息）。不存在 → 404（前端按已删处理）。"""
        learner_id = str(payload.get("learner") or "").strip() or \
            self._router.learner_id
        cid = str(payload.get("conversation_id") or "").strip()
        if not cid:
            return 400, {"error": "缺少 conversation_id",
                         "code": "missing_conversation_id"}
        with self._lock:
            mem = self._get_memory(learner_id)
            deleted = mem.delete_session(cid)
        if not deleted:
            return 404, {"error": f"会话不存在: {cid}",
                         "code": "session_not_found"}
        return 200, {"ok": True, "conversation_id": cid}

    # ---------- 共享实例懒建/缓存 ----------

    def _get_generation(self):
        """懒建/取共享 GenerationEngine（复用 router.graph，practice 走两段式写回）。
        免 Key 也不崩：generate_unit 内部将 LLM 失败降级为结构化 degraded。"""
        if self._generation is not None:
            return self._generation
        from engine.generation.generator import GenerationEngine
        from engine.memory.writeback import Writeback
        g = GenerationEngine(
            graph=self._router.graph,
            writeback=Writeback(graph=self._router.graph,
                                learner_id=self._router.learner_id))
        self._generation = g
        return g

    def _get_planner(self, provider=None):
        """懒建/取 Planner（唯一对话入口）：共享 router 的图谱与三引擎实例（单一来源）。
        0.20 BYOK：按 provider id 缓存（planner 无会话状态，历史每次显式传入，按供应商分实例安全）；
        注入 mock（测试）路径 → 单实例，provider 维度被 mock 覆盖。"""
        from planner.loop import Planner
        from skills import build_registry

        def _build(llm_call):
            r = self._router
            reg = build_registry(graph=r.graph,
                                 generation=self._get_generation(),
                                 recognizer=getattr(r, "recognizer", None),
                                 explainer=getattr(r, "explainer", None),
                                 verifier=getattr(r, "verifier", None))
            return Planner(reg, llm_call=llm_call)

        cache = self._planners
        if self._resolver.injected:
            if "__mock__" not in cache:
                cache["__mock__"] = _build(self._resolver.bind_llm_call(provider))
            return cache["__mock__"]
        pid = provider["id"] if provider else "__default__"
        if pid not in cache:
            cache[pid] = _build(self._resolver.bind_llm_call(provider))
        return cache[pid]

    def _get_memory(self, learner_id: str):
        """按 learner_id 缓存 LearnerMemory 实例（M5：服务端记忆为多轮上下文唯一权威源）。"""
        mem = self._memories.get(learner_id)
        if mem is None:
            from engine.memory.learner_memory import LearnerMemory
            mem = LearnerMemory(learner_id, root=self._memory_root)
            self._memories[learner_id] = mem
        return mem

    def _get_writeback(self, learner_id: str):
        """按 learner_id 缓存 Writeback（M8：ledger 事件账本 + 两段式确认）。"""
        wb = self._writebacks.get(learner_id)
        if wb is None:
            from engine.memory.writeback import Writeback
            wb = Writeback(graph=self._router.graph,
                           learner_id=learner_id, root=self._memory_root)
            self._writebacks[learner_id] = wb
        return wb

    def _get_tracker(self, conversation_id: str):
        """按 conversation_id 缓存介入跟踪器（0.22 方向3：打断计数/近错窗口/上轮档位）。"""
        from engine.intervention import InterventionTracker
        tr = self._interventions.get(conversation_id)
        if tr is None:
            tr = InterventionTracker()
            self._interventions[conversation_id] = tr
        return tr

    def _get_identify_skill(self):
        """预扫识别技能（0.22 方向3 D3.4：识别触发移到 dialog 主链——每轮产出句
        先走 identify（喂图谱，event_key 幂等与 planner 路径同构）再分档介入）。
        共享 router.recognizer + router.graph 实例（与 planner 注册表同一来源）。"""
        if self._identify_skill is None:
            from skills.identify_errors import IdentifyErrorsSkill
            self._identify_skill = IdentifyErrorsSkill(
                recognizer=getattr(self._router, "recognizer", None),
                graph=self._router.graph)
        return self._identify_skill

    # ---------- 纯逻辑静态方法 ----------

    @staticmethod
    def _normalize_user_level(raw) -> Optional[str]:
        """0.25：归一 user_level（'HSK1'-'HSK6'/数字 1-6）→ 'HSK{n}'；非法 None。"""
        s = str(raw or "").strip().upper()
        s = s[len("HSK"):] if s.startswith("HSK") else s
        try:
            n = int(s)
        except (TypeError, ValueError):
            return None
        if not (1 <= n <= 6):
            return None
        return f"HSK{n}"

    @staticmethod
    def _writeback_ledger_events(wb, trace, user_input: str,
                                 skip_text: str = "") -> list:
        """M8 两段式·账本侧（从 planner trace 编排）：
        - 识别命中（identify_errors ok）→ ledger observation_error（惯犯判定数据源）
        - 复述验证 pass（verify_retell verdict=pass）→ on_confirmed 记 concept_confirmed
        图谱侧写入已在技能内完成（identify→ingest_error / verify→ingest_verdict），
        此处只补事件账本；单条失败降级不阻塞对话。
        确认对象：同轮识别的 KP 优先；跨轮（上轮识别讲解、本轮复述通过）时
        从账本推导"最近观察过且其后无确认"的 KP 兜底（复述验证的是最近讲解，
        讲解对象即最近未确认偏误；宁紧勿滥，倒序最多 3 个）。
        skip_text（0.22 方向3）：预扫已对同句记过账，planner 若重复
        identify 同一句则跳过（防 observation_error 双计→惯犯虚高）。"""
        notices = []
        round_kps = []
        for t in trace or []:
            if not t.get("ok"):
                continue
            name = t.get("name")
            result = t.get("result") or {}
            if name == "identify_errors":
                if skip_text and str((t.get("params") or {}).get(
                        "text", "")).strip() == skip_text:
                    continue
                for err in result.get("errors", []):
                    kp = err.get("knowledge_point_id")
                    if not kp:
                        continue
                    round_kps.append(kp)
                    try:
                        wb.ledger.record("observation_error", kp,
                                         signature=err.get("fragment", ""),
                                         evidence=user_input)
                    except Exception as e:  # noqa: BLE001
                        notices.append({"stage": "ledger_write",
                                        "reason": str(e), "fatal": False})
            elif name == "verify_retell" and result.get("verdict") == "pass":
                kps = round_kps or DialogService._unconfirmed_recent_kps(wb.ledger)
                for kp in dict.fromkeys(kps):
                    try:
                        wb.on_confirmed(kp, evidence="复述验证通过")
                    except Exception as e:  # noqa: BLE001
                        notices.append({"stage": "ledger_write",
                                        "reason": str(e), "fatal": False})
        return notices

    @staticmethod
    def _unconfirmed_recent_kps(ledger, limit: int = 3) -> list:
        """账本推导：最近观察过、且其后无确认事件的 KP（按观察顺序倒序）。
        供跨轮确认侧兜底；事件顺序用列表下标判定（同秒 ts 不可靠）。"""
        events = ledger.recent()
        last_obs, last_conf = {}, {}
        for i, e in enumerate(events):
            kp = e.get("kp_id")
            if not kp:
                continue
            if e.get("kind") in ("observation_error", "repeated_error"):
                last_obs[kp] = i
            elif e.get("kind") == "concept_confirmed":
                last_conf[kp] = i
        pending = [kp for kp, i in last_obs.items() if i > last_conf.get(kp, -1)]
        pending.sort(key=lambda kp: last_obs[kp], reverse=True)
        return pending[:limit]

    @staticmethod
    def _build_why(pre_scan, provider, native_lang):
        """0.26 · 为预扫识别到的偏误生成"为什么"（隐藏弃用/错误 → 空列表不阻断）。
        pre_scan：本轮预扫识别结果（None/无 errors → 返回 []）；
        provider：请求级供应商（None → settings 全局配置，供 mock/默认路径）。
        仅 errors 参与生成（uncertain 不入 why）；任何异常静默降级为空。"""
        try:
            if not (isinstance(pre_scan, dict)
                    and (pre_scan.get("errors") or [])):
                return []
            from engine.llm.client import LLMClient
            from engine.generation.why import generate_why
            cfg = None
            if provider:
                cfg = {"base_url": provider.get("base_url"),
                       "api_key": provider.get("api_key"),
                       "model": provider.get("model")}
            dl = ("zh" if str(native_lang or "").lower() in
                  ("zh", "中文", "汉语", "chinese") else "en")
            directive = ("用中文解释，但错误片段保持中文原文。" if dl == "zh"
                         else "Write reasons in English, but keep the Chinese "
                              "fragments in Chinese.")
            items = generate_why(LLMClient(),
                                 pre_scan.get("errors") or [],
                                 pre_scan.get("hypotheses"),
                                 language_directive=directive,
                                 config=cfg)
            # 回配 kp_id：why 条目确定性锚定到预扫描错误的图谱节点，
            # 前端据此对每条错误挂↗/→/↓分支动作（图谱唯一权威，不经 LLM 自报）
            return DialogService._attach_kp_to_why(
                items, pre_scan.get("errors") or [])
        except Exception:  # noqa: BLE001 生成失败不阻断对话
            return []

    @staticmethod
    def _attach_kp_to_why(items, errors):
        """why 条目 deterministic 回配 kp：把预扫描错误的 knowledge_point_id/type/
        confidence 穿进对应 why 条目。匹配顺序=精确 fragment → 精确 correction → 索引位。
        匹配不到（LLM 改写片段）→ 该条不带 kp_id（前端不挂分支动作），不臆造节点。"""
        if not items:
            return items
        by_frag = {}
        by_corr = {}
        for e in errors or []:
            if not isinstance(e, dict):
                continue
            f = str(e.get("fragment") or "").strip()
            c = str(e.get("correction") or "").strip()
            if f:
                by_frag.setdefault(f, e)
            if c:
                by_corr.setdefault(c, e)
        out = []
        for i, it in enumerate(items):
            if not isinstance(it, dict):
                out.append(it)
                continue
            row = dict(it)
            src = (by_frag.get(str(it.get("fragment") or "").strip())
                   or by_corr.get(str(it.get("correction") or "").strip()))
            if src is None and i < len(errors or []):
                src = errors[i] if isinstance(errors[i], dict) else None
            if src:
                kp = (src.get("knowledge_point_id")
                      or (src.get("graph_write") or {}).get("kp_id")
                      or "")
                if kp:
                    row["kp_id"] = kp
                if src.get("type"):
                    row["type"] = src.get("type")
                if src.get("confidence") is not None:
                    row["confidence"] = src.get("confidence")
            out.append(row)
        return out

    @staticmethod
    def _compact_cards(trace):
        """成果卡轻量视图：过滤丢卡（ok=False）与未知技能，只留 {name,result}。
        前端恢复时 renderTrace 直接消费，保持与实时渲染同源、体积可控（去 params）。"""
        if not trace:
            return []
        out = []
        for tr in trace:
            if not isinstance(tr, dict):
                continue
            if tr.get("ok") is False:
                continue
            if tr.get("name") in DialogService._CARD_SKILLS:
                out.append({"name": tr.get("name"), "result": tr.get("result")})
        return out

    @staticmethod
    def _assistant_payload(res, pre_scan, provider, native_lang, reason):
        """0.27 · assistant 消息随会话持久的成果卡载荷：{cards, why}。
        cards 恒存（这轮的成果卡视图）；why 仅当本轮识别到偏误且非材料句才生成。
        provider 门槛：mock(dialog_llm 注入) 下 provider=None，跳过以免单测触网；
        真实运行 provider 已解析（BYOK 或默认 env 供应商）才生成 why。"""
        cards = DialogService._compact_cards((res or {}).get("trace") or [])
        why = []
        if (provider and isinstance(pre_scan, dict)
                and (pre_scan.get("errors") or [])
                and reason not in ("material",)):
            why = DialogService._build_why(pre_scan, provider, native_lang)
        return {"cards": cards, "why": why}
