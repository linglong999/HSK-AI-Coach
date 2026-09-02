# ============================================================
# run_skill_trigger_eval.py（M1 · 触发评测）
# 读取 datasets/eval/skill_trigger_eval.json（四维歧义触发集），
# 用「规则级近似触发判定」在无 LLM Key 环境下评测触发可靠性。
# 指标：A 精确率 / B/C/D 召回率 / 技能链正确性（C/D 严格断言）
# 说明：这是确定性护栏层的触发自测；LLM 层触发评测留待 M6 有 Key 叠加。
# 输出：stdout 明细 + datasets/eval/skill_trigger_results.json
# ============================================================

import json
import os
import sys
from typing import Dict, List

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _PROJECT_ROOT)

EVAL_PATH = os.path.join(_PROJECT_ROOT, "datasets", "eval", "skill_trigger_eval.json")
OUT_PATH = os.path.join(_PROJECT_ROOT, "datasets", "eval", "skill_trigger_results.json")

SKILL_NAMES = {
    "identify_errors",
    "explain_error",
    "verify_retell",
    "lookup_knowledge_point",
    "get_review_queue",
}

# ---- 规则级近似触发判定：用任务特征词/疑问词去映射该触发的技能集 ----
# 约定：被判定的"命中集"是集合，评测断言：
#   - A 组应全会集为空（不误触发）
#   - B/C/D 组：命中集 需覆盖 expect 中每个技能（召回）
#   - 技能链正确性：C/D 组额外检查 identify 是否带出 explain（严格）

# 触发特征表（近似）：{特征类别: [(特征词, 触发技能集)]}
_TRIGGER_FEATURES = [
    # 复习队列
    (["复习", "该复习", "没掌握", "待复习", "最近该"], {"get_review_queue"}),
    # 知识点定义查询：出现具体知识点名 + 求"是什么"
    (["是什么", "什么是", "什么意思", "怎么用"], {"lookup_knowledge_point"}),
    # 识别偏误（有错吗/对吗/帮我看看/求改）：求判断或改错
    (["有错", "有语病", "对吗", "对不对", "对还是不对", "帮我看看", "看看", "帮我改", "改一下", "怎么改"], {"identify_errors", "explain_error"}),
    # 讲解（为什么不对/讲讲/解释）：识别后带讲解
    (["为什么", "讲讲", "解释", "为什么不对"], {"identify_errors", "explain_error"}),
    # 复述验证：学习者复述后求判定
    (["复述", "我再讲一遍", "我试着说", "检验我", "验证我"], {"verify_retell"}),
]

# 疑似偏误句兜底：句内含明确偏误模式（如"很多"作补语），且无任何技能特征命中，则默认进入识别。
_SENTENCE_BIAS_MARKERS = ["很多", "几次", "千万", "了在", "一次两次"]


def rule_predict(query: str) -> set:
    """规则级近似：返回该 query 应触发的技能集（空=不触发）。"""
    triggered: set = set()
    hit_feature = False
    for words, skills in _TRIGGER_FEATURES:
        if any(w in query for w in words):
            triggered |= skills
            hit_feature = True

    # 边界净化：
    # 纯求"是什么"不引偏误链
    if "是什么" in query or "什么是" in query:
        triggered.discard("identify_errors")
        triggered.discard("explain_error")

    # "对不对/对还是不对"求对错判定 → 仅识别（若对则无需讲解）
    if "对不对" in query or "对还是不对" in query or "对吗" in query:
        triggered.discard("explain_error")

    # "帮我改/改一下/怎么改" → 识别+讲解（改错需解释）
    # （已在特征表中按 识别+讲解 处理，"怎么改"除外仅为识别补正）

    # 疑似偏误整句兜底：无任何特征但仍像病句 → 识别
    if not hit_feature and any(m in query for m in _SENTENCE_BIAS_MARKERS):
        triggered.add("identify_errors")

    return triggered


def _assert_trigger(predicted: set, expected: List[str], group: str) -> Dict:
    """单 case 判定。返回 通过/类型/明细。"""
    exp = set(expected)
    if group == "A_no_trigger":
        # A：精确率——不该触发时不误触发
        ok = len(predicted) == 0
        return {"pass": ok, "kind": "precision",
                "detail": f"预测={sorted(predicted)} 期望={[]} 通过={ok}"}

    # B/C/D：召回——每个期望技能都被触发
    recall_ok = exp.issubset(predicted)
    # 技能链正确性（C/D）：identify 是否带出 explain（用于 D 组严格断言）
    chain_ok = True
    if "identify_errors" in exp and "explain_error" in exp:
        chain_ok = "explain_error" in predicted and "identify_errors" in predicted
    ok = recall_ok and chain_ok
    return {"pass": ok, "kind": "recall",
            "detail": f"预测={sorted(predicted)} 期望={sorted(exp)} 通过={ok}"}


def run() -> None:
    with open(EVAL_PATH, encoding="utf-8") as f:
        data = json.load(f)

    rows: List[Dict] = []
    stats = {}
    total = total_pass = 0
    for group, gdata in data["groups"].items():
        cases = gdata.get("cases", [])
        gpass = 0
        for case in cases:
            query = case["query"]
            predicted = rule_predict(query)
            res = _assert_trigger(predicted, case["expect"], group)
            res["group"] = group
            res["query"] = query
            res["why"] = case["why"]
            rows.append(res)
            if res["pass"]:
                gpass += 1
            total += 1
            total_pass += 1 if res["pass"] else 0
        stats[group] = {"cases": len(cases), "pass": gpass}
        # 组级通过率做成分组展示

    # 汇总指标
    prec_cases = [r for r in rows if r["kind"] == "precision"]
    recall_cases = [r for r in rows if r["kind"] == "recall"]
    precision = (sum(1 for r in prec_cases if r["pass"]) / len(prec_cases)) if prec_cases else 1.0
    recall = (sum(1 for r in recall_cases if r["pass"]) / len(recall_cases)) if recall_cases else 1.0

    summary = {
        "layer": data["layer"],
        "metric": {
            "trigger_precision": round(precision, 4),
            "trigger_recall": round(recall, 4),
            "overall_pass": f"{total_pass}/{total}",
        },
        "groups": stats,
    }

    # 输出
    print("=" * 70)
    print("M1 Skill 触发评测（无Key规则层近似） · layer:", data["layer"], "v", data["version"])
    print("=" * 70)
    for r in rows:
        mark = "PASS" if r["pass"] else "FAIL"
        print(f"[{r['group']}] {mark}  {r['query']}\n      {r['detail']}")

    print("-" * 70)
    print(f"触发精确率(precision) = {summary['metric']['trigger_precision']}")
    print(f"触发召回率(recall)    = {summary['metric']['trigger_recall']}")
    print(f"总体通过 = {summary['metric']['overall_pass']}")
    for g, s in stats.items():
        print(f"  组 {g}: {s['pass']}/{s['cases']} 通过")

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "rows": rows}, f, ensure_ascii=False, indent=2)
    print(f"\n结果已写入: {OUT_PATH}")


if __name__ == "__main__":
    run()