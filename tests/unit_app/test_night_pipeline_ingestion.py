"""Tests for night pipeline case ingestion and scheduler triggering.

Covers:
- case_store -> run_learning_cycle ingestion
- idempotent cursor / watermark behavior
- scheduler trigger conditions
- repeated trigger does not endlessly reprocess same cases
"""

import time
import threading
from unittest.mock import MagicMock, patch

import pytest


# ── Watermark + fetch_pending tests ─────────────────────

class TestCaseStoreWatermark:
    """Test the learning watermark and fetch_pending in SQLiteCaseStore."""

    @pytest.fixture
    def store(self, tmp_path):
        from waggledance.adapters.persistence.sqlite_case_store import SQLiteCaseStore
        return SQLiteCaseStore(db_path=str(tmp_path / "cases.db"))

    def _make_case(self, tid="t1", grade="bronze"):
        return {
            "trajectory_id": tid,
            "goal": None,
            "world_snapshot_before": None,
            "selected_capabilities": [],
            "actions": [],
            "world_snapshot_after": None,
            "verifier_result": {},
            "quality_grade": grade,
            "canonical_id": "",
            "profile": "HOME",
            "counterfactual_alternatives": [],
            "trajectory_origin": "observed",
            "synthetic": False,
            "created_at": "2026-03-28T10:00:00+00:00",
        }

    def test_watermark_initially_zero(self, store):
        assert store.get_watermark() == 0.0

    def test_set_and_get_watermark(self, store):
        store.set_watermark(1000.0)
        assert store.get_watermark() == 1000.0

    def test_watermark_idempotent_update(self, store):
        store.set_watermark(1000.0)
        store.set_watermark(2000.0)
        assert store.get_watermark() == 2000.0

    def test_fetch_pending_returns_new_cases(self, store):
        store.save_case(self._make_case("t1"), intent="chat")
        store.save_case(self._make_case("t2"), intent="chat")
        pending = store.fetch_pending()
        assert len(pending) == 2

    def test_fetch_pending_respects_watermark(self, store):
        store.save_case(self._make_case("t1"), intent="chat")
        time.sleep(0.05)
        wm = time.time()
        store.set_watermark(wm)
        time.sleep(0.05)
        store.save_case(self._make_case("t2"), intent="chat")
        pending = store.fetch_pending()
        assert len(pending) == 1
        assert pending[0]["trajectory_id"] == "t2"

    def test_pending_count_matches_fetch(self, store):
        store.save_case(self._make_case("t1"), intent="chat")
        store.save_case(self._make_case("t2"), intent="chat")
        assert store.pending_count() == 2
        assert len(store.fetch_pending()) == store.pending_count()

    def test_no_reprocess_after_watermark_advance(self, store):
        """After advancing watermark past all cases, fetch returns empty."""
        store.save_case(self._make_case("t1"), intent="chat")
        store.save_case(self._make_case("t2"), intent="chat")
        pending = store.fetch_pending()
        assert len(pending) == 2
        max_ts = max(p["_stored_at"] for p in pending)
        store.set_watermark(max_ts)
        assert store.fetch_pending() == []
        assert store.pending_count() == 0


# ── CaseTrajectory.from_stored_dict tests ───────────────

class TestCaseTrajectoryFromStoredDict:

    def test_basic_reconstruction(self):
        from waggledance.core.domain.autonomy import CaseTrajectory, QualityGrade
        d = {
            "trajectory_id": "abc123",
            "quality_grade": "gold",
            "selected_capabilities": [
                {"capability_id": "solve.thermal", "category": "solve"}
            ],
            "verifier_result": {"passed": True, "confidence": 0.9},
            "profile": "HOME",
            "created_at": "2026-03-28T10:00:00+00:00",
        }
        case = CaseTrajectory.from_stored_dict(d)
        assert case.trajectory_id == "abc123"
        assert case.quality_grade == QualityGrade.GOLD
        assert len(case.selected_capabilities) == 1
        assert case.selected_capabilities[0].capability_id == "solve.thermal"
        assert case.profile == "HOME"

    def test_unknown_grade_defaults_bronze(self):
        from waggledance.core.domain.autonomy import CaseTrajectory, QualityGrade
        d = {"quality_grade": "legendary"}
        case = CaseTrajectory.from_stored_dict(d)
        assert case.quality_grade == QualityGrade.BRONZE

    def test_missing_fields_safe(self):
        from waggledance.core.domain.autonomy import CaseTrajectory
        case = CaseTrajectory.from_stored_dict({})
        assert case.trajectory_id  # auto-generated
        assert case.selected_capabilities == []


# ── run_learning_cycle ingestion tests ──────────────────

class TestRunLearningCycleIngestion:
    """Test that run_learning_cycle loads pending cases from store."""

    @pytest.fixture
    def service_with_store(self, tmp_path):
        from waggledance.adapters.persistence.sqlite_case_store import SQLiteCaseStore
        from waggledance.application.services.autonomy_service import AutonomyService

        store = SQLiteCaseStore(db_path=str(tmp_path / "cases.db"))
        runtime = MagicMock()
        runtime.case_store = store

        pipeline = MagicMock()
        result_mock = MagicMock()
        result_mock.success = True
        result_mock.cases_built = 0
        result_mock.legacy_converted = 0
        result_mock.models_trained = 0
        result_mock.gold_count = 0
        result_mock.silver_count = 0
        result_mock.bronze_count = 0
        result_mock.quarantine_count = 0
        result_mock.canary_results = {}
        result_mock.to_dict.return_value = {
            "success": True, "cases_built": 0, "models_trained": 0,
        }
        pipeline.run_cycle.return_value = result_mock
        pipeline.is_running = False
        pipeline._history = []

        svc = AutonomyService(
            runtime=runtime,
            night_pipeline=pipeline,
            profile="HOME",
        )
        return svc, store, pipeline

    def _make_case(self, tid):
        return {
            "trajectory_id": tid,
            "quality_grade": "bronze",
            "selected_capabilities": [],
            "verifier_result": {},
            "profile": "HOME",
            "created_at": "2026-03-28T10:00:00+00:00",
        }

    def test_loads_pending_cases(self, service_with_store):
        svc, store, pipeline = service_with_store
        store.save_case(self._make_case("t1"), intent="chat")
        store.save_case(self._make_case("t2"), intent="chat")

        svc.run_learning_cycle()

        # Pipeline should have been called with day_cases
        call_args = pipeline.run_cycle.call_args
        day_cases = call_args.kwargs.get("day_cases") or call_args[1].get("day_cases")
        assert day_cases is not None
        assert len(day_cases) == 2

    def test_advances_watermark_after_success(self, service_with_store):
        svc, store, pipeline = service_with_store
        store.save_case(self._make_case("t1"), intent="chat")

        assert store.get_watermark() == 0.0
        svc.run_learning_cycle()
        assert store.get_watermark() > 0.0

    def test_repeated_trigger_no_reprocess(self, service_with_store):
        """Second trigger should find 0 pending cases."""
        svc, store, pipeline = service_with_store
        store.save_case(self._make_case("t1"), intent="chat")

        svc.run_learning_cycle()
        pipeline.run_cycle.reset_mock()

        svc.run_learning_cycle()
        # Second call: day_cases should be None or empty (no pending)
        call_args = pipeline.run_cycle.call_args
        day_cases = call_args.kwargs.get("day_cases") or call_args[1].get("day_cases")
        assert day_cases is None  # nothing pending

    def test_explicit_day_cases_bypass_store(self, service_with_store):
        """When day_cases is explicitly provided, don't load from store."""
        from waggledance.core.domain.autonomy import CaseTrajectory
        svc, store, pipeline = service_with_store
        store.save_case(self._make_case("t1"), intent="chat")

        explicit = [CaseTrajectory(trajectory_id="explicit1")]
        svc.run_learning_cycle(day_cases=explicit)

        call_args = pipeline.run_cycle.call_args
        day_cases = call_args.kwargs.get("day_cases") or call_args[1].get("day_cases")
        assert len(day_cases) == 1
        assert day_cases[0].trajectory_id == "explicit1"


# ── Scheduler trigger condition tests ───────────────────

class TestLearningScheduler:

    @pytest.fixture
    def svc(self, tmp_path):
        from waggledance.adapters.persistence.sqlite_case_store import SQLiteCaseStore
        from waggledance.application.services.autonomy_service import AutonomyService

        store = SQLiteCaseStore(db_path=str(tmp_path / "cases.db"))
        runtime = MagicMock()
        runtime.case_store = store

        pipeline = MagicMock()
        pipeline.is_running = False
        pipeline._history = []
        result_mock = MagicMock()
        result_mock.success = True
        result_mock.cases_built = 0
        result_mock.legacy_converted = 0
        result_mock.models_trained = 0
        result_mock.gold_count = 0
        result_mock.silver_count = 0
        result_mock.bronze_count = 0
        result_mock.quarantine_count = 0
        result_mock.canary_results = {}
        result_mock.to_dict.return_value = {"success": True}
        pipeline.run_cycle.return_value = result_mock

        svc = AutonomyService(
            runtime=runtime,
            night_pipeline=pipeline,
            profile="HOME",
        )
        # Stop auto-started scheduler for controlled testing
        svc.stop_learning_scheduler()
        return svc, store, pipeline

    def _make_case(self, tid):
        return {
            "trajectory_id": tid,
            "quality_grade": "bronze",
            "selected_capabilities": [],
            "verifier_result": {},
            "profile": "HOME",
            "created_at": "2026-03-28T10:00:00+00:00",
        }

    def test_no_trigger_below_min_pending(self, svc):
        svc_obj, store, pipeline = svc
        # Add fewer than min_pending cases
        for i in range(5):
            store.save_case(self._make_case(f"t{i}"), intent="chat")
        svc_obj._maybe_trigger_learning()
        pipeline.run_cycle.assert_not_called()

    def test_triggers_above_min_pending(self, svc):
        svc_obj, store, pipeline = svc
        for i in range(15):
            store.save_case(self._make_case(f"t{i}"), intent="chat")
        svc_obj._maybe_trigger_learning()
        pipeline.run_cycle.assert_called_once()

    def test_no_trigger_when_pipeline_running(self, svc):
        svc_obj, store, pipeline = svc
        pipeline.is_running = True
        for i in range(15):
            store.save_case(self._make_case(f"t{i}"), intent="chat")
        svc_obj._maybe_trigger_learning()
        pipeline.run_cycle.assert_not_called()

    def test_no_trigger_under_heavy_load(self, svc):
        svc_obj, store, pipeline = svc
        from waggledance.core.autonomy.resource_kernel import LoadLevel
        svc_obj._resource_kernel.can_accept_learning = MagicMock(return_value=False)
        for i in range(15):
            store.save_case(self._make_case(f"t{i}"), intent="chat")
        svc_obj._maybe_trigger_learning()
        pipeline.run_cycle.assert_not_called()

    def test_rate_limit_prevents_rapid_triggers(self, svc):
        svc_obj, store, pipeline = svc
        for i in range(15):
            store.save_case(self._make_case(f"t{i}"), intent="chat")

        svc_obj._maybe_trigger_learning()
        assert pipeline.run_cycle.call_count == 1

        # Immediate second call should be rate-limited
        pipeline.run_cycle.reset_mock()
        svc_obj._maybe_trigger_learning()
        pipeline.run_cycle.assert_not_called()
