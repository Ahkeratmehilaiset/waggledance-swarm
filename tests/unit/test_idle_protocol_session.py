# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from waggledance.core.idle_protocol_session import (
    summarize_idle_session,
)


def _base_payload(proposal_id: str, round_number: int, event_type: str) -> dict:
    return {
        "protocol_version": "idle-protocol.v1",
        "event_type": event_type,
        "proposal_id": proposal_id,
        "round_number": round_number,
        "proposes_substrate_change": True,
        "problem_statement": "Strategic idle deliberation needs an operator gated next step.",
        "tradeoff_axis": "Structured peer deliberation versus unmanaged idle discussion drift.",
        "simulation_evidence": {
            "kind": "scenario_simulation",
            "summary": "A fixed transcript demonstrates the next event selection without writes.",
        },
        "charter_alignment": {
            "compatible": True,
            "reasoning": "The session summary never executes actions or converts consensus to work.",
        },
    }


def _proposal(proposal_id: str = "idle-prop-20260518-001") -> dict:
    payload = _base_payload(proposal_id, 1, "idle_proposal")
    payload["proposal"] = (
        "Use session status to request the next idle protocol step without "
        "creating implementation work."
    )
    return payload


def _counter(
    proposal_id: str = "idle-prop-20260518-002",
    *,
    responds_to: str = "idle-prop-20260518-001",
    round_number: int = 2,
) -> dict:
    payload = _base_payload(proposal_id, round_number, "idle_counter_proposal")
    payload["responds_to"] = responds_to
    payload["alternative_proposal"] = (
        "Ask the peer agent for an explicit alternative proposal while keeping "
        "operator approval separate."
    )
    payload["reasoning_points"] = [
        "If the next request is wrong, the following activation test should fail.",
        "When no bridge write is requested, the tool must return only a report.",
        "If consensus is reached, the result requires operator approval.",
    ]
    return payload


def _adversarial(proposal_id: str = "idle-prop-20260518-003") -> dict:
    payload = _base_payload(proposal_id, 3, "idle_adversarial_review")
    payload["responds_to"] = "idle-prop-20260518-002"
    payload["counterexamples"] = [
        "If the proposal hides automatic execution, the session must escalate instead.",
        "When evidence is missing, quality validation should block the payload.",
    ]
    return payload


def _consensus(proposal_id: str) -> dict:
    payload = _base_payload(proposal_id, 5, "idle_consensus_reached")
    payload["proposes_substrate_change"] = False
    payload["consensus_target_proposal_id"] = "idle-prop-20260518-002"
    payload["operator_gate_required"] = True
    payload["auto_execute"] = False
    return payload


def _charter_violation() -> dict:
    payload = _base_payload("idle-prop-20260518-004", 4, "idle_charter_violation")
    payload["proposes_substrate_change"] = False
    payload["violating_proposal_id"] = "idle-prop-20260518-002"
    payload["violation_reason"] = "The reviewed proposal would bypass the operator approval gate."
    payload["terminate_protocol"] = True
    payload["operator_escalation_required"] = True
    payload["charter_alignment"] = {
        "compatible": False,
        "reasoning": "Bypassing operator approval violates the idle protocol charter.",
    }
    return payload


def _low_quality() -> dict:
    payload = _base_payload("idle-prop-20260518-low", 2, "idle_low_quality_response")
    payload["proposes_substrate_change"] = False
    payload["rejected_event_id"] = "idle-prop-20260518-002"
    payload["quality_errors"] = ["simulation_evidence.summary: must contain evidence"]
    payload["operator_escalation_required"] = True
    return payload


def _event(payload: dict, *, agent: str = "codex") -> dict:
    return {
        "ts_utc": "2026-05-18T08:00:00Z",
        "agent": agent,
        "type": "message",
        "task_id": "idle-session-test",
        "status": payload["event_type"],
        "severity": "",
        "to": "claude" if agent == "codex" else "codex",
        "message": "Idle protocol test event with substantive content.",
        "paths": [],
        "write_scope": [],
        "run_id": "",
        "pid": 1234,
        "cwd": "C:\\Python\\project2-master",
        "payload": payload,
    }


def test_no_session_requests_round_one_proposal() -> None:
    summary = summarize_idle_session([])

    assert summary["status"] == "no_session"
    assert summary["next_required_event"] == {
        "event_type": "idle_proposal",
        "round_number": 1,
        "responds_to": None,
    }
    assert summary["auto_execute"] is False


def test_round_one_requests_counter_proposal() -> None:
    summary = summarize_idle_session([_event(_proposal())])

    assert summary["status"] == "active_session"
    assert summary["latest_round"] == 1
    assert summary["next_required_event"] == {
        "event_type": "idle_counter_proposal",
        "round_number": 2,
        "responds_to": "idle-prop-20260518-001",
    }


def test_round_two_requests_mandatory_adversarial_review() -> None:
    summary = summarize_idle_session([_event(_proposal()), _event(_counter())])

    assert summary["status"] == "active_session"
    assert summary["next_required_event"]["event_type"] == "idle_adversarial_review"
    assert summary["next_required_event"]["round_number"] == 3


def test_round_three_requests_round_four_counter_proposal() -> None:
    summary = summarize_idle_session([
        _event(_proposal()),
        _event(_counter()),
        _event(_adversarial()),
    ])

    assert summary["status"] == "active_session"
    assert summary["next_required_event"]["event_type"] == "idle_counter_proposal"
    assert summary["next_required_event"]["round_number"] == 4


def test_soft_convergence_is_operator_gated_terminal() -> None:
    summary = summarize_idle_session([
        _event(_proposal()),
        _event(_counter()),
        _event(_adversarial()),
        _event(_consensus("idle-prop-20260518-005a")),
        _event(_consensus("idle-prop-20260518-005b"), agent="claude"),
    ])

    assert summary["status"] == "soft_convergence"
    assert summary["terminal"] is True
    assert summary["operator_gate_required"] is True
    assert summary["auto_execute"] is False
    assert summary["next_required_event"] is None


def test_charter_violation_terminates_for_operator_escalation() -> None:
    summary = summarize_idle_session([
        _event(_proposal()),
        _event(_counter()),
        _event(_charter_violation(), agent="claude"),
    ])

    assert summary["status"] == "charter_violation"
    assert summary["terminal"] is True
    assert summary["operator_gate_required"] is True
    assert summary["next_required_event"] is None


def test_low_quality_response_pauses_for_operator_escalation() -> None:
    summary = summarize_idle_session([_event(_proposal()), _event(_low_quality())])

    assert summary["status"] == "operator_escalation"
    assert summary["terminal"] is True
    assert summary["operator_gate_required"] is True
    assert summary["next_required_event"] is None


def test_invalid_idle_payload_is_terminal_operator_gated() -> None:
    bad = _proposal()
    del bad["simulation_evidence"]

    summary = summarize_idle_session([_event(bad)])

    assert summary["status"] == "invalid_event"
    assert summary["terminal"] is True
    assert summary["operator_gate_required"] is True
    assert summary["next_required_event"] is None


def test_status_summary_is_data_only_not_a_bridge_event() -> None:
    summary = summarize_idle_session([_event(_proposal())])

    assert "type" not in summary
    assert "agent" not in summary
    assert "payload" not in summary
    assert summary["auto_execute"] is False
