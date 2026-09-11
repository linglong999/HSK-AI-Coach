# -*- coding: utf-8 -*-
"""tutor_quality 首版评测用例生成脚本。

从现有黄金集（golden_v1_4.json + retell_golden.json）抽真句，
生成 tutor_quality/cases.json（统一 schema，不改写原数据集）。

复现: python -m datasets.eval.tutor_quality.build_cases
输出: datasets/eval/tutor_quality/cases.json
"""
import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
EVAL = os.path.dirname(_HERE)  # .../datasets/eval
CASES = os.path.join(_HERE, "cases.json")


def _load_golden():
    with open(os.path.join(EVAL, "golden_v1_4.json"), encoding="utf-8") as f:
        return json.load(f)["seed_golden"]


def _load_retell():
    with open(os.path.join(EVAL, "retell_golden.json"), encoding="utf-8") as f:
        return json.load(f)["items"]


def _base(item):
    return {
        "id": "TQ-" + item["item_id"].replace("-", ""),
        "source": "golden_v1_4",
        "source_id": item["item_id"],
        "input": item["original"],
        "context": item.get("context"),
        "_err": {  # 内部判分参照，不落 cases.json
            "error_span": item["error_span"],
            "error_type": item["error_type"],
            "correction": item["correction"],
            "clean": item["sub_type"] == "clean",
        },
    }


def build():
    golden = _load_golden()
    retell = _load_retell()

    cases = []

    # 1) clean_guard 误报对照（取 clean 前 6 条）→ 触发 R2
    clean = [g for g in golden if g["sub_type"] == "clean"][:6]
    for item in clean:
        base = _base(item)
        cases.append({
            "id": base["id"], "source": "golden_v1_4", "source_id": item["item_id"],
            "case_type": "clean_guard",
            "input": item["original"], "context": item.get("context"),
            "expected_behavior": {"detect": None, "must_explain": False, "must_verify": False},
            "redlines": ["R2"],
            "gold_judge_hint": None,
            "metadata": {"learner_level": item["learner_level"], "native_lang": "英语"},
        })

    # 2) error_recognize（偏误定位）→ 取 ERR 前 7 条
    err = [g for g in golden if g["sub_type"] != "clean" and g["golden_status"] == "reviewed"][:7]
    for item in err:
        cases.append({
            "id": "TQ-" + item["item_id"].replace("-", ""), "source": "golden_v1_4",
            "source_id": item["item_id"], "case_type": "explain",
            "input": item["original"], "context": item.get("context"),
            "expected_behavior": {
                "detect": {"span": item["error_span"], "type": item["error_type"],
                           "correction": item["correction"]},
                "must_explain": True, "must_verify": True,
            },
            "redlines": ["R1", "R3"],
            "gold_judge_hint": None,
            "metadata": {"learner_level": item["learner_level"], "native_lang": "英语"},
        })

    # 3) retell 复述验证（从 retell_golden 前 5 条，简单句优先）→ 触发复述严格度
    # 取 references 数最少的前 5 条，保证句子短
    retell_sorted = sorted(retell, key=lambda x: (len(x["raw_sentence"]), len(x["references"])))[:5]
    for i, item in enumerate(retell_sorted):
        cases.append({
            "id": f"TQ-RT-{i+1:03d}", "source": "retell_golden", "source_id": item["source_id"],
            "case_type": "retell",
            "input": item["raw_sentence"], "context": None,
            "expected_behavior": {
                "detect": None, "must_explain": False, "must_verify": True,
                "retell_references": item["references"],
            },
            "redlines": ["R5"],
            "gold_judge_hint": None,
            "metadata": {"learner_level": None, "native_lang": "英语"},
        })

    # 4) adversarial/redline（从 golden ADV，制造 R1/R3 判定样本）——golden 无 ADV 前缀，跳过
    # 首版 6 + 7 + 5 = 18 条，另加 2 条 error 重复变体凑 20（用不同案列 balance）
    extra = [g for g in err[6:7] or golden if g["sub_type"] != "clean"][:2]
    for item in extra[:2]:
        cases.append({
            "id": "TQ-" + item["item_id"].replace("-", "") + "-E1",
            "source": "golden_v1_4", "source_id": item["item_id"], "case_type": "intervention",
            "input": item["original"], "context": item.get("context"),
            "expected_behavior": {"detect": None, "must_explain": True, "must_verify": True},
            "redlines": ["R4"],
            "gold_judge_hint": None,
            "metadata": {"learner_level": item["learner_level"], "native_lang": "英语"},
        })

    with open(CASES, "w", encoding="utf-8") as f:
        json.dump({"meta": {"name": "tutor_cases", "version": "0.1",
                            "date": "2026-09-11", "note": "复用 golden/retell 真句，补预期行为与红线"},
                   "cases": cases}, f, ensure_ascii=False, indent=2)
    print(f"wrote {len(cases)} cases -> {CASES}")


if __name__ == "__main__":
    build()