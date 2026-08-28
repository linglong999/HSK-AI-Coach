# -*- coding: utf-8 -*-
"""golden v0.2 -> v0.3 一次性迁移。
依据二语教师终裁 + 用户命名决定，幂等可复跑。
改动：
1. 4 条 A 类(016/014/019/032)改判 clean 作负样本；把所有 sub_type=clean 的 item_id 前缀 ERR->CLN。
2. HSK3-ERR-009 从 adversarial 移回 seed 作 clean 对照(保留误报测试 note)。
3. B 类 3 条(003/031/023) golden_status 翻 reviewed。
4. arbitrator 规范化: role 标签 -> 域+日期语义(保留字段, 归一到 'l2-teacher-arbitration')。
"""
import json, io, sys

PATH = "golden_v1_4.json"

def load():
    with io.open(PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def save(g):
    with io.open(PATH, "w", encoding="utf-8") as f:
        json.dump(g, f, ensure_ascii=False, indent=4)

def main():
    g = load()
    seed = g["seed_golden"]
    adv = g["adversarial"]

    # 1. 4 条 A 类 -> 改判 clean 负样本(它们已在 seed, 已是 sub_type=clean)
    a_class = {"HSK4-ERR-014", "HSK4-ERR-016", "HSK3-ERR-019", "HSK3-ERR-032"}
    for s in seed:
        if s["item_id"] in a_class:
            s["sub_type"] = "clean"
            s["error_type"] = None
            s["error_span"] = None
            s["correction"] = None
            s["severity"] = 0
            s["impact"] = "none"
            s["golden_status"] = "reviewed"

    # 2. 009: 从 adversarial 移回 seed 作为 clean 对照
    adv_009 = next((a for a in adv if a["item_id"] == "HSK3-ERR-009"), None)
    if adv_009 is not None:
        adv_009["source"] = "self-built"  # 回归 seed self-built
        adv.remove(adv_009)
        seed.append(adv_009)

    # 3. 所有 sub_type=clean 的 item_id 前缀 ERR->CLN (seed + adv)
    for arr in (seed, adv):
        for s in arr:
            if s.get("sub_type") == "clean" and s["item_id"].startswith("HSK"):
                num = s["item_id"].split("-")[-1]
                lvl = s["item_id"].split("-")[0]
                if s["item_id"].startswith(lvl + "-ERR-"):
                    s["item_id"] = f"{lvl}-CLN-{num}"

    # 4. B 类 3 条翻 reviewed
    b_class = {"HSK1-ERR-003", "HSK2-ERR-031", "HSK1-ERR-023"}
    for s in seed:
        if s["item_id"] in b_class or s["item_id"].replace("CLN", "ERR") in b_class:
            s["golden_status"] = "reviewed"

    # 5. arbitrator 规范化
    for arr in (seed, adv):
        for s in arr:
            if s.get("arbitrator"):
                s["arbitrator"] = "l2-teacher-arbitration"
            if s.get("golden_status") == "reviewed" and s.get("arbitrator"):
                # reviewed 且已仲裁 -> 保留日期, 无则补当日
                if not s.get("arbitration_date"):
                    s["arbitration_date"] = "2026-08-23"

    # version bump
    g["version"] = "golden_v1_4_v0.3"

    save(g)

    # 校验输出统计
    seed = g["seed_golden"]; adv = g["adversarial"]
    tot = seed + adv
    clean = [s for s in tot if s.get("sub_type") == "clean"]
    err = [s for s in tot if s.get("error_type") is not None]
    print(f"version={g['version']}")
    print(f"seed={len(seed)} adv={len(adv)} total={len(tot)}")
    print(f"clean={len(clean)} error_with_type={len(err)}")
    from collections import Counter
    print("type_dist:", Counter(s.get("error_type") for s in err))
    print("status_dist:", Counter(s.get("golden_status") for s in tot))
    print("clean_ids:", sorted(s["item_id"] for s in clean))

if __name__ == "__main__":
    main()