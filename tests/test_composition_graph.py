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
    IOSig, SolverNode, SolverEdge,
    build_nodes, build_edges, enumerate_paths,
    find_bridges, summarize, build_graph,
    _ADJACENCY,
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
