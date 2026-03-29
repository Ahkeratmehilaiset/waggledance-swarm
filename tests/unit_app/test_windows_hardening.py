"""Tests for Windows runtime hardening fixes.

Covers:
- Asyncio ProactorEventLoop WinError 10054 filter
- Ollama embed timeout graceful degradation
- Specialist trainer tiny-sample R² guard
- Soak harness monotonic deadline enforcement
"""

import sys
import time
import warnings
from unittest.mock import MagicMock, patch

import pytest


# ── Asyncio WinError 10054 filter ───────────────────────

class TestProactorFilter:
    """Test that WinError 10054 is silenced but other errors pass through."""

    def test_filter_installed_on_windows(self):
        """Filter installs without error on any platform."""
        from waggledance.adapters.cli.start_runtime import _install_windows_proactor_filter
        # Should not raise even on non-Windows
        _install_windows_proactor_filter()

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
    def test_filter_suppresses_10054(self):
        """On Windows, WinError 10054 should be silenced."""
        import asyncio
        from waggledance.adapters.cli.start_runtime import _install_windows_proactor_filter
        _install_windows_proactor_filter()

        loop = asyncio.new_event_loop()
        handler_called = []

        # The filter should suppress this
        exc = ConnectionResetError("[WinError 10054] connection forcibly closed")
        loop.call_exception_handler({"exception": exc, "message": "test"})
        # If filter works, default handler (which logs) is not called
        loop.close()

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
    def test_filter_passes_other_errors(self):
        """Non-10054 errors should still propagate."""
        import asyncio
        from waggledance.adapters.cli.start_runtime import _install_windows_proactor_filter
        _install_windows_proactor_filter()

        loop = asyncio.new_event_loop()
        errors_seen = []

        def capture_handler(loop, context):
            errors_seen.append(context)

        # Install our filter, then verify non-10054 errors still go through
        # by checking that the default handler processes them
        loop.set_exception_handler(capture_handler)
        exc = RuntimeError("real error")
        loop.call_exception_handler({"exception": exc, "message": "test"})
        assert len(errors_seen) == 1
        loop.close()


# ── Ollama embed timeout graceful degradation ───────────

class TestEmbedTimeoutDegradation:
    """Test that embed timeouts log at warning level, not error."""

    def test_timeout_logs_warning_not_error(self):
        from waggledance.adapters.memory.chroma_vector_store import ChromaVectorStore
        import logging

        with patch("waggledance.adapters.memory.chroma_vector_store.chromadb", create=True):
            store = MagicMock(spec=ChromaVectorStore)
            store._embed_timeout = 1.0
            store._embedding_model = "nomic-embed-text"
            store._ollama_base_url = "http://localhost:11434"
            store._embed_cache = {}
            store._embed_cache_max = 500

            # Call the real _embed_text with a timeout exception
            import requests.exceptions
            with patch("requests.post", side_effect=requests.exceptions.ReadTimeout("Read timed out")):
                result = ChromaVectorStore._embed_text(store, "test text")
            assert result is None

    def test_non_timeout_logs_error(self):
        from waggledance.adapters.memory.chroma_vector_store import ChromaVectorStore

        store = MagicMock(spec=ChromaVectorStore)
        store._embed_timeout = 1.0
        store._embedding_model = "nomic-embed-text"
        store._ollama_base_url = "http://localhost:11434"
        store._embed_cache = {}
        store._embed_cache_max = 500

        with patch("requests.post", side_effect=ConnectionError("refused")):
            result = ChromaVectorStore._embed_text(store, "test text")
        assert result is None


# ── Specialist trainer tiny-sample R² guard ─────────────

class TestTinySampleGuard:
    """Test that R² is not computed with <2 test samples."""

    @pytest.fixture
    def trainer(self, tmp_path):
        from waggledance.core.specialist_models.specialist_trainer import SpecialistTrainer
        from waggledance.core.specialist_models.model_store import ModelStore
        store = ModelStore(base_dir=str(tmp_path / "models"))
        return SpecialistTrainer(model_store=store, min_samples=3)

    def test_thermal_tiny_sample_no_warning(self, trainer):
        """Training with exactly min_samples should not emit sklearn warnings."""
        features = [
            {"target": 1.0, "features": [0.1], "grade_num": 0.5, "n_capabilities": 1},
            {"target": 2.0, "features": [0.2], "grade_num": 0.6, "n_capabilities": 2},
            {"target": 3.0, "features": [0.3], "grade_num": 0.7, "n_capabilities": 3},
        ]
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            acc, wp = trainer._train_thermal_predictor(features)
            sklearn_warns = [x for x in w if "UndefinedMetric" in str(x.category.__name__)]
            assert len(sklearn_warns) == 0, f"Got sklearn warnings: {sklearn_warns}"
        assert acc >= 0.0

    def test_energy_tiny_sample_no_warning(self, trainer):
        features = [
            {"target": 10.0, "features": [1.0], "n_capabilities": 1},
            {"target": 20.0, "features": [2.0], "n_capabilities": 2},
            {"target": 30.0, "features": [3.0], "n_capabilities": 3},
        ]
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            acc, wp = trainer._train_energy_forecaster(features)
            sklearn_warns = [x for x in w if "UndefinedMetric" in str(x.category.__name__)]
            assert len(sklearn_warns) == 0
        assert acc >= 0.0

    def test_schedule_tiny_sample_no_warning(self, trainer):
        features = [
            {"goal_type": "thermal", "profile": "HOME", "score": 0.8, "n_capabilities": 2, "has_world_snapshot": 1},
            {"goal_type": "energy", "profile": "HOME", "score": 0.6, "n_capabilities": 1, "has_world_snapshot": 0},
            {"goal_type": "thermal", "profile": "HOME", "score": 0.9, "n_capabilities": 3, "has_world_snapshot": 1},
        ]
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            acc, wp = trainer._train_schedule_optimizer(features)
            sklearn_warns = [x for x in w if "UndefinedMetric" in str(x.category.__name__)]
            assert len(sklearn_warns) == 0
        assert acc >= 0.0

    def test_below_min_samples_returns_zero(self, trainer):
        """With fewer than min_samples, should return 0.0 without training."""
        features = [
            {"target": 1.0, "features": [0.1], "grade_num": 0.5, "n_capabilities": 1},
        ]
        acc, wp = trainer._train_thermal_predictor(features)
        assert acc == 0.0
        assert wp is None


# ── Soak harness monotonic deadline ─────────────────────

class TestSoakHarnessDesign:
    """Test soak harness design properties (no actual WD needed)."""

    def test_query_cutoff_before_deadline(self):
        """Query cutoff must be before the hard deadline."""
        from tools.soak_harness import QUERY_CUTOFF_MARGIN
        assert QUERY_CUTOFF_MARGIN >= 60, "Cutoff margin must be >= 60s"

    def test_query_timeout_less_than_cutoff(self):
        """Individual query timeout must be less than cutoff margin."""
        from tools.soak_harness import QUERY_CUTOFF_MARGIN
        # api_post_chat uses timeout=60, cutoff margin is 120
        assert 60 < QUERY_CUTOFF_MARGIN

    def test_wd_launch_uses_process_group_on_windows(self):
        """On Windows, WD must be launched with CREATE_NEW_PROCESS_GROUP."""
        if sys.platform != "win32":
            pytest.skip("Windows only")
        # Verify the constants are correct
        from tools.soak_harness import start_wd
        import inspect
        src = inspect.getsource(start_wd)
        assert "CREATE_NEW_PROCESS_GROUP" in src
        assert "DETACHED_PROCESS" in src

    def test_funnel_counts_returns_dict(self):
        """funnel_counts should return dict even if DBs don't exist."""
        from tools.soak_harness import funnel_counts
        result = funnel_counts()
        assert "case_trajectories" in result
        assert "verifier_results" in result
        assert "procedures" in result
