# SPDX-License-Identifier: BUSL-1.1
"""Pure W0 advisory attempt/outcome contract, never a learning authority."""

from __future__ import annotations

from copy import deepcopy

import pytest

from tools.bridge_v2_advisory_outcome_contract import (
    ContractError, join_outcomes, parse_attempt, parse_outcome,
)


DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64


def attempt(**changes):
    row = {
        "schema": "wd.bridge-v2-advisory-attempt.v1",
        "attempt_id": "attempt-1",
        "request_id": "request-1",
        "prompt_digest": DIGEST_A,
        "task_id": "task-1",
        "task_class": "coding",
        "artifact_digest": DIGEST_B,
        "artifact_version": "v1",
        "advisor_profile": "grok-advisor",
        "attempt_status": "completed",
        "suggestion_ids": ["suggestion-1", "suggestion-2"],
        "observed_profile": None,
        "observed_model": None,
        "observed_cost_units": None,
        "latency_ms": None,
        "observed_at_utc": "2026-09-28T12:00:00Z",
        "provenance_refs": [DIGEST_C],
    }
    row.update(changes)
    return row


def outcome(a=None, **changes):
    a = attempt() if a is None else a
    row = {
        "schema": "wd.bridge-v2-advisory-outcome.v1",
        "outcome_id": "outcome-1",
        "attempt_id": a["attempt_id"],
        "request_id": a["request_id"],
        "prompt_digest": a["prompt_digest"],
        "task_id": a["task_id"],
        "task_class": a["task_class"],
        "artifact_digest": a["artifact_digest"],
        "artifact_version": a["artifact_version"],
        "observed_profile": a["observed_profile"],
        "suggestion_id": "suggestion-1",
        "disposition": "used",
        "correctness": "correct",
        "evaluator_id": "independent-rco",
        "scoring_evidence_digest": DIGEST_C,
        "changed_artifact_digest": DIGEST_A,
        "judged_at_utc": "2026-09-28T12:01:00Z",
        "provenance_refs": [DIGEST_B],
    }
    row.update(changes)
    return row


def fails(code, attempts, outcomes):
    with pytest.raises(ContractError) as exc:
        join_outcomes(attempts, outcomes)
    assert exc.value.code == code


def test_join_keeps_failed_skipped_unhelpful_and_missing_in_denominator():
    completed = attempt()
    failed = attempt(attempt_id="attempt-2", attempt_status="failed", suggestion_ids=[])
    skipped = attempt(attempt_id="attempt-3", attempt_status="skipped", suggestion_ids=[])
    unused = outcome(completed, outcome_id="outcome-2", suggestion_id="suggestion-2",
                     disposition="rejected", correctness="incorrect")
    joined = join_outcomes([completed, failed, skipped], [outcome(completed), unused])
    assert joined["denominator_attempts"] == 3
    assert joined["attempt_status_counts"] == {
        "completed": 1, "failed": 1, "skipped": 1, "timeout": 0, "unknown": 0
    }
    assert joined["disposition_counts"] == {
        "used": 1, "rejected": 1, "unused": 0, "unknown": 0
    }
    assert joined["correctness_counts"] == {"correct": 1, "incorrect": 1, "unknown": 0}
    assert joined["attempt_count"] == 3 and joined["outcome_count"] == 2
    assert joined["benefit"] == {
        "state": "unknown", "reason": "comparison_protocol_unimplemented",
        "attribution": "not_established"
    }
    assert joined["qualification_allowed"] is False
    assert joined["learning_update_allowed"] is False
    assert joined["spend_allowed"] is False and joined["dispatch_allowed"] is False


def test_missing_outcome_remains_unknown_not_a_success():
    joined = join_outcomes([attempt()], [])
    assert joined["denominator_attempts"] == 1
    assert joined["outcome_count"] == 0
    assert joined["disposition_counts"]["unknown"] == 2
    assert joined["correctness_counts"]["unknown"] == 2
    rows = joined["attempts"][0]["suggestions"]
    assert {row["reason"] for row in rows} == {"missing_outcome"}


def test_exact_replay_deduplicates_but_conflicts_reject():
    a = attempt()
    o = outcome(a)
    joined = join_outcomes([a, deepcopy(a)], [o, deepcopy(o)])
    assert (joined["attempt_count"], joined["outcome_count"]) == (1, 1)
    fails("conflicting_attempt", [a, attempt(observed_model="different")], [o])
    fails("conflicting_outcome", [a], [o, outcome(a, correctness="incorrect")])
    fails("duplicate_suggestion_outcome", [a], [o, outcome(a, outcome_id="outcome-2")])


@pytest.mark.parametrize("field,value", [
    ("request_id", "other-request"), ("prompt_digest", DIGEST_B),
    ("task_id", "other-task"), ("task_class", "review"),
    ("artifact_digest", DIGEST_A), ("artifact_version", "v2"),
    ("observed_profile", "foreign-profile"),
])
def test_foreign_binding_never_joins(field, value):
    a = attempt()
    fails("foreign_binding", [a], [outcome(a, **{field: value})])


def test_unknown_attempt_or_suggestion_is_rejected():
    a = attempt()
    fails("unknown_attempt", [a], [outcome(a, attempt_id="foreign")])
    fails("unknown_suggestion", [a], [outcome(a, suggestion_id="foreign")])


def test_self_grading_and_missing_independent_score_cannot_be_favorable():
    a = attempt()
    fails("self_evaluation", [a], [outcome(a, evaluator_id=a["advisor_profile"])])
    fails("self_evaluation", [a], [outcome(a, evaluator_id=a["advisor_profile"].upper())])
    observed = attempt(observed_profile="actual-advisor")
    fails("self_evaluation", [observed], [outcome(observed, evaluator_id="ACTUAL-ADVISOR")])
    fails("missing_independent_evidence", [a], [outcome(a, scoring_evidence_digest=None)])
    fails("missing_independent_evidence", [a], [outcome(a, evaluator_id=None)])
    joined = join_outcomes([a], [outcome(a)])
    assert joined["attempts"][0]["suggestions"][0]["correctness"] == "correct"
    assert joined["attempts"][0]["suggestions"][0]["evidence_state"] == (
        "reported_independent_not_authenticated"
    )
    assert joined["benefit"]["state"] == "unknown"


def test_used_on_failed_attempt_does_not_become_success():
    a = attempt(attempt_status="failed")
    fails("invalid_disposition_for_attempt", [a], [outcome(a)])


def test_outcome_must_not_precede_attempt():
    a = attempt()
    fails("invalid_time_order", [a], [outcome(a, judged_at_utc="2026-09-28T11:59:59Z")])


@pytest.mark.parametrize("stamp", [
    "2026-09-28 12:00:00Z", "2026-09-28T12:00:00.1234567Z",
    "2026-W40-1T12:00:00Z", "2026-09-28T12:00:00+00:00",
])
def test_noncanonical_utc_is_rejected(stamp):
    with pytest.raises(ContractError, match="invalid_utc"):
        parse_attempt(attempt(observed_at_utc=stamp))
    with pytest.raises(ContractError, match="invalid_utc"):
        parse_outcome(outcome(judged_at_utc=stamp))


@pytest.mark.parametrize("field", ["observed_cost_units", "latency_ms"])
@pytest.mark.parametrize("value", [True, -1, float("nan"), float("inf"), 10**5000],
                         ids=["bool", "negative", "nan", "infinity", "huge_int"])
def test_malformed_numbers_are_stably_rejected(field, value):
    with pytest.raises(ContractError, match="invalid_number"):
        parse_attempt(attempt(**{field: value}))


def test_unknown_observations_remain_null_and_zero_is_explicit():
    unknown = parse_attempt(attempt())
    assert unknown["observed_profile"] is None
    assert unknown["observed_model"] is None
    assert unknown["observed_cost_units"] is None
    assert unknown["latency_ms"] is None
    known_zero = parse_attempt(attempt(observed_profile="actual-advisor",
                                       observed_cost_units=0, latency_ms=0))
    assert known_zero["observed_profile"] == "actual-advisor"
    assert known_zero["observed_cost_units"] == 0
    assert known_zero["latency_ms"] == 0


def test_adoption_or_cli_success_does_not_make_benefit():
    joined = join_outcomes([attempt()], [outcome()])
    assert joined["disposition_counts"]["used"] == 1
    assert joined["correctness_counts"]["correct"] == 1
    assert joined["benefit"]["state"] == "unknown"
    assert joined["learning_update_allowed"] is False


def test_disposition_and_correctness_are_independent_axes():
    a = attempt()
    used_but_wrong = outcome(a, correctness="incorrect")
    rejected_but_correct = outcome(a, outcome_id="outcome-2",
                                   suggestion_id="suggestion-2",
                                   disposition="rejected", correctness="correct")
    joined = join_outcomes([a], [used_but_wrong, rejected_but_correct])
    rows = joined["attempts"][0]["suggestions"]
    assert [(row["disposition"], row["correctness"]) for row in rows] == [
        ("used", "incorrect"), ("rejected", "correct")
    ]
    assert joined["benefit"]["state"] == "unknown"


def test_replay_order_does_not_change_join_or_denominator():
    a = attempt()
    b = attempt(attempt_id="attempt-2", request_id="request-2",
                attempt_status="timeout", suggestion_ids=[])
    o = outcome(a)
    first = join_outcomes([a, b, deepcopy(a)], [o, deepcopy(o)])
    second = join_outcomes([b, a], [deepcopy(o)])
    assert first == second
    assert first["denominator_attempts"] == 2


def test_unknown_fields_and_benefit_claim_are_rejected():
    with pytest.raises(ContractError, match="unknown_field"):
        parse_attempt(attempt(successful_cli_exit=True))
    with pytest.raises(ContractError, match="unknown_field"):
        parse_outcome(outcome(incremental_benefit=1))


def test_suggestion_and_provenance_lists_are_validated():
    with pytest.raises(ContractError, match="duplicate_suggestion"):
        parse_attempt(attempt(suggestion_ids=["same", "same"]))
    with pytest.raises(ContractError, match="invalid_provenance"):
        parse_attempt(attempt(provenance_refs=["not-a-digest"]))
    with pytest.raises(ContractError, match="invalid_suggestions"):
        parse_attempt(attempt(suggestion_ids=[f"s{i}" for i in range(129)]))


def test_input_records_are_not_mutated():
    a = attempt()
    o = outcome(a)
    before = deepcopy((a, o))
    join_outcomes([a], [o])
    assert (a, o) == before


def test_real_bridge_task_id_parses_and_joins_without_normalization():
    task_id = "codex-lead-1/bridge-v2-w0-advisory-outcome-contract-20260928"
    a = attempt(task_id=task_id)
    o = outcome(a)
    assert parse_attempt(a)["task_id"] == task_id
    assert parse_outcome(o)["task_id"] == task_id
    joined = join_outcomes([a], [o])
    assert joined["attempts"][0]["task_id"] == task_id
    assert joined["attempts"][0]["suggestions"][0]["disposition"] == "used"


def test_task_binding_is_case_and_byte_exact():
    a = attempt(task_id="Lead/Task.One")
    fails("foreign_binding", [a], [outcome(a, task_id="lead/Task.One")])
    fails("foreign_binding", [a], [outcome(a, task_id="Lead/Task-One")])


def test_task_id_accepts_safe_160_character_boundary():
    task_id = "a/" + "x" * 158
    assert len(task_id) == 160
    a = attempt(task_id=task_id)
    assert join_outcomes([a], [outcome(a)])["attempt_count"] == 1


@pytest.mark.parametrize("task_id", [
    "a/" + "x" * 159, "a//b", "a/../b", "/a", "a/", "a\\b", "a:b",
    "a/\nb", "a/\x00b", "a/ b", "..", ".a",
])
def test_malformed_or_overlong_task_ids_are_rejected(task_id):
    with pytest.raises(ContractError, match="invalid_task_id"):
        parse_attempt(attempt(task_id=task_id))
    with pytest.raises(ContractError, match="invalid_task_id"):
        parse_outcome(outcome(task_id=task_id))
