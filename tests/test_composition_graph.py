"""Tests for composition_graph + solver_composition_report.

Focused on:
- edges are typed (unit rule enforced)
- no cycles
- max_depth respected
- bridge candidates only cross adjacent cells
- report is deterministic given identical inputs
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent.parent


from waggledance.core.learning.composition_graph import (
    IOSig, SolverNode, SolverEdge, RescaleEdge,
    build_nodes, build_edges, enumerate_paths,
    find_bridges, find_rescale_edges, summarize, build_graph,
    _ADJACENCY, _UNIT_FAMILIES, _unit_family, _rescale_factor,
)


# ── Fixtures ──────────────────────────────────────────────────────

def _solver(id_, cell, inputs, outputs):
    return {
        "id": id_,
        "cell": cell,
        "inputs": [{"name": n, "unit": u} for n, u in inputs],
        "outputs": [{"name": n, "unit": u} for n, u in outputs],
    }


# ── Nodes ─────────────────────────────────────────────────────────

def test_build_nodes_projects_correctly():
    solvers = [_solver("a", "thermal", [("x", "m")], [("y", "W")])]
    nodes = build_nodes(solvers)
    assert len(nodes) == 1
    n = nodes[0]
    assert n.solver_id == "a"
    assert n.cell_id == "thermal"
    assert n.primary_output.unit == "W"


def test_build_nodes_drops_incomplete_entries():
    bad = [
        {"id": "ok", "cell": "thermal", "outputs": [{"name": "z", "unit": "W"}]},
        {"cell": "thermal"},  # no id
        {},
    ]
    assert len(build_nodes(bad)) == 1


def test_primary_output_respects_explicit_flag():
    """GPT R4: build_nodes must prefer the output marked primary=True
    over the first output in the list."""
    s = {
        "id": "s", "cell": "thermal",
        "inputs": [{"name": "x", "unit": "m"}],
        "outputs": [
            {"name": "aux", "unit": "ratio"},
            {"name": "main", "unit": "W", "primary": True},
            {"name": "other", "unit": "degC"},
        ],
    }
    nodes = build_nodes([s])
    assert nodes[0].primary_output.name == "main"
    assert nodes[0].primary_output.unit == "W"


def test_multi_primary_fails_closed_to_no_primary():
    """GPT R5 §4 / Q6: multi-primary library data must fail closed —
    no primary_output → no outgoing runtime or advisory edges."""
    s = {
        "id": "s", "cell": "thermal",
        "inputs": [{"name": "x", "unit": "m"}],
        "outputs": [
            {"name": "first", "unit": "W", "primary": True},
            {"name": "second", "unit": "kWh", "primary": True},  # INVALID
        ],
    }
    nodes = build_nodes([s])
    assert nodes[0].primary_output is None


def test_multi_primary_produces_no_runtime_edges():
    """A multi-primary node must not anchor any outgoing SolverEdge."""
    bad = {
        "id": "bad", "cell": "thermal",
        "inputs": [{"name": "x", "unit": "m"}],
        "outputs": [
            {"name": "a", "unit": "W", "primary": True},
            {"name": "b", "unit": "W", "primary": True},
        ],
    }
    consumer = {
        "id": "ok", "cell": "thermal",
        "inputs": [{"name": "p", "unit": "W"}],
        "outputs": [{"name": "out", "unit": "ratio", "primary": True}],
    }
    nodes = build_nodes([bad, consumer])
    edges, _ = build_edges(nodes)
    # bad has no primary → no edge bad→ok even though W would match
    assert not any(e.src == "bad" for e in edges)


def test_multi_primary_produces_no_rescale_edges():
    bad = {
        "id": "bad", "cell": "thermal",
        "inputs": [{"name": "x", "unit": "m"}],
        "outputs": [
            {"name": "a", "unit": "W", "primary": True},
            {"name": "b", "unit": "W", "primary": True},
        ],
    }
    consumer = {
        "id": "ok", "cell": "thermal",
        "inputs": [{"name": "p", "unit": "kW"}],  # unit-family match
        "outputs": [{"name": "out", "unit": "ratio", "primary": True}],
    }
    nodes = build_nodes([bad, consumer])
    rescales = find_rescale_edges(nodes)
    assert not any(r.src == "bad" for r in rescales)


def test_primary_output_falls_back_to_first_when_no_flag():
    """When no output carries primary=True, the first output is used as
    a legacy compatibility fallback."""
    s = {
        "id": "s", "cell": "thermal",
        "inputs": [{"name": "x", "unit": "m"}],
        "outputs": [
            {"name": "first", "unit": "W"},
            {"name": "second", "unit": "kWh"},
        ],
    }
    nodes = build_nodes([s])
    assert nodes[0].primary_output.name == "first"


# ── Advisory rescale edges (GPT R4 advisory) ──────────────────────

def test_unit_family_lookup():
    assert _unit_family("W") == "power"
    assert _unit_family("kW") == "power"
    assert _unit_family("m") == "length"
    assert _unit_family("not-a-unit") is None
    assert _unit_family("") is None


def test_rescale_factor_power():
    # 1 kW = 1000 W, so factor converting a W-value to kW is 1/1000
    assert _rescale_factor("W", "kW") == 1e-3
    assert _rescale_factor("kW", "W") == 1e3


def test_rescale_factor_none_for_cross_family():
    assert _rescale_factor("W", "m") is None
    assert _rescale_factor("degC", "W") is None  # degC not in any family


def test_rescale_factor_same_unit_is_one():
    assert _rescale_factor("W", "W") == 1.0


def test_find_rescale_edge_detects_cross_unit_same_family():
    solvers = [
        _solver("a", "thermal", [("x", "m")], [("y", "W")]),
        _solver("b", "thermal", [("p", "kW")], [("q", "ratio")]),
    ]
    nodes = build_nodes(solvers)
    rescales = find_rescale_edges(nodes)
    assert rescales, "expected at least one W→kW rescale edge"
    r = rescales[0]
    assert r.src == "a" and r.dst == "b"
    assert r.src_unit == "W" and r.dst_unit == "kW"
    assert r.family == "power"
    assert r.factor == 1e-3


def test_find_rescale_edge_not_between_different_families():
    solvers = [
        _solver("a", "thermal", [("x", "m")], [("y", "W")]),
        _solver("b", "thermal", [("p", "m")], [("q", "ratio")]),
    ]
    nodes = build_nodes(solvers)
    rescales = find_rescale_edges(nodes)
    assert not rescales  # output W vs input m — different families


def test_find_rescale_edge_skips_exact_match():
    """Exact-unit chains are already SolverEdges, not rescale edges."""
    solvers = [
        _solver("a", "thermal", [("x", "m")], [("y", "W")]),
        _solver("b", "thermal", [("p", "W")], [("q", "ratio")]),
    ]
    nodes = build_nodes(solvers)
    rescales = find_rescale_edges(nodes)
    assert not rescales  # exact match → SolverEdge, not RescaleEdge


def test_rescale_edges_do_not_enter_runtime_paths():
    """The key advisory guarantee: enumerate_paths MUST NOT pick up
    rescale edges. Runtime graph is exact-unit-match only."""
    solvers = [
        _solver("a", "thermal", [("x", "m")], [("y", "W")]),
        _solver("b", "thermal", [("p", "kW")], [("q", "ratio")]),
    ]
    nodes = build_nodes(solvers)
    edges, _ = build_edges(nodes)
    paths = enumerate_paths(nodes, edges, max_depth=3)
    # No runtime edge a→b exists (W ≠ kW)
    assert not edges
    # But rescale advice exists
    assert find_rescale_edges(nodes)
    # And paths do not include the rescale edge
    for p in paths:
        assert len(p.nodes) == 1 or edges


def test_build_graph_returns_rescale_edges():
    solvers = [
        _solver("a", "thermal", [("x", "m")], [("y", "W")]),
        _solver("b", "thermal", [("p", "kW")], [("q", "ratio")]),
    ]
    result = build_graph(solvers)
    assert "rescale_edges" in result
    assert len(result["rescale_edges"]) >= 1
    assert result["stats"].advisory_rescale_edges >= 1
    assert "power" in result["stats"].advisory_rescale_by_family


def test_rescale_edges_deterministic_order():
    solvers = []
    for i, u in enumerate(["W", "kW", "mW", "MW"]):
        solvers.append(_solver(f"s{i}", "thermal", [("in", u)], [("out", u)]))
    # Between these nodes many rescale edges will form
    nodes = build_nodes(solvers)
    r1 = find_rescale_edges(nodes)
    r2 = find_rescale_edges(nodes)
    assert r1 == r2
    # Sort key: (family, src, dst, src_unit, dst_unit)
    for a, b in zip(r1, r1[1:]):
        assert (a.family, a.src, a.dst, a.src_unit, a.dst_unit) <= \
               (b.family, b.src, b.dst, b.src_unit, b.dst_unit)


def test_rescale_edge_respects_cell_adjacency():
    # math ↮ thermal → no rescale edges across non-adjacent cells
    solvers = [
        _solver("m", "math", [("x", "m")], [("y", "W")]),
        _solver("t", "thermal", [("p", "kW")], [("q", "ratio")]),
    ]
    nodes = build_nodes(solvers)
    assert not find_rescale_edges(nodes)


def test_primary_output_stable_under_output_order_change():
    """Order of outputs must not silently change graph primary when the
    same one carries primary=True."""
    s_a = {
        "id": "s", "cell": "thermal",
        "inputs": [{"name": "x", "unit": "m"}],
        "outputs": [
            {"name": "main", "unit": "W", "primary": True},
            {"name": "aux", "unit": "ratio"},
        ],
    }
    s_b = {
        "id": "s", "cell": "thermal",
        "inputs": [{"name": "x", "unit": "m"}],
        "outputs": [
            {"name": "aux", "unit": "ratio"},
            {"name": "main", "unit": "W", "primary": True},
        ],
    }
    na = build_nodes([s_a])[0]
    nb = build_nodes([s_b])[0]
    assert na.primary_output.name == nb.primary_output.name == "main"


# ── Edges: unit rule ──────────────────────────────────────────────

def test_edge_emitted_when_output_unit_matches_input_unit():
    solvers = [
        _solver("a", "thermal", [("x", "m")], [("y", "W")]),
        _solver("b", "thermal", [("p", "W")], [("q", "ratio")]),
    ]
    nodes = build_nodes(solvers)
    edges, stats = build_edges(nodes)
    assert any(e.src == "a" and e.dst == "b" and e.shared_unit == "W" for e in edges)


def test_edge_rejected_on_unit_mismatch():
    solvers = [
        _solver("a", "thermal", [("x", "m")], [("y", "W")]),
        _solver("b", "thermal", [("p", "kWh")], [("q", "ratio")]),
    ]
    nodes = build_nodes(solvers)
    edges, stats = build_edges(nodes)
    assert stats.rejected_reasons.get("unit_mismatch", 0) >= 1
    assert not edges


def test_edge_rejected_across_non_adjacent_cells():
    # math and thermal are not adjacent in _ADJACENCY
    assert "thermal" not in _ADJACENCY.get("math", set())
    solvers = [
        _solver("m", "math", [("x", "m")], [("y", "W")]),
        _solver("t", "thermal", [("p", "W")], [("q", "ratio")]),
    ]
    nodes = build_nodes(solvers)
    edges, stats = build_edges(nodes)
    assert not edges
    assert stats.rejected_reasons.get("non_adjacent_cell", 0) >= 1


def test_edge_allowed_within_same_cell():
    solvers = [
        _solver("a", "thermal", [("x", "m")], [("y", "W")]),
        _solver("b", "thermal", [("p", "W")], [("q", "ratio")]),
    ]
    nodes = build_nodes(solvers)
    edges, _ = build_edges(nodes)
    assert any(e.same_cell for e in edges)


def test_edge_allowed_into_ring1_neighbor():
    # thermal's ring-1 includes energy
    assert "energy" in _ADJACENCY["thermal"]
    solvers = [
        _solver("t", "thermal", [("x", "m")], [("y", "W")]),
        _solver("e", "energy", [("p", "W")], [("q", "ratio")]),
    ]
    nodes = build_nodes(solvers)
    edges, _ = build_edges(nodes)
    assert any(e.src == "t" and e.dst == "e" for e in edges)


# ── No cycles ─────────────────────────────────────────────────────

def test_no_cycles_in_paths():
    # Create a 2-cycle in valid units but verify enumerate_paths skips it
    solvers = [
        _solver("a", "thermal", [("p", "W")], [("y", "W")]),
        _solver("b", "thermal", [("x", "W")], [("z", "W")]),
    ]
    nodes = build_nodes(solvers)
    edges, _ = build_edges(nodes)
    # a→b and b→a both exist by unit rule
    srcs = {(e.src, e.dst) for e in edges}
    assert ("a", "b") in srcs and ("b", "a") in srcs

    paths = enumerate_paths(nodes, edges, max_depth=4)
    for p in paths:
        # No solver id should repeat within a single path
        assert len(set(p.nodes)) == len(p.nodes)


# ── Max depth ─────────────────────────────────────────────────────

def test_max_depth_respected():
    # Build a linear chain of 6 solvers, each step unit-compatible
    solvers = []
    for i in range(6):
        solvers.append(_solver(
            f"s{i}", "thermal",
            inputs=[("in", "U")],
            outputs=[("out", "U")],
        ))
    nodes = build_nodes(solvers)
    edges, _ = build_edges(nodes)

    for depth in (2, 3, 4):
        paths = enumerate_paths(nodes, edges, max_depth=depth)
        assert all(p.depth <= depth for p in paths)


# ── Bridges ───────────────────────────────────────────────────────

def test_bridges_cross_cells():
    solvers = [
        _solver("t", "thermal", [("a", "m")], [("y", "W")]),
        _solver("e", "energy", [("p", "W")], [("q", "kWh")]),
    ]
    nodes = build_nodes(solvers)
    edges, _ = build_edges(nodes)
    paths = enumerate_paths(nodes, edges, max_depth=3)
    bridges = find_bridges(paths, nodes)
    assert bridges
    for b in bridges:
        assert b.path.crosses_cells
        assert b.from_cell != b.to_cell


def test_no_bridges_when_all_same_cell():
    solvers = [
        _solver("a", "thermal", [("x", "W")], [("y", "W")]),
        _solver("b", "thermal", [("p", "W")], [("q", "ratio")]),
    ]
    nodes = build_nodes(solvers)
    edges, _ = build_edges(nodes)
    paths = enumerate_paths(nodes, edges, max_depth=3)
    bridges = find_bridges(paths, nodes)
    assert not bridges


# ── Stats and summary ─────────────────────────────────────────────

def test_summary_counts_paths_by_depth():
    solvers = [
        _solver("a", "thermal", [("i", "W")], [("o", "W")]),
        _solver("b", "thermal", [("i", "W")], [("o", "W")]),
        _solver("c", "thermal", [("i", "W")], [("o", "W")]),
    ]
    nodes = build_nodes(solvers)
    edges, _ = build_edges(nodes)
    paths = enumerate_paths(nodes, edges, max_depth=4)
    bridges = find_bridges(paths, nodes)
    stats = summarize(nodes, edges, paths, bridges)
    assert stats.node_count == 3
    assert stats.paths_depth2 + stats.paths_depth3 >= 1


def test_high_entropy_flag():
    # One cell with 7 distinct output units
    solvers = []
    for i, u in enumerate(["W", "kW", "ratio", "degC", "kWh", "Pa", "m3"]):
        solvers.append(_solver(f"s{i}", "thermal", [("a", "m")], [("o", u)]))
    result = build_graph(solvers, entropy_threshold=6)
    assert "thermal" in result["stats"].cells_with_high_entropy


# ── End-to-end build_graph ────────────────────────────────────────

def test_build_graph_deterministic():
    solvers = [
        _solver("a", "thermal", [("x", "m")], [("y", "W")]),
        _solver("b", "energy",  [("p", "W")], [("q", "kWh")]),
    ]
    r1 = build_graph(solvers)
    r2 = build_graph(solvers)
    # Node and edge lists must match exactly
    assert [n.solver_id for n in r1["nodes"]] == [n.solver_id for n in r2["nodes"]]
    assert [(e.src, e.dst, e.shared_unit) for e in r1["edges"]] == \
           [(e.src, e.dst, e.shared_unit) for e in r2["edges"]]
    assert [b.path.nodes for b in r1["bridges"]] == \
           [b.path.nodes for b in r2["bridges"]]


# ── solver_composition_report CLI ─────────────────────────────────

def _load_report_tool():
    path = ROOT / "tools" / "solver_composition_report.py"
    spec = importlib.util.spec_from_file_location("solver_composition_report", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["solver_composition_report"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_report_generation_is_deterministic_modulo_timestamp(tmp_path):
    rt = _load_report_tool()
    # Seed an axioms dir with two trivial YAMLs
    axioms = tmp_path / "axioms" / "x"
    axioms.mkdir(parents=True)
    (axioms / "a.yaml").write_text(yaml.safe_dump({
        "model_id": "a",
        "cell_id": "thermal",
        "variables": {"x": {"unit": "m"}},
        "solver_output_schema": {"primary_value": {"name": "y", "unit": "W"}},
        "formulas": [{"name": "y", "formula": "x", "output_unit": "W"}],
    }), encoding="utf-8")
    (axioms / "b.yaml").write_text(yaml.safe_dump({
        "model_id": "b",
        "cell_id": "thermal",
        "variables": {"p": {"unit": "W"}},
        "solver_output_schema": {"primary_value": {"name": "q", "unit": "ratio"}},
        "formulas": [{"name": "q", "formula": "p", "output_unit": "ratio"}],
    }), encoding="utf-8")

    r1 = rt.run(tmp_path / "axioms", tmp_path / "r1.md")
    r2 = rt.run(tmp_path / "axioms", tmp_path / "r2.md")
    assert r1["summary"] == r2["summary"]
    # Reports match except for the one "Generated:" timestamp line
    def _strip_ts(text):
        return "\n".join(
            line for line in text.splitlines()
            if not line.lstrip().startswith("- **Generated:**")
        )
    assert _strip_ts((tmp_path / "r1.md").read_text("utf-8")) == \
           _strip_ts((tmp_path / "r2.md").read_text("utf-8"))


def test_report_has_no_secrets_no_absolute_paths(tmp_path):
    rt = _load_report_tool()
    axioms = tmp_path / "axioms" / "x"
    axioms.mkdir(parents=True)
    (axioms / "a.yaml").write_text(yaml.safe_dump({
        "model_id": "a",
        "cell_id": "thermal",
        "variables": {"x": {"unit": "m"}},
        "solver_output_schema": {"primary_value": {"name": "y", "unit": "W"}},
        "formulas": [{"name": "y", "formula": "x", "output_unit": "W"}],
    }), encoding="utf-8")
    rt.run(tmp_path / "axioms", tmp_path / "report.md")
    text = (tmp_path / "report.md").read_text("utf-8")
    assert "WAGGLE_API_KEY" not in text
    assert "gnt_" not in text
    assert "C:\\Users" not in text
