"""P2-1 去标签教师标注包；原始 ID 映射仅供协调者保存。"""

import argparse
import hashlib
import json
from pathlib import Path
import random


EVAL_DIR = Path(__file__).resolve().parent


def _read(path: Path) -> tuple[dict, str]:
    raw = path.read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def build_pack(golden_path: Path, retell_path: Path, *, seed: int = 20260929) -> tuple[dict, dict]:
    """只返回内存对象，不自动触碰真实学习者数据或改写金标。"""
    golden, golden_hash = _read(golden_path)
    retell, retell_hash = _read(retell_path)
    cases = []
    for row in golden["seed_golden"] + golden["adversarial"]:
        if row.get("golden_status") == "disputed":
            cases.append(("recognition", row["item_id"], {
                "task": "recognition", "learner_level": row.get("learner_level"),
                "sentence": row["original"], "context": row.get("context"),
            }))
    for row in retell["items"]:
        if row.get("need_arbitration") is True:
            cases.append(("retell", row["id"], {
                "task": "retell", "explanation": row["explanation"],
                "key_points": row["key_points"], "restatement": row["restatement"],
            }))
    random.Random(seed).shuffle(cases)
    items, mapping = [], []
    for number, (task, source_id, visible) in enumerate(cases, 1):
        case_id = f"P2-{number:03d}"
        items.append({"case_id": case_id, **visible, "annotation": {
            "label": None, "error_span": None, "error_type": None,
            "acceptable_alternatives": [], "covered_points": [],
            "rationale": None, "confidence": None,
        }})
        mapping.append({"case_id": case_id, "task": task,
                        "source_id": source_id})
    metadata = {"schema_version": "0.1", "seed": seed,
                "golden_sha256": golden_hash, "retell_sha256": retell_hash}
    return ({"meta": metadata, "items": items},
            {"meta": metadata, "private_mapping": mapping})


def main() -> None:
    parser = argparse.ArgumentParser(description="生成教师盲标包与独立的协调者 ID 映射")
    parser.add_argument("--golden", type=Path, default=EVAL_DIR / "golden_v1_4.json")
    parser.add_argument("--retell", type=Path, default=EVAL_DIR / "retell_verification.json")
    parser.add_argument("--teacher-pack", type=Path, required=True)
    parser.add_argument("--coordinator-map", type=Path, required=True)
    args = parser.parse_args()
    teacher = args.teacher_pack.resolve(strict=False)
    coordinator = args.coordinator_map.resolve(strict=False)
    if teacher == coordinator or teacher.exists() or coordinator.exists():
        parser.error("两个输出路径须不同，且均为不存在的新文件")
    project_root = EVAL_DIR.parents[1]
    if teacher.is_relative_to(project_root) or coordinator.is_relative_to(project_root):
        parser.error("标注包与协调者映射须保存在仓库目录之外")
    if not teacher.parent.is_dir() or not coordinator.parent.is_dir():
        parser.error("输出目录须已存在")
    pack, mapping = build_pack(args.golden, args.retell)
    # 排他创建；若第二次写失败，保留第一份供人工检查，不自动删除未知目标。
    with teacher.open("x", encoding="utf-8") as file:
        json.dump(pack, file, ensure_ascii=False, indent=2)
    with coordinator.open("x", encoding="utf-8") as file:
        json.dump(mapping, file, ensure_ascii=False, indent=2)
    print(f"已生成 {len(pack['items'])} 条盲标样本；协调者映射勿发给教师")


if __name__ == "__main__":
    main()
