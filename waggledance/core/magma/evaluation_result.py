# SPDX-License-Identifier: BUSL-1.1
"""Helpers for emitting MAGMA EvaluationResult v0 objects."""
from __future__ import annotations

from typing import Any

from waggledance.core.magma.canonical import sha256_digest


def build_evaluation_result(
    *,
    case_id: str,
    subject_type: str,
    target_payload: Any,
    risk_class: str,
    expected_gate: str,
    actual_gate: str,
    verifier_path: list[str],
    solver_selection: list[str],
    policy_version: str,
    charter_version: str,
    domain_threshold_version: str,
    verdict: str,
    reason_codes: list[str],
    operator_required: bool,
    confidence_score: float,
    uncertainty_sources: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Build an EvaluationResult v0 dict and bind it to a target payload."""
    return {
        "evaluation_version": "magma.evaluation_result.v0",
        "case_id": case_id,
        "subject_type": subject_type,
        "target_digest": sha256_digest(target_payload),
        "risk_class": risk_class,
        "expected_gate": expected_gate,
        "actual_gate": actual_gate,
        "verifier_path": verifier_path,
        "solver_selection": solver_selection,
        "policy_version": policy_version,
        "charter_version": charter_version,
        "domain_threshold_version": domain_threshold_version,
        "verdict": verdict,
        "reason_codes": reason_codes,
        "operator_required": operator_required,
        "confidence_score": confidence_score,
        "uncertainty_sources": uncertainty_sources or [],
    }
