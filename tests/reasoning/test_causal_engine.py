# SPDX-License-Identifier: Apache-2.0
"""Direct unit tests for waggledance.core.reasoning.causal_engine.CausalEngine.

Codex test-coverage scout flagged this file as Candidate 2 (medium risk):
``CausalReasonerAdapter`` exposes ``CausalEngine`` as the ``solve.causal``
capability, but no test imports the engine directly — adapter smoke tests
use a no-graph instance, leaving the graph-backed branches and the
silent-exception-fallback paths uncovered.

These tests use a deterministic fake graph object that mimics
``CognitiveGraph``'s six methods. Each branch (happy path, no-graph,
graph-method-raises) is asserted explicitly so that the silent-fallback
contract — "return empty/None instead of crashing the capability layer" —
cannot regress unnoticed.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

import pytest

from waggledance.core.reasoning.causal_engine import (
    CausalChain,
    CausalEngine,
    ImpactEstimate,
)


# --- fake graph ------------------------------------------------------

class _FakeGraph:
    """Minimal in-memory graph implementing the six methods CausalEngine calls.

    Any attribute set to ``"raise"`` in ``raise_on`` causes that method to
    throw ``RuntimeError`` — used to verify CausalEngine's silent-fallback
    contract.
    """

    def __init__(
        self,
        paths: Dict[Tuple[str, str], List[str]] | None = None,
        edges: Dict[str, List[Dict[str, Any]]] | None = None,
        dependents: Dict[str, List[Tuple[str, int]]] | None = None,
        ancestors: Dict[str, List[Tuple[str, int]]] | None = None,
        nodes: Dict[str, Dict[str, Any]] | None = None,
        graph_stats: Dict[str, Any] | None = None,
        raise_on: set[str] | None = None,
    ):
        self._paths = paths or {}
        self._edges = edges or {}
        self._dependents = dependents or {}
        self._ancestors = ancestors or {}
        self._nodes = nodes or {}
        self._stats = graph_stats or {"nodes": 0, "edges": 0}
        self._raise_on = raise_on or set()

    def shortest_path(self, source: str, target: str):
        if "shortest_path" in self._raise_on:
            raise RuntimeError("graph error")
        return self._paths.get((source, target))

    def get_edges(self, entity: str):
        if "get_edges" in self._raise_on:
            raise RuntimeError("graph error")
        return self._edges.get(entity, [])

    def find_dependents(self, entity: str, max_depth: int = 3):
        if "find_dependents" in self._raise_on:
            raise RuntimeError("graph error")
        return self._dependents.get(entity, [])

    def find_ancestors(self, entity: str, max_depth: int = 5):
        if "find_ancestors" in self._raise_on:
            raise RuntimeError("graph error")
        return self._ancestors.get(entity, [])

    def get_node(self, entity: str):
        if "get_node" in self._raise_on:
            raise RuntimeError("graph error")
        return self._nodes.get(entity)

    def stats(self):
        if "stats" in self._raise_on:
            raise RuntimeError("graph error")
        return self._stats


# --- engine wiring ---------------------------------------------------
#
# Codex review of the first version of this file (PR #102) flagged that
# `CausalEngine(cognitive_graph=None)` does NOT actually produce a no-graph
# engine in this repo, because `core.cognitive_graph` imports successfully —
# so `_get_graph_class()` returns `CognitiveGraph`, and the constructor
# instantiates a real graph instead of leaving `_graph = None`. Guarding
# the fallback assertions with `if engine._graph is None:` made those
# tests silent no-ops in this repo.
#
# Fix: force the no-graph branch via the `no_graph_engine` fixture, which
# monkeypatches `_get_graph_class` to return None for the duration of the
# test. We then assert `engine._graph is None` up front so the fallback
# tests can never silently degrade again.


@pytest.fixture()
def no_graph_engine(monkeypatch):
    """Force CausalEngine to take the no-graph constructor branch.

    Without this, `CausalEngine(cognitive_graph=None)` would still build a
    real CognitiveGraph because `_get_graph_class()` succeeds in this repo.
    """
    monkeypatch.setattr(
        "waggledance.core.reasoning.causal_engine._get_graph_class",
        lambda: None,
    )
    engine = CausalEngine(cognitive_graph=None)
    assert engine._graph is None, "fixture must produce a no-graph engine"
    return engine


def test_engine_without_graph_reports_unavailable(no_graph_engine):
    assert no_graph_engine.available is False


def test_find_causal_chain_no_graph_returns_none(no_graph_engine):
    assert no_graph_engine.find_causal_chain("a", "b") is None


def test_estimate_impact_no_graph_returns_empty_estimate(no_graph_engine):
    result = no_graph_engine.estimate_impact("a")
    assert isinstance(result, ImpactEstimate)
    assert result.affected_entities == []
    assert result.impact_scores == {}
    assert result.total_impact == 0.0


def test_find_root_causes_no_graph_returns_empty_list(no_graph_engine):
    assert no_graph_engine.find_root_causes("a") == []


def test_get_entity_context_no_graph_reports_unavailable(no_graph_engine):
    ctx = no_graph_engine.get_entity_context("a")
    assert ctx == {"entity": "a", "available": False}


# --- find_causal_chain (graph-backed) --------------------------------

def test_find_causal_chain_returns_chain_with_decayed_strength():
    graph = _FakeGraph(
        paths={("a", "c"): ["a", "b", "c"]},
        edges={
            "a": [{"target": "b", "link_type": "causes"}],
            "b": [{"target": "c", "link_type": "amplifies"}],
        },
    )
    engine = CausalEngine(cognitive_graph=graph)
    chain = engine.find_causal_chain("a", "c")
    assert isinstance(chain, CausalChain)
    assert chain.path == ["a", "b", "c"]
    assert chain.link_types == ["causes", "amplifies"]
    assert chain.depth == 2
    # strength = 0.9 ** (3 - 1) = 0.81
    assert chain.strength == pytest.approx(0.81)


def test_find_causal_chain_returns_none_when_no_path():
    graph = _FakeGraph(paths={})
    engine = CausalEngine(cognitive_graph=graph)
    assert engine.find_causal_chain("a", "z") is None


def test_find_causal_chain_silent_fallback_when_shortest_path_raises():
    graph = _FakeGraph(raise_on={"shortest_path"})
    engine = CausalEngine(cognitive_graph=graph)
    # Contract: graph errors must not crash the capability — return None.
    assert engine.find_causal_chain("a", "z") is None


def test_find_causal_chain_unknown_link_type_when_edge_missing():
    graph = _FakeGraph(
        paths={("a", "c"): ["a", "b", "c"]},
        edges={"a": [], "b": []},  # no edges => "unknown" filler
    )
    engine = CausalEngine(cognitive_graph=graph)
    chain = engine.find_causal_chain("a", "c")
    assert chain.link_types == ["unknown", "unknown"]


# --- estimate_impact -------------------------------------------------

def test_estimate_impact_decays_exponentially_per_depth():
    graph = _FakeGraph(
        dependents={"x": [("d1", 1), ("d2", 2), ("d3", 3)]},
    )
    engine = CausalEngine(cognitive_graph=graph)
    result = engine.estimate_impact("x", change_magnitude=1.0, max_depth=3)
    # 0.7^1 + 0.7^2 + 0.7^3 = 0.7 + 0.49 + 0.343 = 1.533
    assert result.impact_scores["d1"] == pytest.approx(0.7)
    assert result.impact_scores["d2"] == pytest.approx(0.49)
    assert result.impact_scores["d3"] == pytest.approx(0.343)
    assert result.total_impact == pytest.approx(1.533)
    assert result.max_depth == 3


def test_estimate_impact_silent_fallback_when_find_dependents_raises():
    graph = _FakeGraph(raise_on={"find_dependents"})
    engine = CausalEngine(cognitive_graph=graph)
    result = engine.estimate_impact("x")
    assert result.affected_entities == []
    assert result.total_impact == 0.0
    assert result.max_depth == 0


def test_estimate_impact_scales_with_change_magnitude():
    graph = _FakeGraph(dependents={"x": [("d", 1)]})
    engine = CausalEngine(cognitive_graph=graph)
    result = engine.estimate_impact("x", change_magnitude=2.5)
    assert result.impact_scores["d"] == pytest.approx(2.5 * 0.7)


# --- find_root_causes ------------------------------------------------

def test_find_root_causes_returns_ancestors():
    graph = _FakeGraph(ancestors={"y": [("a", 1), ("b", 2)]})
    engine = CausalEngine(cognitive_graph=graph)
    assert engine.find_root_causes("y") == [("a", 1), ("b", 2)]


def test_find_root_causes_silent_fallback_when_find_ancestors_raises():
    graph = _FakeGraph(raise_on={"find_ancestors"})
    engine = CausalEngine(cognitive_graph=graph)
    # Contract: graph errors must not crash — return [].
    assert engine.find_root_causes("y") == []


# --- what_if --------------------------------------------------------

def test_what_if_propagates_positive_delta_to_baseline():
    graph = _FakeGraph(dependents={"x": [("d", 1)]})
    engine = CausalEngine(cognitive_graph=graph)
    # delta = 5 - 0 = 5; impact = 5 * 0.7 = 3.5; projected = 10 + 1.0 * 3.5 = 13.5
    projected = engine.what_if("x", new_value=5.0, baselines={"x": 0.0, "d": 10.0})
    assert projected["d"] == pytest.approx(13.5)


def test_what_if_negative_delta_subtracts_impact():
    graph = _FakeGraph(dependents={"x": [("d", 1)]})
    engine = CausalEngine(cognitive_graph=graph)
    # delta = 0 - 5 = -5; impact uses abs(delta)=5 * 0.7 = 3.5; direction=-1
    # projected = 10 + (-1) * 3.5 = 6.5
    projected = engine.what_if("x", new_value=0.0, baselines={"x": 5.0, "d": 10.0})
    assert projected["d"] == pytest.approx(6.5)


def test_what_if_uses_zero_baseline_when_entity_missing():
    graph = _FakeGraph(dependents={"x": [("d", 1)]})
    engine = CausalEngine(cognitive_graph=graph)
    # baselines empty => entity baseline = 0, dep baseline = 0
    # delta = 3 - 0 = 3; impact = 3 * 0.7 = 2.1; projected = 0 + 1.0 * 2.1 = 2.1
    projected = engine.what_if("x", new_value=3.0)
    assert projected["d"] == pytest.approx(2.1)


# --- get_entity_context ---------------------------------------------

def test_get_entity_context_aggregates_node_edges_ancestors_dependents():
    graph = _FakeGraph(
        nodes={"e": {"name": "Entity", "kind": "concept"}},
        edges={"e": [{"target": "f", "link_type": "causes"}]},
        ancestors={"e": [("a", 1)]},
        dependents={"e": [("d", 1)]},
    )
    engine = CausalEngine(cognitive_graph=graph)
    ctx = engine.get_entity_context("e")
    assert ctx["available"] is True
    assert ctx["attributes"] == {"name": "Entity", "kind": "concept"}
    assert ctx["edges"] == [{"target": "f", "link_type": "causes"}]
    assert ctx["ancestors"] == [("a", 1)]
    assert ctx["dependents"] == [("d", 1)]


def test_get_entity_context_uses_empty_dict_when_node_missing():
    graph = _FakeGraph(nodes={}, edges={}, ancestors={}, dependents={})
    engine = CausalEngine(cognitive_graph=graph)
    ctx = engine.get_entity_context("nope")
    assert ctx["attributes"] == {}
    assert ctx["edges"] == []


def test_get_entity_context_silent_fallback_for_ancestors_and_dependents():
    graph = _FakeGraph(
        nodes={"e": {}},
        edges={"e": []},
        raise_on={"find_ancestors", "find_dependents"},
    )
    engine = CausalEngine(cognitive_graph=graph)
    ctx = engine.get_entity_context("e")
    # Both raises must downgrade to empty lists, not crash.
    assert ctx["ancestors"] == []
    assert ctx["dependents"] == []


# --- stats ----------------------------------------------------------

def test_stats_with_graph_reports_available_and_passes_through():
    graph = _FakeGraph(graph_stats={"nodes": 7, "edges": 12})
    engine = CausalEngine(cognitive_graph=graph)
    s = engine.stats()
    assert s["graph_available"] is True
    assert s["graph_stats"] == {"nodes": 7, "edges": 12}
    assert s["queries_executed"] == 0


def test_stats_increments_queries_executed_after_call():
    graph = _FakeGraph(
        paths={("a", "b"): ["a", "b"]},
        edges={"a": [{"target": "b", "link_type": "x"}]},
    )
    engine = CausalEngine(cognitive_graph=graph)
    engine.find_causal_chain("a", "b")
    engine.find_causal_chain("a", "b")
    assert engine.stats()["queries_executed"] == 2
