# -*- coding: utf-8 -*-
"""约束7覆盖面复检（规则回退层）。
验证：新构造的高频语法小类合法句，在无 LLM Key（规则回退）下是否误报。
运行：python datasets/eval/check_constraint7.py
"""
import os
import sys
import json

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)

from engine.recognizer import Recognizer
from engine.recognizer import detect_beyond_level


def run():
    path = os.path.join(_ROOT, "datasets", "eval", "constraint7_coverage_check.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    rec = Recognizer()
    rows = []
    for cat, items in data["categories"].items():
        for it in items:
            text, level = it["sentence"], it["level"]
            res = rec.recognize(text, level=level)
            errors = res.get("errors", [])
            uncertain = res.get("uncertain", [])
            beyond = res.get("beyond_level", [])
            is_false_positive = bool(errors) or bool(uncertain)
            rows.append({
                "cat": cat,
                "sentence": text,
                "level": level,
                "note": it["note"],
                "errors": [e.get("fragment", "") for e in errors],
                "uncertain": [u.get("fragment", "") for u in uncertain],
                "beyond_level": beyond,
                "false_positive": is_false_positive,
            })

    # 汇总
    a_fp = [r for r in rows if r["cat"] == "A_superclass_within" and r["false_positive"]]
    b_beyond = [r for r in rows if r["cat"] == "B_superclass_beyond"]
    b_trigger = [r for r in b_beyond if r["beyond_level"]]          # 超纲分支真实触发
    b_unc = [r for r in b_beyond if r["uncertain"]]
    b_confirmed_fp = [r for r in b_beyond if r["errors"]]            # 超纲合法句误报 confirmed（应 0）

    print("=" * 64)
    print("约束7覆盖面复检（规则回退层 / 无 LLM Key）")
    print("=" * 64)
    for r in rows:
        flag = "误报" if r["false_positive"] else "✓ 无"
        det = ""
        if r["errors"]:
            det = f"errors={r['errors']}"
        elif r["uncertain"]:
            det = f"uncertain={r['uncertain']}"
        print(f"[{r['cat']}] L{r['level']} | {r['sentence']} | {flag} {det}")
    print("-" * 64)
    print(f"A 类纯纲内合法句: {len([r for r in rows if r['cat']=='A_superclass_within'])} 条")
    print(f"  → 误报 {len(a_fp)} 条  {'（0 误报 ✓）' if not a_fp else '!'}")
    print(f"B 类真超纲合法句: {len(b_beyond)} 条")
    print(f"  → 超纲分支触发 {len(b_trigger)}/{len(b_beyond)}（应=总数，验证盲区已修复）")
    print(f"  → 产 uncertain(超纲提示) {len(b_unc)} 条（预期，非语法误报）")
    print(f"  → 误报 confirmed {len(b_confirmed_fp)} 条  {'（0 误报 ✓，不污染图谱）' if not b_confirmed_fp else '!'}")

    out = os.path.join(_ROOT, "datasets", "eval", "constraint7_results.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"layer": data["test_layer"], "rows": rows}, f, ensure_ascii=False, indent=2)
    print(f"\n结果已存: {out}")


if __name__ == "__main__":
    run()