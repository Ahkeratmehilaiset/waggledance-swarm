"""IR validator — Phase 9 §G.

Schema-shape and invariant checks for IRObject without requiring the
jsonschema package at this scope. We perform direct field-set checks
against the per-type identity-key whitelist plus enum checks against
the package constants.
"""
from __future__ import annotations

from typing import Any

from . import (
    DEPENDENCY_RELATIONS,
    IR_TYPES,
    LIFECYCLE_STATUSES,
    PROMOTION_STATES,
    SOURCE_SESSIONS,
)
from .cognition_ir import IRObject


class IRValidationError(ValueError):
    """Raised when an IRObject fails invariant validation."""


def validate(obj: IRObject) -> list[str]:
    """Return a list of human-readable errors; empty list = valid."""
    errors: list[str] = []
    if obj.ir_type not in IR_TYPES:
        errors.append(f"unknown ir_type {obj.ir_type!r}")
    if obj.lifecycle_status not in LIFECYCLE_STATUSES:
        errors.append(f"unknown lifecycle_status {obj.lifecycle_status!r}")
    if obj.promotion_state not in PROMOTION_STATES:
        errors.append(f"unknown promotion_state {obj.promotion_state!r}")
    if obj.risk not in ("low", "medium", "high"):
        errors.append(f"unknown risk {obj.risk!r}")
    if not obj.ir_id or len(obj.ir_id) != 12:
        errors.append(f"ir_id must be 12 hex chars, got {obj.ir_id!r}")
    if obj.ir_compat_version < 1:
        errors.append("ir_compat_version must be >= 1")
    if obj.provenance.source_session not in SOURCE_SESSIONS:
        errors.append(
            f"unknown provenance.source_session: "
            f"{obj.provenance.source_session!r}"
        )
    for d in obj.dependencies:
        if d.relation not in DEPENDENCY_RELATIONS:
            errors.append(
                f"unknown dependency relation {d.relation!r} "
                f"in dep on {d.depends_on_ir_id!r}"
            )
    if not isinstance(obj.payload, dict):
        errors.append("payload must be a dict")
    if not obj.capsule_context:
        errors.append("capsule_context must be non-empty")
    return errors


def validate_strict(obj: IRObject) -> None:
    """Raise IRValidationError on any failure."""
    errs = validate(obj)
    if errs:
        raise IRValidationError("; ".join(errs))


def validate_batch(objs: list[IRObject]) -> dict[str, list[str]]:
    """Return ir_id → error list for each object that failed."""
    out: dict[str, list[str]] = {}
    for o in objs:
        errs = validate(o)
        if errs:
            out[o.ir_id] = errs
    return out


# ── Cross-object invariants ──────────────────────────────────────-

def detect_dependency_cycles(objs: list[IRObject]) -> list[tuple[str, ...]]:
    """Return any cycles in the dependency graph as tuples of ir_ids."""
    by_id = {o.ir_id: o for o in objs}
    cycles: list[tuple[str, ...]] = []
    visiting: set[str] = set()
    visited: set[str] = set()
    stack: list[str] = []

    def dfs(node_id: str) -> None:
        if node_id in visiting:
            # cycle from where node_id appears in stack
            i = stack.index(node_id)
            cycles.append(tuple(stack[i:]))
            return
        if node_id in visited:
            return
        visiting.add(node_id)
        stack.append(node_id)
        node = by_id.get(node_id)
        if node:
            for d in node.dependencies:
                if d.relation in ("blocks_until_resolved", "supports",
                                   "extends", "specializes",
                                   "composes_with", "refines"):
                    if d.depends_on_ir_id in by_id:
                        dfs(d.depends_on_ir_id)
        stack.pop()
        visiting.remove(node_id)
        visited.add(node_id)

    for o in objs:
        dfs(o.ir_id)
    return cycles
