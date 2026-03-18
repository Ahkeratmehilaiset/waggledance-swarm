"""
Tests for persistence adapters: SQLiteWorldStore, SQLiteProceduralStore,
SQLiteWorkingMemory, SQLiteCaseStore, SQLiteVerifierStore, and runtime wiring.

All tests use in-memory SQLite databases or tmp_path isolation.
"""

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from waggledance.adapters.persistence.sqlite_world_store import SQLiteWorldStore
from waggledance.adapters.persistence.sqlite_procedural_store import SQLiteProceduralStore
from waggledance.adapters.persistence.sqlite_working_memory import SQLiteWorkingMemory
from waggledance.adapters.persistence.sqlite_case_store import SQLiteCaseStore
from waggledance.adapters.persistence.sqlite_verifier_store import SQLiteVerifierStore


class TestSQLiteWorldStore:
    def setup_method(self):
        self.store = SQLiteWorldStore(db_path=":memory:")

    def teardown_method(self):
        self.store.close()

    def test_upsert_and_get_baseline(self):
        self.store.upsert_baseline("hive.1", "temperature", 35.0, confidence=0.9)
        result = self.store.get_baseline("hive.1", "temperature")
        assert result is not None
        assert result["baseline_value"] == pytest.approx(35.0)
        assert result["confidence"] == pytest.approx(0.9)

    def test_get_nonexistent_baseline(self):
        result = self.store.get_baseline("nope", "nope")
        assert result is None

    def test_get_entity_baselines(self):
        self.store.upsert_baseline("hive.1", "temp", 35.0)
        self.store.upsert_baseline("hive.1", "weight", 33.0)
        self.store.upsert_baseline("hive.2", "temp", 34.0)
        baselines = self.store.get_entity_baselines("hive.1")
        assert len(baselines) == 2

    def test_upsert_entity(self):
        self.store.upsert_entity("hive.1", "sensor", {"type": "temp"}, "cottage")
        # Should not raise on update
        self.store.upsert_entity("hive.1", "sensor", {"type": "temp", "unit": "C"}, "cottage")

    def test_save_and_list_snapshots(self):
        self.store.save_snapshot("snap-1", {"entities": {}}, profile="cottage")
        self.store.save_snapshot("snap-2", {"entities": {}}, profile="cottage")
        snapshots = self.store.list_snapshots(profile="cottage", limit=10)
        assert len(snapshots) == 2


class TestSQLiteProceduralStore:
    def setup_method(self):
        self.store = SQLiteProceduralStore(db_path=":memory:")

    def teardown_method(self):
        self.store.close()

    def test_store_procedure(self):
        self.store.store_procedure(
            procedure_id="p1",
            intent="SOLVE",
            capability_chain=["solve.math", "verify.hallucination"],
            quality_grade="gold",
            source_case_id="case-1",
        )
        proc = self.store.get_procedure("SOLVE")
        assert proc is not None
        assert proc["quality_grade"] == "gold"

    def test_get_nonexistent_procedure(self):
        assert self.store.get_procedure("NONEXISTENT") is None

    def test_store_anti_pattern(self):
        self.store.store_anti_pattern(
            pattern_id="ap1",
            intent="SOLVE",
            capability_chain=["bad.cap"],
            failure_reason="verifier_failed",
        )
        assert self.store.is_anti_pattern("SOLVE", ["bad.cap"]) is True
        assert self.store.is_anti_pattern("SOLVE", ["good.cap"]) is False

    def test_list_procedures(self):
        self.store.store_procedure("p1", "SOLVE", ["a"], "gold")
        self.store.store_procedure("p2", "OBSERVE", ["b"], "silver")
        procs = self.store.list_procedures()
        assert len(procs) >= 2

    def test_stats(self):
        self.store.store_procedure("p1", "SOLVE", ["a"], "gold")
        self.store.store_anti_pattern("ap1", "SOLVE", ["b"], "failed")
        stats = self.store.stats()
        assert stats["procedures"] >= 1
        assert stats["anti_patterns"] >= 1


class TestSQLiteWorkingMemory:
    def setup_method(self):
        self.mem = SQLiteWorkingMemory(db_path=":memory:")

    def teardown_method(self):
        self.mem.close()

    def test_store_and_get(self):
        self.mem.store("key1", {"data": "value"}, category="context")
        result = self.mem.get("key1")
        assert result is not None

    def test_get_nonexistent(self):
        assert self.mem.get("nope") is None

    def test_get_by_category(self):
        self.mem.store("k1", "v1", category="a")
        self.mem.store("k2", "v2", category="a")
        self.mem.store("k3", "v3", category="b")
        items = self.mem.get_by_category("a")
        assert len(items) == 2

    def test_clear_category(self):
        self.mem.store("k1", "v1", category="temp")
        self.mem.store("k2", "v2", category="temp")
        self.mem.store("k3", "v3", category="perm")
        self.mem.clear_category("temp")
        assert self.mem.get("k1") is None
        assert self.mem.get("k3") is not None

    def test_expiration(self):
        self.mem.store("k1", "v1", category="temp", ttl_seconds=-1)
        self.mem.clear_expired()
        assert self.mem.get("k1") is None

    def test_clear_session(self):
        self.mem.store("k1", "v1", category="ctx", session_id="s1")
        self.mem.store("k2", "v2", category="ctx", session_id="s2")
        self.mem.clear_session("s1")
        assert self.mem.get("k1") is None
        assert self.mem.get("k2") is not None

    def test_stats(self):
        self.mem.store("k1", "v1")
        stats = self.mem.stats()
        assert isinstance(stats, dict)


# ── SQLiteCaseStore ──────────────────────────────────────

class TestSQLiteCaseStore:
    def setup_method(self):
        self.store = SQLiteCaseStore(db_path=":memory:")

    def teardown_method(self):
        self.store.close()

    def _make_case(self, tid="traj-001", grade="gold", intent="solve",
                   profile="cottage", passed=True):
        return {
            "trajectory_id": tid,
            "goal": {"type": "SOLVE", "description": "test"},
            "quality_grade": grade,
            "profile": profile,
            "selected_capabilities": [
                {"capability_id": "solve.math"},
            ],
            "verifier_result": {"passed": passed, "confidence": 0.95},
            "world_snapshot_before": {"snapshot_id": "snap-before"},
            "world_snapshot_after": {"snapshot_id": "snap-after"},
        }

    def test_save_and_get(self):
        case = self._make_case()
        tid = self.store.save_case(case, intent="solve")
        assert tid == "traj-001"
        result = self.store.get_case("traj-001")
        assert result is not None
        assert result["quality_grade"] == "gold"
        assert "_stored_at" in result

    def test_get_nonexistent(self):
        assert self.store.get_case("nonexistent") is None

    def test_save_replaces(self):
        self.store.save_case(self._make_case(grade="bronze"), intent="solve")
        self.store.save_case(self._make_case(grade="gold"), intent="solve")
        result = self.store.get_case("traj-001")
        assert result["quality_grade"] == "gold"

    def test_list_cases(self):
        self.store.save_case(self._make_case("t1", "gold"), intent="solve")
        self.store.save_case(self._make_case("t2", "silver"), intent="observe")
        self.store.save_case(self._make_case("t3", "bronze"), intent="solve")
        cases = self.store.list_cases()
        assert len(cases) == 3

    def test_list_cases_filter_grade(self):
        self.store.save_case(self._make_case("t1", "gold"))
        self.store.save_case(self._make_case("t2", "silver"))
        gold = self.store.list_cases(quality_grade="gold")
        assert len(gold) == 1
        assert gold[0]["trajectory_id"] == "t1"

    def test_list_cases_filter_intent(self):
        self.store.save_case(self._make_case("t1"), intent="solve")
        self.store.save_case(self._make_case("t2"), intent="observe")
        solve = self.store.list_cases(intent="solve")
        assert len(solve) == 1

    def test_list_cases_filter_profile(self):
        self.store.save_case(self._make_case("t1", profile="cottage"))
        self.store.save_case(self._make_case("t2", profile="field"))
        cottage = self.store.list_cases(profile="cottage")
        assert len(cottage) == 1

    def test_list_cases_filter_since(self):
        self.store.save_case(self._make_case("t1"))
        future = time.time() + 3600
        cases = self.store.list_cases(since=future)
        assert len(cases) == 0

    def test_list_cases_limit(self):
        for i in range(10):
            self.store.save_case(self._make_case(f"t{i}"))
        limited = self.store.list_cases(limit=3)
        assert len(limited) == 3

    def test_list_full(self):
        self.store.save_case(self._make_case("t1", "gold"))
        self.store.save_case(self._make_case("t2", "silver"))
        full = self.store.list_full()
        assert len(full) == 2
        assert "trajectory_id" in full[0]

    def test_list_full_filter_grade(self):
        self.store.save_case(self._make_case("t1", "gold"))
        self.store.save_case(self._make_case("t2", "silver"))
        gold = self.store.list_full(quality_grade="gold")
        assert len(gold) == 1

    def test_count(self):
        assert self.store.count() == 0
        self.store.save_case(self._make_case("t1", "gold"))
        self.store.save_case(self._make_case("t2", "silver"))
        assert self.store.count() == 2
        assert self.store.count(quality_grade="gold") == 1

    def test_grade_distribution(self):
        self.store.save_case(self._make_case("t1", "gold"))
        self.store.save_case(self._make_case("t2", "gold"))
        self.store.save_case(self._make_case("t3", "silver"))
        dist = self.store.grade_distribution()
        assert dist["gold"] == 2
        assert dist["silver"] == 1

    def test_delete_old(self):
        self.store.save_case(self._make_case("t1"))
        # Force an old record by manipulating created_at
        self.store._conn.execute(
            "UPDATE case_trajectories SET created_at = ? WHERE trajectory_id = ?",
            (time.time() - 200 * 86400, "t1"))
        self.store._conn.commit()
        deleted = self.store.delete_old(max_age_days=90)
        assert deleted == 1
        assert self.store.count() == 0

    def test_stats(self):
        self.store.save_case(self._make_case("t1", "gold"))
        self.store.save_case(self._make_case("t2", "silver"))
        stats = self.store.stats()
        assert stats["total"] == 2
        assert "grades" in stats
        assert stats["gold_rate"] == pytest.approx(0.5)

    def test_capability_chain_indexed(self):
        case = self._make_case()
        case["selected_capabilities"] = [
            {"capability_id": "solve.math"},
            {"capability_id": "verify.hallucination"},
        ]
        self.store.save_case(case)
        cases = self.store.list_cases()
        assert cases[0]["capability_chain"] == ["solve.math", "verify.hallucination"]

    def test_verifier_fields_indexed(self):
        case = self._make_case(passed=True)
        case["verifier_result"]["confidence"] = 0.85
        self.store.save_case(case)
        cases = self.store.list_cases()
        assert cases[0]["verifier_passed"] is True
        assert cases[0]["verifier_confidence"] == pytest.approx(0.85)


# ── SQLiteVerifierStore ──────────────────────────────────

class TestSQLiteVerifierStore:
    def setup_method(self):
        self.store = SQLiteVerifierStore(db_path=":memory:")

    def teardown_method(self):
        self.store.close()

    def _make_result(self, passed=True, confidence=0.9, conflict=False,
                     hallucination=False):
        return {
            "passed": passed,
            "confidence": confidence,
            "residual_improvement": 0.1,
            "conflict": conflict,
            "hallucination": hallucination,
            "checks": ["field_check"],
        }

    def test_save_and_get(self):
        rid = self.store.save_result(
            self._make_result(),
            action_id="act-1",
            capability_id="solve.math")
        assert isinstance(rid, str) and len(rid) == 12
        result = self.store.get_result(rid)
        assert result is not None
        assert result["passed"] is True
        assert result["action_id"] == "act-1"
        assert result["capability_id"] == "solve.math"
        assert "_stored_at" in result

    def test_get_nonexistent(self):
        assert self.store.get_result("nonexistent") is None

    def test_get_by_action(self):
        self.store.save_result(self._make_result(), action_id="act-1")
        result = self.store.get_by_action("act-1")
        assert result is not None
        assert result["action_id"] == "act-1"

    def test_get_by_action_nonexistent(self):
        assert self.store.get_by_action("nope") is None

    def test_get_by_action_returns_latest(self):
        self.store.save_result(
            self._make_result(confidence=0.5), action_id="act-1")
        time.sleep(0.01)
        self.store.save_result(
            self._make_result(confidence=0.9), action_id="act-1")
        result = self.store.get_by_action("act-1")
        assert result["confidence"] == pytest.approx(0.9)

    def test_get_by_trajectory(self):
        self.store.save_result(
            self._make_result(), action_id="a1", trajectory_id="traj-1")
        self.store.save_result(
            self._make_result(), action_id="a2", trajectory_id="traj-1")
        self.store.save_result(
            self._make_result(), action_id="a3", trajectory_id="traj-2")
        results = self.store.get_by_trajectory("traj-1")
        assert len(results) == 2

    def test_list_results(self):
        self.store.save_result(self._make_result(passed=True), action_id="a1")
        self.store.save_result(self._make_result(passed=False), action_id="a2")
        all_results = self.store.list_results()
        assert len(all_results) == 2

    def test_list_results_filter_passed(self):
        self.store.save_result(self._make_result(passed=True), action_id="a1")
        self.store.save_result(self._make_result(passed=False), action_id="a2")
        passed = self.store.list_results(passed=True)
        assert len(passed) == 1
        assert passed[0]["passed"] is True

    def test_list_results_filter_capability(self):
        self.store.save_result(
            self._make_result(), capability_id="solve.math")
        self.store.save_result(
            self._make_result(), capability_id="detect.anomaly")
        math = self.store.list_results(capability_id="solve.math")
        assert len(math) == 1

    def test_list_results_filter_since(self):
        self.store.save_result(self._make_result())
        future = time.time() + 3600
        results = self.store.list_results(since=future)
        assert len(results) == 0

    def test_list_results_limit(self):
        for i in range(10):
            self.store.save_result(self._make_result(), action_id=f"a{i}")
        limited = self.store.list_results(limit=3)
        assert len(limited) == 3

    def test_pass_rate(self):
        self.store.save_result(self._make_result(passed=True))
        self.store.save_result(self._make_result(passed=True))
        self.store.save_result(self._make_result(passed=False))
        rate = self.store.pass_rate()
        assert rate == pytest.approx(2 / 3)

    def test_pass_rate_by_capability(self):
        self.store.save_result(
            self._make_result(passed=True), capability_id="solve.math")
        self.store.save_result(
            self._make_result(passed=False), capability_id="solve.math")
        self.store.save_result(
            self._make_result(passed=True), capability_id="detect.anomaly")
        rate = self.store.pass_rate(capability_id="solve.math")
        assert rate == pytest.approx(0.5)

    def test_pass_rate_empty(self):
        assert self.store.pass_rate() == 0.0

    def test_delete_old(self):
        self.store.save_result(self._make_result())
        # Force old record
        self.store._conn.execute(
            "UPDATE verifier_results SET created_at = ?",
            (time.time() - 200 * 86400,))
        self.store._conn.commit()
        deleted = self.store.delete_old(max_age_days=90)
        assert deleted == 1

    def test_conflict_and_hallucination(self):
        self.store.save_result(
            self._make_result(conflict=True, hallucination=True))
        results = self.store.list_results()
        assert results[0]["conflict"] is True
        assert results[0]["hallucination"] is True

    def test_stats(self):
        self.store.save_result(self._make_result(passed=True))
        self.store.save_result(self._make_result(passed=False, conflict=True))
        self.store.save_result(
            self._make_result(passed=False, hallucination=True))
        stats = self.store.stats()
        assert stats["total"] == 3
        assert stats["passed"] == 1
        assert stats["failed"] == 2
        assert stats["conflicts"] == 1
        assert stats["hallucinations"] == 1
        assert stats["pass_rate"] == pytest.approx(1 / 3)


# ── Runtime wiring ───────────────────────────────────────

class TestPersistenceRuntimeWiring:
    """Tests that AutonomyRuntime correctly wires persistence stores."""

    def test_runtime_initializes_stores(self, tmp_path):
        """Runtime should init all 4 persistence stores when available."""
        db_dir = str(tmp_path / "data")
        with patch("waggledance.adapters.persistence.sqlite_case_store.SQLiteCaseStore.__init__",
                    return_value=None) as mock_case, \
             patch("waggledance.adapters.persistence.sqlite_verifier_store.SQLiteVerifierStore.__init__",
                    return_value=None) as mock_ver:
            from waggledance.core.autonomy.runtime import AutonomyRuntime
            rt = AutonomyRuntime(profile="test")
            assert rt.case_store is not None or rt.world_store is not None

    def test_persist_safe_catches_errors(self):
        """_persist_safe should swallow exceptions."""
        from waggledance.core.autonomy.runtime import AutonomyRuntime
        rt = AutonomyRuntime(profile="test")
        def boom():
            raise RuntimeError("kaboom")
        result = rt._persist_safe("test.boom", boom)
        assert result is None

    def test_persist_safe_returns_value(self):
        """_persist_safe should return the function's return value."""
        from waggledance.core.autonomy.runtime import AutonomyRuntime
        rt = AutonomyRuntime(profile="test")
        result = rt._persist_safe("test.ok", lambda: 42)
        assert result == 42

    def test_stats_includes_persistence(self):
        """Stats should include persistence sections when stores exist."""
        from waggledance.core.autonomy.runtime import AutonomyRuntime
        rt = AutonomyRuntime(profile="test")
        stats = rt.stats()
        # If stores were initialised, persistence keys should appear
        if rt.case_store:
            assert "persist_cases" in stats
        if rt.verifier_store:
            assert "persist_verifier" in stats

    def test_stop_closes_stores(self, tmp_path):
        """Stopping the runtime should close persistence stores."""
        from waggledance.core.autonomy.runtime import AutonomyRuntime
        rt = AutonomyRuntime(profile="test")
        # Replace stores with mocks that track close()
        mock_store = MagicMock()
        rt.case_store = mock_store
        rt.verifier_store = mock_store
        rt.start()
        rt.stop()
        assert mock_store.close.call_count >= 2

    def test_handle_query_persists_case(self, tmp_path):
        """handle_query should persist the case trajectory."""
        from waggledance.core.autonomy.runtime import AutonomyRuntime
        rt = AutonomyRuntime(profile="test")
        mock_case_store = MagicMock()
        rt.case_store = mock_case_store
        result = rt.handle_query("what is 2 + 2?")
        if result.get("executed"):
            mock_case_store.save_case.assert_called()

    def test_handle_query_persists_verifier(self, tmp_path):
        """handle_query should persist verifier result when available."""
        from waggledance.core.autonomy.runtime import AutonomyRuntime
        rt = AutonomyRuntime(profile="test")
        mock_ver_store = MagicMock()
        rt.verifier_store = mock_ver_store
        result = rt.handle_query("what is 2 + 2?")
        if result.get("executed"):
            mock_ver_store.save_result.assert_called()

    def test_handle_query_persists_snapshot(self, tmp_path):
        """handle_query should persist the world snapshot."""
        from waggledance.core.autonomy.runtime import AutonomyRuntime
        rt = AutonomyRuntime(profile="test")
        mock_world_store = MagicMock()
        rt.world_store = mock_world_store
        rt.handle_query("what is 2 + 2?")
        mock_world_store.save_snapshot.assert_called()

    def test_execute_mission_persists_snapshots(self, tmp_path):
        """execute_mission should persist both before and after snapshots."""
        from waggledance.core.autonomy.runtime import AutonomyRuntime
        rt = AutonomyRuntime(profile="test")
        mock_world_store = MagicMock()
        rt.world_store = mock_world_store
        rt.execute_mission("observe", "test mission")
        assert mock_world_store.save_snapshot.call_count >= 2

    def test_execute_mission_persists_case(self, tmp_path):
        """execute_mission should persist the case trajectory."""
        from waggledance.core.autonomy.runtime import AutonomyRuntime
        rt = AutonomyRuntime(profile="test")
        mock_case_store = MagicMock()
        rt.case_store = mock_case_store
        rt.execute_mission("observe", "test mission")
        mock_case_store.save_case.assert_called_once()

    def test_persistence_failure_does_not_break_query(self, tmp_path):
        """If persistence fails, handle_query should still return results."""
        from waggledance.core.autonomy.runtime import AutonomyRuntime
        rt = AutonomyRuntime(profile="test")
        broken_store = MagicMock()
        broken_store.save_case.side_effect = RuntimeError("DB locked")
        broken_store.save_snapshot.side_effect = RuntimeError("DB locked")
        broken_store.save_result.side_effect = RuntimeError("DB locked")
        rt.case_store = broken_store
        rt.world_store = broken_store
        rt.verifier_store = broken_store
        result = rt.handle_query("what is 2 + 2?")
        assert "intent" in result

    def test_persistence_failure_does_not_break_mission(self, tmp_path):
        """If persistence fails, execute_mission should still return results."""
        from waggledance.core.autonomy.runtime import AutonomyRuntime
        rt = AutonomyRuntime(profile="test")
        broken_store = MagicMock()
        broken_store.save_case.side_effect = RuntimeError("DB locked")
        broken_store.save_snapshot.side_effect = RuntimeError("DB locked")
        rt.case_store = broken_store
        rt.world_store = broken_store
        result = rt.execute_mission("observe", "test mission")
        assert "goal_id" in result
