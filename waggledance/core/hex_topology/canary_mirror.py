# SPDX-License-Identifier: BUSL-1.1
"""Hex-mesh canary mirror — read-only shadow comparison of mesh routing.

Storyboard panels 4→5 need MEASURED runtime evidence that the hex mesh
would route real queries to the right cells BEFORE any cutover. This module
is the pure core of that canary: given one production routing decision
(intent + selected capability, optionally the capability's home cell), it
computes what the hex mesh WOULD have decided for the same query and
classifies the agreement. An aggregate report summarizes a batch of such
comparisons into a re-derivable, digest-bound evidence artifact.

Strict canary contract:
- READ-ONLY MIRROR. The production routing decision is an input and is
  never altered; nothing here routes traffic, mutates topology state,
  grants authority, or skips a gate (``no_runtime_mutation=True`` and the
  authority flags are emitted as literal constants on every artifact).
- PRIVACY-SAFE. The raw query text never appears in any artifact — only
  its sha256 digest and length (same discipline as the runtime summary
  receipt payload).
- RE-DERIVABLE. Classification is a pure function of the inputs; the
  aggregate report carries a ``canonical_digest`` over its core fields so
  an auditor recomputes the verdict instead of trusting it.
- FAIL-CLOSED. Malformed inputs raise :class:`CanaryMirrorError`; the
  aggregator refuses records with drifted schema or authority flags
  rather than silently filtering them.

Engine wiring (opt-in mirror at ``handle_query`` time) and the receipted
proof tool are deliberately separate follow-up slices.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from waggledance.core.hex_cell_topology import HexCellTopology
from waggledance.core.magma.canonical import sha256_digest

CANARY_ROUTE_COMPARISON_SCHEMA = "hex.canary_route_comparison.v0"
CANARY_MIRROR_REPORT_SCHEMA = "hex.canary_mirror_report.v0"

# Exact classification set (closed; aggregation keys always cover all four).
CANARY_MATCH_PRODUCTION_CELL = "match_production_cell"
CANARY_DIVERGENT_PRODUCTION_CELL = "divergent_production_cell"
CANARY_MATCH_INTENT_CELL = "match_intent_cell"
CANARY_DIVERGENT_KEYWORD_OVERRIDE = "divergent_keyword_override"
CANARY_CLASSIFICATIONS = (
    CANARY_MATCH_PRODUCTION_CELL,
    CANARY_DIVERGENT_PRODUCTION_CELL,
    CANARY_MATCH_INTENT_CELL,
    CANARY_DIVERGENT_KEYWORD_OVERRIDE,
)
_MATCH_CLASSIFICATIONS = frozenset(
    {CANARY_MATCH_PRODUCTION_CELL, CANARY_MATCH_INTENT_CELL}
)

# Authority flags every canary artifact must carry, with their only legal
# values. Strict identity checks (is True / is False) — never bool coercion.
_AUTHORITY_FLAG_VALUES = {
    "no_runtime_mutation": True,
    "runtime_authority_granted": False,
    "routing_influence_applied": False,
    "production_decision_unchanged": True,
}


class CanaryMirrorError(ValueError):
    """Raised when a canary comparison or report would be unsafe/ambiguous."""


def _require_str(name: str, value: Any, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise CanaryMirrorError(f"{name} must be a string")
    if not allow_empty and not value.strip():
        raise CanaryMirrorError(f"{name} must be non-empty")
    return value


def classify_canary_route(
    *,
    mesh_cell_id: str,
    intent_cell_id: str,
    production_cell_id: str | None,
) -> str:
    """Pure, re-derivable classification of one mirrored routing decision.

    With a known production home cell the mesh is judged against it; without
    one the mesh is judged against the pure intent→cell mapping, so a
    divergence means the mesh's keyword evidence pointed somewhere else
    than the production intent classifier did.
    """
    if production_cell_id is not None:
        production_cell_id = _require_str(
            "production_cell_id", production_cell_id
        )
        if mesh_cell_id == production_cell_id:
            return CANARY_MATCH_PRODUCTION_CELL
        return CANARY_DIVERGENT_PRODUCTION_CELL
    if mesh_cell_id == intent_cell_id:
        return CANARY_MATCH_INTENT_CELL
    return CANARY_DIVERGENT_KEYWORD_OVERRIDE


def build_canary_route_comparison(
    *,
    query: str,
    intent: str,
    production_capability_id: str,
    quality_path: str,
    production_cell_id: str | None = None,
) -> dict[str, Any]:
    """Mirror ONE production routing decision through the hex mesh, read-only.

    Returns a privacy-safe comparison record: the raw query is digested,
    never stored. The production decision is recorded as given and never
    altered. Deterministic: identical inputs yield an identical record (a
    fresh :class:`HexCellTopology` is used per call, so no shared state).
    """
    query = _require_str("query", query, allow_empty=True)
    intent = _require_str("intent", intent)
    production_capability_id = _require_str(
        "production_capability_id", production_capability_id
    )
    quality_path = _require_str("quality_path", quality_path)

    topology = HexCellTopology()
    mesh = topology.assign_cell(intent, query)
    intent_only = topology.assign_cell(intent, "")
    classification = classify_canary_route(
        mesh_cell_id=mesh.cell_id,
        intent_cell_id=intent_only.cell_id,
        production_cell_id=production_cell_id,
    )
    record: dict[str, Any] = {
        "schema_version": CANARY_ROUTE_COMPARISON_SCHEMA,
        "query_digest": sha256_digest({"query": query}),
        "query_length": len(query),
        "intent": intent,
        "production_capability_id": production_capability_id,
        "production_cell_id": production_cell_id,
        "quality_path": quality_path,
        "mesh_cell_id": mesh.cell_id,
        "mesh_method": mesh.method,
        "mesh_ring1_count": len(mesh.neighbors_ring1),
        "intent_cell_id": intent_only.cell_id,
        "classification": classification,
        "agreement": classification in _MATCH_CLASSIFICATIONS,
    }
    record.update(_AUTHORITY_FLAG_VALUES)
    return record


def _validate_comparison(index: int, record: Any) -> Mapping[str, Any]:
    if not isinstance(record, Mapping):
        raise CanaryMirrorError(
            f"comparison[{index}] must be a mapping (fail-closed)"
        )
    if record.get("schema_version") != CANARY_ROUTE_COMPARISON_SCHEMA:
        raise CanaryMirrorError(
            f"comparison[{index}] has wrong schema_version (fail-closed)"
        )
    classification = record.get("classification")
    if classification not in CANARY_CLASSIFICATIONS:
        raise CanaryMirrorError(
            f"comparison[{index}] has unknown classification (fail-closed)"
        )
    for flag, legal in _AUTHORITY_FLAG_VALUES.items():
        if record.get(flag) is not legal:
            raise CanaryMirrorError(
                f"comparison[{index}] authority flag {flag!r} drifted "
                "(fail-closed)"
            )
    mesh_cell = record.get("mesh_cell_id")
    if not isinstance(mesh_cell, str) or not mesh_cell:
        raise CanaryMirrorError(
            f"comparison[{index}] missing mesh_cell_id (fail-closed)"
        )
    return record


def summarize_canary_mirror(
    comparisons: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Aggregate comparison records into a digest-bound canary report.

    Fail-closed: any malformed record (wrong schema, unknown
    classification, drifted authority flag) raises instead of being
    silently dropped — an auditor must never receive an aggregate that
    quietly excluded evidence. The report's ``canonical_digest`` covers
    every core field, so the verdict is re-derivable.
    """
    if not isinstance(comparisons, Sequence) or isinstance(
        comparisons, (str, bytes)
    ):
        raise CanaryMirrorError("comparisons must be a non-string sequence")

    by_classification = {key: 0 for key in CANARY_CLASSIFICATIONS}
    by_mesh_cell: dict[str, int] = {}
    by_method: dict[str, int] = {}
    agreement_count = 0
    for index, raw in enumerate(comparisons):
        record = _validate_comparison(index, raw)
        classification = str(record["classification"])
        by_classification[classification] += 1
        if classification in _MATCH_CLASSIFICATIONS:
            agreement_count += 1
        cell = str(record["mesh_cell_id"])
        by_mesh_cell[cell] = by_mesh_cell.get(cell, 0) + 1
        method = str(record.get("mesh_method") or "unknown")
        by_method[method] = by_method.get(method, 0) + 1

    sample_count = len(list(comparisons))
    divergence_count = sample_count - agreement_count
    agreement_rate = (
        agreement_count / sample_count if sample_count else 0.0
    )
    core: dict[str, Any] = {
        "schema_version": CANARY_MIRROR_REPORT_SCHEMA,
        "measurement_scope": "shadow_mirror_read_only",
        "sample_count": sample_count,
        "agreement_count": agreement_count,
        "divergence_count": divergence_count,
        "agreement_rate": agreement_rate,
        "by_classification": dict(sorted(by_classification.items())),
        "by_mesh_cell": dict(sorted(by_mesh_cell.items())),
        "by_mesh_method": dict(sorted(by_method.items())),
    }
    core.update(_AUTHORITY_FLAG_VALUES)
    return {**core, "canonical_digest": sha256_digest(core)}
