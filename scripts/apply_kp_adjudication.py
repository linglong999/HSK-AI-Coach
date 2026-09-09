# -*- coding: utf-8 -*-
"""P0.15 转正 · 把 kp_meta_compare 的裁定落进 knowledge_points_v1_4.json（原子批次）

依据：reports/kp_meta_compare-裁定建议.md（A 角色已确认三项改名全采纳：介词/代词/复句）。
改动 = 等级修正(10) + kp 改名 + 程度副词 id 修正 + 新建 3 kp + 三处硬错误剔除
    + 每 kp 挂 syllabus_refs(sid:in_scope) + level_span + 双标准 note + 裁决日志。
等级 → level_gf 沿用全表现有映射 {1:1, 2:3, 3:5, 4:6}（数据自洽，见 knowledge_points_v1_4.json）。
in_scope：1-4 级为 true（MVP 入库），5+/7-9 为 false（记录但不入库，供扩级启用）。
备份原文件为 .bak（已 gitignore，不入库）；输出打印裁决汇总。
"""
import csv
import json
import os
import sys
import datetime
import shutil

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
DATASETS = os.path.join(_ROOT, "datasets")
REPORTS = os.path.join(_ROOT, "reports")
KP_PATH = os.path.join(DATASETS, "knowledge_points_v1_4.json")
CSV_PATH = os.path.join(REPORTS, "kp_meta_compare_full.csv")

# 等级 → level_gf（沿用全表现有映射，保证自洽）
GF = {1: 1, 2: 3, 3: 5, 4: 6}


def in_scope(sid_level):
    # sid_level: int(1-9) 或 "7-9"
    if sid_level == "7-9":
        return False
    return 1 <= int(sid_level) <= 4


def span_of(refs):
    """kpi → "min-max" 字符串。sid 形如 hsk30-g3-067：级别在中段 g3；g79=7-9 合编按 9 计高。"""
    nums = set()
    for sid in refs:
        seg = sid.split("-")[1]          # g3 / g79
        n = int(seg[1:])                 # 3 / 79
        nums.add(9 if n >= 7 else n)     # 7-9 合编归 9
    return "%d-%d" % (min(nums), max(nums)) if nums else ""


def load_csv():
    """{kp_id: {sid: level}} —— 全量候选（含 L5+/7-9）"""
    base = {}
    with open(CSV_PATH, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            base.setdefault(r["kp_id"], {})[r["syllabus_id"]] = r["level"]
    return base


def load_kp():
    return json.load(open(KP_PATH, encoding="utf-8"))


def main():
    base = load_csv()
    kp_data = load_kp()
    kps = kp_data["knowledge_points"]

    # —— 按 syllabus 重建依赖（新 kp 直接用考纲 item 命题，旧 kp 用 base 候选裁剪）——
    sy = json.load(open(os.path.join(DATASETS, "syllabus_hsk30_2025.json"), encoding="utf-8"))["points"]
    sy_by_id = {p["id"]: p for p in sy}

    def sid_item(sid):
        p = sy_by_id.get(sid)
        return (p or {}).get("item") or ""

    # ---- 新 kp 的 refs（直接按考纲 item 命题重建，不依赖 base）----
    def collect(pred, lo=1, hi=999):
        out = {}
        for p in sy:
            if pred(p):
                lv = p["level"]
                out[p["id"]] = lv
        return out

    # 程度补语：item 含"程度补语"
    chengdu_buyu = collect(lambda p: "程度补语" in (p.get("item") or ""))
    # 可能补语：item 含"可能补语"
    keneng_buyu = collect(lambda p: "可能补语" in (p.get("item") or ""))
    # 动量补语/动量词：g1-041(数量补语1 动量) + 词类 item=="动量词"
    dongliang = collect(lambda p: (p.get("item") == "动量词") or (p.get("id") == "hsk30-g1-041"))

    # ---- 逐 kp 重构 refs（默认全采纳；仅部分→drop；name/level 修正）----
    REN = {  # kp_id: 裁定覆盖
        # name/level 修正 + refs 裁剪
        "kp-bei-sentence":       {"level": 3, "gf": GF[3]},
        "kp-liangci":            {"drop_pred": lambda p: p.get("item") == "动量词",   # 动量词拆去 dongliang
                                  "name": "量词（个/本/张/件…）"},
        "kp-he-yiyang":          {"level": 3, "gf": GF[3], "name": "“跟/和……一样”比较"},
        "kp-le-dynamic":         {"level": 1, "gf": GF[1], "name": "动态助词“了”（了1/了2）"},
        "kp-jiuguo-jiegou":      {"drop_pred": lambda p: "程度补语" in (p.get("item") or ""),
                                  "name": "结果补语（做完/听懂/学会）"},
        "kp-quxiang-buyu":       {"level": 2, "gf": GF[2]},
        "kp-de-di-de":           {"level": 1, "gf": GF[1]},
        "kp-cunxian-ju":         {"level": 1, "gf": GF[1]},
        "kp-liandong-ju":        {"level": 1, "gf": GF[1]},
        "kp-standing-shi":       {"level": 2, "gf": GF[2]},
        "kp-shide-sentence":     {"level": 2, "gf": GF[2]},
        "kp-zhuangyu-chezhi":    {"drop_sid": ["hsk30-g4-058"],
                                  "name": "状语语序（时间/地点/方式）"},
        "kp-zhongci-zhitou":     {"name": "代词（人称/指示/疑问）", "level": 1, "gf": GF[1]},
        "kp-preposition-zaizai": {"name": "介词（在/从/给/向…）", "level": 1, "gf": GF[1]},
        "kp-jietiaoyu-tiaojian": {"name": "复句与关联词语", "level": 3, "gf": GF[3]},
        "kp-haishi-xuanze":      {"name": "选择问（还是）与正反问（…吗/…不…）",
                                  "drop_sid": ["hsk30-g1-047", "hsk30-g1-048", "hsk30-g3-065"]},
        "kp-zhizhi-dao":         {"name": "时量补语（看了一个小时）",
                                  "drop_sid": ["hsk30-g1-041"]},
    }

    # 程度副词 id 修正
    RID = {"kp-chengdu-jieci": "kp-chengdu-fuci"}

    out = {}
    log = []
    today = datetime.date.today().isoformat()
    tf_span = {}
    for kid_orig, kp in list(kps.items()):
        new_id = RID.get(kid_orig, kid_orig)
        kid = new_id
        new_kp = dict(kp)
        new_kp["id"] = new_id

        over = REN.get(kid_orig, {})
        if "name" in over:
            new_kp["knowledge_point"] = over["name"]
        if "level" in over:
            new_kp["level"] = over["level"]
            new_kp["level_gf"] = over.get("gf", kp.get("level_gf"))

        # base 候选键可能已被改名（幂等：可从 v0.1 或已 rename 的 v0.2 起步）
        base_key = kid_orig
        if base_key not in base:
            for old, nw in RID.items():
                if nw == kid_orig and old in base:
                    base_key = old
                    break

        # refs：从 base 候选应用 drop
        refs = {}
        for sid in base.get(base_key, {}):
            it = sid_item(sid)
            if over.get("drop_sid") and sid in over["drop_sid"]:
                log.append("DROP %s ← %s (%s)" % (kid_orig, sid, it))
                continue
            if over.get("drop_pred") and over["drop_pred"](sy_by_id.get(sid, {})):
                log.append("DROP %s ← %s (%s)" % (kid_orig, sid, it))
                continue
            refs[sid] = in_scope(base[base_key][sid])
        new_kp["syllabus_refs"] = refs
        new_kp["level_span"] = span_of(refs) if refs else kp.get("level", 1)
        tf_span[kid] = new_kp["level_span"]

        # 注记：考纲依据 + 双标准
        new_kp["note"] = (new_kp.get("note", "") + "；"
                          "等级以 2025 新 HSK 语法考纲为准（调用于转正），"
                          "level_span=%s；签核判定见 adjudication_meta." % new_kp["level_span"]).strip("；")
        out[kid] = new_kp

    # ---- 新建 3 kp ----
    def make_new(new_id, name, level, gf, refs_src, note):
        refs = {sid: in_scope(lv) for sid, lv in refs_src.items()}
        d = {
            "id": new_id,
            "knowledge_point": name,
            "level": level,
            "level_gf": gf,
            "error_types": ["语法"],
            "note": note + "；等级以 2025 考纲为准，level=%s" % level,
            "syllabus_refs": refs,
            "level_span": span_of(refs),
        }
        out[new_id] = d
        tf_span[new_id] = d["level_span"]
        return d

    make_new("kp-chengdu-buyu", "程度补语（得很/极了/死了）", 3, GF[3], chengdu_buyu,
             "从结果补语拆出的独立类：形容词+得很/极了/坏了；心理动词+死了/厉害；得+不得了/透。原被误挂 kp-jiuguo-jiegou")
    make_new("kp-keneng-buyu", "可能补语（得/不）", 3, GF[3], keneng_buyu,
             "动词+得/不+动词；得/不+了；得/不得。英语母语者高频回避区，与结果/趋向补语强关联")
    make_new("kp-dongliang-buyu", "动量补语/动量词（次/回/遍）", 1, GF[1], dongliang,
             "动量补语 g1-041 + 动量词（次/口回遍声）。原 g1-041 被误挂 kp-zhizhi-dao、动量词被误挂 kp-liangci")

    # ---- 重组 knowledge_points 并 bump version ----
    kp_data["knowledge_points"] = {k: out[k] for k in out}
    kp_data["version"] = "0.2"
    kp_data["source"] = "manual-curated + 2025 HSK grammar syllabus (P0.15 adjudicated)"
    kp_data["$note"] = (
        "HSK 1-4 级高频语法点 MVP（v0.2，P0.15 转正内建）。语法点等级以 2025 新 HSK 语法考纲为准"
        "（调准 10 项）；词汇超纲判定维持 GF 0025-2021 词表（dichotomy：语法走 2025，词汇走 2021）。"
        "每点含 syllabus_refs（考纲条目 sid → in_scope 布尔，1-4 级 true 入库 / 5+ false 记录待启）"
        "与 level_span（跨级分布）。新增 程度补语/可能补语/动量补语-动量词 三 kp。"
        "kps 结构调整见 adjudication_meta。"
    )
    kp_data["adjudication_meta"] = {
        "date": today,
        "by": "A角色裁定 + B实现",
        "basis": "reports/kp_meta_compare-裁定建议.md（改：介词/代词/复句三项确认全采纳）",
        "decisions": [
            "10 项离级修正（de-di-de/存现/连动/能愿/了→L1；兼语/是…的/趋向补语→L2；被动→L3；和…一样→L3）",
            "id 修正：kp-chengdu-jieci→kp-chengdu-fuci（T-6，jieci=介词为命名错误）",
            "改名：介词/代词/复句全采纳；he-yiyang、le-dynamic（了1/了2）",
            "新建 3 kp：程度补语 / 可能补语 / 动量补语-动量词",
            "三处硬错误剔除：程度补语→新kp，动量→新kp，g4-058 把字子项剔出状语",
            "复句 level 取典型起始级 L3（非绝对最低 L1 的'不用关联词语'条目）——待 A 复核",
        ],
        "open_items": ["复句 level=L3 是否接受（若按绝对最低则为 1）"],
    }

    # 备份原文件
    bak = KP_PATH + ".bak" + today
    shutil.copyfile(KP_PATH, bak)

    with open(KP_PATH, "w", encoding="utf-8") as f:
        json.dump(kp_data, f, ensure_ascii=False, indent=2)

    # ---- 汇总打印 ----
    print("已写入:", KP_PATH)
    print("备份:", bak)
    print("DROPs:", len([x for x in log if x.startswith("DROP")]))
    print("level_span:")
    for k in sorted(out):
        print("  %-28s %-6s span=%-5s refs=%d scope(true)=%d" % (
            k, out[k]["knowledge_point"], tf_span[k], len(out[k]["syllabus_refs"]),
            sum(1 for v in out[k]["syllabus_refs"].values() if v)))
    print("DROP 明细:")
    for x in log:
        print("  ", x)


if __name__ == "__main__":
    main()