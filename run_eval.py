# ============================================================
# 3.1 偏误识别引擎 baseline 评测
# 输入：datasets/eval/golden_v1_4.json（v0.2，已过 check_dataset.py 校验）
# 输出：span-level F1 / type 识别率 / 干净句误报率 / 对抗 overcorrection 率 / 待确认比例
# 运行：python run_eval.py（项目根 HSK-AI-Coach/ 下）
# 依赖：DEEPSEEK_API_KEY 环境变量（或 .env）
# ============================================================

import os
import sys
import json
from collections import Counter

_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _PROJECT_ROOT)

from engine.recognizer import Recognizer


# Golden 偏误样本：span 命中（LLM fragment 与 golden span 或原句子串重叠）
def is_hit(fragment, golden_span, original):
    if not fragment:
        return False
    # 精准：fragment == error_span
    if golden_span and fragment == golden_span:
        return True
    # 兼容：golden span 是原句子串，且 fragment 是 span 的子串，或反之
    return bool(fragment in original)


def run():
    with open(os.path.join(_PROJECT_ROOT, "datasets", "eval", "golden_v1_4.json"), encoding="utf-8") as f:
        golden = json.load(f)

    seed = golden["seed_golden"]
    adv = golden["adversarial"]
    all_items = seed + adv

    rec = Recognizer()
    print("=" * 62)
    print("3.1 偏误识别引擎 baseline 评测（golden v0.2）")
    print(f"样本：seed {len(seed)} + 对抗 {len(adv)} = {len(all_items)}")
    print("=" * 62)

    rows = []
    tp = fp = fn = 0          # span 级 检测
    type_correct = type_total = 0   # type 识别
    clean_fp = clean_total = 0      # 干净句误报
    adv_over = adv_clean = 0        # 对抗 overcorrection（对抗且应为干净的句）
    uncertain_cnt = 0

    for item in all_items:
        iid, text = item["item_id"], item["original"]
        level = item["learner_level"]
        golden_span = item.get("error_span")
        golden_type = item.get("error_type")
        is_error = golden_span is not None  # True=应识别出偏误, False=干净句

        try:
            result = rec.recognize(text, level=level)
        except Exception as e:
            print(f"\n[{iid}] 调用失败: {e}")
            rows.append({"id": iid, "error": str(e)})
            continue

        # 合并识别输出（确认 + 待确认都算"识别到偏误"；uncertain 单列比例）
        found = result["errors"] + result["uncertain"]
        found_types = [e.get("type", "") for e in found]
        frames = [e.get("fragment", "") for e in found]
        uncertain_cnt += len(result["uncertain"])

        # ---- 检测判定 ----
        any_hit = any(is_hit(fr, golden_span, text) for fr in frames)
        if is_error:  # golden 有偏误
            if any_hit:
                tp += 1
            else:
                fn += 1
        else:  # 干净句
            clean_total += 1
            if found:
                clean_fp += 1  # 误报（在干净句上识别出偏误）

        # 对抗 overcorrection：对抗样本中应为干净的，若报偏误计 over
        if iid.startswith("HSK") and "ADV" in iid and golden_span is None:
            adv_clean += 1
            if found:
                adv_over += 1

        # ---- type 识别（仅 golden 有偏误且有命中时）----
        if is_error and any_hit:
            type_total += 1
            # 用命中该偏误的 fragment 对应 type 与 golden_type 比对
            matched_type = ""
            for fr, t in zip(frames, found_types):
                if is_hit(fr, golden_span, text):
                    matched_type = t
                    break
            if matched_type and matched_type == golden_type:
                type_correct += 1

        # 展示
        status = "TP" if (is_error and any_hit) else ("FN" if is_error else ("FP" if found else "TN"))
        det = "+".join(f"{fr}({t})" for fr, t in zip(frames, found_types)) or "—"
        rows.append({
            "id": iid, "level": level, "golden_span": golden_span,
            "golden_type": golden_type, "status": status, "det": det,
            "uncertain": len(result["uncertain"]),
        })
        print(f"[{status}] {iid} L{level} | golden={golden_span or '无(干净)'}/{golden_type or '-'} | 检测={det}")

    # ---- 指标计算 ----
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec_ = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * prec * rec_ / (prec + rec_) if (prec + rec_) else 0.0
    type_acc = type_correct / type_total if type_total else 0.0
    clean_fp_rate = clean_fp / clean_total if clean_total else 0.0
    adv_over_rate = adv_over / adv_clean if adv_clean else 0.0

    print("\n" + "=" * 62)
    print("BASELINE 指标（初值，待 3.3 扩集标定）")
    print("=" * 62)
    print(f"span 检测:  TP={tp} FP={fp} FN={fn}")
    print(f"  精确率   {prec:.2%}")
    print(f"  召回率   {rec_:.2%}")
    print(f"  F1       {f1:.2f}")
    print(f"type 识别率 (命中样本内): {type_correct}/{type_total} = {type_acc:.2%}")
    print(f"干净句误报率: {clean_fp}/{clean_total} = {clean_fp_rate:.2%}")
    print(f"对抗 overcorrection 率: {adv_over}/{adv_clean} = {adv_over_rate:.2%}")
    print(f"待确认(uncertain)偏误数: {uncertain_cnt}")

    # 持久化逐条结果
    out_path = os.path.join(_PROJECT_ROOT, "datasets", "eval", "eval_results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "version": "golden_v1_4_v0.2",
            "metrics": {
                "precision": prec, "recall": rec_, "f1": f1,
                "type_accuracy": type_acc,
                "clean_fp_rate": clean_fp_rate,
                "adversarial_overcorrection_rate": adv_over_rate,
                "uncertain_count": uncertain_cnt,
            },
            "rows": rows,
        }, f, ensure_ascii=False, indent=2)
    print(f"\n逐条结果已存: {out_path}")


if __name__ == "__main__":
    run()