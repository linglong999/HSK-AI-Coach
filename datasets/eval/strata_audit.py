"""P2-2 只读分层审计：盘点字段覆盖，不计算伪造的当前模型准确率。"""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


EVAL_DIR = Path(__file__).resolve().parent


def _read(path: Path) -> tuple[dict, str]:
    raw = path.read_bytes()
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(f"数据文件顶层应为对象：{path.name}")
    return data, hashlib.sha256(raw).hexdigest()


def _counts(rows: list[dict], key: str, *, clean_label: str | None = None) -> dict:
    values = []
    for row in rows:
        value = row.get(key)
        if value is None or value == "":
            value = clean_label if clean_label is not None else "unknown"
        values.append(str(value))
    return dict(sorted(Counter(values).items()))


def _check_ids(rows: list[dict], key: str, label: str) -> None:
    ids = [row.get(key) for row in rows]
    if any(not isinstance(item, str) or not item for item in ids):
        raise ValueError(f"{label} 存在空 ID")
    if len(ids) != len(set(ids)):
        raise ValueError(f"{label} 存在重复 ID")


def audit(golden_path: Path, retell_path: Path, results_path: Path | None = None) -> dict:
    golden, golden_sha = _read(golden_path)
    retell, retell_sha = _read(retell_path)
    recognition_rows = golden.get("seed_golden", []) + golden.get("adversarial", [])
    retell_rows = retell.get("items", [])
    if not all(isinstance(rows, list) and all(isinstance(row, dict) for row in rows)
               for rows in (recognition_rows, retell_rows)):
        raise ValueError("语料 items 应为对象数组")
    _check_ids(recognition_rows, "item_id", "识别语料")
    _check_ids(retell_rows, "id", "复述语料")

    # 不从 HSK 前缀或汉语句子猜母语/等级；没有显式字段就如实报 unknown。
    rec_l1 = [dict(row, learner_l1=row.get("learner_l1") or row.get("native_lang"))
              for row in recognition_rows]
    ret_l1 = [dict(row, learner_l1=row.get("learner_l1") or row.get("native_lang"))
              for row in retell_rows]
    ret_levels = [dict(row, learner_level=row.get("learner_level")) for row in retell_rows]
    result = {
        "source": {
            "golden_version": golden.get("version"), "golden_sha256": golden_sha,
            "retell_version": (retell.get("meta") or {}).get("version"),
            "retell_sha256": retell_sha,
        },
        "recognition": {
            "task": "recognition", "n": len(recognition_rows),
            "by_level": _counts(recognition_rows, "learner_level"),
            "by_l1": _counts(rec_l1, "learner_l1"),
            "by_error_type": _counts(recognition_rows, "error_type", clean_label="clean"),
            "by_status": _counts(recognition_rows, "golden_status"),
            "adversarial_n": len(golden.get("adversarial", [])),
        },
        "retell": {
            "task": "retell", "n": len(retell_rows),
            "by_level": _counts(ret_levels, "learner_level"),
            "by_l1": _counts(ret_l1, "learner_l1"),
            "by_error_type": _counts(retell_rows, "error_type"),
            "by_truth": _counts(retell_rows, "truth"),
            "by_source": _counts(retell_rows, "source"),
            "need_arbitration_n": sum(row.get("need_arbitration") is True for row in retell_rows),
        },
    }
    if results_path is not None:
        historical, historical_sha = _read(results_path)
        reasons = []
        if historical.get("version") != golden.get("version"):
            reasons.append("golden_version_mismatch")
        if historical.get("source_sha256") != golden_sha:
            reasons.append("source_sha256_missing_or_mismatch")
        result_rows = historical.get("rows")
        source_ids = {row["item_id"] for row in recognition_rows}
        if (not isinstance(result_rows, list)
                or any(not isinstance(row, dict) for row in result_rows)
                or any(not isinstance(row.get("id"), str) for row in result_rows)
                or len(result_rows) != len(source_ids)
                or {row.get("id") for row in result_rows} != source_ids):
            reasons.append("result_ids_missing_or_mismatch")
        result["historical_results"] = {
            "file_sha256": historical_sha,
            "version": historical.get("version"),
            "join_allowed": not reasons,
            "reasons": reasons,
        }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="P2-2 识别/复述语料只读分层盘点")
    parser.add_argument("--golden", type=Path, default=EVAL_DIR / "golden_v1_4.json")
    parser.add_argument("--retell", type=Path, default=EVAL_DIR / "retell_verification.json")
    parser.add_argument("--results", type=Path, default=EVAL_DIR / "eval_results.json")
    args = parser.parse_args()
    print(json.dumps(audit(args.golden, args.retell, args.results),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
