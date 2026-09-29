# -*- coding: utf-8 -*-
# ============================================================
# B6 J5 · 分层回归单入口（datasets/eval/run_all.py）
# 依 B6-A④ 五层金字塔落地：统一收集/跑/汇总/判定/CI 阻塞（exit 0=过 / 1=阻塞）。
# 五层金字塔折叠为两层可调金字塔（红线全放 deterministic 层）：
#   deterministic  —— L1/L3 程序断言 + 语料加载校验 + D-6 断言 smoke（零 LLM，PR gate 跑法）
#   llm_judge      —— tutor_quality L2 真 LLM-judge（烧 LLM，D-4 语义质量，nightly 跑法）
# 分层语义：
#   --layer deterministic   PR gate（红线全护、零 LLM 成本；只跑影响路径的轻量 subset）
#   --layer llm_judge       L2 语义质量（全量 judge + 统计门控，成本 cents-级）
#   --layer all / nightly   全量回归（deterministic + llm_judge + 统计门控），放 nightly job
# J6（B6-A⑤ CI 稳定性）随本文件落地：
#   - 三元组 pin：每次 llm_judge 运行 meta 记录 judge_model_id + rubric_version +
#     prompt_template_hash（rubric 全文 sha256 前 8 位）
#   - 统计门控：nightly 判定 = pass^k ≥90% 且 naturalness_mean delta vs baseline ≤ 带宽
#     （baseline 落 baseline.json；慢漂移即 fail，非逐字 exact-match）
# ============================================================
import argparse
import hashlib
import json
import os
import sys
from unittest.mock import patch

_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(_HERE))
for p in (_HERE, _PROJECT_ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

# --- 语料 / 子模块路径 ------------------------------------------------
EVAL = _HERE
TUTOR = os.path.join(EVAL, "tutor_quality")
CORPORA = {
    "golden":     os.path.join(EVAL, "golden_v1_4.json"),
    "retell":     os.path.join(EVAL, "retell_golden.json"),
    "cged":       os.path.join(EVAL, "cged_recognition.json"),
    "skill":      os.path.join(EVAL, "skill_trigger_eval.json"),
}
TUTOR_CASES = os.path.join(TUTOR, "cases.json")
RUBRIC_PATH = os.path.join(TUTOR, "rubric.md")
BASELINE_PATH = os.path.join(EVAL, "baseline.json")

PASS_THRESHOLD = 0.90          # 回归门槛（同时是自然度发布门槛的 pass 率维度）
NATURAL_BAND = 0.3             # 统计门控：naturalness_mean delta vs baseline 带宽（慢漂移容限）


# --- deterministic 层 suites ---------------------------------------------------

def suite_tutor_l1_l3():
    """全 cases 的离线 L1/L3 回归；真实模型评测另走 llm_judge 层。

    识别走确定性构式回退，讲解走降级模板。图谱只在内存中使用，
    避免评测读取或覆盖真实用户的 data/graph_default.json。
    """
    from tutor_quality.run_tutor_judge import summarize
    from tutor_quality.judge import load_cases, judge_case
    from engine.graph.error_graph import ErrorGraph
    from engine.llm.client import JSONStrictError, LLMClient
    from engine.router import Router
    cases = load_cases()
    with (patch.object(ErrorGraph, "load", return_value=None),
          patch.object(ErrorGraph, "save", return_value=None),
          patch.object(LLMClient, "chat_json",
                       side_effect=RuntimeError("deterministic eval: offline")),
          patch.object(LLMClient, "chat_json_strict",
                       side_effect=JSONStrictError("deterministic eval: offline"))):
        router = Router()
        results = [judge_case(c, router, mock_llm=True) for c in cases]
    s = summarize(results)
    _dump("tutor_l1_l3", s)
    return {"ok": bool(s["gate"] and not s["fail_ids"]),
            "detail": {"total": s["total"], "passed": s["passed"],
                       "pass_rate": s["pass_rate"], "fail_ids": s["fail_ids"]}}


def _check_corpus(path, check):
    """语料加载 + 结构性校验（防语料腐化；B6 收集完整性标定）。"""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    problems = check(data)
    return {"ok": not problems, "detail": {"file": os.path.basename(path),
                                           "problems": problems}}


def suite_golden_corpus():
    return _check_corpus(CORPORA["golden"], lambda d: ([] if
        (d.get("seed_golden") and d.get("version")) else ["seed_golden/version 缺失"]))


def suite_retell_corpus():
    return _check_corpus(CORPORA["retell"],
                         lambda d: [] if d.get("items") else ["items 为空"])


def suite_cged_corpus():
    return _check_corpus(CORPORA["cged"],
                         lambda d: [] if d.get("items") else ["items 为空"])


def suite_skill_corpus():
    return _check_corpus(CORPORA["skill"],
                         lambda d: [] if d.get("groups") else ["groups 为空"])


def suite_d6_assertions():
    """D-6 八条确定性断言的 smoke 集成点：合法样本全过、构造违规样本必被抓。
    使 D-6 断言器在 runtime 真实生效（不做 LLM，纯确定性）。"""
    from count_assertions import assert_d6
    violations = assert_d6({}, {"nodes": {}}, {})  # 缺省容错 → 全过
    bad = assert_d6({"deep_dive_count": 2}, {"nodes": {}}, {})  # 构造违规
    return {"ok": not violations and any(v.startswith("d6-1") for v in bad),
            "detail": {"default_ok": not violations,
                       "sample_bad_caught": bad if bad else None}}


DETERMINISTIC_SUITES = {
    "tutor_l1_l3": suite_tutor_l1_l3,
    "golden_corpus": suite_golden_corpus,
    "retell_corpus": suite_retell_corpus,
    "cged_corpus": suite_cged_corpus,
    "skill_corpus": suite_skill_corpus,
    "d6_assertions": suite_d6_assertions,
}


# --- llm_judge 层 suites ---------------------------------------------------

def _pin_meta(judge_model=""):
    """J6 三元组 pin：judge_model_id + rubric_version + prompt_template_hash。"""
    rubric = ""
    if os.path.exists(RUBRIC_PATH):
        rubric = open(RUBRIC_PATH, encoding="utf-8").read()
    version = "unknown"
    for line in rubric.splitlines():
        if "rubric_version" in line:
            version = line.split("rubric_version", 1)[1].split("=")[-1].strip()
            break
    return {
        "judge_model_id": judge_model or "(mock/cli 未指定)",
        "rubric_version": version,
        "prompt_template_hash": hashlib.sha256(rubric.encode("utf-8")).hexdigest()[:8],
    }


def _load_baseline():
    if os.path.exists(BASELINE_PATH):
        with open(BASELINE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"naturalness_mean": None, "pass_rate": None, "band": None}


def suite_tutor_l2_real(judge_model=""):
    """tutor_quality 全 cases：L2 真 LLM-judge（stats 门控）。jv 由 judge 判分。
    依赖：must 注入 LLM client（见 main 的 --judge-model 注入）；无 client → L2 skipped，
    不隔离地记 not-run（不因缺 Key 阻塞 PR 确定性层，nightly 需带 Key）。"""
    from tutor_quality.judge import load_cases, judge_case
    from engine.router import Router
    client = _LLM_CLIENT
    if client is None:
        return {"ok": True, "skipped": True,
                "detail": {"note": "未注入 LLM judge client，L2 NOT_RUN（nightly 需带 Key 注入）"}}
    results = [judge_case(c, Router(), mock_llm=False, client=client,
                          judge_model=judge_model) for c in load_cases()]
    judged = [r for r in results if not (r.l2 or {}).get("skipped")]
    if not judged:
        return {"ok": False, "detail": {"note": "未注入 LLM client，L2 未跑（nightly 需带 Key）"}}
    fails = [r.case["id"] for r in results if r.redline_fails]
    pass_n = len(results) - len(fails)
    pass_k = pass_n / len(results)
    means = [r.l2.get("naturalness_mean") for r in judged if r.l2.get("naturalness_mean") is not None]
    nat_mean = (sum(means) / len(means)) if means else None
    # 统计门控：pass^k ≥90% 且 naturalness_mean delta vs baseline ≤ 带宽
    base = _load_baseline()
    band = base.get("band") if base.get("band") is not None else NATURAL_BAND
    delta_ok = True
    if nat_mean is not None and base.get("naturalness_mean") is not None:
        delta_ok = abs(nat_mean - base["naturalness_mean"]) <= band
    detail = {"total": len(results), "pass_k": round(pass_k, 3),
              "naturalness_mean": (round(nat_mean, 3) if nat_mean else None),
              "baseline_nat": base.get("naturalness_mean"),
              "band": band, "delta_ok": delta_ok,
              "pin": _pin_meta(judge_model)}
    _dump("tutor_l2_real", detail)
    return {"ok": bool(pass_k >= PASS_THRESHOLD and delta_ok and not fails),
            "detail": detail}


# --- 编排 ---------------------------------------------------------------

_LLM_CLIENT = None          # main 注入；测试可重置


def _dump(name, data):
    out = os.path.join(EVAL, "reports", f"run_all_{name}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def run(layer="deterministic", judge_model=""):
    """执行指定层，返回 (exit_code, {suite_name: {ok, detail}})。
    layer: deterministic | llm_judge | all | nightly。nightly=all。"""
    suites = {}
    if layer in ("deterministic", "all", "nightly"):
        for name, fn in DETERMINISTIC_SUITES.items():
            suites[name] = _safe(fn)
    if layer in ("llm_judge", "all", "nightly"):
        suites["tutor_l2_real"] = _safe(lambda: suite_tutor_l2_real(judge_model))
    ok = all(v["ok"] for v in suites.values() if v is not None)
    print(f"== run_all --layer {layer} ==")
    for name, v in suites.items():
        status = "PASS" if v and v["ok"] else ("FAIL" if v else "SKIP(异常)")
        print(f"  [{status}] {name}  {json.dumps(v['detail'], ensure_ascii=False) if v else ''}")
    return (0 if ok else 1, suites)


def _safe(fn):
    """suite 包装：异常 → ok=False（不让单个 suite 崩溃吞掉 CI 阻塞语义）。"""
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "detail": {"suite_error": repr(e)}}


# --- CLI ----------------------------------------------------------------

def set_judge_client(client):
    """注入 L2 judge 的 LLM client（nightly / 测试调用）。client 契约：
    `.complete(prompt, temperature=0, model=...) -> str`。None → 清空（L2 NOT_RUN）。"""
    global _LLM_CLIENT
    _LLM_CLIENT = client


def main():
    ap = argparse.ArgumentParser(description="B6 分层回归单入口（CI 阻塞 exit 0/1）")
    ap.add_argument("--layer", default="deterministic",
                    choices=["deterministic", "llm_judge", "all", "nightly"],
                    help="deterministic=PR gate(零LLM) / llm_judge=语义质量 / nightly=all")
    ap.add_argument("--judge-model", default="", help="L2 judge 模型 id（进三元组 pin）")
    args = ap.parse_args()
    code, _ = run(args.layer, args.judge_model)
    return code


if __name__ == "__main__":
    sys.exit(main())
