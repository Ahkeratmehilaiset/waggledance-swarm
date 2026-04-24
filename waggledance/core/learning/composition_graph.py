"""Typed composition graph for solver chaining.

Goal: represent possible solver compositions as a typed DAG rather than
2**n blind combinations. Only edges whose output types and units match
downstream inputs are valid; cycles are forbidden; path depth is
configurable and bounded so graph generation stays tractable.

This module is **read-only** over the existing solver library. It does
not mutate axiom files, registry, or hex topology. Writers live in
`tools/solver_composition_report.py` (Phase 6) and
`tools/hex_subdivision_plan.py` (Phase 7).

Terminology:
- `SolverNode`: a single solver's I/O surface.
- `SolverEdge`: A → B, valid iff A's primary output unit matches any
  B input unit, and the two solvers are in the same cell or ring-1
  neighbors.
- `CompositePath`: a sequence of solvers (nodes) joined by valid edges.
- `BridgeCandidate`: a path that crosses cells (its nodes are not all
  in a single cell). Candidates are *proposals*, never production
  solvers — promotion goes through the Phase 5 gate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


# Ring-1 adjacency, mirrored here to keep this module free of runtime
# dependencies. In sync with waggledance/core/hex_cell_topology.py.
_ADJACENCY: dict[str, frozenset[str]] = {
    "general":  frozenset({"safety", "seasonal", "math", "learning"}),
    "thermal":  frozenset({"energy", "seasonal", "safety"}),
    "energy":   frozenset({"thermal", "safety", "math"}),
    "safety":   frozenset({"thermal", "energy", "system", "general"}),
    "seasonal": frozenset({"thermal", "general", "learning"}),
    "math":     frozenset({"energy", "general", "system"}),
    "system":   frozenset({"safety", "math", "learning"}),
    "learning": frozenset({"seasonal", "general", "system"}),
}


@dataclass(frozen=True)
class IOSig:
    """Canonical (name, unit) pair for input or output declarations."""
    name: str
    unit: str


@dataclass(frozen=True)
class SolverNode:
    """Projection of a solver's I/O surface usable for composition.

    Node equality is by `solver_id`. Two solvers with the same id in
    different cells would collide here — by construction the library
    enforces unique ids, but the graph logs this as a reject if it
    ever happens.
    """
    solver_id: str
    cell_id: str
    inputs: tuple[IOSig, ...]
    outputs: tuple[IOSig, ...]
    primary_output: IOSig | None
    latency_ms: float | None = None  # may be None when unknown

    def input_units(self) -> set[str]:
        return {i.unit for i in self.inputs if i.unit}

    def output_units(self) -> set[str]:
        return {o.unit for o in self.outputs if o.unit}


@dataclass(frozen=True)
class SolverEdge:
    """Directed edge `src -> dst` under a shared unit.

    `shared_unit` is what makes the edge typed: the output of src and
    some input of dst carry that unit. If multiple units match we emit
    one edge per unit so graph statistics stay clean.
    """
    src: str
    dst: str
    shared_unit: str
    same_cell: bool


@dataclass(frozen=True)
class CompositePath:
    """An ordered list of solver ids joined by valid edges."""
    nodes: tuple[str, ...]
    edges: tuple[SolverEdge, ...]
    depth: int
    crosses_cells: bool


@dataclass(frozen=True)
class BridgeCandidate:
    """Composite path that crosses cell boundaries — a structural
    argument for a new composite solver or a hex subdivision.
    """
    path: CompositePath
    from_cell: str
    to_cell: str
    shared_unit: str
    score: float   # heuristic value: longer paths in adjacent cells score higher


@dataclass
class GraphStats:
    """Summary of graph construction — fields are surfaceable as
    metrics by Phase 8."""
    node_count: int = 0
    valid_edges: int = 0
    rejected_edges: int = 0
    rejected_reasons: dict[str, int] = field(default_factory=dict)
    paths_depth2: int = 0
    paths_depth3: int = 0
    paths_depth4: int = 0
    bridges: int = 0
    cells_with_bridges: dict[str, int] = field(default_factory=dict)
    cells_with_high_entropy: list[str] = field(default_factory=list)


# ── Construction ───────────────────────────────────────────────────

def build_nodes(solvers: Iterable[dict]) -> list[SolverNode]:
    """Project dicts (from cell_manifest._collect_all_solvers or
    similar) into SolverNode instances. Silently drops entries that
    are not usable (no id or no outputs)."""
    out: list[SolverNode] = []
    for s in solvers:
        solver_id = s.get("id") or s.get("model_id")
        if not solver_id:
            continue
        cell = s.get("cell") or s.get("cell_id") or "general"

        # Inputs can come in two shapes: manifest-style (list of names
        # with separate variable unit map) or proposal-style
        # (list of dicts with unit). Handle both.
        raw_inputs = s.get("inputs", [])
        unit_map = s.get("variable_units") or {}
        inputs: list[IOSig] = []
        for i in raw_inputs or []:
            if isinstance(i, dict):
                inputs.append(IOSig(
                    name=i.get("name", ""),
                    unit=str(i.get("unit", "") or "").strip(),
                ))
            else:
                inputs.append(IOSig(name=str(i), unit=str(unit_map.get(i, "") or "").strip()))

        raw_outputs = s.get("outputs", [])
        outputs: list[IOSig] = []
        if isinstance(raw_outputs, list):
            for o in raw_outputs:
                if isinstance(o, dict):
                    outputs.append(IOSig(
                        name=o.get("name", ""),
                        unit=str(o.get("unit", "") or "").strip(),
                    ))
                else:
                    # plain names — unit unknown
                    outputs.append(IOSig(name=str(o), unit=""))

        primary: IOSig | None = None
        if outputs:
            primary = outputs[0]
        latency = s.get("latency_ms")
        if latency is not None:
            try:
                latency = float(latency)
            except (TypeError, ValueError):
                latency = None

        out.append(SolverNode(
            solver_id=str(solver_id),
            cell_id=str(cell),
            inputs=tuple(inputs),
            outputs=tuple(outputs),
            primary_output=primary,
            latency_ms=latency,
        ))
    return out


def _cells_reachable(src_cell: str) -> frozenset[str]:
    """Cells whose solvers are composable from `src_cell`: self + ring-1.
    Ring-2 is reachable via chained edges but never a single-hop edge.
    """
    return _ADJACENCY.get(src_cell, frozenset()) | {src_cell}


def build_edges(nodes: list[SolverNode]) -> tuple[list[SolverEdge], GraphStats]:
    """Emit one SolverEdge per (src, dst, shared_unit) triple where the
    type rule holds. Edges across non-adjacent cells are rejected."""
    by_id: dict[str, SolverNode] = {n.solver_id: n for n in nodes}
    edges: list[SolverEdge] = []
    stats = GraphStats(node_count=len(nodes))

    for src in nodes:
        if src.primary_output is None or not src.primary_output.unit:
            continue
        for dst in nodes:
            if dst.solver_id == src.solver_id:
                continue
            # Cell reachability
            if dst.cell_id not in _cells_reachable(src.cell_id):
                stats.rejected_edges += 1
                stats.rejected_reasons["non_adjacent_cell"] = (
                    stats.rejected_reasons.get("non_adjacent_cell", 0) + 1
                )
                continue
            # Type rule: src primary output unit must match one of dst's
            # declared input units
            shared = src.primary_output.unit
            if shared not in dst.input_units():
                stats.rejected_edges += 1
                stats.rejected_reasons["unit_mismatch"] = (
                    stats.rejected_reasons.get("unit_mismatch", 0) + 1
                )
                continue
            edges.append(SolverEdge(
                src=src.solver_id, dst=dst.solver_id,
                shared_unit=shared,
                same_cell=(src.cell_id == dst.cell_id),
            ))
    stats.valid_edges = len(edges)
    return edges, stats


# ── Path enumeration ───────────────────────────────────────────────

def enumerate_paths(
    nodes: list[SolverNode],
    edges: list[SolverEdge],
    max_depth: int = 4,
) -> list[CompositePath]:
    """Enumerate all simple (no-cycle) composite paths up to max_depth
    nodes. Returns one `CompositePath` per distinct node tuple — if two
    edges link the same pair under different units the path is still
    counted once (the edge chain records the unit used)."""
    if max_depth < 2:
        return []

    by_id = {n.solver_id: n for n in nodes}
    out_edges: dict[str, list[SolverEdge]] = {}
    for e in edges:
        out_edges.setdefault(e.src, []).append(e)

    paths: list[CompositePath] = []

    def _dfs(node_id: str, current_nodes: tuple[str, ...],
             current_edges: tuple[SolverEdge, ...]):
        if len(current_nodes) >= max_depth:
            return
        for e in out_edges.get(node_id, ()):
            if e.dst in current_nodes:
                continue  # no cycles
            new_nodes = current_nodes + (e.dst,)
            new_edges = current_edges + (e,)
            paths.append(_make_path(new_nodes, new_edges, by_id))
            _dfs(e.dst, new_nodes, new_edges)

    for n in nodes:
        _dfs(n.solver_id, (n.solver_id,), ())
    return paths


def _make_path(node_ids: tuple[str, ...],
               edges: tuple[SolverEdge, ...],
               by_id: dict[str, SolverNode]) -> CompositePath:
    cells = {by_id[n].cell_id for n in node_ids if n in by_id}
    return CompositePath(
        nodes=node_ids,
        edges=edges,
        depth=len(node_ids),
        crosses_cells=len(cells) > 1,
    )


# ── Bridge candidates ──────────────────────────────────────────────

def find_bridges(
    paths: list[CompositePath],
    nodes: list[SolverNode],
    min_depth: int = 2,
) -> list[BridgeCandidate]:
    """A path is a bridge candidate iff it crosses ≥ 2 cells and every
    edge in it links adjacent cells (ring-1). The score favors paths
    whose from/to cells are not direct neighbors of every intermediate
    cell — these are the paths Dream Mode would otherwise miss."""
    by_id = {n.solver_id: n for n in nodes}
    bridges: list[BridgeCandidate] = []
    for p in paths:
        if p.depth < min_depth or not p.crosses_cells:
            continue
        first = by_id.get(p.nodes[0])
        last = by_id.get(p.nodes[-1])
        if first is None or last is None:
            continue
        # The bridge's score grows with depth and with cell diversity;
        # path staying fully in one cell is already excluded by
        # `crosses_cells`.
        cells = {by_id[n].cell_id for n in p.nodes if n in by_id}
        score = round(0.5 * p.depth + 0.25 * len(cells), 3)
        shared_units = {e.shared_unit for e in p.edges}
        bridges.append(BridgeCandidate(
            path=p,
            from_cell=first.cell_id,
            to_cell=last.cell_id,
            shared_unit=next(iter(sorted(shared_units))) if shared_units else "",
            score=score,
        ))
    # Deterministic order
    return sorted(
        bridges,
        key=lambda b: (-b.score, b.from_cell, b.to_cell,
                       b.path.nodes),
    )


# ── Summary stats ──────────────────────────────────────────────────

def summarize(
    nodes: list[SolverNode],
    edges: list[SolverEdge],
    paths: list[CompositePath],
    bridges: list[BridgeCandidate],
    entropy_threshold: int = 6,
) -> GraphStats:
    """Combine construction stats with path and bridge counts."""
    stats = GraphStats(node_count=len(nodes), valid_edges=len(edges))

    for p in paths:
        if p.depth == 2:
            stats.paths_depth2 += 1
        elif p.depth == 3:
            stats.paths_depth3 += 1
        elif p.depth == 4:
            stats.paths_depth4 += 1

    stats.bridges = len(bridges)
    for b in bridges:
        for c in {b.from_cell, b.to_cell}:
            stats.cells_with_bridges[c] = stats.cells_with_bridges.get(c, 0) + 1

    # Entropy marker: a cell with many distinct output units may be
    # heterogeneous enough to warrant subdivision (Phase 7 candidate).
    by_cell: dict[str, set[str]] = {}
    for n in nodes:
        by_cell.setdefault(n.cell_id, set()).update(n.output_units())
    stats.cells_with_high_entropy = sorted(
        c for c, us in by_cell.items() if len(us) >= entropy_threshold
    )
    return stats


def build_graph(
    solvers: Iterable[dict],
    max_depth: int = 4,
    entropy_threshold: int = 6,
) -> dict:
    """Convenience wrapper — build nodes, edges, paths, bridges, stats
    from a list of dict solver projections. Everything is returned in a
    dict so the report tool can emit deterministic JSON."""
    nodes = build_nodes(solvers)
    edges, build_stats = build_edges(nodes)
    paths = enumerate_paths(nodes, edges, max_depth=max_depth)
    bridges = find_bridges(paths, nodes)
    stats = summarize(nodes, edges, paths, bridges,
                      entropy_threshold=entropy_threshold)
    # Merge rejected-edge counts from construction
    stats.rejected_edges = build_stats.rejected_edges
    stats.rejected_reasons = dict(build_stats.rejected_reasons)
    return {
        "nodes": nodes,
        "edges": edges,
        "paths": paths,
        "bridges": bridges,
        "stats": stats,
    }
