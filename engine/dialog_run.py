# -*- coding: utf-8 -*-
# engine/dialog_run.py —— dialog() 主链编排协作器（0.33 深化批次D）
# 纯接收：所有依赖（router/planner/识别技能/记忆/写回/介入跟踪器/纯静态助手）
# 由 DialogService 解析后注入——本类不持锁（事务边界归 DialogService 入口
# 统一持有）、不触实例缓存（缓存即权威归 DialogService，见 CONTEXT.md）。
# 阶段：AssembleDirectives → PreScanIntervene → CallPlanner →
#       WritebackLedger → AssembleResponse。
# LoadSessionContext / 游客闸门 / ResolveProvider 是入口阶段，留在
# DialogService（依赖 payload 归一与 ProviderResolver 注入缝）。


class DialogRun:
    """单次 dialog 主链的编排器。可独立单测：直接注入 fake 依赖即可跑通，
    不必构造 DialogService / HTTP 层。"""

    def __init__(self, router, planner, identify_skill, mem, wb, tracker,
                 normalize_user_level, writeback_ledger_events,
                 assistant_payload):
        self._router = router
        self._planner = planner
        self._identify_skill = identify_skill
        self._mem = mem
        self._wb = wb
        self._tracker = tracker
        # 纯静态助手（DialogService 稳定挂点，注入以保持解耦与可测性）
        self._normalize_user_level = normalize_user_level
        self._writeback_ledger_events = writeback_ledger_events
        self._assistant_payload = assistant_payload

    def run(self, ctx, provider):
        """执行主链（约定：调用方已持 DialogService._lock）。
        ctx：LoadSessionContext 产物；provider：ResolveProvider 产物
        （mock 注入路径为 None）。返回 (status, payload)。"""
        history = self._mem.to_llm_history(ctx["conversation_id"])
        directives = self._assemble_directives(
            ctx["native_lang"], ctx["learner_l1"], ctx["scene_id"])
        pre = self._pre_scan_intervene(
            ctx["user_input"], ctx["native_lang"],
            directives["level_label"], directives["level_int"],
            directives["cap"])
        res = self._planner.run(
            ctx["user_input"], history=history,
            learner_id=ctx["learner_id"],
            profile_summary=directives["profile_summary"],
            native_lang=ctx["native_lang"],
            user_level=directives["level_label"],
            scene_brief=directives["scene_brief"],
            persona_brief=directives["persona_brief"],
            style_directive=directives["style_directive"],
            intervention_directive=pre["intervention_directive"])
        wb_out = self._writeback_round(
            res, pre, ctx["user_input"], ctx["conversation_id"],
            history, provider, ctx["native_lang"])
        graph_snapshot = self._router.graph.graph_snapshot()
        out = self._build_dialog_response(
            ctx["learner_id"], ctx["conversation_id"], provider, res, pre,
            wb_out, directives["cap"], graph_snapshot)
        return 200, out

    # ---------- 阶段方法 ----------

    def _assemble_directives(self, native_lang, learner_l1, scene_id):
        """阶段 · AssembleDirectives：画像/persona/风格/场景/等级收集成 planner
        prompt 参数（各策略模块保持独立，由本阶段统一组装，不强行统一接口）。
        含 0.25 起点分层：画像等级归一并同步 Router（同步失败不阻断）。"""
        from engine.intervention import DEFAULT_CAP
        from engine.memory.summarize import build_profile_summary
        from engine.persona import build_persona_brief, build_tutor_style_directive
        # M8 画像注入：常错点/惯犯摘要进 system（含本轮前全部图谱+账本状态）
        try:
            profile_summary = build_profile_summary(
                self._router.graph, self._wb.ledger)
        except Exception:  # noqa: BLE001
            profile_summary = ""

        # ---- 0.22 方向3 · persona（个性化栏：风格/称呼/人设/自定义指令/打断上限）----
        profile_block = self._mem.get_profile() or {}
        persona = profile_block.get("persona") if isinstance(
            profile_block.get("persona"), dict) else {}
        persona_brief = build_persona_brief(persona, native_lang, learner_l1=learner_l1)
        # P0.16：tutor 措辞规范（[Style] 去 AI 味）——无条件全局注入（D1=A），
        # 独立于 persona 是否配置；与 [Persona] 人设层并列、正交。
        style_directive = build_tutor_style_directive(native_lang)
        try:
            cap = int((persona or {}).get("interrupt_cap", DEFAULT_CAP))
        except (TypeError, ValueError):
            cap = DEFAULT_CAP

        # ---- 0.25 起点分层：画像等级 → 识别/超纲/讲解全链生效 ----
        # 画像 user_level 未设 → HSK3（与技能默认一致）。归一用注入的纯助手
        # （不依赖 Router 具体实现，测试替身替出 Router 亦兼容）；同步 Router 仅当支持。
        level_int = 3
        raw_level = profile_block.get("user_level")
        _level_label = self._normalize_user_level(raw_level)
        if _level_label is not None:
            level_int = int(_level_label[3:])
            if hasattr(self._router, "set_level"):
                try:
                    self._router.set_level(_level_label)
                except Exception:  # noqa: BLE001 同步失败不阻断
                    pass

        # ---- 0.22 方向2 · 场景对话：scene_id → 编译 [Scene] 段注入主链（D2.2）。
        # 场景缺失/未命中 → scene_brief 空串，planner 照常走自由对话（不阻断）。----
        scene_brief = ""
        if scene_id:
            try:
                from engine.scenarios import build_scene_brief, get_scene
                _scene = get_scene(scene_id)
                if _scene:
                    scene_brief = build_scene_brief(_scene, native_lang)
            except Exception:  # noqa: BLE001 场景加载失败 → 退化为自由对话
                scene_brief = ""
        return {
            "profile_summary": profile_summary,
            "persona_brief": persona_brief,
            "style_directive": style_directive,
            "cap": cap,
            "level_int": level_int,
            "level_label": f"HSK{level_int}",
            "scene_brief": scene_brief,
        }

    def _pre_scan_intervene(self, user_input, native_lang, level_label,
                            level_int, cap):
        """阶段 · PreScanIntervene：介入判定（确定性分档，D3.4 主链重构）。
        预扫：每轮产出句先走 identify（喂图谱+迁移假设，句子→图谱链不变），
        再用组合信号分档（求助/空/含义不清/连续错率 + 打断上限）。
        求助句不预扫（meta 问题，识别交给 planner 按需调技能）；
        超长文本视为学习材料（交给 parse_document），不识别不介入。"""
        from engine.intervention import (
            MAX_SCAN_CHARS, build_intervention_directive, detect_help_intent)
        pre_scan = None
        scan_notices = []
        help_intent = detect_help_intent(user_input)
        too_long = len(user_input) > MAX_SCAN_CHARS
        if not help_intent and not too_long:
            try:
                pre_scan = self._identify_skill.run(
                    {"text": user_input, "native_lang": native_lang,
                     "level": level_label})
            except Exception as e:  # noqa: BLE001 预扫失败不阻断对话
                pre_scan = None
                scan_notices.append({"stage": "pre_scan",
                                     "reason": str(e), "fatal": False})
        max_conf, error_flag = 1.0, None
        if isinstance(pre_scan, dict):
            confs = [float(e.get("confidence") or 0.0)
                     for e in (pre_scan.get("errors") or [])
                     + (pre_scan.get("uncertain") or [])
                     if isinstance(e, dict)]
            max_conf = max(confs) if confs else 1.0
            error_flag = bool(pre_scan.get("errors")
                              or pre_scan.get("uncertain"))
            # 账本：预扫确认偏误 → observation_error（惯犯数据源不断档；
            # planner 重复 identify 同句由 skip_text 去重）
            for err in pre_scan.get("errors") or []:
                kp = err.get("knowledge_point_id") if isinstance(err, dict) else None
                if not kp:
                    continue
                try:
                    self._wb.ledger.record(
                        "observation_error", kp,
                        signature=err.get("fragment", ""),
                        evidence=user_input)
                except Exception as e:  # noqa: BLE001
                    scan_notices.append({"stage": "ledger_write",
                                         "reason": str(e), "fatal": False})
        if too_long:
            level, reason = "none", "material"   # 材料句：不介入不分档
        else:
            level, reason = self._tracker.observe(
                user_input, max_conf=max_conf,
                error_flag=error_flag, cap=cap)
        intervention_directive = build_intervention_directive(
            level, reason, native_lang, recognition=pre_scan,
            hsk_level=level_int)
        return {
            "pre_scan": pre_scan,
            "scan_notices": scan_notices,
            "level": level,
            "reason": reason,
            "intervention_directive": intervention_directive,
        }

    def _writeback_round(self, res, pre, user_input, conversation_id,
                         history, provider, native_lang):
        """阶段 · WritebackLedger：planner 回来后的写回——M8 两段式账本事件 +
        画像 facts 写回记忆 + M5 记忆追加（user 必记/assistant 非空才记）+
        会话标题（首句）+ 0.27 成果卡 metadata。失败降级为 notices，不阻断对话。"""
        from engine.memory.summarize import build_profile_facts
        pre_scan = pre["pre_scan"]
        reason = pre["reason"]
        # M8 两段式·账本侧：识别命中→observation_error；复述 pass→concept_confirmed
        m8_notices = pre["scan_notices"] + self._writeback_ledger_events(
            self._wb, res.get("trace", []), user_input,
            skip_text=(user_input if isinstance(pre_scan, dict) else ""))
        # M8 汇合 M5：常错点结构化 facts 写回长期记忆 profile 块
        try:
            facts = build_profile_facts(self._router.graph, self._wb.ledger)
            if facts:
                self._mem.update_profile(common_errors=facts)
        except Exception as e:  # noqa: BLE001
            m8_notices.append({"stage": "profile_writeback",
                               "reason": str(e), "fatal": False})
        # 写回记忆：user 必记；assistant 回复非空才记（fallback 文案也记，多轮不断档）
        reply = str(res.get("text") or "")
        self._mem.append(conversation_id, "user", user_input)
        # 会话标题（0.19 前端 v2）：首条消息自动设为标题（取自首句提问，便于侧栏回访识别）
        if not history:
            try:
                self._mem.touch(conversation_id, title=user_input[:18])
            except Exception:  # noqa: BLE001
                pass
        why_items = []
        if reply:
            # 0.27 · 成果卡随会话持久：cards(卡视图)+why 写进 assistant 消息 metadata，
            # 恢复会话时前端据以原位重建。仅新会话生效（旧记录无此字段）。
            meta = self._assistant_payload(res, pre_scan, provider,
                                           native_lang, reason)
            why_items = meta["why"]
            self._mem.append(conversation_id, "assistant", reply,
                             metadata={"skills": res.get("used_skills", []),
                                       "fallback": bool(res.get("fallback", False)),
                                       **meta})
        return {"notices": m8_notices, "why_items": why_items, "reply": reply}

    def _build_dialog_response(self, learner_id, conversation_id, provider,
                               res, pre, wb_out, cap, graph_snapshot):
        """阶段 · AssembleResponse：组装对外响应（13 键契约）。
        degraded = 账本/画像写入降级 notices；fallback 时追加 planner 通知（非致命）。"""
        degraded = wb_out["notices"]
        if res.get("fallback"):
            degraded.append({"stage": "planner",
                             "reason": f"planner fallback: {res.get('reason', 'unterminated')}",
                             "fatal": False})
        return {
            "dialog_version": "v1",
            "learner_id": learner_id,
            "conversation_id": conversation_id,
            "provider": ({"id": provider["id"], "name": provider["name"],
                          "model": provider["model"]} if provider else None),
            "text": res.get("text", ""),
            "used_skills": res.get("used_skills", []),
            "steps": res.get("steps", 0),
            "fallback": bool(res.get("fallback", False)),
            "trace": res.get("trace", []),
            "intervention": {"level": pre["level"], "reason": pre["reason"],
                             "used": self._tracker.interrupt_used, "cap": cap},
            "why": wb_out["why_items"],
            "degraded": degraded,
            "graph": graph_snapshot,
        }
