# SPDX-License-Identifier: Apache-2.0
"""Tests for the ADR-053 operator-feedback amplifier planner."""
from __future__ import annotations

import json

import pytest

from waggledance.core.autonomy_growth.operator_feedback_amplifier import (
    FEEDBACK_ACTION_TAKEN_EVENT_TYPE,
    OPS_FEEDBACK_EVENT_TYPE,
    OperatorFeedbackPolicy,
    OperatorFeedbackValidationError,
    amplify_operator_feedback,
    build_operator_feedback_scheduler_preflight,
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


def _bridge_event(feedback, **overrides):
    base = {
        "ts_utc": "2026-06-05T12:00:00Z",
        "agent": "operator",
        "type": "message",
        "task_id": "operator-feedback-test",
        "status": "ops_feedback_received",
        "message": "operator feedback",
        "agent_uuid": "",
        "session_id": "",
        "payload": {"ops_feedback": feedback},
    }
    base.update(overrides)
    return base


def _policy(
    *,
    fast_track_per_hour_max: int = 10,
    fast_track_global_per_hour_max: int = 30,
) -> OperatorFeedbackPolicy:
    loaded = load_operator_feedback_policy()
    return OperatorFeedbackPolicy(
        feedback_kinds=loaded.feedback_kinds,
        priority_enum=loaded.priority_enum,
        required_fields=loaded.required_fields,
        fast_track_canary_minutes=loaded.fast_track_canary_minutes,
        fast_track_per_hour_max=fast_track_per_hour_max,
        fast_track_global_per_hour_max=fast_track_global_per_hour_max,
    )


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
    assert policy.fast_track_global_per_hour_max == 30


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


def test_validator_rejects_malformed_feedback_refs() -> None:
    with pytest.raises(OperatorFeedbackValidationError, match="feedback_id"):
        validate_operator_feedback_event(_event(feedback_id="../bad"))

    with pytest.raises(OperatorFeedbackValidationError, match="query_class_hash"):
        validate_operator_feedback_event(_event(query_class_hash="not-a-hash"))


def test_high_priority_needs_solver_gets_bounded_fast_track_plan() -> None:
    plan = amplify_operator_feedback(_event())
    as_dict = plan.to_dict()

    assert plan.event_type == FEEDBACK_ACTION_TAKEN_EVENT_TYPE
    assert plan.action_id == "feedback_action:needs_solver:fb-001"
    assert plan.lane == "fast_track_canary"
    assert plan.route_context_hash is None
    assert plan.scheduled_for_utc == "2026-06-05T12:15:00Z"
    assert plan.rate_limited is False
    assert plan.runtime_authority_granted is False
    assert plan.canary_activation_applied is False
    assert plan.bridge_event_written is False
    assert as_dict["bridge_event_written"] is False
    assert as_dict["gap_signal"]["gap_kind"] == "needs_solver"
    assert as_dict["gap_signal"]["fast_track_canary"] is True
    assert as_dict["gap_signal"]["queue_priority"] == "fast_track"
    assert as_dict["gap_signal"]["queue_priority_only"] is True
    assert as_dict["gap_signal"]["gate_skip_allowed"] is False
    assert as_dict["gap_signal"]["promotion_gate_skip_allowed"] is False
    assert as_dict["gap_signal"]["adversarial_gate_skip_allowed"] is False
    assert as_dict["gap_signal"]["canary_gate_skip_allowed"] is False
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


def test_broken_route_requires_route_context_hash() -> None:
    with pytest.raises(OperatorFeedbackValidationError, match="route_context_required"):
        amplify_operator_feedback(_event(feedback_kind="broken_route"))

    with pytest.raises(OperatorFeedbackValidationError, match="route_context_hash"):
        amplify_operator_feedback(
            _event(
                feedback_kind="broken_route",
                route_context_hash="raw-from-cell-to-solver",
            )
        )


def test_broken_route_schedules_negative_tunnel_without_gap_signal() -> None:
    plan = amplify_operator_feedback(
        _event(
            feedback_kind="broken_route",
            route_context_hash="sha256:" + "B" * 64,
        )
    )

    assert plan.action_kind == "schedule_negative_tunnel_mining"
    assert plan.route_context_hash == "sha256:" + "b" * 64
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


def test_scheduler_preflight_rejects_free_string_operator_id() -> None:
    event = _event(operator_id="operator:jkh")
    source = _bridge_event(event)

    with pytest.raises(OperatorFeedbackValidationError, match="verified bridge"):
        build_operator_feedback_scheduler_preflight(
            event,
            source_bridge_event=source,
            durable_bridge_events=[source],
        )


def test_scheduler_preflight_requires_source_event_in_durable_log() -> None:
    event = _event(operator_id="bridge:operator")
    source = _bridge_event(event)

    with pytest.raises(OperatorFeedbackValidationError, match="durable bridge log"):
        build_operator_feedback_scheduler_preflight(
            event,
            source_bridge_event=source,
            durable_bridge_events=[],
        )


def test_scheduler_preflight_rejects_event_mismatch_with_durable_payload() -> None:
    source_event = _event(
        operator_id="bridge:operator",
        feedback_id="fb-durable",
        query_class_hash="sha256:" + "a" * 64,
    )
    source = _bridge_event(source_event)
    supplied_event = _event(
        operator_id="bridge:operator",
        feedback_id="fb-not-in-durable-log",
        query_class_hash="sha256:" + "b" * 64,
    )

    with pytest.raises(OperatorFeedbackValidationError, match="durable source"):
        build_operator_feedback_scheduler_preflight(
            supplied_event,
            source_bridge_event=source,
            durable_bridge_events=[source],
        )


def test_scheduler_preflight_uses_durable_bridge_log_for_operator_rate_limit() -> None:
    current = _event(operator_id="bridge:operator", feedback_id="fb-011")
    source = _bridge_event(current)
    prior = [
        _bridge_event(
            _event(
                feedback_id=f"prior-{index}",
                operator_id="bridge:operator",
                submitted_at_utc=f"2026-06-05T11:{index:02d}:00Z",
            ),
            ts_utc=f"2026-06-05T11:{index:02d}:01Z",
        )
        for index in range(10)
    ]

    preflight = build_operator_feedback_scheduler_preflight(
        current,
        source_bridge_event=source,
        durable_bridge_events=[*prior, source],
    )

    assert preflight.rate_limit_source == "durable_bridge_log"
    assert preflight.operator_fast_track_count == 10
    assert preflight.action_plan.rate_limited is True
    assert preflight.scheduler_candidate_artifact["fast_track_priority"] is False
    assert preflight.scheduler_candidate_artifact["queue_priority"] == "normal"


def test_scheduler_preflight_enforces_global_fast_track_ceiling() -> None:
    current_uuid = "11111111-1111-1111-1111-111111111111"
    current_operator = f"bridge:operator:{current_uuid}"
    current = _event(operator_id=current_operator, feedback_id="fb-global")
    source = _bridge_event(current, agent_uuid=current_uuid)
    prior = [
        _bridge_event(
            _event(
                feedback_id=f"global-{index}",
                operator_id=f"bridge:operator:other-{index}",
                submitted_at_utc=f"2026-06-05T11:1{index}:00Z",
            ),
            ts_utc=f"2026-06-05T11:1{index}:01Z",
        )
        for index in range(2)
    ]

    preflight = build_operator_feedback_scheduler_preflight(
        current,
        source_bridge_event=source,
        durable_bridge_events=[*prior, source],
        policy=_policy(fast_track_per_hour_max=10, fast_track_global_per_hour_max=2),
    )

    assert preflight.operator_fast_track_count == 0
    assert preflight.global_fast_track_count == 2
    assert preflight.action_plan.rate_limited is True
    assert preflight.scheduler_candidate_artifact["fast_track_priority"] is False


def test_scheduler_preflight_marks_fast_track_as_queue_priority_only() -> None:
    current = _event(operator_id="bridge:operator")
    source = _bridge_event(current)

    preflight = build_operator_feedback_scheduler_preflight(
        current,
        source_bridge_event=source,
        durable_bridge_events=[source],
    )
    artifact = preflight.scheduler_candidate_artifact

    assert preflight.action_plan.rate_limited is False
    assert artifact["queue_priority"] == "fast_track"
    assert artifact["fast_track_priority"] is True
    assert artifact["scheduler_enqueue_allowed"] is False
    assert artifact["scheduler_tick_allowed"] is False
    assert artifact["gate_skip_allowed"] is False
    assert artifact["promotion_gate_skip_allowed"] is False
    assert artifact["adversarial_gate_skip_allowed"] is False
    assert artifact["canary_gate_skip_allowed"] is False
    assert preflight.scheduler_enqueue_allowed is False
    assert preflight.scheduler_tick_allowed is False
    assert preflight.gate_skip_allowed is False
    assert preflight.bridge_event_written is False
