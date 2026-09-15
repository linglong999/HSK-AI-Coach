# ============================================================
# engine/transfer_loader.py
# 0.35-05 · L1 迁移归因 —— 规则加载模块（与 transfer_matcher.py 拆分，单一职责）
# 只负责"声明式规则表资产"的读取、按母语路由、进程内缓存、宽松降级 + 严格校验。
#   - JSON 是分发资产（datasets/transfer_rules_*.json），data/ 被 gitignore
#   - 运行时宽松（缺失/损坏 → 空表或降级备用表，不抛错阻断服务）
#   - 校验时严格（validate_rules 暴露，供测试/工具显式调用，运行时 load 不调用）
# ============================================================

import json
import os
import warnings

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES_PATH = os.path.join(_PROJECT_ROOT, "datasets", "transfer_rules_en.json")
RULES_KO_PATH = os.path.join(_PROJECT_ROOT, "datasets", "transfer_rules_ko.json")

# 已有独立规则表的母语（其余/空/未收录统一降级到 en 通用底座）
SUPPORTED_L1 = ("en", "ko")

# 语言别名归一（边界鲁棒：客户端/旧测试可能送 "英语"/"English"/"한국어" 等）
_L1_ALIASES = {"english": "en", "英语": "en", "英文": "en",
               "chinese": "zh", "中文": "zh", "汉语": "zh", "汉语官话": "zh",
               "korean": "ko", "韩语": "ko", "韓語": "ko", "韩国语": "ko", "한국어": "ko"}

# 规则必须携带的字段（校验基准；sample 内含中原样出现在每条）
_REQUIRED_KEYS = ("rule_id", "sig", "kp_anchors", "types", "nature",
                  "l1_anchor", "zh_signature", "strategy", "ref", "conf")

_rules_cache = {}


def _norm_l1(native_lang) -> str:
    v = str(native_lang or "").strip().lower()
    return _L1_ALIASES.get(v, v)


def load_rules(path: str = RULES_PATH) -> list:
    """加载指定语言规则文件（进程内按文件缓存）。文件缺失/损坏 → 空表（不产假设，调用方可降级）。"""
    if path in _rules_cache:
        return _rules_cache[path]
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        rules = data.get("rules", []) if isinstance(data, dict) else []
    except Exception:
        rules = []
    _rules_cache[path] = [r for r in rules if r.get("rule_id")]
    return _rules_cache[path]


def _rules_for_l1(l1: str) -> list:
    """按母语路由规则文件（P0.11 多语言）：
      ko        → ko 专用表；该表缺失/损坏 → 降级 en 通用底座 + warning
      en/未知/严格空 → en 通用底座（unknown→en、空→en 降级，本批定案）
    zh 不产假设（调用方在 match_one/match 入口拦截），此处不处理。"""
    if l1 == "ko":
        ko_rules = load_rules(RULES_KO_PATH)
        if not ko_rules:
            warnings.warn("transfer_rules_ko.json 缺失或损坏，降级到 en 通用底座")
            return load_rules(RULES_PATH)
        return ko_rules
    return load_rules(RULES_PATH)


def validate_rules(rules: list, known_sigs=()) -> list:
    """0.35-05 · 规则表严格校验（显式调用；运行时 load 不调用，保持宽松降级）。
    逐条断言，返回违规清单 [{rule_id, problem, detail}]；合规返回 []。
      - rule_id 非空且全局唯一（自检重复）
      - 必填字段齐全（_REQUIRED_KEYS）
      - conf 为可转 float 且 < 0.7（迁移假设永不到确认阈值）
      - kp_anchors / types 为 list
      - 可选 known_sigs：rule["sig"] 必须落在注册表（防 JSON 表出现悬空 sig 引用）
    """
    problems = []
    seen = set()
    for r in rules or []:
        rid = r.get("rule_id") if isinstance(r, dict) else None
        if not rid:
            problems.append({"rule_id": "?", "problem": "empty rule_id",
                             "detail": f"{r}"})
            continue
        if rid in seen:
            problems.append({"rule_id": rid, "problem": "duplicate rule_id",
                             "detail": rid})
        seen.add(rid)
        for k in _REQUIRED_KEYS:
            if k not in r:
                problems.append({"rule_id": rid, "problem": f"missing:{k}",
                                 "detail": ""})
        try:
            conf = float(r.get("conf", 1))
            if not conf < 0.7:
                problems.append({"rule_id": rid, "problem": "conf>=0.7",
                                 "detail": str(r.get("conf"))})
        except (TypeError, ValueError):
            problems.append({"rule_id": rid, "problem": "conf not numeric",
                             "detail": str(r.get("conf"))})
        for k in ("kp_anchors", "types"):
            if k in r and not isinstance(r[k], list):
                problems.append({"rule_id": rid, "problem": f"{k} not list",
                                 "detail": ""})
        if known_sigs and r.get("sig") not in known_sigs:
            problems.append({"rule_id": rid, "problem": "dangling sig",
                             "detail": str(r.get("sig"))})
    return problems