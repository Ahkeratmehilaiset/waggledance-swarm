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
  real-looking content digest would bake a *governance overclaim* into the
  receipt. So those two fields carry a fixed, well-known N/A sentinel: a
  ``sha256`` of an explicit not-applicable namespace. (MAGMA receipt v1 requires
  these fields to match ``^sha256:[a-f0-9]{64}$``, so the sentinel is hex, not a
  literal.) It is honest-by-construction and re-derivable: a consumer that knows
  the constant can positively identify it as N/A -- distinct from a real content
  digest, ``sha256("")`` and a zero-digest -- and it never reads as "this gate
  was evaluated and passed". The offline verifier independently re-derives and
  requires these exact sentinels for every chat-served payload.
* ``charter``/``policy``/``world_snapshot`` are REAL provenance digests
  (version/context binding) -- required for cross-verifiability, not fake.
* ``evaluation_result`` truthfully encodes the route decision (route_type /
  source / confidence); it never synthesizes a verification that did not run.
* The payload is privacy-safe: query/response are carried as digests only,
  never raw text.
"""
from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from waggledance.core.magma.canonical import sha256_digest
from waggledance.core.magma.chat_query_route_evidence import canonical_query_digest
from waggledance.core.magma.evaluation_result import build_evaluation_result
from waggledance.core.magma.receipt import build_magma_receipt
from waggledance.core.magma.receipt_bundle import (
    ReceiptBundleEntry,
    write_receipt_bundle,
)


PAYLOAD_VERSION = "magma.chat_served_receipt_payload.v1"
CHAIN_ID = "magma:chat_service:served:v0"

# Explicit "not-applicable for route_class=chat" sentinels for the RCO-decision
# and solver-contract digest fields. NOTE: MAGMA receipt v1 (enforced by
# receipt.py::_validate_magma_receipt) constrains these fields to
# ^sha256:[a-f0-9]{64}$, so the sentinel MUST be hex -- a non-hex literal is
# NOT schema-valid (contra an early design note). We therefore use a FIXED,
# WELL-KNOWN sha256 of a self-describing not-applicable namespace: it is
# schema-valid, and a specific known constant that a consumer which knows the
# constant can positively match to distinguish it from (i) a real content
# digest, (ii) sha256("") and (iii) a zero-digest. Because it is hex (opaque),
# a consumer must KNOW these constants to recognize them as N/A. Machine
# The verifier independently re-derives and requires these exact sentinels for
# chat/informational receipts, so a chat path cannot masquerade a governed
# rco_decision or solver_contract.
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

# Digest-shaped payload fields (query/response provenance) must match the same
# canonical sha256 shape the MAGMA receipt schema enforces, so a malformed or
# forged provenance digest can never enter the audit trail.
_SHA256_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_METADATA_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._/-]{0,127}$")

# Route trace strings are finite enums only. Arbitrary strings such as source,
# cell_id, or exception names are intentionally omitted even when the live
# trace carries them: they are not needed to prove the route and could contain
# caller-controlled text. Numeric fields are bounded and finite, and every
# stage has an exact field schema matching ChatService's live trace.
_ROUTE_STAGE_ORDER = (
    "language_detection",
    "hot_cache",
    "memory_context",
    "route_selection",
    "deterministic_solver",
    "hybrid_retrieval_8_cell",
    "hex_neighbor_assist_7_cell",
    "orchestrator_llm_fallback",
)
_ROUTE_STAGES = frozenset(_ROUTE_STAGE_ORDER)
_ROUTE_STAGE_INDEX = {
    stage: index for index, stage in enumerate(_ROUTE_STAGE_ORDER)
}
_ROUTE_TYPES = frozenset(
    {"hotcache", "memory", "micromodel", "solver", "llm", "swarm"}
)
_SOLVER_INTENTS = frozenset(
    {
        "math",
        "symbolic",
        "constraint",
        "seasonal",
        "stats",
        "thermal",
        "optimization",
        "retrieval",
        "causal",
        "anomaly",
        "chat",
        "v3_13_0_solver",
    }
)
_RETRIEVAL_MODES = frozenset(
    {
        "global_only",
        "hybrid:shadow",
        "hybrid:candidate",
        "hybrid:authoritative",
    }
)
_ROUTE_STAGE_FIELDS = {
    "language_detection": frozenset({"explicit_hint", "detected_language"}),
    "hot_cache": frozenset({"hit"}),
    "memory_context": frozenset({"limit", "result_count", "memory_score"}),
    "route_selection": frozenset(
        {"route_type", "solver_intent", "memory_score"}
    ),
    "deterministic_solver": frozenset({"intent", "answered"}),
    "hybrid_retrieval_8_cell": frozenset(
        {
            "enabled",
            "authoritative",
            "answered",
            "retrieval_mode",
            "hit_count",
        }
    ),
    "hex_neighbor_assist_7_cell": frozenset(
        {"enabled", "answered", "confidence"}
    ),
    "orchestrator_llm_fallback": frozenset(
        {"route_type", "confidence", "round_table_used"}
    ),
}
_ROUTE_BOOLEAN_FIELDS = frozenset(
    {
        "explicit_hint",
        "hit",
        "answered",
        "enabled",
        "authoritative",
        "round_table_used",
    }
)
_ROUTE_COUNT_FIELDS = frozenset({"limit", "result_count", "hit_count"})
_ROUTE_UNIT_FIELDS = frozenset({"memory_score", "confidence"})
_MAX_ROUTE_STAGE_EVENTS = len(_ROUTE_STAGE_ORDER)
_MAX_ROUTE_COUNT = 1_000_000

# The COMPLETE set of top-level keys a chat-served payload may carry. The
# persisted receipt payload is copied wholesale, so anything not on this
# allowlist is rejected -- otherwise a caller could smuggle a raw-content field
# (e.g. raw_query_text / prompt) into the audit trail past the digests-only
# privacy invariant. Keep in exact sync with build_chat_served_summary's return.
_ALLOWED_PAYLOAD_KEYS = frozenset(
    {
        "payload_version",
        "served_path",
        "route_type",
        "source",
        "confidence",
        "latency_ms",
        "cached",
        "round_table",
        "agent_id",
        "language",
        "profile",
        "world_snapshot_ref",
        "query_digest",
        "query_length",
        "response_digest",
        "response_length",
        "route_stage_trace",
        "route_stage_trace_count",
        "route_stage_trace_digest",
        "digest_semantics",
    }
)

# The FIXED digest_semantics declaration. The builder emits this exact dict
# deterministically (it depends on no input), so the validator requires an exact
# match -- digest_semantics is the one dict-valued allowlisted key, and pinning
# it closes the "raw content smuggled one level deeper" gap (a nested value like
# {"raw_leak": "..."} can never ride into the receipt). Single source of truth
# for both the builder and the validator.
_DIGEST_SEMANTICS = {
    "charter_digest": "real:charter_version",
    "policy_digest": "real:route_policy_version",
    "world_snapshot_digest": "real:profile+world_snapshot_ref",
    "rco_decision_digest": "na:no_rco_decision_gate_for_chat",
    "solver_contract_digest": "na:no_solver_contract_for_chat",
}


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
        # Clamp to [0,1] so the payload provenance never records an
        # out-of-range confidence (the evaluation clamps separately).
        "confidence": round(_clamp_unit(confidence), 4),
        "latency_ms": round(float(latency_ms), 2),
        "cached": bool(cached),
        "round_table": bool(round_table),
        "agent_id": str(agent_id) if agent_id is not None else None,
        "language": str(language),
        "profile": str(profile),
        "world_snapshot_ref": str(world_snapshot_ref),
        "query_digest": canonical_query_digest(str(query)),
        "query_length": len(str(query)),
        "response_digest": sha256_digest({"response": str(response)}),
        "response_length": len(str(response)),
        "route_stage_trace": sanitized_trace,
        "route_stage_trace_count": len(sanitized_trace),
        "route_stage_trace_digest": sha256_digest(
            {"route_stage_trace": sanitized_trace}
        ),
        # Machine-readable record of which governance digest fields are real
        # provenance vs an explicit not-applicable-for-chat sentinel. This is a
        # self-describing declaration emitted by the builder. The offline
        # verifier also requires the matching sentinels for informational chat
        # receipts.
        "digest_semantics": dict(_DIGEST_SEMANTICS),
    }


def build_chat_served_receipt(
    *,
    summary_payload: Mapping[str, Any],
    now_utc: datetime,
    ordinal: int,
    previous_receipt: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Validate the summary and build one chat-served MAGMA receipt.

    Returns ``(payload, evaluation_result, receipt)`` WITHOUT persisting a
    bundle. ``previous_receipt`` chains the receipt
    (``prev_receipt_hash = sha256_digest(previous_receipt)``); pass ``None`` for
    a genesis receipt. Use this to assemble a multi-entry chain;
    ``write_chat_served_receipt_bundle`` persists a single self-verified genesis
    bundle.
    """
    payload = dict(summary_payload)
    validate_chat_served_payload(payload)
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
        # honest-by-construction (schema-valid hex) N/A sentinels, never a fake
        # pass. The offline verifier independently enforces both sentinels.
        rco_decision_digest=RCO_DECISION_NA_SENTINEL,
        world_snapshot_digest=sha256_digest(
            {
                "profile": payload["profile"],
                "world_snapshot_ref": payload["world_snapshot_ref"],
            }
        ),
        solver_contract_digest=SOLVER_CONTRACT_NA_SENTINEL,
    )
    return payload, evaluation, receipt


def write_chat_served_receipt_bundle(
    *,
    out_dir,
    summary_payload: Mapping[str, Any],
    now_utc: datetime,
    verify_manifest: Callable[..., dict[str, Any]],
    ordinal: int,
) -> dict[str, Any]:
    """Write and self-verify a one-entry (genesis) chat-served receipt bundle.

    Each served response is persisted as its own self-contained genesis bundle.
    To link receipts into a chain, build them with
    ``build_chat_served_receipt(..., previous_receipt=...)`` and assemble a
    multi-entry manifest; a chained (non-genesis) receipt cannot be a standalone
    single-entry bundle (it would have no genesis). The built receipt is exposed
    in the return under ``"receipt"``.
    """
    payload, evaluation, receipt = build_chat_served_receipt(
        summary_payload=summary_payload,
        now_utc=now_utc,
        ordinal=ordinal,
    )
    bundle_report = write_receipt_bundle(
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
    return {**bundle_report, "receipt": receipt}


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
            "chat_served_payload_v1",
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


def validate_chat_served_payload(payload: Mapping[str, Any]) -> None:
    """Validate a persisted chat-served payload without trusting its emitter."""
    if payload.get("payload_version") != PAYLOAD_VERSION:
        raise ValueError("chat served payload_version mismatch")
    if payload.get("served_path") != "ChatService.handle":
        raise ValueError("chat served served_path mismatch")
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
    for key in ("route_type", "source", "language", "profile", "world_snapshot_ref"):
        _validate_metadata_token(payload, key)
    agent_id = payload.get("agent_id")
    if agent_id is not None:
        _validate_metadata_token(payload, "agent_id")
    for key in ("query_length", "response_length"):
        value = payload.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"chat served {key} must be a non-negative integer")
    latency_ms = payload.get("latency_ms")
    latency_value = _finite_number(latency_ms)
    if latency_value is None or latency_value < 0.0:
        raise ValueError("chat served latency_ms must be a finite non-negative number")
    for key in ("cached", "round_table"):
        if not isinstance(payload.get(key), bool):
            raise ValueError(f"chat served {key} must be a boolean")
    # Provenance digests must be well-formed sha256 (same shape MAGMA receipt
    # fields require), so a malformed/forged digest cannot enter the trail.
    for digest_key in (
        "query_digest",
        "response_digest",
        "route_stage_trace_digest",
    ):
        digest = payload.get(digest_key)
        if not isinstance(digest, str) or not _SHA256_DIGEST_RE.fullmatch(digest):
            raise ValueError(
                f"chat served {digest_key} must be a sha256 digest"
            )
    # Confidence provenance must be a real number in [0,1].
    confidence = payload.get("confidence")
    confidence_value = _finite_number(confidence)
    if confidence_value is None:
        raise ValueError("chat served confidence must be a number")
    if not 0.0 <= confidence_value <= 1.0:
        raise ValueError("chat served confidence must be finite and in [0,1]")
    trace = payload.get("route_stage_trace")
    if not isinstance(trace, list):
        raise ValueError("chat served route_stage_trace must be a list")
    # Re-sanitize in the validation path. The digest check below is only
    # SELF-CONSISTENCY (the public build path's caller supplies BOTH the trace
    # AND its digest), and _sanitize_route_stage_trace -- which actually enforces
    # the privacy invariant (allowlisted keys, scalar-only values) -- otherwise
    # runs ONLY in the producer build_chat_served_summary. So re-sanitize here
    # and reject any trace that is not already its sanitized form; otherwise raw
    # content can be smuggled into a trace entry through the direct build path
    # with a recomputed matching digest.
    sanitized_trace = _sanitize_route_stage_trace(trace)
    if not _route_stage_trace_matches_schema(trace, sanitized_trace):
        raise ValueError(
            "chat served route_stage_trace has non-allowlisted keys or "
            "non-canonical values (must be pre-sanitized before persistence)"
        )
    _validate_route_stage_sequence(sanitized_trace)
    trace_count = payload.get("route_stage_trace_count")
    if (
        not isinstance(trace_count, int)
        or isinstance(trace_count, bool)
        or trace_count != len(trace)
    ):
        raise ValueError("chat served route_stage_trace_count mismatch")
    if payload.get("route_stage_trace_digest") != sha256_digest(
        {"route_stage_trace": trace}
    ):
        raise ValueError("chat served route_stage_trace_digest mismatch")
    # Privacy invariant: the persisted payload is copied wholesale, so its
    # top-level keys must be EXACTLY the allowlist -- no extra field (raw-content
    # or otherwise) may ride along into the audit trail. Named raw keys keep a
    # specific message; any other unknown key is rejected fail-closed.
    for key in payload:
        if key in ("query", "response", "context"):
            raise ValueError(f"chat served payload must not carry raw '{key}'")
        if key not in _ALLOWED_PAYLOAD_KEYS:
            raise ValueError(
                f"chat served payload has unexpected top-level key '{key}' "
                "(only allowlisted keys may be persisted)"
            )
    missing_keys = _ALLOWED_PAYLOAD_KEYS.difference(payload)
    if missing_keys:
        raise ValueError("chat served payload must contain the exact v1 keyset")
    # digest_semantics is the one dict-valued allowlisted key; its nested values
    # are otherwise unvalidated, so pin it to the exact fixed declaration -- this
    # closes the "raw content smuggled one level deeper" gap (a nested value can
    # never ride into the receipt). The builder emits it deterministically.
    if payload.get("digest_semantics") != _DIGEST_SEMANTICS:
        raise ValueError(
            "chat served digest_semantics must equal the fixed declaration"
        )


def _sanitize_route_stage_trace(
    trace: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    if len(trace) > _MAX_ROUTE_STAGE_EVENTS:
        raise ValueError(
            f"route_stage_trace must contain at most {_MAX_ROUTE_STAGE_EVENTS} events"
        )
    sanitized: list[dict[str, Any]] = []
    for item in trace:
        if not isinstance(item, Mapping):
            raise ValueError("route_stage_trace entries must be mappings")
        stage = item.get("stage")
        if not isinstance(stage, str) or stage not in _ROUTE_STAGES:
            continue
        entry: dict[str, Any] = {"stage": stage}
        for key in _ROUTE_STAGE_FIELDS[stage]:
            if key not in item:
                continue
            normalized = _sanitize_route_stage_value(key, item[key])
            if normalized is not None:
                entry[key] = normalized
        sanitized.append(entry)
    return sanitized


def _sanitize_route_stage_value(key: str, value: Any) -> Any | None:
    if key in _ROUTE_BOOLEAN_FIELDS:
        return value if isinstance(value, bool) else None
    if key in _ROUTE_COUNT_FIELDS:
        if (
            isinstance(value, int)
            and not isinstance(value, bool)
            and 0 <= value <= _MAX_ROUTE_COUNT
        ):
            return value
        return None
    if key in _ROUTE_UNIT_FIELDS:
        number = _finite_number(value)
        if number is not None and 0.0 <= number <= 1.0:
            return round(number, 4)
        return None
    if key == "detected_language":
        return (
            value
            if isinstance(value, str) and value in {"en", "fi", "custom"}
            else None
        )
    if key == "route_type":
        return value if isinstance(value, str) and value in _ROUTE_TYPES else None
    if key in {"solver_intent", "intent"}:
        return value if isinstance(value, str) and value in _SOLVER_INTENTS else None
    if key == "retrieval_mode":
        return value if isinstance(value, str) and value in _RETRIEVAL_MODES else None
    return None


def _route_stage_trace_matches_schema(
    trace: Sequence[Mapping[str, Any]],
    sanitized: Sequence[Mapping[str, Any]],
) -> bool:
    if len(trace) != len(sanitized):
        return False
    for raw, clean in zip(trace, sanitized, strict=True):
        if set(raw) != set(clean):
            return False
        for key, clean_value in clean.items():
            raw_value = raw[key]
            if type(raw_value) is not type(clean_value) or raw_value != clean_value:
                return False
    return True


def _validate_route_stage_sequence(trace: Sequence[Mapping[str, Any]]) -> None:
    stages = [str(entry["stage"]) for entry in trace]
    if len(stages) != len(set(stages)):
        raise ValueError("chat served route_stage_trace stages must be unique")
    indexes = [_ROUTE_STAGE_INDEX[stage] for stage in stages]
    if indexes != sorted(indexes):
        raise ValueError("chat served route_stage_trace stages are out of order")


def _validate_metadata_token(payload: Mapping[str, Any], key: str) -> None:
    value = payload.get(key)
    if not isinstance(value, str) or not _METADATA_TOKEN_RE.fullmatch(value):
        raise ValueError(
            f"chat served {key} must be a compact metadata token"
        )


def _clamp_unit(value: Any) -> float:
    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _finite_number(value: Any) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


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
    "build_chat_served_receipt",
    "validate_chat_served_payload",
    "write_chat_served_receipt_bundle",
]
