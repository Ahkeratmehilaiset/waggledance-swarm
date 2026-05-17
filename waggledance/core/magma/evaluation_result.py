# SPDX-License-Identifier: BUSL-1.1
"""Helpers for emitting MAGMA EvaluationResult v0 objects."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema

from waggledance.core.magma.canonical import sha256_digest


SCHEMA_PATH = Path(__file__).resolve().parents[3] / "schemas" / "v3_13_0" / "evaluation_result.v0.json"


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
    confidence_score: float,
    uncertainty_sources: list[dict[str, str]] | None = None,
    allow_external_effect: bool = False,
) -> dict[str, Any]:
    """Build an EvaluationResult v0 dict and bind it to a target payload."""
    if risk_class == "external_effect" and not allow_external_effect:
        raise ValueError("pure EvaluationResult helper refuses external_effect by default")

    result = {
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
        "operator_required": risk_class == "external_effect",
        "confidence_score": confidence_score,
        "uncertainty_sources": uncertainty_sources or [],
    }
    _validate_evaluation_result(result)
    return result


def _validate_evaluation_result(result: dict[str, Any]) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = jsonschema.Draft7Validator(
        schema,
        format_checker=jsonschema.FormatChecker(),
    )
    errors = sorted(validator.iter_errors(result), key=lambda item: list(item.path))
    if errors:
        message = "; ".join(
            f"{'.'.join(str(part) for part in error.path) or '<root>'}: {error.message}"
            for error in errors
        )
        raise ValueError(f"invalid EvaluationResult v0: {message}")
