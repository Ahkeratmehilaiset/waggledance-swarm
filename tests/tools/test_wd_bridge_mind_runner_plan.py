"""Positive controls and named fail-closed twins for a NON-executing Source plan."""
from copy import deepcopy
import pytest
from tools.wd_bridge_mind_runner_plan import REQUIRED, plan

H = "a" * 40


def frame():
    return (
        {"pr": 1902, "head": H, "cycle_utc": "2026-10-13T10:37:02Z"},
        {"ok": True, "pr": 1902, "utc": "2026-10-13T10:40:00Z", "state": "OPEN", "draft": False,
         "head": H, "base": "b" * 40, "base_ref": "main", "checks": dict.fromkeys(REQUIRED, "SUCCESS"),
         "mergeable": "MERGEABLE", "merge_state": "CLEAN"},
        {"ok": True, "pr": 1902, "head": H, "base": "b" * 40, "veto_clear": True, "consensus": True,
         "class": "allowlist", "operator_signed": False, "error": None},
        {"kill_switch": False, "merged": set(), "attempts": {}},
    )


def assert_refused(value, reason=None):
    assert value["status"] == "REFUSE"
    assert value["can_execute"] is False
    assert value["authority"] == "none"
    if reason:
        assert value["reason"] == reason


def test_positive_candidate_is_unarmable_and_preserves_every_input():
    values = frame()
    before = deepcopy(values)
    output = plan(*values)
    assert output["status"] == "CANDIDATE"
    assert output["head"] == H
    assert output["can_execute"] is False
    assert output["gate_observation"] == "caller_declared_not_authenticated"
    assert values == before


@pytest.mark.parametrize("value", [True, False, 0, -1, "1902", None])
def test_mutant_bool_or_nonpositive_pr_is_not_a_pr(value):
    item, read, gate, state = frame()
    item["pr"] = value
    assert_refused(plan(item, read, gate, state), "mind_item_malformed")


@pytest.mark.parametrize("value", ["abc", "A" * 40, "a" * 39, "a" * 41, None, 123])
def test_mutant_nonexact_head_is_not_admitted(value):
    item, read, gate, state = frame()
    item["head"] = read["head"] = gate["head"] = value
    assert_refused(plan(item, read, gate, state), "mind_item_malformed")


@pytest.mark.parametrize("index,key,value,reason", [
    (3, "kill_switch", True, "kill_switch"), (3, "kill_switch", None, "runner_state_unknown"),
    (1, "ok", False, "github_read_failed"), (1, "state", "CLOSED", "pr_not_open"),
    (1, "draft", True, "draft"), (1, "draft", None, "draft_unknown"),
    (1, "draft", 0, "draft_unknown"), (1, "base", None, "github_base_unknown"),
    (1, "head", "c" * 40, "head_moved_since_mind_cycle"),
    (1, "checks", {}, "required_checks_incomplete"),
    (1, "checks", {name: "SUCCESS" for name in REQUIRED if name != "unified"}, "required_checks_incomplete"),
    (1, "checks", dict.fromkeys(REQUIRED, "FAILURE"), "required_checks_not_green"),
    (1, "mergeable", "UNKNOWN", "github_not_clean"), (1, "merge_state", "BLOCKED", "github_not_clean"),
    (2, "ok", False, "gate_failed_closed"), (2, "error", "unknown", "gate_failed_closed"),
    (2, "head", "c" * 40, "gate_head_not_runner_head"),
    (2, "veto_clear", None, "veto_not_clear"), (2, "veto_clear", False, "veto_not_clear"),
    (2, "consensus", False, "consensus_missing"), (2, "consensus", 1, "consensus_missing"),
    (2, "class", "UNKNOWN", "class_unknown"), (2, "class", "protected", "operator_signature_required"),
])
def test_mutant_missing_or_false_control_refuses(index, key, value, reason):
    values = frame()
    values[index][key] = value
    assert_refused(plan(*values), reason)


def test_time_comparison_uses_instants_not_lexical_offset_strings():
    item, read, gate, state = frame()
    item["cycle_utc"] = "2026-10-13T12:00:00+02:00"
    read["utc"] = "2026-10-13T10:01:00Z"
    assert plan(item, read, gate, state)["status"] == "CANDIDATE"
    item["cycle_utc"] = "2026-10-13T09:59:00-01:00"  # Actually after the reader.
    assert_refused(plan(item, read, gate, state), "mind_cycle_not_older_than_read")


@pytest.mark.parametrize("value", ["", None, "2026-10-13T10:00:00", "2026-13-99T10:00:00Z",
                                  "2026-10-13T10:00:00+99:00", True, "x" * 100])
def test_mutant_missing_or_invalid_time_refuses(value):
    item, read, gate, state = frame()
    item["cycle_utc"] = value
    assert_refused(plan(item, read, gate, state), "observation_time_invalid")


def test_extra_advisory_check_does_not_replace_any_required_check():
    item, read, gate, state = frame()
    read["checks"]["extra"] = "FAILURE"
    assert plan(item, read, gate, state)["status"] == "CANDIDATE"
    del read["checks"]["unified"]
    assert_refused(plan(item, read, gate, state), "required_checks_incomplete")


@pytest.mark.parametrize("value", [None, {"attempts": {1902: -1}}, {"attempts": {True: 1}},
                                  {"attempts": {1902: True}}, {"merged": [(True, H)]}])
def test_mutant_unknown_or_aliasing_state_refuses(value):
    item, read, gate, state = frame()
    if value is None:
        state = None
    else:
        state.update(value)
    assert_refused(plan(item, read, gate, state), "runner_state_unknown")


def test_idempotency_and_attempt_budget_are_head_scoped_and_no_retry_storm():
    item, read, gate, state = frame()
    state["merged"] = {(1902, H)}
    assert_refused(plan(item, read, gate, state), "already_merged_at_head")
    state["merged"] = {(1902, "d" * 40)}
    assert plan(item, read, gate, state)["status"] == "CANDIDATE"
    state["attempts"] = {1902: 2}
    assert_refused(plan(item, read, gate, state), "attempt_budget_spent")


def test_protected_signature_shape_is_not_execution_authority():
    item, read, gate, state = frame()
    gate["class"] = "protected"
    gate["operator_signed"] = True
    output = plan(item, read, gate, state)
    assert output["status"] == "CANDIDATE"
    assert output["can_execute"] is False
    assert output["authority"] == "none"


@pytest.mark.parametrize("index,reason", [(1, "github_pr_not_mind_pr"),
                                        (2, "gate_pr_not_runner_pr")])
@pytest.mark.parametrize("value", [1999, True, False, None, "1902", 0])
def test_mutant_cross_pr_or_missing_pr_refuses_even_with_same_commit(index, reason, value):
    values = frame()
    values[index]["pr"] = value
    assert_refused(plan(*values), reason)


def test_mutant_both_read_and_gate_from_another_pr_refuses_same_head():
    item, read, gate, state = frame()
    read["pr"] = gate["pr"] = 1999
    assert_refused(plan(item, read, gate, state), "github_pr_not_mind_pr")


@pytest.mark.parametrize("value", ["develop", "MAIN", "", None, True])
def test_mutant_non_main_base_ref_refuses(value):
    item, read, gate, state = frame()
    read["base_ref"] = value
    assert_refused(plan(item, read, gate, state), "github_base_ref_not_main")


@pytest.mark.parametrize("value", ["c" * 40, None, "b" * 39, "B" * 40, True])
def test_mutant_gate_base_mismatch_or_missing_refuses(value):
    item, read, gate, state = frame()
    gate["base"] = value
    assert_refused(plan(item, read, gate, state), "gate_base_not_runner_base")


def test_exact_pr_and_main_base_control_remains_unarmable():
    item, read, gate, state = frame()
    output = plan(item, read, gate, state)
    assert output["status"] == "CANDIDATE"
    assert output["pr"] == item["pr"] == read["pr"] == gate["pr"]
    assert output["base"] == read["base"] == gate["base"]
    assert read["base_ref"] == "main"
    assert output["can_execute"] is False
