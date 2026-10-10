import json
from copy import deepcopy
from pathlib import Path

import pytest

from engine.feedback_selector import select_feedback_strategies
from engine.intervention import decide_intervention


DATA = Path(__file__).parent / "data" / "feedback_replay_p34.json"
CASES = json.loads(DATA.read_text(encoding="utf-8"))["cases"]


def _run(case):
    payload = case["input"]
    level, reason = decide_intervention(
        payload["text"], max_conf=payload["max_conf"],
        recent_error_flags=payload["recent_error_flags"],
        interrupt_used=payload["interrupt_used"], cap=payload["cap"],
        last_level=payload["last_level"], diagnosed=payload["diagnosed"])
    strategies = select_feedback_strategies(
        payload["errors"], payload["hsk_level"], level,
        recurrence_map=payload["recurrence_map"],
        deep_dived_kps=payload["deep_dived_kps"],
        intervention_reason=reason, scene_active=payload["scene_active"],
        mastery_map=payload["mastery_map"])
    decisions = [{k: d.get(k) for k in ("target_kp", "mode", "reason_code")}
                 for d in strategies["decisions"]]
    return level, reason, decisions


def _assert_case(case):
    actual = _run(case)
    expected = case["expect"]
    assert actual[0] == expected["level"], case["id"]
    assert actual[1] == expected["reason"], case["id"]
    assert actual[2] == expected["decisions"], case["id"]
    return actual


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_replay_case(case):
    _assert_case(case)


def test_reason_code_mismatch_is_rejected():
    case = deepcopy(next(c for c in CASES if c["id"] == "S2"))
    case["expect"]["decisions"][0]["reason_code"] = "FIRST_PROMPT"
    with pytest.raises(AssertionError):
        _assert_case(case)


def test_p34_replay_matches_all_cases_and_covers_five_modes():
    data = json.loads(DATA.read_text(encoding="utf-8"))
    assert data["version"] == "p34-v1"
    assert len(data["cases"]) == 18
    assert {c["id"] for c in data["cases"]} == {
        "R1", "R2", "R3", "R4", *[f"S{i}" for i in range(1, 15)]}
    seen_modes = set()
    for case in data["cases"]:
        actual = _assert_case(case)
        seen_modes.update(d["mode"] for d in actual[2])
    assert seen_modes == {"recast", "intonation_hint", "local_question",
                          "contrastive_hint", "explicit_correction"}
