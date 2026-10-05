import pytest
import math
from types import SimpleNamespace

from engine.feedback_selector import (
    FEEDBACK_MODES, HINT_LEVELS, MODE_HINT_LEVEL, REASON_CODES,
    compile_strategy_section, decide_feedback, select_feedback_strategies,
    _MODE_LABEL,
)
from engine.dialog_run import DialogRun
from engine.study import StudyError, StudyTracker


def err(sev=None, kp="kp-x", fragment="目标片段"):
    value = {"knowledge_point_id": kp, "fragment": fragment}
    if sev:
        value["verdict"] = sev
    return value


def decision(**kwargs):
    base = dict(position=0, is_deep=False, hsk_level=3,
                intervention_level="light", intervention_reason="",
                scene_active=False, recurrence=0, mastery=0.0)
    base.update(kwargs)
    return decide_feedback(err(), **base)


@pytest.mark.parametrize("kwargs,reason,mode", [
    ({"intervention_reason": "help"}, "HELP_REQUESTED", "explicit_correction"),
    ({"is_deep": True}, "DEEP_DIVE", "explicit_correction"),
    ({"scene_active": True}, "SCENE_LOW_IMPACT", "recast"),
    ({"intervention_level": "none"}, "FLUENT_KEEP_FLOW", "recast"),
    ({"hsk_level": 2, "recurrence": 1}, "LOW_LEVEL_MODELING", "recast"),
    ({"hsk_level": 2, "recurrence": 2}, "LOW_LEVEL_REPEAT", "explicit_correction"),
    ({"recurrence": 1, "mastery": 0.9}, "SLIP_ON_MASTERED", "intonation_hint"),
    ({"recurrence": 1}, "FIRST_PROMPT", "intonation_hint"),
    ({"recurrence": 2}, "REPEAT_1", "local_question"),
    ({"recurrence": 3}, "REPEAT_2", "contrastive_hint"),
    ({"recurrence": 4}, "REPEAT_ESCALATE", "explicit_correction"),
])
def test_every_rule_has_positive_example(kwargs, reason, mode):
    got = decision(**kwargs)
    assert got["reason_code"] == reason
    assert got["mode"] == mode
    assert got["hint_level"] == MODE_HINT_LEVEL[mode]


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), 2.5, 0, -1, "2"])
def test_numeric_normalization_for_hsk(value):
    got = decision(hsk_level=value, recurrence=1)
    assert got["reason_code"] == "FIRST_PROMPT"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), "bad"])
def test_numeric_normalization_for_recurrence_and_mastery(value):
    assert decision(recurrence=value)["reason_code"] == "FIRST_PROMPT"
    assert decision(recurrence=1, mastery=value)["reason_code"] == "FIRST_PROMPT"


def test_rule_priority_counterexamples():
    assert decision(intervention_reason="help", scene_active=True)["reason_code"] == "HELP_REQUESTED"
    assert decide_feedback(err("error"), position=0, is_deep=False, hsk_level=3,
                           intervention_level="light", intervention_reason="",
                           scene_active=True, recurrence=1, mastery=0.0)["reason_code"] == "FIRST_PROMPT"
    assert decision(hsk_level=2, recurrence=4)["reason_code"] == "LOW_LEVEL_REPEAT"
    assert decision(recurrence=2, mastery=0.9)["reason_code"] == "REPEAT_1"


def test_help_without_deep_dive_is_not_swallowed():
    errors = [err(kp="a"), err(kp="b")]
    out = select_feedback_strategies(errors, 3, "block", deep_dived_kps={"a", "b"},
                                     intervention_reason="help")
    assert out["deep_dive"] is None
    assert out["decisions"][0]["mode"] == "explicit_correction"
    assert out["decisions"][0]["reason_code"] == "HELP_REQUESTED"
    assert all(item["user_requested_help"] for item in out["decisions"])


def test_scene_low_impact_and_escalation():
    out = select_feedback_strategies([err(kp="a")], 3, "none", scene_active=True)
    assert out["decisions"][0]["reason_code"] == "SCENE_LOW_IMPACT"
    reasons = [select_feedback_strategies([err(kp="a")], 3, "light",
                                           recurrence_map={"a": c})["decisions"][0]["reason_code"]
               for c in (1, 2, 3, 4)]
    assert reasons == ["FIRST_PROMPT", "REPEAT_1", "REPEAT_2", "REPEAT_ESCALATE"]


def test_backward_compatible_selection_keys():
    cases = [
        (dict(), {"deep_dive": "a", "light_marks": ["b", "c"], "silent_kps": [], "deep_dive_count": 1}),
        ({"deep_dived_kps": {"a"}, "recurrence_map": {"b": 2}},
         {"deep_dive": "b", "light_marks": ["a", "c"], "silent_kps": [], "deep_dive_count": 1}),
        ({"intervention_level": "none"}, {"deep_dive": None, "light_marks": ["a", "b"], "silent_kps": ["c"], "deep_dive_count": 0}),
        ({"intervention_level": "light"}, {"deep_dive": None, "light_marks": ["a", "b"], "silent_kps": ["c"], "deep_dive_count": 0}),
    ]
    errors = [err(kp="a"), err(kp="b"), err(kp="c")]
    for options, expected in cases:
        level = options.pop("intervention_level", "block")
        out = select_feedback_strategies(errors, 3, level, **options)
        assert (out["deep_dive"] or {}).get("knowledge_point_id") == expected["deep_dive"] if expected["deep_dive"] else out["deep_dive"] is None
        assert [x["knowledge_point_id"] for x in out["light_marks"]] == expected["light_marks"]
        assert out["silent_kps"] == expected["silent_kps"]
        assert out["deep_dive_count"] == expected["deep_dive_count"]


def test_compile_modes_include_labels_fragments_and_no_reason_codes():
    decisions = [{"mode": mode, "hint_level": MODE_HINT_LEVEL[mode],
                  "reason_code": reason, "target_kp": "kp-x",
                  "target_fragment": f"片段-{index}"}
                 for index, (mode, reason) in enumerate(zip(FEEDBACK_MODES, REASON_CODES))]
    for lang in ("zh", "en"):
        section = compile_strategy_section({"decisions": decisions}, lang)
        lines = section.splitlines()
        assert lines[0].startswith("[FeedbackStrategy]")
        assert len([line for line in lines if line.startswith("-")]) == len(decisions) + 1
        for index, mode in enumerate(FEEDBACK_MODES):
            line = next(line for line in lines if f"片段-{index}" in line)
            assert _MODE_LABEL["zh" if lang == "zh" else "en"][mode] in line
        assert not any(code in section for code in REASON_CODES)
        if lang == "zh":
            assert "复习队列" in section


def test_mastery_exception_does_not_clear_other_recurrence():
    class Ledger:
        def repeated_count(self, kp):
            return {"bad": 0, "good": 2}[kp]
    class Graph:
        def get_kp(self, kp):
            if kp == "bad":
                raise RuntimeError("broken graph")
            return {"node": {"mastery": 0.0}}
    runner = DialogRun.__new__(DialogRun)
    runner._router = SimpleNamespace(graph=Graph())
    runner._wb = SimpleNamespace(ledger=Ledger())
    runner._deep_dived_kps = set()
    out = runner._select_and_log_strategies(
        {"errors": [err(kp="bad"), err(kp="good")]}, 3, "light", "zh", True, "input")
    by_kp = {item["target_kp"]: item["reason_code"] for item in out["decisions"]}
    assert by_kp["bad"] == "FIRST_PROMPT"
    assert by_kp["good"] == "REPEAT_1"


def test_study_summary_reads_decisions_only():
    rows = DialogRun._study_strategies({"preferences": [("input", "metalinguistic", "OLD")],
                                        "decisions": [{"target_kp": "kp-x", "mode": "recast",
                                                       "hint_level": "none", "reason_code": "FIRST_PROMPT"}]})
    assert rows == [{"kp_id": "kp-x", "mode": "recast", "hint_level": "none",
                     "reason_code": "FIRST_PROMPT"}]


def test_study_payload_validates_strategy_enums(tmp_path):
    tracker = StudyTracker(root=str(tmp_path))
    with pytest.raises(StudyError):
        tracker._validate_payload("feedback_presented", {"strategies": [{"mode": "metalinguistic"}]})
    with pytest.raises(StudyError):
        tracker._validate_payload("feedback_presented", {"strategies": [{"hint_level": "huge"}]})
    assert tracker._validate_payload("feedback_presented", {
        "strategies": [{"mode": "recast", "hint_level": "none", "kp_id": "kp-x"}]})
    tracker._conn.close()
