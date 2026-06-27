# SPDX-License-Identifier: BUSL-1.1
"""Dormant runtime-commit envelope for shadow subdivision activation."""
from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any, Mapping

from waggledance.core.hex_topology.subdivision_preflight import (
    SUBDIVISION_ACTIVATION_PREFLIGHT_NEXT_GATE,
    SUBDIVISION_ACTIVATION_PREFLIGHT_SCHEMA,
)
from waggledance.core.magma.canonical import sha256_digest


SUBDIVISION_RUNTIME_COMMIT_ENVELOPE_SCHEMA = (
    "hex.subdivision_runtime_commit_envelope.v0"
)
SUBDIVISION_RUNTIME_COMMIT_ENVELOPE_STATUS = (
    "subdivision_runtime_commit_envelope_ready"
)
SUBDIVISION_RUNTIME_COMMIT_ACTION = (
    "operator_signed_runtime_subdivision_commit"
)

CANARY_SIGNAL_KIND = "p4b_confirmed_regress"
MIN_CANARY_CONFIRMATIONS = 2
MAX_CANARY_FP_THRESHOLD = 0.01

_PREFLIGHT_EMBEDDED_KEYS = {
    "preflight_digest",
    "shadow_activation_packet",
    "ring_delivery_observability",
    "canary_mirror_report",
}

_FORBIDDEN_TRUE_SIGNATURE_FLAGS = (
    "runtime_authority_granted",
    "runtime_topology_mutation_applied",
    "routing_influence_applied",
    "transport_performed",
    "claim_safe_upgrade",
    "runtime_commit_performed",
)


def build_subdivision_runtime_commit_envelope(
    *,
    preflight: Mapping[str, Any],
    operator_signature: Mapping[str, Any] | None = None,
    canary_policy: Mapping[str, Any] | None = None,
    rollback_policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Bind subdivision preflight evidence to the runtime commit gate.

    The envelope is deliberately dormant: it can prove that the next executor
    gate is satisfied, but it never applies the topology mutation, sends
    transport, or claims the subdivision is live-safe.
    """
    canary = _normalize_canary_policy(canary_policy)
    rollback = _normalize_rollback_policy(rollback_policy)

    guardrails = {
        "preflight_schema_current": (
            preflight.get("schema_version")
            == SUBDIVISION_ACTIVATION_PREFLIGHT_SCHEMA
        ),
        "preflight_ok": preflight.get("ok") is True,
        "preflight_has_no_blockers": preflight.get("blockers") == [],
        "preflight_required_next_gate_matches": (
            preflight.get("required_next_gate")
            == SUBDIVISION_ACTIVATION_PREFLIGHT_NEXT_GATE
            == SUBDIVISION_RUNTIME_COMMIT_ACTION
        ),
        "preflight_digest_rederives": _preflight_digest_matches(preflight),
        "shadow_packet_digest_rederives": _embedded_digest_matches(
            preflight,
            digest_key="shadow_activation_packet_digest",
            embedded_key="shadow_activation_packet",
        ),
        "ring_observability_digest_rederives": _embedded_digest_matches(
            preflight,
            digest_key="ring_delivery_observability_digest",
            embedded_key="ring_delivery_observability",
        ),
        "canary_report_digest_rederives": _canary_digest_matches(preflight),
        "preflight_guardrails_all_true": _guardrails_all_true(
            preflight.get("guardrails")
        ),
        "preflight_runtime_authority_false": (
            preflight.get("runtime_authority_granted") is False
        ),
        "preflight_runtime_topology_mutation_false": (
            preflight.get("runtime_topology_mutation_applied") is False
        ),
        "preflight_routing_influence_false": (
            preflight.get("routing_influence_applied") is False
        ),
        "preflight_transport_false": (
            preflight.get("transport_performed") is False
        ),
        "preflight_claim_safe_upgrade_false": (
            preflight.get("claim_safe_upgrade") is False
        ),
        "operator_signature_present": isinstance(
            operator_signature, Mapping
        ),
        "operator_signature_action_matches": _signature_field_matches(
            operator_signature, "action", SUBDIVISION_RUNTIME_COMMIT_ACTION
        ),
        "operator_signature_plan_matches": _signature_field_matches(
            operator_signature, "plan_id", preflight.get("plan_id")
        ),
        "operator_signature_preflight_digest_matches": (
            _signature_field_matches(
                operator_signature,
                "preflight_digest",
                preflight.get("preflight_digest"),
            )
        ),
        "operator_signature_identity_present": _non_empty_signature_field(
            operator_signature, "signed_by"
        ),
        "operator_signature_timestamp_utc": _signature_timestamp_utc(
            operator_signature
        ),
        "operator_signature_contains_no_runtime_mutation_claim": (
            _signature_has_no_forbidden_true_flags(operator_signature)
        ),
        "operator_signature_not_fixture": (
            not isinstance(operator_signature, Mapping)
            or operator_signature.get("fixture_only") is not True
        ),
        "post_merge_canary_policy_bound": (
            canary["post_merge_canary_required"] is True
            and canary["signal_kind"] == CANARY_SIGNAL_KIND
            and canary["min_confirmations"] >= MIN_CANARY_CONFIRMATIONS
            and canary["fp_threshold"] <= MAX_CANARY_FP_THRESHOLD
        ),
        "auto_rollback_policy_bound": (
            rollback["auto_rollback_eligibility_required"] is True
            and rollback["target_must_be_known_green_consensus"] is True
            and rollback["result_tree_must_equal_target_tree"] is True
            and rollback["failure_signal_must_be_debounced"] is True
            and rollback["operator_escalate_on_uncertainty"] is True
            and rollback["forbidden_surfaces_blocked"] is True
        ),
        "no_runtime_commit_performed": True,
    }
    blockers = [
        name for name, satisfied in guardrails.items()
        if satisfied is not True
    ]
    ready_for_executor = not blockers
    signature_digest = (
        sha256_digest(operator_signature)
        if isinstance(operator_signature, Mapping)
        else None
    )

    core = {
        "schema_version": SUBDIVISION_RUNTIME_COMMIT_ENVELOPE_SCHEMA,
        "envelope_status": SUBDIVISION_RUNTIME_COMMIT_ENVELOPE_STATUS,
        "ok": ready_for_executor,
        "blockers": blockers,
        "required_operator_action": SUBDIVISION_RUNTIME_COMMIT_ACTION,
        "plan_id": preflight.get("plan_id"),
        "parent_cell_id": preflight.get("parent_cell_id"),
        "new_child_cell_ids": list(preflight.get("new_child_cell_ids") or []),
        "target_state": preflight.get("target_state"),
        "subdivision_preflight_digest": preflight.get("preflight_digest"),
        "operator_signature_digest": signature_digest,
        "ready_for_runtime_commit_executor": ready_for_executor,
        "post_merge_canary_required": (
            canary["post_merge_canary_required"] is True
        ),
        "canary_signal_kind": canary["signal_kind"],
        "canary_min_confirmations": canary["min_confirmations"],
        "canary_fp_threshold": canary["fp_threshold"],
        "auto_rollback_eligibility_required": (
            rollback["auto_rollback_eligibility_required"] is True
        ),
        "operator_escalate_on_uncertainty": (
            rollback["operator_escalate_on_uncertainty"] is True
        ),
        "runtime_authority_granted": False,
        "runtime_topology_mutation_applied": False,
        "routing_influence_applied": False,
        "transport_performed": False,
        "claim_safe_upgrade": False,
        "runtime_commit_performed": False,
        "guardrails": guardrails,
    }
    return {
        **core,
        "envelope_digest": sha256_digest(core),
        "operator_signature": (
            dict(operator_signature)
            if isinstance(operator_signature, Mapping)
            else None
        ),
        "canary_policy": canary,
        "rollback_policy": rollback,
        "subdivision_activation_preflight": dict(preflight),
    }


def _normalize_canary_policy(
    policy: Mapping[str, Any] | None,
) -> dict[str, Any]:
    raw = dict(policy or {})
    min_confirmations = raw.get(
        "min_confirmations", MIN_CANARY_CONFIRMATIONS
    )
    if not isinstance(min_confirmations, int) or isinstance(
        min_confirmations, bool
    ):
        min_confirmations = 0

    fp_threshold = raw.get("fp_threshold", MAX_CANARY_FP_THRESHOLD)
    if (
        not isinstance(fp_threshold, (int, float))
        or isinstance(fp_threshold, bool)
        or not math.isfinite(float(fp_threshold))
        or float(fp_threshold) < 0.0
    ):
        fp_threshold = 1.0

    return {
        "post_merge_canary_required": raw.get(
            "post_merge_canary_required", True
        ),
        "signal_kind": raw.get("signal_kind", CANARY_SIGNAL_KIND),
        "min_confirmations": min_confirmations,
        "fp_threshold": float(fp_threshold),
    }


def _normalize_rollback_policy(
    policy: Mapping[str, Any] | None,
) -> dict[str, Any]:
    raw = dict(policy or {})
    return {
        "auto_rollback_eligibility_required": raw.get(
            "auto_rollback_eligibility_required", True
        ),
        "target_must_be_known_green_consensus": raw.get(
            "target_must_be_known_green_consensus", True
        ),
        "result_tree_must_equal_target_tree": raw.get(
            "result_tree_must_equal_target_tree", True
        ),
        "failure_signal_must_be_debounced": raw.get(
            "failure_signal_must_be_debounced", True
        ),
        "operator_escalate_on_uncertainty": raw.get(
            "operator_escalate_on_uncertainty", True
        ),
        "forbidden_surfaces_blocked": raw.get(
            "forbidden_surfaces_blocked", True
        ),
    }


def _preflight_digest_matches(preflight: Mapping[str, Any]) -> bool:
    digest = preflight.get("preflight_digest")
    if not isinstance(digest, str) or not digest:
        return False
    core = {
        key: value for key, value in preflight.items()
        if key not in _PREFLIGHT_EMBEDDED_KEYS
    }
    return sha256_digest(core) == digest


def _embedded_digest_matches(
    preflight: Mapping[str, Any],
    *,
    digest_key: str,
    embedded_key: str,
) -> bool:
    digest = preflight.get(digest_key)
    embedded = preflight.get(embedded_key)
    if not isinstance(digest, str) or not isinstance(embedded, Mapping):
        return False
    return sha256_digest(embedded) == digest


def _canary_digest_matches(preflight: Mapping[str, Any]) -> bool:
    digest = preflight.get("canary_mirror_report_digest")
    report = preflight.get("canary_mirror_report")
    if not isinstance(digest, str) or not isinstance(report, Mapping):
        return False
    return report.get("canonical_digest") == digest


def _guardrails_all_true(value: Any) -> bool:
    return isinstance(value, Mapping) and bool(value) and all(
        item is True for item in value.values()
    )


def _signature_field_matches(
    signature: Mapping[str, Any] | None,
    key: str,
    expected: Any,
) -> bool:
    return isinstance(signature, Mapping) and signature.get(key) == expected


def _non_empty_signature_field(
    signature: Mapping[str, Any] | None,
    key: str,
) -> bool:
    return (
        isinstance(signature, Mapping)
        and isinstance(signature.get(key), str)
        and bool(signature[key].strip())
    )


def _signature_timestamp_utc(
    signature: Mapping[str, Any] | None,
) -> bool:
    if not _non_empty_signature_field(signature, "signed_at_utc"):
        return False
    value = str(signature["signed_at_utc"]).strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return (
        parsed.tzinfo is not None
        and parsed.utcoffset() == timezone.utc.utcoffset(parsed)
    )


def _signature_has_no_forbidden_true_flags(
    signature: Mapping[str, Any] | None,
) -> bool:
    if not isinstance(signature, Mapping):
        return False
    return all(
        signature.get(flag) is not True
        for flag in _FORBIDDEN_TRUE_SIGNATURE_FLAGS
    )
