# SPDX-License-Identifier: BUSL-1.1
"""Pure Memory Palace projection helpers.

The projection maps existing WD memory metadata into a human-readable
hierarchy. It deliberately carries no storage, gate, scheduler, bridge, or
promotion authority.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
import re
from typing import Any, Mapping, Sequence


MEMORY_PALACE_PROJECTION_SCHEMA_VERSION = "memory_palace_projection.v1"
MEMORY_PALACE_NAVIGATION_INDEX_SCHEMA_VERSION = "memory_palace_navigation_index.v0"

PALACE_NODE_KINDS = ("wing", "hall", "room", "closet", "drawer")
PLACEMENT_SOURCES = (
    "manual",
    "tag_selector",
    "hex_cell",
    "capsule_context",
    "source_type",
    "vector_kind",
    "anchor_status",
    "tier",
)
SELECTOR_KEYS = (
    "tags",
    "cell_id",
    "capsule_context",
    "source_type",
    "vector_kind",
    "anchor_status",
    "tier",
)
AUTHORITY_FLAGS = (
    "runtime_authority",
    "storage_write_authority",
    "bridge_write_authority",
    "gate_skip_authority",
    "promotion_authority",
    "solver_call_authority",
)

_NODE_ID_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")
_REFERENCE_RE = re.compile(r"^[A-Za-z0-9_.:/#@+-]{1,256}$")

_ALLOWED_CHILD_KINDS = {
    "wing": frozenset({"hall", "room"}),
    "hall": frozenset({"hall", "room", "closet", "drawer"}),
    "room": frozenset({"hall", "closet", "drawer"}),
    "closet": frozenset({"drawer"}),
    "drawer": frozenset(),
}


class MemoryPalaceProjectionError(ValueError):
    """Raised when a Memory Palace projection would be ambiguous or unsafe."""


@dataclass(frozen=True)
class PalaceNode:
    node_id: str
    kind: str
    label: str
    parent_id: str | None = None
    selectors: Mapping[str, Sequence[str]] = field(default_factory=dict)
    tags: Sequence[str] = field(default_factory=tuple)
    source_refs: Sequence[str] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "node_id": self.node_id,
            "kind": self.kind,
            "label": self.label,
            "parent_id": self.parent_id,
            "selectors": {
                key: list(values)
                for key, values in _normalize_selector_map(self.selectors).items()
            },
            "tags": list(_normalize_string_tuple("tags", self.tags)),
            "source_refs": list(
                _normalize_string_tuple("source_refs", self.source_refs)
            ),
        }
        if self.metadata:
            result["metadata"] = _json_safe_mapping(self.metadata)
        return result


@dataclass(frozen=True)
class MemoryPlacement:
    memory_id: str
    palace_node_id: str
    confidence: float
    placement_source: str
    vector_node_id: str | None = None
    dedup_anchor: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        _validate_placement(self)
        result: dict[str, Any] = {
            "memory_id": self.memory_id,
            "palace_node_id": self.palace_node_id,
            "confidence": float(self.confidence),
            "placement_source": self.placement_source,
        }
        if self.vector_node_id:
            result["vector_node_id"] = self.vector_node_id
        if self.dedup_anchor:
            result["dedup_anchor"] = self.dedup_anchor
        if self.metadata:
            result["metadata"] = _json_safe_mapping(self.metadata)
        return result


@dataclass(frozen=True)
class PalaceShortcutHint:
    shortcut_id: str
    source_node_id: str
    target_node_id: str
    matched_selector_keys: Sequence[str]
    matched_values: Mapping[str, Sequence[str]]
    confidence: float
    hierarchy_hops: int
    rationale: str = ""
    no_runtime_mutation: bool = True
    runtime_authority: bool = False
    storage_write_authority: bool = False
    bridge_write_authority: bool = False
    gate_skip_authority: bool = False
    promotion_authority: bool = False
    solver_call_authority: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        _validate_shortcut_hint(self)
        result: dict[str, Any] = {
            "shortcut_id": self.shortcut_id,
            "source_node_id": self.source_node_id,
            "target_node_id": self.target_node_id,
            "matched_selector_keys": list(
                _normalize_selector_keys(self.matched_selector_keys)
            ),
            "matched_values": {
                key: list(values)
                for key, values in _normalize_selector_map(
                    self.matched_values
                ).items()
            },
            "confidence": float(self.confidence),
            "hierarchy_hops": int(self.hierarchy_hops),
            "rationale": self.rationale,
            "no_runtime_mutation": self.no_runtime_mutation,
            "runtime_authority": self.runtime_authority,
            "storage_write_authority": self.storage_write_authority,
            "bridge_write_authority": self.bridge_write_authority,
            "gate_skip_authority": self.gate_skip_authority,
            "promotion_authority": self.promotion_authority,
            "solver_call_authority": self.solver_call_authority,
        }
        if self.metadata:
            result["metadata"] = _json_safe_mapping(self.metadata)
        return result


@dataclass(frozen=True)
class PalaceShortcutCandidate:
    memory_id: str
    source_node_id: str
    target_node_id: str
    shortcut_id: str
    placement_confidence: float
    shortcut_confidence: float
    rank_score: float
    hierarchy_hops: int
    matched_selector_keys: Sequence[str]
    matched_values: Mapping[str, Sequence[str]]
    rationale: str = ""
    no_runtime_mutation: bool = True
    runtime_authority: bool = False
    storage_write_authority: bool = False
    bridge_write_authority: bool = False
    gate_skip_authority: bool = False
    promotion_authority: bool = False
    solver_call_authority: bool = False

    def to_dict(self) -> dict[str, Any]:
        _validate_shortcut_candidate(self)
        return {
            "memory_id": self.memory_id,
            "source_node_id": self.source_node_id,
            "target_node_id": self.target_node_id,
            "shortcut_id": self.shortcut_id,
            "placement_confidence": float(self.placement_confidence),
            "shortcut_confidence": float(self.shortcut_confidence),
            "rank_score": float(self.rank_score),
            "hierarchy_hops": int(self.hierarchy_hops),
            "matched_selector_keys": list(
                _normalize_selector_keys(self.matched_selector_keys)
            ),
            "matched_values": {
                key: list(values)
                for key, values in _normalize_selector_map(
                    self.matched_values
                ).items()
            },
            "rationale": self.rationale,
            "no_runtime_mutation": self.no_runtime_mutation,
            "runtime_authority": self.runtime_authority,
            "storage_write_authority": self.storage_write_authority,
            "bridge_write_authority": self.bridge_write_authority,
            "gate_skip_authority": self.gate_skip_authority,
            "promotion_authority": self.promotion_authority,
            "solver_call_authority": self.solver_call_authority,
        }


def build_memory_palace_projection(
    nodes: Sequence[PalaceNode | Mapping[str, Any]],
    placements: Sequence[MemoryPlacement | Mapping[str, Any]] = (),
    shortcuts: Sequence[PalaceShortcutHint | Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Return a deterministic, projection-only Memory Palace document."""

    normalized_nodes = validate_palace_hierarchy(nodes)
    normalized_placements = [
        _coerce_placement(placement) for placement in placements
    ]
    normalized_shortcuts = [
        _coerce_shortcut_hint(shortcut) for shortcut in shortcuts
    ]
    known_node_ids = {node.node_id for node in normalized_nodes}
    for placement in normalized_placements:
        _validate_placement(placement)
        if placement.palace_node_id not in known_node_ids:
            raise MemoryPalaceProjectionError(
                f"placement references unknown palace node: {placement.palace_node_id}"
            )
    seen_shortcuts: set[str] = set()
    for shortcut in normalized_shortcuts:
        _validate_shortcut_hint(shortcut)
        if shortcut.shortcut_id in seen_shortcuts:
            raise MemoryPalaceProjectionError(
                f"duplicate shortcut_id: {shortcut.shortcut_id}"
            )
        seen_shortcuts.add(shortcut.shortcut_id)
        for label, node_id in (
            ("source_node_id", shortcut.source_node_id),
            ("target_node_id", shortcut.target_node_id),
        ):
            if node_id not in known_node_ids:
                raise MemoryPalaceProjectionError(
                    f"shortcut references unknown {label}: {node_id}"
                )

    return {
        "schema_version": MEMORY_PALACE_PROJECTION_SCHEMA_VERSION,
        "source_of_truth": "projection_only",
        "runtime_authority": False,
        "storage_write_authority": False,
        "bridge_write_authority": False,
        "gate_skip_authority": False,
        "promotion_authority": False,
        "nodes": [node.to_dict() for node in normalized_nodes],
        "placements": [placement.to_dict() for placement in normalized_placements],
        "shortcuts": [
            shortcut.to_dict()
            for shortcut in sorted(
                normalized_shortcuts, key=lambda item: item.shortcut_id
            )
        ],
    }


def build_memory_palace_navigation_index(
    nodes: Sequence[PalaceNode | Mapping[str, Any]],
) -> dict[str, Any]:
    """Return deterministic path and child indexes for palace visualization.

    The navigation index is derived from the same validated hierarchy as the
    projection document. It is a read-side view only: it does not read storage,
    mutate memory, dispatch solvers, append bridge events, or grant gate or
    promotion authority.
    """

    normalized_nodes = validate_palace_hierarchy(nodes)
    by_id = {node.node_id: node for node in normalized_nodes}
    root_node_ids: list[str] = []
    children_by_node: dict[str, list[str]] = {
        node.node_id: [] for node in normalized_nodes
    }
    for node in normalized_nodes:
        if node.parent_id is None:
            root_node_ids.append(node.node_id)
            continue
        children_by_node[node.parent_id].append(node.node_id)
    for child_ids in children_by_node.values():
        child_ids.sort()

    entries: list[dict[str, Any]] = []
    paths_by_node: dict[str, list[str]] = {}
    for node in normalized_nodes:
        path_node_ids = _lineage_node_ids(by_id, node.node_id)
        path_labels = tuple(by_id[node_id].label for node_id in path_node_ids)
        depth = len(path_node_ids) - 1
        paths_by_node[node.node_id] = list(path_node_ids)
        entries.append({
            "node_id": node.node_id,
            "kind": node.kind,
            "label": node.label,
            "parent_id": node.parent_id,
            "depth": depth,
            "path_node_ids": list(path_node_ids),
            "path_labels": list(path_labels),
            "child_node_ids": list(children_by_node[node.node_id]),
        })

    return {
        "schema_version": MEMORY_PALACE_NAVIGATION_INDEX_SCHEMA_VERSION,
        "source_of_truth": "projection_only",
        "runtime_authority": False,
        "storage_write_authority": False,
        "bridge_write_authority": False,
        "gate_skip_authority": False,
        "promotion_authority": False,
        "solver_call_authority": False,
        "node_count": len(normalized_nodes),
        "root_node_ids": sorted(root_node_ids),
        "max_depth": max((entry["depth"] for entry in entries), default=0),
        "nodes": entries,
        "paths_by_node": paths_by_node,
        "children_by_node": children_by_node,
    }


def validate_palace_hierarchy(
    nodes: Sequence[PalaceNode | Mapping[str, Any]],
) -> tuple[PalaceNode, ...]:
    """Validate node shape, parent links, child kinds, and cycles."""

    normalized = tuple(_coerce_node(node) for node in nodes)
    by_id: dict[str, PalaceNode] = {}
    for node in normalized:
        _validate_node(node)
        if node.node_id in by_id:
            raise MemoryPalaceProjectionError(f"duplicate palace node: {node.node_id}")
        by_id[node.node_id] = node

    for node in normalized:
        if node.parent_id is None:
            continue
        parent = by_id.get(node.parent_id)
        if parent is None:
            raise MemoryPalaceProjectionError(
                f"palace node {node.node_id} references unknown parent: {node.parent_id}"
            )
        allowed = _ALLOWED_CHILD_KINDS[parent.kind]
        if node.kind not in allowed:
            raise MemoryPalaceProjectionError(
                f"palace node {node.node_id} kind {node.kind} is not allowed under "
                f"{parent.kind} parent {parent.node_id}"
            )

    _assert_acyclic(by_id)
    return _parent_first_order(by_id)


def derive_candidate_placements(
    *,
    memory_id: str,
    metadata: Mapping[str, Any],
    nodes: Sequence[PalaceNode | Mapping[str, Any]],
    vector_node_id: str | None = None,
    dedup_anchor: str | None = None,
) -> tuple[MemoryPlacement, ...]:
    """Derive candidate placements from existing WD metadata selectors.

    A node matches only when every selector key on that node has at least one
    overlap with the memory metadata. Empty selectors never match implicitly.
    """

    if not _non_empty_string(memory_id):
        raise MemoryPalaceProjectionError("memory_id must be a non-empty string")
    normalized_nodes = validate_palace_hierarchy(nodes)
    metadata_values = {
        key: set(_metadata_values(metadata.get(key))) for key in SELECTOR_KEYS
    }
    placements: list[MemoryPlacement] = []
    for node in normalized_nodes:
        selectors = _normalize_selector_map(node.selectors)
        if not selectors:
            continue
        matched_keys: list[str] = []
        for key, selector_values in selectors.items():
            if not (set(selector_values) & metadata_values[key]):
                matched_keys = []
                break
            matched_keys.append(key)
        if not matched_keys:
            continue
        placements.append(
            MemoryPlacement(
                memory_id=memory_id,
                vector_node_id=vector_node_id,
                palace_node_id=node.node_id,
                confidence=min(1.0, 0.5 + (0.1 * len(matched_keys))),
                placement_source=_placement_source_for(matched_keys),
                dedup_anchor=dedup_anchor,
                metadata={"matched_selector_keys": tuple(matched_keys)},
            )
        )
    return tuple(placements)


def derive_shortcut_hints(
    nodes: Sequence[PalaceNode | Mapping[str, Any]],
    *,
    min_shared_selector_keys: int = 1,
    min_hierarchy_hops: int = 2,
    max_hints_per_source: int = 3,
) -> tuple[PalaceShortcutHint, ...]:
    """Derive deterministic cross-hierarchy hints from shared selectors.

    The hints are visualization/retrieval affordances only. They never mutate
    the hierarchy, call solvers, enqueue work, or skip gates.
    """

    if min_shared_selector_keys < 1:
        raise MemoryPalaceProjectionError(
            "min_shared_selector_keys must be at least 1"
        )
    if min_hierarchy_hops < 1:
        raise MemoryPalaceProjectionError("min_hierarchy_hops must be at least 1")
    if max_hints_per_source < 1:
        raise MemoryPalaceProjectionError("max_hints_per_source must be at least 1")

    normalized_nodes = validate_palace_hierarchy(nodes)
    by_id = {node.node_id: node for node in normalized_nodes}
    hints: list[PalaceShortcutHint] = []
    for source in normalized_nodes:
        source_selectors = _normalize_selector_map(source.selectors)
        if not source_selectors:
            continue
        source_hints: list[PalaceShortcutHint] = []
        for target in normalized_nodes:
            if target.node_id == source.node_id:
                continue
            target_selectors = _normalize_selector_map(target.selectors)
            if not target_selectors:
                continue
            hierarchy_hops = _hierarchy_distance_hops(
                by_id, source.node_id, target.node_id
            )
            if hierarchy_hops is None or hierarchy_hops < min_hierarchy_hops:
                continue
            matched_values = _shared_selector_values(
                source_selectors,
                target_selectors,
            )
            matched_keys = tuple(sorted(matched_values))
            if len(matched_keys) < min_shared_selector_keys:
                continue
            source_hints.append(
                PalaceShortcutHint(
                    shortcut_id=_shortcut_id(source.node_id, target.node_id),
                    source_node_id=source.node_id,
                    target_node_id=target.node_id,
                    matched_selector_keys=matched_keys,
                    matched_values=matched_values,
                    confidence=_shortcut_confidence(matched_keys, hierarchy_hops),
                    hierarchy_hops=hierarchy_hops,
                    rationale=(
                        "Shared Memory Palace selectors suggest a read-side "
                        "shortcut to related distant expertise."
                    ),
                    metadata={"projection_only": True},
                )
            )
        hints.extend(
            sorted(
                source_hints,
                key=lambda item: (
                    -float(item.confidence),
                    int(item.hierarchy_hops),
                    item.target_node_id,
                    item.shortcut_id,
                ),
            )[:max_hints_per_source]
        )
    return tuple(sorted(hints, key=lambda item: item.shortcut_id))


def rank_shortcut_candidates_for_memory(
    projection: Mapping[str, Any],
    memory_id: str,
    *,
    max_candidates: int = 3,
    min_rank_score: float = 0.0,
) -> tuple[dict[str, Any], ...]:
    """Rank read-side shortcut targets for a memory placement.

    This helper is a projection reader only. It does not navigate, call a
    solver, enqueue work, write storage, append bridge events, skip gates, or
    upgrade a claim. Unsafe shortcut authority flags fail closed by reusing the
    same ``PalaceShortcutHint`` validation path as projection construction.
    """

    if not isinstance(projection, Mapping):
        raise MemoryPalaceProjectionError("projection must be an object")
    if projection.get("schema_version") != MEMORY_PALACE_PROJECTION_SCHEMA_VERSION:
        raise MemoryPalaceProjectionError("unsupported projection schema_version")
    for field_name in AUTHORITY_FLAGS:
        if bool(projection.get(field_name, False)):
            raise MemoryPalaceProjectionError(
                f"projection {field_name} must be false"
            )
    if not _non_empty_string(memory_id):
        raise MemoryPalaceProjectionError("memory_id must be a non-empty string")
    if (
        not isinstance(max_candidates, int)
        or isinstance(max_candidates, bool)
        or max_candidates < 1
    ):
        raise MemoryPalaceProjectionError("max_candidates must be at least 1")
    if not (
        isinstance(min_rank_score, (int, float))
        and math.isfinite(float(min_rank_score))
        and 0.0 <= float(min_rank_score) <= 1.0
    ):
        raise MemoryPalaceProjectionError("min_rank_score must be 0..1")

    normalized_nodes = validate_palace_hierarchy(
        [
            _coerce_node(node)
            for node in _projection_sequence(projection, "nodes")
        ]
    )
    known_node_ids = {node.node_id for node in normalized_nodes}
    placements = [
        _coerce_placement(placement)
        for placement in _projection_sequence(projection, "placements")
    ]
    shortcuts = [
        _coerce_shortcut_hint(shortcut)
        for shortcut in _projection_sequence(projection, "shortcuts")
    ]
    for placement in placements:
        _validate_placement(placement)
        if placement.palace_node_id not in known_node_ids:
            raise MemoryPalaceProjectionError(
                f"placement references unknown palace node: {placement.palace_node_id}"
            )
    by_source: dict[str, list[PalaceShortcutHint]] = {}
    seen_shortcuts: set[str] = set()
    for shortcut in shortcuts:
        _validate_shortcut_hint(shortcut)
        if shortcut.shortcut_id in seen_shortcuts:
            raise MemoryPalaceProjectionError(
                f"duplicate shortcut_id: {shortcut.shortcut_id}"
            )
        seen_shortcuts.add(shortcut.shortcut_id)
        for label, node_id in (
            ("source_node_id", shortcut.source_node_id),
            ("target_node_id", shortcut.target_node_id),
        ):
            if node_id not in known_node_ids:
                raise MemoryPalaceProjectionError(
                    f"shortcut references unknown {label}: {node_id}"
                )
        by_source.setdefault(shortcut.source_node_id, []).append(shortcut)

    candidates: list[PalaceShortcutCandidate] = []
    for placement in placements:
        if placement.memory_id != memory_id:
            continue
        for shortcut in by_source.get(placement.palace_node_id, ()):
            score = round(
                float(placement.confidence) * float(shortcut.confidence),
                6,
            )
            if score < float(min_rank_score):
                continue
            candidates.append(
                PalaceShortcutCandidate(
                    memory_id=memory_id,
                    source_node_id=placement.palace_node_id,
                    target_node_id=shortcut.target_node_id,
                    shortcut_id=shortcut.shortcut_id,
                    placement_confidence=float(placement.confidence),
                    shortcut_confidence=float(shortcut.confidence),
                    rank_score=score,
                    hierarchy_hops=int(shortcut.hierarchy_hops),
                    matched_selector_keys=shortcut.matched_selector_keys,
                    matched_values=shortcut.matched_values,
                    rationale=(
                        "Read-side Memory Palace shortcut candidate; "
                        "projection-only and authority-free."
                    ),
                )
            )
    return tuple(
        candidate.to_dict()
        for candidate in sorted(
            candidates,
            key=lambda item: (
                -float(item.rank_score),
                -int(item.hierarchy_hops),
                item.target_node_id,
                item.shortcut_id,
            ),
        )[:max_candidates]
    )


def rank_shortcut_candidates_for_metadata(
    *,
    memory_id: str,
    metadata: Mapping[str, Any],
    nodes: Sequence[PalaceNode | Mapping[str, Any]],
    source_node_id: str | None = None,
    vector_node_id: str | None = None,
    dedup_anchor: str | None = None,
    max_candidates: int = 3,
    min_rank_score: float = 0.0,
    min_shared_selector_keys: int = 1,
    min_hierarchy_hops: int = 2,
    max_hints_per_source: int = 3,
) -> tuple[dict[str, Any], ...]:
    """Rank shortcut targets directly from memory/query metadata.

    This is a product-facing read-side adapter over the projection primitives:
    metadata derives candidate placements, the validated hierarchy derives
    shortcut hints, and the resulting projection is ranked by the existing
    memory shortcut reader. It does not mutate storage, append bridge events,
    dispatch solvers, enqueue schedulers, change routes, skip gates, or grant
    promotion/runtime authority.
    """

    if not isinstance(metadata, Mapping):
        raise MemoryPalaceProjectionError("metadata must be an object")
    normalized_nodes = validate_palace_hierarchy(nodes)
    known_node_ids = {node.node_id for node in normalized_nodes}
    if source_node_id is not None:
        if not _NODE_ID_RE.fullmatch(source_node_id):
            raise MemoryPalaceProjectionError(
                "source_node_id must be a valid palace node_id"
            )
        if source_node_id not in known_node_ids:
            raise MemoryPalaceProjectionError(
                f"source_node_id references unknown palace node: {source_node_id}"
            )
    placements = derive_candidate_placements(
        memory_id=memory_id,
        metadata=metadata,
        nodes=normalized_nodes,
        vector_node_id=vector_node_id,
        dedup_anchor=dedup_anchor,
    )
    if source_node_id is not None:
        placements = tuple(
            placement
            for placement in placements
            if placement.palace_node_id == source_node_id
        )
    if not placements:
        return ()
    shortcuts = derive_shortcut_hints(
        normalized_nodes,
        min_shared_selector_keys=min_shared_selector_keys,
        min_hierarchy_hops=min_hierarchy_hops,
        max_hints_per_source=max_hints_per_source,
    )
    if not shortcuts:
        return ()
    projection = build_memory_palace_projection(
        normalized_nodes,
        placements=placements,
        shortcuts=shortcuts,
    )
    return rank_shortcut_candidates_for_memory(
        projection,
        memory_id,
        max_candidates=max_candidates,
        min_rank_score=min_rank_score,
    )


def _coerce_node(value: PalaceNode | Mapping[str, Any]) -> PalaceNode:
    if isinstance(value, PalaceNode):
        return value
    if not isinstance(value, Mapping):
        raise MemoryPalaceProjectionError("palace node must be an object")
    selectors = _optional_mapping(value, "selectors")
    metadata = _optional_mapping(value, "metadata")
    tags = _optional_sequence(value, "tags")
    source_refs = _optional_sequence(value, "source_refs")
    return PalaceNode(
        node_id=str(value.get("node_id") or ""),
        kind=str(value.get("kind") or ""),
        label=str(value.get("label") or ""),
        parent_id=(
            str(value["parent_id"]) if value.get("parent_id") is not None else None
        ),
        selectors=selectors,
        tags=tags,
        source_refs=source_refs,
        metadata=metadata,
    )


def _coerce_placement(value: MemoryPlacement | Mapping[str, Any]) -> MemoryPlacement:
    if isinstance(value, MemoryPlacement):
        return value
    if not isinstance(value, Mapping):
        raise MemoryPalaceProjectionError("memory placement must be an object")
    metadata = _optional_mapping(value, "metadata")
    confidence = _coerce_confidence(value.get("confidence"))
    return MemoryPlacement(
        memory_id=str(value.get("memory_id") or ""),
        palace_node_id=str(value.get("palace_node_id") or ""),
        confidence=confidence,
        placement_source=str(value.get("placement_source") or ""),
        vector_node_id=(
            str(value["vector_node_id"])
            if value.get("vector_node_id") is not None
            else None
        ),
        dedup_anchor=(
            str(value["dedup_anchor"]) if value.get("dedup_anchor") is not None else None
        ),
        metadata=metadata,
    )


def _coerce_shortcut_hint(
    value: PalaceShortcutHint | Mapping[str, Any],
) -> PalaceShortcutHint:
    if isinstance(value, PalaceShortcutHint):
        return value
    if not isinstance(value, Mapping):
        raise MemoryPalaceProjectionError("palace shortcut hint must be an object")
    metadata = _optional_mapping(value, "metadata")
    return PalaceShortcutHint(
        shortcut_id=str(value.get("shortcut_id") or ""),
        source_node_id=str(value.get("source_node_id") or ""),
        target_node_id=str(value.get("target_node_id") or ""),
        matched_selector_keys=_optional_sequence(value, "matched_selector_keys"),
        matched_values=_optional_mapping(value, "matched_values"),
        confidence=_coerce_confidence(value.get("confidence")),
        hierarchy_hops=_coerce_positive_int(
            value.get("hierarchy_hops"),
            "hierarchy_hops",
        ),
        rationale=str(value.get("rationale") or ""),
        no_runtime_mutation=_coerce_bool(
            value.get("no_runtime_mutation"),
            "no_runtime_mutation",
            default=True,
        ),
        runtime_authority=_coerce_bool(
            value.get("runtime_authority"),
            "runtime_authority",
            default=False,
        ),
        storage_write_authority=_coerce_bool(
            value.get("storage_write_authority"),
            "storage_write_authority",
            default=False,
        ),
        bridge_write_authority=_coerce_bool(
            value.get("bridge_write_authority"),
            "bridge_write_authority",
            default=False,
        ),
        gate_skip_authority=_coerce_bool(
            value.get("gate_skip_authority"),
            "gate_skip_authority",
            default=False,
        ),
        promotion_authority=_coerce_bool(
            value.get("promotion_authority"),
            "promotion_authority",
            default=False,
        ),
        solver_call_authority=_coerce_bool(
            value.get("solver_call_authority"),
            "solver_call_authority",
            default=False,
        ),
        metadata=metadata,
    )


def _validate_node(node: PalaceNode) -> None:
    if not _NODE_ID_RE.fullmatch(node.node_id):
        raise MemoryPalaceProjectionError(f"invalid palace node_id: {node.node_id}")
    if node.kind not in PALACE_NODE_KINDS:
        raise MemoryPalaceProjectionError(f"invalid palace node kind: {node.kind}")
    if not _non_empty_string(node.label):
        raise MemoryPalaceProjectionError("palace node label must be non-empty")
    if node.parent_id is not None:
        if not _NODE_ID_RE.fullmatch(node.parent_id):
            raise MemoryPalaceProjectionError(
                f"invalid palace parent_id: {node.parent_id}"
            )
        if node.parent_id == node.node_id:
            raise MemoryPalaceProjectionError(
                f"palace node cannot parent itself: {node.node_id}"
            )
    _normalize_selector_map(node.selectors)
    _normalize_string_tuple("tags", node.tags)
    for source_ref in _normalize_string_tuple("source_refs", node.source_refs):
        if not _REFERENCE_RE.fullmatch(source_ref):
            raise MemoryPalaceProjectionError(f"invalid source_ref: {source_ref}")
    _assert_no_authority_flags(node.metadata, label=f"palace node {node.node_id}")


def _validate_placement(placement: MemoryPlacement) -> None:
    if not _non_empty_string(placement.memory_id):
        raise MemoryPalaceProjectionError("placement memory_id must be non-empty")
    if not _NODE_ID_RE.fullmatch(placement.palace_node_id):
        raise MemoryPalaceProjectionError(
            f"invalid placement palace_node_id: {placement.palace_node_id}"
        )
    if not (
        isinstance(placement.confidence, (int, float))
        and math.isfinite(float(placement.confidence))
        and 0.0 <= float(placement.confidence) <= 1.0
    ):
        raise MemoryPalaceProjectionError("placement confidence must be 0..1")
    if placement.placement_source not in PLACEMENT_SOURCES:
        raise MemoryPalaceProjectionError(
            f"invalid placement_source: {placement.placement_source}"
        )
    if placement.vector_node_id is not None and not _non_empty_string(
        placement.vector_node_id
    ):
        raise MemoryPalaceProjectionError("vector_node_id must be non-empty")
    if placement.dedup_anchor is not None and not _non_empty_string(
        placement.dedup_anchor
    ):
        raise MemoryPalaceProjectionError("dedup_anchor must be non-empty")
    _assert_no_authority_flags(placement.metadata, label="memory placement")


def _validate_shortcut_hint(shortcut: PalaceShortcutHint) -> None:
    if not _NODE_ID_RE.fullmatch(shortcut.shortcut_id):
        raise MemoryPalaceProjectionError(
            f"invalid shortcut_id: {shortcut.shortcut_id}"
        )
    for label, node_id in (
        ("source_node_id", shortcut.source_node_id),
        ("target_node_id", shortcut.target_node_id),
    ):
        if not _NODE_ID_RE.fullmatch(node_id):
            raise MemoryPalaceProjectionError(f"invalid shortcut {label}: {node_id}")
    if shortcut.source_node_id == shortcut.target_node_id:
        raise MemoryPalaceProjectionError(
            "shortcut source_node_id and target_node_id must differ"
        )
    matched_keys = _normalize_selector_keys(shortcut.matched_selector_keys)
    matched_values = _normalize_selector_map(shortcut.matched_values)
    missing = sorted(set(matched_keys) - set(matched_values))
    if missing:
        raise MemoryPalaceProjectionError(
            f"shortcut matched_values missing selector key: {missing[0]}"
        )
    extra = sorted(set(matched_values) - set(matched_keys))
    if extra:
        raise MemoryPalaceProjectionError(
            f"shortcut matched_values contains unlisted selector key: {extra[0]}"
        )
    if not (
        isinstance(shortcut.confidence, (int, float))
        and math.isfinite(float(shortcut.confidence))
        and 0.0 <= float(shortcut.confidence) <= 1.0
    ):
        raise MemoryPalaceProjectionError("shortcut confidence must be 0..1")
    if not isinstance(shortcut.hierarchy_hops, int) or shortcut.hierarchy_hops < 1:
        raise MemoryPalaceProjectionError("shortcut hierarchy_hops must be >= 1")
    if not shortcut.no_runtime_mutation:
        raise MemoryPalaceProjectionError("shortcut no_runtime_mutation must be true")
    for field_name in AUTHORITY_FLAGS:
        if bool(getattr(shortcut, field_name)):
            raise MemoryPalaceProjectionError(
                f"shortcut {field_name} must be false"
            )
    _assert_no_authority_flags(shortcut.metadata, label="palace shortcut")


def _validate_shortcut_candidate(candidate: PalaceShortcutCandidate) -> None:
    if not _non_empty_string(candidate.memory_id):
        raise MemoryPalaceProjectionError(
            "shortcut candidate memory_id must be non-empty"
        )
    for label, node_id in (
        ("source_node_id", candidate.source_node_id),
        ("target_node_id", candidate.target_node_id),
    ):
        if not _NODE_ID_RE.fullmatch(node_id):
            raise MemoryPalaceProjectionError(
                f"invalid shortcut candidate {label}: {node_id}"
            )
    if candidate.source_node_id == candidate.target_node_id:
        raise MemoryPalaceProjectionError(
            "shortcut candidate source_node_id and target_node_id must differ"
        )
    if not _NODE_ID_RE.fullmatch(candidate.shortcut_id):
        raise MemoryPalaceProjectionError(
            f"invalid shortcut candidate shortcut_id: {candidate.shortcut_id}"
        )
    for field_name in (
        "placement_confidence",
        "shortcut_confidence",
        "rank_score",
    ):
        value = getattr(candidate, field_name)
        if not (
            isinstance(value, (int, float))
            and math.isfinite(float(value))
            and 0.0 <= float(value) <= 1.0
        ):
            raise MemoryPalaceProjectionError(
                f"shortcut candidate {field_name} must be 0..1"
            )
    if not isinstance(candidate.hierarchy_hops, int) or candidate.hierarchy_hops < 1:
        raise MemoryPalaceProjectionError(
            "shortcut candidate hierarchy_hops must be >= 1"
        )
    matched_keys = _normalize_selector_keys(candidate.matched_selector_keys)
    matched_values = _normalize_selector_map(candidate.matched_values)
    missing = sorted(set(matched_keys) - set(matched_values))
    if missing:
        raise MemoryPalaceProjectionError(
            f"shortcut candidate matched_values missing selector key: {missing[0]}"
        )
    extra = sorted(set(matched_values) - set(matched_keys))
    if extra:
        raise MemoryPalaceProjectionError(
            "shortcut candidate matched_values contains unlisted selector key: "
            f"{extra[0]}"
        )
    if not candidate.no_runtime_mutation:
        raise MemoryPalaceProjectionError(
            "shortcut candidate no_runtime_mutation must be true"
        )
    for field_name in AUTHORITY_FLAGS:
        if bool(getattr(candidate, field_name)):
            raise MemoryPalaceProjectionError(
                f"shortcut candidate {field_name} must be false"
            )


def _normalize_selector_map(
    selectors: Mapping[str, Sequence[str]],
) -> dict[str, tuple[str, ...]]:
    if not isinstance(selectors, Mapping):
        raise MemoryPalaceProjectionError("selectors must be an object")
    normalized: dict[str, tuple[str, ...]] = {}
    for key, raw_values in selectors.items():
        key_text = str(key)
        if key_text not in SELECTOR_KEYS:
            raise MemoryPalaceProjectionError(f"unsupported selector key: {key_text}")
        values = _normalize_string_tuple(f"selectors.{key_text}", raw_values)
        if not values:
            raise MemoryPalaceProjectionError(
                f"selector {key_text} must contain at least one value"
            )
        normalized[key_text] = values
    return dict(sorted(normalized.items()))


def _normalize_selector_keys(values: Sequence[str]) -> tuple[str, ...]:
    keys = _normalize_string_tuple("matched_selector_keys", values)
    for key in keys:
        if key not in SELECTOR_KEYS:
            raise MemoryPalaceProjectionError(
                f"unsupported shortcut selector key: {key}"
            )
    if not keys:
        raise MemoryPalaceProjectionError(
            "matched_selector_keys must contain at least one value"
        )
    return keys


def _normalize_string_tuple(label: str, values: Sequence[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise MemoryPalaceProjectionError(f"{label} must be a list of strings")
    result: list[str] = []
    for value in values:
        if not _non_empty_string(value):
            raise MemoryPalaceProjectionError(f"{label} must contain only strings")
        result.append(str(value).strip())
    return tuple(sorted(dict.fromkeys(result)))


def _metadata_values(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return tuple(
            str(item).strip()
            for item in value
            if isinstance(item, str) and item.strip()
        )
    return ()


def _placement_source_for(matched_keys: Sequence[str]) -> str:
    if "tags" in matched_keys:
        return "tag_selector"
    for key in (
        "cell_id",
        "capsule_context",
        "source_type",
        "vector_kind",
        "anchor_status",
        "tier",
    ):
        if key in matched_keys:
            return key if key != "cell_id" else "hex_cell"
    return "manual"


def _assert_acyclic(by_id: Mapping[str, PalaceNode]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in visited:
            return
        if node_id in visiting:
            raise MemoryPalaceProjectionError(
                f"cycle detected in palace hierarchy at: {node_id}"
            )
        visiting.add(node_id)
        parent_id = by_id[node_id].parent_id
        if parent_id is not None:
            visit(parent_id)
        visiting.remove(node_id)
        visited.add(node_id)

    for candidate in by_id:
        visit(candidate)


def _hierarchy_distance_hops(
    by_id: Mapping[str, PalaceNode],
    source_node_id: str,
    target_node_id: str,
) -> int | None:
    if source_node_id == target_node_id:
        return 0
    visited = {source_node_id}
    frontier = [(source_node_id, 0)]
    index = 0
    while index < len(frontier):
        node_id, distance = frontier[index]
        index += 1
        node = by_id.get(node_id)
        if node is None:
            continue
        neighbors = [
            candidate.node_id
            for candidate in by_id.values()
            if candidate.parent_id == node_id
        ]
        if node.parent_id is not None:
            neighbors.append(node.parent_id)
        else:
            neighbors.extend(
                candidate.node_id
                for candidate in by_id.values()
                if candidate.parent_id is None and candidate.node_id != node_id
            )
        for neighbor in sorted(neighbors):
            if neighbor == target_node_id:
                return distance + 1
            if neighbor not in visited:
                visited.add(neighbor)
                frontier.append((neighbor, distance + 1))
    return None


def _lineage_node_ids(
    by_id: Mapping[str, PalaceNode],
    node_id: str,
) -> tuple[str, ...]:
    if node_id not in by_id:
        raise MemoryPalaceProjectionError(f"unknown palace node: {node_id}")
    lineage: list[str] = []
    seen: set[str] = set()
    current: str | None = node_id
    while current is not None:
        if current in seen:
            raise MemoryPalaceProjectionError(
                f"cycle detected in palace hierarchy at: {current}"
            )
        node = by_id.get(current)
        if node is None:
            raise MemoryPalaceProjectionError(
                f"palace node references unknown parent: {current}"
            )
        seen.add(current)
        lineage.append(current)
        current = node.parent_id
    return tuple(reversed(lineage))


def _parent_first_order(by_id: Mapping[str, PalaceNode]) -> tuple[PalaceNode, ...]:
    children: dict[str | None, list[PalaceNode]] = {}
    for node in by_id.values():
        children.setdefault(node.parent_id, []).append(node)

    ordered: list[PalaceNode] = []

    def append_subtree(node: PalaceNode) -> None:
        ordered.append(node)
        for child in sorted(children.get(node.node_id, ()), key=lambda item: item.node_id):
            append_subtree(child)

    for root in sorted(children.get(None, ()), key=lambda item: item.node_id):
        append_subtree(root)
    return tuple(ordered)


def _shared_selector_values(
    source_selectors: Mapping[str, Sequence[str]],
    target_selectors: Mapping[str, Sequence[str]],
) -> dict[str, tuple[str, ...]]:
    result: dict[str, tuple[str, ...]] = {}
    for key in sorted(set(source_selectors) & set(target_selectors)):
        shared = tuple(
            sorted(set(source_selectors[key]) & set(target_selectors[key]))
        )
        if shared:
            result[key] = shared
    return result


def _shortcut_confidence(matched_keys: Sequence[str], hierarchy_hops: int) -> float:
    score = 0.55 + (0.1 * len(matched_keys))
    if "cell_id" in matched_keys:
        score += 0.05
    if "tags" in matched_keys:
        score += 0.05
    if hierarchy_hops >= 3:
        score += 0.05
    return min(1.0, score)


def _shortcut_id(source_node_id: str, target_node_id: str) -> str:
    payload = json.dumps(
        {"source": source_node_id, "target": target_node_id},
        sort_keys=True,
        separators=(",", ":"),
    )
    return "shortcut." + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _assert_no_authority_flags(
    value: Any, *, label: str, path: str = "metadata"
) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key)
            child_path = f"{path}.{key_text}" if path else key_text
            if key_text in AUTHORITY_FLAGS:
                raise MemoryPalaceProjectionError(
                    f"{label} cannot carry authority flag {child_path}"
                )
            _assert_no_authority_flags(child, label=label, path=child_path)
        return
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        for index, child in enumerate(value):
            _assert_no_authority_flags(
                child, label=label, path=f"{path}[{index}]"
            )


def _non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _optional_mapping(value: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    raw = value.get(key, {})
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise MemoryPalaceProjectionError(f"{key} must be an object")
    return raw


def _optional_sequence(value: Mapping[str, Any], key: str) -> Sequence[str]:
    raw = value.get(key, ())
    if raw is None:
        return ()
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        raise MemoryPalaceProjectionError(f"{key} must be a list of strings")
    return raw


def _projection_sequence(
    projection: Mapping[str, Any], key: str
) -> Sequence[Mapping[str, Any]]:
    raw = projection.get(key, ())
    if raw is None:
        return ()
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        raise MemoryPalaceProjectionError(f"projection {key} must be a list")
    for item in raw:
        if not isinstance(item, Mapping):
            raise MemoryPalaceProjectionError(
                f"projection {key} must contain only objects"
            )
    return raw


def _coerce_confidence(value: Any) -> float:
    try:
        return float(value if value is not None else 0)
    except (TypeError, ValueError) as exc:
        raise MemoryPalaceProjectionError(
            "placement confidence must be numeric"
        ) from exc


def _coerce_positive_int(value: Any, label: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise MemoryPalaceProjectionError(f"{label} must be an integer") from exc
    if result < 1:
        raise MemoryPalaceProjectionError(f"{label} must be >= 1")
    return result


def _coerce_bool(value: Any, label: str, *, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise MemoryPalaceProjectionError(f"{label} must be boolean")
    return value


def _json_safe_mapping(metadata: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): _json_safe_value(value) for key, value in metadata.items()}


def _json_safe_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _json_safe_mapping(value)
    if isinstance(value, tuple):
        return [_json_safe_value(item) for item in value]
    if isinstance(value, list):
        return [_json_safe_value(item) for item in value]
    return value
