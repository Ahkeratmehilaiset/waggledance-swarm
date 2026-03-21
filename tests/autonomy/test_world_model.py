"""
Phase 3 Tests: World Model, Entity Registry, Baseline Store integration.

Tests cover:
- EntityRegistry CRUD, listing, filtering
- BaselineStore EMA updates, residuals
- WorldModel integration (entity + baseline + snapshot)
- WorldModel without CognitiveGraph (graceful degradation)
- WorldSnapshot creation from WorldModel
- Relation management through WorldModel
"""

import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from waggledance.core.world.baseline_store import Baseline, BaselineStore
from waggledance.core.world.entity_registry import Entity, EntityRegistry
from waggledance.core.world.world_model import WorldModel


# ── EntityRegistry ────────────────────────────────────────────

class TestEntityRegistry:
    def test_register_and_get(self):
        reg = EntityRegistry()
        e = reg.register("hive_1", "beehive", {"location": "garden"})
        assert e.entity_id == "hive_1"
        assert e.entity_type == "beehive"
        assert e.attributes["location"] == "garden"
        assert reg.get("hive_1") is e

    def test_register_update_existing(self):
        reg = EntityRegistry()
        reg.register("hive_1", "beehive", {"temp": 35.0})
        e = reg.register("hive_1", "beehive", {"temp": 36.0, "humidity": 60.0})
        assert e.attributes["temp"] == 36.0
        assert e.attributes["humidity"] == 60.0
        assert reg.count() == 1

    def test_register_with_kwargs(self):
        reg = EntityRegistry()
        e = reg.register("sensor_1", "temperature_sensor", color="red")
        assert e.attributes["color"] == "red"

    def test_list_all(self):
        reg = EntityRegistry()
        reg.register("hive_1", "beehive")
        reg.register("hive_2", "beehive")
        reg.register("sensor_1", "sensor")
        assert reg.count() == 3
        assert len(reg.list()) == 3

    def test_list_by_type(self):
        reg = EntityRegistry()
        reg.register("hive_1", "beehive")
        reg.register("hive_2", "beehive")
        reg.register("sensor_1", "sensor")
        hives = reg.list("beehive")
        assert len(hives) == 2
        assert all(e.entity_type == "beehive" for e in hives)

    def test_remove(self):
        reg = EntityRegistry()
        reg.register("hive_1", "beehive")
        assert reg.remove("hive_1") is True
        assert reg.get("hive_1") is None
        assert reg.remove("nonexistent") is False

    def test_has(self):
        reg = EntityRegistry()
        reg.register("hive_1", "beehive")
        assert reg.has("hive_1") is True
        assert reg.has("nonexistent") is False

    def test_clear(self):
        reg = EntityRegistry()
        reg.register("hive_1", "beehive")
        reg.register("hive_2", "beehive")
        reg.clear()
        assert reg.count() == 0

    def test_count_by_type(self):
        reg = EntityRegistry()
        reg.register("h1", "beehive")
        reg.register("h2", "beehive")
        reg.register("s1", "sensor")
        assert reg.count("beehive") == 2
        assert reg.count("sensor") == 1
        assert reg.count("nonexistent") == 0

    def test_get_nonexistent(self):
        reg = EntityRegistry()
        assert reg.get("nonexistent") is None

    def test_entity_to_dict(self):
        e = Entity(entity_id="hive_1", entity_type="beehive",
                   attributes={"location": "garden"})
        d = e.to_dict()
        assert d["entity_id"] == "hive_1"
        assert d["entity_type"] == "beehive"
        assert d["attributes"]["location"] == "garden"
        assert "created_at" in d

    def test_entity_update(self):
        e = Entity(entity_id="hive_1", entity_type="beehive")
        old_updated = e.updated_at
        import time; time.sleep(0.01)
        e.update(temp=35.0)
        assert e.attributes["temp"] == 35.0
        assert e.updated_at >= old_updated

    def test_to_dict(self):
        reg = EntityRegistry()
        reg.register("h1", "beehive")
        reg.register("s1", "sensor")
        d = reg.to_dict()
        assert "h1" in d
        assert "s1" in d
        assert d["h1"]["entity_type"] == "beehive"


# ── BaselineStore ─────────────────────────────────────────────

class TestBaselineStore:
    @pytest.fixture
    def store(self, tmp_path):
        db_path = str(tmp_path / "test_baselines.db")
        s = BaselineStore(db_path=db_path)
        yield s
        s.close()

    def test_upsert_new(self, store):
        bl = store.upsert("hive_1", "temperature", 35.0)
        assert bl.entity_id == "hive_1"
        assert bl.metric_name == "temperature"
        assert bl.baseline_value == pytest.approx(35.0)
        assert bl.sample_count == 1

    def test_upsert_existing_ema(self, store):
        store.upsert("hive_1", "temperature", 35.0)
        bl = store.upsert("hive_1", "temperature", 40.0)
        # EMA: 35.0 * 0.9 + 40.0 * 0.1 = 35.5
        assert bl.baseline_value == pytest.approx(35.5)
        assert bl.sample_count == 2

    def test_get(self, store):
        store.upsert("hive_1", "temperature", 35.0)
        bl = store.get("hive_1", "temperature")
        assert bl is not None
        assert bl.baseline_value == pytest.approx(35.0)

    def test_get_nonexistent(self, store):
        assert store.get("nonexistent", "metric") is None

    def test_get_all_for_entity(self, store):
        store.upsert("hive_1", "temperature", 35.0)
        store.upsert("hive_1", "humidity", 60.0)
        store.upsert("hive_2", "temperature", 33.0)
        baselines = store.get_all_for_entity("hive_1")
        assert len(baselines) == 2

    def test_get_all(self, store):
        store.upsert("hive_1", "temperature", 35.0)
        store.upsert("hive_2", "temperature", 33.0)
        all_bl = store.get_all()
        assert len(all_bl) == 2

    def test_compute_residual(self, store):
        store.upsert("hive_1", "temperature", 35.0)
        r = store.compute_residual("hive_1", "temperature", 37.5)
        assert r == pytest.approx(2.5)

    def test_compute_residual_no_baseline(self, store):
        assert store.compute_residual("x", "y", 10.0) is None

    def test_baselines_dict(self, store):
        store.upsert("hive_1", "temperature", 35.0)
        store.upsert("hive_1", "humidity", 60.0)
        d = store.get_baselines_dict()
        assert d["hive_1.temperature"] == pytest.approx(35.0)
        assert d["hive_1.humidity"] == pytest.approx(60.0)

    def test_count(self, store):
        assert store.count() == 0
        store.upsert("hive_1", "temperature", 35.0)
        assert store.count() == 1

    def test_baseline_key_property(self):
        bl = Baseline(
            entity_id="hive_1", metric_name="temperature",
            baseline_value=35.0, confidence=0.5, sample_count=1,
            last_updated=0.0, source_type="observed",
        )
        assert bl.key == "hive_1.temperature"

    def test_source_type_preserved(self, store):
        store.upsert("hive_1", "temperature", 35.0, source_type="inferred_by_solver")
        bl = store.get("hive_1", "temperature")
        assert bl.source_type == "inferred_by_solver"


# ── WorldModel ────────────────────────────────────────────────

class TestWorldModel:
    @pytest.fixture
    def model(self, tmp_path):
        """WorldModel without CognitiveGraph (pure unit test)."""
        db_path = str(tmp_path / "test_baselines.db")
        bs = BaselineStore(db_path=db_path)
        wm = WorldModel(
            cognitive_graph=None,  # no graph layer for unit tests
            baseline_store=bs,
            profile="TEST",
        )
        yield wm
        bs.close()

    def test_create(self, model):
        assert model.profile == "TEST"
        assert model.graph is None

    def test_register_entity(self, model):
        e = model.register_entity("hive_1", "beehive", {"location": "garden"})
        assert e.entity_id == "hive_1"
        assert model.get_entity("hive_1") is not None

    def test_list_entities(self, model):
        model.register_entity("h1", "beehive")
        model.register_entity("h2", "beehive")
        model.register_entity("s1", "sensor")
        entities = model.list_entities()
        assert len(entities) == 3
        hives = model.list_entities("beehive")
        assert len(hives) == 2

    def test_update_baseline(self, model):
        bl = model.update_baseline("hive_1", "temperature", 35.0)
        assert bl.baseline_value == pytest.approx(35.0)

    def test_get_baseline(self, model):
        model.update_baseline("hive_1", "temperature", 35.0)
        assert model.get_baseline("hive_1", "temperature") == pytest.approx(35.0)
        assert model.get_baseline("nonexistent", "x") is None

    def test_compute_residual(self, model):
        model.update_baseline("hive_1", "temperature", 35.0)
        r = model.compute_residual("hive_1", "temperature", 37.0)
        assert r == pytest.approx(2.0)

    def test_compute_all_residuals(self, model):
        model.update_baseline("hive_1", "temperature", 35.0)
        model.update_baseline("hive_1", "humidity", 60.0)
        residuals = model.compute_all_residuals({
            "hive_1.temperature": 37.0,
            "hive_1.humidity": 55.0,
        })
        assert residuals["hive_1.temperature"] == pytest.approx(2.0)
        assert residuals["hive_1.humidity"] == pytest.approx(-5.0)

    def test_take_snapshot(self, model):
        model.register_entity("hive_1", "beehive", {"location": "garden"})
        model.update_baseline("hive_1", "temperature", 35.0)
        snap = model.take_snapshot(
            observations={"hive_1.temperature": 37.5},
        )
        assert snap.profile == "TEST"
        assert snap.entities["hive_1"]["type"] == "beehive"
        assert snap.baselines["hive_1.temperature"] == pytest.approx(35.0)
        assert snap.residuals["hive_1.temperature"] == pytest.approx(2.5)

    def test_take_snapshot_no_observations(self, model):
        model.register_entity("sensor_1", "sensor")
        snap = model.take_snapshot()
        assert snap.residuals == {}
        assert "sensor_1" in snap.entities

    def test_stats(self, model):
        model.register_entity("h1", "beehive")
        model.update_baseline("h1", "temperature", 35.0)
        s = model.stats()
        assert s["profile"] == "TEST"
        assert s["entities"] == 1
        assert s["baselines"] == 1

    def test_relations_without_graph(self, model):
        """Relations gracefully degrade without CognitiveGraph."""
        model.add_relation("a", "b", "causal")  # should log warning, not crash
        assert model.get_relations("a") == []
        assert model.find_dependents("a") == []
        assert model.find_ancestors("a") == []


# ── WorldModel with MockGraph ─────────────────────────────────

class MockGraph:
    """Minimal mock of CognitiveGraph for testing."""
    def __init__(self):
        self._nodes = {}
        self._edges = []

    def add_node(self, node_id, **attrs):
        self._nodes[node_id] = attrs

    def has_node(self, node_id):
        return node_id in self._nodes

    def get_node(self, node_id):
        if node_id not in self._nodes:
            return None
        return {"id": node_id, **self._nodes[node_id]}

    def add_edge(self, source, target, link_type="semantic", **attrs):
        if not self.has_node(source):
            self.add_node(source)
        if not self.has_node(target):
            self.add_node(target)
        self._edges.append({"source": source, "target": target, "link_type": link_type, **attrs})

    def get_edges(self, node_id):
        return [e for e in self._edges if e["source"] == node_id or e["target"] == node_id]

    def find_dependents(self, node_id, max_depth=5):
        return []

    def find_ancestors(self, node_id, max_depth=5):
        return []

    def stats(self):
        return {"nodes": len(self._nodes), "edges": len(self._edges)}

    def save(self):
        pass


class TestWorldModelWithGraph:
    @pytest.fixture
    def model(self, tmp_path):
        db_path = str(tmp_path / "test_baselines.db")
        bs = BaselineStore(db_path=db_path)
        wm = WorldModel(
            cognitive_graph=MockGraph(),
            baseline_store=bs,
            profile="COTTAGE",
        )
        yield wm
        bs.close()

    def test_register_entity_adds_to_graph(self, model):
        model.register_entity("hive_1", "beehive", {"location": "garden"})
        assert model.graph.has_node("hive_1")
        node = model.graph.get_node("hive_1")
        assert node["entity_type"] == "beehive"

    def test_add_relation(self, model):
        model.register_entity("hive_1", "beehive")
        model.register_entity("sensor_1", "sensor")
        model.add_relation("sensor_1", "hive_1", "input_to")
        edges = model.get_relations("sensor_1")
        assert len(edges) == 1
        assert edges[0]["link_type"] == "input_to"

    def test_save(self, model):
        model.register_entity("hive_1", "beehive")
        model.save()  # should not raise

    def test_stats_with_graph(self, model):
        model.register_entity("h1", "beehive")
        model.register_entity("s1", "sensor")
        model.add_relation("s1", "h1", "input_to")
        s = model.stats()
        assert s["graph"]["nodes"] == 2
        assert s["graph"]["edges"] == 1
