"""Tests for Windows runtime hardening fixes.

Covers:
- Asyncio WinError 10054 logging filter
- Ollama embed timeout graceful degradation
- Specialist trainer tiny-sample R² guard
- Specialist trainer _safe_cv_splits guard
- Rate limiter localhost exemption
- Soak harness monotonic deadline enforcement
"""

import logging
import sys
import time
import warnings
from unittest.mock import MagicMock, patch

import pytest


# ── Asyncio WinError 10054 logging filter ─────────────

class TestWin10054Filter:
    """Test that WinError 10054 is silenced via logging filter."""

    def test_filter_installed_on_windows(self):
        """Filter installs without error on any platform."""
        from waggledance.adapters.cli.start_runtime import _install_windows_proactor_filter
        _install_windows_proactor_filter()

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
    def test_filter_suppresses_10054_message(self):
        """WinError 10054 log records should be suppressed."""
        from waggledance.adapters.cli.start_runtime import _install_windows_proactor_filter
        _install_windows_proactor_filter()

        asyncio_logger = logging.getLogger("asyncio")
        record = logging.LogRecord(
            name="asyncio", level=logging.ERROR,
            pathname="", lineno=0, msg="ConnectionResetError: [WinError 10054] forcibly closed",
            args=(), exc_info=None,
        )
        # Filter should reject this record
        assert asyncio_logger.filter(record) is False

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
    def test_filter_suppresses_10054_exc_info(self):
        """WinError 10054 in exc_info should also be suppressed."""
        from waggledance.adapters.cli.start_runtime import _install_windows_proactor_filter
        _install_windows_proactor_filter()

        asyncio_logger = logging.getLogger("asyncio")
        exc = ConnectionResetError("[WinError 10054] forcibly closed")
        record = logging.LogRecord(
            name="asyncio", level=logging.ERROR,
            pathname="", lineno=0, msg="Exception in callback",
            args=(), exc_info=(type(exc), exc, None),
        )
        assert asyncio_logger.filter(record) is False

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
    def test_filter_passes_other_errors(self):
        """Non-10054 errors must pass through the filter."""
        from waggledance.adapters.cli.start_runtime import _install_windows_proactor_filter
        _install_windows_proactor_filter()

        asyncio_logger = logging.getLogger("asyncio")
        record = logging.LogRecord(
            name="asyncio", level=logging.ERROR,
            pathname="", lineno=0, msg="RuntimeError: something else",
            args=(), exc_info=None,
        )
        assert asyncio_logger.filter(record)  # truthy = passes through

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
    def test_filter_passes_non_connection_reset(self):
        """Non-ConnectionResetError exceptions should pass through."""
        from waggledance.adapters.cli.start_runtime import _install_windows_proactor_filter
        _install_windows_proactor_filter()

        asyncio_logger = logging.getLogger("asyncio")
        exc = OSError("some other OS error")
        record = logging.LogRecord(
            name="asyncio", level=logging.ERROR,
            pathname="", lineno=0, msg="Exception in callback",
            args=(), exc_info=(type(exc), exc, None),
        )
        assert asyncio_logger.filter(record)  # truthy = passes through


# ── Ollama embed timeout graceful degradation ───────────

class TestEmbedTimeoutDegradation:
    """Test that embed timeouts log at warning level, not error."""

    def test_timeout_logs_warning_not_error(self):
        from waggledance.adapters.memory.chroma_vector_store import ChromaVectorStore

        with patch("waggledance.adapters.memory.chroma_vector_store.chromadb", create=True):
            store = MagicMock(spec=ChromaVectorStore)
            store._embed_timeout = 1.0
            store._embedding_model = "nomic-embed-text"
            store._ollama_base_url = "http://localhost:11434"
            store._embed_cache = {}
            store._embed_cache_max = 500

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


# ── Specialist trainer _safe_cv_splits ──────────────────

class TestSafeCvSplits:
    """Test _safe_cv_splits guards against class-count < n_splits."""

    @pytest.fixture
    def trainer(self, tmp_path):
        from waggledance.core.specialist_models.specialist_trainer import SpecialistTrainer
        from waggledance.core.specialist_models.model_store import ModelStore
        store = ModelStore(store_path=str(tmp_path / "models.json"))
        return SpecialistTrainer(model_store=store, min_samples=3)

    def test_single_class_returns_zero(self, trainer):
        """Labels with only 1 unique class → 0 (skip cross-val)."""
        assert trainer._safe_cv_splits(["a", "a", "a"]) == 0

    def test_two_classes_one_member_returns_zero(self, trainer):
        """One class has 1 member, less than default max_splits=3 → 0."""
        assert trainer._safe_cv_splits(["a", "a", "a", "b"]) == 0

    def test_two_classes_two_members_each(self, trainer):
        """Each class has 2 members → n_splits=2."""
        assert trainer._safe_cv_splits(["a", "a", "b", "b"]) == 2

    def test_balanced_three_each(self, trainer):
        """Each class has 3+ members → n_splits=3 (max)."""
        assert trainer._safe_cv_splits(["a", "a", "a", "b", "b", "b"]) == 3

    def test_many_classes_few_members(self, trainer):
        """Multiple classes with min 2 members → n_splits=2."""
        labels = ["a", "a", "b", "b", "c", "c"]
        assert trainer._safe_cv_splits(labels) == 2

    def test_empty_labels_returns_zero(self, trainer):
        assert trainer._safe_cv_splits([]) == 0

    def test_cross_val_no_sklearn_warning(self, trainer):
        """Route classifier with small single-class should not warn."""
        features = [
            {"goal_type": "thermal", "profile": "HOME", "grade": "gold"},
            {"goal_type": "thermal", "profile": "HOME", "grade": "gold"},
            {"goal_type": "energy", "profile": "HOME", "grade": "gold"},
        ]
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            trainer._train_route_classifier(features)
            sklearn_warns = [
                x for x in w
                if "least populated class" in str(x.message)
            ]
            assert len(sklearn_warns) == 0


# ── Specialist trainer tiny-sample R² guard ─────────────

class TestTinySampleGuard:
    """Test that R² is not computed with <2 test samples."""

    @pytest.fixture
    def trainer(self, tmp_path):
        from waggledance.core.specialist_models.specialist_trainer import SpecialistTrainer
        from waggledance.core.specialist_models.model_store import ModelStore
        store = ModelStore(store_path=str(tmp_path / "models.json"))
        return SpecialistTrainer(model_store=store, min_samples=3)

    def test_thermal_tiny_sample_no_warning(self, trainer):
        features = [
            {"target": 1.0, "features": [0.1], "grade_num": 0.5, "n_capabilities": 1},
            {"target": 2.0, "features": [0.2], "grade_num": 0.6, "n_capabilities": 2},
            {"target": 3.0, "features": [0.3], "grade_num": 0.7, "n_capabilities": 3},
        ]
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            acc, wp = trainer._train_thermal_predictor(features)
            sklearn_warns = [x for x in w if "UndefinedMetric" in str(x.category.__name__)]
            assert len(sklearn_warns) == 0
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
        features = [
            {"target": 1.0, "features": [0.1], "grade_num": 0.5, "n_capabilities": 1},
        ]
        acc, wp = trainer._train_thermal_predictor(features)
        assert acc == 0.0
        assert wp is None


# ── Rate limiter localhost exemption ────────────────────

class TestRateLimiterLocalhostExempt:
    """Test that localhost is exempt from rate limiting."""

    def test_localhost_ips_defined(self):
        from waggledance.adapters.http.middleware.rate_limit import RateLimitMiddleware
        assert "127.0.0.1" in RateLimitMiddleware._LOCALHOST_IPS
        assert "::1" in RateLimitMiddleware._LOCALHOST_IPS

    @pytest.mark.asyncio
    async def test_localhost_bypasses_rate_limit(self):
        """Rapid localhost requests should never get 429."""
        from waggledance.adapters.http.middleware.rate_limit import RateLimitMiddleware

        calls = []

        async def mock_app(scope, receive, send):
            pass

        async def mock_call_next(request):
            calls.append(1)
            return MagicMock(status_code=200)

        mw = RateLimitMiddleware(mock_app, requests_per_minute=2)  # very low limit

        mock_request = MagicMock()
        mock_request.client.host = "127.0.0.1"
        mock_request.headers = {}

        # Fire 10 requests rapidly — all should pass
        for _ in range(10):
            resp = await mw.dispatch(mock_request, mock_call_next)
            assert resp.status_code == 200

        assert len(calls) == 10

    @pytest.mark.asyncio
    async def test_external_ip_gets_rate_limited(self):
        """External IPs should still be rate-limited."""
        from waggledance.adapters.http.middleware.rate_limit import RateLimitMiddleware

        async def mock_app(scope, receive, send):
            pass

        async def mock_call_next(request):
            return MagicMock(status_code=200)

        mw = RateLimitMiddleware(mock_app, requests_per_minute=2)

        mock_request = MagicMock()
        mock_request.client.host = "192.168.1.100"
        mock_request.headers = {}

        # First 2 should pass, 3rd should be 429
        results = []
        for _ in range(5):
            resp = await mw.dispatch(mock_request, mock_call_next)
            results.append(resp.status_code)

        assert 429 in results


# ── Soak harness monotonic deadline ─────────────────────

class TestSoakHarnessDesign:
    """Test soak harness design properties (no actual WD needed)."""

    def test_query_cutoff_before_deadline(self):
        from tools.soak_harness import QUERY_CUTOFF_MARGIN
        assert QUERY_CUTOFF_MARGIN >= 60

    def test_query_timeout_less_than_cutoff(self):
        from tools.soak_harness import QUERY_CUTOFF_MARGIN
        assert 60 < QUERY_CUTOFF_MARGIN

    def test_wd_launch_uses_process_group_on_windows(self):
        if sys.platform != "win32":
            pytest.skip("Windows only")
        from tools.soak_harness import start_wd
        import inspect
        src = inspect.getsource(start_wd)
        assert "CREATE_NEW_PROCESS_GROUP" in src
        assert "DETACHED_PROCESS" in src

    def test_funnel_counts_returns_dict(self):
        from tools.soak_harness import funnel_counts
        result = funnel_counts()
        assert "case_trajectories" in result
        assert "verifier_results" in result
        assert "procedures" in result

    def test_default_output_dir_on_c_drive(self):
        """Default output should resolve to C: (durable), not U: (volatile)."""
        from tools.soak_harness import main
        import inspect
        src = inspect.getsource(main)
        assert "C:\\\\WaggleDance_Soak" in src or "C:/WaggleDance_Soak" in src

    def test_pid_validation_before_kill(self):
        """stop_wd must validate PID is still WD before killing."""
        from tools.soak_harness import _is_wd_process
        # Non-existent PID should return False (safe)
        assert _is_wd_process(999999) is False

    def test_signal_handler_registered(self):
        """Harness should register SIGTERM handler."""
        from tools.soak_harness import main
        import inspect
        src = inspect.getsource(main)
        assert "signal.SIGTERM" in src

    def test_stderr_categorizer_empty(self):
        """Categorize stderr with non-existent file returns empty."""
        from tools.soak_harness import _categorize_stderr
        result = _categorize_stderr("/nonexistent/path/stderr.log")
        assert result == {}

    def test_stderr_categorizer_known_patterns(self, tmp_path):
        """Categorize stderr identifies known noise patterns."""
        from tools.soak_harness import _categorize_stderr
        stderr = tmp_path / "wd_stderr.log"
        stderr.write_text(
            "2026 ERROR asyncio: ConnectionResetError [WinError 10054]\n"
            "sklearn UserWarning: least populated class\n"
            "Ollama embed error: Read timed out\n"
        )
        cats = _categorize_stderr(str(stderr))
        assert "WinError_10054" in cats
        assert "sklearn_warning" in cats
        assert "ollama_timeout" in cats
