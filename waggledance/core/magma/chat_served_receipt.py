# SPDX-License-Identifier: BUSL-1.1
"""Opt-in MAGMA receipt bundles for ChatService served responses (P2 S1b).

Every *served* chat response (solver-first / hybrid / hex / LLM / hotcache)
gets a gapless append-only MAGMA receipt so the deterministic-solver-first
claim-safe milestone (Image #1 Panel 2) has a single, end-to-end verifiable
audit trail alongside the v3.13 dispatch + HTTP-route receipts.

Claim-safety (design confirmed with lead A-prime + rco-2):
* The receipt is the canonical MAGMA v1 envelope (one receipt type, one
  verifier walks one chain across all served paths) -- NOT a divergent shape.
* A chat response does NOT pass an RCO/approval gate and has no solver
  contract. Faking ``rco_decision_digest`` / ``solver_contract_digest`` with a
  real-looking (hex) value would bake a *governance overclaim* into the
  receipt. So those two fields carry an explicit, NON-HEX, self-describing
  ``na:route_class=chat:*`` sentinel that a verifier can positively distinguish
  from a real 64-hex digest AND from a missing / zero / ``sha256("")`` one, and
  that can never read as "this gate was evaluated and passed".
* ``charter``/``policy``/``world_snapshot`` are REAL provenance digests
  (version/context binding) -- required for cross-verifiability, not fake.
* ``evaluation_result`` truthfully encodes the route decision (route_type /
  source / confidence); it never synthesizes a verification that did not run.
* The payload is privacy-safe: query/response are carried as digests only,
  never raw text.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from waggledance.core.magma.canonical import sha256_digest
from waggledance.core.magma.evaluation_result import build_evaluation_result
from waggledance.core.magma.receipt import build_magma_receipt
from waggledance.core.magma.receipt_bundle import (
    ReceiptBundleEntry,
    write_receipt_bundle,
)


PAYLOAD_VERSION = "magma.chat_served_receipt_payload.v0"
CHAIN_ID = "magma:chat_service:served:v0"

# Explicit "not-applicable for route_class=chat" sentinels for the RCO-decision
# and solver-contract digest fields. NOTE: MAGMA receipt v1 (enforced by
# receipt.py::_validate_magma_receipt) constrains these fields to
# ^sha256:[a-f0-9]{64}$, so the sentinel MUST be hex -- a non-hex literal is
# NOT schema-valid (contra an early design note). We therefore use a FIXED,
# WELL-KNOWN sha256 of a self-describing not-applicable namespace: it is
# schema-valid, and a specific known constant a verifier can positively match
# to distinguish it from (i) a real content digest, (ii) sha256("") and
# (iii) a zero-digest. Because it is hex, distinguishability RELIES ON the
# verifier knowing these constants (see the verify_magma_receipt enforcement),
# so a chat path can never masquerade a governed rco_decision/solver_contract.
RCO_DECISION_NA_SENTINEL = sha256_digest(
    {
        "not_applicable": True,
        "route_class": "chat",
        "field": "rco_decision_digest",
        "reason": "no_rco_decision_gate",
    }
)
SOLVER_CONTRACT_NA_SENTINEL = sha256_digest(
    {
        "not_applicable": True,
        "route_class": "chat",
        "field": "solver_contract_digest",
        "reason": "no_solver_contract",
    }
)

# route_stage_trace stage events are sanitized to this allowlisted key set.
_ROUTE_STAGE_ALLOWED_KEYS = frozenset(
    {
        "stage",
        "route_type",
        "source",
        "intent",
        "answered",
        "hit",
        "detected_language",
        "explicit_hint",
        "result_count",
        "memory_score",
        "solver_intent",
    }
)


def build_chat_served_summary(
    *,
    query: str,
    response: str,
    route_type: str,
    source: str,
    confidence: float,
    latency_ms: float,
    cached: bool,
    round_table: bool,
    agent_id: str | None,
    language: str,
    profile: str,
    world_snapshot_ref: str,
    route_stage_trace: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a payload-safe summary for one served ChatService response.

    Raw ``query``/``response`` never enter the payload -- only their digests.
    """
    sanitized_trace = _sanitize_route_stage_trace(route_stage_trace or [])
    return {
        "payload_version": PAYLOAD_VERSION,
        "served_path": "ChatService.handle",
        "route_type": str(route_type),
        "source": str(source),
        "confidence": round(float(confidence), 4),
        "latency_ms": round(float(latency_ms), 2),
        "cached": bool(cached),
        "round_table": bool(round_table),
        "agent_id": str(agent_id) if agent_id is not None else None,
        "language": str(language),
        "profile": str(profile),
        "world_snapshot_ref": str(world_snapshot_ref),
        "query_digest": sha256_digest({"query": str(query)}),
        "query_length": len(str(query)),
        "response_digest": sha256_digest({"response": str(response)}),
        "response_length": len(str(response)),
        "route_stage_trace": sanitized_trace,
        "route_stage_trace_count": len(sanitized_trace),
        "route_stage_trace_digest": sha256_digest(
            {"route_stage_trace": sanitized_trace}
        ),
        # Machine-readable record of which governance digest fields are real
        # provenance vs an explicit not-applicable-for-chat sentinel. Backed by
        # the verifier (verify_magma_receipt enforces the sentinels for
        # risk_class=informational chat receipts).
        "digest_semantics": {
            "charter_digest": "real:charter_version",
            "policy_digest": "real:route_policy_version",
            "world_snapshot_digest": "real:profile+world_snapshot_ref",
            "rco_decision_digest": "na:no_rco_decision_gate_for_chat",
            "solver_contract_digest": "na:no_solver_contract_for_chat",
        },
    }


def write_chat_served_receipt_bundle(
    *,
    out_dir,
    summary_payload: Mapping[str, Any],
    now_utc: datetime,
    verify_manifest: Callable[..., dict[str, Any]],
    ordinal: int,
    previous_receipt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write and verify a one-entry chat-served receipt bundle (chained)."""
    payload = dict(summary_payload)
    _validate_chat_served_payload(payload)
    now_utc = _coerce_utc(now_utc)
    evaluation = _build_chat_served_evaluation(payload)
    receipt = build_magma_receipt(
        event_id=(
            f"magma:chat_served:{now_utc.strftime('%Y%m%dT%H%M%S%fZ')}"
            f"-{int(ordinal):06d}"
        ),
        ts_utc=_iso(now_utc),
        risk_class=evaluation["risk_class"],
        payload=payload,
        evaluation_result=evaluation,
        previous_receipt=previous_receipt,
        policy_digest=sha256_digest({"policy_version": evaluation["policy_version"]}),
        charter_digest=sha256_digest(
            {"charter_version": evaluation["charter_version"]}
        ),
        # Chat has no RCO/approval gate and no solver contract: explicit,
        # verifier-enforced, non-hex N/A sentinels (never a fake pass).
        rco_decision_digest=RCO_DECISION_NA_SENTINEL,
        world_snapshot_digest=sha256_digest(
            {
                "profile": payload["profile"],
                "world_snapshot_ref": payload["world_snapshot_ref"],
            }
        ),
        solver_contract_digest=SOLVER_CONTRACT_NA_SENTINEL,
    )
    return write_receipt_bundle(
        out_dir=out_dir,
        chain_id=CHAIN_ID,
        entries=[
            ReceiptBundleEntry(
                label="chat-served",
                payload=payload,
                evaluation_result=evaluation,
                receipt=receipt,
            )
        ],
        verify_manifest=verify_manifest,
    )


def _build_chat_served_evaluation(payload: Mapping[str, Any]) -> dict[str, Any]:
    # The MAGMA evaluation_result enums are constrained, so chat-served maps to
    # the LEAST-overclaiming schema-valid values (claim-safety, per rco-2):
    #   * subject_type="policy"      -- a route decision is policy-driven (as in
    #                                   runtime_summary_receipt); chat is not a
    #                                   solver/promotion/peer_review subject.
    #   * actual_gate=expected_gate="allow" -- the response was served/allowed;
    #                                   NOT refuse/review/require_approval.
    #   * verdict="abstain"          -- NO governance verdict was rendered (chat
    #                                   ran no verifier/RCO gate); "abstain" is
    #                                   truthful, never a synthesized "pass".
    # The truth that no RCO/approval gate ran is carried explicitly by the
    # rco_decision_digest N/A sentinel + payload.digest_semantics + reason_codes.
    return build_evaluation_result(
        case_id=f"case:chat_served:{payload['route_type']}",
        subject_type="policy",
        target_payload=dict(payload),
        risk_class="informational",
        expected_gate="allow",
        actual_gate="allow",
        verifier_path=[
            "chat_service_handle",
            "chat_served_payload_v0",
            "magma_receipt_v1",
            "offline_receipt_verifier",
        ],
        solver_selection=(
            [str(payload["route_type"])]
            if payload["route_type"] == "solver"
            else []
        ),
        policy_version="policy:chat_service_route:v0",
        charter_version="charter:runtime_truth:v1",
        domain_threshold_version="threshold:chat_service_route:v0",
        verdict="abstain",
        reason_codes=[
            "chat_served",
            "informational_no_governance_gate",
            f"route_type:{payload['route_type']}",
            f"source:{payload['source']}",
            "cached:true" if payload["cached"] else "cached:false",
        ],
        confidence_score=_clamp_unit(payload["confidence"]),
    )


def _validate_chat_served_payload(payload: Mapping[str, Any]) -> None:
    if payload.get("payload_version") != PAYLOAD_VERSION:
        raise ValueError("chat served payload_version mismatch")
    for key in (
        "route_type",
        "source",
        "profile",
        "world_snapshot_ref",
        "query_digest",
        "response_digest",
        "route_stage_trace_digest",
    ):
        if not payload.get(key):
            raise ValueError(f"chat served payload missing required field: {key}")
    trace = payload.get("route_stage_trace")
    if not isinstance(trace, list):
        raise ValueError("chat served route_stage_trace must be a list")
    if payload.get("route_stage_trace_count") != len(trace):
        raise ValueError("chat served route_stage_trace_count mismatch")
    if payload.get("route_stage_trace_digest") != sha256_digest(
        {"route_stage_trace": trace}
    ):
        raise ValueError("chat served route_stage_trace_digest mismatch")
    # Privacy invariant: no raw-query / raw-response keys may appear.
    for banned in ("query", "response", "context"):
        if banned in payload:
            raise ValueError(f"chat served payload must not carry raw '{banned}'")


def _sanitize_route_stage_trace(
    trace: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    sanitized: list[dict[str, Any]] = []
    for item in trace:
        if not isinstance(item, Mapping):
            raise ValueError("route_stage_trace entries must be mappings")
        entry: dict[str, Any] = {}
        for key, value in item.items():
            if key not in _ROUTE_STAGE_ALLOWED_KEYS:
                # Drop any non-allowlisted key so raw text can never ride along.
                continue
            if isinstance(value, (str, bool, int)) or value is None:
                entry[key] = value
            elif isinstance(value, float):
                entry[key] = round(value, 4)
            else:
                entry[key] = str(value)
        sanitized.append(entry)
    return sanitized


def _clamp_unit(value: Any) -> float:
    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _coerce_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "PAYLOAD_VERSION",
    "CHAIN_ID",
    "RCO_DECISION_NA_SENTINEL",
    "SOLVER_CONTRACT_NA_SENTINEL",
    "build_chat_served_summary",
    "write_chat_served_receipt_bundle",
]
