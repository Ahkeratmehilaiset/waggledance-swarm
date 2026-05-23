# SPDX-License-Identifier: BUSL-1.1
"""Opt-in MAGMA receipt bundles for AutonomyRuntime query summaries.

The runtime summary payload is intentionally sanitized: it binds the query path
to digests, ids, gates, and verifier outcomes without copying raw query text,
context values, executor payloads, or solver outputs into the emitted bundle.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from waggledance.core.magma.canonical import sha256_digest
from waggledance.core.magma.evaluation_result import (
    build_evaluation_result,
    build_evaluation_result_v1,
)
from waggledance.core.magma.receipt import build_magma_receipt
from waggledance.core.magma.receipt_bundle import (
    ReceiptBundleEntry,
    write_receipt_bundle,
)


PAYLOAD_VERSION = "magma.runtime_summary_receipt_payload.v0"
CHAIN_ID = "magma:runtime_summary:handle_query:v0"
EVALUATION_VERSION_V0 = "magma.evaluation_result.v0"
EVALUATION_VERSION_V1 = "magma.evaluation_result.v1"


def build_handle_query_runtime_summary(
    *,
    query: str,
    context: Mapping[str, Any],
    profile: str,
    intent: str,
    quality_path: str,
    capability_id: str,
    action_id: str,
    approved: bool,
    executed: bool,
    needs_approval: bool,
    decision_reason: str,
    elapsed_ms: float,
    snapshot_id: str,
    case_id: str,
    verifier_passed: bool | None,
    verifier_confidence: float | None,
    result_keys: list[str],
) -> dict[str, Any]:
    """Build a payload-safe summary for a completed handle_query path."""
    actual_gate = _actual_gate(
        approved=approved,
        executed=executed,
        needs_approval=needs_approval,
    )
    verdict = _verdict(
        approved=approved,
        executed=executed,
        needs_approval=needs_approval,
        verifier_passed=verifier_passed,
    )
    return {
        "payload_version": PAYLOAD_VERSION,
        "runtime_path": "AutonomyRuntime.handle_query",
        "profile": str(profile),
        "intent": str(intent),
        "quality_path": str(quality_path),
        "capability_id": str(capability_id),
        "action_id": str(action_id),
        "case_id": str(case_id),
        "world_snapshot_ref": str(snapshot_id),
        "approved": bool(approved),
        "executed": bool(executed),
        "needs_approval": bool(needs_approval),
        "actual_gate": actual_gate,
        "expected_gate": actual_gate,
        "verdict": verdict,
        "elapsed_ms": round(float(elapsed_ms), 2),
        "query_digest": sha256_digest({"query": str(query)}),
        "query_length": len(str(query)),
        "context_keys": sorted(str(key) for key in context.keys()),
        "decision_reason_digest": sha256_digest({"reason": str(decision_reason)}),
        "result_keys": sorted(str(key) for key in result_keys),
        "verifier_passed": verifier_passed,
        "verifier_confidence": (
            round(float(verifier_confidence), 4)
            if verifier_confidence is not None
            else None
        ),
    }


def write_runtime_summary_receipt_bundle(
    *,
    out_dir: Path,
    summary_payload: Mapping[str, Any],
    now_utc: datetime,
    verify_manifest: Callable[[Path], dict[str, Any]],
    previous_receipt: dict[str, Any] | None = None,
    evaluation_version: str = EVALUATION_VERSION_V0,
) -> dict[str, Any]:
    """Write and verify a one-entry runtime summary receipt bundle."""
    payload = dict(summary_payload)
    _validate_summary_payload(payload)
    now_utc = _coerce_utc(now_utc)
    evaluation = _build_runtime_evaluation(
        payload=payload,
        evaluation_version=evaluation_version,
    )
    receipt = build_magma_receipt(
        event_id=f"magma:runtime_summary:{payload['action_id']}",
        ts_utc=_iso(now_utc),
        risk_class=evaluation["risk_class"],
        payload=payload,
        evaluation_result=evaluation,
        previous_receipt=previous_receipt,
        policy_digest=sha256_digest({"policy_version": evaluation["policy_version"]}),
        charter_digest=sha256_digest({"charter_version": evaluation["charter_version"]}),
        rco_decision_digest=sha256_digest(
            {
                "actual_gate": evaluation["actual_gate"],
                "approved": payload["approved"],
                "executed": payload["executed"],
                "needs_approval": payload["needs_approval"],
                "decision_reason_digest": payload["decision_reason_digest"],
            }
        ),
        world_snapshot_digest=sha256_digest(
            {
                "profile": payload["profile"],
                "world_snapshot_ref": payload["world_snapshot_ref"],
            }
        ),
        solver_contract_digest=sha256_digest(
            {
                "capability_id": payload["capability_id"],
                "quality_path": payload["quality_path"],
                "solver_selection": evaluation["solver_selection"],
            }
        ),
    )
    return write_receipt_bundle(
        out_dir=out_dir,
        chain_id=CHAIN_ID,
        entries=[
            ReceiptBundleEntry(
                label="runtime-summary",
                payload=payload,
                evaluation_result=evaluation,
                receipt=receipt,
            )
        ],
        verify_manifest=verify_manifest,
    )


def _build_runtime_evaluation(
    *,
    payload: Mapping[str, Any],
    evaluation_version: str,
) -> dict[str, Any]:
    kwargs = {
        "case_id": str(payload["case_id"]),
        "subject_type": "policy",
        "target_payload": payload,
        "risk_class": "internal_memory",
        "expected_gate": str(payload["expected_gate"]),
        "actual_gate": str(payload["actual_gate"]),
        "verifier_path": [
            "autonomy_runtime_handle_query",
            "runtime_summary_payload_v0",
            "magma_receipt_v1",
            "offline_receipt_verifier",
        ],
        "solver_selection": _solver_selection(payload),
        "policy_version": f"policy:autonomy_runtime:{payload['profile']}",
        "charter_version": "charter:v1",
        "domain_threshold_version": "threshold:autonomy_runtime:v1",
        "verdict": str(payload["verdict"]),
        "reason_codes": _reason_codes(payload),
        "confidence_score": _confidence(payload),
        "uncertainty_sources": _uncertainty_sources(payload),
    }
    if evaluation_version == EVALUATION_VERSION_V0:
        return build_evaluation_result(**kwargs)
    if evaluation_version == EVALUATION_VERSION_V1:
        return build_evaluation_result_v1(
            **kwargs,
            confidence_basis={
                "method": "point_estimate",
                "sample_count": 1,
                "methodology_reference": "runtime_summary_receipt_v0",
            },
            sanitization_audit={
                "applied": ["reserved_domain_allowlist"],
                "redaction_count": 0,
            },
            subject_payload_size_bytes=_payload_size_bytes(payload),
        )
    raise ValueError(
        "runtime summary evaluation_version must be one of: "
        f"{EVALUATION_VERSION_V0}, {EVALUATION_VERSION_V1}"
    )


def _validate_summary_payload(payload: Mapping[str, Any]) -> None:
    if payload.get("payload_version") != PAYLOAD_VERSION:
        raise ValueError("runtime summary payload_version mismatch")
    for key in (
        "profile",
        "case_id",
        "action_id",
        "actual_gate",
        "expected_gate",
        "verdict",
        "query_digest",
        "decision_reason_digest",
    ):
        if not payload.get(key):
            raise ValueError(f"runtime summary missing required field: {key}")


def _payload_size_bytes(payload: Mapping[str, Any]) -> int:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return len(encoded.encode("utf-8"))


def _actual_gate(*, approved: bool, executed: bool, needs_approval: bool) -> str:
    if needs_approval:
        return "require_approval"
    if approved and executed:
        return "allow"
    if approved:
        return "review"
    return "refuse"


def _verdict(
    *,
    approved: bool,
    executed: bool,
    needs_approval: bool,
    verifier_passed: bool | None,
) -> str:
    if needs_approval:
        return "review"
    if not approved:
        return "refuse"
    if not executed:
        return "review"
    if verifier_passed is False:
        return "review"
    return "pass"


def _solver_selection(payload: Mapping[str, Any]) -> list[str]:
    cap_id = str(payload.get("capability_id") or "")
    return [cap_id] if cap_id else []


def _reason_codes(payload: Mapping[str, Any]) -> list[str]:
    codes = [
        "runtime_summary",
        "path:handle_query",
        f"gate:{payload['actual_gate']}",
    ]
    codes.append("action:executed" if payload.get("executed") else "action:not_executed")
    verifier_passed = payload.get("verifier_passed")
    if verifier_passed is True:
        codes.append("verifier:passed")
    elif verifier_passed is False:
        codes.append("verifier:failed")
    else:
        codes.append("verifier:not_run")
    return codes


def _confidence(payload: Mapping[str, Any]) -> float:
    verifier_confidence = payload.get("verifier_confidence")
    if isinstance(verifier_confidence, (int, float)):
        return min(1.0, max(0.0, float(verifier_confidence)))
    if payload.get("approved") and payload.get("executed"):
        return 0.75
    if payload.get("needs_approval"):
        return 0.55
    return 0.4


def _uncertainty_sources(payload: Mapping[str, Any]) -> list[dict[str, str]]:
    if payload.get("verifier_passed") is None:
        return [
            {
                "kind": "limited_evidence",
                "detail": "Runtime summary was emitted without a verifier result.",
            }
        ]
    return []


def _coerce_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
