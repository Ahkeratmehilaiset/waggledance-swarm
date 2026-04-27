# SPDX-License-Identifier: BUSL-1.1
"""Vector provenance graph — Phase 9 §H.

In-memory graph of vector nodes + lineage edges. Nodes are
content-addressed (sha256 of content); duplicates collapse to the
same node_id; lineage edges record explicit relations between nodes.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Iterable

from . import (
    ANCHOR_STATUSES,
    LINEAGE_RELATIONS,
    NODE_KINDS,
    VECTOR_IDENTITY_SCHEMA_VERSION,
)


@dataclass(frozen=True)
class LineageEdge:
    target_node_id: str
    relation: str
    confidence: float = 1.0

    def __post_init__(self) -> None:
        if self.relation not in LINEAGE_RELATIONS:
            raise ValueError(
                f"unknown lineage relation: {self.relation!r}; "
                f"allowed: {LINEAGE_RELATIONS}"
            )

    def to_dict(self) -> dict:
        return {"target_node_id": self.target_node_id,
                "relation": self.relation, "confidence": self.confidence}


@dataclass(frozen=True)
class VectorNode:
    schema_version: int
    node_id: str
    content_sha256: str
    kind: str
    anchor_status: str
    capsule_context: str
    source: str
    source_kind: str
    ingested_via: str       # copy_mode | link_mode
    external_path: str | None
    fixture_fallback_used: bool
    ingested_at_tick: int
    lineage: tuple[LineageEdge, ...]
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in NODE_KINDS:
            raise ValueError(
                f"unknown node kind: {self.kind!r}; allowed: {NODE_KINDS}"
            )
        if self.anchor_status not in ANCHOR_STATUSES:
            raise ValueError(
                f"unknown anchor_status: {self.anchor_status!r}; "
                f"allowed: {ANCHOR_STATUSES}"
            )
        if self.ingested_via not in ("copy_mode", "link_mode"):
            raise ValueError(
                f"ingested_via must be copy_mode or link_mode, "
                f"got {self.ingested_via!r}"
            )

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "node_id": self.node_id,
            "content_sha256": self.content_sha256,
            "kind": self.kind,
            "anchor_status": self.anchor_status,
            "capsule_context": self.capsule_context,
            "provenance": {
                "source": self.source,
                "source_kind": self.source_kind,
                "ingested_via": self.ingested_via,
                "external_path": self.external_path,
                "fixture_fallback_used": self.fixture_fallback_used,
            },
            "ingested_at_tick": self.ingested_at_tick,
            "lineage": [e.to_dict() for e in self.lineage],
            "tags": list(self.tags),
        }


def compute_node_id(content_sha256: str, kind: str,
                         capsule_context: str) -> str:
    """Stable structural id; identical content in identical capsule
    + kind dedups to the same id."""
    canonical = json.dumps({
        "content_sha256": content_sha256,
        "kind": kind,
        "capsule_context": capsule_context,
    }, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def make_node(*,
                  content_bytes: bytes,
                  kind: str,
                  source: str,
                  source_kind: str,
                  ingested_via: str,
                  capsule_context: str = "neutral_v1",
                  anchor_status: str = "candidate",
                  external_path: str | None = None,
                  ingested_at_tick: int = 0,
                  lineage: Iterable[LineageEdge] = (),
                  tags: Iterable[str] = (),
                  fixture_fallback_used: bool = False,
                  ) -> VectorNode:
    content_sha = "sha256:" + hashlib.sha256(content_bytes).hexdigest()
    nid = compute_node_id(content_sha, kind, capsule_context)
    return VectorNode(
        schema_version=VECTOR_IDENTITY_SCHEMA_VERSION,
        node_id=nid, content_sha256=content_sha,
        kind=kind, anchor_status=anchor_status,
        capsule_context=capsule_context,
        source=source, source_kind=source_kind,
        ingested_via=ingested_via,
        external_path=external_path,
        fixture_fallback_used=fixture_fallback_used,
        ingested_at_tick=ingested_at_tick,
        lineage=tuple(lineage),
        tags=tuple(tags),
    )


# ── Graph (mutable container for in-memory work) ─────────────────-

@dataclass
class VectorProvenanceGraph:
    nodes: dict[str, VectorNode] = field(default_factory=dict)

    def add_node(self, node: VectorNode) -> tuple["VectorProvenanceGraph",
                                                       bool]:
        """Add node iff its node_id is not present. Returns
        (self, was_new)."""
        if node.node_id in self.nodes:
            return self, False
        self.nodes[node.node_id] = node
        return self, True

    def add_lineage(self, source_id: str, edge: LineageEdge) -> bool:
        """Add a lineage edge to an existing source node. Returns
        True if added, False if duplicate."""
        node = self.nodes.get(source_id)
        if node is None:
            return False
        if any(e.target_node_id == edge.target_node_id
                  and e.relation == edge.relation
                  for e in node.lineage):
            return False
        new_lineage = tuple(list(node.lineage) + [edge])
        self.nodes[source_id] = VectorNode(
            schema_version=node.schema_version, node_id=node.node_id,
            content_sha256=node.content_sha256, kind=node.kind,
            anchor_status=node.anchor_status,
            capsule_context=node.capsule_context,
            source=node.source, source_kind=node.source_kind,
            ingested_via=node.ingested_via,
            external_path=node.external_path,
            fixture_fallback_used=node.fixture_fallback_used,
            ingested_at_tick=node.ingested_at_tick,
            lineage=new_lineage, tags=node.tags,
        )
        return True

    def by_capsule(self, capsule_context: str) -> list[VectorNode]:
        return [n for n in self.nodes.values()
                 if n.capsule_context == capsule_context]

    def to_dict(self) -> dict:
        return {
            "schema_version": VECTOR_IDENTITY_SCHEMA_VERSION,
            "nodes": {nid: n.to_dict()
                       for nid, n in sorted(self.nodes.items())},
        }
