# SPDX-License-Identifier: BUSL-1.1
"""Idle-protocol session summarization primitives.

The helpers in this module are read-only. They infer the current idle-protocol
session state from bridge events or raw idle payloads and describe the next
expected deliberation step. They never create work items and never execute
consensus.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from waggledance.core.idle_protocol import detect_idle_convergence, validate_idle_proposal


REFERENCE_FIELDS = (
    "responds_to",
    "consensus_target_proposal_id",
    "rejected_event_id",
    "violating_proposal_id",
)
NEXT_BY_ROUND = {
    1: "idle_counter_proposal",
    2: "idle_adversarial_review",
    3: "idle_counter_proposal",
    4: "idle_consensus_reached",
}
TERMINAL_STATUSES = {
    "charter_violation",
    "soft_convergence",
    "hard_convergence",
    "invalid_event",
}


def summarize_idle_session(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Return an operator-gated summary for the latest idle-protocol instance."""
    payloads = extract_idle_payloads(events)
    if not payloads:
        return {
            "protocol_version": "idle-protocol.v1",
            "status": "no_session",
            "terminal": False,
            "operator_gate_required": False,
            "auto_execute": False,
            "latest_round": 0,
            "latest_proposal_id": None,
            "instance_root_proposal_id": None,
            "payload_count": 0,
            "next_required_event": {
                "event_type": "idle_proposal",
                "round_number": 1,
                "responds_to": None,
            },
        }

    instance_payloads = _latest_instance(payloads)
    convergence = detect_idle_convergence(instance_payloads)
    latest = instance_payloads[-1]
    latest_round = int(latest["round_number"])
    latest_event_type = str(latest["event_type"])
    root_id = _instance_root(latest, _payloads_by_id(payloads))

    if convergence is not None:
        status = str(convergence["status"])
        return {
            "protocol_version": "idle-protocol.v1",
            "status": status,
            "terminal": status in TERMINAL_STATUSES,
            "operator_gate_required": bool(
                convergence.get("operator_gate_required")
                or convergence.get("operator_escalation_required")
                or status in TERMINAL_STATUSES
            ),
            "auto_execute": False,
            "latest_round": latest_round,
            "latest_event_type": latest_event_type,
            "latest_proposal_id": latest.get("proposal_id"),
            "instance_root_proposal_id": root_id,
            "payload_count": len(instance_payloads),
            "convergence": convergence,
            "next_required_event": None,
        }

    if latest_event_type == "idle_low_quality_response":
        return {
            "protocol_version": "idle-protocol.v1",
            "status": "operator_escalation",
            "terminal": True,
            "operator_gate_required": True,
            "auto_execute": False,
            "latest_round": latest_round,
            "latest_event_type": latest_event_type,
            "latest_proposal_id": latest.get("proposal_id"),
            "instance_root_proposal_id": root_id,
            "payload_count": len(instance_payloads),
            "next_required_event": None,
            "reason_codes": ["idle_protocol:low_quality_response"],
        }

    next_event_type = NEXT_BY_ROUND.get(latest_round, "idle_consensus_reached")
    next_round = min(latest_round + 1, 10)
    return {
        "protocol_version": "idle-protocol.v1",
        "status": "active_session",
        "terminal": False,
        "operator_gate_required": False,
        "auto_execute": False,
        "latest_round": latest_round,
        "latest_event_type": latest_event_type,
        "latest_proposal_id": latest.get("proposal_id"),
        "instance_root_proposal_id": root_id,
        "payload_count": len(instance_payloads),
        "next_required_event": {
            "event_type": next_event_type,
            "round_number": next_round,
            "responds_to": latest.get("proposal_id"),
        },
    }


def extract_idle_payloads(events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Extract idle-protocol payloads from raw payloads or bridge envelopes."""
    payloads: list[dict[str, Any]] = []
    for event in events:
        payload = _idle_payload(event)
        if payload is None:
            continue
        payloads.append(dict(payload))
    return payloads


def _idle_payload(event: Mapping[str, Any]) -> Mapping[str, Any] | None:
    if event.get("protocol_version") == "idle-protocol.v1":
        ok, _errors = validate_idle_proposal(dict(event))
        return event if ok else event
    payload = event.get("payload")
    if isinstance(payload, Mapping) and payload.get("protocol_version") == "idle-protocol.v1":
        return payload
    return None


def _latest_instance(payloads: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    by_id = _payloads_by_id(payloads)
    root = _instance_root(payloads[-1], by_id)
    if root is None:
        return list(payloads)
    return [payload for payload in payloads if _instance_root(payload, by_id) == root]


def _payloads_by_id(
    payloads: Sequence[Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    return {
        str(payload.get("proposal_id")): payload
        for payload in payloads
        if payload.get("proposal_id")
    }


def _instance_root(
    payload: Mapping[str, Any],
    by_id: Mapping[str, Mapping[str, Any]],
) -> str | None:
    try:
        if int(payload.get("round_number", 0)) == 1:
            return str(payload.get("proposal_id", "")) or None
    except (TypeError, ValueError):
        return None

    target = _first_reference(payload)
    seen: set[str] = set()
    while target and target not in seen:
        seen.add(target)
        prior = by_id.get(target)
        if prior is None:
            return None
        try:
            if int(prior.get("round_number", 0)) == 1:
                return str(prior.get("proposal_id", "")) or None
        except (TypeError, ValueError):
            return None
        target = _first_reference(prior)
    return None


def _first_reference(payload: Mapping[str, Any]) -> str | None:
    for field in REFERENCE_FIELDS:
        target = payload.get(field)
        if target:
            return str(target)
    return None
