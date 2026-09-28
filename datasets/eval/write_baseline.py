# -*- coding: utf-8 -*-
# B6 校准 · 跑生产 L2 judge 于 cases.json，用真实 naturalness_mean 回写 baseline.json（启用统计门控）
# 依赖：注入 LLM client（同 judge_focus_calibration 的 _CompleteAdapter 包装 LLMClient）。
# 首版 baseline.naturalness_mean=null 为哨兵（门控退化）；本跑回填后 delta 门控生效。
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(_HERE))
for p in (_HERE, _PROJECT_ROOT, os.path.join(_PROJECT_ROOT, "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from engine.llm.client import LLMClient  # noqa: E402
import run_all  # noqa: E402


class _CompleteAdapter:
    """judge.py 契约 `.complete(prompt, temperature, model) -> str`。model 空走 settings 全局模型。"""
    def __init__(self, lc):
        self._lc = lc

    def complete(self, prompt, temperature=0, model=""):
        config = {"model": model} if model else None
        return self._lc.chat([{"role": "user", "content": prompt}],
                             temperature=temperature, config=config)


def main():
    adapter = _CompleteAdapter(LLMClient())
    run_all.set_judge_client(adapter)
    code, suites = run_all.run(layer="llm_judge", judge_model="")
    l2 = suites.get("tutor_l2_real") or {}
    detail = l2.get("detail") or {}
    nat = detail.get("naturalness_mean")
    print(f"tutor_l2_real ok={l2.get('ok')} naturalness_mean={nat} pass_k={detail.get('pass_k')}")
    if nat is None:
        print("未得到 naturalness_mean，不回写 baseline（L2 可能被跳过）")
        return 1
    baseline_path = os.path.join(_HERE, "baseline.json")
    base = json.load(open(baseline_path, encoding="utf-8")) if os.path.exists(baseline_path) else {}
    base["pass_rate"] = base.get("pass_rate", 0.9)
    base["naturalness_mean"] = round(nat, 3)
    base["band"] = base.get("band", 0.3)
    base["note"] = ("B6 校准后首版真实自然度基线（生产 L2 judge 注入 rubric §七锚，judge_model=settings 默认）。"
                    "由 write_baseline.py 运行 llm_judge 层回填，启用 delta 统计门控。")
    base["judge_qualified"] = True
    with open(baseline_path, "w", encoding="utf-8") as f:
        json.dump(base, f, ensure_ascii=False, indent=2)
    print(f"回写 baseline.json：naturalness_mean={base['naturalness_mean']} pass_rate={base['pass_rate']}")
    return 0 if l2.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())