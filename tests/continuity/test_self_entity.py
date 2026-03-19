# SPDX-License-Identifier: Apache-2.0
"""Tests for self-entity in CognitiveGraph (v3.2 Phase 2)."""

import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.cognitive_graph import CognitiveGraph


@pytest.fixture
def graph(tmp_path):
    return CognitiveGraph(persist_path=str(tmp_path / "cg.json"))


class TestSelfEntityLifecycle:
    def test_ensure_creates_self_entity(self, graph):
        entity = graph.ensure_self_entity()
        assert entity["id"] == "self"
        assert entity["entity_type"] == "system"
        assert entity["identity_version"] == "v3.2-autonomy"

    def test_get_self_entity_none_before_init(self, graph):
        assert graph.get_self_entity() is None

    def test_get_self_entity_after_init(self, graph):
        graph.ensure_self_entity()
        entity = graph.get_self_entity()
        assert entity is not None
        assert entity["id"] == "self"

    def test_ensure_idempotent(self, graph):
        graph.ensure_self_entity(felt_load=0.3)
        graph.ensure_self_entity()  # should not reset felt_load
        entity = graph.get_self_entity()
        assert entity["felt_load"] == 0.3

    def test_update_self_entity(self, graph):
        graph.ensure_self_entity()
        updated = graph.update_self_entity(
            felt_load=0.7,
            epistemic_uncertainty_score=0.4,
            active_goals=["goal_1", "goal_2"],
        )
        assert updated["felt_load"] == 0.7
        assert updated["epistemic_uncertainty_score"] == 0.4
        assert updated["active_goals"] == ["goal_1", "goal_2"]

    def test_update_creates_if_missing(self, graph):
        entity = graph.update_self_entity(felt_load=0.5)
        assert entity["id"] == "self"
        assert entity["felt_load"] == 0.5

    def test_self_entity_in_stats(self, graph):
        stats = graph.stats()
        assert stats["has_self_entity"] is False
        graph.ensure_self_entity()
        stats = graph.stats()
        assert stats["has_self_entity"] is True


class TestSelfEntityInSnapshots:
    def test_self_entity_survives_save_load(self, tmp_path):
        path = str(tmp_path / "cg.json")
        g1 = CognitiveGraph(persist_path=path)
        g1.ensure_self_entity(felt_load=0.6, active_goals=["g1"])
        g1.save()

        g2 = CognitiveGraph(persist_path=path)
        entity = g2.get_self_entity()
        assert entity is not None
        assert entity["felt_load"] == 0.6
        assert entity["active_goals"] == ["g1"]

    def test_self_entity_edges(self, graph):
        graph.ensure_self_entity()
        graph.add_node("goal_123", entity_type="goal")
        graph.add_edge("self", "goal_123", link_type="causal")
        deps = graph.find_dependents("self")
        assert any(nid == "goal_123" for nid, _ in deps)


class TestSelfEntityHardwareAnchor:
    def test_hardware_dict_stored(self, graph):
        hw = {
            "device": "desktop",
            "ram_mb": 32768,
            "cpu_count": 8,
            "location": "helsinki",
            "connected_sensors": [],
            "uptime_hours": 1.5,
        }
        graph.ensure_self_entity(hardware=hw)
        entity = graph.get_self_entity()
        assert entity["hardware"]["device"] == "desktop"
        assert entity["hardware"]["ram_mb"] == 32768

    def test_hardware_updated_independently(self, graph):
        graph.ensure_self_entity(hardware={"device": "laptop"})
        graph.update_self_entity(hardware={"device": "laptop", "ram_mb": 16384})
        entity = graph.get_self_entity()
        assert entity["hardware"]["ram_mb"] == 16384
