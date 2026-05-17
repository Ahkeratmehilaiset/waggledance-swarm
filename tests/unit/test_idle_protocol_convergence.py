# SPDX-License-Identifier: BUSL-1.1
"""Unit tests for idle protocol convergence detection."""
from __future__ import annotations

import copy

from waggledance.core.idle_protocol import detect_idle_convergence


def good_idle_proposal() -> dict:
    return {
        "protocol_version": "idle-protocol.v1",
        "event_type": "idle_proposal",
        "proposal_id": "idle-prop-20260517-001",
        "round_number": 1,
        "proposes_substrate_change": True,
        "problem_statement": "Strategic bridge deliberation stalls when no PR vehicle exists.",
        "proposal": (
            "Add a manual idle-check primitive that reports active or idle "
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


def _consensus(proposal_id: str, target: str, round_number: int = 5) -> dict:
    event = good_consensus()
    event["proposal_id"] = proposal_id
    event["consensus_target_proposal_id"] = target
    event["round_number"] = round_number
    return event


def test_soft_convergence_requires_two_round_five_consensus_links() -> None:
    events = [
        good_idle_proposal(),
        good_counter_proposal(),
        good_adversarial_review(),
        _consensus("idle-prop-20260517-005a", "idle-prop-20260517-002", 5),
        _consensus("idle-prop-20260517-005b", "idle-prop-20260517-002", 5),
    ]

    report = detect_idle_convergence(events)

    assert report == {
        "status": "soft_convergence",
        "round_number": 5,
        "target_proposal_id": "idle-prop-20260517-002",
        "supporting_consensus_ids": [
            "idle-prop-20260517-005a",
            "idle-prop-20260517-005b",
        ],
        "operator_gate_required": True,
        "auto_execute": False,
    }


def test_hard_convergence_round_ten_returns_two_or_three_finalists() -> None:
    proposal = good_idle_proposal()
    counter = good_counter_proposal()
    alternative = copy.deepcopy(counter)
    alternative["proposal_id"] = "idle-prop-20260517-006"
    alternative["round_number"] = 9
    alternative["responds_to"] = "idle-prop-20260517-003"
    alternative["alternative_proposal"] = (
        "Use a stricter detector cooldown and keep manual operator launch until "
        "historical replay proves no false-idle cases remain."
    )
    round_ten = copy.deepcopy(counter)
    round_ten["proposal_id"] = "idle-prop-20260517-010"
    round_ten["round_number"] = 10
    round_ten["responds_to"] = "idle-prop-20260517-006"
    round_ten["alternative_proposal"] = (
        "Stop at round ten and forward the last concrete finalists to the operator."
    )

    report = detect_idle_convergence([
        proposal,
        counter,
        good_adversarial_review(),
        alternative,
        round_ten,
    ])

    assert report is not None
    assert report["status"] == "hard_convergence"
    assert report["round_number"] == 10
    assert report["operator_gate_required"] is True
    assert report["auto_execute"] is False
    assert report["finalist_proposal_ids"] == [
        "idle-prop-20260517-010",
        "idle-prop-20260517-006",
        "idle-prop-20260517-002",
    ]


def test_charter_violation_early_terminates_before_later_consensus() -> None:
    violation = good_charter_violation()
    later_consensus = _consensus("idle-prop-20260517-006", "idle-prop-20260517-002", 6)

    report = detect_idle_convergence([
        good_idle_proposal(),
        good_counter_proposal(),
        violation,
        later_consensus,
    ])

    assert report == {
        "status": "charter_violation",
        "round_number": 4,
        "violating_proposal_id": "idle-prop-20260517-003",
        "violation_event_id": "idle-prop-20260517-004",
        "terminate_protocol": True,
        "operator_escalation_required": True,
    }


def test_returns_none_before_convergence_or_round_ten() -> None:
    report = detect_idle_convergence([
        good_idle_proposal(),
        good_counter_proposal(),
        good_adversarial_review(),
    ])

    assert report is None


def test_bridge_envelope_payloads_are_supported() -> None:
    event = _consensus("idle-prop-20260517-005a", "idle-prop-20260517-002", 5)
    other = _consensus("idle-prop-20260517-005b", "idle-prop-20260517-002", 5)

    report = detect_idle_convergence([
        {"type": "message", "payload": event},
        {"type": "message", "payload": other},
    ])

    assert report is not None
    assert report["status"] == "soft_convergence"


def test_invalid_idle_event_reports_validation_error() -> None:
    event = good_idle_proposal()
    del event["simulation_evidence"]

    report = detect_idle_convergence([event])

    assert report is not None
    assert report["status"] == "invalid_event"
    assert report["proposal_id"] == "idle-prop-20260517-001"
    assert any("simulation_evidence" in error for error in report["errors"])
