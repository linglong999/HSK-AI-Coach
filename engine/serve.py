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
    generation: 可选 GenerationEngine；默认 None 懒建（共享 router.graph + Writeback）。
    dialog_llm: 可选 planner llm_call 注入（测试 mock；None → 真实 LLMClient.chat）。
    memory_root: LearnerMemory 落盘根目录（默认 data/；测试注入临时目录）。
    gate: 可选 VisitorGate（P0.10 游客每日配额）；None → 闸门关闭（测试默认豁免）。"""

    lock = threading.RLock()

    class H(BaseHTTPRequestHandler):
        _router = router
        _index_dir = index_dir
        _lock = lock
        _generation = generation
        _planners = {}   # provider_id -> Planner（0.20 BYOK：按供应商缓存；"__mock__"=注入路径）
        _dialog_llm = dialog_llm
        _memory_root = memory_root
        _gate = gate   # P0.10 游客配额闸门（None = 关闭）
        _memories = {}   # learner_id -> LearnerMemory（M5 多轮记忆）
        _writebacks = {}  # learner_id -> Writeback（M8 事件账本/两段式）
        _interventions = {}  # conversation_id -> InterventionTracker（0.22 方向3）
        _identify_skill = None  # 预扫识别技能（共享 router.recognizer+graph 实例）

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

        def _get_tracker(self, conversation_id: str):
            """按 conversation_id 缓存介入跟踪器（0.22 方向3：打断计数/近错窗口/上轮档位）。"""
            from engine.intervention import InterventionTracker
            tr = self.__class__._interventions.get(conversation_id)
            if tr is None:
                tr = InterventionTracker()
                self.__class__._interventions[conversation_id] = tr
            return tr

        def _get_identify_skill(self):
            """预扫识别技能（0.22 方向3 D3.4：识别触发移到 serve——每轮产出句先走
            identify（喂图谱，event_key 幂等与 planner 路径同构）再分档介入）。
            共享 router.recognizer + router.graph 实例（与 planner 注册表同一来源）。"""
            if self.__class__._identify_skill is None:
                from skills.identify_errors import IdentifyErrorsSkill
                self.__class__._identify_skill = IdentifyErrorsSkill(
                    recognizer=getattr(self._router, "recognizer", None),
                    graph=self._router.graph)
            return self.__class__._identify_skill

        def _writeback_ledger_events(self, wb, trace, user_input: str,
                                     skip_text: str = "") -> list:
            """M8 两段式·账本侧（从 planner trace 编排）：
            - 识别命中（identify_errors ok）→ ledger observation_error（惯犯判定数据源）
            - 复述验证 pass（verify_retell verdict=pass）→ on_confirmed 记 concept_confirmed
            图谱侧写入已在技能内完成（identify→ingest_error / verify→ingest_verdict），
            此处只补事件账本；单条失败降级不阻塞对话。
            确认对象：同轮识别的 KP 优先；跨轮（上轮识别讲解、本轮复述通过）时
            从账本推导"最近观察过且其后无确认"的 KP 兜底（复述验证的是最近讲解，
            讲解对象即最近未确认偏误；宁紧勿滥，倒序最多 3 个）。
            skip_text（0.22 方向3）：serve 预扫已对同句记过账，planner 若重复
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
                return H._attach_kp_to_why(items, pre_scan.get("errors") or [])
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

        # 前端 renderTrace 会渲染成成果卡的技能白名单（0.27 随会话持久）
        _CARD_SKILLS = frozenset({
            "identify_errors", "explain_error", "verify_retell",
            "lookup_knowledge_point", "get_review_queue", "generate_unit",
            "web_search", "parse_document", "retrieve_corpus",
        })

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
                if tr.get("name") in H._CARD_SKILLS:
                    out.append({"name": tr.get("name"), "result": tr.get("result")})
            return out

        @staticmethod
        def _assistant_payload(res, pre_scan, provider, native_lang, reason):
            """0.27 · assistant 消息随会话持久的成果卡载荷：{cards, why}。
            cards 恒存（这轮的成果卡视图）；why 仅当本轮识别到偏误且非材料句才生成。"""
            cards = H._compact_cards((res or {}).get("trace") or [])
            why = []
            # provider 门槛：mock(dialog_llm 注入) 下 provider=None，跳过以免单测触网；
            # 真实运行 provider 已解析（BYOK 或默认 env 供应商）才生成 why。
            if (provider and isinstance(pre_scan, dict)
                    and (pre_scan.get("errors") or [])
                    and reason not in ("material",)):
                why = H._build_why(pre_scan, provider, native_lang)
            return {"cards": cards, "why": why}

        def _send_json(self, obj, status=200):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            vid_cookie = getattr(self, "_vid_cookie", None)   # P0.10 游客 vid 下发（dialog 闸门暂存）
            if vid_cookie:
                self.send_header("Set-Cookie", vid_cookie)
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
            # 边界校验：key_points 契约是 dict 列表（含 text）；畸形入参给 400，
            # 不把它透传给 verifier 炸成内部 500（此前实测畸形输入会 AttributeError）
            if (not isinstance(key_points, list)
                    or not all(isinstance(kp, dict) and str(kp.get("text") or "").strip()
                               for kp in key_points)):
                self._send_json({"error": "invalid key_points: 需 [{text}, …]"}, 400)
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
            # 0.21 教学层语言：前端语言开关上送；缺省回落启动参数 --lang（Router.native_lang）
            # v0.3 P0.1 S2 拆分：新增 ui_lang（新）与 learner_l1（新）两个字段；
            # native_lang 保留作为 ui_lang 的兼容别名（旧前端仍可用，旧测试不破坏）。
            ui_lang = str((payload.get("ui_lang") or payload.get("native_lang") or "")).strip().lower() or \
                str(getattr(self._router, "native_lang", "") or "")
            learner_l1 = str((payload.get("learner_l1") or "unknown")).strip().lower()
            # ui_lang 与 learner_l1 独立：选中文界面 ≠ 中文母语；
            # learner_l1 缺省 = "unknown"（不污染 L1 统计）。
            native_lang = ui_lang  # 局部别名：函数内下游调用兼容旧字段

            # 0.22 方向2 · 场景对话：scene_id → 编译 [Scene] 段注入主链（D2.2）。
            # 场景缺失/未命中 → scene_brief 空串，planner 照常走自由对话（不阻断）。
            scene_brief = ""
            scene_id = str((payload.get("scene_id") or "")).strip()
            if scene_id:
                try:
                    from engine.scenarios import build_scene_brief, get_scene
                    _scene = get_scene(scene_id)
                    if _scene:
                        scene_brief = build_scene_brief(_scene, native_lang)
                except Exception:  # noqa: BLE001 场景加载失败 → 退化为自由对话
                    scene_brief = ""

            # P0.10 游客模式闸门（放置在供应商解析之前：满额直接 429，不调模型、不计次、不深校验）。
            # 判定口径：未带供应商 id、或 id == 环境默认供应商（"env"）＝走 owner Key 的请求 → 计入游客配额；
            # 显式 BYOK（带非 env 供应商 id）完全豁免。
            gate = self.__class__._gate
            if gate is not None:
                from engine import providers as _prov
                uses_owner_key = (not provider_id) or (provider_id == _prov.ENV_PROVIDER_ID)
                if uses_owner_key:
                    vid = gate.ensure_vid(self)
                    with self._lock:
                        ok, remaining, reset = gate.check_and_consume(vid)
                    if not ok:
                        self._send_json({
                            "error": "今日游客额度已用尽，次日 UTC 00:00 重置；"
                                     "配置你自己的 API Key 可无限使用。",
                            "code": "visitor_quota_exceeded",
                            "message": "配置你自己的 API Key 可无限使用",
                            "remaining": 0, "reset_at": reset}, 429)
                        return

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
                from engine.intervention import (
                    MAX_SCAN_CHARS, DEFAULT_CAP, build_intervention_directive,
                    detect_help_intent)
                from engine.persona import build_persona_brief, build_tutor_style_directive
                mem = self._get_memory(learner_id)
                wb = self._get_writeback(learner_id)
                history = mem.to_llm_history(conversation_id)
                # M8 画像注入：常错点/惯犯摘要进 system（含本轮前全部图谱+账本状态）
                try:
                    profile_summary = build_profile_summary(
                        self._router.graph, wb.ledger)
                except Exception:  # noqa: BLE001
                    profile_summary = ""

                # ---- 0.22 方向3 · persona（个性化栏：风格/称呼/人设/自定义指令/打断上限）----
                profile_block = mem.get_profile() or {}
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
                # 画像 user_level 未设 → HSK3（与技能默认一致）。归一用 serve 自身方法
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
                level_label = f"HSK{level_int}"

                # ---- 0.22 方向3 · 介入判定（确定性分档，D3.4 主链重构）----
                # 预扫：每轮产出句先走 identify（喂图谱+迁移假设，句子→图谱链不变），
                # 再用组合信号分档（求助/空/含义不清/连续错率 + 打断上限）。
                # 求助句不预扫（meta 问题，识别交给 planner 按需调技能）；
                # 超长文本视为学习材料（交给 parse_document），不识别不介入。
                tracker = self._get_tracker(conversation_id)
                pre_scan = None
                scan_notices = []
                help_intent = detect_help_intent(user_input)
                too_long = len(user_input) > MAX_SCAN_CHARS
                if not help_intent and not too_long:
                    try:
                        pre_scan = self._get_identify_skill().run(
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
                            wb.ledger.record(
                                "observation_error", kp,
                                signature=err.get("fragment", ""),
                                evidence=user_input)
                        except Exception as e:  # noqa: BLE001
                            scan_notices.append({"stage": "ledger_write",
                                                 "reason": str(e), "fatal": False})
                if too_long:
                    level, reason = "none", "material"   # 材料句：不介入不分档
                else:
                    level, reason = tracker.observe(
                        user_input, max_conf=max_conf,
                        error_flag=error_flag, cap=cap)
                intervention_directive = build_intervention_directive(
                    level, reason, native_lang, recognition=pre_scan,
                    hsk_level=level_int)

                res = self._get_planner(provider).run(
                    user_input, history=history, learner_id=learner_id,
                    profile_summary=profile_summary, native_lang=native_lang,
                    user_level=level_label, scene_brief=scene_brief, persona_brief=persona_brief,
                    style_directive=style_directive,
                    intervention_directive=intervention_directive)
                # M8 两段式·账本侧：识别命中→observation_error；复述 pass→concept_confirmed
                m8_notices = scan_notices + self._writeback_ledger_events(
                    wb, res.get("trace", []), user_input,
                    skip_text=(user_input if isinstance(pre_scan, dict) else ""))
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
                # 会话标题（0.19 前端 v2）：首条消息自动设为标题（取自首句提问，便于侧栏回访识别）
                if not history:
                    try:
                        mem.touch(conversation_id, title=user_input[:18])
                    except Exception:  # noqa: BLE001
                        pass
                if reply:
                    # 0.27 · 成果卡随会话持久：cards(卡视图)+why 写进 assistant 消息 metadata，
                    # 恢复会话时前端据以原位重建。仅新会话生效（旧记录无此字段）。
                    payload = H._assistant_payload(res, pre_scan, provider,
                                                   native_lang, reason)
                    why_items = payload["why"]
                    mem.append(conversation_id, "assistant", reply,
                               metadata={"skills": res.get("used_skills", []),
                                         "fallback": bool(res.get("fallback", False)),
                                         **payload})
                graph_snapshot = self._router.graph.graph_snapshot()
            degraded = m8_notices  # 账本/画像写入失败降级（非致命）
            if res.get("fallback"):
                degraded.append({"stage": "planner",
                                 "reason": f"planner fallback: {res.get('reason', 'unterminated')}",
                                 "fatal": False})
            # why_items：assistant 已写请记忆时由其填充；否则（无回复）为空
            if "why_items" not in locals():
                why_items = []
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
                "intervention": {"level": level, "reason": reason,
                                 "used": tracker.interrupt_used, "cap": cap},
                "why": why_items,
                "degraded": degraded,
                "graph": graph_snapshot,
            }
            self._send_json(out)

        def do_POST(self):
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/")
            if path in ("/api/process", "/api/verify", "/api/generate", "/api/dialog",
                        "/api/providers", "/api/providers/test",
                        "/api/providers/default", "/api/profile",
                        "/api/session/update", "/api/session/delete",
                        "/api/quiz/answer", "/api/onboard/assess", "/api/ocr",
                        "/api/feedback"):
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
            if path == "/api/quiz/answer":
                # P0.13 复习页答完回写：逐 kp review_feedback（多知识点回写）
                try:
                    self._do_api_quiz_answer(payload)
                except Exception as e:
                    self._send_json({"error": f"quiz answer failed: {e}"}, 500)
                return
            if path == "/api/onboard/assess":
                # P0.12 冷启动摸底：对引导里填的句子做识别预扫 → 定级建议（可改）
                try:
                    self._do_api_onboard_assess(payload)
                except Exception as e:
                    self._send_json({"error": f"onboard assess failed: {e}"}, 500)
                return
            if path == "/api/ocr":
                # P0.7 OCR：图片/PDF → 干净文本（仅识字，识别交给 recognizer）
                try:
                    self._do_api_ocr(payload)
                except Exception as e:
                    self._send_json({"error": f"ocr failed: {e}"}, 500)
                return
            if path == "/api/feedback":
                # P0.18 评分入口：对话结束 1–5 星可选提交 → 写 feedback_<learner>.json
                try:
                    self._do_api_feedback(payload)
                except Exception as e:
                    self._send_json({"error": f"feedback failed: {e}"}, 500)
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
            if path == "/api/profile":
                try:
                    self._do_api_profile_save(payload)
                except Exception as e:
                    self._send_json({"error": f"profile save failed: {e}"}, 500)
                return
            if path == "/api/session/update":
                try:
                    self._do_api_session_update(payload)
                except Exception as e:
                    self._send_json({"error": f"session update failed: {e}"}, 500)
                return
            if path == "/api/session/delete":
                try:
                    self._do_api_session_delete(payload)
                except Exception as e:
                    self._send_json({"error": f"session delete failed: {e}"}, 500)
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
            if path == "/api/scenarios":
                # 0.22 方向2 · 预置场景库（前端场景卡列表，D2.3）
                try:
                    from engine.scenarios import list_scene_cards
                    self._send_json({"scenes": list_scene_cards(),
                                     "count": len(list_scene_cards())})
                except Exception as e:  # noqa: BLE001 场景库不可用 → 空列表不报错
                    self._send_json({"scenes": [], "count": 0, "error": str(e)})
                return
            if path == "/api/quiz":
                # P0.13 独立复习页：拉复习队列 + 确定性挖空造题
                try:
                    self._do_api_quiz(parsed)
                except Exception as e:  # noqa: BLE001
                    self._send_json({"errors": [], "queue": [],
                                     "error": f"quiz failed: {e}"}, 500)
                return
            if path == "/api/metrics":
                # P0.18 效果度量：聚合全部 learner 的 4 指标（实时算，不靠手工跑）
                try:
                    from engine.metrics import all_metrics
                    self._send_json(all_metrics(self._memory_root))
                except Exception as e:  # noqa: BLE001
                    self._send_json({"learners": {}, "generated_at": 0,
                                     "error": f"metrics failed: {e}"}, 500)
                return
            if path == "/api/alignment":
                # P0.19 fe5：教材对齐 + 考纲审核概览（textbook_map × syllabus 真实数据）
                try:
                    self._send_json(_alignment_summary())
                except Exception as e:  # noqa: BLE001
                    self._send_json({"error": f"alignment failed: {e}"}, 500)
                return
            # 静态：根 → index.html
            rel = parsed.path or "/"
            if rel == "/" or rel == "":
                rel = "/index.html"
            self._send_static(rel)

        def _do_api_quiz(self, parsed):
            """P0.13 独立复习页 GET：拉复习队列 → 用确定性挖空引擎造题。
            队列里的 kp：有例句能挖到空 → cloze；否则降级为 recall 记忆自检。
            （对齐：无例句点造题时降级处理）返回 {queue, questions}。"""
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
                        "pinned": bool(sess.get("pinned")),
                    })
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
                has_learning_data = bool(raw_sessions) or bool(snap.get("nodes", {}))
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
                mem = self._get_memory(learner_id)
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
                mem = self._get_memory(learner_id)
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
                    label = self._normalize_user_level(raw_level)
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
            复用 _get_identify_skill（确定性规则，无 LLM 依赖 → 无 Key 也能摸底）。"""
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
                mem = self._get_memory(learner_id)
                prof = mem.get_profile()
                raw_level = payload.get("user_level") or prof.get("user_level")
                base = self._normalize_user_level(raw_level)
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
        def _teaching_lang(native_lang: str, learner_l1: str) -> str:
            """0.19 N6 · 语言政策判定输入：用真实母语(learner_l1)代替 UI 语言(native_lang)，
            缺信时回退 UI 语言。非英语亦非中文母语（如 th/ar）→ 回落中文教学壳（不强制英语，
            与规则8"非英语母语不强制英文"一致；其母语无教学壳时用中文可理解形式兜底）。"""
            l1 = str(learner_l1 or "").strip().lower()
            if not l1 or l1 == "unknown":
                return str(native_lang or "")
            if l1 in ("en", "english", "英语", "英文"):
                return "en"
            if l1 in ("zh", "中文", "汉语", "chinese", "汉语官话", "zh-cn", "zh-hans", "zh-hant"):
                return "zh"
            return "zh"  # 其他母语：无对应教学壳，宁用中文可理解形式也不强制英语

        # ---------- 会话管理（0.24：右键菜单 置顶/重命名/删除） ----------

        def _do_api_session_update(self, payload):
            """会话元信息管理：置顶/取消置顶、重命名。
            - bump=False：管理操作不改变 updated_at（排序只反映对话活跃度）
            - title 非空截 60 字；pinned 必须 bool；至少给一个字段
            - 会话不存在 → 404 session_not_found（前端刷新列表自愈）"""
            learner_id = str(payload.get("learner") or "").strip() or \
                self._router.learner_id
            cid = str(payload.get("conversation_id") or "").strip()
            if not cid:
                self._send_json({"error": "缺少 conversation_id",
                                 "code": "missing_conversation_id"}, 400)
                return
            title = payload.get("title")
            pinned = payload.get("pinned")
            if title is not None:
                title = str(title).strip()[:60]
                if not title:
                    self._send_json({"error": "标题不能为空",
                                     "code": "invalid_title"}, 400)
                    return
            if pinned is not None and not isinstance(pinned, bool):
                self._send_json({"error": "pinned 应为布尔值",
                                 "code": "invalid_pinned"}, 400)
                return
            if title is None and pinned is None:
                self._send_json({"error": "无可更新字段（title/pinned）",
                                 "code": "nothing_to_update"}, 400)
                return
            with self._lock:
                mem = self._get_memory(learner_id)
                if mem._get(mem._safe(cid)) is None:
                    self._send_json({"error": f"会话不存在: {cid}",
                                     "code": "session_not_found"}, 404)
                    return
                mem.touch(cid, title=title, pinned=pinned, bump=False)
            self._send_json({"ok": True, "conversation_id": cid,
                             "title": title, "pinned": pinned})

        def _do_api_session_delete(self, payload):
            """删除整个会话（消息+元信息）。不存在 → 404（前端按已删处理）。"""
            learner_id = str(payload.get("learner") or "").strip() or \
                self._router.learner_id
            cid = str(payload.get("conversation_id") or "").strip()
            if not cid:
                self._send_json({"error": "缺少 conversation_id",
                                 "code": "missing_conversation_id"}, 400)
                return
            with self._lock:
                mem = self._get_memory(learner_id)
                deleted = mem.delete_session(cid)
            if not deleted:
                self._send_json({"error": f"会话不存在: {cid}",
                                 "code": "session_not_found"}, 404)
                return
            self._send_json({"ok": True, "conversation_id": cid})

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