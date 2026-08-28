import json

d = json.load(open("datasets/eval/eval_3_3_results.json", encoding="utf-8"))
m = d["metrics"]
print("consistency_hard:", m["consistency_hard"])
print("pass_accuracy:", m["pass_accuracy"])
print("flowery_block_rate:", m["flowery_block_rate"])
print("flowery_flag_count:", m["flowery_flag_count"])
print("degraded:", m["degraded"])
print()
print("混淆矩阵 truth->verdict:")
for t, vc in m["confusion"].items():
    print(f"  {t:<8}: " + ", ".join(f"{v}={c}" for v, c in sorted(vc.items())))
print()
print("=== 判定不一致明细 ===")
for r in d["rows"]:
    truth = r["truth"]
    vd = r["verdict"]
    is_bad = (
        (truth == "pass" and vd != "pass")
        or (truth == "fail" and vd != "fail")
        or (truth == "hollow" and vd != "fail")
    )
    if is_bad:
        print(f"  {r['id']} | truth={truth:<8} verdict={vd:<8} cov={r['coverage_ratio']} flowery={r['flowery_but_empty']}")
        print(f"      restatement: {r['restatement']}")
print()
print("=== 方向二多 pass 等价解(iso_solution>1)的判定 ===")
for r in d["rows"]:
    if r.get("iso_solution", 1) > 1:
        print(f"  {r['id']} | truth={r['truth']:<6} verdict={r['verdict']:<8} rest={r['restatement']}")