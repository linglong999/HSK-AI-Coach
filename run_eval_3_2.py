# ============================================================
# 3.2 讲解引擎效果评测
# 输入：datasets/eval/golden_v1_4.json 的偏误样本（17 条）
# 输出：
#   - 自动指标：修正忠实性 / 超纲率(近似) / 结构合规率 / 字数
#   - 人工抽评表：每条讲解 + checklist（费曼式/留白引导），导出 json 供人工判定
# 运行：
#   python run_eval_3_2.py                # 全量评测
#   python run_eval_3_2.py --limit 1      # 单样本 dry-run（验证链路）
# 依赖：DEEPSEEK_API_KEY
# ============================================================

import os
import sys
import json
import argparse

_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _PROJECT_ROOT)

from engine.explainer import Explainer
from engine.llm.client import JSONStrictError


# ---------- 自动指标 ----------
def eval_correction_fidelity(explanation: str, correction: str) -> bool:
    """修正忠实性：讲解是否包含 correction 的核心字面（忠实传递，不篡改/替换）"""
    if not correction:
        return True   # 无修正要求视为通过
    return correction in explanation or any(len(c) >= 2 and c in explanation
                                            for c in correction.replace("，", ",").split(",") if c.strip())


# 常见超纲高频词（近似指标：MVP 用 HSK1-4 高频词清单做弱检测，3.x 换权威等级词表）
_SUPER_HIGH_LEVEL_MARKERS = [
    "尽管", "既然", "何况", "不惜", "与其", "除非", "固然", "况且",
    "成语", "波澜壮阔", "持之以恒", "循序渐进", "融会贯通",
]


def eval_beyond_level(explanation: str, level: int) -> list:
    """超纲率近似：命中超纲高频词标记则记录。弱势指标，仅供趋势，非权威。"""
    hits = [w for w in _SUPER_HIGH_LEVEL_MARKERS if w in explanation]
    return hits


def eval_structure(raw: dict) -> dict:
    """结构合规：key_points 带 id / free_generated 标记 / 字数"""
    kps = raw.get("key_points", [])
    kp_ok = isinstance(kps, list) and all(isinstance(k, dict) and k.get("id") for k in kps)
    exp = raw.get("explanation", "")
    return {
        "length": len(exp),
        "length_ok": 0 < len(exp) <= 150,
        "key_points_ids_ok": kp_ok,
        "key_points_count": len(kps) if isinstance(kps, list) else 0,
        "free_generated": raw.get("free_generated", False),
        "uncertain_note_present": bool(raw.get("uncertain_note")),
        "is_keyword_list": isinstance(raw.get("keywords"), list),
    }


def run(limit: int = None, out_path: str = None):
    golden_path = os.path.join(_PROJECT_ROOT, "datasets", "eval", "golden_v1_4.json")
    with open(golden_path, encoding="utf-8") as f:
        golden = json.load(f)
    errs = [s for s in (golden["seed_golden"] + golden["adversarial"])
            if s.get("error_type")]
    if limit:
        errs = errs[:limit]

    explainer = Explainer()
    print("=" * 62)
    print("3.2 讲解引擎效果评测（golden 偏误样本）")
    print(f"样本：{len(errs)} 条偏误讲解")
    print("=" * 62)

    rows = []
    fidelity_ok = struct_ok = total = 0
    superlevel_hits = []
    fail_log = []

    for s in errs:
        iid = s["item_id"]
        error = {
            "sentence": s["original"],
            "fragment": s.get("error_span", s["original"]),
            "correction": s.get("correction", ""),
            "type": s.get("error_type", ""),
            "knowledge_point_id": "",
            "level": f"HSK{s['learner_level']}",
            "beyond_level": False,
            "uncertain": False,
        }
        try:
            raw = explainer.explain(
                {**error, "sentence": s["original"]},
                user_level=f"HSK{s['learner_level']}",
                native_lang=s.get("context", {}).get("native") if isinstance(s.get("context"), dict) else "",
                candidate_points=None,
            )
        except Exception as e:
            fail_log.append({"id": iid, "error": repr(e)})
            rows.append({"id": iid, "status": "FAIL", "error": repr(e)})
            print(f"[FAIL] {iid}: {e}")
            continue

        total += 1
        # 自动指标
        corr_fid = eval_correction_fidelity(raw.get("explanation", ""), s.get("correction", ""))
        if corr_fid:
            fidelity_ok += 1
        else:
            fail_log.append({"id": iid, "metric": "fidelity", "reason": "讲解未含修正"})
        beyond = eval_beyond_level(raw.get("explanation", ""), s["learner_level"])
        if beyond:
            superlevel_hits.append({"id": iid, "words": beyond})
        struct = eval_structure(raw)
        if struct["length_ok"] and struct["key_points_ids_ok"] and struct["is_keyword_list"]:
            struct_ok += 1

        rows.append({
            "id": iid,
            "level": f"HSK{s['learner_level']}",
            "original": s["original"],
            "golden_correction": s.get("correction", ""),
            "explanation": raw.get("explanation", ""),
            "key_points": raw.get("key_points", []),
            "keywords": raw.get("keywords", []),
            "auto": {"correction_fidelity": corr_fid, "beyond_level_words": beyond,
                     **struct},
            "manual": {"feynman_method_ok": None, "retention_prompt_ok": None,
                       "accurate_ok": None, "note": ""},
        })
        print(f"[OK] {iid} | 忠实={corr_fid} 超纲={beyond or '无'} 字数={struct['length']}")

    # 汇总
    print("\n" + "=" * 62)
    print("3.2 自动指标汇总（初值，待 3.3 标定）")
    print("=" * 62)
    print(f"成功讲解: {total}/{len(errs)}")
    if total:
        print(f"修正忠实率: {fidelity_ok}/{total} = {fidelity_ok/total:.2%}")
        print(f"结构合规率(字数+kp id+keywords): {struct_ok}/{total} = {struct_ok/total:.2%}")
    print(f"超纲词命中: {len(superlevel_hits)} 处")
    if fail_log:
        print("\n[FAIL] 异常/未达标项:")
        for x in fail_log:
            print("  ", x)

    # 导出（自动指标 + 人工抽评清单）
    out_path = out_path or os.path.join(_PROJECT_ROOT, "datasets", "eval", "eval_3_2_results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "metrics": {
                "success": total, "total": len(errs),
                "correction_fidelity_rate": (fidelity_ok / total) if total else None,
                "structure_compliance_rate": (struct_ok / total) if total else None,
                "beyond_level_hit_count": len(superlevel_hits),
            },
            "rows": rows,
        }, f, ensure_ascii=False, indent=2)
    print(f"\n结果已存: {out_path}（含 auto 指标 + manual 待填 checklist）")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="只评测前 N 条（dry-run）")
    args = ap.parse_args()
    run(limit=args.limit)