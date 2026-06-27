# SPDX-License-Identifier: BUSL-1.1
"""Fail-closed executor admission dry-run for subdivision runtime handoff."""
from __future__ import annotations

from typing import Any, Mapping

from waggledance.core.hex_topology.subdivision_runtime_execution_request import (
    SUBDIVISION_RUNTIME_EXECUTION_REQUEST_ACTION,
    SUBDIVISION_RUNTIME_EXECUTION_REQUEST_NEXT_GATE,
    SUBDIVISION_RUNTIME_EXECUTION_REQUEST_SCHEMA,
)
from waggledance.core.magma.canonical import sha256_digest


SUBDIVISION_RUNTIME_EXECUTOR_ADMISSION_SCHEMA = (
    "hex.subdivision_runtime_executor_admission.v0"
)
SUBDIVISION_RUNTIME_EXECUTOR_ADMISSION_STATUS = (
    "subdivision_runtime_executor_admission_blocked"
)
SUBDIVISION_RUNTIME_EXECUTOR_ADMISSION_DECISION = (
    "blocked_pending_operator_verified_cutover"
)
SUBDIVISION_RUNTIME_EXECUTOR_ADMISSION_BLOCKER = (
    "operator_verified_runtime_subdivision_executor_cutover_missing"
)

_REQUEST_DIGEST_EXCLUDED_KEYS = {
    "execution_request_digest",
    "runtime_application",
    "request_metadata",
}
_APPLICATION_DIGEST_EXCLUDED_KEYS = {
    "application_digest",
    "commit_candidate_topology",
    "commit_envelope",
    "runtime_rehearsal",
}
_RUNTIME_FALSE_FLAGS = (
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
_REQUEST_METADATA_FORBIDDEN_TRUE_FLAGS = (
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


def build_subdivision_runtime_executor_admission(
    *,
    execution_request: Mapping[str, Any],
    cutover_authorization: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Dry-run executor admission while keeping live cutover blocked.

    The artifact consumes a reviewed execution request and verifies that the
    evidence chain is intact. It deliberately refuses to admit the executor:
    the separate operator-verified cutover gate must remain outside this dry
    run. Passing a cutover authorization mapping is treated as a blocker, not
    as permission to perform live runtime work.
    """
    runtime_application = _mapping_as_dict(
        execution_request.get("runtime_application")
    )
    request_metadata = _mapping_as_dict(
        execution_request.get("request_metadata")
    )
    request_digest = execution_request.get("execution_request_digest")
    application_digest = runtime_application.get("application_digest")
    request_metadata_digest = (
        sha256_digest(request_metadata) if request_metadata else None
    )
    cutover_digest = (
        sha256_digest(cutover_authorization)
        if isinstance(cutover_authorization, Mapping)
        else None
    )

    guardrails = {
        "execution_request_schema_current": (
            execution_request.get("schema_version")
            == SUBDIVISION_RUNTIME_EXECUTION_REQUEST_SCHEMA
        ),
        "execution_request_status_ready": (
            execution_request.get("execution_request_status")
            == "subdivision_runtime_execution_request_ready"
        ),
        "execution_request_ok": execution_request.get("ok") is True,
        "execution_request_has_no_blockers": (
            execution_request.get("blockers") == []
        ),
        "execution_request_digest_rederives": _digest_matches(
            execution_request,
            digest_key="execution_request_digest",
            excluded_keys=_REQUEST_DIGEST_EXCLUDED_KEYS,
        ),
        "execution_request_guardrails_all_true": _guardrails_all_true(
            execution_request.get("guardrails")
        ),
        "execution_request_ready_for_handoff": (
            execution_request.get("ready_for_runtime_executor_handoff")
            is True
        ),
        "execution_request_action_is_review_only": (
            execution_request.get("requested_action")
            == SUBDIVISION_RUNTIME_EXECUTION_REQUEST_ACTION
        ),
        "execution_request_next_gate_matches_cutover": (
            execution_request.get("required_next_gate")
            == SUBDIVISION_RUNTIME_EXECUTION_REQUEST_NEXT_GATE
        ),
        "execution_request_runtime_flags_false": _runtime_flags_false(
            execution_request
        ),
        "runtime_application_embedded": bool(runtime_application),
        "runtime_application_digest_rederives": _digest_matches(
            runtime_application,
            digest_key="application_digest",
            excluded_keys=_APPLICATION_DIGEST_EXCLUDED_KEYS,
        ),
        "request_application_digest_matches_embedded": (
            execution_request.get("runtime_application_digest")
            == application_digest
        ),
        "request_metadata_embedded": bool(request_metadata),
        "request_metadata_digest_rederives": (
            request_metadata_digest is not None
            and request_metadata_digest
            == execution_request.get("request_metadata_digest")
        ),
        "request_metadata_no_operator_approval": (
            request_metadata.get("operator_approval") is not True
        ),
        "request_metadata_no_runtime_claim": (
            _contains_no_forbidden_true_flags(request_metadata)
        ),
        "cutover_authorization_absent": not isinstance(
            cutover_authorization,
            Mapping,
        ),
        "admission_remains_blocked_without_operator_cutover": True,
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
    ok = not blockers
    admission_blockers = [
        SUBDIVISION_RUNTIME_EXECUTOR_ADMISSION_BLOCKER
    ]
    core = {
        "schema_version": SUBDIVISION_RUNTIME_EXECUTOR_ADMISSION_SCHEMA,
        "executor_admission_status": (
            SUBDIVISION_RUNTIME_EXECUTOR_ADMISSION_STATUS
        ),
        "admission_decision": SUBDIVISION_RUNTIME_EXECUTOR_ADMISSION_DECISION,
        "ok": ok,
        "blockers": blockers,
        "admission_blockers": admission_blockers,
        "required_next_gate": SUBDIVISION_RUNTIME_EXECUTION_REQUEST_NEXT_GATE,
        "requested_action": execution_request.get("requested_action"),
        "plan_id": execution_request.get("plan_id"),
        "parent_cell_id": execution_request.get("parent_cell_id"),
        "new_child_cell_ids": list(
            execution_request.get("new_child_cell_ids") or []
        ),
        "target_state": execution_request.get("target_state"),
        "subdivision_preflight_digest": execution_request.get(
            "subdivision_preflight_digest"
        ),
        "runtime_execution_request_digest": request_digest,
        "runtime_application_digest": execution_request.get(
            "runtime_application_digest"
        ),
        "commit_envelope_digest": execution_request.get(
            "commit_envelope_digest"
        ),
        "runtime_rehearsal_digest": execution_request.get(
            "runtime_rehearsal_digest"
        ),
        "operator_signature_digest": execution_request.get(
            "operator_signature_digest"
        ),
        "commit_candidate_topology_digest": execution_request.get(
            "commit_candidate_topology_digest"
        ),
        "request_metadata_digest": execution_request.get(
            "request_metadata_digest"
        ),
        "cutover_authorization_digest": cutover_digest,
        "ready_for_runtime_executor_admission": False,
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
            execution_request.get("post_merge_canary_required") is True
        ),
        "auto_rollback_eligibility_required": (
            execution_request.get("auto_rollback_eligibility_required") is True
        ),
        "guardrails": guardrails,
    }
    return {
        **core,
        "executor_admission_digest": sha256_digest(core),
        "execution_request": dict(execution_request),
        "cutover_authorization": (
            dict(cutover_authorization)
            if isinstance(cutover_authorization, Mapping)
            else None
        ),
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
    return all(
        value.get(flag) is not True
        for flag in _REQUEST_METADATA_FORBIDDEN_TRUE_FLAGS
    )


def _mapping_as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}
