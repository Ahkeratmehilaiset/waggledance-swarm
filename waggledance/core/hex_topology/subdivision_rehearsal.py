# SPDX-License-Identifier: BUSL-1.1
"""Read-only runtime rehearsal for shadow subdivision activation."""
from __future__ import annotations

from typing import Any, Mapping

from waggledance.core.hex_topology.subdivision_operator import (
    SubdivisionPlan,
    apply_plan_to_topology,
    compute_plan_id,
)
from waggledance.core.hex_topology.subdivision_preflight import (
    SUBDIVISION_ACTIVATION_PREFLIGHT_NEXT_GATE,
    SUBDIVISION_ACTIVATION_PREFLIGHT_SCHEMA,
)
from waggledance.core.magma.canonical import sha256_digest


SUBDIVISION_RUNTIME_REHEARSAL_SCHEMA = (
    "hex.subdivision_runtime_rehearsal.v0"
)
SUBDIVISION_RUNTIME_REHEARSAL_STATUS = (
    "subdivision_runtime_rehearsal_ready"
)

_PREFLIGHT_EMBEDDED_KEYS = {
    "preflight_digest",
    "shadow_activation_packet",
    "ring_delivery_observability",
    "canary_mirror_report",
}


def build_subdivision_runtime_rehearsal(
    *,
    topology: Mapping[str, Any],
    preflight: Mapping[str, Any],
) -> dict[str, Any]:
    """Rehearse a runtime subdivision commit without applying one.

    This is the next runtime-adjacent proof after activation preflight: it
    derives the candidate topology a future executor would commit, compares it
    to the already-reviewed shadow activation packet, and records the operator
    gate still required. It never mutates the input topology, grants runtime
    authority, performs transport, or marks the subdivision live-safe.
    """
    source_digest_before = sha256_digest(topology)
    plan, plan_buildable = _plan_from_preflight(preflight)
    candidate_topology: dict[str, Any] = {}
    build_error = ""
    if plan is not None:
        try:
            candidate_topology = apply_plan_to_topology(dict(topology), plan)
        except ValueError as exc:
            build_error = str(exc)

    source_digest_after = sha256_digest(topology)
    shadow_topology = _shadow_topology(preflight)
    candidate_digest = (
        sha256_digest(candidate_topology) if candidate_topology else None
    )
    shadow_digest = (
        sha256_digest(shadow_topology) if shadow_topology else None
    )
    parent_id = preflight.get("parent_cell_id")
    child_ids = list(preflight.get("new_child_cell_ids") or [])
    cells = candidate_topology.get("cells") or {}
    parent = cells.get(parent_id) if isinstance(parent_id, str) else None

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
        "plan_rebuildable_from_preflight": plan_buildable,
        "candidate_topology_buildable": (
            bool(candidate_topology) and build_error == ""
        ),
        "candidate_matches_shadow_packet_topology": (
            bool(candidate_topology)
            and bool(shadow_topology)
            and candidate_topology == shadow_topology
        ),
        "source_topology_unchanged": source_digest_before == source_digest_after,
        "parent_lists_all_shadow_children": (
            isinstance(parent, Mapping)
            and all(
                child_id in (parent.get("child_cell_ids") or [])
                for child_id in child_ids
            )
        ),
        "candidate_children_point_to_parent": all(
            (cells.get(child_id) or {}).get("parent_cell_id") == parent_id
            for child_id in child_ids
        ),
        "candidate_children_shadow_only": all(
            (cells.get(child_id) or {}).get("live_state") == "shadow_only"
            for child_id in child_ids
        ),
        "candidate_children_remain_leaf_nodes": all(
            (cells.get(child_id) or {}).get("subdivision_state") == "leaf"
            for child_id in child_ids
        ),
        "operator_gate_still_required": (
            SUBDIVISION_ACTIVATION_PREFLIGHT_NEXT_GATE
            == "operator_signed_runtime_subdivision_commit"
        ),
        "runtime_commit_not_performed": True,
        "no_transport_performed": True,
        "no_claim_safe_upgrade": True,
    }
    blockers = [
        name for name, satisfied in guardrails.items()
        if satisfied is not True
    ]
    ready = not blockers
    core = {
        "schema_version": SUBDIVISION_RUNTIME_REHEARSAL_SCHEMA,
        "rehearsal_status": SUBDIVISION_RUNTIME_REHEARSAL_STATUS,
        "ok": ready,
        "blockers": blockers,
        "required_next_gate": SUBDIVISION_ACTIVATION_PREFLIGHT_NEXT_GATE,
        "plan_id": preflight.get("plan_id"),
        "parent_cell_id": parent_id,
        "new_child_cell_ids": child_ids,
        "target_state": preflight.get("target_state"),
        "source_topology_digest_before": source_digest_before,
        "source_topology_digest_after": source_digest_after,
        "subdivision_preflight_digest": preflight.get("preflight_digest"),
        "candidate_topology_digest": candidate_digest,
        "shadow_topology_digest": shadow_digest,
        "candidate_topology_build_error": build_error,
        "ready_for_operator_commit_gate": ready,
        "runtime_authority_granted": False,
        "runtime_topology_mutation_applied": False,
        "routing_influence_applied": False,
        "transport_performed": False,
        "claim_safe_upgrade": False,
        "runtime_commit_performed": False,
        "post_merge_canary_required": True,
        "auto_rollback_eligibility_required": True,
        "guardrails": guardrails,
    }
    return {
        **core,
        "rehearsal_digest": sha256_digest(core),
        "candidate_topology": candidate_topology,
        "subdivision_activation_preflight": dict(preflight),
    }


def _plan_from_preflight(
    preflight: Mapping[str, Any],
) -> tuple[SubdivisionPlan | None, bool]:
    parent_id = preflight.get("parent_cell_id")
    child_ids = preflight.get("new_child_cell_ids")
    target_state = preflight.get("target_state")
    plan_id = preflight.get("plan_id")
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
            rationale="runtime rehearsal from preflight evidence",
            target_state=target_state,
            no_runtime_mutation=True,
        ),
        True,
    )


def _shadow_topology(preflight: Mapping[str, Any]) -> dict[str, Any]:
    packet = preflight.get("shadow_activation_packet")
    if not isinstance(packet, Mapping):
        return {}
    topology = packet.get("shadow_topology")
    return dict(topology) if isinstance(topology, Mapping) else {}


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
