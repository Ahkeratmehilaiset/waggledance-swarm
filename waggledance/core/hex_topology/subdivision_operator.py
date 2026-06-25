# SPDX-License-Identifier: BUSL-1.1
"""Subdivision operator — Phase 9 §K.

Plans cell subdivisions deterministically. CRITICAL CONTRACT
(Prompt_1_Master §K):
- shadow-first before live usage
- no runtime mutation in this session
- subdivision is a controlled primitive

This module produces a SubdivisionPlan; the actual runtime
subdivision swap is a future Phase Z gated step.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .cell_message_contract import make_message
from .parent_child_relations import children_of, neighbors_of, parent_of
from .ring_messaging import RingDelivery, deliver_batch


@dataclass(frozen=True)
class SubdivisionPlan:
    plan_id: str
    parent_cell_id: str
    new_child_cell_ids: tuple[str, ...]
    rationale: str
    target_state: str   # subdivision_planned | subdivision_in_shadow
    no_runtime_mutation: bool

    def to_dict(self) -> dict:
        return {
            "plan_id": self.plan_id,
            "parent_cell_id": self.parent_cell_id,
            "new_child_cell_ids": list(self.new_child_cell_ids),
            "rationale": self.rationale,
            "target_state": self.target_state,
            "no_runtime_mutation": self.no_runtime_mutation,
        }


@dataclass(frozen=True)
class SubdivisionShadowActivationPacket:
    plan: SubdivisionPlan
    shadow_topology: dict
    deliveries: tuple[RingDelivery, ...]
    checks: dict[str, bool]
    no_runtime_mutation: bool
    runtime_activation_authority_granted: bool

    @property
    def ok(self) -> bool:
        return (
            all(self.checks.values())
            and self.no_runtime_mutation is True
            and self.runtime_activation_authority_granted is False
        )

    def to_dict(self) -> dict:
        delivered_count = sum(1 for delivery in self.deliveries
                              if delivery.delivered)
        blocked_count = len(self.deliveries) - delivered_count
        return {
            "ok": self.ok,
            "plan": self.plan.to_dict(),
            "shadow_topology": self.shadow_topology,
            "activation_messages": [
                delivery.msg.to_dict() for delivery in self.deliveries
            ],
            "delivery_summary": {
                "message_count": len(self.deliveries),
                "delivered_count": delivered_count,
                "blocked_count": blocked_count,
                "blocked_categories": sorted(
                    {
                        delivery.blocked_category
                        for delivery in self.deliveries
                        if delivery.blocked_category is not None
                    }
                ),
            },
            "deliveries": [delivery.to_dict()
                           for delivery in self.deliveries],
            "checks": dict(sorted(self.checks.items())),
            "no_runtime_mutation": self.no_runtime_mutation,
            "runtime_activation_authority_granted": (
                self.runtime_activation_authority_granted
            ),
        }


def compute_plan_id(*, parent_cell_id: str,
                          new_child_cell_ids: tuple[str, ...]) -> str:
    canonical = json.dumps({
        "parent_cell_id": parent_cell_id,
        "new_child_cell_ids": sorted(new_child_cell_ids),
    }, sort_keys=True, separators=(",", ":"))
    return "subdiv_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:10]


def plan_subdivision(*,
                          parent_cell_id: str,
                          new_child_cell_ids: tuple[str, ...],
                          rationale: str = "",
                          target_state: str = "subdivision_in_shadow",
                          ) -> SubdivisionPlan:
    if not parent_cell_id:
        raise ValueError("parent_cell_id required")
    if len(new_child_cell_ids) < 2:
        raise ValueError(
            "subdivision requires at least 2 child cells"
        )
    if target_state not in ("subdivision_planned", "subdivision_in_shadow"):
        raise ValueError(f"unknown target_state: {target_state!r}")
    if parent_cell_id in new_child_cell_ids:
        raise ValueError(
            "parent_cell_id cannot appear in new_child_cell_ids"
        )
    if len(set(new_child_cell_ids)) != len(new_child_cell_ids):
        raise ValueError("new_child_cell_ids must be unique")
    return SubdivisionPlan(
        plan_id=compute_plan_id(
            parent_cell_id=parent_cell_id,
            new_child_cell_ids=new_child_cell_ids,
        ),
        parent_cell_id=parent_cell_id,
        new_child_cell_ids=tuple(sorted(new_child_cell_ids)),
        rationale=rationale,
        target_state=target_state,
        no_runtime_mutation=True,
    )


def apply_plan_to_topology(topology: dict,
                                  plan: SubdivisionPlan) -> dict:
    """Return a NEW topology dict with the plan applied (subdivision
    target_state set on parent + new children registered as leaves
    in shadow). Original topology unchanged."""
    cells = dict((topology.get("cells") or {}))
    parent = dict(cells.get(plan.parent_cell_id) or {})
    if not parent:
        raise ValueError(
            f"unknown parent_cell_id: {plan.parent_cell_id!r}"
        )
    # Set parent's subdivision_state + child list
    parent["subdivision_state"] = plan.target_state
    parent_children = list(parent.get("child_cell_ids") or [])
    for c in plan.new_child_cell_ids:
        if c not in parent_children:
            parent_children.append(c)
    parent["child_cell_ids"] = sorted(parent_children)
    cells[plan.parent_cell_id] = parent
    # Register new children as shadow_only leaves.
    # Sibling-only adjacency lets the shadow ring be replayed without
    # granting live topology mutation authority.
    child_neighbors = {
        c: sorted(other for other in plan.new_child_cell_ids if other != c)
        for c in plan.new_child_cell_ids
    }
    for c in plan.new_child_cell_ids:
        if c not in cells:
            cells[c] = {
                "schema_version": 1,
                "cell_id": c,
                "parent_cell_id": plan.parent_cell_id,
                "child_cell_ids": [],
                "neighbor_cell_ids": child_neighbors[c],
                "shards": {
                    "curiosity": True, "self_model": True,
                    "vector": True, "event_log": True,
                    "proposal_queue": True, "dream_queue": True,
                    "local_budget": True,
                },
                "live_state": "shadow_only",
                "subdivision_state": "leaf",
            }
    return {"cells": cells}


def _canonical_topology(topology: dict) -> str:
    return json.dumps(topology, sort_keys=True, separators=(",", ":"))


def build_shadow_activation_packet(
    topology: dict,
    plan: SubdivisionPlan,
) -> SubdivisionShadowActivationPacket:
    """Build a deterministic shadow activation packet for a subdivision.

    The packet is the first runtime-adjacent primitive after a shadow plan:
    it applies the plan to a NEW topology dict, derives parent/child and
    sibling-ring activation messages from that topology, and validates that
    those messages deliver through the pure ring boundary checks. It does not
    mutate the input topology, perform transport, or grant live activation
    authority.
    """
    if plan.target_state != "subdivision_in_shadow":
        raise ValueError(
            "shadow activation requires target_state='subdivision_in_shadow'"
        )

    before = _canonical_topology(topology)
    shadow_topology = apply_plan_to_topology(topology, plan)
    child_ids = tuple(plan.new_child_cell_ids)
    activation_payload = {
        "plan_id": plan.plan_id,
        "activation_state": "shadow_only",
    }

    messages = []
    for child_id in child_ids:
        messages.append(make_message(
            from_cell_id=plan.parent_cell_id,
            to_cell_id=child_id,
            kind="parent_to_child",
            payload=activation_payload,
        ))
        messages.append(make_message(
            from_cell_id=child_id,
            to_cell_id=plan.parent_cell_id,
            kind="child_to_parent",
            payload=activation_payload,
        ))
    for index, child_id in enumerate(child_ids):
        messages.append(make_message(
            from_cell_id=child_id,
            to_cell_id=child_ids[(index + 1) % len(child_ids)],
            kind="ring_request",
            payload={
                "plan_id": plan.plan_id,
                "activation_state": "shadow_sibling_ring",
            },
        ))

    deliveries = tuple(deliver_batch(shadow_topology, messages))
    cells = shadow_topology.get("cells") or {}
    checks = {
        "input_topology_unchanged": before == _canonical_topology(topology),
        "parent_lists_shadow_children": all(
            child_id in children_of(shadow_topology, plan.parent_cell_id)
            for child_id in child_ids
        ),
        "children_point_to_parent": all(
            parent_of(shadow_topology, child_id) == plan.parent_cell_id
            for child_id in child_ids
        ),
        "children_are_shadow_only": all(
            (cells.get(child_id) or {}).get("live_state") == "shadow_only"
            for child_id in child_ids
        ),
        "children_remain_leaf_nodes": all(
            (cells.get(child_id) or {}).get("subdivision_state") == "leaf"
            for child_id in child_ids
        ),
        "sibling_ring_neighbors_complete": all(
            all(other_id in neighbors_of(shadow_topology, child_id)
                for other_id in child_ids if other_id != child_id)
            for child_id in child_ids
        ),
        "activation_messages_have_no_runtime_mutation": all(
            delivery.msg.no_runtime_mutation is True
            for delivery in deliveries
        ),
        "activation_messages_delivered": all(
            delivery.delivered is True for delivery in deliveries
        ),
    }
    return SubdivisionShadowActivationPacket(
        plan=plan,
        shadow_topology=shadow_topology,
        deliveries=deliveries,
        checks=checks,
        no_runtime_mutation=(
            checks["input_topology_unchanged"]
            and checks["activation_messages_have_no_runtime_mutation"]
        ),
        runtime_activation_authority_granted=False,
    )
