# -*- coding: utf-8 -*-
"""复述验证黄金集数据校验脚本（CI 雏形, v0.2）
校验 retell_verification.json 结构 + 样本分布。
用法: python datasets/eval/check_retell.py
"""
import json
import os
import sys
from collections import Counter

BASE = os.path.dirname(os.path.abspath(__file__))


def check_retell_verification():
    p = os.path.join(BASE, "retell_verification.json")
    d = json.load(open(p, encoding="utf-8"))
    items = d["items"]
    ver = d["meta"].get("version")
    errs = 0
    warns = 0

    ids = set()
    truth_ok = {"pass", "fail", "partial", "hollow"}
    for it in items:
        # 必备字段
        need = {"id", "source", "explanation", "key_points", "restatement", "truth", "need_arbitration"}
        if set(it.keys()) & need != need:
            errs += 1
        # id 唯一
        if it["id"] in ids:
            errs += 1
        ids.add(it["id"])
        # truth 枚举
        if it["truth"] not in truth_ok:
            errs += 1
        # 关键点结构
        kps = it["key_points"]
        if not isinstance(kps, list) or len(kps) == 0:
            errs += 1
        for kp in kps:
            if not (isinstance(kp, dict) and "id" in kp and "text" in kp and kp["text"]):
                errs += 1
        # need_arbitration 一致性：pass/fail→不要求仲裁, partial/hollow→要求
        expect_arb = it["truth"] in ("partial", "hollow")
        if it["need_arbitration"] != expect_arb:
            warns += 1
        # evid 一致性：pass/fail 应有 evid, hollow 一般无
        evid = it.get("evid", "")
        if it["truth"] in ("fail",) and not evid:
            warns += 1
        # 来源字段
        if it["source"] == "direction2_mucgec":
            if "source_id" not in it or not it["restatement"]:
                errs += 1
        elif it["source"] != "direction1_constructed":
            errs += 1
        # v0.3: iso_solution 校验 —— pass 多解必有; 非 pass 必为 1
        iso = it.get("iso_solution")
        if it["source"] == "direction2_mucgec":
            if not isinstance(iso, int) or iso < 1:
                errs += 1
            if iso > 1 and it["truth"] != "pass":
                errs += 1

    # 分布
    src = Counter(i["source"] for i in items)
    truth = Counter(i["truth"] for i in items)
    arb = sum(1 for i in items if i["need_arbitration"])

    print(f"[retell_verification v{ver}] {len(items)} 条")
    print(f"  来源: {dict(src)}")
    print(f"  truth: {dict(truth)}")
    print(f"  需仲裁(partial+hollow): {arb}")
    print(f"  结构错误 {errs} | 一致性警告 {warns}")
    return errs == 0


def check_retell():
    p = os.path.join(BASE, "retell_golden.json")
    d = json.load(open(p, encoding="utf-8"))
    items = d["items"]
    required = {"source", "source_id", "raw_sentence", "references", "golden_status"}
    errs = 0
    for it in items:
        if set(it.keys()) & required != required or it["source"] != "mucgec":
            errs += 1
        if not it["raw_sentence"] or len(it["references"]) < 1:
            errs += 1
    print(f"[retell_golden] {len(items)} 条 | 结构错误 {errs}")
    return errs == 0


def check_cged():
    p = os.path.join(BASE, "cged_recognition.json")
    d = json.load(open(p, encoding="utf-8"))
    items = d["items"]
    errs = 0
    warns = 0
    n_err = n_clean = 0
    for it in items:
        if it["source"] != "cged":
            errs += 1
        s = it["raw_sentence"]
        for sp in it["error_spans"]:
            st, en, frag = sp["start"], sp["end"], sp["fragment"]
            if not (0 <= st <= en <= len(s)):
                errs += 1
                continue
            if frag and s[st:en] != frag:
                is_boundary = frag.strip() == "" or (frag in ("我", "的", "你", "他", "是", "有", "了") and (st <= 1 or en >= len(s) - 1))
                if is_boundary:
                    warns += 1
                else:
                    errs += 1
        if it["has_error"] != (len(it["error_spans"]) > 0):
            errs += 1
        if it["has_error"]:
            n_err += 1
        else:
            n_clean += 1
    print(f"[cged_recognition] {len(items)} 句 | 带偏误 {n_err} | 纯句 {n_clean} | 结构错误 {errs} | 边界噪声(warn) {warns}")
    return errs == 0


if __name__ == "__main__":
    ok = check_retell_verification() and check_retell() and check_cged()
    print("校验:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)