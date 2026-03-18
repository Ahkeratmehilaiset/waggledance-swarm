"""
Comprehensive tests for all migration scripts.

Covers for each migration path:
  - Happy path
  - Partial data / missing field handling
  - Repeated run / idempotency
  - Alias resolution correctness
"""

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

CONFIGS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "configs")
ALIAS_YAML = os.path.join(CONFIGS_DIR, "alias_registry.yaml")


# ── Helpers ──────────────────────────────────────────────────────


def _make_alias_db(tmp_path, entries=None):
    """Create a minimal alias_registry.db for testing."""
    db_path = str(tmp_path / "alias_registry.db")
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE alias_registry "
        "(legacy_id TEXT PRIMARY KEY, canonical TEXT NOT NULL, profile TEXT DEFAULT '')"
    )
    for legacy_id, canonical, profile in (entries or [
        ("beekeeper", "domain.apiary.beekeeper", "cottage"),
        ("tarhaaja", "domain.apiary.beekeeper", "cottage"),
        ("heating", "domain.home.heating", "home"),
    ]):
        conn.execute(
            "INSERT OR REPLACE INTO alias_registry VALUES (?, ?, ?)",
            (legacy_id, canonical, profile)
        )
    conn.commit()
    conn.close()
    return db_path


def _make_target_db(tmp_path, table="trust_signals", agent_col="agent_id",
                    agents=None):
    """Create a minimal target DB with agent_id column."""
    db_path = str(tmp_path / "target.db")
    conn = sqlite3.connect(db_path)
    conn.execute(f"CREATE TABLE {table} ({agent_col} TEXT, value REAL)")
    for agent_id in (agents or ["beekeeper", "heating", "unknown_agent"]):
        conn.execute(f"INSERT INTO {table} VALUES (?, ?)", (agent_id, 1.0))
    conn.commit()
    conn.close()
    return db_path


def _make_trajectories_jsonl(tmp_path, trajectories=None):
    """Create a case_trajectories.jsonl for testing."""
    path = str(tmp_path / "case_trajectories.jsonl")
    if trajectories is None:
        trajectories = [
            {
                "trajectory_id": "abc123",
                "goal": {"type": "SOLVE", "description": "calculate 2+2"},
                "selected_capabilities": [{"capability_id": "solve.math"}],
                "quality_grade": "gold",
                "verifier_result": {"passed": True, "confidence": 0.95},
            },
            {
                "trajectory_id": "def456",
                "goal": {"type": "OBSERVE", "description": "what is the temp"},
                "selected_capabilities": [{"capability_id": "explain.llm_reasoning"}],
                "quality_grade": "bronze",
                "verifier_result": {"passed": True, "confidence": 0.5},
            },
            {
                "trajectory_id": "ghi789",
                "goal": {"type": "DIAGNOSE"},
                "selected_capabilities": [{"capability_id": "solve.symbolic"}],
                "quality_grade": "quarantine",
                "verifier_result": {"passed": False, "has_correction": True},
            },
        ]
    with open(path, "w", encoding="utf-8") as f:
        for t in trajectories:
            f.write(json.dumps(t) + "\n")
    return path


# ═══════════════════════════════════════════════════════════════════
# 1. ALIAS REGISTRY BUILDER
# ═══════════════════════════════════════════════════════════════════


class TestAliasRegistryBuilder:
    def test_load_registry_happy(self):
        if not os.path.exists(ALIAS_YAML):
            pytest.skip("alias_registry.yaml not found")
        from tools.alias_registry_builder import load_registry
        registry = load_registry(ALIAS_YAML)
        assert isinstance(registry, dict)
        assert len(registry) > 0

    def test_build_db_happy(self, tmp_path):
        if not os.path.exists(ALIAS_YAML):
            pytest.skip("alias_registry.yaml not found")
        from tools.alias_registry_builder import load_registry, build_db
        registry = load_registry(ALIAS_YAML)
        db_path = str(tmp_path / "alias.db")
        stats = build_db(registry, db_path)
        assert stats["agents"] > 0
        assert stats["total_rows"] > 0
        assert stats["canonical_ids"] > 0

        conn = sqlite3.connect(db_path)
        count = conn.execute("SELECT COUNT(*) FROM alias_registry").fetchone()[0]
        conn.close()
        assert count == stats["total_rows"]

    def test_build_db_idempotent(self, tmp_path):
        """Running build_db twice produces identical results."""
        if not os.path.exists(ALIAS_YAML):
            pytest.skip("alias_registry.yaml not found")
        from tools.alias_registry_builder import load_registry, build_db
        registry = load_registry(ALIAS_YAML)
        db_path = str(tmp_path / "alias.db")
        stats1 = build_db(registry, db_path)
        stats2 = build_db(registry, db_path)
        assert stats1["total_rows"] == stats2["total_rows"]
        assert stats1["canonical_ids"] == stats2["canonical_ids"]

    def test_build_db_empty_registry(self, tmp_path):
        from tools.alias_registry_builder import build_db
        db_path = str(tmp_path / "alias.db")
        stats = build_db({}, db_path)
        assert stats["agents"] == 0
        assert stats["total_rows"] == 0

    def test_build_db_partial_entry(self, tmp_path):
        """Entry without canonical key still processes (canonical defaults to '')."""
        from tools.alias_registry_builder import build_db
        registry = {
            "valid_agent": {"canonical": "domain.valid", "aliases": ["va"]},
            "no_canonical": {"aliases": ["x", "y"]},  # missing canonical → ''
            "not_a_dict": "just_a_string",  # not a dict → skipped
        }
        db_path = str(tmp_path / "alias.db")
        stats = build_db(registry, db_path)
        assert stats["agents"] == 2  # valid_agent + no_canonical (not_a_dict skipped)
        # Verify canonical was empty for the partial entry
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT canonical FROM alias_registry WHERE legacy_id = 'no_canonical'"
        ).fetchone()
        conn.close()
        assert row[0] == ""  # missing canonical defaults to empty

    def test_alias_resolution_from_real_yaml(self):
        """Verify canonical resolution through AliasRegistry from actual YAML."""
        if not os.path.exists(ALIAS_YAML):
            pytest.skip("alias_registry.yaml not found")
        from waggledance.core.capabilities.aliasing import AliasRegistry
        registry = AliasRegistry.from_yaml(ALIAS_YAML)
        # Verify specific alias resolutions
        assert registry.resolve("beekeeper") == "domain.apiary.beekeeper"
        assert registry.resolve("tarhaaja") == "domain.apiary.beekeeper"
        assert registry.resolve("bee_keeper") == "domain.apiary.beekeeper"
        # Finnish aliases resolve
        assert registry.resolve("mehiläishoitaja") is None or \
               registry.resolve("tarhaaja") == "domain.apiary.beekeeper"
        # Case insensitive
        assert registry.resolve("Beekeeper") == "domain.apiary.beekeeper"
        assert registry.resolve("BEEKEEPER") == "domain.apiary.beekeeper"
        # Canonical resolves to itself
        assert registry.resolve("domain.apiary.beekeeper") == "domain.apiary.beekeeper"
        # Unknown returns None
        assert registry.resolve("nonexistent_agent_xyz") is None

    def test_alias_profiles_from_real_yaml(self):
        """Verify profile filtering works correctly."""
        if not os.path.exists(ALIAS_YAML):
            pytest.skip("alias_registry.yaml not found")
        from waggledance.core.capabilities.aliasing import AliasRegistry
        registry = AliasRegistry.from_yaml(ALIAS_YAML)
        cottage_agents = registry.by_profile("cottage")
        assert len(cottage_agents) > 5  # multiple cottage agents
        # All returned agents have cottage in profiles
        for agent in cottage_agents:
            assert "cottage" in agent.profiles

    def test_alias_build_legacy_map(self):
        """bulk migration map covers all aliases."""
        if not os.path.exists(ALIAS_YAML):
            pytest.skip("alias_registry.yaml not found")
        from waggledance.core.capabilities.aliasing import AliasRegistry
        registry = AliasRegistry.from_yaml(ALIAS_YAML)
        lookup = registry.build_legacy_to_canonical_map()
        assert isinstance(lookup, dict)
        assert len(lookup) > len(registry)  # aliases > agents
        # Every canonical should map to itself
        for canonical_id in registry.all_canonical_ids():
            assert lookup.get(canonical_id.lower()) == canonical_id


# ═══════════════════════════════════════════════════════════════════
# 2. BACKFILL CANONICAL IDS
# ═══════════════════════════════════════════════════════════════════


class TestBackfillCanonicalIds:
    def test_load_alias_map_happy(self, tmp_path):
        from tools.backfill_canonical_ids import load_alias_map
        db_path = _make_alias_db(tmp_path)
        alias_map = load_alias_map(db_path)
        assert alias_map["beekeeper"] == "domain.apiary.beekeeper"
        assert alias_map["tarhaaja"] == "domain.apiary.beekeeper"
        assert alias_map["heating"] == "domain.home.heating"

    def test_load_alias_map_missing_db(self, tmp_path):
        from tools.backfill_canonical_ids import load_alias_map
        result = load_alias_map(str(tmp_path / "nonexistent.db"))
        assert result == {}

    def test_backfill_table_happy(self, tmp_path):
        from tools.backfill_canonical_ids import backfill_table
        alias_map = {"beekeeper": "domain.apiary.beekeeper",
                     "heating": "domain.home.heating"}
        db_path = _make_target_db(tmp_path)
        result = backfill_table(db_path, "trust_signals", "agent_id", alias_map)
        assert result["status"] == "ok"
        assert result["updated"] == 2  # beekeeper + heating, not unknown_agent

        # Verify the canonical_id column was filled
        conn = sqlite3.connect(db_path)
        filled = conn.execute(
            "SELECT COUNT(*) FROM trust_signals WHERE canonical_id != ''"
        ).fetchone()[0]
        conn.close()
        assert filled == 2

    def test_backfill_table_idempotent(self, tmp_path):
        """Running backfill twice doesn't double-update."""
        from tools.backfill_canonical_ids import backfill_table
        alias_map = {"beekeeper": "domain.apiary.beekeeper"}
        db_path = _make_target_db(tmp_path, agents=["beekeeper"])
        result1 = backfill_table(db_path, "trust_signals", "agent_id", alias_map)
        assert result1["updated"] == 1

        result2 = backfill_table(db_path, "trust_signals", "agent_id", alias_map)
        assert result2["updated"] == 0  # already filled

    def test_backfill_table_missing_db(self, tmp_path):
        from tools.backfill_canonical_ids import backfill_table
        result = backfill_table(
            str(tmp_path / "nonexistent.db"), "t", "id", {}
        )
        assert result["status"] == "not_found"

    def test_backfill_table_empty_alias_map(self, tmp_path):
        from tools.backfill_canonical_ids import backfill_table
        db_path = _make_target_db(tmp_path)
        result = backfill_table(db_path, "trust_signals", "agent_id", {})
        assert result["status"] == "ok"
        assert result["updated"] == 0


# ═══════════════════════════════════════════════════════════════════
# 3. REINDEX CHROMA
# ═══════════════════════════════════════════════════════════════════


class TestReindexChroma:
    def test_load_alias_map(self, tmp_path):
        from tools.reindex_chroma_with_canonical import load_alias_map
        db_path = _make_alias_db(tmp_path)
        alias_map = load_alias_map(db_path)
        assert "beekeeper" in alias_map
        assert alias_map["beekeeper"] == "domain.apiary.beekeeper"

    def test_load_alias_map_missing(self, tmp_path):
        from tools.reindex_chroma_with_canonical import load_alias_map
        assert load_alias_map(str(tmp_path / "nope.db")) == {}

    def test_reindex_collection_not_found(self):
        """Handles missing collection gracefully."""
        from tools.reindex_chroma_with_canonical import reindex_collection
        # Create a mock client that raises on get_or_create_collection
        class MockClient:
            def get_or_create_collection(self, name):
                raise ValueError("not found")
        result = reindex_collection(MockClient(), "test_coll", {})
        assert result["status"] == "not_found"

    def test_reindex_collection_empty(self):
        """Empty collection returns empty stats."""
        from tools.reindex_chroma_with_canonical import reindex_collection

        class MockCollection:
            def count(self):
                return 0

        class MockClient:
            def get_or_create_collection(self, name):
                return MockCollection()

        result = reindex_collection(MockClient(), "test_coll", {})
        assert result["status"] == "empty"
        assert result["documents"] == 0

    def test_reindex_collection_idempotent(self):
        """Documents with canonical_id already set are skipped."""
        from tools.reindex_chroma_with_canonical import reindex_collection

        class MockCollection:
            def __init__(self):
                self._updated = []

            def count(self):
                return 2

            def get(self, limit=100, offset=0, include=None):
                if offset > 0:
                    return {"ids": [], "metadatas": []}
                return {
                    "ids": ["doc1", "doc2"],
                    "metadatas": [
                        {"agent_id": "beekeeper", "canonical_id": "already.set"},
                        {"agent_id": "heating"},
                    ],
                }

            def update(self, ids, metadatas):
                self._updated.extend(ids)

        class MockClient:
            def __init__(self):
                self.collection = MockCollection()
            def get_or_create_collection(self, name):
                return self.collection

        alias_map = {"beekeeper": "domain.apiary.beekeeper",
                     "heating": "domain.home.heating"}
        client = MockClient()
        result = reindex_collection(client, "test", alias_map)
        assert result["skipped_already_set"] == 1  # doc1 skipped
        assert result["updated"] == 1  # doc2 updated
        assert "doc2" in client.collection._updated


# ═══════════════════════════════════════════════════════════════════
# 4. BUILD CASE TRAJECTORIES
# ═══════════════════════════════════════════════════════════════════


class TestBuildCaseTrajectories:
    def test_infer_goal_type_keywords(self):
        from tools.build_case_trajectories_from_legacy import infer_goal_type
        assert infer_goal_type("calculate the area") == "SOLVE"
        assert infer_goal_type("laske lämpötila") == "SOLVE"
        assert infer_goal_type("what is temperature") == "OBSERVE"
        assert infer_goal_type("mikä on lämpötila") == "OBSERVE"
        assert infer_goal_type("why did it fail") == "DIAGNOSE"
        assert infer_goal_type("miksi pesä kuoli") == "DIAGNOSE"
        assert infer_goal_type("how to treat varroa") == "PLAN"
        assert infer_goal_type("miten hoitaa varroa") == "PLAN"
        assert infer_goal_type("optimize energy") == "OPTIMIZE"
        assert infer_goal_type("optimoi energia") == "OPTIMIZE"
        assert infer_goal_type("check the hive") == "VERIFY"
        assert infer_goal_type("tarkista pesä") == "VERIFY"
        assert infer_goal_type("protect the bees") == "PROTECT"
        assert infer_goal_type("suojaa mehiläiset") == "PROTECT"

    def test_infer_goal_type_default(self):
        from tools.build_case_trajectories_from_legacy import infer_goal_type
        assert infer_goal_type("random question") == "OBSERVE"
        assert infer_goal_type("") == "OBSERVE"

    def test_infer_quality_grade_all_grades(self):
        from tools.build_case_trajectories_from_legacy import infer_quality_grade
        assert infer_quality_grade({"source": "solver", "confidence": 0.95}) == "gold"
        assert infer_quality_grade({"source": "math", "confidence": 0.9}) == "gold"
        assert infer_quality_grade({"source": "symbolic", "confidence": 0.85}) == "gold"
        assert infer_quality_grade({"source": "solver", "confidence": 0.5}) == "silver"
        assert infer_quality_grade({"source": "micromodel", "confidence": 0.5}) == "silver"
        assert infer_quality_grade({"source": "memory", "confidence": 0.3}) == "silver"
        assert infer_quality_grade({"source": "llm", "confidence": 0.95}) == "silver"
        assert infer_quality_grade({"source": "llm", "confidence": 0.3}) == "bronze"
        assert infer_quality_grade({"has_correction": True}) == "quarantine"
        assert infer_quality_grade({"has_correction": True, "source": "solver",
                                    "confidence": 0.99}) == "quarantine"

    def test_infer_quality_grade_missing_fields(self):
        from tools.build_case_trajectories_from_legacy import infer_quality_grade
        assert infer_quality_grade({}) == "bronze"
        assert infer_quality_grade({"source": ""}) == "bronze"

    def test_build_trajectory_happy(self):
        from tools.build_case_trajectories_from_legacy import build_trajectory
        entry = {
            "question": "calculate 2+2",
            "answer": "4",
            "confidence": 0.95,
            "source": "solver",
            "timestamp": 1710000000.0,
        }
        traj = build_trajectory(entry, {}, 0)
        assert traj["trajectory_id"]
        assert len(traj["trajectory_id"]) == 16
        assert traj["goal"]["type"] == "SOLVE"
        assert traj["goal"]["description"] == "calculate 2+2"
        assert traj["quality_grade"] == "gold"
        assert traj["verifier_result"]["passed"] is True
        assert traj["source"] == "legacy_migration"
        assert len(traj["selected_capabilities"]) == 1
        assert traj["selected_capabilities"][0]["capability_id"] == "legacy.solver"

    def test_build_trajectory_with_correction(self):
        from tools.build_case_trajectories_from_legacy import build_trajectory
        entry = {"question": "what is varroa", "answer": "wrong", "source": "llm"}
        corrections = {"what is varroa": "A parasitic mite"}
        traj = build_trajectory(entry, corrections, 0)
        assert traj["quality_grade"] == "quarantine"
        assert traj["verifier_result"]["passed"] is False
        assert traj["verifier_result"]["has_correction"] is True

    def test_build_trajectory_missing_fields(self):
        from tools.build_case_trajectories_from_legacy import build_trajectory
        entry = {}  # minimal entry
        traj = build_trajectory(entry, {}, 0)
        assert traj["trajectory_id"]
        assert traj["goal"]["type"] == "OBSERVE"
        assert traj["quality_grade"] == "bronze"

    def test_build_trajectory_deterministic(self):
        from tools.build_case_trajectories_from_legacy import build_trajectory
        entry = {"question": "q", "answer": "a", "source": "llm"}
        t1 = build_trajectory(entry, {}, 42)
        t2 = build_trajectory(entry, {}, 42)
        assert t1["trajectory_id"] == t2["trajectory_id"]

    def test_load_training_pairs_missing_db(self):
        from tools.build_case_trajectories_from_legacy import load_training_pairs
        assert load_training_pairs("/nonexistent/path.db") == []

    def test_load_corrections_missing_db(self):
        from tools.build_case_trajectories_from_legacy import load_corrections
        assert load_corrections("/nonexistent/path.db") == {}

    def test_load_training_pairs_from_db(self, tmp_path):
        from tools.build_case_trajectories_from_legacy import load_training_pairs
        db_path = str(tmp_path / "waggle.db")
        conn = sqlite3.connect(db_path)
        conn.execute(
            "CREATE TABLE training_pairs "
            "(question TEXT, answer TEXT, confidence REAL, source TEXT, timestamp REAL)"
        )
        conn.execute(
            "INSERT INTO training_pairs VALUES (?, ?, ?, ?, ?)",
            ("q1", "a1", 0.9, "solver", 1.0)
        )
        conn.execute(
            "INSERT INTO training_pairs VALUES (?, ?, ?, ?, ?)",
            ("q2", "a2", 0.5, "llm", 2.0)
        )
        conn.commit()
        conn.close()
        pairs = load_training_pairs(db_path)
        assert len(pairs) == 2
        assert pairs[0]["question"] == "q1"
        assert pairs[1]["confidence"] == 0.5

    def test_load_ledger_events(self, tmp_path):
        from tools.build_case_trajectories_from_legacy import load_ledger_events
        path = str(tmp_path / "ledger.jsonl")
        with open(path, "w") as f:
            f.write('{"event": "train"}\n')
            f.write('bad json\n')
            f.write('{"event": "correct"}\n')
        events = load_ledger_events(path)
        assert len(events) == 2

    def test_load_ledger_events_missing(self):
        from tools.build_case_trajectories_from_legacy import load_ledger_events
        assert load_ledger_events("/nonexistent/ledger.jsonl") == []


# ═══════════════════════════════════════════════════════════════════
# 5. BACKFILL WORLD SNAPSHOTS
# ═══════════════════════════════════════════════════════════════════


class TestBackfillWorldSnapshots:
    def test_build_entities_from_capsule_happy(self):
        from tools.backfill_world_snapshots import build_entities_from_capsule
        capsule = {
            "data_sources": [
                {"id": "temp", "type": "sensor", "unit": "°C"},
                {"id": "weight", "type": "sensor", "unit": "kg"},
            ],
            "key_decisions": [
                {"id": "honey_yield", "primary_layer": "model_based",
                 "model": "honey_yield"},
            ],
        }
        entities = build_entities_from_capsule(capsule, "cottage")
        assert len(entities) == 3
        assert entities[0]["entity_id"] == "cottage.temp"
        assert entities[0]["entity_type"] == "sensor"
        assert entities[2]["entity_type"] == "decision"
        assert entities[2]["entity_id"] == "cottage.decision.honey_yield"

    def test_build_entities_empty_capsule(self):
        from tools.backfill_world_snapshots import build_entities_from_capsule
        assert build_entities_from_capsule({}, "test") == []
        assert build_entities_from_capsule({"data_sources": []}, "test") == []

    def test_build_baselines_from_axioms_happy(self, tmp_path):
        """Test with actual axiom variable structure (dict format)."""
        from tools.backfill_world_snapshots import build_baselines_from_axioms
        axiom_dir = tmp_path / "axioms" / "cottage"
        axiom_dir.mkdir(parents=True)
        axiom_yaml = {
            "model_id": "heating_cost",
            "variables": {
                "T_indoor": {"default": 21, "unit": "°C"},
                "T_outdoor": {"default": -10, "unit": "°C"},
                "area_m2": {"default": 80, "unit": "m²"},
            },
        }
        import yaml
        with open(axiom_dir / "heating_cost.yaml", "w") as f:
            yaml.dump(axiom_yaml, f)

        baselines = build_baselines_from_axioms(str(tmp_path / "axioms"), "cottage")
        assert len(baselines) == 3
        assert baselines["cottage.heating_cost.T_indoor"] == 21.0
        assert baselines["cottage.heating_cost.T_outdoor"] == -10.0
        assert baselines["cottage.heating_cost.area_m2"] == 80.0

    def test_build_baselines_nonexistent_profile(self, tmp_path):
        from tools.backfill_world_snapshots import build_baselines_from_axioms
        baselines = build_baselines_from_axioms(str(tmp_path / "axioms"), "nonexistent")
        assert baselines == {}

    def test_build_baselines_skips_non_numeric(self, tmp_path):
        """Non-numeric defaults are skipped."""
        from tools.backfill_world_snapshots import build_baselines_from_axioms
        axiom_dir = tmp_path / "axioms" / "test"
        axiom_dir.mkdir(parents=True)
        import yaml
        with open(axiom_dir / "model.yaml", "w") as f:
            yaml.dump({
                "model_id": "test",
                "variables": {
                    "numeric": {"default": 42},
                    "string_val": {"default": "hello"},
                    "no_default": {"unit": "kg"},
                },
            }, f)
        baselines = build_baselines_from_axioms(str(tmp_path / "axioms"), "test")
        assert len(baselines) == 1
        assert baselines["test.test.numeric"] == 42.0

    def test_build_baselines_from_real_axioms(self):
        """Test against real axiom files in configs/axioms/cottage/."""
        from tools.backfill_world_snapshots import build_baselines_from_axioms
        axioms_dir = os.path.join(CONFIGS_DIR, "axioms")
        if not os.path.isdir(os.path.join(axioms_dir, "cottage")):
            pytest.skip("configs/axioms/cottage/ not found")
        baselines = build_baselines_from_axioms(axioms_dir, "cottage")
        assert len(baselines) > 0
        # Check known baseline from heating_cost.yaml
        assert "cottage.heating_cost.T_indoor" in baselines
        assert baselines["cottage.heating_cost.T_indoor"] == 21.0

    def test_create_world_store_idempotent(self, tmp_path):
        from tools.backfill_world_snapshots import create_world_store
        db_path = str(tmp_path / "world.db")
        conn1 = create_world_store(db_path)
        conn1.execute(
            "INSERT INTO entities VALUES ('e1', 'sensor', '{}', 'test', 0, 0)")
        conn1.commit()
        conn1.close()

        # Running again shouldn't drop existing data (CREATE IF NOT EXISTS)
        conn2 = create_world_store(db_path)
        count = conn2.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        conn2.close()
        assert count == 1

    def test_load_yaml_missing(self):
        from tools.backfill_world_snapshots import load_yaml
        assert load_yaml("/nonexistent/file.yaml") == {}


# ═══════════════════════════════════════════════════════════════════
# 6. BACKFILL PROCEDURAL MEMORY
# ═══════════════════════════════════════════════════════════════════


class TestBackfillProceduralMemory:
    def test_extract_capability_chain_happy(self):
        from tools.backfill_procedural_memory import extract_capability_chain
        traj = {
            "selected_capabilities": [
                {"capability_id": "solve.math"},
                {"capability_id": "verify.hallucination"},
            ]
        }
        chain = extract_capability_chain(traj)
        assert chain == ["solve.math", "verify.hallucination"]

    def test_extract_empty_chain(self):
        from tools.backfill_procedural_memory import extract_capability_chain
        assert extract_capability_chain({}) == []
        assert extract_capability_chain({"selected_capabilities": []}) == []

    def test_extract_chain_missing_capability_id(self):
        from tools.backfill_procedural_memory import extract_capability_chain
        traj = {"selected_capabilities": [{"category": "solve"}, {"capability_id": "x"}]}
        chain = extract_capability_chain(traj)
        assert chain == ["x"]  # first entry filtered out

    def test_build_procedure_id_deterministic(self):
        from tools.backfill_procedural_memory import build_procedure_id
        pid1 = build_procedure_id("SOLVE", ["a", "b"])
        pid2 = build_procedure_id("SOLVE", ["a", "b"])
        assert pid1 == pid2
        assert len(pid1) == 16

    def test_build_procedure_id_unique(self):
        from tools.backfill_procedural_memory import build_procedure_id
        pid1 = build_procedure_id("SOLVE", ["a", "b"])
        pid2 = build_procedure_id("OBSERVE", ["a", "b"])
        pid3 = build_procedure_id("SOLVE", ["a", "c"])
        assert pid1 != pid2
        assert pid1 != pid3

    def test_load_trajectories_happy(self, tmp_path):
        from tools.backfill_procedural_memory import load_trajectories
        path = _make_trajectories_jsonl(tmp_path)
        trajs = load_trajectories(path)
        assert len(trajs) == 3

    def test_load_trajectories_missing(self):
        from tools.backfill_procedural_memory import load_trajectories
        assert load_trajectories("/nonexistent/file.jsonl") == []

    def test_load_trajectories_malformed(self, tmp_path):
        from tools.backfill_procedural_memory import load_trajectories
        path = str(tmp_path / "bad.jsonl")
        with open(path, "w") as f:
            f.write('{"ok": true}\n')
            f.write('not json\n')
            f.write('\n')
            f.write('{"ok": false}\n')
        trajs = load_trajectories(path)
        assert len(trajs) == 2


# ═══════════════════════════════════════════════════════════════════
# 7. BACKFILL PROVENANCE
# ═══════════════════════════════════════════════════════════════════


class TestBackfillProvenance:
    def test_grade_to_source_type(self):
        from tools.backfill_provenance import grade_to_source_type
        assert grade_to_source_type("gold") == "confirmed_by_verifier"
        assert grade_to_source_type("silver") == "inferred_by_solver"
        assert grade_to_source_type("bronze") == "proposed_by_llm"
        assert grade_to_source_type("quarantine") == "proposed_by_llm"
        assert grade_to_source_type("unknown") == "proposed_by_llm"

    def test_load_trajectories_happy(self, tmp_path):
        from tools.backfill_provenance import load_trajectories
        path = _make_trajectories_jsonl(tmp_path)
        trajs = load_trajectories(path)
        assert len(trajs) == 3

    def test_load_trajectories_missing(self):
        from tools.backfill_provenance import load_trajectories
        assert load_trajectories("/nonexistent/path.jsonl") == []


# ═══════════════════════════════════════════════════════════════════
# 8. MIGRATION ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════════


class TestMigrationOrchestrator:
    def test_phases_defined(self):
        from tools.run_migration import PHASES
        assert len(PHASES) == 7
        names = [name for name, _ in PHASES]
        assert "1a_alias_registry" in names
        assert "7c_provenance" in names

    def test_run_phase_success(self):
        from tools.run_migration import run_phase
        result = run_phase("test", lambda dry_run: {"ok": True})
        assert result["status"] == "ok"
        assert "elapsed_s" in result

    def test_run_phase_failure(self):
        from tools.run_migration import run_phase
        def failing_fn(dry_run):
            raise RuntimeError("test error")
        result = run_phase("test", failing_fn)
        assert result["status"] == "error"
        assert "test error" in result["error"]

    def test_phase_1a_dry_run(self):
        if not os.path.exists(ALIAS_YAML):
            pytest.skip("alias_registry.yaml not found")
        from tools.run_migration import phase_1a_alias_registry
        result = phase_1a_alias_registry(dry_run=True)
        assert result["status"] == "dry_run"
        assert result["agents"] > 0

    def test_phase_1a_live(self):
        """Phase 1a produces correct result structure."""
        if not os.path.exists(ALIAS_YAML):
            pytest.skip("alias_registry.yaml not found")
        from tools.run_migration import phase_1a_alias_registry
        result = phase_1a_alias_registry(dry_run=False)
        assert result["agents"] > 0
        assert result["total_rows"] > 0
        assert result["canonical_ids"] > 0


# ═══════════════════════════════════════════════════════════════════
# 9. ALIAS RESOLUTION CORRECTNESS
# ═══════════════════════════════════════════════════════════════════


class TestAliasResolutionCorrectness:
    """Verify that alias resolution is correct across the migration pipeline."""

    @pytest.fixture
    def registry(self):
        from waggledance.core.capabilities.aliasing import AliasRegistry, AgentAlias
        agents = [
            AgentAlias(
                legacy_id="beekeeper",
                canonical="domain.apiary.beekeeper",
                aliases=("beekeeper", "tarhaaja", "bee_keeper"),
                profiles=("cottage", "home"),
            ),
            AgentAlias(
                legacy_id="heating",
                canonical="domain.home.heating",
                aliases=("heating", "lämmitys"),
                profiles=("home",),
            ),
            AgentAlias(
                legacy_id="meteorologist",
                canonical="shared.weather.meteorology",
                aliases=("meteorologist", "meteorologi", "saaasiantuntija"),
                profiles=("gadget", "cottage", "home", "factory"),
            ),
        ]
        return AliasRegistry(agents)

    def test_resolve_all_aliases(self, registry):
        assert registry.resolve("beekeeper") == "domain.apiary.beekeeper"
        assert registry.resolve("tarhaaja") == "domain.apiary.beekeeper"
        assert registry.resolve("bee_keeper") == "domain.apiary.beekeeper"
        assert registry.resolve("heating") == "domain.home.heating"
        assert registry.resolve("lämmitys") == "domain.home.heating"

    def test_resolve_canonical_to_itself(self, registry):
        assert registry.resolve("domain.apiary.beekeeper") == "domain.apiary.beekeeper"
        assert registry.resolve("domain.home.heating") == "domain.home.heating"

    def test_resolve_case_insensitive(self, registry):
        assert registry.resolve("BEEKEEPER") == "domain.apiary.beekeeper"
        assert registry.resolve("Tarhaaja") == "domain.apiary.beekeeper"
        assert registry.resolve("HEATING") == "domain.home.heating"

    def test_resolve_unknown_returns_none(self, registry):
        assert registry.resolve("nonexistent") is None
        assert registry.resolve("") is None

    def test_resolve_strict_raises(self, registry):
        with pytest.raises(KeyError):
            registry.resolve_strict("nonexistent")

    def test_profile_filtering(self, registry):
        cottage = registry.by_profile("cottage")
        assert len(cottage) == 2  # beekeeper + meteorologist
        home = registry.by_profile("home")
        assert len(home) == 3  # beekeeper + heating + meteorologist
        factory = registry.by_profile("factory")
        assert len(factory) == 1  # meteorologist only

    def test_bulk_map_completeness(self, registry):
        lookup = registry.build_legacy_to_canonical_map()
        # Every alias should be in the map
        assert lookup["beekeeper"] == "domain.apiary.beekeeper"
        assert lookup["tarhaaja"] == "domain.apiary.beekeeper"
        assert lookup["bee_keeper"] == "domain.apiary.beekeeper"
        assert lookup["heating"] == "domain.home.heating"
        assert lookup["lämmitys"] == "domain.home.heating"
        # Canonical IDs also map to themselves
        assert lookup["domain.apiary.beekeeper"] == "domain.apiary.beekeeper"

    def test_primary_legacy_id(self, registry):
        assert registry.primary_legacy("domain.apiary.beekeeper") == "beekeeper"
        assert registry.primary_legacy("domain.home.heating") == "heating"
        assert registry.primary_legacy("nonexistent") is None

    def test_all_legacy_ids(self, registry):
        ids = registry.all_legacy_ids("domain.apiary.beekeeper")
        assert "beekeeper" in ids
        assert "tarhaaja" in ids
        assert "bee_keeper" in ids

    def test_cross_profile_agent(self, registry):
        """Meteorologist appears in all 4 profiles."""
        for profile in ("gadget", "cottage", "home", "factory"):
            agents = registry.by_profile(profile)
            canonicals = [a.canonical for a in agents]
            assert "shared.weather.meteorology" in canonicals
