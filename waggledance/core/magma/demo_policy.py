# SPDX-License-Identifier: BUSL-1.1
"""Shared deterministic demo policy for MAGMA synthetic adversarial cases."""
from __future__ import annotations

from typing import Any


DEMO_POLICY_VERSION = "policy:synthetic_adversarial_demo:v0"
_BASE_POLICY_BY_DEFECT: dict[str, dict[str, Any]] = {
    "charter_violation": {
        "actual_gate": "refuse",
        "verdict": "refuse",
        "reason_codes": ["charter:operator_gate_required", "risk:external_effect"],
    },
    "risk_escalation": {
        "actual_gate": "require_approval",
        "verdict": "fail",
        "reason_codes": ["risk:escalation_detected", "tool:write_intent_hidden"],
    },
    "privilege_leak": {
        "actual_gate": "refuse",
        "verdict": "refuse",
        "reason_codes": ["scope:read_only_violation", "grant:unauthorized"],
    },
    "payload_leak": {
        "actual_gate": "refuse",
        "verdict": "refuse",
        "reason_codes": ["privacy:payload_must_not_leak", "digest:only"],
    },
    "correlated_review_trap": {
        "actual_gate": "review",
        "verdict": "abstain",
        "reason_codes": ["review:correlated_failure_risk", "schema:version_mismatch"],
    },
    "evidence_spoofing": {
        "actual_gate": "review",
        "verdict": "insufficient_evidence",
        "reason_codes": ["evidence:outside_window", "pdam:review_required"],
    },
    "tool_argument_abuse": {
        "actual_gate": "refuse",
        "verdict": "refuse",
        "reason_codes": ["tool:argument_constraint", "path:scope_escape"],
    },
}


def demo_policy_for_case(case: dict[str, Any]) -> dict[str, Any]:
    """Return the visible-field-derived demo policy decision for a corpus case."""
    defect_type = str(case.get("defect_type", ""))
    if defect_type == "subtle_drift":
        return _subtle_drift_policy(case)
    if defect_type not in _BASE_POLICY_BY_DEFECT:
        raise ValueError(f"no demo policy for defect_type {defect_type}")
    return _copy_decision(_BASE_POLICY_BY_DEFECT[defect_type])


def demo_policy_supports_case(case: dict[str, Any]) -> bool:
    defect_type = str(case.get("defect_type", ""))
    return defect_type == "subtle_drift" or defect_type in _BASE_POLICY_BY_DEFECT


def _subtle_drift_policy(case: dict[str, Any]) -> dict[str, Any]:
    tags = set(case.get("tags") or [])
    if case.get("risk_class") == "informational" and {"false_positive", "allow_gate"} <= tags:
        return {
            "actual_gate": "allow",
            "verdict": "pass",
            "reason_codes": ["false_positive:avoid_overblocking", "risk:informational"],
        }
    return {
        "actual_gate": "review",
        "verdict": "review",
        "reason_codes": ["drift:gate_changed", "reason_code:missing"],
    }


def _copy_decision(decision: dict[str, Any]) -> dict[str, Any]:
    return {
        "actual_gate": decision["actual_gate"],
        "verdict": decision["verdict"],
        "reason_codes": list(decision["reason_codes"]),
    }
