"""P2-2：缺失维度不得凭 ID 猜测；旧结果不得跨版本拼接。"""

import json
import hashlib
from pathlib import Path

from datasets.eval.strata_audit import audit


EVAL_DIR = Path(__file__).resolve().parents[1] / "datasets" / "eval"


def test_repo_strata_and_historical_join_gate():
    result = audit(EVAL_DIR / "golden_v1_4.json",
                   EVAL_DIR / "retell_verification.json",
                   EVAL_DIR / "eval_results.json")
    assert result["recognition"]["n"] == 36
    assert result["recognition"]["by_l1"] == {"unknown": 36}
    assert result["recognition"]["by_error_type"]["clean"] == 19
    assert result["retell"]["n"] == 60
    assert result["retell"]["by_level"] == {"unknown": 60}
    assert result["retell"]["by_l1"] == {"unknown": 60}
    assert result["retell"]["need_arbitration_n"] == 26
    assert not result["historical_results"]["join_allowed"]
    assert "golden_version_mismatch" in result["historical_results"]["reasons"]


def test_duplicate_ids_rejected(tmp_path):
    golden = {"version": "v1", "seed_golden": [
        {"item_id": "duplicate"}, {"item_id": "duplicate"}], "adversarial": []}
    retell = {"meta": {"version": "v1"}, "items": []}
    gp, rp = tmp_path / "golden.json", tmp_path / "retell.json"
    gp.write_text(json.dumps(golden), encoding="utf-8")
    rp.write_text(json.dumps(retell), encoding="utf-8")
    import pytest
    with pytest.raises(ValueError, match="重复 ID"):
        audit(gp, rp)


def test_claimed_matching_version_and_hash_still_require_complete_ids(tmp_path):
    golden = {"version": "v1", "seed_golden": [
        {"item_id": "a"}, {"item_id": "b"}], "adversarial": []}
    retell = {"meta": {"version": "v1"}, "items": []}
    gp, rp, old = (tmp_path / name for name in ("golden.json", "retell.json", "old.json"))
    gp.write_text(json.dumps(golden), encoding="utf-8")
    rp.write_text(json.dumps(retell), encoding="utf-8")
    old.write_text(json.dumps({"version": "v1",
        "source_sha256": hashlib.sha256(gp.read_bytes()).hexdigest(),
        "rows": [{"id": "a"}, {"id": "a"}]}), encoding="utf-8")
    result = audit(gp, rp, old)
    assert not result["historical_results"]["join_allowed"]
    assert "result_ids_missing_or_mismatch" in result["historical_results"]["reasons"]
