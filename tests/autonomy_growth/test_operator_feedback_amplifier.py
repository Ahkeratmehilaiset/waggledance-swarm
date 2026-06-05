# SPDX-License-Identifier: Apache-2.0
"""Tests for the ADR-053 operator-feedback amplifier planner."""
from __future__ import annotations

import json

import pytest

from waggledance.core.autonomy_growth.operator_feedback_amplifier import (
    FEEDBACK_ACTION_TAKEN_EVENT_TYPE,
    OPS_FEEDBACK_EVENT_TYPE,
    OperatorFeedbackValidationError,
    amplify_operator_feedback,
    load_operator_feedback_policy,
    validate_operator_feedback_event,
)


def _event(**overrides):
    base = {
        "event_type": OPS_FEEDBACK_EVENT_TYPE,
        "feedback_id": "fb-001",
        "feedback_kind": "needs_solver",
        "query_class_hash": "sha256:" + "a" * 64,
        "operator_id": "operator:jkh",
        "priority": "high",
        "submitted_at_utc": "2026-06-05T12:00:00Z",
    }
    base.update(overrides)
    return base


def test_policy_loads_from_machine_readable_contract() -> None:
    policy = load_operator_feedback_policy()

    assert set(policy.feedback_kinds) == {
        "needs_solver",
        "broken_route",
        "wrong_output",
    }
    assert set(policy.priority_enum) == {"high", "normal"}
    assert policy.fast_track_canary_minutes == 15
    assert policy.fast_track_per_hour_max == 10


def test_validator_accepts_contract_fields_and_ignores_extras() -> None:
    normalized = validate_operator_feedback_event(
        _event(extra_text_that_must_not_echo="raw operator note")
    )

    assert normalized == {
        "event_type": OPS_FEEDBACK_EVENT_TYPE,
        "feedback_id": "fb-001",
        "feedback_kind": "needs_solver",
        "query_class_hash": "sha256:" + "a" * 64,
        "operator_id": "operator:jkh",
        "priority": "high",
        "submitted_at_utc": "2026-06-05T12:00:00Z",
    }


@pytest.mark.parametrize(
    "field",
    [
        "event_type",
        "feedback_id",
        "feedback_kind",
        "query_class_hash",
        "operator_id",
        "priority",
        "submitted_at_utc",
    ],
)
def test_validator_rejects_missing_or_empty_required_field(field: str) -> None:
    payload = _event()
    payload[field] = ""

    with pytest.raises(OperatorFeedbackValidationError, match=field):
        validate_operator_feedback_event(payload)


def test_validator_rejects_unknown_kind_priority_and_naive_time() -> None:
    with pytest.raises(OperatorFeedbackValidationError, match="feedback_kind"):
        validate_operator_feedback_event(_event(feedback_kind="complaint"))

    with pytest.raises(OperatorFeedbackValidationError, match="priority"):
        validate_operator_feedback_event(_event(priority="urgent"))

    with pytest.raises(OperatorFeedbackValidationError, match="timezone"):
        validate_operator_feedback_event(
            _event(submitted_at_utc="2026-06-05T12:00:00")
        )


def test_high_priority_needs_solver_gets_bounded_fast_track_plan() -> None:
    plan = amplify_operator_feedback(_event())
    as_dict = plan.to_dict()

    assert plan.event_type == FEEDBACK_ACTION_TAKEN_EVENT_TYPE
    assert plan.action_id == "feedback_action:needs_solver:fb-001"
    assert plan.lane == "fast_track_canary"
    assert plan.scheduled_for_utc == "2026-06-05T12:15:00Z"
    assert plan.rate_limited is False
    assert plan.runtime_authority_granted is False
    assert plan.canary_activation_applied is False
    assert plan.bridge_event_written is False
    assert as_dict["gap_signal"]["gap_kind"] == "needs_solver"
    assert as_dict["gap_signal"]["fast_track_canary"] is True
    assert as_dict["gap_signal"]["raw_query_exported"] is False
    assert as_dict["adversarial_probe_intent"] is None


def test_normal_priority_queues_without_fast_track() -> None:
    plan = amplify_operator_feedback(_event(priority="normal"))

    assert plan.lane == "normal_gap_queue"
    assert plan.scheduled_for_utc is None
    assert plan.rate_limited is False
    assert plan.gap_signal is not None
    assert plan.gap_signal["fast_track_canary"] is False


def test_fast_track_is_rate_limited_per_operator_per_hour() -> None:
    prior = [
        _event(
            feedback_id=f"prior-{i}",
            submitted_at_utc=f"2026-06-05T11:{i:02d}:00Z",
        )
        for i in range(10)
    ]
    prior.extend([
        _event(
            feedback_id="old",
            submitted_at_utc="2026-06-05T10:59:00Z",
        ),
        _event(
            feedback_id="other-operator",
            operator_id="operator:other",
            submitted_at_utc="2026-06-05T11:30:00Z",
        ),
        _event(
            feedback_id="normal-priority",
            priority="normal",
            submitted_at_utc="2026-06-05T11:31:00Z",
        ),
    ])

    plan = amplify_operator_feedback(_event(feedback_id="fb-011"), prior_events=prior)

    assert plan.rate_limited is True
    assert plan.lane == "normal_gap_queue"
    assert plan.scheduled_for_utc is None
    assert plan.gap_signal is not None
    assert plan.gap_signal["fast_track_canary"] is False
    assert plan.rate_limit_window_start_utc == "2026-06-05T11:00:00Z"


def test_broken_route_schedules_negative_tunnel_without_gap_signal() -> None:
    plan = amplify_operator_feedback(_event(feedback_kind="broken_route"))

    assert plan.action_kind == "schedule_negative_tunnel_mining"
    assert plan.gap_signal is None
    assert plan.adversarial_probe_intent is None
    assert plan.runtime_authority_granted is False


def test_wrong_output_adds_sanitized_gap_and_probe_intent() -> None:
    plan = amplify_operator_feedback(_event(feedback_kind="wrong_output"))
    as_json = json.dumps(plan.to_dict(), sort_keys=True)

    assert plan.action_kind == "spawn_gap_signal_and_probe_intent"
    assert plan.gap_signal is not None
    assert plan.gap_signal["gap_kind"] == "wrong_output_confirmed_gap"
    assert plan.adversarial_probe_intent is not None
    assert plan.adversarial_probe_intent["probe_source"] == (
        "operator_feedback_wrong_output"
    )
    assert plan.adversarial_probe_intent["yaml_write_applied"] is False
    assert "raw operator note" not in as_json


def test_invalid_prior_events_do_not_break_rate_limit_counting() -> None:
    prior = [
        {"event_type": "not_feedback"},
        _event(feedback_id="valid-1", submitted_at_utc="2026-06-05T11:30:00Z"),
    ]

    plan = amplify_operator_feedback(_event(feedback_id="fb-002"), prior_events=prior)

    assert plan.rate_limited is False
    assert plan.lane == "fast_track_canary"
