# SPDX-License-Identifier: BUSL-1.1
"""Unit tests for idle protocol quality validation."""
from __future__ import annotations

import copy

from waggledance.core.idle_protocol import validate_idle_proposal


def good_idle_proposal() -> dict:
    return {
        "protocol_version": "idle-protocol.v1",
        "event_type": "idle_proposal",
        "proposal_id": "idle-prop-20260517-001",
        "round_number": 1,
        "proposes_substrate_change": True,
        "problem_statement": "Strategic bridge deliberation stalls when no PR vehicle exists.",
        "proposal": (
            "Add a manual idle-check primitive that only reports active or idle "
            "and leaves proposal emission to a later operator-invoked step."
        ),
        "tradeoff_axis": "Lower automation risk versus slower activation cadence during v1.",
        "simulation_evidence": {
            "kind": "scenario_simulation",
            "summary": (
                "A 90 minute bridge-silent window with no open claims returns idle, "
                "while an active claim keeps the bridge active."
            ),
            "artifacts": [
                {
                    "path": "tests/tools/test_idle_check.py",
                    "description": "Fixture proving active claim blocks false idle.",
                }
            ],
        },
        "charter_alignment": {
            "compatible": True,
            "reasoning": (
                "The detector has no side effects and keeps consensus execution "
                "behind the operator gate."
            ),
        },
    }


def good_counter_proposal() -> dict:
    event = copy.deepcopy(good_idle_proposal())
    event.update(
        {
            "event_type": "idle_counter_proposal",
            "proposal_id": "idle-prop-20260517-002",
            "round_number": 2,
            "responds_to": "idle-prop-20260517-001",
            "alternative_proposal": (
                "Keep detection outside the agent loop and require manual proposal "
                "construction until validator and convergence checks have shipped."
            ),
            "reasoning_points": [
                "If stale bridge requests accumulate, manual activation exposes the false positive before automation.",
                "If active claim files are present, the detector must remain active regardless of message silence.",
                "If cron polls are counted as substantive messages, idle can be falsified by a heartbeat-only stream.",
            ],
        }
    )
    del event["proposal"]
    return event


def good_adversarial_review() -> dict:
    event = copy.deepcopy(good_idle_proposal())
    event.update(
        {
            "event_type": "idle_adversarial_review",
            "proposal_id": "idle-prop-20260517-003",
            "round_number": 3,
            "responds_to": "idle-prop-20260517-002",
            "counterexamples": [
                "An active work claim without recent messages would be a false idle unless claim files are checked.",
                "A malformed events file must become unknown rather than idle because silence is not proof.",
            ],
        }
    )
    del event["proposal"]
    return event


def good_consensus() -> dict:
    event = copy.deepcopy(good_idle_proposal())
    event.update(
        {
            "event_type": "idle_consensus_reached",
            "proposal_id": "idle-prop-20260517-005",
            "round_number": 5,
            "proposes_substrate_change": False,
            "consensus_target_proposal_id": "idle-prop-20260517-002",
            "operator_gate_required": True,
            "auto_execute": False,
        }
    )
    del event["proposal"]
    return event


def good_charter_violation() -> dict:
    event = copy.deepcopy(good_idle_proposal())
    event.update(
        {
            "event_type": "idle_charter_violation",
            "proposal_id": "idle-prop-20260517-004",
            "round_number": 4,
            "proposes_substrate_change": False,
            "violating_proposal_id": "idle-prop-20260517-003",
            "violation_reason": "The proposal would convert consensus into automatic execution.",
            "terminate_protocol": True,
            "operator_escalation_required": True,
            "charter_alignment": {
                "compatible": False,
                "reasoning": "Auto-execution would bypass the required operator-owned gate.",
            },
        }
    )
    del event["proposal"]
    return event


def test_valid_idle_proposal_passes() -> None:
    ok, errors = validate_idle_proposal(good_idle_proposal())

    assert ok is True
    assert errors == []


def test_schema_errors_are_returned_without_throwing() -> None:
    event = good_idle_proposal()
    del event["simulation_evidence"]

    ok, errors = validate_idle_proposal(event)

    assert ok is False
    assert any("simulation_evidence" in error for error in errors)


def test_counter_proposal_requires_three_falsifiable_reasoning_points() -> None:
    event = good_counter_proposal()
    event["reasoning_points"] = [
        "This is better because it is safer and cleaner.",
        "This improves things generally without a testable condition.",
        "If active claims remain open, idle must stay false in the detector output.",
    ]

    ok, errors = validate_idle_proposal(event)

    assert ok is False
    assert any("reasoning_points" in error for error in errors)


def test_adversarial_review_requires_real_counterexample_language() -> None:
    event = good_adversarial_review()
    event["counterexamples"] = ["This is risky and needs more thought."]

    ok, errors = validate_idle_proposal(event)

    assert ok is False
    assert any("counterexamples" in error for error in errors)


def test_padding_like_simulation_and_charter_reasoning_are_rejected() -> None:
    event = good_idle_proposal()
    event["simulation_evidence"]["summary"] = (
        "good good good good good good good good good good good good good good"
    )
    event["charter_alignment"]["reasoning"] = (
        "safe safe safe safe safe safe safe safe safe safe safe safe safe safe"
    )

    ok, errors = validate_idle_proposal(event)

    assert ok is False
    assert any("simulation_evidence.summary" in error for error in errors)
    assert any("charter_alignment.reasoning" in error for error in errors)


def test_consensus_locks_operator_gate_and_blocks_auto_execute() -> None:
    event = good_consensus()
    event["auto_execute"] = True

    ok, errors = validate_idle_proposal(event)

    assert ok is False
    assert any("auto_execute" in error for error in errors)


def test_charter_violation_requires_termination_and_escalation() -> None:
    event = good_charter_violation()
    event["terminate_protocol"] = False

    ok, errors = validate_idle_proposal(event)

    assert ok is False
    assert any("terminate_protocol" in error for error in errors)
