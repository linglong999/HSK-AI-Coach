# -*- coding: utf-8 -*-
"""校准 · judge 侧评分驱动（纯文本校准集）

对 calibration_cases.json 的每条「教学回复文本」调生产 LLM judge（_l2_llm_judge），
使 judge 与人工对**同一段文本**打质量分（verdict + per_dim 1..10）。
- judge client 复用生产 LLMClient（DeepSeek，key 读 .env），满足 judge.py 的
  `.complete(prompt, temperature=0, model)` 契约（thin adapter，绕 Router，纯文本评分）；
- 单条 smoke 用 --smoke N 只跑前 N 条验证连通与输出结构。

运行: python datasets/eval/calibration/judge_calibration.py [--smoke N]
产出: datasets/eval/calibration/judge_verdicts.json
"""
import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))          # .../eval/calibration
_EVAL = os.path.dirname(_HERE)
_PROJECT_ROOT = os.path.dirname(os.path.dirname(_EVAL))
for _p in (_PROJECT_ROOT, os.path.join(_PROJECT_ROOT, "src"), _EVAL,
           os.path.join(_EVAL, "tutor_quality"), _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from engine.llm.client import LLMClient  # noqa: E402
from tutor_quality.judge import _l2_llm_judge, CaseResult  # noqa: E402

_CASES = os.path.join(_HERE, "calibration_cases.json")
_OUT = os.path.join(_HERE, "judge_verdicts.json")


class _CompleteAdapter:
    """judge.py 契约 `.complete(prompt, temperature, model) -> str` 的生产适配。
    model 为空则走 settings 全局模型；非空则 BYOK 覆盖该 model（judge 模型 id）。"""

    def __init__(self, lc):
        self._lc = lc

    def complete(self, prompt, temperature=0, model=""):
        config = {"model": model} if model else None
        return self._lc.chat([{"role": "user", "content": prompt}],
                             temperature=temperature, config=config)


def _minimal_case(c):
    # judge 只消费 id/input/context/metadata.native_lang；绕 Router（纯文本评分，无红线）
    return {"id": c["id"], "input": c["text"], "case_type": "naturalness_anchor",
            "context": None,
            "expected_behavior": {},
            "metadata": {"learner_level": None, "native_lang": "英语"}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", type=int, default=0, help="只跑前 N 条（连通/结构验证）")
    ap.add_argument("--model", default="", help="judge 模型 id（默认 settings 全局模型）")
    args = ap.parse_args()

    cases = json.load(open(_CASES, encoding="utf-8"))["cases"]
    sel = cases[:args.smoke] if args.smoke else cases

    adapter = _CompleteAdapter(LLMClient())
    out = {}
    for i, c in enumerate(sel, 1):
        case = _minimal_case(c)
        rc = CaseResult(case)          # detected=[] → judge 对纯文本评分
        rc.trace = {}
        res = _l2_llm_judge(case, rc, adapter, args.model)
        out[c["id"]] = {"verdict": res.get("verdict"),
                        "per_dim": res.get("per_dim"),
                        "quality_mean": res.get("quality_mean"),
                        "naturalness_mean": res.get("naturalness_mean"),
                        "reason": res.get("reason"),
                        "pending": res.get("pending"),
                        "judge_model": args.model or "(settings default)"}
        print(f"[{i}/{len(sel)}] {c['id']} dim{c['dim']} {c['quality']} "
              f"verdict={res.get('verdict')} qm={res.get('quality_mean')} "
              f"nm={res.get('naturalness_mean')} pending={res.get('pending')}")
        if res.get("pending"):
            print("   PENDING:", res.get("reason"))

    if not args.smoke:
        with open(_OUT, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(f"wrote {len(out)} judge verdicts -> {_OUT}")
    else:
        p = _OUT.replace(".json", "_smoke.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(f"smoke {len(out)} -> {p}")


if __name__ == "__main__":
    main()