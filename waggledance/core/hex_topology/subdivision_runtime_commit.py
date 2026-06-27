# SPDX-License-Identifier: BUSL-1.1
"""Pure runtime-commit candidate builder for shadow subdivision activation."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from waggledance.core.hex_topology.subdivision_commit import (
    SUBDIVISION_RUNTIME_COMMIT_ACTION,
    SUBDIVISION_RUNTIME_COMMIT_ENVELOPE_SCHEMA,
    build_subdivision_runtime_commit_envelope,
)
from waggledance.core.hex_topology.subdivision_operator import (
    SubdivisionPlan,
    apply_plan_to_topology,
    compute_plan_id,
)
from waggledance.core.hex_topology.subdivision_rehearsal import (
    SUBDIVISION_RUNTIME_REHEARSAL_SCHEMA,
)
from waggledance.core.magma.canonical import sha256_digest


SUBDIVISION_RUNTIME_COMMIT_APPLICATION_SCHEMA = (
    "hex.subdivision_runtime_commit_application.v0"
)
SUBDIVISION_RUNTIME_COMMIT_APPLICATION_STATUS = (
    "subdivision_runtime_commit_application_ready"
)

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


def build_subdivision_runtime_commit_application(
    *,
    topology: Mapping[str, Any],
    commit_envelope: Mapping[str, Any],
    runtime_rehearsal: Mapping[str, Any],
) -> dict[str, Any]:
    """Prepare a commit candidate from reviewed evidence without live mutation.

    This is intentionally a pure artifact builder. It replays the subdivision
    plan against a copy of the provided topology, compares the result to the
    reviewed rehearsal candidate, and returns a receipt. It does not mutate the
    caller's topology, grant runtime authority, perform transport, or mark the
    subdivision live-safe.
    """
    source_digest_before = sha256_digest(topology)
    envelope_preflight = commit_envelope.get("subdivision_activation_preflight")
    signature = commit_envelope.get("operator_signature")
    rebuilt_envelope = (
        build_subdivision_runtime_commit_envelope(
            preflight=envelope_preflight,
            operator_signature=signature,
            canary_policy=commit_envelope.get("canary_policy"),
            rollback_policy=commit_envelope.get("rollback_policy"),
        )
        if isinstance(envelope_preflight, Mapping)
        else {}
    )
    plan, plan_buildable = _plan_from_evidence(commit_envelope)
    candidate_topology: dict[str, Any] = {}
    build_error = ""
    if plan is not None:
        try:
            candidate_topology = apply_plan_to_topology(
                deepcopy(dict(topology)),
                plan,
            )
        except ValueError as exc:
            build_error = str(exc)

    source_digest_after = sha256_digest(topology)
    rehearsal_candidate = _mapping_as_dict(
        runtime_rehearsal.get("candidate_topology")
    )
    candidate_digest = (
        sha256_digest(candidate_topology) if candidate_topology else None
    )
    rehearsal_candidate_digest = (
        sha256_digest(rehearsal_candidate) if rehearsal_candidate else None
    )
    envelope_preflight_digest = commit_envelope.get(
        "subdivision_preflight_digest"
    )
    rehearsal_preflight_digest = runtime_rehearsal.get(
        "subdivision_preflight_digest"
    )

    guardrails = {
        "commit_envelope_schema_current": (
            commit_envelope.get("schema_version")
            == SUBDIVISION_RUNTIME_COMMIT_ENVELOPE_SCHEMA
        ),
        "commit_envelope_ok": commit_envelope.get("ok") is True,
        "commit_envelope_ready_for_executor": (
            commit_envelope.get("ready_for_runtime_commit_executor")
            is True
        ),
        "commit_envelope_digest_rederives": _digest_matches(
            commit_envelope,
            digest_key="envelope_digest",
            excluded_keys=_ENVELOPE_DIGEST_EXCLUDED_KEYS,
        ),
        "commit_envelope_rebuilds_from_embedded_preflight": (
            bool(rebuilt_envelope)
            and rebuilt_envelope.get("envelope_digest")
            == commit_envelope.get("envelope_digest")
        ),
        "operator_signature_digest_rederives": (
            isinstance(signature, Mapping)
            and sha256_digest(signature)
            == commit_envelope.get("operator_signature_digest")
        ),
        "operator_action_matches_runtime_commit": (
            commit_envelope.get("required_operator_action")
            == SUBDIVISION_RUNTIME_COMMIT_ACTION
        ),
        "commit_envelope_runtime_flags_false": _runtime_flags_false(
            commit_envelope
        ),
        "runtime_rehearsal_schema_current": (
            runtime_rehearsal.get("schema_version")
            == SUBDIVISION_RUNTIME_REHEARSAL_SCHEMA
        ),
        "runtime_rehearsal_ok": runtime_rehearsal.get("ok") is True,
        "runtime_rehearsal_ready_for_operator_gate": (
            runtime_rehearsal.get("ready_for_operator_commit_gate") is True
        ),
        "runtime_rehearsal_digest_rederives": _digest_matches(
            runtime_rehearsal,
            digest_key="rehearsal_digest",
            excluded_keys=_REHEARSAL_DIGEST_EXCLUDED_KEYS,
        ),
        "runtime_rehearsal_runtime_flags_false": _runtime_flags_false(
            runtime_rehearsal
        ),
        "envelope_rehearsal_preflight_digest_match": (
            isinstance(envelope_preflight_digest, str)
            and envelope_preflight_digest == rehearsal_preflight_digest
        ),
        "envelope_rehearsal_plan_id_match": (
            commit_envelope.get("plan_id") == runtime_rehearsal.get("plan_id")
        ),
        "envelope_rehearsal_parent_match": (
            commit_envelope.get("parent_cell_id")
            == runtime_rehearsal.get("parent_cell_id")
        ),
        "envelope_rehearsal_children_match": (
            list(commit_envelope.get("new_child_cell_ids") or [])
            == list(runtime_rehearsal.get("new_child_cell_ids") or [])
        ),
        "source_topology_matches_rehearsal_input": (
            source_digest_before
            == runtime_rehearsal.get("source_topology_digest_before")
        ),
        "plan_rebuildable_from_envelope": plan_buildable,
        "commit_candidate_topology_buildable": (
            bool(candidate_topology) and build_error == ""
        ),
        "commit_candidate_matches_rehearsal": (
            bool(candidate_topology)
            and bool(rehearsal_candidate)
            and candidate_topology == rehearsal_candidate
        ),
        "commit_candidate_digest_matches_rehearsal": (
            candidate_digest is not None
            and candidate_digest
            == runtime_rehearsal.get("candidate_topology_digest")
            == rehearsal_candidate_digest
        ),
        "source_topology_unchanged": source_digest_before == source_digest_after,
        "post_merge_canary_required": (
            commit_envelope.get("post_merge_canary_required") is True
        ),
        "auto_rollback_eligibility_required": (
            commit_envelope.get("auto_rollback_eligibility_required") is True
        ),
        "no_live_runtime_commit_authorized": True,
        "no_live_runtime_mutation_performed": True,
        "no_transport_performed": True,
        "no_claim_safe_upgrade": True,
    }
    blockers = [
        name for name, satisfied in guardrails.items()
        if satisfied is not True
    ]
    ready = not blockers
    core = {
        "schema_version": SUBDIVISION_RUNTIME_COMMIT_APPLICATION_SCHEMA,
        "application_status": SUBDIVISION_RUNTIME_COMMIT_APPLICATION_STATUS,
        "ok": ready,
        "blockers": blockers,
        "plan_id": commit_envelope.get("plan_id"),
        "parent_cell_id": commit_envelope.get("parent_cell_id"),
        "new_child_cell_ids": list(
            commit_envelope.get("new_child_cell_ids") or []
        ),
        "target_state": commit_envelope.get("target_state"),
        "source_topology_digest_before": source_digest_before,
        "source_topology_digest_after": source_digest_after,
        "subdivision_preflight_digest": envelope_preflight_digest,
        "commit_envelope_digest": commit_envelope.get("envelope_digest"),
        "runtime_rehearsal_digest": runtime_rehearsal.get("rehearsal_digest"),
        "operator_signature_digest": commit_envelope.get(
            "operator_signature_digest"
        ),
        "commit_candidate_topology_digest": candidate_digest,
        "rehearsal_candidate_topology_digest": rehearsal_candidate_digest,
        "commit_candidate_topology_build_error": build_error,
        "commit_candidate_prepared": ready,
        "live_runtime_commit_authorized": False,
        "runtime_authority_granted": False,
        "runtime_topology_mutation_applied": False,
        "routing_influence_applied": False,
        "transport_performed": False,
        "claim_safe_upgrade": False,
        "runtime_commit_performed": False,
        "post_merge_canary_required": (
            commit_envelope.get("post_merge_canary_required") is True
        ),
        "auto_rollback_eligibility_required": (
            commit_envelope.get("auto_rollback_eligibility_required") is True
        ),
        "guardrails": guardrails,
    }
    return {
        **core,
        "application_digest": sha256_digest(core),
        "commit_candidate_topology": candidate_topology,
        "commit_envelope": dict(commit_envelope),
        "runtime_rehearsal": dict(runtime_rehearsal),
    }


def _plan_from_evidence(
    commit_envelope: Mapping[str, Any],
) -> tuple[SubdivisionPlan | None, bool]:
    parent_id = commit_envelope.get("parent_cell_id")
    child_ids = commit_envelope.get("new_child_cell_ids")
    target_state = commit_envelope.get("target_state")
    plan_id = commit_envelope.get("plan_id")
    if (
        not isinstance(parent_id, str)
        or not isinstance(child_ids, list)
        or not all(isinstance(child_id, str) for child_id in child_ids)
        or target_state != "subdivision_in_shadow"
        or not isinstance(plan_id, str)
    ):
        return None, False
    computed_plan_id = compute_plan_id(
        parent_cell_id=parent_id,
        new_child_cell_ids=tuple(child_ids),
    )
    if computed_plan_id != plan_id:
        return None, False
    return (
        SubdivisionPlan(
            plan_id=plan_id,
            parent_cell_id=parent_id,
            new_child_cell_ids=tuple(sorted(child_ids)),
            rationale="runtime commit application from reviewed evidence",
            target_state=target_state,
            no_runtime_mutation=True,
        ),
        True,
    )


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


def _runtime_flags_false(document: Mapping[str, Any]) -> bool:
    return all(
        document.get(flag) is False
        for flag in (
            "runtime_authority_granted",
            "runtime_topology_mutation_applied",
            "routing_influence_applied",
            "transport_performed",
            "claim_safe_upgrade",
            "runtime_commit_performed",
        )
    )


def _mapping_as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}
