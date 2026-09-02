# ============================================================
# engine/generation/generator.py
# 生成引擎全流程编排器（0.16 落地）
# 流水线：authoring 槽位 → LLM.chat_json_strict → 确定性校验 → 自愈重试 → 可选写回
# 对齐铁律：不静默透传坏结果 / 宁漏勿错 / 不污染图谱 / 图谱唯一权威。
# 纯标准库，复用既有 LLMClient + GenerationUnitValidator，无第三方依赖。
# ============================================================

import os
import sys
import time
from typing import Any, Dict, List, Optional

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from engine.generation.authoring import (
    build_explain_prompt,
    build_practice_prompt,
)
from engine.generation.validator import GenerationUnitValidator, UnitDiagnostic
from engine.llm.client import LLMClient, JSONStrictError

# 生成引擎给 LLM 的稳定系统指令（身份 + 只产 JSON，不靠"愿望堆叠"）
_SYSTEM = (
    "你是一位 HSK 中文教学专家，严格按 user 提示词中的 schema 输出 JSON 生成单元。"
    "只输出一个合法 JSON object，不含任何多余解释、注释或代码块。"
)

_SCENE_TYPES = ("explain", "practice")


def _repair_feedback(diagnostics: List[UnitDiagnostic]) -> str:
    """把诊断序列化为自愈反馈（Archify P2：稳定 code + subject + evidence + fixes）。
    修复优先级先正确性后结构（P3，诊断已按 repair_order 升序）。"""
    parts = ["【上一轮生成未通过契约校验，请务必按以下修复方向重生成完整 JSON】："]
    for d in sorted(diagnostics, key=lambda x: x.repair_order()):
        cd = d.to_dict()
        line = f"- [{cd['code']}] {cd['subject']}: {cd['message']}"
        if cd["evidence"] is not None:
            line += f"（实测值={cd['evidence']}）"
        if cd["supported_fixes"]:
            line += "  修复方法: " + "；".join(cd["supported_fixes"])
        parts.append(line)
    parts.append(
        "修复优先级：先确保字段/类型正确，再补语义完整，最后做结构优化。"
        "请重新输出一条完整、合法、不带上一轮错误的新 JSON，不要复述上一轮内容。"
    )
    return "\n".join(parts)


class GenerationEngine:
    """生成编排器：authoring → LLM → 校验 → 自愈 → 写回。

    不静默透传坏结果：连续修复仍不过，返回结构化降级（status=degraded），
    内含 diagnostics[]（稳定 code）+ attempts，unit 给 best_effort 供前端口径。
    写回默认关闭（守"图谱唯一权威、无自由发散"）；仅 write_back=True 且命中 kp 才写。
    """

    def __init__(self, client: Optional[LLMClient] = None,
                 validator: Optional[GenerationUnitValidator] = None,
                 graph=None, writeback=None, search_tool=None):
        self.client = client or LLMClient()
        self.validator = validator or GenerationUnitValidator()
        self.graph = graph                    # 可选 ErrorGraph（写回用）
        self.writeback = writeback            # 可选 M8 Writeback（两段式确认写回）
        self._search_tool = search_tool       # 可选联网检索（None → 懒加载 tools.call_tool）
        self._tool_attempted = search_tool is not None

    # ---------------- 入口 ----------------
    def generate_unit(self, unit_type: str, context: Dict[str, Any],
                      max_repairs: int = 1, write_back: bool = False) -> dict:
        """生成一条生成单元。

        返回：{"ok":True,"unit":{...}, "attempts":N}  或
             {"ok":False,"status":"degraded","message","unit","diagnostics","attempts"}
        """
        if unit_type not in _SCENE_TYPES:
            return {
                "ok": False, "status": "degraded",
                "message": f"不支持的生成单元类型: {unit_type}（仅 {_SCENE_TYPES} 首发）",
                "unit": None, "diagnostics": [], "attempts": 0,
            }
        if max_repairs < 0:
            max_repairs = 0

        # 路 2（M9 工具接入）：explain 单元可选联网研究增强 —— 只搜一次，重试沿用同一份资料
        research_meta = {"used": False, "status": "not_applicable"}
        reference_sources = ""
        if unit_type == "explain":
            reference_sources, research_meta = self._research_explain(context)

        prompt = self._build_prompt(unit_type, context, reference_sources=reference_sources)
        client = self.client or LLMClient()

        unit: Any = None
        diagnostics: List[UnitDiagnostic] = []
        best_effort = None
        attempts = 0

        # 自愈循环：最多 max_repairs+1 次尝试（首轮 + 修复重试轮数）
        for attempt in range(max_repairs + 1):
            user = prompt if attempt == 0 else prompt + "\n\n" + _repair_feedback(diagnostics)
            attempts = attempt + 1
            try:
                parsed = client.chat_json_strict(_SYSTEM, user, temperature=0.2, retries=1)
            except JSONStrictError as e:
                # 解析坏 JSON：归类为解析失败诊断，继续自愈
                diagnostics = [UnitDiagnostic(
                    "code_invalid_json", "root",
                    "LLM 结构化输出无法解析为合法 JSON", evidence=str(e),
                    fixes=["要求按 schema 输出严格 JSON（无注释/尾逗号）"])]
                continue
            except Exception as e:  # 网络/Key 等底层失败：结构化降级，不裸抛
                diagnostics = [UnitDiagnostic(
                    "code_llm_failed", "root",
                    f"LLM 调用失败（待上层按需降级）: {e}", evidence=type(e).__name__,
                    fixes=["检查 API Key/网络，或切换 provider"])]
                break

            best_effort = parsed
            unit = parsed
            diagnostics = self.validator.validate(parsed)
            if not diagnostics:  # 合法单元
                out = {"ok": True, "unit": unit, "attempts": attempts,
                       "research": research_meta}
                if write_back and unit_type == "practice":
                    out["writeback"] = self._write_back(unit)
                return out

        # 自愈耗尽仍不过（含底层失败）→ 结构化降级
        return {
            "ok": False, "status": "degraded",
            "message": "生成单元经自愈重试仍未通过契约校验，返回 best_effort 供前端口径",
            "unit": best_effort,
            "diagnostics": [d.to_dict() for d in diagnostics],
            "attempts": attempts,
            "research": research_meta,
        }

    # ---------------- 内部：联网研究增强（路 2 / M9 工具接入） ----------------
    def _get_search_tool(self):
        """懒加载默认检索工具（tools.call_tool，call_tool 兼容签名）；失败返回 None（禁用增强）。"""
        if self._tool_attempted:
            return self._search_tool
        self._tool_attempted = True
        try:
            from tools import call_tool
        except Exception:  # noqa: BLE001  tools 包不可用 → 无工具，禁用增强
            call_tool = None
        self._search_tool = call_tool
        return call_tool

    def _research_explain(self, context: Dict[str, Any]):
        """explain 单元研究增强：有明确 for_keypoint 且检索可用才搜；任一环节失败静默降级。
        返回 (reference_sources:str, meta:dict)，meta 供契约/测试观测 use 状态。"""
        if not context.get("research", True):   # 显式关闭
            return "", {"used": False, "status": "skip"}
        fk = str(context.get("for_keypoint") or "").strip()
        if not fk:
            return "", {"used": False, "status": "skip"}
        tool = self._get_search_tool()
        if tool is None:
            return "", {"used": False, "status": "no_tool"}
        try:
            res = tool("web_search", params={"query": fk, "max_results": 3})
        except Exception as e:  # noqa: BLE001 工具异常不绝不阻塞生成
            return "", {"used": False, "status": "error", "detail": str(e)}
        if not (isinstance(res, dict) and res.get("ok")):
            status = (res or {}).get("status", "error") if isinstance(res, dict) else "error"
            return "", {"used": False, "status": status}
        refs = [r for r in (res.get("results") or [])
                if isinstance(r, dict) and (r.get("title") or r.get("snippet"))]
        if not refs:
            return "", {"used": False, "status": "empty"}
        text = "\n".join(
            f"· {r.get('title','(无题)')}：{r.get('snippet','')}"
            for r in refs[:3])
        return text, {"used": True, "status": "ok", "n_sources": len(refs)}

    # ---------------- 内部 ----------------
    def _build_prompt(self, unit_type: str, context: Dict[str, Any],
                      reference_sources: str = "") -> str:
        if unit_type == "explain":
            return build_explain_prompt(
                for_keypoint=context.get("for_keypoint", ""),
                teaching_objective=context.get("teaching_objective", ""),
                curriculum_at=context.get("curriculum_at", ""),
                previous_speech=context.get("previous_speech", ""),
                all_titles=context.get("all_titles") or [],
                language_directive=context.get("language_directive", ""),
                self_language=context.get("self_language", "zh"),
                reference_sources=reference_sources,
            )
        for_keypoints = context.get("for_keypoints") or []
        if isinstance(for_keypoints, str):
            for_keypoints = [for_keypoints]
        targets_errors = context.get("targets_errors") or []
        if isinstance(targets_errors, str):
            targets_errors = [{"fragment": targets_errors}]
        # authoring 槽位是字符串列表；dict（含 knowledge_point_id）转成 fragment 串
        target_strings = [t if isinstance(t, str) else (t.get("fragment", "") or str(t))
                          for t in targets_errors]
        return build_practice_prompt(
            for_keypoints=[k if isinstance(k, str) else str(k) for k in for_keypoints],
            targets_errors=target_strings,
            task_kind=context.get("task_kind", "mcq"),
            curriculum_at=context.get("curriculum_at", ""),
            previous_speech=context.get("previous_speech", ""),
            all_titles=context.get("all_titles") or [],
            language_directive=context.get("language_directive", ""),
        )

    def _write_back(self, unit: dict) -> List[str]:
        """practice 命中 targets_errors 且有 knowledge_point_id → 写回图谱（两段式确认）。
        默认边界：未给 graph/writeback 或未命中 kp → 不写（返回空列表）。"""
        written: List[str] = []
        if self.writeback is None and self.graph is None:
            return written
        for err in unit.get("targets_errors", []) or []:
            if not isinstance(err, dict):
                continue
            kp = err.get("knowledge_point_id")
            if not kp:
                continue
            evidence = err.get("fragment", "")
            if self.writeback is not None:
                self.writeback.on_confirmed(kp, evidence=evidence)
            elif hasattr(self.graph, "ingest_error"):
                self.graph.ingest_error(
                    err, event_key=self._event_key())
            written.append(kp)
        return written

    @staticmethod
    def _event_key() -> str:
        return "gen_" + time.strftime("%Y%m%d%H%M%S", time.localtime())