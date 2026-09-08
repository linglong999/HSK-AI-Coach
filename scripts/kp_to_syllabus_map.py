# -*- coding: utf-8 -*-
# ============================================================
# scripts/kp_to_syllabus_map.py —— P0.15 v1_4 知识点 ↔ 考纲 疑似映射
#
# 结论：v1_4 25 个知识点是"教学抽象层"命名（"能愿动词（能/会/…)"),
#       考纲是"官方目录层"（类别名称=动词/代词/… + 细目=具体点），
#       两套命名体系字符串完全无交集（实测 593 全未命中）。
#   因此自动对照只能做"核心词模糊疑似"，精确映射需人工裁定。
#
# 方法：
#   1 从各 v1_4 知识点的 knowledge_point + note 提取核心词（显式表见下）
#   2 用核心词在考纲 item + grammar + name 全文子串模糊命中
#   3 输出三级：
#       [A] 精确项（核心词与考纲完全一致，实为子串全等/包含）
#       [B] 疑似项（核心词子串命中，供人工裁定）
#       [C] v1_4 中完全无考纲命中的知识点（需人工补考纲条/或本就超纲）
#
# 运行: python scripts/kp_to_syllabus_map.py
# ============================================================

import json
import os
import re
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from engine.syllabus import SyllabusDatabase

DATASETS = os.path.join(_PROJECT_ROOT, "datasets")
REPORTS = os.path.join(_PROJECT_ROOT, "reports")
SYLLABUS = os.path.join(DATASETS, "syllabus_hsk30_2025.json")
KP = os.path.join(DATASETS, "knowledge_points_v1_4.json")

# v1_4 知识点 → 考纲侧检索核心词（显式；命中&别名归一）
KP_KEYWORDS = {
    "kp-ba-sentence": ["把字句", "“把”"],
    "kp-bei-sentence": ["被字句", "被动句"],        # 别名：被动句↔被字句
    "kp-liangci": ["量词"],
    "kp-nengyuan-dongci": ["能愿动词", "能够"],
    "kp-bi-sentence": ["比较句", "比"],
    "kp-he-yiyang": ["一样", "和……一样", "和…一样"],
    "kp-le-dynamic": ["动态助词", "“了”", "动态助词"],
    "kp-zhe": ["动态助词", "着"],
    "kp-guo": ["动态助词", "过"],
    "kp-jiuguo-jiegou": ["结果补语", "补语"],
    "kp-quxiang-buyu": ["趋向补语", "趋向"],
    "kp-de-di-de": ["的地得", "结构助词", "地"],
    "kp-jianyu-ju2": [],   #（无 id，忽略）
    "kp-cunxian-ju": ["存现句"],
    "kp-liandong-ju": ["连动句", "连谓"],
    "kp-standing-shi": ["兼语句"],          # kp-standing-shi=兼语句（请/让/叫）
    "kp-shide-sentence": ["是……的", "“是…的”", "“是……的”"],
    "kp-dongci-shuangbin": ["双宾语句", "双宾语", "宾语1", "主语+动词+宾语1"],
    "kp-zhuangyu-chezhi": ["状语", "语序"],
    "kp-zhongci-zhitou": ["指示代词", "人称代词", "代词"],
    "kp-preposition-zaizai": ["介词", "在", "从", "给", "向"],
    "kp-jietiaoyu-tiaojian": ["关联词", "复句", "如果", "因为", "条件复句"],
    "kp-haishi-xuanze": ["选择问", "正反问", "还是", "疑问句"],
    "kp-chengdu-jieci": ["程度副词", "很", "太", "非常"],
    "kp-zhizhi-dao": ["时量补语", "动量补语", "补语"],
    "kp-zhongci-fugao": ["范围副词", "都", "也", "只", "还"],
}


def norm(s):
    return re.sub(r"[\s（）()\[\]{}“”\"'、…——]", "", s or "")


def main():
    os.makedirs(REPORTS, exist_ok=True)
    db = SyllabusDatabase.load(SYLLABUS)
    kps = json.load(open(KP, encoding="utf-8"))["knowledge_points"]

    hay_by_id = {}
    for p in db.all():
        hay_by_id[p["id"]] = norm(p["item"] + " " + p["name"] + " " + p["grammar"])

    report = []
    report.append("# v1_4 知识点 ↔ 考纲 疑似映射报告")
    report.append("")
    report.append("- v1_4 %d 个知识点（教学抽象层）· 考纲 %d 条（官方目录层）" % (len(kps), len(db.all())))
    report.append("- 两套命名体系字符串无交集，自动对照只能给**核心词疑似**，精确映射须人工裁定")
    report.append("")
    report.append("## A. 精确疑似（核心词完整出现在考纲条目中，高置信）")
    report.append("")
    report.append("## B. 疑似命中（核心词子串命中，需人工裁定）")
    report.append("")

    hit_count = 0
    unmatched_kps = []
    for kid, kp in kps.items():
        kw_list = KP_KEYWORDS.get(kid, [kp.get("knowledge_point", kid)])
        hits = set()
        for kw in kw_list:
            nkw = norm(kw)
            if not nkw:
                continue
            for sid, hay in hay_by_id.items():
                if nkw in hay:
                    hits.add(sid)
        if hits:
            report.append("- `%s` (%s) → 命中 %d 条：%s" %
                          (kid, kp.get("knowledge_point", ""), len(hits),
                           ", ".join(sorted(hits)[:12])))
            hit_count += 1
        else:
            unmatched_kps.append(kid)

    report.append("")
    report.append("## C. v1_4 中无考纲疑似命中的知识点（需人工补/判定超纲）")
    report.append("")
    for kid in unmatched_kps:
        report.append("- `%s` (%s)" % (kid, kps[kid].get("knowledge_point", "")))

    out = os.path.join(REPORTS, "kp_to_syllabus_map.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(report))
    print("报告已生成:", out)
    print("v1_4 命中疑似=%d · 无命中=%d" % (hit_count, len(unmatched_kps)))


if __name__ == "__main__":
    main()