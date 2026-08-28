import json

d = json.load(open("datasets/eval/eval_3_3_results.json", encoding="utf-8"))
# 找几个代表性样本的完整 point_judgements
targets = {
    "MUCGEC-29:pass#2": None,
    "HSK2-ERR-025:hollow": None,
}
for r in d["rows"]:
    if r["id"] in targets:
        targets[r["id"]] = r

for iid, r in targets.items():
    print("=" * 60)
    print(iid, "| truth:", r["truth"], "| verdict:", r["verdict"], "| cov:", r["coverage_ratio"])
    print("explanation:", r["explanation"])
    print("restatement:", r["restatement"])
    print("key_points:")
    for kp in r["key_points"]:
        print("   ", kp["id"], kp["text"])
    print("point_judgements:")
    for pj in r["point_judgements"]:
        print(f"    id={pj.get('id')} covered={pj.get('is_covered')} | {pj.get('text')}")
        print(f"        evidence: {pj.get('evidence')}")