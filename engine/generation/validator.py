# ============================================================
# engine/generation/validator.py
# 生成单元 v1 · 契约口径校验器（Archify P2：稳定 code + evidence + 修复定序）
# 借鉴 Archify：校验不笼统报错——每条问题含 稳定code + 精确subject + evidence(实测值)，
#   并给 supportedFixes；repair order 先 schema 正确性 → 再语义完整 → 最后结构优化。
# 本校验器是"生成单元校验器"的可移植样板，与 0.14-M9 同一哲学（LLM 产出 typed JSON →
#   确定性校验）。纯标准库，无 LLM 依赖。
# ============================================================

import json
import re
from typing import Any, Dict, List, Optional

from engine.generation.authoring import (
    EXPLAIN_FIELDS,
    PRACTICE_FIELDS,
    POSTPONED_FIELDS,
    PRACTICE_TASK_KINDS,
    SCENE_TYPES,
)

# repair order：先正确性(0-2) → 再语义(3-4) → 最后结构优化(5)
_REPAIR_ORDER = {
    "code_invalid_json": 0,
    "code_unknown_type": 0,
    "code_missing_required": 1,
    "code_field_wrong_type": 1,
    "code_keypoints_empty": 2,
    "code_forbidden_missing": 2,
    "code_postponed_not_null": 3,
    "code_title_dup": 4,
    "code_title_too_long": 5,
    "code_context_mismatch": 3,
    "code_task_kind_invalid": 1,
}


class UnitDiagnostic:
    """一条校验诊断：稳定 code + subject + evidence + supportedFixes（Archify P2）。"""

    def __init__(self, code: str, subject: str, message: str,
                 evidence: Any = None, fixes: Optional[List[str]] = None):
        self.code = code
        self.subject = subject
        self.message = message
        self.evidence = evidence
        self.fixes = fixes or []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "repair_order": _REPAIR_ORDER.get(self.code, 5),
            "subject": self.subject,
            "message": self.message,
            "evidence": self.evidence,
            "supported_fixes": self.fixes,
        }

    def __repr__(self):  # pragma: no cover - 仅调试
        return f"<UnitDiagnostic {self.code}@{self.subject} order={self.repair_order()}>"

    def repair_order(self) -> int:
        return _REPAIR_ORDER.get(self.code, 5)


class GenerationUnitValidator:
    """校验生成单元（explain/practice）是否满足 schema v1 契约口径。

    确定性校验，非 LLM。输入 dict 或 JSON 字符串。输出诊断列表（按 repair_order 升序）。
    """

    def validate(self, unit: Any) -> List[UnitDiagnostic]:
        diagnostics: List[UnitDiagnostic] = []
        if isinstance(unit, str):
            unit = self._parse_json(unit)
            if unit is None:
                return [UnitDiagnostic("code_invalid_json", "root",
                                       "生成单元不是合法 JSON", evidence=None,
                                       fixes=["要求生成引擎输出严格 JSON（无注释/尾逗号）"])]
            assert isinstance(unit, dict)

        if not isinstance(unit, dict):
            return [UnitDiagnostic("code_invalid_json", "root",
                                   "生成单元应为 JSON object", evidence=type(unit).__name__,
                                   fixes=["要求输出 JSON object"])]

        utype = unit.get("type")
        if utype not in ("explain", "practice"):
            return [UnitDiagnostic("code_unknown_type", "type",
                                   f"未知单元类型: {utype}（仅 explain/practice 首发）",
                                   evidence=utype, fixes=[f"TypeError 应为 {SCENE_TYPES[0]} 或 {SCENE_TYPES[1]}"])]

        # 字段口径差异
        if utype == "explain":
            self._check_fields(unit, EXPLAIN_FIELDS, diagnostics)
            self._check_keypoints(unit, diagnostics)
            self._check_string(unit, "teachingObjective", diagnostics)
        else:  # practice
            self._check_fields(unit, PRACTICE_FIELDS, diagnostics)
            self._check_keypoints(unit, diagnostics)
            self._check_task_kind(unit, diagnostics)
            if isinstance(unit.get("questionCount"), int):
                if unit["questionCount"] > 10:
                    diagnostics.append(UnitDiagnostic(
                        "code_question_count_high", "questionCount",
                        "questionCount 超过上限 10", evidence=unit["questionCount"],
                        fixes=["questionCount ≤ 10（防御题量失控）"]))

        # 后置字段必须为 null
        for f in POSTPONED_FIELDS:
            if f in unit and unit[f] is not None:
                diagnostics.append(UnitDiagnostic(
                    "code_postponed_not_null", f,
                    f"后置字段 {f} 应置 null（仅类型位），不得生成内容",
                    evidence=unit[f], fixes=[f"{f} 置 null"]))

        self._check_context(unit, diagnostics)

        # Archify P3：修复有定序——按 repair_order 升序返回（先正确性→再语义→后结构）
        diagnostics.sort(key=lambda d: d.repair_order())
        return diagnostics

    # ---- 内部判定 ----
    def _parse_json(self, s: str) -> Optional[Dict[str, Any]]:
        try:
            obj = json.loads(s)
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None

    def _check_fields(self, unit: Dict, fields: tuple, diagnostics: List[UnitDiagnostic]) -> None:
        for f in fields:
            if f not in unit:
                diagnostics.append(UnitDiagnostic(
                    "code_missing_required", f, f"缺少必选字段 {f}", evidence=None,
                    fixes=[f"补 {f}（字段口径与 schema v1 一致）"]))
            elif f == "forbidden_errors" and unit[f] is None:
                diagnostics.append(UnitDiagnostic(
                    "code_forbidden_missing", "forbidden_errors",
                    "forbidden_errors 允许空数组但不可缺省/为 null（护栏失效）",
                    evidence=unit[f], fixes=["forbidden_errors 置为 [] 而非缺省"]))

    def _check_keypoints(self, unit: Dict, diagnostics: List[UnitDiagnostic]) -> None:
        kps = unit.get("keyPoints")
        if not isinstance(kps, list) or len(kps) == 0:
            diagnostics.append(UnitDiagnostic(
                "code_keypoints_empty", "keyPoints",
                "keyPoints 必须为非空数组（只装正向目标知识）",
                evidence=kps, fixes=["至少给 1 条正向知识点"]))

    def _check_string(self, unit: Dict, f: str, diagnostics: List[UnitDiagnostic]) -> None:
        v = unit.get(f)
        if v is not None and not isinstance(v, str):
            diagnostics.append(UnitDiagnostic(
                "code_field_wrong_type", f, f"{f} 应为 string", evidence=type(v).__name__,
                fixes=[f"{f} 改为 string"]))

    def _check_task_kind(self, unit: Dict, diagnostics: List[UnitDiagnostic]) -> None:
        tk = unit.get("task_kind")
        if tk not in PRACTICE_TASK_KINDS:
            diagnostics.append(UnitDiagnostic(
                "code_task_kind_invalid", "task_kind",
                f"task_kind 非法: {tk}（须为枚举之一）", evidence=tk,
                fixes=[f"task_kind ∈ {list(PRACTICE_TASK_KINDS)}"]))

    def _check_context(self, unit: Dict, diagnostics: List[UnitDiagnostic]) -> None:
        ctx = unit.get("context")
        if ctx is None:
            diagnostics.append(UnitDiagnostic(
                "code_missing_required", "context", "缺少 context", evidence=None,
                fixes=["补 context 对象"]))
            return
        if not isinstance(ctx, dict):
            diagnostics.append(UnitDiagnostic(
                "code_field_wrong_type", "context", "context 应为对象",
                evidence=type(ctx).__name__, fixes=["context 改为 object"]))
            return
        if "curriculumAt" not in ctx:
            diagnostics.append(UnitDiagnostic(
                "code_missing_required", "context.curriculumAt",
                "context.curriculumAt 必填（只读图谱位置）", evidence=None,
                fixes=["补 context.curriculumAt"]))