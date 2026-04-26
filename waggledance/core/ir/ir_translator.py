# SPDX-License-Identifier: BUSL-1.1
"""IR translator — Phase 9 §G.

Round-trip dict ↔ IRObject. Preserves all fields under canonical
JSON serialization so two IR-canonical-JSON outputs of the same
object are byte-identical.
"""
from __future__ import annotations

import json
from typing import Any

from . import IR_COMPAT_VERSION, IR_SCHEMA_VERSION
from .cognition_ir import (
    Dependency,
    IRObject,
    Provenance,
)


def to_dict(obj: IRObject) -> dict:
    return obj.to_dict()


def to_canonical_json(obj: IRObject) -> str:
    """Stable serialization for byte-identity tests."""
    return json.dumps(obj.to_dict(), indent=2, sort_keys=True)


def from_dict(d: dict) -> IRObject:
    """Reconstruct an IRObject from a plain dict (e.g. JSON loaded)."""
    prov_d = d.get("provenance") or {}
    provenance = Provenance(
        branch_name=str(prov_d.get("branch_name") or ""),
        base_commit_hash=str(prov_d.get("base_commit_hash") or ""),
        pinned_input_manifest_sha256=str(
            prov_d.get("pinned_input_manifest_sha256") or "sha256:unknown"
        ),
        produced_by=str(prov_d.get("produced_by") or "unknown"),
        source_session=str(prov_d.get("source_session") or "external"),
        fixture_fallback_used=bool(prov_d.get("fixture_fallback_used", False)),
    )
    deps = []
    for dep in d.get("dependencies") or []:
        deps.append(Dependency(
            depends_on_ir_id=str(dep["depends_on_ir_id"]),
            relation=str(dep["relation"]),
        ))
    return IRObject(
        schema_version=int(d.get("schema_version") or IR_SCHEMA_VERSION),
        ir_id=str(d["ir_id"]),
        ir_type=str(d["ir_type"]),
        ir_compat_version=int(d.get("ir_compat_version") or IR_COMPAT_VERSION),
        lifecycle_status=str(d["lifecycle_status"]),
        risk=str(d["risk"]),
        promotion_state=str(d["promotion_state"]),
        evidence_refs=tuple(d.get("evidence_refs") or ()),
        dependencies=tuple(deps),
        provenance=provenance,
        capsule_context=str(d.get("capsule_context") or "neutral_v1"),
        payload=dict(d.get("payload") or {}),
    )


def round_trip(obj: IRObject) -> IRObject:
    """obj → dict → JSON → dict → obj. Used in determinism tests."""
    s = to_canonical_json(obj)
    return from_dict(json.loads(s))
