# SPDX-License-Identifier: BUSL-1.1
"""Acceptance and fail-closed tests for the dormant admission controller.

The five acceptance criteria in final-plan.md section "Hyväksymiskokeet ennen
aktivointia" that fall inside this module's scope are implemented literally,
including the 10 000 idle checks. The rest of the file exists to prove the
uninteresting-sounding half of the contract: that *absence* parks, that a
clock cannot buy a judgment, and that no path leaks execution authority.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.bridge_capacity_advisor import InputError  # noqa: E402
from tools.bridge_capacity_recovery import BINDING_FIELDS  # noqa: E402
from tools.bridge_task_admission import (  # noqa: E402
    ADMISSION_SCHEMA,
    EPOCH_FIELDS,
    ESCALATE,
    KEEP,
    PARK,
    REVALIDATION_SCHEMA,
    admit,
    classify_failure,
    decision_key,
    revalidate,
)

BINDING = {
    "agent_id": "fable-5",
    "session_id": "wd-fable-direct-20260924T090104Z",
    "native_thread_id": "9f375967-f824-4e2e-8104-7f0011117cf5",
    "task_id": "codex-lead-1/autonomy-implementation-20260925/admission",
    "request_id": "e67ad5d8-7033-45be-93dd-d4a5caffb011",
    "head": "b2250ba7e1783dd6ba5bf353e19692bb1406694d",
    "claim_id": "fable-5/autonomy-admission-20260925",
    "scope_digest": "d3ad0b0f" * 8,
    "authority_ref": "operator/implement-final-brainstorm-plan",
    "policy_digest": "a1" * 32,
    "permission_digest": "b2" * 32,
    "native_pid": 24400,
    "native_process_started_at": "2026-09-24T09:01:04.252477+00:00",
}

EPOCHS = {name: f"{name}-1" for name in EPOCH_FIELDS}


def base_request(**overrides):
    request = {
        "binding": deepcopy(BINDING),
        "epochs": deepcopy(EPOCHS),
        "hold": False,
        "cancelled": False,
        "owner": {"verified": True, "principal": "fable-5",
                  "verification_ref": "claim/fable-5/autonomy-admission-20260925"},
        "current_profile": {"profile_id": "fable-producer/default", "authorized": True,
                            "healthy": True, "authorization_ref": "operator/2026-09-25"},
        "failure": None,
    }
    request.update(overrides)
    return request


# --- the structural invariants ------------------------------------------------


@pytest.mark.parametrize("request_payload", [
    base_request(),
    base_request(hold=True),
    base_request(failure={"kind": "quota"}),
    base_request(failure={"kind": "safety"}),
    base_request(failure={"kind": "banana"}),
    base_request(ttl_expired=True),
])
def test_no_path_returns_execution_authority_or_a_profile(request_payload):
    """The two invariants that make this module safe to leave lying around."""
    result = admit(request_payload)
    assert result["execution_allowed"] is False
    assert result["proposed_profile"] is None
    assert result["model_calls"] == 0
    assert result["schema"] == ADMISSION_SCHEMA
    assert result["verdict"] in (KEEP, PARK, ESCALATE)


def test_keep_requires_authorized_and_healthy():
    assert admit(base_request())["verdict"] == KEEP


# --- acceptance: 10 000 unchanged idle checks ---------------------------------


def test_ten_thousand_unchanged_idle_checks_spend_nothing():
    """Acceptance criterion 1, run literally rather than argued."""
    request = base_request()
    judgments: dict = {}
    verdicts = set()
    judgment_calls = 0
    profile_changes = 0
    for _ in range(10_000):
        result = admit(request, judgments=judgments)
        verdicts.add(result["verdict"])
        judgment_calls += int(result["judgment_requested"])
        profile_changes += int(result["proposed_profile"] is not None)
    assert verdicts == {KEEP}
    assert judgment_calls == 0
    assert profile_changes == 0
    assert judgments == {}


# --- acceptance: one judgment per semantic key --------------------------------


def test_same_semantic_key_buys_at_most_one_judgment():
    """Acceptance criterion 2. The second ask is suppressed, not re-answered."""
    request = base_request(failure={"kind": "quota"})
    first = admit(request)
    assert first["verdict"] == ESCALATE
    assert first["judgment_requested"] is True
    assert first["suppressed"] is False

    judgments = {first["decision_key"]: {"verdict": PARK}}
    second = admit(request, judgments=judgments)
    assert second["verdict"] == PARK
    assert second["judgment_requested"] is False
    assert second["suppressed"] is True


def test_a_new_timestamp_does_not_reset_the_judgment_budget():
    """Acceptance criterion 2, second half: the clock must not buy a judgment."""
    early = base_request(failure={"kind": "quota"}, observed_at="2026-09-25T07:00:00+00:00")
    late = base_request(failure={"kind": "quota"}, observed_at="2026-09-25T23:59:59+00:00")
    assert decision_key(early) == decision_key(late)

    judgments = {decision_key(early): {"verdict": PARK}}
    assert admit(late, judgments=judgments)["suppressed"] is True


def test_changing_a_binding_or_epoch_field_is_a_different_key():
    request = base_request()
    moved_head = deepcopy(request)
    moved_head["binding"]["head"] = "0" * 40
    assert decision_key(moved_head) != decision_key(request)

    moved_epoch = deepcopy(request)
    moved_epoch["epochs"]["catalog_epoch"] = "catalog_epoch-2"
    assert decision_key(moved_epoch) != decision_key(request)


def test_unreadable_cache_entry_parks_rather_than_re_asking():
    request = base_request(failure={"kind": "quota"})
    judgments = {decision_key(request): {"verdict": "MAYBE"}}
    result = admit(request, judgments=judgments)
    assert result["verdict"] == PARK
    assert result["judgment_requested"] is False
    assert result["suppressed"] is True


# --- unknown parks, it never keeps and never escalates ------------------------


@pytest.mark.parametrize("field", [f for f in BINDING_FIELDS
                                   if f not in ("native_pid", "native_process_started_at")])
def test_each_missing_binding_field_parks(field):
    request = base_request()
    del request["binding"][field]
    result = admit(request)
    assert result["verdict"] == PARK
    assert f"binding_incomplete:{field}" in result["reasons"]


@pytest.mark.parametrize("name", EPOCH_FIELDS)
def test_each_missing_epoch_parks(name):
    request = base_request()
    del request["epochs"][name]
    result = admit(request)
    assert result["verdict"] == PARK
    assert f"epoch_unknown:{name}" in result["reasons"]


def test_pid_without_a_start_epoch_parks():
    """A reusable PID is not a process identity; recovery says so and so do we."""
    request = base_request()
    del request["binding"]["native_process_started_at"]
    result = admit(request)
    assert result["verdict"] == PARK
    assert "binding_incomplete:native_process_epoch" in result["reasons"]


@pytest.mark.parametrize("overrides,reason", [
    ({"hold": None}, "hold_unknown_or_set"),
    ({"hold": True}, "hold_unknown_or_set"),
    ({"hold": "false"}, "hold_unknown_or_set"),
    ({"hold": 0}, "hold_unknown_or_set"),
    ({"cancelled": None}, "cancelled_unknown_or_set"),
    ({"cancelled": True}, "cancelled_unknown_or_set"),
    ({"owner": {}}, "owner_unverified_or_unknown"),
    ({"owner": {"verified": "yes", "principal": "x", "verification_ref": "y"}},
     "owner_unverified_or_unknown"),
    ({"current_profile": {"profile_id": "p", "healthy": True}},
     "profile_not_already_authorized"),
    ({"ttl_expired": True}, "ttl_expired_is_unknown_not_a_trigger"),
])
def test_missing_or_unknown_facts_park(overrides, reason):
    result = admit(base_request(**overrides))
    assert result["verdict"] == PARK
    assert reason in result["reasons"]


def test_ttl_expiry_never_escalates_even_with_a_relevant_failure():
    """A lapsed cache entry is missing information, not a trigger."""
    result = admit(base_request(ttl_expired=True, failure={"kind": "quota"}))
    assert result["verdict"] == PARK
    assert result["judgment_requested"] is False


def test_authorized_but_unhealthy_parks_rather_than_keeping():
    request = base_request()
    request["current_profile"]["healthy"] = False
    result = admit(request)
    assert result["verdict"] == PARK
    assert "current_work_not_observed_healthy" in result["reasons"]


def test_all_gaps_are_reported_not_just_the_first():
    request = base_request(hold=None, cancelled=None, owner={})
    del request["binding"]["head"]
    reasons = admit(request)["reasons"]
    assert {"hold_unknown_or_set", "cancelled_unknown_or_set",
            "owner_unverified_or_unknown", "binding_incomplete:head"} <= set(reasons)


# --- failure classification ---------------------------------------------------


@pytest.mark.parametrize("kind", ["tool", "auth", "permission", "safety"])
def test_diagnosis_failures_park_and_never_escalate(kind):
    result = admit(base_request(failure={"kind": kind}))
    assert result["verdict"] == PARK
    assert result["reasons"] == ["failure_requires_diagnosis_not_model_switch"]
    assert result["judgment_requested"] is False


@pytest.mark.parametrize("failure", [{"kind": "banana"}, {}, "quota", 7, [1]])
def test_unclassified_failure_parks(failure):
    result = admit(base_request(failure=failure))
    assert result["verdict"] == PARK
    assert "failure_unclassified" in result["reasons"]


def test_single_quality_failure_is_not_admission_relevant():
    result = admit(base_request(failure={"kind": "model_quality"}, quality_failures=1))
    assert result["verdict"] == PARK
    assert "quality_failure_not_repeated_or_uncounted" in result["reasons"]


def test_uncounted_quality_failure_parks():
    result = admit(base_request(failure={"kind": "model_quality"}))
    assert result["verdict"] == PARK


def test_repeated_quality_failure_escalates_once():
    result = admit(base_request(failure={"kind": "model_quality"}, quality_failures=2))
    assert result["verdict"] == ESCALATE
    assert result["judgment_requested"] is True


def test_classify_failure_contract():
    assert classify_failure(None) is None
    assert classify_failure({"kind": "quota"}) == "quota"
    assert classify_failure({"kind": "nope"}) == "unknown"
    assert classify_failure("quota") == "unknown"


# --- structurally malformed input fails closed with an error ------------------


@pytest.mark.parametrize("payload", [None, "request", 5, [], {"binding": "x", "epochs": {}},
                                     {"binding": {}, "epochs": None}])
def test_structurally_malformed_input_raises(payload):
    with pytest.raises(InputError):
        admit(payload)


def test_oversized_request_is_refused_not_truncated():
    request = base_request()
    request["padding"] = "x" * (256 * 1024)
    with pytest.raises(InputError):
        admit(request)


@pytest.mark.parametrize("poison", [
    float("nan"),
    float("inf"),
    {1, 2},
    object(),
])
def test_unserialisable_values_fail_closed_as_input_error(poison):
    """Found by self-review: these escaped as bare TypeError/ValueError.

    A value we cannot represent exactly is malformed input. It must not leave
    the module as a different exception type, because a caller that catches
    InputError would then see an uncaught crash instead of a refusal.
    """
    request = base_request()
    request["extra"] = poison
    with pytest.raises(InputError):
        admit(request)


def test_unserialisable_binding_fails_closed_in_decision_key():
    request = base_request()
    request["binding"]["scope_digest"] = {1, 2}
    with pytest.raises(InputError):
        decision_key(request)


# --- execution-time revalidation ---------------------------------------------


def observed_from(request):
    return {"hold": request["hold"], "cancelled": request["cancelled"],
            "owner": request["owner"], "binding": request["binding"],
            "epochs": request["epochs"]}


def test_clean_revalidation_still_grants_nothing():
    request = base_request()
    decision = admit(request)
    outcome = revalidate(decision, observed_from(request))
    assert outcome["schema"] == REVALIDATION_SCHEMA
    assert outcome["revalidated"] is True
    assert outcome["reasons"] == []
    assert outcome["execution_allowed"] is False


@pytest.mark.parametrize("mutate,reason", [
    (lambda o: o.update(hold=True), "hold_unknown_or_set_at_execution"),
    (lambda o: o.update(hold=None), "hold_unknown_or_set_at_execution"),
    (lambda o: o.update(cancelled=True), "cancelled_unknown_or_set_at_execution"),
    (lambda o: o.update(owner={"verified": False, "principal": "fable-5"}),
     "owner_unverified_at_execution"),
    (lambda o: o["binding"].update(claim_id="someone-else/claim"),
     "binding_or_epoch_changed_since_admission"),
    (lambda o: o["binding"].update(head="0" * 40),
     "binding_or_epoch_changed_since_admission"),
    (lambda o: o["epochs"].update(policy_epoch="policy_epoch-2"),
     "binding_or_epoch_changed_since_admission"),
])
def test_a_cached_keep_is_blocked_by_any_change_at_execution(mutate, reason):
    """Acceptance criterion 3: cache hit plus a new fact must block."""
    request = base_request()
    decision = admit(request)
    assert decision["verdict"] == KEEP
    observed = deepcopy(observed_from(request))
    mutate(observed)
    outcome = revalidate(decision, observed)
    assert outcome["revalidated"] is False
    assert reason in outcome["reasons"]


def test_only_a_keep_decision_is_actionable():
    request = base_request(hold=True)
    parked = admit(request)
    assert parked["verdict"] == PARK
    outcome = revalidate(parked, observed_from(base_request()))
    assert outcome["revalidated"] is False
    assert "only_a_keep_decision_is_actionable" in outcome["reasons"]


def test_revalidation_rejects_a_foreign_decision_record():
    outcome = revalidate({"schema": "something.else", "verdict": KEEP,
                          "decision_key": "0" * 64}, observed_from(base_request()))
    assert outcome["revalidated"] is False
    assert "decision_schema_unrecognised" in outcome["reasons"]


@pytest.mark.parametrize("payload", [None, "decision", 3, []])
def test_revalidation_rejects_malformed_input(payload):
    with pytest.raises(InputError):
        revalidate(payload, observed_from(base_request()))
    with pytest.raises(InputError):
        revalidate(admit(base_request()), payload)
