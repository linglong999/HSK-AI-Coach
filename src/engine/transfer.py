# ============================================================
# engine/transfer.py
# 0.22 方向1 · L1 母语迁移归因（迁移带）
# 0.35-05 拆分后的编排门面：match_one / match 负责"锚点命中 + 签名裁决 + 产假设"，
# 加载/路由（transfer_loader.py）与签名判定（transfer_matcher.py）已独立成模块。
#   - 纯确定性匹配（规则表 + fragment/correction 签名），零 LLM 调用
#   - 假设 status 恒为 "candidate"，绝不进偏误图谱 confirmed 层（宁漏勿错）
#   - native_lang 为 zh → 返回 []（不追因）
#   - 对外符号在此 re-export，旧 import（recognizer/explainer/测试）保持可用
# ============================================================

from engine.transfer_loader import (
    RULES_KO_PATH,
    RULES_PATH,
    _norm_l1,
    load_rules,
    validate_rules,
)
from engine.transfer_loader import _rules_for_l1  # noqa: F401 路由供编排用
from engine.transfer_matcher import _SIGNATURES


def match_one(error: dict, native_lang: str) -> dict:
    """对单个偏误产出迁移假设；无命中返回 None。
    三层判定（宁漏勿错）：
      ① l1 分流 —— 按母语选规则文件：en→en 表、ko→ko 表、未知/空→en 降级、zh→不归因
      ② 锚点命中 —— knowledge_point_id ∈ kp_anchors 或 type ∈ types
      ③ 签名命中 —— fragment/correction 满足该规则 sig 字段引用的确定性签名
    三者同时满足才产假设；每条偏误至多一条（按规则表顺序取首个命中）。"""
    if not isinstance(error, dict):
        return None
    l1 = _norm_l1(native_lang)
    if l1 == "zh":
        return None
    kp = str(error.get("knowledge_point_id", "") or "")
    etype = str(error.get("type", "") or "")
    for rule in _rules_for_l1(l1):
        kp_hit = bool(kp) and kp in rule.get("kp_anchors", [])
        type_hit = etype in rule.get("types", [])
        if not (kp_hit or type_hit):
            continue
        sig = _SIGNATURES.get(rule.get("sig"))
        if sig and sig(error):
            return {
                "rule_id": rule["rule_id"],
                "l1": rule.get("l1", l1),
                "conf": float(rule.get("conf", 0.4)),
                "status": "candidate",
                "fragment": error.get("fragment", ""),
                "l1_anchor": rule.get("l1_anchor", ""),
                "zh_signature": rule.get("zh_signature", ""),
                "correction": error.get("correction", ""),
            }
    return None


def match(errors: list, native_lang: str) -> list:
    """对一批已确认偏误产出迁移假设列表（识别结果 hypotheses[] 的唯一来源）。
    只对确认层偏误做归因（uncertain 本身未站稳，叠加归因会放大不确定性）。
    zh 学习者一律不归因；en/ko 走各自规则表，未知/空母语走 en 通用底座降级。"""
    if _norm_l1(native_lang) == "zh":
        return []
    return [h for h in (match_one(e, native_lang) for e in errors or []) if h]