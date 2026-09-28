# ============================================================
# 轻路由（router）—— 0.6.2 轻路由（Router+Chain）
# 按固定顺序串三引擎：识别 → 讲解 → 验证（写回图谱）；加简单降级分支。
# 取代旧"编排器中心调度多 Agent"（0.6 决策块7：轻路由，防漂移/循环）。
# - 写死顺序 + 简单降级，单一引擎失败不阻塞整体闭环
# ============================================================

import os
import sys
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

from config.paths import PROJECT_ROOT as _PROJECT_ROOT
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from engine.recognizer import Recognizer
from engine.explainer import Explainer
from engine.verifier import Verifier
from engine.graph.error_graph import ErrorGraph
from engine.unpack_guard import guard_unpack


@dataclass
class RequestContext:
    """请求级平台上下文（0.33-04 · 显式上下文传播）。
    作为 process() 的可选覆盖：ctx 有值用之、无值回退 Router 实例字段
    （会话级默认）。最高优先级是请求内命中 BYOK 的 provider_config——
    引擎每次 client 调用透传 config，None → settings 全局配置。"""
    learner_id: str = ""
    native_lang: str = ""
    user_level: str = ""
    provider_config: Optional[Dict] = field(default=None)   # {base_url, api_key, model}


def ordered_error(e: dict, version: int = 2) -> dict:
    """契约 error 条目字段白名单 + 稳定顺序（前端不依赖内部实现字段）。
    B0（契约 v2）：v1 的 7 旧键恒序（version<2 时不带新键），v2 追加
    score/verdict/construction_diagnostics——总在但值可 None（软评分缺省不破坏旧调用方）。
    construction_diagnostics 由 B1 产出，B0 恒 None。"""
    base = {
        "fragment": e.get("fragment", ""),
        "correction": e.get("correction", ""),
        "type": e.get("type", ""),
        "type_confident": bool(e.get("type_confident", False)),
        "confidence": float(e.get("confidence", 0.0)),
        "knowledge_point_id": e.get("knowledge_point_id", ""),
        "uncertain": bool(e.get("uncertain", False)),
    }
    if version < 2:
        return base
    base["score"] = e.get("score") if e.get("score") is not None else None
    base["verdict"] = e.get("verdict") if e.get("verdict") is not None else None
    base["construction_diagnostics"] = e.get("construction_diagnostics")  # B1 产出；B0 恒 None
    # A 面：fragment 在原句 input_text 中的字符起点（识别层 locate_fragment 产出；
    #       -1 = 定位失败；v2 可选键，恒在但值可 -1，老客户端忽略不破坏）
    base["offset"] = int(e.get("offset", -1) or -1)
    return base


class Router:
    """轻路由：按固定顺序串三引擎 + 图谱写读，单引擎失败不阻塞整体"""

    def __init__(self, learner_id: str = "default", native_lang: str = "",
                 user_level: str = "HSK3"):
        self.learner_id = learner_id
        self.native_lang = native_lang
        self.user_level = user_level
        self.recognizer = Recognizer()
        self.explainer = Explainer()
        self.verifier = Verifier()
        self.graph = ErrorGraph(learner_id)
        self.graph.load()

    @staticmethod
    def _norm_level(raw) -> int:
        """0.25 起点分层：将 user_level（'HSK3'/'3'/3）归一为 int；非法回退 3。"""
        s = str(raw or "").strip().upper()
        s = s[len("HSK"):] if s.startswith("HSK") else s
        try:
            return max(1, min(6, int(s)))
        except (TypeError, ValueError):
            return 3

    def _level_int(self) -> int:
        """0.25 起点分层：实例字段等级归一（识别/超纲/讲解用）；非法回退 3。"""
        return self._norm_level(self.user_level)

    def set_level(self, user_level: str):
        """0.25：外部同步学习者等级（serve 从画像读取后调用），识别/超纲/讲解随之生效。"""
        self.user_level = user_level
        self._level_int()   # 触发归一（非法值也会回退默认，不抛错）

    def process(self, user_text: str, event_key: str = "",
                ctx: Optional[RequestContext] = None) -> dict:
        """一次偏误纠错闭环：识别 → 讲解 → 图谱 → 复习队列。
        返回「JSON 接缝契约 v2」结构化结果（详见 datasets/docs/JSON-接缝契约-v2.md）：
        纯可序列化 dict，degraded[] 结构化（stage/reason/fatal），前端可直接渲染。
        降级分支见各步 try —— 识别主失败 fatal，其余局部降级不阻塞整体。
        ctx: 可选请求级上下文覆盖（0.33-04 BYOK）。有值用之、无值回退实例字段
        （会话级默认）。provider_config 命中时透传给识别/讲解引擎。

        契约「请求级上下文覆盖」：ctx.learner_id/native_lang/user_level 非空则覆盖
        实例字段（会话级）；为空则沿用实例默认。provider_config 覆盖决定 BYOK 用哪把 Key。
        """
        start = time.time()
        eff_learner = (ctx.learner_id or self.learner_id) if ctx else self.learner_id
        eff_native = (ctx.native_lang or self.native_lang) if ctx else self.native_lang
        eff_level = (ctx.user_level or self.user_level) if ctx else self.user_level
        provider_config = ctx.provider_config if ctx else None
        # 请求级等级归一（非法回退 3），仅本请求识别/讲解用；图谱写入仍按会话级 self.user_level
        eff_level_int = self._norm_level(eff_level)

        result = {"contract_version": "v2", "learner_id": eff_learner,
                  "user_level": eff_level, "native_lang": eff_native,
                  "input_text": user_text, "errors": [], "uncertain": [],
                  "hypotheses": [],
                  "has_error": False, "graph_size": 0, "review_queue": [],
                  "verdict": None, "score": None,
                  "degraded": [], "meta": {"start_ts": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                                     time.gmtime()),
                                           "end_ts": "", "elapsed_ms": 0}}
        degraded = result["degraded"]

        def _notice(stage, reason, fatal=False, **extra):
            d = {"stage": stage, "reason": reason, "fatal": fatal}
            d.update(extra)
            degraded.append(d)

        # 1. 识别引擎（已对齐 2.1 v0.3；LLM 失败时内部已回退规则匹配）
        try:
            # 0.22：native_lang 传入识别（0.21 只接了 explainer/verifier，此路径漏传——
            # 迁移假设与"母语"上下文提示词都依赖它）
            # 0.25：level 传入识别（曾漏传恒用默认 3，超纲宽容判定 + 识别难度失真）
            # 0.33-04：provider_config 存在才透传（与 identify 技能同策略；None 省略，
            # 客户端即回退 settings，语义等价且测试替身零迁移）
            rk = {}
            if provider_config:
                rk["config"] = provider_config
            recog = self.recognizer.recognize(
                user_text, level=eff_level_int, native_lang=eff_native, **rk)
            confirmed = recog.get("errors", [])
            uncertain = recog.get("uncertain", [])
            # 0.22：L1 迁移假设透传（契约 v1 新增可选键，向后兼容；只读不写图谱）
            result["hypotheses"] = recog.get("hypotheses", [])
            # B0：识别层顶层软评分透传（识别主失败早退分支即前述 None 初值；mock 不给 → 保持 None）
            result["verdict"] = recog.get("verdict")
            result["score"] = recog.get("score")
            # 识别层降级透传：_rule_fallback 返回字符串（非列表），两者都要记（契约 §5）
            rd_raw = recog.get("degraded")
            if isinstance(rd_raw, list):
                for rd in rd_raw:
                    _notice("recognize", str(rd))
            elif rd_raw:
                _notice("recognize", str(rd_raw))
            kps_in_sentence = []
        except Exception as e:
            _notice("recognize", str(e), fatal=True)
            try:
                self.graph.save()
            except Exception:
                pass
            result["meta"]["end_ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            result["meta"]["elapsed_ms"] = int((time.time() - start) * 1000)
            return result

        # 2. 对每个已确认偏误：图谱写入 + 讲解（契约 §1）
        for i, err in enumerate(confirmed):
            ev_key = f"{event_key or user_text}#err{i}"
            try:
                graph_write = self.graph.ingest_error(
                    {**err, "sentence": user_text, "level": self.user_level}, ev_key)
            except Exception as e:
                _notice("graph_write", str(e), error_index=i)
                graph_write = {"status": f"write_failed:{e}"}
            item = {"error": ordered_error(err), "explanation": None,
                    "graph_write": graph_write, "verification": None}

            try:
                # B5 事后拦截环（I4）：讲解生成 → 超纲硬查 → 违规重试1次 → 再漏降级标注。
                # generate 闭包把 guard 重试违规清单经 _b5_guard_violations 注入 error，
                # explain 追加约束重生成（主链零改动）。
                ek = {}
                if provider_config:
                    ek["config"] = provider_config

                def _b5_generate(pending):
                    err_for_gen = {**err, "sentence": user_text}
                    if pending:
                        vstr = "、".join(v["word"] for v in pending)
                        err_for_gen["_b5_guard_violations"] = vstr
                    return self.explainer.explain(err_for_gen,
                                                  user_level=eff_level,
                                                  native_lang=eff_native, **ek)

                guard_r = guard_unpack(_b5_generate, learner_level=eff_level_int)
                expl = guard_r["result"]
                if not guard_r["ok"]:
                    # 再漏：标注降级呈现（含超纲词 + 兜底标注），不静默透传违规讲解放给用户
                    expl["degraded_overscope"] = True
                    expl["overscope_violations"] = guard_r["violations"]
                    expl.setdefault("explanation", "")
                item["explanation"] = expl if isinstance(expl, dict) else {"_degraded": True}
            except Exception as e:
                _notice("explain", str(e), error_index=i)
                item["explanation"] = {"_degraded": True, "explanation": "",
                                       "key_points": []}
            kp = err.get("knowledge_point_id")
            if kp:
                kps_in_sentence.append(kp)
            result["errors"].append(item)

        # 3. 待确认偏误 → 待确认队列（契约 §1 uncertain，结构与 error 对齐）
        for i, u in enumerate(uncertain):
            result["uncertain"].append(ordered_error(u))
            try:
                self.graph.ingest_error({**u, "sentence": user_text, "uncertain": True},
                                        f"{event_key or user_text}#unc{i}")
            except Exception as e:
                _notice("graph_write", str(e), error_index=i)

        # 4. 同句 ≥2 已确认偏误 → 混淆边（2.4 §6）
        try:
            self.graph.link_errors_in_sentence(kps_in_sentence,
                                               f"{event_key or user_text}#sentence")
        except Exception as e:
            _notice("confusion_edge", str(e))

        result["has_error"] = bool(result["errors"])
        result["graph_size"] = len(self.graph)
        try:
            result["review_queue"] = self.graph.get_review_queue()
        except Exception as e:
            result["review_queue"] = []
            _notice("review_queue", str(e))
        try:
            self.graph.save()
        except Exception as e:
            _notice("graph_save", str(e))
        result["meta"]["end_ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        result["meta"]["elapsed_ms"] = int((time.time() - start) * 1000)
        return result

    def verify_rephrase(self, explanation: str, key_points: list, restatement: str,
                        uncertain: bool = False, bias_ref: Optional[dict] = None,
                        event_key: str = "", ctx: Optional[RequestContext] = None) -> dict:
        """复述验证（2.3 v0.3）：逐点判定 + 规则聚合 + 写回图谱。
        0.21：反馈语言跟随 Router.native_lang（教学层语言，中文要点/判定逻辑不变）。
        ctx: 可选请求级上下文覆盖（0.33-04 BYOK）；provider_config 命中时透传验证引擎。"""
        eff_native = (ctx.native_lang or self.native_lang) if ctx else self.native_lang
        provider_config = ctx.provider_config if ctx else None
        vk = {}
        if provider_config:
            vk["config"] = provider_config
        return self.verifier.verify(explanation, key_points, restatement,
                                    uncertain=uncertain, bias_ref=bias_ref,
                                    event_key=event_key, commit_graph=True,
                                    native_lang=eff_native, **vk)

    def get_review_queue(self):
        """委托图谱读接口：按 priority 降序的可复习 KP 列表"""
        return self.graph.get_review_queue()