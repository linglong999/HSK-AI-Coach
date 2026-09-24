# ============================================================
# 前端壳 HTTP 服务层（M10）+ Controller（0.30 拆分第 1 步）
# M0 迁移：BaseHTTPRequestHandler → FastAPI（uvicorn），只换传输层，契约逐字保留。
# 分层：本层只做 HTTP 职责——路由、JSON/静态读写、Cookie（游客 vid）、异常包装；
#   6 个改数据端点的业务编排（process/verify/generate/dialog/session）在
#   engine/dialog_service.py（DialogService，与 create_app 共享同一把 RLock）。
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

import argparse
import base64
import json
import os
import secrets
import threading
import time

import anyio
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from engine.dialog_service import DialogService
from engine.invite import (OPENID_COOKIE, check_openid, parse_allowlist)  # B7 S5 邀请守卫
from engine.visitor_gate import COOKIE_NAME, VisitorGate  # P0.10 游客配额闸门
from engine.router import Router

from config.paths import PROJECT_ROOT as _PROJECT_ROOT
from config import settings as _cfg_settings  # B7 S5：读邀请白名单（测试 patch config.settings.X）


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


# P0.10 · 游客 vid（Cookie 读写，from handler 依赖 → Request/Response）
def _extract_vid(request: Request, payload: dict, gate) -> tuple:
    """判定口径：未带供应商 id、或 id == 环境默认供应商（"env"）＝走 owner Key
    → 计入游客配额；显式 BYOK（带非 env 供应商 id）与闸门关闭完全豁免。

    返回 (vid_or_None, vid_cookie_header_or_None)：vid 给配额扣减，cookie 待下发给前端。
    gate 为 None（闸门关闭）或显式 BYOK → 豁免，均返回 (None, None)。"""
    if gate is None:
        return None, None
    provider_id = str((payload.get("provider_id") or "")).strip()
    from engine import providers as _prov
    uses_owner_key = (not provider_id) or (provider_id == _prov.ENV_PROVIDER_ID)
    if not uses_owner_key:
        return None, None
    # 读 Cookie 头；无则生成 vid 并构造待下发 cookie（同 visitor_gate.ensure_vid 语义）
    cookie = request.headers.get("Cookie") or ""
    for part in cookie.split(";"):
        k, _, v = part.strip().partition("=")
        if k == COOKIE_NAME and v:
            return v, None
    vid = secrets.token_urlsafe(16)
    xff = (request.headers.get("X-Forwarded-Proto") or "").lower()
    secure = "; Secure" if xff == "https" else ""
    vid_cookie = f"{COOKIE_NAME}={vid}; Path=/; HttpOnly; SameSite=Lax{secure}"
    return vid, vid_cookie


def _json(obj, status=200, vid_cookie=None):
    """保 send_json 语义：CORS 头 + 可选游客 vid cookie（P0.10）。"""
    headers = {"Access-Control-Allow-Origin": "*"}
    if vid_cookie:
        headers["Set-Cookie"] = vid_cookie
    return JSONResponse(obj, status_code=status, headers=headers)


def _request_open_id(request: Request):
    """B7 S5 · 从请求取 open_id：query `open_id=` 优先，其次 Cookie `hsk_openid`。"""
    q = str((request.query_params.get("open_id") or "")).strip()
    if q:
        return q
    cookie = request.headers.get("Cookie") or ""
    for part in cookie.split(";"):
        k, _, v = part.strip().partition("=")
        if k == OPENID_COOKIE and v:
            return v
    return None


def _read_json_body(request: Request) -> tuple:
    """读 POST body 为 dict；非法/缺失 → 返回 (None, {"error":"invalid json body"})。"""
    try:
        raw = anyio.from_thread.run(request.body)
        if not raw:
            raw = b"{}"
        return json.loads(raw), None
    except Exception:
        return None, {"error": "invalid json body"}


def _dialog_sse_events(out: dict):
    """B7 S1 · /api/dialog SSE 四事件契约（契约文档 v2，[B7-B] 聚焦 6 形状落地）：
    事件序 = intercept? → message×N → done。
    - intercept.data：配额三档（S3 注入 out["intercept"]，未注入则无此事件）。
    - message.data：单个回合卡片 {trace_i, kind, ok, payload}，payload = 契约回合卡片原样内嵌。
    - done.data：完整对话响应 dict（含 trace/conversation_id/…，另加 rounds/energy_used）——
      对前端是流式收尾全集，对测试是可保真重汇编回原 JSON 形状（既有断言零改动）。
    断连：sse-starlette 取消生成器 → asyncio.CancelledError 由 EventSourceResponse 消化；
    已产出回合已由服务端内存落盘（对话深链幂等恢复），不丢。"""
    intc = out.get("intercept")
    if intc:
        yield {"event": "intercept", "data": json.dumps(intc, ensure_ascii=False)}
    for i, item in enumerate(out.get("trace", [])):
        card = {
            "trace_i": i,
            "kind": item.get("kind", "dialog"),
            "ok": bool(item.get("ok")),
            "payload": item,
        }
        yield {"event": "message", "data": json.dumps(card, ensure_ascii=False)}
    done = dict(out)
    done["rounds"] = len(out.get("trace", []))
    done.setdefault("energy_used", 0)   # S2/S3 计量后回填
    yield {"event": "done", "data": json.dumps(done, ensure_ascii=False)}


def create_app(router: "Router", index_dir: str, generation=None, dialog_llm=None,
               memory_root: str = "data",
               gate=None, legacy_dir=None):
    """构造 FastAPI app，闭包捕获 Router（每个请求共享同一图谱实例）。
    路由函数全部 def（sync）→ Starlette 丢线程池执行，每请求一线程 ≈
    ThreadingHTTPServer 语义，RLock 串行化闭环保持。禁 async def 路由（防事件循环改锁语义）。
    generation: 可选 GenerationEngine；默认 None 懒建（共享 router.graph + Writeback）。
    dialog_llm: 可选 planner llm_call 注入（测试 mock；None → 真实 LLMClient.chat）。
    memory_root: LearnerMemory 落盘根目录（默认 data/；测试注入临时目录）。
    gate: 可选 VisitorGate（P0.10 游客每日配额）；None → 闸门关闭（测试默认豁免）。"""

    lock = threading.RLock()
    svc = DialogService(router=router, lock=lock, generation=generation,
                        dialog_llm=dialog_llm, memory_root=memory_root, gate=gate)

    _lock = lock
    _svc = svc
    _router = router
    _memory_root = memory_root
    # B7 S4 双壳：/ → React 壳（index_dir 或其 dist 构建产物）；/legacy/ → legacy 工具壳。
    _legacy_dir = legacy_dir or os.path.join(_PROJECT_ROOT, "web_legacy")

    app = FastAPI()

    # B7 S5 · 邀请制守卫（白名单非空才启用；默认空 = 完全旁路，契约零变化）。
    # 未授权：页面请求 → 只出邀请页；/api/* → 403 invite_required（防绕过直取数据）。
    # async 中间件仅读请求头/query 并可能短路返回，不触业务 RLock（sync handler 仍走线程池）。
    _invite_allowlist = parse_allowlist(getattr(_cfg_settings, "INVITE_ALLOWLIST", ""))
    if _invite_allowlist:
        from engine.invite import INVITE_HTML

        @app.middleware("http")
        async def _invite_guard(request: Request, call_next):
            if not check_openid(_invite_allowlist, _request_open_id(request)):
                if request.url.path.startswith("/api/"):
                    return JSONResponse(
                        {"error": "invite_required",
                         "message": "当前为邀请制内测，尚未开放公开访问。"},
                        status_code=403)
                return HTMLResponse(INVITE_HTML, status_code=200)
            return await call_next(request)

    # ---------------- 6 个改数据端点：薄委托 DialogService（0.30 拆分第 1 步） ----------------

    @app.post("/api/process")
    def api_process(request: Request):
        payload, err = _read_json_body(request)
        if err is not None:
            return _json(err, 400)
        status, out = _svc.process(payload)
        return _json(out, status=status)

    @app.post("/api/verify")
    def api_verify(request: Request):
        payload, err = _read_json_body(request)
        if err is not None:
            return _json(err, 400)
        status, out = _svc.verify(payload)
        return _json(out, status=status)

    @app.post("/api/generate")
    def api_generate(request: Request):
        payload, err = _read_json_body(request)
        if err is not None:
            return _json(err, 400)
        try:
            status, out = _svc.generate(payload)
            return _json(out, status=status)
        except Exception as e:
            # 生成引擎内部已结构化为 degraded；此处兜底防 HTTP 500
            return _json({
                "ok": False, "status": "degraded",
                "message": f"generate failed: {e}",
                "unit": None, "diagnostics": [], "attempts": 0}, 500)

    @app.post("/api/dialog")
    def api_dialog(request: Request):
        # 唯一对话入口（业务编排全在 DialogService.dialog）：
        # vid（P0.10 游客配额）是唯一需 HTTP 上下文的前置——Cookie 读写自此解耦；
        # owner-key 判定在 HTTP 层，配额扣减在服务层。
        # B7 S1：200 成功改 SSE（事件序 intercept?→message*→done，见契约文档 v2）；
        #       非 200（empty text/无 Key/配额硬错）仍一次性 JSON，与 M0 纪律同构。
        payload, err = _read_json_body(request)
        if err is not None:
            return _json(err, 400)
        vid, vid_cookie = _extract_vid(request, payload, _svc.gate)
        status, out = _svc.dialog(payload, vid=vid)
        if status != 200 or "trace" not in out:
            return _json(out, status=status, vid_cookie=vid_cookie)
        headers = {"Access-Control-Allow-Origin": "*"}
        if vid_cookie:
            headers["Set-Cookie"] = vid_cookie
        return EventSourceResponse(_dialog_sse_events(out), headers=headers)

    @app.get("/api/quota")
    def api_quota(request: Request):
        """B7 S1 · QuotaBar 首屏数据源：读 VisitorGate 现态（只读，不消耗、不落盘）。
        返回 {energy_left, daily_total, est_cost, round_count}（契约文档 v2，[B7 quota 现态]）。
        能量折算(S2 TokenMeter) / 12 回合硬限(S3) 落闸后由 gate 回填；此处读取现态原样外挂。
        - gate=None（测试默认豁免）或 BYOK（非 env 供应商）→ 不限额度
        - 复用 _extract_vid 的 owner-key 判定，但 vid 只读解析（GET 无副作用，不生成不下发 cookie）"""
        gate = _svc.gate
        st = {"energy_left": -1, "daily_total": 0, "est_cost": 1,
              "round_count": 0, "reset_at": "额度不限"}
        if gate is not None:
            provider_id = str((request.query_params.get("provider_id") or "")).strip()
            session_id = (request.query_params.get("session_id") or "").strip() or None
            from engine import providers as _prov
            uses_owner_key = (not provider_id) or (provider_id == _prov.ENV_PROVIDER_ID)
            if uses_owner_key:
                vid = None
                cookie = request.headers.get("Cookie") or ""
                for part in cookie.split(";"):
                    k, _, v = part.strip().partition("=")
                    if k == COOKIE_NAME and v:
                        vid = v
                        break
                s = gate.get_state(vid, session_id=session_id)
                st = {"energy_left": s["energy_left"], "daily_total": s["daily_total"],
                      "est_cost": s["est_cost"], "round_count": s["round_count"],
                      "reset_at": s["reset_at"]}
        return _json(st)

    @app.post("/api/session/update")
    def api_session_update(request: Request):
        payload, err = _read_json_body(request)
        if err is not None:
            return _json(err, 400)
        status, out = _svc.session_update(payload)
        return _json(out, status=status)

    @app.post("/api/session/delete")
    def api_session_delete(request: Request):
        payload, err = _read_json_body(request)
        if err is not None:
            return _json(err, 400)
        status, out = _svc.session_delete(payload)
        return _json(out, status=status)

    # ---------------- GET 数据端点（各端点模块级降级，不统一包装） ----------------

    @app.get("/api/graph")
    def api_graph(request: Request):
        with _lock:
            snap = _router.graph.graph_snapshot()
        return _json(snap)

    @app.get("/api/scenarios")
    def api_scenarios(request: Request):
        try:
            from engine.scenarios import list_scene_cards
            return _json({"scenes": list_scene_cards(),
                          "count": len(list_scene_cards())})
        except Exception as e:  # noqa: BLE001 场景库不可用 → 空列表不报错
            return _json({"scenes": [], "count": 0, "error": str(e)})

    @app.get("/api/metrics")
    def api_metrics(request: Request):
        try:
            from engine.metrics import all_metrics
            return _json(all_metrics(_memory_root))
        except Exception as e:  # noqa: BLE001
            return _json({"learners": {}, "generated_at": 0,
                          "error": f"metrics failed: {e}"}, 500)

    @app.get("/api/alignment")
    def api_alignment(request: Request):
        try:
            return _json(_alignment_summary())
        except Exception as e:  # noqa: BLE001
            return _json({"error": f"alignment failed: {e}"}, 500)

    @app.get("/api/quiz")
    def api_quiz(request: Request):
        """P0.13 独立复习页 GET：拉复习队列 → 用确定性挖空引擎造题。
        队列里的 kp：有例句能挖到空 → cloze；否则降级为 recall 记忆自检。
        （对齐：无例句点造题时降级处理）返回 {queue, questions}。"""
        try:
            qs = request.query_params
            limit = int((qs.get("limit") or "10"))
            with _lock:
                queue = _router.graph.get_review_queue()
                sub = queue[:limit]
                from engine.quiz import build_review_items
                questions = build_review_items(sub)
            return _json({
                "queue": sub,
                "count": len(questions),
                "questions": questions,
            })
        except Exception as e:  # noqa: BLE001 造题失败 → 空队列 500，前端自降级
            return _json({"errors": [], "queue": [],
                          "error": f"quiz failed: {e}"}, 500)

    @app.post("/api/quiz/answer")
    def api_quiz_answer(request: Request):
        """P0.13 复习页答完回写：接收 [{kp_id, correct}]，逐 kp review_feedback。
        多知识点回写：每次提交可含多个 kp，逐个调度（FSRS + 化石化）。
        rating：correct=True→3(想起) / False→1(忘记)。"""
        payload, err = _read_json_body(request)
        if err is not None:
            return _json(err, 400)
        answers = payload.get("answers") or payload.get("items") or []
        results = []
        with _lock:
            for a in answers:
                kp_id = a.get("kp_id")
                correct = a.get("correct")
                event_key = a.get("event_key", "")
                if not kp_id or correct is None:
                    results.append({"kp_id": kp_id, "status": "bad_request"})
                    continue
                try:
                    res = _router.graph.review_feedback(
                        kp_id, rating=(3 if correct else 1),
                        event_key=event_key)
                    results.append({"kp_id": kp_id, "status": res.get("status"),
                                    "interval_days": res.get("interval_days")})
                except Exception as e:  # noqa: BLE001
                    results.append({"kp_id": kp_id, "status": "error", "msg": str(e)})
        return _json({"results": results, "count": len(results)})

    @app.get("/api/profile")
    def api_profile(request: Request):
        """只读画像 + 会话列表（0.19 前端 v2：AI 学习报告 / 侧栏数据源）。
        - common_errors：live facts（graph×ledger 实时汇合，与每轮写回同源）
        - profile：落盘画像原块（level/native_lang 及历史 common_errors 兜底）
        - sessions：按 updated_at 降序，仅元信息不含 messages（防载荷膨胀）
        - stats：图谱/复习/惯犯计数（报告卡头部数据）"""
        qs = request.query_params
        learner_id = (qs.get("learner") or "").strip() or _router.learner_id
        with _lock:
            from engine.memory.summarize import (
                build_profile_summary, build_profile_facts)
            mem = _svc.get_memory(learner_id)
            wb = _svc.get_writeback(learner_id)
            profile = mem.get_profile()
            try:
                facts = build_profile_facts(_router.graph, wb.ledger)
            except Exception:  # noqa: BLE001
                facts = []
            try:
                summary_text = build_profile_summary(_router.graph, wb.ledger)
            except Exception:  # noqa: BLE001
                summary_text = ""
            sessions = mem.list_sessions()
            # 0.24：置顶优先，组内仍按 updated_at 降序
            sessions.sort(key=lambda s: (not s["pinned"], -s["updated_at"]))
            snap = _router.graph.graph_snapshot()
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
            return _json({
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

    @app.get("/api/conversation")
    def api_conversation(request: Request):
        """只读单会话消息历史（0.19：URL 深链 ?conversation=<id> 恢复会话）。
        透传 user/assistant 的 {role, content}；assistant 消息额外白名单透传 cards/why
        （0.27 成果卡随会话持久），其余 metadata 内部项一律不暴露。"""
        qs = request.query_params
        conversation_id = (qs.get("id") or "default").strip()
        learner_id = (qs.get("learner") or "").strip() or _router.learner_id
        with _lock:
            mem = _svc.get_memory(learner_id)
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
        return _json({
            "conversation_id": conversation_id,
            "messages": visible,
        })

    @app.post("/api/profile")
    def api_profile_save(request: Request):
        """0.22 方向3 · 保存个性化 persona；0.25 · 扩展支持顶层 user_level（起点分层）。
        - persona：normalize_persona 校验/清洗（白名单 + 截长），非法 reply_style → 400
        - user_level：统一存 'HSK{n}'（1-6），非法值 400
        - 部分合并：只更新给出的字段，其余沿用现值；至少给一个字段"""
        payload, err = _read_json_body(request)
        if err is not None:
            return _json(err, 400)
        learner_id = str(payload.get("learner") or "").strip() or _router.learner_id
        raw = payload.get("persona")
        raw_level = payload.get("user_level")
        raw_l1 = payload.get("learner_l1")
        raw_uilang = payload.get("ui_lang") or payload.get("native_lang")
        raw_onboard = payload.get("onboarding_done")
        if (raw is None and raw_level is None and raw_l1 is None
                and raw_uilang is None and raw_onboard is None):
            return _json({"error": "无可更新字段"
                                  "（persona/user_level/learner_l1/ui_lang/onboarding_done）",
                         "code": "nothing_to_update"}, 400)
        with _lock:
            mem = _svc.get_memory(learner_id)
            result = {"ok": True}
            if raw is not None:
                if not isinstance(raw, dict):
                    return _json({"error": "persona 应为对象",
                                  "code": "invalid_persona"}, 400)
                from engine.persona import normalize_persona
                current = mem.get_profile().get("persona")
                try:
                    normalized = normalize_persona(
                        raw, current=current if isinstance(current, dict) else None)
                except ValueError as e:
                    return _json({"error": str(e),
                                  "code": "invalid_persona"}, 400)
                mem.update_profile(persona=normalized)
                result["persona"] = normalized
            if raw_level is not None:
                label = DialogService._normalize_user_level(raw_level)
                if label is None:
                    return _json(
                        {"error": "user_level 应为 'HSK1'-'HSK6' 或数字 1-6",
                         "code": "invalid_user_level"}, 400)
                mem.update_profile(user_level=label)
                if hasattr(_router, "set_level"):
                    try:
                        _router.set_level(label)   # 起点分层即时生效
                    except Exception:  # noqa: BLE001 同步失败不阻断
                        pass
                result["user_level"] = label
            # ---- P0.12 冷启动引导落库：learner_l1 / ui_lang / onboarding_done ----
            if raw_l1 is not None:
                s = str(raw_l1).strip().lower()
                if not s or len(s) > 16:
                    return _json({"error": "learner_l1 应为非空语言代码",
                                  "code": "invalid_learner_l1"}, 400)
                mem.update_profile(learner_l1=s)
                result["learner_l1"] = s
            if raw_uilang is not None:
                s = str(raw_uilang).strip().lower()
                if not s or len(s) > 16:
                    return _json({"error": "ui_lang 应为非空语言代码",
                                  "code": "invalid_ui_lang"}, 400)
                mem.update_profile(native_lang=s)
                result["native_lang"] = s
            if raw_onboard is not None:
                mem.update_profile(onboarding_done=bool(raw_onboard))
                result["onboarding_done"] = bool(raw_onboard)
        return _json(result)

    @app.post("/api/onboard/assess")
    def api_onboard_assess(request: Request):
        """P0.12 · 冷启动摸底定级（建议，可改，落库由完成端点管）。
        对引导里填的 1-4 句跑识别预扫：句内识别到确认层偏误记为偏误句。
        保守启发式：偏误率高 → 建议下调起点等级；3 句太少不自动上调（防高估）。
        复用 DialogService 的识别技能构造口径（确定性规则，无 LLM 依赖 → 无 Key 也能摸底）。"""
        payload, err = _read_json_body(request)
        if err is not None:
            return _json(err, 400)
        learner_id = str(payload.get("learner") or "").strip() or _router.learner_id
        texts = payload.get("texts")
        if not isinstance(texts, list) or not 1 <= len(texts) <= 4:
            return _json({"error": "texts 应为 1-4 条句子",
                          "code": "invalid_texts"}, 400)
        texts = [str(t).strip() for t in texts]
        texts = [t for t in texts if t]
        if not texts:
            return _json({"error": "texts 不能全为空",
                          "code": "invalid_texts"}, 400)
        with _lock:
            mem = _svc.get_memory(learner_id)
            prof = mem.get_profile()
            raw_level = payload.get("user_level") or prof.get("user_level")
            base = DialogService._normalize_user_level(raw_level)
            base = 3 if base is None else int(base[3:])
            # 只读预扫：构造无 graph 的识别器（不写图谱），并把真实母语喂给迁移规则
            from skills.identify_errors import IdentifyErrorsSkill
            identify = IdentifyErrorsSkill(
                recognizer=getattr(_router, "recognizer", None),
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
        return _json({
            "learner_id": learner_id,
            "suggested_level": f"HSK{suggest}",
            "base_level": f"HSK{base}",
            "error_sentence_count": err_sentences,
            "total": total,
            "reason": reason,
        })

    @app.post("/api/ocr")
    def api_ocr(request: Request):
        """P0.7 OCR：图片/PDF（base64）→ 干净文本。仅识字层，识别交给 recognizer。
        入参：data=base64 字节码 / {data, name}；或 list=files[{data,name}]。
        name 以 .pdf 结尾 → 走 PDF→图→OCR；否则按图片处理。
        RapidOCR 缺失 → 优雅降级为 422（前端提示"不支持图片"）。"""
        from engine.ocr import (OcrUnavailable, extract_text_from_images,
                                extract_text_from_pdf)
        payload, err = _read_json_body(request)
        if err is not None:
            return _json(err, 400)
        files = payload.get("list")
        if files is None:
            if not isinstance(payload.get("data"), str):
                return _json({"error": "data 应传 base64 字符串",
                              "code": "invalid_data"}, 400)
            files = [payload]
        if not isinstance(files, list) or not files:
            return _json({"error": "files 应为非空列表", "code": "invalid_files"}, 400)
        pdf_buf, img_bytes = b"", []
        for f in files:
            try:
                raw = base64.b64decode(str(f.get("data", "")), validate=False)
            except Exception as e:  # noqa: BLE001
                return _json({"error": f"base64 解码失败：{e}",
                              "code": "invalid_data"}, 400)
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
            return _json({"error": str(e), "code": "ocr_unavailable",
                          "message": "不支持图片/PDF（OCR 能力未安装，可先粘贴文本）"},
                         422)
        text = "\n\n".join(p for p in text_parts if p)
        elapsed_ms = int(round(time.time() * 1000)) - start_ms
        return _json({
            "text": text,
            "chars": len(text),
            "files": len(files),
            "elapsed_ms": elapsed_ms,
        })

    @app.post("/api/feedback")
    def api_feedback(request: Request):
        """P0.18 评分入口落盘：对话结束 1–5 星可选提交 → feedback_<learner>.json。
        learner 沿用 router.learner_id（与 dialog 一致）；评分越界/非数由
        record_feedback 拒绝并回 400。"""
        from engine.metrics import record_feedback
        payload, err = _read_json_body(request)
        if err is not None:
            return _json(err, 400)
        learner = str(payload.get("learner") or "").strip() or _router.learner_id
        ok = record_feedback(
            root=_memory_root,
            learner=learner,
            score=payload.get("score"),
            conversation_id=payload.get("conversation_id", ""),
            comment=payload.get("comment", ""),
        )
        if not ok:
            return _json({"error": "invalid score (1-5 required)",
                          "code": "invalid_score"}, 400)
        return _json({"ok": True, "learner": learner})

    # ---------- BYOK 供应商管理（0.20：OpenAI 兼容多供应商，UI 内配置免重启） ----------

    @app.get("/api/providers")
    def api_providers_list(request: Request):
        """供应商列表（api_key 掩码，响应绝不含完整 Key）。
        default_id = 实际生效默认（store 显式默认 → env → 首个）。"""
        from engine import providers as prov
        with _lock:
            plist = [prov.masked(p)
                     for p in prov.effective_providers(_memory_root)]
            default = prov.resolve_provider(_memory_root, None)
        return _json({
            "providers": plist,
            "default_id": default["id"] if default else None,
        })

    @app.post("/api/providers")
    def api_providers_add(request: Request):
        from engine import providers as prov
        payload, err = _read_json_body(request)
        if err is not None:
            return _json(err, 400)
        name = str(payload.get("name") or "").strip()
        base_url = str(payload.get("base_url") or "").strip()
        api_key = str(payload.get("api_key") or "").strip()
        model = str(payload.get("model") or "").strip()
        if not (name and base_url and api_key and model):
            return _json({"error": "name/base_url/api_key/model 均为必填"}, 400)
        with _lock:
            store = prov.load_store(_memory_root)
            p = prov.new_provider(name, base_url, api_key, model)
            store["providers"].append(p)
            # 无 env 默认且未设显式默认 → 首个添加者自动成为默认
            if not store.get("default_id") and not prov.env_provider():
                store["default_id"] = p["id"]
            prov.save_store(_memory_root, store)
        return _json({"ok": True, "provider": prov.masked(p)})

    @app.post("/api/providers/test")
    def api_providers_test(request: Request):
        """连通性测试：支持按 id（已存供应商/env）或直接传字段（先测后存）。"""
        from engine import providers as prov
        payload, err = _read_json_body(request)
        if err is not None:
            return _json(err, 400)
        pid = str(payload.get("id") or "").strip()
        if pid:
            with _lock:
                provider = next(
                    (p for p in prov.effective_providers(_memory_root)
                     if p["id"] == pid), None)
            if provider is None:
                return _json({"error": "未知供应商 id"}, 404)
        else:
            base_url = str(payload.get("base_url") or "").strip()
            api_key = str(payload.get("api_key") or "").strip()
            model = str(payload.get("model") or "").strip()
            if not (base_url and api_key and model):
                return _json(
                    {"error": "需提供 id，或 base_url/api_key/model 三字段"}, 400)
            provider = {"base_url": base_url, "api_key": api_key,
                        "model": model}
        # 测试调用在锁外：网络 IO 不持有服务锁
        result = prov.test_provider(provider["base_url"],
                                    provider["api_key"], provider["model"])
        return _json(result)

    @app.post("/api/providers/default")
    def api_providers_default(request: Request):
        from engine import providers as prov
        payload, err = _read_json_body(request)
        if err is not None:
            return _json(err, 400)
        pid = str(payload.get("id") or "").strip()
        with _lock:
            store = prov.load_store(_memory_root)
            if pid == prov.ENV_PROVIDER_ID:
                store["default_id"] = None   # 回落 env 默认
            elif any(p["id"] == pid for p in store["providers"]):
                store["default_id"] = pid
            else:
                return _json({"error": "未知供应商 id"}, 404)
            prov.save_store(_memory_root, store)
        return _json({"ok": True, "default_id": pid})

    @app.delete("/api/providers")
    def api_providers_delete(request: Request):
        from engine import providers as prov
        qs = request.query_params
        pid = (qs.get("id") or "").strip()
        if not pid:
            return _json({"error": "缺少 id 参数"}, 400)
        with _lock:
            store = prov.load_store(_memory_root)
            before = len(store["providers"])
            store["providers"] = [p for p in store["providers"]
                                  if p["id"] != pid]
            if len(store["providers"]) == before:
                return _json(
                    {"error": "未知供应商（.env 供应商不可删除，请编辑 .env）"}, 404)
            if store.get("default_id") == pid:
                store["default_id"] = None
            prov.save_store(_memory_root, store)
        return _json({"ok": True})

    # 非 GET 未知路径 → 404（还原旧 serve 契约：经 StaticFiles 会因方法受限变 405）
    @app.api_route("/{path:path}", methods=["POST", "PUT", "PATCH", "DELETE"])
    def api_unknown_method(path: str):
        return _json({"error": "not found"}, 404)

    # 尾挂静态 · B7 S4 双壳：/legacy/ → legacy 工具壳（先挂，防被 / catch-all 吞噬）；
    # / → React 壳构建产物 dist（未构建则回退 index_dir 目录本身，便于开发）。
    # 两者/API 均为 GET 静态/home，POST 等未知路径由上方 api_unknown_method 统一 404。
    if os.path.isdir(_legacy_dir):
        app.mount("/legacy", StaticFiles(directory=_legacy_dir, html=True),
                  name="static_legacy")
    root_dir = index_dir
    _dist = os.path.join(index_dir, "dist")
    if os.path.isdir(_dist):
        root_dir = _dist
    if os.path.isdir(root_dir):
        app.mount("/", StaticFiles(directory=root_dir, html=True), name="static")

    return app


def main():
    import uvicorn
    ap = argparse.ArgumentParser(description="HSK-AI-Coach 前端壳服务（FastAPI+uvicorn）")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8612)))
    ap.add_argument("--learner", default="demo", help="学习者 id → data/graph_<id>.json")
    ap.add_argument("--lang", default="en", help="讲解/纠错语言：en(英壳+中例句,默认) 或 zh(全中文)")
    args = ap.parse_args()

    router = Router(learner_id=args.learner, native_lang=args.lang, user_level="HSK3")
    if not os.path.isdir(_INDEX_DIR):
        print(f"[serve] 前端壳目录不存在：{_INDEX_DIR}")
        print("[serve] 将仅提供 API（/api/process /api/graph），静态页待 M10 生成 web/index.html")
    app = create_app(router, _INDEX_DIR, gate=VisitorGate(root="data"))
    uvicorn.run(app, host=args.host, port=args.port)
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


if __name__ == "__main__":
    main()