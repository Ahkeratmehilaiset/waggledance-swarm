# SPDX-License-Identifier: BUSL-1.1
"""Helpers for emitting MAGMA receipt v1 objects."""
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
    / "magma_receipt.v1.json"
)
SIGNATURE_ENVELOPE_KEYS = {"signature_algorithm", "signature", "key_id"}


def build_magma_receipt(
    *,
    event_id: str,
    ts_utc: str,
    risk_class: str,
    payload: Any,
    evaluation_result: dict[str, Any],
    policy_digest: str,
    charter_digest: str,
    rco_decision_digest: str,
    world_snapshot_digest: str,
    solver_contract_digest: str,
    payload_visibility: str = "digest_only",
    previous_receipt: dict[str, Any] | None = None,
    approval_id: str | None = None,
    signature_envelope: dict[str, str] | None = None,
    anchored_at: str | None = None,
    allow_external_effect: bool = False,
) -> dict[str, Any]:
    """Build a schema-valid MAGMA receipt bound to payload and evaluation digests.

    External-reference digests are caller-provided; this helper validates their
    schema shape but cannot verify the underlying artifacts.
    """
    operator_gate_required = risk_class == "external_effect"
    if operator_gate_required and not allow_external_effect:
        raise ValueError("pure MAGMA receipt helper refuses external_effect by default")
    if operator_gate_required and approval_id is None:
        raise ValueError("external_effect MAGMA receipt requires approval_id")

    signature_fields = _signature_fields(signature_envelope)
    receipt = {
        "receipt_version": "magma.receipt.v1",
        "event_id": event_id,
        "ts_utc": ts_utc,
        "risk_class": risk_class,
        "payload_visibility": payload_visibility,
        "canonical_payload_digest": sha256_digest(payload),
        "prev_receipt_hash": (
            sha256_digest(previous_receipt)
            if previous_receipt is not None
            else None
        ),
        "policy_digest": policy_digest,
        "charter_digest": charter_digest,
        "rco_decision_digest": rco_decision_digest,
        "world_snapshot_digest": world_snapshot_digest,
        "solver_contract_digest": solver_contract_digest,
        "evaluation_result_digest": sha256_digest(evaluation_result),
        "approval_id": approval_id,
        "operator_gate_required": operator_gate_required,
        "signature_algorithm": signature_fields["signature_algorithm"],
        "signature": signature_fields["signature"],
        "key_id": signature_fields["key_id"],
        "anchored_at": anchored_at,
    }
    _validate_magma_receipt(receipt)
    return receipt


def _signature_fields(signature_envelope: dict[str, str] | None) -> dict[str, str | None]:
    if signature_envelope is None:
        return {
            "signature_algorithm": None,
            "signature": None,
            "key_id": None,
        }
    if set(signature_envelope) != SIGNATURE_ENVELOPE_KEYS:
        expected = ", ".join(sorted(SIGNATURE_ENVELOPE_KEYS))
        raise ValueError(f"signature_envelope must contain exactly: {expected}")
    return {
        "signature_algorithm": signature_envelope["signature_algorithm"],
        "signature": signature_envelope["signature"],
        "key_id": signature_envelope["key_id"],
    }


def _validate_magma_receipt(receipt: dict[str, Any]) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = jsonschema.Draft7Validator(
        schema,
        format_checker=jsonschema.FormatChecker(),
    )
    errors = sorted(validator.iter_errors(receipt), key=lambda item: list(item.path))
    if errors:
        message = "; ".join(
            f"{'.'.join(str(part) for part in error.path) or '<root>'}: {error.message}"
            for error in errors
        )
        raise ValueError(f"invalid MAGMA receipt v1: {message}")
