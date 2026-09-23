# -*- coding: utf-8 -*-
"""tutor_quality 首版评测用例生成脚本。

从现有黄金集（golden_v1_4.json + retell_golden.json）抽真句，
生成 tutor_quality/cases.json（统一 schema，不改写原数据集）。
B6 J1 扩充：自然度锚 case（附录 D-4 正/负样例，L2 维度 7–10 判分种子），
  用 --no-anchors 可关（保持只出 18/19 条黄金派生）。

复现: python -m datasets.eval.tutor_quality.build_cases
      python -m datasets.eval.tutor_quality.build_cases --no-anchors
输出: datasets/eval/tutor_quality/cases.json
"""
import argparse
import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
EVAL = os.path.dirname(_HERE)  # .../datasets/eval
CASES = os.path.join(_HERE, "cases.json")

# B6 J1：自然度 4 维（rubric v2 二 · 7 任务真实性/8 角色代入/9 非命令性/10 对话推进感）
#   正样例 ≈ pass 锚（应高分）、负样例 ≈ fail 锚（应被打压）。
#   gold_judge_hint 携带每维度预期分（供 judge-human 校准 seed，预标注 gold）。
NATURALNESS_ANCHORS = [
    # ---- pass 锚（4 个：每维一个高分样例）----
    {"id": "TQ-NAT-001", "anchor_dim": "任务真实性", "expect": "pass",
     "input": "（点餐）你要点什么？我们看下菜单……要不要再来杯茶？",
     "hint": {"task_authenticity": 5, "role_immersion": 4, "non_command": 4,
              "dialogue_progress": 4}},
    {"id": "TQ-NAT-002", "anchor_dim": "角色代入", "expect": "pass",
     "input": "（服务员口吻）好嘞，一杯热茶马上就好，您稍等。",
     "hint": {"task_authenticity": 4, "role_immersion": 5, "non_command": 4,
              "dialogue_progress": 4}},
    {"id": "TQ-NAT-003", "anchor_dim": "非命令性", "expect": "pass",
     "input": "想先说说整件事，还是先练这个「把」结构？都行。",
     "hint": {"task_authenticity": 4, "role_immersion": 4, "non_command": 5,
              "dialogue_progress": 4}},
    {"id": "TQ-NAT-004", "anchor_dim": "对话推进感", "expect": "pass",
     "input": "你说顺了。那要是换成「我把书放到桌上」呢？我们推一步。",
     "hint": {"task_authenticity": 4, "role_immersion": 4, "non_command": 4,
              "dialogue_progress": 5}},
    # ---- fail 锚（4 个：每维一个应被打压样例）----
    {"id": "TQ-NAT-101", "anchor_dim": "任务真实性", "expect": "fail",
     "input": "请把「我把咖啡喝了」这个句子翻译成中文。",
     "hint": {"task_authenticity": 1, "role_immersion": 2, "non_command": 2,
              "dialogue_progress": 2}},
    {"id": "TQ-NAT-102", "anchor_dim": "角色代入", "expect": "fail",
     "input": "（冷读旁白）你输出了一句「我把咖啡喝了」。让我们分析一下。",
     "hint": {"task_authenticity": 2, "role_immersion": 1, "non_command": 3,
              "dialogue_progress": 2}},
    {"id": "TQ-NAT-103", "anchor_dim": "非命令性", "expect": "fail",
     "input": "现在必须复述这句话，连续两次，马上。",
     "hint": {"task_authenticity": 2, "role_immersion": 2, "non_command": 1,
              "dialogue_progress": 2}},
    {"id": "TQ-NAT-104", "anchor_dim": "对话推进感", "expect": "fail",
     "input": "很好。再来一次。再来一次。再来一次。",
     "hint": {"task_authenticity": 2, "role_immersion": 2, "non_command": 3,
              "dialogue_progress": 1}},
]


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


def build(include_anchors=True):
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

    if include_anchors:
        for a in NATURALNESS_ANCHORS:
            cases.append({
                "id": a["id"], "source": "rubric_v2_D4", "source_id": None,
                "case_type": "naturalness_anchor",
                "input": a["input"], "context": None,
                "expected_behavior": {"detect": None, "must_explain": False,
                                      "must_verify": False},
                "redlines": [],
                "gold_judge_hint": a["hint"],
                "metadata": {"learner_level": None, "native_lang": "英语",
                             "anchor_dim": a["anchor_dim"],
                             "anchor_expect": a["expect"]},
            })

    with open(CASES, "w", encoding="utf-8") as f:
        json.dump({"meta": {"name": "tutor_cases", "version": "0.2",
                            "date": "2026-09-23",
                            "note": ("golden/retell 真句 + 自然度锚 case（rubric v2 "
                                     "附录 D-4 正负样例）")},
                   "cases": cases}, f, ensure_ascii=False, indent=2)
    print(f"wrote {len(cases)} cases -> {CASES}")


if __name__ == "__main__":
    _ap = argparse.ArgumentParser()
    _ap.add_argument("--no-anchors", action="store_true",
                     help="不生成自然度锚 case（只出黄金派生）")
    _args = _ap.parse_args()
    build(include_anchors=not _args.no_anchors)