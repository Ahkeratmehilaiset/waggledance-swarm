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
    # Register new children as shadow_only leaves
    for c in plan.new_child_cell_ids:
        if c not in cells:
            cells[c] = {
                "schema_version": 1,
                "cell_id": c,
                "parent_cell_id": plan.parent_cell_id,
                "child_cell_ids": [],
                "neighbor_cell_ids": [],
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
