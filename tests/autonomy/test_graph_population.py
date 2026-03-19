"""
Tests for CognitiveGraph population and GraphBuilder.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from waggledance.core.capabilities.registry import CapabilityRegistry
from waggledance.core.world.graph_builder import GraphBuilder


@pytest.fixture
def temp_graph():
    """Create a CognitiveGraph with a temp persist path."""
    from core.cognitive_graph import CognitiveGraph
    with tempfile.TemporaryDirectory() as td:
        path = str(Path(td) / "test_graph.json")
        graph = CognitiveGraph(persist_path=path)
        yield graph


class TestGraphBuilder:
    def test_record_creates_intent_and_capability_nodes(self, temp_graph):
        builder = GraphBuilder(temp_graph)
        builder.record(
            query="2+2", intent="math",
            capability_id="solve.math", executed=True,
            quality_grade="gold", quality_path="gold",
        )

        assert temp_graph.has_node("intent:math")
        assert temp_graph.has_node("capability:solve.math")

        intent = temp_graph.get_node("intent:math")
        assert intent["query_count"] == 1

        cap = temp_graph.get_node("capability:solve.math")
        assert cap["invocation_count"] == 1
        assert cap["success_count"] == 1
        assert cap["success_rate"] == 1.0

    def test_record_increments_counts(self, temp_graph):
        builder = GraphBuilder(temp_graph)
        for _ in range(3):
            builder.record("q", "math", "solve.math", True)
        builder.record("q", "math", "solve.math", False)

        cap = temp_graph.get_node("capability:solve.math")
        assert cap["invocation_count"] == 4
        assert cap["success_count"] == 3
        assert cap["success_rate"] == 0.75

        intent = temp_graph.get_node("intent:math")
        assert intent["query_count"] == 4

    def test_record_creates_causal_edge(self, temp_graph):
        builder = GraphBuilder(temp_graph)
        builder.record("q", "math", "solve.math", True)

        edges = temp_graph.get_edges("intent:math")
        causal = [e for e in edges if e.get("link_type") == "causal"]
        assert len(causal) >= 1
        assert causal[0]["target"] == "capability:solve.math"

    def test_no_duplicate_nodes(self, temp_graph):
        builder = GraphBuilder(temp_graph)
        builder.record("q1", "math", "solve.math", True)
        builder.record("q2", "math", "solve.math", True)

        # Should still have just one intent and one capability node
        nodes = [n for n in [temp_graph.get_node("intent:math")] if n]
        assert len(nodes) == 1

    def test_ensure_capability_nodes(self, temp_graph):
        builder = GraphBuilder(temp_graph)
        registry = CapabilityRegistry()

        added = builder.ensure_capability_nodes(registry)
        assert added == registry.count()

        # Running again should add 0
        added2 = builder.ensure_capability_nodes(registry)
        assert added2 == 0

    def test_ensure_agent_nodes(self, temp_graph):
        try:
            from waggledance.core.capabilities.aliasing import AliasRegistry
            registry = AliasRegistry.from_yaml("configs/alias_registry.yaml")
        except FileNotFoundError:
            pytest.skip("alias_registry.yaml not found")

        builder = GraphBuilder(temp_graph)
        added = builder.ensure_agent_nodes(registry)
        assert added == len(registry)

        # Running again should add 0
        added2 = builder.ensure_agent_nodes(registry)
        assert added2 == 0

    def test_json_persistence(self, temp_graph):
        builder = GraphBuilder(temp_graph)
        builder.record("q", "math", "solve.math", True)
        temp_graph.save()

        data = json.loads(Path(temp_graph.persist_path).read_text())
        assert data["directed"] is True
        assert len(data["nodes"]) > 0
        assert len(data["links"]) > 0

    def test_none_graph_is_safe(self):
        builder = GraphBuilder(None)
        builder.record("q", "math", "solve.math", True)  # no crash
        assert builder.ensure_capability_nodes(CapabilityRegistry()) == 0


class TestRuntimeGraphIntegration:
    def test_queries_grow_graph(self):
        from waggledance.core.autonomy.runtime import AutonomyRuntime

        rt = AutonomyRuntime(profile="VALIDATION")
        rt.start()

        # Graph and builder should be active
        assert rt.graph_builder is not None

        # Get initial invocation count for math capability
        graph = rt.world_model.graph
        math_node = graph.get_node("capability:solve.math")
        initial_count = math_node.get("invocation_count", 0) if math_node else 0

        rt.handle_query("What is 2+2?")
        rt.handle_query("paljonko on 5+3")

        # Invocation count should have grown
        math_node = graph.get_node("capability:solve.math")
        assert math_node is not None
        assert math_node["invocation_count"] > initial_count

        # Intent node should exist and have query_count
        intent_node = graph.get_node("intent:math")
        assert intent_node is not None
        assert intent_node["query_count"] >= 2

        rt.stop()
