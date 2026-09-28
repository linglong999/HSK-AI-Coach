# -*- coding: utf-8 -*-
# ============================================================
# B6 校准 · calibrate_focus 对拍单测
# 锁「CSV 人工分 → 配对 → quadratic kappa 报告」管线：
#   - 解析：合法/坏行/未填/重复/越界
#   - 转换：漂移 id→skips、focus 与语料不匹配→mismatch、只留 cases 内
#   - 配对：只取有分条目、judge 缺维→missing、配对顺序稳定
#   - 对拍：报告 kappa 必须 == 直接用配对数组调 quadratic_kappa（证明配对拼装正确）；
#           合格判定、分歧(分差≥2)清单、按维分桶
# kappa 实现本身由 test_cohen_kappa 独立对拍，此处不重测。
# ============================================================
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_CAL = os.path.join(os.path.dirname(_HERE), "datasets", "eval", "calibration")
sys.path.insert(0, _CAL)

import pytest  # noqa: E402

import calibrate_focus as cf  # noqa: E402
from calibrate_judge import quadratic_kappa  # noqa: E402

# --- 合成夹具 ----------------------------------------------------------
CASES = {"C1": {"id": "C1", "dim": 7, "quality": "high", "gold_pass": True,
                "text": "（点餐）你要点什么？" if False else "（点餐）你要点什么？我们看下菜单。"},
         "C2": {"id": "C2", "dim": 7, "quality": "mid", "gold_pass": False, "text": "我们来练习点餐。"},
         "C3": {"id": "C3", "dim": 9, "quality": "high", "gold_pass": True, "text": "你自己先试试？"},
         "C4": {"id": "C4", "dim": 10, "quality": "low", "gold_pass": False, "text": "很好。再来一次。"}}

JUDGE = {
    "C1": {"verdict": "pass", "per_dim": {"7": 4, "1": 3, "2": 1}},
    "C2": {"verdict": "pass", "per_dim": {"7": 1, "1": 2, "2": 1}},
    "C3": {"verdict": "pass", "per_dim": {"9": 5, "1": 2, "2": 1}},
    # C4：judge 缺 focus_dim 10，用于测 missing
    "C4": {"verdict": "pass", "per_dim": {"10": None}},
}

HEADER = "id,focus_dim,文本,build_placeholder,构建质量锚,judge在该维参考,你的focus分(1-5),你判该样本是否达标(pass/fail),备注"
GOOD = {
    "C1": (7, "4", "pass"),
    "C2": (7, "1", "fail"),
    "C3": (9, "2", "pass"),
    "C4": (10, "3", ""),   # 有分但 unqualified 已经；judge 缺维
}


def _write_csv(tmp_path, rows, header=HEADER):
    p = tmp_path / "users.csv"
    lines = [header] + rows
    p.write_text("\n".join(lines), encoding="utf-8")
    return str(p)


def _good_rows():
    return [f"{k},{fd},t,anch,{q},{j},{s},{r},"
            for k, (fd, s, r) in GOOD.items()
            for q, j in [("high", 4)]]  # placeholder, judge ref col 填 0 亦可


def test_parse_good(tmp_path):
    rows = ["C1,7,text,anch,high,4,5,pass,", "C2,7,text,anch,mid,0,2,fail,"]
    users, errs = cf.parse_user_csv(_write_csv(tmp_path, rows))
    assert errs == []
    assert users["C1"] == {"focus_dim": 7, "focus_score": 5, "reach": True}
    assert users["C2"] == {"focus_dim": 7, "focus_score": 2, "reach": False}


def test_parse_partial_and_errors(tmp_path):
    rows = [
        "C1,7,text,anch,high,4,5,pass,",      # ok
        "C2,7,text,anch,mid,0,,,",             # 未填分 → score None
        "C3,9,text,anch,high,0,6,pass,",       # 越界 6 → error
        "C4,10,text,anch,low,0,abc,fail,",     # 非整 → error
        "C5,abc,text,anch,mid,0,3,pass,",      # focus 非整 → error
        "C6,1,text,anch,mid,0,3,pass,",        # 重复 id（下一条同 id）
        "C6,1,text,anch,mid,0,4,pass,",        # dup
    ]
    users, errs = cf.parse_user_csv(_write_csv(tmp_path, rows))
    assert users["C2"]["focus_score"] is None
    assert users["C1"]["focus_score"] == 5
    assert any("C3" in e and "超出" in e for e in errs)
    assert any("C4" in e and "非整数" in e for e in errs)
    assert any("C5" in e and "非整数" in e for e in errs)
    assert any("重复 id: C6" in e for e in errs)


def test_to_human_verdicts_filter(tmp_path):
    rows = [
        "C1,7,text,anch,high,4,5,pass,",
        "C2,8,text,anch,mid,0,3,pass,",        # focus 与语料(dim7)不符 → mismatch
        "CX,7,text,anch,high,0,4,pass,",       # 不在 cases → skips
    ]
    users, _ = cf.parse_user_csv(_write_csv(tmp_path, rows))
    human, conv = cf.to_human_verdicts(users, CASES)
    assert "CX" in conv["skips"]
    assert any("C2" in m and "不一致" in m for m in conv["mismatch"])
    assert set(human) == {"C1"}


def test_build_pairs_and_kappa_equivalence(tmp_path):
    rows = ["C1,7,text,anch,high,4,4,pass,",
            "C2,7,text,anch,mid,0,1,fail,",
            "C3,9,text,anch,high,0,2,pass,",
            "C4,10,text,anch,low,0,3,fail,"]   # C4 judge 缺 focus → missing
    users, _ = cf.parse_user_csv(_write_csv(tmp_path, rows))
    human, _ = cf.to_human_verdicts(users, CASES)
    jf, hf, ids, missing = cf.build_pairs(human, JUDGE)
    assert missing == ["C4"]                    # judge 缺维进 missing
    assert ids == ["C1", "C2", "C3"]            # 顺序稳定，只含已填且 judge 有分
    assert jf == [4, 1, 5] and hf == [4, 1, 2]  # judge 取值来自 per_dim[focus]
    report, pairs = cf.calibrate_focus(human, JUDGE)
    assert report["kappa_focus_quadratic"] == pytest.approx(quadratic_kappa(jf, hf), abs=1e-3)


def test_calibrate_focus_report(tmp_path):
    rows = ["C1,7,text,anch,high,4,4,pass,",
            "C2,7,text,anch,mid,0,1,fail,",
            "C3,9,text,anch,high,0,2,pass,"]
    users, _ = cf.parse_user_csv(_write_csv(tmp_path, rows))
    human, _ = cf.to_human_verdicts(users, CASES)
    report, _ = cf.calibrate_focus(human, JUDGE)
    jf, hf = [4, 1, 5], [4, 1, 2]
    kappa = quadratic_kappa(jf, hf)
    assert report["n_paired"] == 3
    assert report["kappa_focus_quadratic"] == pytest.approx(kappa, abs=1e-3)
    assert report["judge_qualified"] == (kappa >= cf.KAPPA_THRESHOLD)
    # 分歧：C3 judge5 human2 (span3) ≥ 2 → 列表含 C3；C1/C2 无分歧
    spans = {d["id"]: d["span"] for d in report["disagreements_span_ge2"]}
    assert spans.get("C3") == 3
    assert "C1" not in spans and "C2" not in spans
    # 按维分桶：dim7 有 2 条
    assert report["per_dim"]["7"]["n"] == 2 and report["per_dim"]["9"]["n"] == 1


def test_classify_kappa():
    assert cf.classify_kappa(0.85)[0] == "almost_perfect"
    assert cf.classify_kappa(0.70)[0] == "substantial"
    assert cf.classify_kappa(0.50)[0] == "moderate"
    assert cf.classify_kappa(0.30)[0] == "fair"
    assert cf.classify_kappa(0.10)[0] == "poor"
    assert cf.classify_kappa(None)[0] == "n/a"