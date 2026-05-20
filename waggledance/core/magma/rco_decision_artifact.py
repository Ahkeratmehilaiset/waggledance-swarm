# SPDX-License-Identifier: BUSL-1.1
"""Helpers for emitting MAGMA RCO decision artifact v0 objects."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema

from waggledance.core.magma.canonical import sha256_digest


SCHEMA_PATH = (
    Path(__file__).resolve().parents[3]
    / "schemas"
    / "v3_13_0"
    / "rco_decision_artifact.v0.json"
)


def build_rco_decision_artifact(
    *,
    decision_id: str,
    ts_utc: str,
    intent: Any,
    write_payload: Any,
    risk_class: str,
    gate_decision: str,
    approved: bool,
    policy_version: str,
    charter_version: str,
    reason_codes: list[str],
    verifier_path: list[str],
    operator_required: bool | None = None,
    approval_id: str | None = None,
    scope_policy_decision: str = "not_applicable",
    peer_rco_verdict: str = "not_requested",
    audit_event_ids: list[str] | None = None,
    stop_condition: str | None = None,
) -> dict[str, Any]:
    """Build a payload-free RCO artifact bound to intent and write digests."""
    if gate_decision == "refuse" and approved:
        raise ValueError("refused RCO decisions cannot be approved")
    if risk_class == "external_effect" and operator_required is False:
        raise ValueError("external_effect RCO decisions require operator_required")

    artifact = {
        "rco_decision_version": "magma.rco_decision_artifact.v0",
        "decision_id": decision_id,
        "ts_utc": ts_utc,
        "intent_digest": sha256_digest(intent),
        "write_payload_digest": sha256_digest(write_payload),
        "risk_class": risk_class,
        "gate_decision": gate_decision,
        "approved": approved,
        "operator_required": (
            risk_class == "external_effect"
            if operator_required is None
            else operator_required
        ),
        "approval_id": approval_id,
        "policy_version": policy_version,
        "charter_version": charter_version,
        "scope_policy_decision": scope_policy_decision,
        "peer_rco_verdict": peer_rco_verdict,
        "verifier_path": verifier_path,
        "reason_codes": reason_codes,
        "audit_event_ids": audit_event_ids or [],
        "stop_condition": stop_condition,
    }
    validate_rco_decision_artifact(artifact)
    return artifact


def validate_rco_decision_artifact(artifact: dict[str, Any]) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = jsonschema.Draft7Validator(
        schema,
        format_checker=jsonschema.FormatChecker(),
    )
    errors = sorted(validator.iter_errors(artifact), key=lambda item: list(item.path))
    if errors:
        message = "; ".join(
            f"{'.'.join(str(part) for part in error.path) or '<root>'}: {error.message}"
            for error in errors
        )
        raise ValueError(f"invalid RCO decision artifact v0: {message}")
