# SPDX-License-Identifier: BUSL-1.1
"""Helpers for emitting MAGMA EvaluationResult v0 and v1 objects.

Version dispatch:
- ``magma.evaluation_result.v0`` validates against ``evaluation_result.v0.json``.
- ``magma.evaluation_result.v1`` validates against ``evaluation_result.v1.json``
  (additive superset: optional ``confidence_basis``, ``sanitization_audit``,
  ``competitor_axis_reference``, ``subject_payload_size_bytes``).

See ``docs/architecture/EVALUATION_RESULT_V1_DRAFT.md`` for the v1 design RFC.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import jsonschema

from waggledance.core.magma.canonical import sha256_digest


SCHEMA_ROOT = Path(__file__).resolve().parents[3] / "schemas" / "v3_13_0"
SCHEMA_PATH = SCHEMA_ROOT / "evaluation_result.v0.json"
SCHEMA_PATHS: dict[str, Path] = {
    "magma.evaluation_result.v0": SCHEMA_ROOT / "evaluation_result.v0.json",
    "magma.evaluation_result.v1": SCHEMA_ROOT / "evaluation_result.v1.json",
}


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


def build_evaluation_result_v1(
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
    confidence_basis: Mapping[str, Any] | None = None,
    sanitization_audit: Mapping[str, Any] | None = None,
    competitor_axis_reference: str | None = None,
    subject_payload_size_bytes: int | None = None,
) -> dict[str, Any]:
    """Build an EvaluationResult v1 dict.

    v1 is an additive superset of v0. The four additional kwargs
    (``confidence_basis``, ``sanitization_audit``,
    ``competitor_axis_reference``, ``subject_payload_size_bytes``) are
    OPTIONAL. Anti-overclaim invariants from v0 still hold:
    external_effect ⇒ operator_required=True, and the receipt binding
    in ``waggledance.core.magma.receipt`` checks the v1 record's
    ``target_digest`` exactly like v0.
    """
    if risk_class == "external_effect" and not allow_external_effect:
        raise ValueError(
            "pure EvaluationResult helper refuses external_effect by default"
        )

    result: dict[str, Any] = {
        "evaluation_version": "magma.evaluation_result.v1",
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
    if confidence_basis is not None:
        result["confidence_basis"] = dict(confidence_basis)
    if sanitization_audit is not None:
        result["sanitization_audit"] = dict(sanitization_audit)
    if competitor_axis_reference is not None:
        result["competitor_axis_reference"] = competitor_axis_reference
    if subject_payload_size_bytes is not None:
        result["subject_payload_size_bytes"] = subject_payload_size_bytes
    _validate_evaluation_result(result)
    return result


def _validate_evaluation_result(result: dict[str, Any]) -> None:
    version = result.get("evaluation_version")
    schema_path = SCHEMA_PATHS.get(version)
    if schema_path is None:
        raise ValueError(
            f"unknown evaluation_version {version!r}; expected one of: "
            + ", ".join(sorted(SCHEMA_PATHS))
        )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
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
        raise ValueError(f"invalid {version}: {message}")
