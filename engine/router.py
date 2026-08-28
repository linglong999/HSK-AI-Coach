# ============================================================
# 轻路由（router）—— 0.6.2 轻路由（Router+Chain）
# 按固定顺序串三引擎：识别 → 讲解 → 验证（写回图谱）；加简单降级分支。
# 取代旧"编排器中心调度多 Agent"（0.6 决策块7：轻路由，防漂移/循环）。
# - 写死顺序 + 简单降级，单一引擎失败不阻塞整体闭环
# ============================================================

import os
import sys
import time
from typing import Optional

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from engine.recognizer import Recognizer
from engine.explainer import Explainer
from engine.verifier import Verifier
from engine.graph.error_graph import ErrorGraph


def ordered_error(e: dict) -> dict:
    """契约 §1 error 字段白名单 + 稳定顺序（前端不依赖内部实现字段）。"""
    return {
        "fragment": e.get("fragment", ""),
        "correction": e.get("correction", ""),
        "type": e.get("type", ""),
        "type_confident": bool(e.get("type_confident", False)),
        "confidence": float(e.get("confidence", 0.0)),
        "knowledge_point_id": e.get("knowledge_point_id", ""),
        "uncertain": bool(e.get("uncertain", False)),
    }


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

    def process(self, user_text: str, event_key: str = "") -> dict:
        """一次偏误纠错闭环：识别 → 讲解 → 图谱 → 复习队列。
        返回「JSON 接缝契约 v1」结构化结果（详见 datasets/docs/JSON-接缝契约-v1.md）：
        纯可序列化 dict，degraded[] 结构化（stage/reason/fatal），前端可直接渲染。
        降级分支见各步 try —— 识别主失败 fatal，其余局部降级不阻塞整体。
        """
        start = time.time()
        result = {"contract_version": "v1", "learner_id": self.learner_id,
                  "user_level": self.user_level, "native_lang": self.native_lang,
                  "input_text": user_text, "errors": [], "uncertain": [],
                  "has_error": False, "graph_size": 0, "review_queue": [],
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
            recog = self.recognizer.recognize(user_text)
            confirmed = recog.get("errors", [])
            uncertain = recog.get("uncertain", [])
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
                expl = self.explainer.explain({**err, "sentence": user_text},
                                              user_level=self.user_level,
                                              native_lang=self.native_lang)
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
                        event_key: str = "") -> dict:
        """复述验证（2.3 v0.3）：逐点判定 + 规则聚合 + 写回图谱"""
        return self.verifier.verify(explanation, key_points, restatement,
                                    uncertain=uncertain, bias_ref=bias_ref,
                                    event_key=event_key, commit_graph=True)

    def get_review_queue(self):
        """委托图谱读接口：按 priority 降序的可复习 KP 列表"""
        return self.graph.get_review_queue()