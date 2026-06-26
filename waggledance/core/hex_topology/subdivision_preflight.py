# SPDX-License-Identifier: BUSL-1.1
"""Fail-closed preflight for shadow subdivision activation packets."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from waggledance.core.hex_topology.canary_mirror import (
    summarize_canary_mirror,
)
from waggledance.core.hex_topology.subdivision_operator import (
    SubdivisionPlan,
    build_shadow_activation_packet,
)
from waggledance.core.hex_topology.ring_messaging import (
    RING_DELIVERY_OBSERVABILITY_SCHEMA,
    summarize_ring_delivery_batch,
)
from waggledance.core.magma.canonical import sha256_digest


SUBDIVISION_ACTIVATION_PREFLIGHT_SCHEMA = (
    "hex.subdivision_activation_preflight.v0"
)
SUBDIVISION_ACTIVATION_PREFLIGHT_STATUS = (
    "subdivision_activation_preflight_ready"
)
SUBDIVISION_ACTIVATION_PREFLIGHT_NEXT_GATE = (
    "operator_signed_runtime_subdivision_commit"
)

_CANARY_AUTHORITY_FLAGS = {
    "no_runtime_mutation": True,
    "runtime_authority_granted": False,
    "routing_influence_applied": False,
    "production_decision_unchanged": True,
}


def build_subdivision_activation_preflight(
    *,
    topology: Mapping[str, Any],
    plan: SubdivisionPlan,
    canary_comparisons: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build a read-only preflight over shadow subdivision evidence.

    The preflight binds the shadow activation packet to a canary mirror
    report and records the next gate. It never grants runtime authority,
    mutates topology, sends transport, or marks the subdivision as safe for
    live use.
    """
    packet = build_shadow_activation_packet(dict(topology), plan)
    packet_dict = packet.to_dict()
    canary_report = summarize_canary_mirror(canary_comparisons)

    delivery_summary = packet_dict["delivery_summary"]
    ring_delivery_observability = summarize_ring_delivery_batch(
        list(packet.deliveries)
    )
    message_kind_summary = ring_delivery_observability["by_message_kind"]
    guardrails = {
        "target_state_is_shadow": plan.target_state == (
            "subdivision_in_shadow"
        ),
        "shadow_activation_packet_ok": packet.ok is True,
        "shadow_activation_packet_no_runtime_mutation": (
            packet.no_runtime_mutation is True
        ),
        "shadow_activation_packet_no_runtime_authority": (
            packet.runtime_activation_authority_granted is False
        ),
        "shadow_activation_input_unchanged": (
            packet.checks.get("input_topology_unchanged") is True
        ),
        "activation_messages_delivered": (
            delivery_summary["blocked_count"] == 0
        ),
        "canary_evidence_present": canary_report["sample_count"] > 0,
        "canary_report_read_only": all(
            canary_report.get(flag) is expected
            for flag, expected in _CANARY_AUTHORITY_FLAGS.items()
        ),
        "ring_delivery_observability_schema_current": (
            ring_delivery_observability["schema_version"]
            == RING_DELIVERY_OBSERVABILITY_SCHEMA
        ),
        "ring_delivery_observability_covers_activation_messages": (
            ring_delivery_observability["total"]
            == delivery_summary["message_count"]
        ),
        "ring_delivery_observability_transport_false": (
            ring_delivery_observability["transport_applied"] is False
        ),
        "ring_delivery_observability_no_blocked_messages": (
            ring_delivery_observability["blocked_count"] == 0
        ),
        "ring_delivery_observability_success_ratio_one": (
            ring_delivery_observability["delivery_success_ratio"] == 1.0
        ),
        "ring_hierarchy_message_mix_present": all(
            message_kind_summary.get(kind, {}).get("delivered", 0) > 0
            for kind in ("parent_to_child", "child_to_parent", "ring_request")
        ),
        "required_next_gate_is_runtime_commit": (
            SUBDIVISION_ACTIVATION_PREFLIGHT_NEXT_GATE
            == "operator_signed_runtime_subdivision_commit"
        ),
        "no_transport_performed": True,
        "no_claim_safe_upgrade": True,
    }
    blockers = [
        name for name, satisfied in guardrails.items()
        if satisfied is not True
    ]
    no_runtime_mutation = (
        packet.no_runtime_mutation is True
        and canary_report["no_runtime_mutation"] is True
    )
    core = {
        "schema_version": SUBDIVISION_ACTIVATION_PREFLIGHT_SCHEMA,
        "preflight_status": SUBDIVISION_ACTIVATION_PREFLIGHT_STATUS,
        "required_next_gate": SUBDIVISION_ACTIVATION_PREFLIGHT_NEXT_GATE,
        "ok": not blockers,
        "blockers": blockers,
        "plan_id": plan.plan_id,
        "parent_cell_id": plan.parent_cell_id,
        "new_child_cell_ids": list(plan.new_child_cell_ids),
        "target_state": plan.target_state,
        "shadow_activation_packet_digest": sha256_digest(packet_dict),
        "ring_delivery_observability_digest": sha256_digest(
            ring_delivery_observability
        ),
        "ring_delivery_success_ratio": (
            ring_delivery_observability["delivery_success_ratio"]
        ),
        "ring_delivery_blocked_by_class": (
            ring_delivery_observability["blocked_by_class"]
        ),
        "canary_mirror_report_digest": canary_report["canonical_digest"],
        "canary_sample_count": canary_report["sample_count"],
        "canary_agreement_rate": canary_report["agreement_rate"],
        "no_runtime_mutation": no_runtime_mutation,
        "runtime_authority_granted": False,
        "runtime_topology_mutation_applied": False,
        "routing_influence_applied": False,
        "transport_performed": False,
        "claim_safe_upgrade": False,
        "guardrails": guardrails,
    }
    return {
        **core,
        "preflight_digest": sha256_digest(core),
        "shadow_activation_packet": packet_dict,
        "ring_delivery_observability": ring_delivery_observability,
        "canary_mirror_report": canary_report,
    }
