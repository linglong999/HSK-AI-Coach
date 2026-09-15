# ============================================================
# 前端壳 HTTP 服务层（M10）+ Controller（0.30 拆分第 1 步）
# 零运行时依赖：仅 Python 标准库 http.server。
# 分层：本层只做 HTTP 职责——路由、JSON/静态读写、Cookie（游客 vid）、异常包装；
#   6 个改数据端点的业务编排（process/verify/generate/dialog/session）在
#   engine/dialog_service.py（DialogService，与 H 共享同一把 RLock）。
# 暴露入口给前端壳（原生 JS+SVG）：
#   POST /api/dialog   → Planner 自由 ReAct（0.17 唯一对话入口，共享 skill 注册表）
#                        + M5 多轮记忆（0.18 接入项①）：服务端 LearnerMemory 权威，
#                        按 conversation_id 注入历史并写回落盘（0.31 起 SQLite：
#                        data/coach.db 的 sessions/messages/profiles 表）
#                        + M8 画像/惯犯（0.18 接入项③）：常错点摘要注入 system；
#                        trace 编排 ledger 两段式事件（coach.db · ledger_events 表）；
#                        常错点 facts 汇合 M5 profile.common_errors
#   POST /api/process  → Router.process(text) → 契约 v1 result（兼容遗留 runner）
#   POST /api/verify   → Router.verify_rephrase() → 复述验证（契约 verify_rephrase 节）
#   POST /api/generate → GenerationEngine.generate_unit()（费曼单元，0.16 接入，共享 graph）
#   GET  /api/graph    → ErrorGraph.graph_snapshot()（nodes/edges/queue）
#   GET  /api/profile  → 学习者画像 + 会话列表（0.19 前端 v2：AI 学习报告/侧栏数据源，只读）
#   POST /api/profile  → 保存个性化 persona（0.22 方向3：reply_style/address/identity/自定义指令/打断上限）
#   GET  /api/conversation?id= → 单会话消息历史（0.19：URL 深链恢复会话，只读）
#   POST /api/session/update   → 会话置顶/重命名（0.24 右键菜单，bump=False 不动活跃度排序）
#   POST /api/session/delete   → 删除会话（0.24 右键菜单）
#   GET  /api/providers → BYOK 供应商列表（掩码，0.20）
#   POST /api/providers → 添加供应商（0.20 BYOK：OpenAI 兼容，UI 内配置免重启）
#   POST /api/providers/test → 连通性测试（最小 chat 请求，不落盘）
#   POST /api/providers/default → 切换默认供应商
#   DEL  /api/providers?id= → 删除供应商（env 虚拟供应商不可删）
#   GET  /             → 托管 前端壳 index.html
# 启动: python -m engine.serve [--port 8612] [--host 127.0.0.1] [--learner demo]
# 免 Key 亦可启动：process 走 4.2 规则回退降级；图谱照常返回。
# /api/dialog 需 LLM Key（A3 决策：无 Key 直接报错，不静默降级）；generate 无 Key 结构化 degraded。
# ============================================================

from typing import Optional
import argparse
import base64
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from engine.visitor_gate import VisitorGate   # P0.10 游客配额闸门

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from engine import http_util
from engine.dialog_service import DialogService
from engine.router import Router

# 前端壳静态目录：serve.py 位于 engine/，前端壳在项目根前端/ 或 web/
_INDEX_DIR = os.path.join(_PROJECT_ROOT, "web")


def _alignment_summary() -> dict:
    """P0.19 fe5：教材对齐 + 考纲概览（textbook_map × syllabus 真实数据，只读）。
    暴露给前端"效果度量"面板顶部信息块：教材元信息、单元数、已映射/未映射考纲点。
    任何数据缺失 → 对应字段为空，不阻断。"""
    from engine.textbook_map import load_map, active_book, units_of
    from engine.syllabus import SyllabusDatabase as Syllabus

    d = load_map()
    book_key = active_book(d)
    units = units_of(book_key, d)
    book_meta = (d.get("books") or {}).get(book_key, {})
    n_units = len(units)
    # 已映射 = 教材单元里出现的 kp 考纲 id 去重数；需先统计"被哪些单元覆盖"
    covered = set()
    for u in units:
        for rid in (u.get("kp_ids") or []):
            covered.add(str(rid))
    try:
        syl = Syllabus.load()
        n_points = len(syl.all())
    except Exception:  # noqa: BLE001
        n_points = d.get("count")
    return {
        "active_book": book_key or "",
        "book_title": book_meta.get("title", ""),
        "version": d.get("version", ""),
        "status": d.get("status", ""),
        "note": d.get("note", ""),
        "units": n_units,
        "syllabus_points": n_points,
        "mapped_points": len(covered),
        "unmapped_points": (n_points - len(covered)) if isinstance(n_points, int) else None,
        "cover_ratio": round(len(covered) / n_points, 3) if n_points else None,
    }


def make_handler(router: "Router", index_dir: str, generation=None, dialog_llm=None,
                 memory_root: str = "data",
                 gate=None):
    """工厂构造 handler，闭包捕获 Router（每个请求共享同一图谱实例）。
    ThreadingHTTPServer 每请求一线程 → 用一把锁串行化整个闭环
    （process/verify 是多步读写组合，仅靠图谱内部锁防不住交错）。
    0.30 拆分第 1 步：锁与共享实例缓存归 DialogService（业务编排层），
    H 与 service 共享同一把 RLock，HTTP 层经 _svc 调用业务入口。
    generation: 可选 GenerationEngine；默认 None 懒建（共享 router.graph + Writeback）。
    dialog_llm: 可选 planner llm_call 注入（测试 mock；None → 真实 LLMClient.chat）。
    memory_root: LearnerMemory 落盘根目录（默认 data/；测试注入临时目录）。
    gate: 可选 VisitorGate（P0.10 游客每日配额）；None → 闸门关闭（测试默认豁免）。"""

    lock = threading.RLock()
    svc = DialogService(router=router, lock=lock, generation=generation,
                        dialog_llm=dialog_llm, memory_root=memory_root, gate=gate)

    class H(BaseHTTPRequestHandler):
        _router = router
        _index_dir = index_dir
        _lock = lock
        _svc = svc            # 对话运营编排（0.30 拆分：业务与 HTTP 分层）
        _memory_root = memory_root   # providers/metrics 等遗留 handler 直用

        # POST 路由表：path → (handler 方法名, 错误文案名)。统一异常包装用，
        # 每端点 500 文案原样保留（"<错误文案名> failed: {e}"）。
        _POST_API = {
            "/api/process": ("_do_api_process", "process"),
            "/api/quiz/answer": ("_do_api_quiz_answer", "quiz answer"),
            "/api/onboard/assess": ("_do_api_onboard_assess", "onboard assess"),
            "/api/ocr": ("_do_api_ocr", "ocr"),
            "/api/feedback": ("_do_api_feedback", "feedback"),
            "/api/verify": ("_do_api_verify", "verify"),
            "/api/generate": ("_do_api_generate", "generate"),
            "/api/dialog": ("_do_api_dialog", "dialog"),
            "/api/profile": ("_do_api_profile_save", "profile save"),
            "/api/session/update": ("_do_api_session_update", "session update"),
            "/api/session/delete": ("_do_api_session_delete", "session delete"),
            "/api/providers": ("_do_api_providers_add", "providers add"),
            "/api/providers/test": ("_do_api_providers_test", "providers test"),
            "/api/providers/default": ("_do_api_providers_default", "providers default"),
        }
        # 这些 POST 端点接收 JSON body（其余 404）。
        _JSON_POST_PATHS = frozenset(_POST_API)

        # GET 路由表：path → handler 方法名（统一签名 (parsed)，异常由各 handler
        # 自行结构化降级——本表内端点的失败响应形态互不相同，不做统一 500 包装）。
        _GET_API = {
            "/api/graph": "_do_api_graph",
            "/api/profile": "_do_api_profile",
            "/api/conversation": "_do_api_conversation",
            "/api/providers": "_do_api_providers_list",
            "/api/scenarios": "_do_api_scenarios",
            "/api/quiz": "_do_api_quiz",
            "/api/metrics": "_do_api_metrics",
            "/api/alignment": "_do_api_alignment",
        }

        def log_message(self, fmt, *args):
            sys.stderr.write("  [serve] " + fmt % args + "\n")

        def _send_json(self, obj, status=200):
            # 经 http_util 写 JSON（CORS/游客 vid cookie 收敛在该处）
            http_util.send_json(self, obj, status=status)

        def _send_static(self, rel: str):
            # 经 http_util 静态托管（含路径穿越防护 + no-store）
            http_util.send_static(self, self._index_dir, rel)

        # ---------- 6 个改数据端点：薄委托 DialogService（0.30 拆分第 1 步） ----------

        def _do_api_process(self, payload: dict):
            status, out = self._svc.process(payload)
            self._send_json(out, status=status)

        def _do_api_verify(self, payload: dict):
            status, out = self._svc.verify(payload)
            self._send_json(out, status=status)

        def _do_api_generate(self, payload: dict):
            status, out = self._svc.generate(payload)
            self._send_json(out, status=status)

        def _do_api_dialog(self, payload: dict):
            # 唯一对话入口（业务编排全在 DialogService.dialog）：
            # vid（P0.10 游客配额）是唯一需 HTTP 上下文的前置——Cookie 读写
            # 依赖 handler；owner-key 判定在 HTTP 层，配额扣减在服务层。
            vid = self._extract_vid(payload)
            status, out = self._svc.dialog(payload, vid=vid)
            self._send_json(out, status=status)

        def _extract_vid(self, payload: dict) -> Optional[str]:
            """P0.10 · 游客 vid 提取（HTTP 层职责：Cookie 由 _send_json 冲刷）。
            判定口径：未带供应商 id、或 id == 环境默认供应商（"env"）＝走 owner Key
            → 计入游客配额；显式 BYOK（带非 env 供应商 id）与闸门关闭完全豁免。"""
            gate = self._svc.gate
            if gate is None:
                return None
            provider_id = str((payload.get("provider_id") or "")).strip()
            from engine import providers as _prov
            uses_owner_key = (not provider_id) or (provider_id == _prov.ENV_PROVIDER_ID)
            if not uses_owner_key:
                return None
            return gate.ensure_vid(self)

        def _do_api_session_update(self, payload: dict):
            # 会话置顶/重命名（业务编排在 DialogService.session_update）
            status, out = self._svc.session_update(payload)
            self._send_json(out, status=status)

        def _do_api_session_delete(self, payload: dict):
            # 删除会话（业务编排在 DialogService.session_delete）
            status, out = self._svc.session_delete(payload)
            self._send_json(out, status=status)

        def do_POST(self):
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/")
            if path not in self.__class__._JSON_POST_PATHS:
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length else b"{}"
                payload = json.loads(raw or b"{}")
            except Exception:
                self._send_json({"error": "invalid json body"}, 400)
                return
            handler_name, err_name = self.__class__._POST_API[path]
            try:
                getattr(self, handler_name)(payload)
            except Exception as e:
                if path == "/api/generate":
                    # 生成引擎内部已结构化为 degraded；此处兜底防 HTTP 500
                    self._send_json({
                        "ok": False, "status": "degraded",
                        "message": f"generate failed: {e}",
                        "unit": None, "diagnostics": [], "attempts": 0}, 500)
                    return
                self._send_json({"error": f"{err_name} failed: {e}"}, 500)

        def do_DELETE(self):
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/")
            if path == "/api/providers":
                try:
                    self._do_api_providers_delete(parsed)
                except Exception as e:
                    self._send_json({"error": f"providers delete failed: {e}"}, 500)
                return
            self.send_error(404)

        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/")
            handler_name = self.__class__._GET_API.get(path)
            if handler_name:
                getattr(self, handler_name)(parsed)
                return
            # 静态：根 → index.html
            rel = parsed.path or "/"
            if rel == "/" or rel == "":
                rel = "/index.html"
            self._send_static(rel)

        def _do_api_graph(self, parsed):
            with self._lock:
                snap = self._router.graph.graph_snapshot()
            self._send_json(snap)

        def _do_api_scenarios(self, parsed):
            # 0.22 方向2 · 预置场景库（前端场景卡列表，D2.3）
            try:
                from engine.scenarios import list_scene_cards
                self._send_json({"scenes": list_scene_cards(),
                                 "count": len(list_scene_cards())})
            except Exception as e:  # noqa: BLE001 场景库不可用 → 空列表不报错
                self._send_json({"scenes": [], "count": 0, "error": str(e)})

        def _do_api_metrics(self, parsed):
            # P0.18 效果度量：聚合全部 learner 的 4 指标（实时算，不靠手工跑）
            try:
                from engine.metrics import all_metrics
                self._send_json(all_metrics(self._memory_root))
            except Exception as e:  # noqa: BLE001
                self._send_json({"learners": {}, "generated_at": 0,
                                 "error": f"metrics failed: {e}"}, 500)

        def _do_api_alignment(self, parsed):
            # P0.19 fe5：教材对齐 + 考纲审核概览（textbook_map × syllabus 真实数据）
            try:
                self._send_json(_alignment_summary())
            except Exception as e:  # noqa: BLE001
                self._send_json({"error": f"alignment failed: {e}"}, 500)

        def _do_api_quiz(self, parsed):
            """P0.13 独立复习页 GET：拉复习队列 → 用确定性挖空引擎造题。
            队列里的 kp：有例句能挖到空 → cloze；否则降级为 recall 记忆自检。
            （对齐：无例句点造题时降级处理）返回 {queue, questions}。"""
            try:
                qs = parse_qs(parsed.query)
                limit = int((qs.get("limit") or ["10"])[0])
                with self._lock:
                    queue = self._router.graph.get_review_queue()
                    sub = queue[:limit]
                    from engine.quiz import build_review_items
                    questions = build_review_items(sub)
                self._send_json({
                    "queue": sub,
                    "count": len(questions),
                    "questions": questions,
                })
            except Exception as e:  # noqa: BLE001 造题失败 → 空队列 500，前端自降级
                self._send_json({"errors": [], "queue": [],
                                 "error": f"quiz failed: {e}"}, 500)

        def _do_api_quiz_answer(self, payload):
            """P0.13 复习页答完回写：接收 [{kp_id, correct}]，逐 kp review_feedback。
            多知识点回写：每次提交可含多个 kp，逐个调度（FSRS + 化石化）。
            rating：correct=True→3(想起) / False→1(忘记)。"""
            answers = payload.get("answers") or payload.get("items") or []
            results = []
            with self._lock:
                for a in answers:
                    kp_id = a.get("kp_id")
                    correct = a.get("correct")
                    event_key = a.get("event_key", "")
                    if not kp_id or correct is None:
                        results.append({"kp_id": kp_id, "status": "bad_request"})
                        continue
                    try:
                        res = self._router.graph.review_feedback(
                            kp_id, rating=(3 if correct else 1),
                            event_key=event_key)
                        results.append({"kp_id": kp_id, "status": res.get("status"),
                                        "interval_days": res.get("interval_days")})
                    except Exception as e:  # noqa: BLE001
                        results.append({"kp_id": kp_id, "status": "error", "msg": str(e)})
            self._send_json({"results": results, "count": len(results)})

        def _do_api_profile(self, parsed):
            """只读画像 + 会话列表（0.19 前端 v2：AI 学习报告 / 侧栏数据源）。
            - common_errors：live facts（graph×ledger 实时汇合，与每轮写回同源）
            - profile：落盘画像原块（level/native_lang 及历史 common_errors 兜底）
            - sessions：按 updated_at 降序，仅元信息不含 messages（防载荷膨胀）
            - stats：图谱/复习/惯犯计数（报告卡头部数据）"""
            qs = parse_qs(parsed.query)
            learner_id = (qs.get("learner") or [""])[0].strip() or \
                self._router.learner_id
            with self._lock:
                from engine.memory.summarize import (
                    build_profile_summary, build_profile_facts)
                mem = self._svc.get_memory(learner_id)
                wb = self._svc.get_writeback(learner_id)
                profile = mem.get_profile()
                try:
                    facts = build_profile_facts(self._router.graph, wb.ledger)
                except Exception:  # noqa: BLE001
                    facts = []
                try:
                    summary_text = build_profile_summary(
                        self._router.graph, wb.ledger)
                except Exception:  # noqa: BLE001
                    summary_text = ""
                sessions = mem.list_sessions()
                # 0.24：置顶优先，组内仍按 updated_at 降序
                sessions.sort(key=lambda s: (not s["pinned"], -s["updated_at"]))
                snap = self._router.graph.graph_snapshot()
                queue = snap.get("queue", [])
                try:
                    offenders = [f["knowledge_point"] for f in facts
                                 if f.get("repeat_offender")]
                except Exception:  # noqa: BLE001
                    offenders = []
                # ---- P0.12 冷启动引导判定 ----
                # 全新用户（未完成引导 且 无任何学习数据）→ 弹引导；老用户不受影响
                onboarding_done = bool(profile.get("onboarding_done"))
                has_learning_data = bool(sessions) or bool(snap.get("nodes", {}))
                self._send_json({
                    "learner_id": learner_id,
                    "profile": profile,
                    "onboarding_needed": (not onboarding_done) and (not has_learning_data),
                    "common_errors": facts,
                    "summary_text": summary_text,
                    "sessions": sessions,
                    "stats": {
                        "graph_nodes": len(snap.get("nodes", {})),
                        "graph_edges": len(snap.get("edges", [])),
                        "review_count": len(queue),
                        "repeat_offenders": offenders,
                    },
                })

        def _do_api_conversation(self, parsed):
            """只读单会话消息历史（0.19：URL 深链 ?conversation=<id> 恢复会话）。
            透传 user/assistant 的 {role, content}；assistant 消息额外白名单透传 cards/why
            （0.27 成果卡随会话持久），其余 metadata 内部项一律不暴露。"""
            qs = parse_qs(parsed.query)
            conversation_id = ((qs.get("id") or [""])[0] or "default").strip()
            learner_id = (qs.get("learner") or [""])[0].strip() or \
                self._router.learner_id
            with self._lock:
                mem = self._svc.get_memory(learner_id)
                msgs = mem.get_history(conversation_id, window=200)
            visible = []
            for m in msgs:
                if m.get("role") not in ("user", "assistant"):
                    continue
                item = {"role": m.get("role"), "content": m.get("content", "")}
                if m.get("role") == "assistant":
                    md = (m.get("metadata") or {}) or {}
                    if md.get("cards"):
                        item["cards"] = md["cards"]
                    if md.get("why"):
                        item["why"] = md["why"]
                visible.append(item)
            self._send_json({
                "conversation_id": conversation_id,
                "messages": visible,
            })

        def _do_api_profile_save(self, payload):
            """0.22 方向3 · 保存个性化 persona；0.25 · 扩展支持顶层 user_level（起点分层）。
            - persona：normalize_persona 校验/清洗（白名单 + 截长），非法 reply_style → 400
            - user_level：统一存 'HSK{n}'（1-6），非法值 400
            - 部分合并：只更新给出的字段，其余沿用现值；至少给一个字段"""
            learner_id = str(payload.get("learner") or "").strip() or \
                self._router.learner_id
            raw = payload.get("persona")
            raw_level = payload.get("user_level")
            raw_l1 = payload.get("learner_l1")
            raw_uilang = payload.get("ui_lang") or payload.get("native_lang")
            raw_onboard = payload.get("onboarding_done")
            if (raw is None and raw_level is None and raw_l1 is None
                    and raw_uilang is None and raw_onboard is None):
                self._send_json({"error": "无可更新字段"
                                            "（persona/user_level/learner_l1/ui_lang/onboarding_done）",
                                 "code": "nothing_to_update"}, 400)
                return
            with self._lock:
                mem = self._svc.get_memory(learner_id)
                result = {"ok": True}
                if raw is not None:
                    if not isinstance(raw, dict):
                        self._send_json({"error": "persona 应为对象",
                                         "code": "invalid_persona"}, 400)
                        return
                    from engine.persona import normalize_persona
                    current = mem.get_profile().get("persona")
                    try:
                        normalized = normalize_persona(
                            raw, current=current if isinstance(current, dict) else None)
                    except ValueError as e:
                        self._send_json({"error": str(e),
                                         "code": "invalid_persona"}, 400)
                        return
                    mem.update_profile(persona=normalized)
                    result["persona"] = normalized
                if raw_level is not None:
                    label = DialogService._normalize_user_level(raw_level)
                    if label is None:
                        self._send_json(
                            {"error": "user_level 应为 'HSK1'-'HSK6' 或数字 1-6",
                             "code": "invalid_user_level"}, 400)
                        return
                    mem.update_profile(user_level=label)
                    if hasattr(self._router, "set_level"):
                        try:
                            self._router.set_level(label)   # 起点分层即时生效
                        except Exception:  # noqa: BLE001 同步失败不阻断
                            pass
                    result["user_level"] = label
                # ---- P0.12 冷启动引导落库：learner_l1 / ui_lang / onboarding_done ----
                if raw_l1 is not None:
                    s = str(raw_l1).strip().lower()
                    if not s or len(s) > 16:
                        self._send_json({"error": "learner_l1 应为非空语言代码",
                                         "code": "invalid_learner_l1"}, 400)
                        return
                    mem.update_profile(learner_l1=s)
                    result["learner_l1"] = s
                if raw_uilang is not None:
                    s = str(raw_uilang).strip().lower()
                    if not s or len(s) > 16:
                        self._send_json({"error": "ui_lang 应为非空语言代码",
                                         "code": "invalid_ui_lang"}, 400)
                        return
                    mem.update_profile(native_lang=s)
                    result["native_lang"] = s
                if raw_onboard is not None:
                    mem.update_profile(onboarding_done=bool(raw_onboard))
                    result["onboarding_done"] = bool(raw_onboard)
            self._send_json(result)

        def _do_api_onboard_assess(self, payload):
            """P0.12 · 冷启动摸底定级（建议，可改，落库由完成端点管）。
            对引导里填的 1-4 句跑识别预扫：句内识别到确认层偏误记为偏误句。
            保守启发式：偏误率高 → 建议下调起点等级；3 句太少不自动上调（防高估）。
            复用 DialogService 的识别技能构造口径（确定性规则，无 LLM 依赖 → 无 Key 也能摸底）。"""
            learner_id = str(payload.get("learner") or "").strip() or \
                self._router.learner_id
            texts = payload.get("texts")
            if not isinstance(texts, list) or not 1 <= len(texts) <= 4:
                self._send_json({"error": "texts 应为 1-4 条句子",
                                 "code": "invalid_texts"}, 400)
                return
            texts = [str(t).strip() for t in texts]
            texts = [t for t in texts if t]
            if not texts:
                self._send_json({"error": "texts 不能全为空",
                                 "code": "invalid_texts"}, 400)
                return
            with self._lock:
                mem = self._svc.get_memory(learner_id)
                prof = mem.get_profile()
                raw_level = payload.get("user_level") or prof.get("user_level")
                base = DialogService._normalize_user_level(raw_level)
                base = 3 if base is None else int(base[3:])
                # 只读预扫：构造无 graph 的识别器（不写图谱），并把真实母语喂给迁移规则
                from skills.identify_errors import IdentifyErrorsSkill
                identify = IdentifyErrorsSkill(
                    recognizer=getattr(self._router, "recognizer", None),
                    graph=None)
                learner_l1 = str(prof.get("learner_l1") or "").strip()
                err_sentences, total = 0, 0
                failures = 0
                for t in texts:
                    total += 1
                    try:
                        pre = identify.run({"text": t, "level": f"HSK{base}",
                                            "native_lang": learner_l1})
                        if isinstance(pre, dict) and pre.get("errors"):
                            err_sentences += 1
                    except Exception:  # noqa: BLE001 单句失败不阻断其余
                        failures += 1
            # 偏误率高 → 下调；过低/无错 → 维持（冷启动样本小，宁低位慎重）
            if total == 0:
                suggest = base
            elif failures == total:
                suggest = base          # 全判定失败 → 回退基线，不硬降
            else:
                er = err_sentences / total
                if er >= 0.8 and base > 2:
                    suggest = base - 2
                elif er >= 0.6 and base > 1:
                    suggest = base - 1
                else:
                    suggest = base
                suggest = max(1, min(6, suggest))
            reason = (f"摸底 {total} 句，检测到 {err_sentences} 句含偏误"
                      f"（{round(err_sentences * 100 / total)}%）。"
                      + (f"建议先定在 HSK{suggest}（看基础弱，先降档更踏实；之后可随时调整）。"
                         if suggest < base
                         else f"未检出明显偏误，建议维持 HSK{suggest}（3 句样本少，不急于跳级）。")
                      if total else "无有效句子，未给出定级建议。")
            if failures:
                reason += (f"（{failures} 句判定失败，按剩余句子计）")
            self._send_json({
                "learner_id": learner_id,
                "suggested_level": f"HSK{suggest}",
                "base_level": f"HSK{base}",
                "error_sentence_count": err_sentences,
                "total": total,
                "reason": reason,
            })

        def _do_api_ocr(self, payload):
            """P0.7 OCR：图片/PDF（base64）→ 干净文本。仅识字层，识别交给 recognizer。
            入参：data=base64 字节码 / {data, name}；或 list=files[{data,name}]。
            name 以 .pdf 结尾 → 走 PDF→图→OCR；否则按图片处理。
            RapidOCR 缺失 → 优雅降级为 422（前端提示"不支持图片"）。"""
            from engine.ocr import (OcrUnavailable, extract_text_from_images,
                                    extract_text_from_pdf)
            files = payload.get("list")
            if files is None:
                if not isinstance(payload.get("data"), str):
                    self._send_json({"error": "data 应传 base64 字符串",
                                     "code": "invalid_data"}, 400)
                    return
                files = [payload]
            if not isinstance(files, list) or not files:
                self._send_json({"error": "files 应为非空列表", "code": "invalid_files"}, 400)
                return
            pdf_buf, img_bytes = b"", []
            for f in files:
                try:
                    raw = base64.b64decode(str(f.get("data", "")), validate=False)
                except Exception as e:  # noqa: BLE001
                    self._send_json({"error": f"base64 解码失败：{e}",
                                     "code": "invalid_data"}, 400)
                    return
                if str(f.get("name") or "").lower().endswith(".pdf"):
                    pdf_buf = raw
                else:
                    img_bytes.append(raw)
            start_ms = int(round(time.time() * 1000))
            try:
                text_parts = []
                if img_bytes:
                    text_parts.append(extract_text_from_images(img_bytes))
                if pdf_buf:
                    text_parts.append(extract_text_from_pdf(pdf_buf))
            except OcrUnavailable as e:
                self._send_json({"error": str(e), "code": "ocr_unavailable",
                                 "message": "不支持图片/PDF（OCR 能力未安装，可先粘贴文本）"},
                                422)
                return
            text = "\n\n".join(p for p in text_parts if p)
            elapsed_ms = int(round(time.time() * 1000)) - start_ms
            self._send_json({
                "text": text,
                "chars": len(text),
                "files": len(files),
                "elapsed_ms": elapsed_ms,
            })

        def _do_api_feedback(self, payload):
            """P0.18 评分入口落盘：对话结束 1–5 星可选提交 → feedback_<learner>.json。
            learner 沿用 router.learner_id（与 dialog 一致）；评分越界/非数由
            record_feedback 拒绝并回 400。"""
            from engine.metrics import record_feedback
            learner = str(payload.get("learner") or "").strip() or \
                self._router.learner_id
            ok = record_feedback(
                root=self._memory_root,
                learner=learner,
                score=payload.get("score"),
                conversation_id=payload.get("conversation_id", ""),
                comment=payload.get("comment", ""),
            )
            if not ok:
                self._send_json({"error": "invalid score (1-5 required)",
                                 "code": "invalid_score"}, 400)
                return
            self._send_json({"ok": True, "learner": learner})

        # ---------- BYOK 供应商管理（0.20：OpenAI 兼容多供应商，UI 内配置免重启） ----------

        def _do_api_providers_list(self, parsed):
            """供应商列表（api_key 掩码，响应绝不含完整 Key）。
            default_id = 实际生效默认（store 显式默认 → env → 首个）。"""
            from engine import providers as prov
            with self._lock:
                plist = [prov.masked(p)
                         for p in prov.effective_providers(self._memory_root)]
                default = prov.resolve_provider(self._memory_root, None)
            self._send_json({
                "providers": plist,
                "default_id": default["id"] if default else None,
            })

        def _do_api_providers_add(self, payload):
            from engine import providers as prov
            name = str(payload.get("name") or "").strip()
            base_url = str(payload.get("base_url") or "").strip()
            api_key = str(payload.get("api_key") or "").strip()
            model = str(payload.get("model") or "").strip()
            if not (name and base_url and api_key and model):
                self._send_json({"error": "name/base_url/api_key/model 均为必填"}, 400)
                return
            with self._lock:
                store = prov.load_store(self._memory_root)
                p = prov.new_provider(name, base_url, api_key, model)
                store["providers"].append(p)
                # 无 env 默认且未设显式默认 → 首个添加者自动成为默认
                if not store.get("default_id") and not prov.env_provider():
                    store["default_id"] = p["id"]
                prov.save_store(self._memory_root, store)
            self._send_json({"ok": True, "provider": prov.masked(p)})

        def _do_api_providers_test(self, payload):
            """连通性测试：支持按 id（已存供应商/env）或直接传字段（先测后存）。"""
            from engine import providers as prov
            pid = str(payload.get("id") or "").strip()
            if pid:
                with self._lock:
                    provider = next(
                        (p for p in prov.effective_providers(self._memory_root)
                         if p["id"] == pid), None)
                if provider is None:
                    self._send_json({"error": "未知供应商 id"}, 404)
                    return
            else:
                base_url = str(payload.get("base_url") or "").strip()
                api_key = str(payload.get("api_key") or "").strip()
                model = str(payload.get("model") or "").strip()
                if not (base_url and api_key and model):
                    self._send_json(
                        {"error": "需提供 id，或 base_url/api_key/model 三字段"}, 400)
                    return
                provider = {"base_url": base_url, "api_key": api_key,
                            "model": model}
            # 测试调用在锁外：网络 IO 不持有服务锁
            result = prov.test_provider(provider["base_url"],
                                        provider["api_key"], provider["model"])
            self._send_json(result)

        def _do_api_providers_default(self, payload):
            from engine import providers as prov
            pid = str(payload.get("id") or "").strip()
            with self._lock:
                store = prov.load_store(self._memory_root)
                if pid == prov.ENV_PROVIDER_ID:
                    store["default_id"] = None   # 回落 env 默认
                elif any(p["id"] == pid for p in store["providers"]):
                    store["default_id"] = pid
                else:
                    self._send_json({"error": "未知供应商 id"}, 404)
                    return
                prov.save_store(self._memory_root, store)
            self._send_json({"ok": True, "default_id": pid})

        def _do_api_providers_delete(self, parsed):
            from engine import providers as prov
            qs = parse_qs(parsed.query)
            pid = (qs.get("id") or [""])[0].strip()
            if not pid:
                self._send_json({"error": "缺少 id 参数"}, 400)
                return
            with self._lock:
                store = prov.load_store(self._memory_root)
                before = len(store["providers"])
                store["providers"] = [p for p in store["providers"]
                                      if p["id"] != pid]
                if len(store["providers"]) == before:
                    self._send_json(
                        {"error": "未知供应商（.env 供应商不可删除，请编辑 .env）"}, 404)
                    return
                if store.get("default_id") == pid:
                    store["default_id"] = None
                prov.save_store(self._memory_root, store)
            self._send_json({"ok": True})

    return H


def main():
    ap = argparse.ArgumentParser(description="HSK-AI-Coach 前端壳服务（零依赖）")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8612)))
    ap.add_argument("--learner", default="demo", help="学习者 id → data/graph_<id>.json")
    ap.add_argument("--lang", default="en", help="讲解/纠错语言：en(英壳+中例句,默认) 或 zh(全中文)")
    args = ap.parse_args()

    router = Router(learner_id=args.learner, native_lang=args.lang, user_level="HSK3")
    if not os.path.isdir(_INDEX_DIR):
        print(f"[serve] 前端壳目录不存在：{_INDEX_DIR}")
        print("[serve] 将仅提供 API（/api/process /api/graph），静态页待 M10 生成 web/index.html")
    httpd = ThreadingHTTPServer((args.host, args.port),
                                make_handler(router, _INDEX_DIR,
                                             gate=VisitorGate(root="data")))
    print(f"HSK-AI-Coach 前端壳：http://{args.host}:{args.port}  (learner={args.learner})")
    print("  POST /api/dialog   自由对话（planner 唯一入口，需 LLM Key）")
    print("                      入参 text / learner_id / conversation_id")
    print("                      多轮记忆服务端持久化：同 conversation_id 延续上下文")
    print("  POST /api/process   纠错闭环（契约 v1 JSON）")
    print("  POST /api/verify    复述验证（key_points + restatement）")
    print("  POST /api/generate  费曼讲解/练习单元（unit_type=explain|practice）")
    print("  GET  /api/graph     图谱 nodes/edges/queue")
    print("  GET  /api/profile   学习者画像 + 会话列表（AI 学习报告/侧栏，只读）")
    print("  GET  /api/conversation?id=  单会话消息历史（URL 深链恢复，只读）")
    print("  POST /api/session/update   会话置顶/重命名（右键菜单）")
    print("  POST /api/session/delete   删除会话（右键菜单）")
    print("  GET  /              前端壳页面（若 web/index.html 存在）")
    print("  Ctrl+C 停止")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[serve] 停止")
        httpd.server_close()


if __name__ == "__main__":
    main()
