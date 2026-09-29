"""盲标包必须去掉原始标签及带标签的源 ID。"""

import json
from pathlib import Path

from datasets.eval.prepare_teacher_pack import build_pack


EVAL_DIR = Path(__file__).resolve().parents[1] / "datasets" / "eval"


def test_teacher_pack_blinds_labels_and_separates_mapping():
    pack, mapping = build_pack(EVAL_DIR / "golden_v1_4.json",
                               EVAL_DIR / "retell_verification.json")
    assert len(pack["items"]) == len(mapping["private_mapping"]) == 27
    ids = [item["case_id"] for item in pack["items"]]
    assert len(set(ids)) == 27
    visible = json.dumps(pack, ensure_ascii=False)
    for forbidden in ("golden_status", "need_arbitration", "source_id",
                      "HSK1-ADV-004", "HSK2-ERR-025:hollow", '"truth"'):
        assert forbidden not in visible
    assert all(item["annotation"]["label"] is None for item in pack["items"])
    assert any(row["source_id"] == "HSK1-ADV-004"
               for row in mapping["private_mapping"])
    assert build_pack(EVAL_DIR / "golden_v1_4.json",
                      EVAL_DIR / "retell_verification.json")[0] == pack
