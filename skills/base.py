# ============================================================
# Skill 抽象基类（skills/base.py）
# M1 Skill 化 · 程序化双形态
# - run(context)          : 调引擎执行（薄适配，不改引擎）
# - to_manifest()        : 渲染完整四段式详情（供 LLM 调用前查看，对应 SKILL.md 层）
# - metadata.summary     : 一句话作用（list_all 紧凑清单用）
# - 对齐官方渐进式披露：list_all=description 层 / to_manifest=SKILL.md 层 / run=scripts 层
# - 零第三方依赖（仅标准库）
# ============================================================

import json
from typing import Any, Dict, List, Optional, Tuple


def normalize_level_int(raw: Any) -> int:
    """用户层级归一（int 1-6）。接受 int / '3' / 'HSK3'；非法回退默认 3。

    规范样板共享函数：所有带 level 的技能统一入口，再按引擎签名转换。
    """
    if isinstance(raw, int):
        return raw if 1 <= raw <= 6 else 3
    s = str(raw).strip().upper()
    if s.startswith("HSK"):
        s = s[3:]
    try:
        v = int(s)
    except (TypeError, ValueError):
        return 3
    return v if 1 <= v <= 6 else 3


def normalize_level_label(raw: Any) -> str:
    """层级的引擎标签（'HSK{n}'）。供讲解释引擎（其签名期望 'HSK3' 形式）。"""
    return f"HSK{normalize_level_int(raw)}"


def _coerce_type(declared: str, value: Any) -> Tuple[Optional[Any], Optional[str]]:
    """按声明类型做安全纠正；返回 (纠正值, 失败原因)。失败时纠正值为 None。

    宽松取向：LLM 常见错型（数字/布尔传成字符串）自动纠正，避免浪费一轮
    自纠；不可安全纠正的（数组/对象形态错、bool 硬当 int）返回原因喂回。
    注意 Python bool 是 int 子类，须先排除再判数字。
    """
    if declared == "string":
        if isinstance(value, str):
            return value, None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value), None
        return None, f"应为 string，收到 {type(value).__name__}"
    if declared == "integer":
        if isinstance(value, bool):
            return None, "应为 integer，收到 boolean"
        if isinstance(value, int):
            return value, None
        if isinstance(value, float) and value.is_integer():
            return int(value), None
        if isinstance(value, str):
            s = value.strip()
            try:
                return int(float(s)) if ("." in s or "e" in s.lower()) else int(s), None
            except ValueError:
                return None, f"应为 integer，无法从 '{value}' 转换"
        return None, f"应为 integer，收到 {type(value).__name__}"
    if declared == "number":
        if isinstance(value, bool):
            return None, "应为 number，收到 boolean"
        if isinstance(value, (int, float)):
            return value, None
        if isinstance(value, str):
            try:
                return float(value), None
            except ValueError:
                return None, f"应为 number，无法从 '{value}' 转换"
        return None, f"应为 number，收到 {type(value).__name__}"
    if declared == "boolean":
        if isinstance(value, bool):
            return value, None
        if isinstance(value, str):
            if value.strip().lower() == "true":
                return True, None
            if value.strip().lower() == "false":
                return False, None
        if isinstance(value, int) and value in (0, 1):
            return bool(value), None
        return None, f"应为 boolean，收到 {value!r}"
    if declared == "array":
        if isinstance(value, list):
            return value, None
        return None, f"应为 array，收到 {type(value).__name__}（不要自动包装，请传 JSON 数组）"
    if declared == "object":
        if isinstance(value, dict):
            return value, None
        return None, f"应为 object，收到 {type(value).__name__}（请传 JSON 对象）"
    return value, None   # 未知类型声明：放行（宽松）


class Skill:
    """技能抽象基类：metadata + schema + run + to_manifest。

    子类只需实现 run(context)；metadata/输入输出注释供 LLM 发现与调用。
    """

    metadata: Dict[str, Any] = {
        "name": "",
        "version": "",
        "summary": "",
        "triggers": [],     # ["何时该用（正例）"]
        "guardrails": [],   # ["何时不要用（反例）"]
        "input": {},        # {"field": "说明 + 示例"}
        "output": {},       # 返回结构说明（自然语言）
    }
    input_schema: Dict[str, Any] = {}
    output_schema: Dict[str, Any] = {}

    def __init__(self):
        if not self.metadata.get("name"):
            raise NotImplementedError(f"{type(self).__name__} 必须定义 metadata['name']")

    # ---------------- 子类实现 ----------------
    def run(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """调引擎执行入参映射。context 为已解析/注入的入参 dict。子类必须实现。"""
        raise NotImplementedError

    # ---------------- 校验（0.18 接入项④a） ----------------
    def validate_params(self, params: Any) -> Tuple[Dict[str, Any], List[str]]:
        """按 input_schema 校验入参并做安全纠正。返回 (corrected, errors)。

        errors 非空 → 调用方不应执行 run（喂回 LLM 下一轮自纠，多数模型
        二轮即可修正——对标 Anthropic is_error tool_result 模式）。
        规则（宽松取向；技能内部本就有兜底防御，校验只拦"确定跑不对"的）：
        - required 缺失或空值 → error
        - 类型不符 → 可安全纠正的自动纠正（'3'→3、'true'→True、数字→字符串）；
          不可纠正 → error
        - enum 越界 → error（含大小写不敏感匹配）
        - 未声明字段 → 放行（planner 常带上下文字段，宁宽勿严）
        - schema 为空 / params 非 dict → params 非 dict 报错，空 schema 全放行
        """
        if not isinstance(params, dict):
            return {}, [f"params 应为 JSON 对象，收到 {type(params).__name__}"]
        if not self.input_schema:
            return params, []

        corrected = dict(params)
        errors: List[str] = []
        props = self.input_schema.get("properties", {}) or {}

        for field in self.input_schema.get("required", []) or []:
            v = corrected.get(field)
            if v is None or (isinstance(v, str) and not v.strip()):
                decl = props.get(field, {})
                expect = decl.get("type", "any")
                errors.append(f"缺少必填字段 '{field}'（{self.metadata['name']} 需要"
                              f" {field}: {expect}）")

        for key, decl in props.items():
            if key not in corrected or corrected[key] is None:
                continue
            enum = decl.get("enum")
            if enum:
                v = corrected[key]
                if v in enum:
                    continue
                if isinstance(v, str) and v.strip().lower() in [
                        str(e).lower() for e in enum]:
                    corrected[key] = next(e for e in enum
                                          if str(e).lower() == v.strip().lower())
                    continue
                errors.append(f"'{key}' 必须是 {enum} 之一，收到 {v!r}")
                continue
            declared = decl.get("type")
            if not declared:
                continue
            fixed, why = _coerce_type(declared, corrected[key])
            if why:
                errors.append(f"'{key}' {why}")
            else:
                corrected[key] = fixed

        return corrected, errors

    # ---------------- 公共（表达层面） ----------------
    def to_manifest(self) -> Dict[str, Any]:
        """渲染完整四段式详情（渐进式披露第二级：decision 前展开）。"""
        m = self.metadata
        return {
            "name": m["name"],
            "version": m["version"],
            "summary": m["summary"],
            "triggers": m.get("triggers", []),
            "guardrails": m.get("guardrails", []),
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "input": m.get("input", {}),
            "output": m.get("output", {}),
        }

    def to_json(self) -> str:
        """manifest 的 JSON 序列化（供 LLM 调用方落地/调试）。"""
        return json.dumps(self.to_manifest(), ensure_ascii=False, indent=2)

    def __repr__(self):
        return f"<Skill {self.metadata.get('name')}@{self.metadata.get('version')}>"