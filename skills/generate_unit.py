# ============================================================
# Skill: generate_unit（生成费曼讲解/巩固练习单元/场景对话 brief）
# 0.17 统一能力契约 · 自由技能清单 #6
# 包 GenerationEngine.generate_unit：生成费曼讲解/练习单元（0.16 全流程）
#   0.22 方向2 · unit_type 扩 "dialogue"：扩写预置场景为更丰满的场景 brief（D2.2）
# - run(params) 期望 params['unit_type'] + params['context']；context 内字段透传
# - 写回默认关；走引擎内部两段式确认逻辑，技能侧不改动
# ============================================================

from typing import Any, Dict

from skills.base import Skill


class GenerateUnitSkill(Skill):
    metadata: Dict[str, Any] = {
        "name": "generate_unit",
        "version": "0.2",
        "summary": "生成一个费曼讲解单元、巩固练习题单元，或扩写一份场景对话 brief",
        "triggers": [
            "学习者需要把某偏误/知识点讲透（费曼式）",
            "学习者需要针对已确认偏误做一道巩固练习",
            "学习者需要把某个中文学习场景扩写成更丰满的场景对话 brief（0.22 方向2）",
        ],
        "input": {
            "unit_type": "必填，explain|practice|dialogue",
            "context": "对象，含 for_keypoint / for_keypoints / targets_errors / "
                       "all_titles / previous_speech / curriculum_at / task_kind，"
                       "或（dialogue）scene_id / level / kp_ids / seed 等",
        },
        "output": "{ok, unit, attempts, research, unit_type, diagnostics?, writeback?}（引擎结构化）",
        "guardrails": [
            "仅 explain/practice/dialogue 三种类型；未知类型不建",
            "practice 写回图谱仅在显式开启且命中已确认偏误时（防污染）",
            "dialogue 只产场景 brief（扩写/注入用），不写图谱",
            "生成失败返回结构化 degraded，不阻塞对话",
        ],
    }
    input_schema: Dict[str, Any] = {
        "type": "object",
        "required": ["unit_type"],
        "properties": {
            "unit_type": {"enum": ["explain", "practice", "dialogue"]},
            "context": {"type": "object"},
            "max_repairs": {"type": "integer"},
            "write_back": {"type": "boolean"},
        },
    }
    output_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "ok": {"type": "boolean"},
            "status": {"type": "string"},
            "message": {"type": "string"},
            "unit": {"type": "object"},
            "attempts": {"type": "integer"},
            "research": {"type": "object"},
            "unit_type": {"type": "string"},
            "diagnostics": {"type": "array"},
            "writeback": {"type": "object"},
        },
    }

    def __init__(self, engine=None):
        super().__init__()
        self._engine = engine

    def _get_engine(self):
        if self._engine is not None:
            return self._engine
        from engine.generation.generator import GenerationEngine
        self._engine = GenerationEngine()
        return self._engine

    def run(self, context: Dict[str, Any]) -> Dict[str, Any]:
        unit_type = str(context.get("unit_type") or "explain").strip()
        if unit_type not in ("explain", "practice", "dialogue"):
            return {"ok": False, "status": "degraded",
                    "message": f"unsupported unit_type: {unit_type}"}
        ctx = context.get("context") or {}
        if not isinstance(ctx, dict):
            ctx = {k: v for k, v in context.items()
                   if k not in ("unit_type", "max_repairs", "write_back")}
        return self._get_engine().generate_unit(
            unit_type, ctx,
            max_repairs=int(context.get("max_repairs") or 1),
            write_back=bool(context.get("write_back", False)),
        )


__all__ = ["GenerateUnitSkill"]