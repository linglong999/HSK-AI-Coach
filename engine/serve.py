# ============================================================
# 前端壳 HTTP 服务层（M10）+ 唯一对话入口（0.17 统一骨架）
# 零第三方依赖：仅 Python 标准库 http.server。
# 暴露入口给前端壳（原生 JS+SVG）：
#   POST /api/dialog   → Planner 自由 ReAct（0.17 唯一对话入口，共享 skill 注册表）
#                        + M5 多轮记忆（0.18 接入项①）：服务端 LearnerMemory 权威，
#                        按 conversation_id 注入历史并写回落盘（data/memory_<learner>.json）
#                        + M8 画像/惯犯（0.18 接入项③）：常错点摘要注入 system；
#                        trace 编排 ledger 两段式事件（data/ledger_<learner>.json）；
#                        常错点 facts 汇合 M5 profile.common_errors
#   POST /api/process  → Router.process(text) → 契约 v1 result（兼容遗留 runner）
#   POST /api/verify   → Router.verify_rephrase() → 复述验证（契约 verify_rephrase 节）
#   POST /api/generate → GenerationEngine.generate_unit()（费曼单元，0.16 接入，共享 graph）
#   GET  /api/graph    → ErrorGraph.graph_snapshot()（nodes/edges/queue）
#   GET  /api/profile  → 学习者画像 + 会话列表（0.19 前端 v2：AI 学习报告/侧栏数据源，只读）
#   GET  /api/conversation?id= → 单会话消息历史（0.19：URL 深链恢复会话，只读）
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
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from engine.router import Router

# 前端壳静态目录：serve.py 位于 engine/，前端壳在项目根前端/ 或 web/
_INDEX_DIR = os.path.join(_PROJECT_ROOT, "web")


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


def make_handler(router: "Router", index_dir: str, generation=None, dialog_llm=None,
                 memory_root: str = "data"):
    """工厂构造 handler，闭包捕获 Router（每个请求共享同一图谱实例）。
    ThreadingHTTPServer 每请求一线程 → 用一把锁串行化整个闭环
    （process/verify 是多步读写组合，仅靠图谱内部锁防不住交错）。
    generation: 可选 GenerationEngine；默认 None 懒建（共享 router.graph + Writeback）。
    dialog_llm: 可选 planner llm_call 注入（测试 mock；None → 真实 LLMClient.chat）。
    memory_root: LearnerMemory 落盘根目录（默认 data/；测试注入临时目录）。"""

    lock = threading.RLock()

    class H(BaseHTTPRequestHandler):
        _router = router
        _index_dir = index_dir
        _lock = lock
        _generation = generation
        _planners = {}   # provider_id -> Planner（0.20 BYOK：按供应商缓存；"__mock__"=注入路径）
        _dialog_llm = dialog_llm
        _memory_root = memory_root
        _memories = {}   # learner_id -> LearnerMemory（M5 多轮记忆）
        _writebacks = {}   # learner_id -> Writeback（M8 事件账本/两段式）

        def log_message(self, fmt, *args):
            sys.stderr.write("  [serve] " + fmt % args + "\n")

        def _get_generation(self):
            """懒建/取共享 GenerationEngine（复用 router.graph，practice 走两段式写回）。
            免 Key 也不崩：generate_unit 内部将 LLM 失败降级为结构化 degraded。"""
            if self._generation is not None:
                return self.__class__._generation
            from engine.generation.generator import GenerationEngine
            from engine.memory.writeback import Writeback
            g = GenerationEngine(
                graph=self._router.graph,
                writeback=Writeback(graph=self._router.graph,
                                    learner_id=self._router.learner_id))
            self.__class__._generation = g
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

            cache = self.__class__._planners
            if self.__class__._dialog_llm is not None:
                if "__mock__" not in cache:
                    cache["__mock__"] = _build(self.__class__._dialog_llm)
                return cache["__mock__"]
            pid = provider["id"] if provider else "__default__"
            if pid not in cache:
                cache[pid] = _build(_provider_llm(provider) if provider
                                    else _default_dialog_llm)
            return cache[pid]

        def _get_memory(self, learner_id: str):
            """按 learner_id 缓存 LearnerMemory 实例（M5：服务端记忆为多轮上下文唯一权威源）。"""
            mem = self.__class__._memories.get(learner_id)
            if mem is None:
                from engine.memory.learner_memory import LearnerMemory
                mem = LearnerMemory(learner_id, root=self._memory_root)
                self.__class__._memories[learner_id] = mem
            return mem

        def _get_writeback(self, learner_id: str):
            """按 learner_id 缓存 Writeback（M8：ledger 事件账本 + 两段式确认）。"""
            wb = self.__class__._writebacks.get(learner_id)
            if wb is None:
                from engine.memory.writeback import Writeback
                wb = Writeback(graph=self._router.graph,
                               learner_id=learner_id, root=self._memory_root)
                self.__class__._writebacks[learner_id] = wb
            return wb

        def _writeback_ledger_events(self, wb, trace, user_input: str) -> list:
            """M8 两段式·账本侧（从 planner trace 编排）：
            - 识别命中（identify_errors ok）→ ledger observation_error（惯犯判定数据源）
            - 复述验证 pass（verify_retell verdict=pass）→ on_confirmed 记 concept_confirmed
            图谱侧写入已在技能内完成（identify→ingest_error / verify→ingest_verdict），
            此处只补事件账本；单条失败降级不阻塞对话。
            确认对象：同轮识别的 KP 优先；跨轮（上轮识别讲解、本轮复述通过）时
            从账本推导"最近观察过且其后无确认"的 KP 兜底（复述验证的是最近讲解，
            讲解对象即最近未确认偏误；宁紧勿滥，倒序最多 3 个）。"""
            notices = []
            round_kps = []
            for t in trace or []:
                if not t.get("ok"):
                    continue
                name = t.get("name")
                result = t.get("result") or {}
                if name == "identify_errors":
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
                    kps = round_kps or self._unconfirmed_recent_kps(wb.ledger)
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

        def _send_json(self, obj, status=200):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def _send_static(self, rel: str):
            # 防路径穿越：只允许 web/ 下的静态资源
            base = os.path.realpath(self._index_dir)
            path = os.path.realpath(os.path.join(base, rel.lstrip("/")))
            if not path.startswith(base) or not os.path.isfile(path):
                self.send_error(404, "not found")
                return
            ctype = "text/html; charset=utf-8"
            if path.endswith(".js"):
                ctype = "application/javascript; charset=utf-8"
            elif path.endswith(".css"):
                ctype = "text/css; charset=utf-8"
            elif path.endswith(".svg"):
                ctype = "image/svg+xml"
            with open(path, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")  # 本地开发改前端即生效
            self.end_headers()
            self.wfile.write(body)

        def _do_api_process(self, payload: dict):
            text = str((payload.get("text") or "")).strip()
            if not text:
                self._send_json({"error": "empty text"}, 400)
                return
            with self._lock:
                # 同一 Router 实例 → 图谱随学习者在服务内持久累积
                result = self._router.process(text)
                # 补充图谱快照，供前端一次性渲染（无需二次 GET）
                result["graph"] = self._router.graph.graph_snapshot()
            self._send_json(result)

        def _do_api_verify(self, payload: dict):
            # 契约：key_points 是复述验证唯一点来源（前端从讲解卡回传）
            restatement = str((payload.get("restatement") or "")).strip()
            key_points = payload.get("key_points") or []
            explanation = str(payload.get("explanation") or "")
            if not restatement:
                self._send_json({"error": "empty restatement"}, 400)
                return
            with self._lock:
                out = self._router.verify_rephrase(
                    explanation, key_points, restatement,
                    event_key=f"web#{restatement}")  # 稳定 key：实体本身（原则4）
            self._send_json(out)

        def _do_api_generate(self, payload: dict):
            # 生成费曼单元（逻辑统一，错误由 generate_unit 结构化为 degraded，非 500）
            unit_type = str((payload.get("unit_type") or "")).strip()
            if unit_type not in ("explain", "practice"):
                self._send_json({
                    "ok": False, "status": "degraded",
                    "message": f"unsupported unit_type: {unit_type}（仅 explain/practice 首发）",
                    "unit": None, "diagnostics": [], "attempts": 0}, 400)
                return

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
            self._send_json(out)

        def _do_api_dialog(self, payload: dict):
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
            user_input = str((payload.get("text") or "")).strip()
            if not user_input:
                self._send_json({"error": "empty text"}, 400)
                return
            learner_id = str((payload.get("learner_id") or "")).strip() or \
                self._router.learner_id
            conversation_id = str((payload.get("conversation_id") or "")).strip() or "default"
            provider_id = str((payload.get("provider_id") or "")).strip()

            # fail-loud：真实 LLM 路径必须先有可用供应商（mock 注入路径跳过）
            provider = None
            if self.__class__._dialog_llm is None:
                from engine import providers as prov
                if provider_id:
                    provider = next(
                        (p for p in prov.effective_providers(self._memory_root)
                         if p["id"] == provider_id), None)
                    if provider is None:
                        self._send_json({
                            "error": "未知模型供应商（可能已被删除），"
                                     "请在输入框左上角重新选择",
                            "code": "unknown_provider"}, 400)
                        return
                else:
                    provider = prov.resolve_provider(self._memory_root, None)
                if provider is None or not provider.get("api_key"):
                    self._send_json({
                        "error": "自由对话需要 LLM API Key：在「设置 → 模型密钥」添加供应商，"
                                 "或复制 .env.example 为 .env 填入 DEEPSEEK_API_KEY 后重启服务",
                        "code": "llm_not_configured"}, 400)
                    return

            with self._lock:
                from engine.memory.summarize import (
                    build_profile_summary, build_profile_facts)
                mem = self._get_memory(learner_id)
                wb = self._get_writeback(learner_id)
                history = mem.to_llm_history(conversation_id)
                # M8 画像注入：常错点/惯犯摘要进 system（含本轮前全部图谱+账本状态）
                try:
                    profile_summary = build_profile_summary(
                        self._router.graph, wb.ledger)
                except Exception:  # noqa: BLE001
                    profile_summary = ""
                res = self._get_planner(provider).run(
                    user_input, history=history, learner_id=learner_id,
                    profile_summary=profile_summary)
                # M8 两段式·账本侧：识别命中→observation_error；复述 pass→concept_confirmed
                m8_notices = self._writeback_ledger_events(
                    wb, res.get("trace", []), user_input)
                # M8 汇合 M5：常错点结构化 facts 写回长期记忆 profile 块
                try:
                    facts = build_profile_facts(self._router.graph, wb.ledger)
                    if facts:
                        mem.update_profile(common_errors=facts)
                except Exception as e:  # noqa: BLE001
                    m8_notices.append({"stage": "profile_writeback",
                                       "reason": str(e), "fatal": False})
                # 写回记忆：user 必记；assistant 回复非空才记（fallback 文案也记，多轮不断档）
                reply = str(res.get("text") or "")
                mem.append(conversation_id, "user", user_input)
                # 会话标题（0.19 前端 v2）：首条消息自动设为标题（仿 Explore 卡片标题取自提问）
                if not history:
                    try:
                        mem.touch(conversation_id, title=user_input[:18])
                    except Exception:  # noqa: BLE001
                        pass
                if reply:
                    mem.append(conversation_id, "assistant", reply,
                               metadata={"skills": res.get("used_skills", []),
                                         "fallback": bool(res.get("fallback", False))})
                graph_snapshot = self._router.graph.graph_snapshot()
            degraded = m8_notices  # 账本/画像写入失败降级（非致命）
            if res.get("fallback"):
                degraded.append({"stage": "planner",
                                 "reason": f"planner fallback: {res.get('reason', 'unterminated')}",
                                 "fatal": False})
            out = {
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
                "degraded": degraded,
                "graph": graph_snapshot,
            }
            self._send_json(out)

        def do_POST(self):
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/")
            if path in ("/api/process", "/api/verify", "/api/generate", "/api/dialog",
                        "/api/providers", "/api/providers/test",
                        "/api/providers/default"):
                try:
                    length = int(self.headers.get("Content-Length", 0))
                    raw = self.rfile.read(length) if length else b"{}"
                    payload = json.loads(raw or b"{}")
                except Exception:
                    self._send_json({"error": "invalid json body"}, 400)
                    return
            if path == "/api/process":
                try:
                    self._do_api_process(payload)
                except Exception as e:
                    self._send_json({"error": f"process failed: {e}"}, 500)
                return
            if path == "/api/verify":
                try:
                    self._do_api_verify(payload)
                except Exception as e:
                    # 验证引擎无规则回退（2.3）：如实报错，不伪造 verdict
                    self._send_json({"error": f"verify failed: {e}"}, 500)
                return
            if path == "/api/generate":
                try:
                    self._do_api_generate(payload)
                except Exception as e:
                    # 生成引擎内部已结构化为 degraded；此处兜底防 HTTP 500
                    self._send_json({
                        "ok": False, "status": "degraded",
                        "message": f"generate failed: {e}",
                        "unit": None, "diagnostics": [], "attempts": 0}, 500)
                return
            if path == "/api/dialog":
                try:
                    self._do_api_dialog(payload)
                except Exception as e:
                    self._send_json({"error": f"dialog failed: {e}"}, 500)
                return
            if path == "/api/providers":
                try:
                    self._do_api_providers_add(payload)
                except Exception as e:
                    self._send_json({"error": f"providers add failed: {e}"}, 500)
                return
            if path == "/api/providers/test":
                try:
                    self._do_api_providers_test(payload)
                except Exception as e:
                    self._send_json({"error": f"providers test failed: {e}"}, 500)
                return
            if path == "/api/providers/default":
                try:
                    self._do_api_providers_default(payload)
                except Exception as e:
                    self._send_json({"error": f"providers default failed: {e}"}, 500)
                return
            self.send_error(404)

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
            if path == "/api/graph":
                with self._lock:
                    snap = self._router.graph.graph_snapshot()
                self._send_json(snap)
                return
            if path == "/api/profile":
                self._do_api_profile(parsed)
                return
            if path == "/api/conversation":
                self._do_api_conversation(parsed)
                return
            if path == "/api/providers":
                self._do_api_providers_list()
                return
            # 静态：根 → index.html
            rel = parsed.path or "/"
            if rel == "/" or rel == "":
                rel = "/index.html"
            self._send_static(rel)

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
                mem = self._get_memory(learner_id)
                wb = self._get_writeback(learner_id)
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
                sessions = []
                # serve 单进程：缓存实例即权威（与 dialog 写回同源），无需重读盘
                raw_sessions = mem._load().get("sessions", {})
                for sid, sess in raw_sessions.items():
                    sessions.append({
                        "id": sid,
                        "title": str(sess.get("title") or ""),
                        "status": str(sess.get("status") or "active"),
                        "updated_at": int(sess.get("updated_at") or 0),
                        "message_count": len(sess.get("messages", [])),
                    })
                sessions.sort(key=lambda s: s["updated_at"], reverse=True)
                snap = self._router.graph.graph_snapshot()
                queue = snap.get("queue", [])
                try:
                    offenders = [f["knowledge_point"] for f in facts
                                 if f.get("repeat_offender")]
                except Exception:  # noqa: BLE001
                    offenders = []
                self._send_json({
                    "learner_id": learner_id,
                    "profile": profile,
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
            只透 user/assistant 的 {role, content}，不暴露 metadata 内部项。"""
            qs = parse_qs(parsed.query)
            conversation_id = ((qs.get("id") or [""])[0] or "default").strip()
            learner_id = (qs.get("learner") or [""])[0].strip() or \
                self._router.learner_id
            with self._lock:
                mem = self._get_memory(learner_id)
                msgs = mem.get_history(conversation_id, window=200)
            self._send_json({
                "conversation_id": conversation_id,
                "messages": [
                    {"role": m.get("role"), "content": m.get("content", "")}
                    for m in msgs if m.get("role") in ("user", "assistant")],
            })

        # ---------- BYOK 供应商管理（0.20：OpenAI 兼容多供应商，UI 内配置免重启） ----------

        def _do_api_providers_list(self):
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
    ap.add_argument("--port", type=int, default=8612)
    ap.add_argument("--learner", default="demo", help="学习者 id → data/graph_<id>.json")
    args = ap.parse_args()

    router = Router(learner_id=args.learner, native_lang="英语", user_level="HSK3")
    if not os.path.isdir(_INDEX_DIR):
        print(f"[serve] 前端壳目录不存在：{_INDEX_DIR}")
        print("[serve] 将仅提供 API（/api/process /api/graph），静态页待 M10 生成 web/index.html")
    httpd = ThreadingHTTPServer((args.host, args.port), make_handler(router, _INDEX_DIR))
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
    print("  GET  /              前端壳页面（若 web/index.html 存在）")
    print("  Ctrl+C 停止")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[serve] 停止")
        httpd.server_close()


if __name__ == "__main__":
    main()