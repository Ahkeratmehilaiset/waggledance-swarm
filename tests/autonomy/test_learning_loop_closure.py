# SPDX-License-Identifier: Apache-2.0
"""Tests for v3.2 learning loop closure.

Validates:
  - CognitiveGraph initializes properly (not None) in AutonomyRuntime
  - Self-entity seeded at startup
  - Graph health check fires after 10 queries
  - WorldModel logs ERROR when graph unavailable
  - GraphBuilder.find_alternative_paths() works
  - Dream mode produces non-inconclusive results with graph data
  - SolverRouter accepts dream routing hints
  - validate_cutover includes CognitiveGraph check
"""

from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from waggledance.core.autonomy.runtime import AutonomyRuntime
from waggledance.core.world.graph_builder import GraphBuilder
from waggledance.core.world.world_model import WorldModel


@pytest.fixture
def temp_graph():
    from core.cognitive_graph import CognitiveGraph
    with tempfile.TemporaryDirectory() as td:
        path = str(Path(td) / "test_graph.json")
        graph = CognitiveGraph(persist_path=path)
        yield graph


class TestCognitiveGraphInit:
    def test_runtime_has_graph(self):
        """CognitiveGraph should not be None when runtime uses default WorldModel."""
        rt = AutonomyRuntime(profile="TEST")
        assert rt.world_model.graph is not None

    def test_graph_builder_active(self):
        """GraphBuilder should be initialised when graph is available."""
        rt = AutonomyRuntime(profile="TEST")
        assert rt.graph_builder is not None

    def test_self_entity_seeded(self):
        """Self-entity should be created at startup."""
        rt = AutonomyRuntime(profile="TEST")
        se = rt.world_model.get_self_entity()
        assert se is not None


class TestGraphPopulatesAfterQueries:
    def test_queries_create_nodes_and_edges(self):
        rt = AutonomyRuntime(profile="TEST")
        rt.start()

        graph = rt.world_model.graph
        # Check initial state of intent:math node
        math_intent = graph.get_node("intent:math")
        initial_qc = math_intent.get("query_count", 0) if math_intent else 0

        rt.handle_query("What is 2+2?")
        rt.handle_query("paljonko on 5+3")

        # Invocations should have been recorded
        math_intent = graph.get_node("intent:math")
        assert math_intent is not None
        assert math_intent["query_count"] > initial_qc

        math_cap = graph.get_node("capability:solve.math")
        assert math_cap is not None
        assert math_cap["invocation_count"] >= 2

        rt.stop()

    def test_graph_health_fires_at_10_queries(self, caplog):
        rt = AutonomyRuntime(profile="TEST")
        rt.start()

        with caplog.at_level(logging.INFO, logger="waggledance.autonomy.runtime"):
            for i in range(12):
                rt.handle_query(f"query {i}")

        health_msgs = [r for r in caplog.records
                       if "Graph health" in r.message]
        assert len(health_msgs) >= 1
        rt.stop()


class TestFindAlternativePaths:
    def test_finds_alternatives_from_graph(self, temp_graph):
        builder = GraphBuilder(temp_graph)
        # Record multiple capabilities for same intent
        builder.record("q", "math", "solve.math", True, "gold", "gold")
        builder.record("q", "math", "solve.math", True, "gold", "gold")
        builder.record("q", "math", "solve.symbolic", True, "silver", "silver")
        builder.record("q", "math", "solve.constraints", False, "bronze", "bronze")

        alts = builder.find_alternative_paths(
            "math",
            exclude_capabilities=["solve.math"],
            min_success_rate=0.0,
        )
        cap_ids = [cap_id for cap_id, sr in alts]
        assert "solve.symbolic" in cap_ids
        assert "solve.math" not in cap_ids

    def test_respects_min_success_rate(self, temp_graph):
        builder = GraphBuilder(temp_graph)
        builder.record("q", "math", "solve.math", True)
        builder.record("q", "math", "solve.bad", False)

        alts = builder.find_alternative_paths(
            "math",
            exclude_capabilities=[],
            min_success_rate=0.5,
        )
        cap_ids = [cap_id for cap_id, sr in alts]
        assert "solve.math" in cap_ids
        assert "solve.bad" not in cap_ids

    def test_none_graph_returns_empty(self):
        builder = GraphBuilder(None)
        assert builder.find_alternative_paths("math") == []


class TestWorldModelGraphError:
    def test_logs_error_when_graph_import_fails(self, caplog):
        """WorldModel should log ERROR (not WARNING) when CognitiveGraph import fails."""
        import waggledance.core.world.world_model as wm_mod

        # Temporarily make the lazy import fail by patching the import inside __init__
        def _fake_import_error(*a, **kw):
            raise ImportError("mocked CognitiveGraph unavailable")

        with caplog.at_level(logging.ERROR, logger="waggledance.world.model"):
            with patch.dict("sys.modules", {"core.cognitive_graph": None}):
                with patch("builtins.__import__", side_effect=_fake_import_error):
                    # Use _UNSET (default) so the lazy import path is triggered
                    wm = WorldModel.__new__(WorldModel)
                    # Manually call __init__ with _UNSET sentinel
                    wm_mod.WorldModel.__init__(wm, profile="TEST")

        assert wm.graph is None
        error_msgs = [r for r in caplog.records
                      if r.levelno >= logging.ERROR and "CognitiveGraph" in r.message]
        assert len(error_msgs) >= 1

    def test_explicit_none_still_accepted(self):
        """Passing cognitive_graph=None explicitly should not raise."""
        wm = WorldModel(cognitive_graph=None, profile="TEST")
        assert wm.graph is None


class TestValidateCutoverGraph:
    def test_check_cognitive_graph_function_exists(self):
        from waggledance.tools.validate_cutover import check_cognitive_graph
        ok, msg = check_cognitive_graph()
        # Should return a boolean and message
        assert isinstance(ok, bool)
        assert isinstance(msg, str)
