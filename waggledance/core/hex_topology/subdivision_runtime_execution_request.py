# SPDX-License-Identifier: BUSL-1.1
"""Fail-closed execution request for shadow subdivision runtime handoff."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from waggledance.core.hex_topology.subdivision_runtime_commit import (
    SUBDIVISION_RUNTIME_COMMIT_APPLICATION_SCHEMA,
)
from waggledance.core.magma.canonical import sha256_digest


SUBDIVISION_RUNTIME_EXECUTION_REQUEST_SCHEMA = (
    "hex.subdivision_runtime_execution_request.v0"
)
SUBDIVISION_RUNTIME_EXECUTION_REQUEST_STATUS = (
    "subdivision_runtime_execution_request_ready"
)
SUBDIVISION_RUNTIME_EXECUTION_REQUEST_ACTION = (
    "request_runtime_subdivision_executor_review"
)
SUBDIVISION_RUNTIME_EXECUTION_REQUEST_NEXT_GATE = (
    "operator_verified_runtime_subdivision_executor_cutover"
)

_APPLICATION_DIGEST_EXCLUDED_KEYS = {
    "application_digest",
    "commit_candidate_topology",
    "commit_envelope",
    "runtime_rehearsal",
}
_ENVELOPE_DIGEST_EXCLUDED_KEYS = {
    "envelope_digest",
    "operator_signature",
    "canary_policy",
    "rollback_policy",
    "subdivision_activation_preflight",
}
_REHEARSAL_DIGEST_EXCLUDED_KEYS = {
    "rehearsal_digest",
    "candidate_topology",
    "subdivision_activation_preflight",
}
_RUNTIME_FALSE_FLAGS = (
    "runtime_authority_granted",
    "runtime_topology_mutation_applied",
    "routing_influence_applied",
    "transport_performed",
    "claim_safe_upgrade",
    "runtime_commit_performed",
)
_REQUEST_FORBIDDEN_TRUE_FLAGS = (
    "operator_approval",
    "live_runtime_execution_authorized",
    "live_runtime_commit_authorized",
    "runtime_authority_granted",
    "runtime_topology_mutation_applied",
    "routing_influence_applied",
    "transport_performed",
    "claim_safe_upgrade",
    "runtime_commit_performed",
    "runtime_executor_invoked",
)


def build_subdivision_runtime_execution_request(
    *,
    runtime_application: Mapping[str, Any],
    request_metadata: Mapping[str, Any],
) -> dict[str, Any]:
    """Prepare a gated executor request without invoking the executor.

    This artifact is the handoff after a reviewed runtime commit application.
    It proves the application/evidence chain is internally consistent and that
    the request metadata asks for executor review only. It does not authorize or
    perform a live runtime commit, mutate topology, influence routing, or send
    transport.
    """
    embedded_envelope = _mapping_as_dict(
        runtime_application.get("commit_envelope")
    )
    embedded_rehearsal = _mapping_as_dict(
        runtime_application.get("runtime_rehearsal")
    )
    candidate_topology = _mapping_as_dict(
        runtime_application.get("commit_candidate_topology")
    )
    application_digest = runtime_application.get("application_digest")
    request_metadata_digest = (
        sha256_digest(request_metadata)
        if isinstance(request_metadata, Mapping)
        else None
    )
    candidate_digest = (
        sha256_digest(candidate_topology) if candidate_topology else None
    )
    child_ids = list(runtime_application.get("new_child_cell_ids") or [])

    guardrails = {
        "runtime_application_schema_current": (
            runtime_application.get("schema_version")
            == SUBDIVISION_RUNTIME_COMMIT_APPLICATION_SCHEMA
        ),
        "runtime_application_status_ready": (
            runtime_application.get("application_status")
            == "subdivision_runtime_commit_application_ready"
        ),
        "runtime_application_ok": runtime_application.get("ok") is True,
        "runtime_application_has_no_blockers": (
            runtime_application.get("blockers") == []
        ),
        "runtime_application_digest_rederives": _digest_matches(
            runtime_application,
            digest_key="application_digest",
            excluded_keys=_APPLICATION_DIGEST_EXCLUDED_KEYS,
        ),
        "runtime_application_guardrails_all_true": _guardrails_all_true(
            runtime_application.get("guardrails")
        ),
        "runtime_application_candidate_prepared": (
            runtime_application.get("commit_candidate_prepared") is True
        ),
        "runtime_application_live_authority_false": (
            runtime_application.get("live_runtime_commit_authorized") is False
        ),
        "runtime_application_runtime_flags_false": _runtime_flags_false(
            runtime_application
        ),
        "commit_envelope_embedded": bool(embedded_envelope),
        "commit_envelope_digest_rederives": _digest_matches(
            embedded_envelope,
            digest_key="envelope_digest",
            excluded_keys=_ENVELOPE_DIGEST_EXCLUDED_KEYS,
        ),
        "runtime_rehearsal_embedded": bool(embedded_rehearsal),
        "runtime_rehearsal_digest_rederives": _digest_matches(
            embedded_rehearsal,
            digest_key="rehearsal_digest",
            excluded_keys=_REHEARSAL_DIGEST_EXCLUDED_KEYS,
        ),
        "application_envelope_digest_matches_embedded": (
            runtime_application.get("commit_envelope_digest")
            == embedded_envelope.get("envelope_digest")
        ),
        "application_rehearsal_digest_matches_embedded": (
            runtime_application.get("runtime_rehearsal_digest")
            == embedded_rehearsal.get("rehearsal_digest")
        ),
        "application_candidate_digest_rederives": (
            candidate_digest is not None
            and candidate_digest
            == runtime_application.get("commit_candidate_topology_digest")
        ),
        "application_candidate_digest_matches_rehearsal": (
            candidate_digest is not None
            and candidate_digest
            == embedded_rehearsal.get("candidate_topology_digest")
        ),
        "application_source_topology_unchanged": (
            runtime_application.get("source_topology_digest_before")
            == runtime_application.get("source_topology_digest_after")
        ),
        "evidence_plan_id_matches": (
            runtime_application.get("plan_id")
            == embedded_envelope.get("plan_id")
            == embedded_rehearsal.get("plan_id")
        ),
        "evidence_parent_matches": (
            runtime_application.get("parent_cell_id")
            == embedded_envelope.get("parent_cell_id")
            == embedded_rehearsal.get("parent_cell_id")
        ),
        "evidence_children_match": (
            child_ids
            == list(embedded_envelope.get("new_child_cell_ids") or [])
            == list(embedded_rehearsal.get("new_child_cell_ids") or [])
        ),
        "evidence_preflight_digest_matches": (
            runtime_application.get("subdivision_preflight_digest")
            == embedded_envelope.get("subdivision_preflight_digest")
            == embedded_rehearsal.get("subdivision_preflight_digest")
        ),
        "post_merge_canary_required": (
            runtime_application.get("post_merge_canary_required") is True
        ),
        "auto_rollback_eligibility_required": (
            runtime_application.get("auto_rollback_eligibility_required")
            is True
        ),
        "request_metadata_present": isinstance(request_metadata, Mapping),
        "request_action_matches": (
            request_metadata.get("requested_action")
            == SUBDIVISION_RUNTIME_EXECUTION_REQUEST_ACTION
        ),
        "request_application_digest_matches": (
            isinstance(application_digest, str)
            and request_metadata.get("application_digest")
            == application_digest
        ),
        "request_plan_id_matches": (
            request_metadata.get("plan_id") == runtime_application.get("plan_id")
        ),
        "requester_identity_present": _non_empty_field(
            request_metadata,
            "requested_by",
        ),
        "request_timestamp_utc": _timestamp_utc(
            request_metadata.get("requested_at_utc")
        ),
        "request_contains_no_operator_approval": (
            request_metadata.get("operator_approval") is not True
        ),
        "request_contains_no_runtime_claim": (
            _contains_no_forbidden_true_flags(request_metadata)
        ),
        "no_live_runtime_execution_authorized": True,
        "no_runtime_executor_invoked": True,
        "no_live_runtime_commit_authorized": True,
        "no_live_runtime_mutation_performed": True,
        "no_routing_influence_applied": True,
        "no_transport_performed": True,
        "no_claim_safe_upgrade": True,
    }
    blockers = [
        name for name, satisfied in guardrails.items()
        if satisfied is not True
    ]
    ready = not blockers
    core = {
        "schema_version": SUBDIVISION_RUNTIME_EXECUTION_REQUEST_SCHEMA,
        "execution_request_status": (
            SUBDIVISION_RUNTIME_EXECUTION_REQUEST_STATUS
        ),
        "ok": ready,
        "blockers": blockers,
        "requested_action": SUBDIVISION_RUNTIME_EXECUTION_REQUEST_ACTION,
        "required_next_gate": SUBDIVISION_RUNTIME_EXECUTION_REQUEST_NEXT_GATE,
        "plan_id": runtime_application.get("plan_id"),
        "parent_cell_id": runtime_application.get("parent_cell_id"),
        "new_child_cell_ids": child_ids,
        "target_state": runtime_application.get("target_state"),
        "subdivision_preflight_digest": runtime_application.get(
            "subdivision_preflight_digest"
        ),
        "runtime_application_digest": application_digest,
        "commit_envelope_digest": runtime_application.get(
            "commit_envelope_digest"
        ),
        "runtime_rehearsal_digest": runtime_application.get(
            "runtime_rehearsal_digest"
        ),
        "operator_signature_digest": runtime_application.get(
            "operator_signature_digest"
        ),
        "commit_candidate_topology_digest": runtime_application.get(
            "commit_candidate_topology_digest"
        ),
        "request_metadata_digest": request_metadata_digest,
        "ready_for_runtime_executor_handoff": ready,
        "live_runtime_execution_authorized": False,
        "live_runtime_commit_authorized": False,
        "runtime_authority_granted": False,
        "runtime_topology_mutation_applied": False,
        "routing_influence_applied": False,
        "transport_performed": False,
        "claim_safe_upgrade": False,
        "runtime_commit_performed": False,
        "runtime_executor_invoked": False,
        "post_merge_canary_required": (
            runtime_application.get("post_merge_canary_required") is True
        ),
        "auto_rollback_eligibility_required": (
            runtime_application.get("auto_rollback_eligibility_required")
            is True
        ),
        "guardrails": guardrails,
    }
    return {
        **core,
        "execution_request_digest": sha256_digest(core),
        "runtime_application": dict(runtime_application),
        "request_metadata": dict(request_metadata),
    }


def _digest_matches(
    document: Mapping[str, Any],
    *,
    digest_key: str,
    excluded_keys: set[str],
) -> bool:
    digest = document.get(digest_key)
    if not isinstance(digest, str) or not digest:
        return False
    core = {
        key: value for key, value in document.items()
        if key not in excluded_keys
    }
    return sha256_digest(core) == digest


def _guardrails_all_true(value: Any) -> bool:
    return isinstance(value, Mapping) and bool(value) and all(
        item is True for item in value.values()
    )


def _runtime_flags_false(document: Mapping[str, Any]) -> bool:
    return all(document.get(flag) is False for flag in _RUNTIME_FALSE_FLAGS)


def _contains_no_forbidden_true_flags(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    return all(value.get(flag) is not True for flag in _REQUEST_FORBIDDEN_TRUE_FLAGS)


def _non_empty_field(value: Any, key: str) -> bool:
    return (
        isinstance(value, Mapping)
        and isinstance(value.get(key), str)
        and bool(value[key].strip())
    )


def _timestamp_utc(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return False
    return (
        parsed.tzinfo is not None
        and parsed.utcoffset() == timezone.utc.utcoffset(parsed)
    )


def _mapping_as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}
