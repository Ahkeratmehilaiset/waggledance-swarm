"""Shadow graph — Phase 8.5 Session C, deliverable C.5.

Represents the live solver world plus one or more accepted shadow
proposals in memory. Strictly non-mutating: no writes to authoritative
solver roots, no axiom YAML updates, no runtime registration.

Structure (c.txt §C5):
- nodes = solver_id × {is_live: bool, source: "library" | "shadow_proposal"}
- edges = (solver_a, solver_b, relation_type) with relation_type in
  {composes_with, refines, alternates_with, depends_on}
- node and edge ordering is sorted by stable keys for serialization

Per c.txt §CRITICAL COMPLEXITY RULE: structural diffs are computed
via set operations, not graph-isomorphism algorithms.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Iterable, Sequence

from . import DREAMING_SCHEMA_VERSION


VALID_RELATIONS = (
    "composes_with",
    "refines",
    "alternates_with",
    "depends_on",
)


@dataclass(frozen=True)
class ShadowNode:
    solver_id: str
    is_live: bool
    source: str          # "library" | "shadow_proposal"
    cell_id: str | None = None


@dataclass(frozen=True)
class ShadowEdge:
    solver_a: str
    solver_b: str
    relation_type: str   # one of VALID_RELATIONS

    def key(self) -> tuple[str, str, str]:
        return (self.solver_a, self.solver_b, self.relation_type)


@dataclass(frozen=True)
class ShadowGraph:
    schema_version: int
    nodes: tuple[ShadowNode, ...]
    edges: tuple[ShadowEdge, ...]

    def node_ids(self) -> set[str]:
        return {n.solver_id for n in self.nodes}

    def shadow_node_ids(self) -> set[str]:
        return {n.solver_id for n in self.nodes
                if n.source == "shadow_proposal"}

    def edge_keys(self) -> set[tuple[str, str, str]]:
        return {e.key() for e in self.edges}


# ── Construction ─────────────────────────────────────────────────-

def build_live_graph(
    library_solvers: Sequence[dict],
    library_edges: Sequence[dict] | None = None,
) -> ShadowGraph:
    """Build a graph from the existing solver library (no shadow
    proposals)."""
    nodes = sorted(
        (
            ShadowNode(
                solver_id=str(s.get("solver_id") or s.get("solver_name") or ""),
                is_live=True,
                source="library",
                cell_id=s.get("cell_id"),
            )
            for s in library_solvers
            if (s.get("solver_id") or s.get("solver_name"))
        ),
        key=lambda n: n.solver_id,
    )
    edges = sorted(
        (
            ShadowEdge(
                solver_a=str(e["solver_a"]),
                solver_b=str(e["solver_b"]),
                relation_type=str(e["relation_type"]),
            )
            for e in (library_edges or [])
            if e.get("relation_type") in VALID_RELATIONS
        ),
        key=lambda e: e.key(),
    )
    return ShadowGraph(
        schema_version=DREAMING_SCHEMA_VERSION,
        nodes=tuple(nodes),
        edges=tuple(edges),
    )


def add_shadow_proposals(
    live: ShadowGraph,
    accepted_proposals: Sequence[dict],
    extra_edges: Sequence[dict] | None = None,
) -> ShadowGraph:
    """Return a new graph with shadow nodes + edges added on top of
    the live graph. Live nodes/edges are preserved verbatim."""
    new_nodes = list(live.nodes)
    seen_ids = live.node_ids()
    for p in accepted_proposals:
        sid = str(p.get("solver_name") or p.get("proposal_id") or "")
        if not sid or sid in seen_ids:
            continue
        new_nodes.append(ShadowNode(
            solver_id=sid, is_live=False, source="shadow_proposal",
            cell_id=p.get("cell_id"),
        ))
        seen_ids.add(sid)

    new_edges = list(live.edges)
    seen_edges = live.edge_keys()
    for e in (extra_edges or []):
        if e.get("relation_type") not in VALID_RELATIONS:
            continue
        edge = ShadowEdge(
            solver_a=str(e["solver_a"]),
            solver_b=str(e["solver_b"]),
            relation_type=str(e["relation_type"]),
        )
        if edge.key() in seen_edges:
            continue
        new_edges.append(edge)
        seen_edges.add(edge.key())

    new_nodes.sort(key=lambda n: n.solver_id)
    new_edges.sort(key=lambda e: e.key())
    return ShadowGraph(
        schema_version=DREAMING_SCHEMA_VERSION,
        nodes=tuple(new_nodes),
        edges=tuple(new_edges),
    )


# ── Structural diff ──────────────────────────────────────────────-

@dataclass(frozen=True)
class StructuralDiff:
    new_nodes: tuple[str, ...]
    new_structural_edges: tuple[tuple[str, str, str], ...]
    new_bridge_candidates: tuple[tuple[str, str, str], ...]
    new_rescale_opportunities: tuple[tuple[str, str, str], ...]
    affected_cells: tuple[str, ...]
    proposal_solver_hashes: tuple[str, ...]


def diff_graphs(live: ShadowGraph, shadow: ShadowGraph,
                proposal_solver_hashes: Sequence[str] = ()) -> StructuralDiff:
    """Compute the structural diff via set operations only."""
    new_node_ids = sorted(shadow.node_ids() - live.node_ids())
    new_edges = sorted(shadow.edge_keys() - live.edge_keys())
    # Bridge candidates = composes_with edges that touch a shadow node
    shadow_only_ids = shadow.shadow_node_ids() - live.node_ids()
    bridge_candidates = sorted(
        e for e in new_edges
        if e[2] == "composes_with" and (
            e[0] in shadow_only_ids or e[1] in shadow_only_ids
        )
    )
    rescale_opportunities = sorted(
        e for e in new_edges if e[2] == "refines"
    )
    affected_cells = sorted({
        n.cell_id for n in shadow.nodes
        if n.solver_id in shadow_only_ids and n.cell_id
    })
    return StructuralDiff(
        new_nodes=tuple(new_node_ids),
        new_structural_edges=tuple(new_edges),
        new_bridge_candidates=tuple(bridge_candidates),
        new_rescale_opportunities=tuple(rescale_opportunities),
        affected_cells=tuple(affected_cells),
        proposal_solver_hashes=tuple(sorted(proposal_solver_hashes)),
    )


# ── Serialization ────────────────────────────────────────────────-

def graph_to_dict(g: ShadowGraph) -> dict:
    return {
        "schema_version": g.schema_version,
        "nodes": [
            {"solver_id": n.solver_id, "is_live": n.is_live,
             "source": n.source, "cell_id": n.cell_id}
            for n in g.nodes
        ],
        "edges": [
            {"solver_a": e.solver_a, "solver_b": e.solver_b,
             "relation_type": e.relation_type}
            for e in g.edges
        ],
    }


def diff_to_dict(d: StructuralDiff) -> dict:
    return {
        "new_nodes": list(d.new_nodes),
        "new_structural_edges": [list(e) for e in d.new_structural_edges],
        "new_bridge_candidates": [list(e) for e in d.new_bridge_candidates],
        "new_rescale_opportunities": [list(e) for e in d.new_rescale_opportunities],
        "affected_cells": list(d.affected_cells),
        "proposal_solver_hashes": list(d.proposal_solver_hashes),
    }
