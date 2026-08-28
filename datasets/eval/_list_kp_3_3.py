import json

d = json.load(open("datasets/eval/retell_verification.json", encoding="utf-8"))
# 列出方向一所有样本的 explanation + key_points，供清洗对照
seen = {}
for it in d["items"]:
    if it["source"] == "direction1_constructed":
        k = it["kp_id"]
        seen.setdefault(k, it["explanation"])
for k, exp in seen.items():
    print(f"### {k}")
    print("   ", exp)
print()
print("总方向一样本:", sum(1 for it in d["items"] if it["source"] == "direction1_constructed"))
print("方向二样本:", sum(1 for it in d["items"] if it["source"] == "direction2_mucgec"))