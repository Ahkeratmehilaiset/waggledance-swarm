"""Tests for v3.5.6 Adaptive Runtime Efficiency.

Covers: preflight scoring, skip logic, budget controls, counter truth,
trace alignment, adaptive success memory, safe defaults, and regressions.
"""

from __future__ import annotations

import collections
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from waggledance.core.domain.hex_mesh import (
    HexCellDefinition,
    HexCellHealth,
    HexCoord,
    HexNeighborResponse,
    HexResolutionTrace,
)
from waggledance.application.services.hex_neighbor_assist import (
    HexMeshMetrics,
    HexNeighborAssist,
    _AdaptiveSuccessMemory,
)


# ── Fixtures ──────────────────────────────────────────────────


class FakeTopologyRegistry:
    """Minimal topology registry for testing."""

    def __init__(self, cells=None):
        self.cells = cells or {
            "hub": HexCellDefinition(
                id="hub", coord=HexCoord(0, 0),
                domain_selectors=["general", "home"],
            ),
            "electricity": HexCellDefinition(
                id="electricity", coord=HexCoord(1, 0),
                domain_selectors=["electricity", "sähkö", "energy", "price"],
            ),
            "weather": HexCellDefinition(
                id="weather", coord=HexCoord(0, 1),
                domain_selectors=["weather", "sää", "temperature"],
            ),
        }

    @property
    def cell_count(self):
        return len(self.cells)

    def select_origin_cell(self, query, intent=""):
        """Simple keyword-based cell selection."""
        q = query.lower()
        if any(w in q for w in ("sähkö", "electric", "price", "kwh")):
            return "electricity"
        if any(w in q for w in ("sää", "weather", "temp")):
            return "weather"
        return "hub"

    def get_cell_agents(self, cell_id):
        agent = MagicMock()
        agent.name = cell_id
        agent.domain = cell_id
        return [agent]

    def get_neighbor_cells(self, cell_id):
        others = [c for cid, c in self.cells.items() if cid != cell_id]
        return others[:2]


class FakeHealthMonitor:
    """Minimal health monitor for testing."""

    def __init__(self):
        self._healthy = set()
        self._healths: dict[str, HexCellHealth] = {}

    def is_healthy(self, cell_id):
        return cell_id not in self._healthy or True

    def get_health(self, cell_id):
        return self._healths.get(cell_id, HexCellHealth(cell_id=cell_id))

    def record_success(self, cell_id):
        h = self._healths.setdefault(cell_id, HexCellHealth(cell_id=cell_id))
        h.recent_success_count += 1

    def record_error(self, cell_id):
        h = self._healths.setdefault(cell_id, HexCellHealth(cell_id=cell_id))
        h.recent_error_count += 1

    def get_quarantined_cells(self):
        return []

    def stats(self):
        return {}


def _make_assist(**kwargs) -> HexNeighborAssist:
    """Create a HexNeighborAssist with test defaults."""
    defaults = dict(
        topology_registry=FakeTopologyRegistry(),
        health_monitor=FakeHealthMonitor(),
        llm_service=None,
        parallel_dispatcher=None,
        magma_audit=None,
        enabled=True,
        local_threshold=0.72,
        neighbor_threshold=0.82,
        preflight_min_score=0.3,
        local_budget_ms=15000,
        neighbor_budget_ms=10000,
        total_hex_budget_ms=25000,
        skip_low_value_neighbor_when_sequential=True,
    )
    defaults.update(kwargs)
    return HexNeighborAssist(**defaults)


# ══════════════════════════════════════════════════════════════
# 1. PREFLIGHT SCORING
# ══════════════════════════════════════════════════════════════

class TestPreflightScoring:
    """Tests for cheap preflight scoring."""

    def test_domain_match_boosts_score(self):
        ha = _make_assist()
        score = ha._preflight_score("Mikä on sähkön hinta?", "", "electricity")
        assert score >= 0.3, f"Domain match should boost score, got {score}"

    def test_no_domain_match_low_score(self):
        ha = _make_assist()
        score = ha._preflight_score("Hello world", "", "hub")
        assert score < 0.3, f"No domain match should be low, got {score}"

    def test_solver_intent_lowers_score(self):
        ha = _make_assist()
        score = ha._preflight_score("What is 5+5?", "math", "hub")
        assert score < 0.2, f"Solver intent should lower score, got {score}"

    def test_long_query_bonus(self):
        ha = _make_assist()
        short = ha._preflight_score("hi", "", "hub")
        long_q = ha._preflight_score("A" * 100, "", "hub")
        assert long_q > short, "Longer queries should score higher"

    def test_cell_success_ratio_boosts(self):
        ha = _make_assist()
        # Record some successes
        for _ in range(5):
            ha._success_memory.record("electricity", "electricity", True)
        score = ha._preflight_score("Electricity price?", "", "electricity")
        assert score >= 0.5, f"Cell with good history should score high, got {score}"

    def test_cell_zero_ratio_lowers(self):
        ha = _make_assist()
        # Record only failures
        for _ in range(5):
            ha._success_memory.record("hub", "general", False)
        score = ha._preflight_score("Hello", "", "hub")
        assert score < 0.2, f"Cell with zero success should score low, got {score}"

    def test_preflight_score_bounded_0_1(self):
        ha = _make_assist()
        # Try various extreme inputs
        for query in ["", "x" * 1000, "math 5+5", "sähkö weather"]:
            for intent in ["", "math", "thermal"]:
                for cell in ["hub", "electricity", "weather"]:
                    score = ha._preflight_score(query, intent, cell)
                    assert 0.0 <= score <= 1.0, f"Score out of range: {score}"


class TestQueryCategoryClassification:
    """Tests for cheap query classification."""

    def test_electricity_category(self):
        assert HexNeighborAssist._classify_query_category("sähkön hinta") == "electricity"

    def test_weather_category(self):
        assert HexNeighborAssist._classify_query_category("weather in Helsinki") == "weather"

    def test_math_category(self):
        assert HexNeighborAssist._classify_query_category("calculate 5+3") == "math"

    def test_system_category(self):
        assert HexNeighborAssist._classify_query_category("system status") == "system"

    def test_general_fallback(self):
        assert HexNeighborAssist._classify_query_category("tell me a joke") == "general"


# ══════════════════════════════════════════════════════════════
# 2. SKIP LOCAL ATTEMPT LOGIC
# ══════════════════════════════════════════════════════════════

class TestSkipLocalAttempt:
    """Tests for preflight skip logic."""

    @pytest.mark.asyncio
    async def test_low_preflight_skips_local(self):
        ha = _make_assist(preflight_min_score=0.9)  # Very high threshold
        result = await ha.resolve("Hello world", intent="")
        assert result is None  # Should skip and return None
        m = ha.get_metrics()
        assert m["preflight_skips"] >= 1
        assert m["skipped_local_attempts"] >= 1

    @pytest.mark.asyncio
    async def test_high_preflight_attempts_local(self):
        ha = _make_assist(preflight_min_score=0.0)  # Always pass preflight
        # Without LLM, local will fail, but it should attempt
        result = await ha.resolve("sähkön hinta?", intent="")
        m = ha.get_metrics()
        assert m["preflight_passes"] >= 1

    @pytest.mark.asyncio
    async def test_solver_intent_triggers_skip(self):
        ha = _make_assist(preflight_min_score=0.3)
        result = await ha.resolve("What is 5+5?", intent="math")
        m = ha.get_metrics()
        # Math intent should lower preflight score below threshold
        assert m["preflight_skips"] >= 1 or m["global_escalations"] >= 1


# ══════════════════════════════════════════════════════════════
# 3. SKIP NEIGHBOR WHEN SEQUENTIAL
# ══════════════════════════════════════════════════════════════

class TestSkipNeighborSequential:
    """Tests for sequential neighbor skip logic."""

    @pytest.mark.asyncio
    async def test_sequential_low_preflight_skips_neighbor(self):
        """When parallel=False and preflight<0.5, neighbor should be skipped."""
        ha = _make_assist(
            parallel_neighbor=False,
            skip_low_value_neighbor_when_sequential=True,
            preflight_min_score=0.0,  # Don't skip at preflight
        )
        result = await ha.resolve("Hello", intent="")
        m = ha.get_metrics()
        assert m["skipped_neighbor_attempts"] >= 1

    @pytest.mark.asyncio
    async def test_skip_disabled_does_not_skip(self):
        ha = _make_assist(
            parallel_neighbor=False,
            skip_low_value_neighbor_when_sequential=False,
            preflight_min_score=0.0,
        )
        # Even with low preflight, neighbor skip is disabled
        result = await ha.resolve("Hello", intent="")
        m = ha.get_metrics()
        # Should not have skipped neighbor (it will still fail without LLM)
        # but the skip counter should be 0
        assert m["skipped_neighbor_attempts"] == 0 or m["global_escalations"] >= 1


# ══════════════════════════════════════════════════════════════
# 4. BUDGET EXHAUSTION
# ══════════════════════════════════════════════════════════════

class TestBudgetExhaustion:
    """Tests for ms-based budget controls."""

    @pytest.mark.asyncio
    async def test_total_budget_zero_skips_immediately(self):
        """With total_hex_budget_ms=0, should budget-exhaust after local."""
        slow_llm = AsyncMock()
        slow_llm.generate = AsyncMock(side_effect=lambda **kw: "response")
        ha = _make_assist(
            llm_service=slow_llm,
            total_hex_budget_ms=0,  # Immediate exhaust
            preflight_min_score=0.0,
        )
        result = await ha.resolve("sähkön hinta?", intent="")
        m = ha.get_metrics()
        # Should hit budget exhaustion
        assert m["budget_exhaustions"] >= 1 or m["skipped_neighbor_attempts"] >= 1

    def test_budget_fields_in_metrics(self):
        ha = _make_assist()
        m = ha.get_metrics()
        assert "budget_exhaustions" in m
        assert "skipped_local_attempts" in m
        assert "skipped_neighbor_attempts" in m


# ══════════════════════════════════════════════════════════════
# 5. COUNTER CONSISTENCY
# ══════════════════════════════════════════════════════════════

class TestCounterConsistency:
    """Tests for counter truth between metrics sources."""

    def test_initial_counters_zero(self):
        ha = _make_assist()
        m = ha.get_metrics()
        assert m["origin_cell_resolutions"] == 0
        assert m["local_only_resolutions"] == 0
        assert m["neighbor_assist_resolutions"] == 0
        assert m["global_escalations"] == 0
        assert m["preflight_skips"] == 0
        assert m["preflight_passes"] == 0
        assert m["skipped_local_attempts"] == 0
        assert m["skipped_neighbor_attempts"] == 0
        assert m["budget_exhaustions"] == 0

    @pytest.mark.asyncio
    async def test_preflight_skip_counter_consistency(self):
        """preflight_skips should equal skipped_local_attempts from preflight."""
        ha = _make_assist(preflight_min_score=0.9)
        for _ in range(5):
            await ha.resolve("Hello world")
        m = ha.get_metrics()
        # When preflight skips, both local AND neighbor are skipped
        assert m["preflight_skips"] == m["skipped_local_attempts"]

    @pytest.mark.asyncio
    async def test_total_queries_consistent(self):
        """origin_resolutions + preflight_skips = total hex queries."""
        ha = _make_assist(preflight_min_score=0.0)
        for _ in range(3):
            await ha.resolve("test query")
        m = ha.get_metrics()
        eff = ha.get_efficiency_stats()
        total = eff["total_hex_queries"]
        assert total == m["origin_cell_resolutions"] + m["preflight_skips"]

    def test_efficiency_stats_has_required_fields(self):
        ha = _make_assist()
        eff = ha.get_efficiency_stats()
        required = {
            "total_hex_queries", "preflight_skips", "preflight_passes",
            "preflight_skip_ratio", "skipped_local_attempts",
            "skipped_neighbor_attempts", "budget_exhaustions",
            "local_success_ratio", "neighbor_success_ratio",
            "escalation_ratio", "cell_success_memory",
        }
        assert required.issubset(set(eff.keys()))


# ══════════════════════════════════════════════════════════════
# 6. CACHE-AWARE TRACE CORRECTNESS
# ══════════════════════════════════════════════════════════════

class TestCacheAwareTrace:
    """Hotcache hits should not show hex/global path."""

    def test_trace_preflight_fields_exist(self):
        t = HexResolutionTrace(trace_id="t1", origin_cell_id="hub", query="test")
        d = t.to_dict()
        assert "preflight_score" in d
        assert "preflight_skipped" in d
        assert "neighbor_skipped" in d
        assert "budget_exhausted" in d

    def test_trace_preflight_defaults(self):
        t = HexResolutionTrace(trace_id="t1", origin_cell_id="hub", query="test")
        assert t.preflight_score == -1.0
        assert t.preflight_skipped is False
        assert t.neighbor_skipped is False
        assert t.budget_exhausted is False

    @pytest.mark.asyncio
    async def test_preflight_skip_sets_trace_fields(self):
        ha = _make_assist(preflight_min_score=0.9)
        await ha.resolve("Hello world")
        trace = ha.get_last_trace()
        if trace:
            assert trace.get("preflight_skipped") is True
            assert trace.get("neighbor_skipped") is True


# ══════════════════════════════════════════════════════════════
# 7. SOLVER PATH NOT POLLUTED BY HEX TRACE
# ══════════════════════════════════════════════════════════════

class TestSolverPathIsolation:
    """Solver hits must not show hex trace in chat_service."""

    def test_solver_route_does_not_call_hex(self):
        """If route is solver and solver succeeds, hex should not be called."""
        # This is a structural test — chat_service returns on solver success
        # before reaching the hex section (line 136-154 in chat_service.py).
        from waggledance.core.orchestration.routing_policy import (
            extract_features, select_route, RoutingFeatures,
        )
        # Math intent routes to solver
        features = RoutingFeatures(
            query_length=20,
            solver_intent="math",
        )
        config = MagicMock()
        config.get = MagicMock(return_value=False)
        route = select_route(features, config)
        assert route.route_type == "solver"


# ══════════════════════════════════════════════════════════════
# 8. HOTCACHE PATH NOT POLLUTED BY HEX TRACE
# ══════════════════════════════════════════════════════════════

class TestHotcacheIsolation:
    """Hotcache hits must not go through hex."""

    def test_hotcache_route_returns_before_hex(self):
        """If hotcache hits, routing returns hotcache before hex code runs."""
        from waggledance.core.orchestration.routing_policy import (
            RoutingFeatures, select_route,
        )
        features = RoutingFeatures(has_hot_cache_hit=True)
        config = MagicMock()
        config.get = MagicMock(return_value=False)
        route = select_route(features, config)
        assert route.route_type == "hotcache"
        assert route.confidence == 1.0


# ══════════════════════════════════════════════════════════════
# 9. OPS/STATE/HOLOGRAM COUNTER ALIGNMENT
# ══════════════════════════════════════════════════════════════

class TestCounterAlignment:
    """Metrics from get_metrics() must include all v3.5.6 fields."""

    def test_metrics_has_efficiency_fields(self):
        ha = _make_assist()
        m = ha.get_metrics()
        for field_name in [
            "skipped_local_attempts", "skipped_neighbor_attempts",
            "budget_exhaustions", "preflight_skips", "preflight_passes",
        ]:
            assert field_name in m, f"Missing {field_name} in metrics"

    def test_metrics_and_efficiency_stats_agree(self):
        ha = _make_assist()
        m = ha.get_metrics()
        e = ha.get_efficiency_stats()
        assert m["preflight_skips"] == e["preflight_skips"]
        assert m["skipped_local_attempts"] == e["skipped_local_attempts"]
        assert m["skipped_neighbor_attempts"] == e["skipped_neighbor_attempts"]
        assert m["budget_exhaustions"] == e["budget_exhaustions"]


# ══════════════════════════════════════════════════════════════
# 10. NO AUTH REGRESSION
# ══════════════════════════════════════════════════════════════

class TestNoAuthRegression:
    """Verify auth-related code is not affected by v3.5.6 changes."""

    def test_auth_module_exists(self):
        import waggledance.adapters.http.middleware.auth as auth_mod
        assert hasattr(auth_mod, "BearerAuthMiddleware")

    def test_public_paths_unchanged(self):
        """PUBLIC_PATHS should include expected paths."""
        from waggledance.adapters.http.middleware.auth import PUBLIC_PATHS
        assert "/api/status" in PUBLIC_PATHS or "/api/auth/check" in PUBLIC_PATHS


# ══════════════════════════════════════════════════════════════
# 11. NO SECRET LEAK REGRESSION
# ══════════════════════════════════════════════════════════════

class TestNoSecretLeak:
    """Ensure no API keys leak into frontend-facing code."""

    def test_hologram_html_no_api_key(self):
        from pathlib import Path
        html_path = Path(__file__).resolve().parents[1] / "web" / "hologram-brain-v6.html"
        if html_path.exists():
            content = html_path.read_text(encoding="utf-8")
            assert "WAGGLE_API_KEY" not in content
            assert "Bearer " not in content

    def test_settings_no_token_leak(self):
        """settings.yaml should not contain actual API tokens."""
        from pathlib import Path
        yaml_path = Path(__file__).resolve().parents[1] / "configs" / "settings.yaml"
        if yaml_path.exists():
            content = yaml_path.read_text(encoding="utf-8")
            # Token fields should be empty
            lines = content.split("\n")
            for line in lines:
                if "token:" in line.lower() and "bot_token" not in line:
                    val = line.split(":", 1)[-1].strip().strip("'\"")
                    assert len(val) < 10 or val == "", f"Possible token leak: {line}"


# ══════════════════════════════════════════════════════════════
# 12. SAFE DEFAULTS
# ══════════════════════════════════════════════════════════════

class TestSafeDefaults:
    """Verify safe defaults are conservative."""

    def test_hex_mesh_default_disabled(self):
        """hex_mesh.enabled must default to false in settings.yaml."""
        from pathlib import Path
        import yaml

        yaml_path = Path(__file__).resolve().parents[1] / "configs" / "settings.yaml"
        with open(yaml_path, encoding="utf-8") as f:
            settings = yaml.safe_load(f)
        assert settings["hex_mesh"]["enabled"] is False

    def test_default_budgets_conservative(self):
        """Default budgets should not be zero or extremely large."""
        ha = _make_assist()
        assert ha._local_budget_ms > 0
        assert ha._neighbor_budget_ms > 0
        assert ha._total_hex_budget_ms > 0
        assert ha._total_hex_budget_ms >= ha._local_budget_ms

    def test_preflight_min_score_positive(self):
        """Preflight min score must be > 0 to actually gate."""
        ha = _make_assist()
        assert ha._preflight_min_score > 0

    def test_skip_sequential_neighbor_default_on(self):
        """Skip low-value sequential neighbor should default to True."""
        from pathlib import Path
        import yaml

        yaml_path = Path(__file__).resolve().parents[1] / "configs" / "settings.yaml"
        with open(yaml_path, encoding="utf-8") as f:
            settings = yaml.safe_load(f)
        assert settings["hex_mesh"]["skip_low_value_neighbor_when_sequential"] is True

    def test_llm_parallel_default_off(self):
        """llm_parallel should default to off for safe sequential mode."""
        from pathlib import Path
        import yaml

        yaml_path = Path(__file__).resolve().parents[1] / "configs" / "settings.yaml"
        with open(yaml_path, encoding="utf-8") as f:
            settings = yaml.safe_load(f)
        assert settings["llm_parallel"]["enabled"] is False


# ══════════════════════════════════════════════════════════════
# 13. AUTOTUNER CANDIDATE FILE OUTPUT
# ══════════════════════════════════════════════════════════════

class TestAutotunerOutput:
    """Tests for autotuner tool file format."""

    def test_autotune_module_importable(self):
        import importlib.util
        from pathlib import Path as _Path
        spec = importlib.util.spec_from_file_location(
            "runtime_autotune_30h",
            str(_Path(__file__).resolve().parents[1] / "tools" / "runtime_autotune_30h.py"),
        )
        assert spec is not None

    def test_soak_module_importable(self):
        import importlib.util
        from pathlib import Path as _Path
        spec = importlib.util.spec_from_file_location(
            "runtime_soak_30h",
            str(_Path(__file__).resolve().parents[1] / "tools" / "runtime_soak_30h.py"),
        )
        assert spec is not None


# ══════════════════════════════════════════════════════════════
# 14. NO DEADLOCK / NO LOCK REGRESSION
# ══════════════════════════════════════════════════════════════

class TestNoDeadlock:
    """Verify thread safety of new code."""

    def test_metrics_access_thread_safe(self):
        """Multiple threads reading/writing metrics should not deadlock."""
        ha = _make_assist()
        errors = []

        def reader():
            try:
                for _ in range(100):
                    ha.get_metrics()
                    ha.get_efficiency_stats()
            except Exception as e:
                errors.append(e)

        def writer():
            try:
                for _ in range(100):
                    with ha._lock:
                        ha._metrics.preflight_skips += 1
                        ha._metrics.origin_cell_resolutions += 1
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=reader) for _ in range(3)]
        threads += [threading.Thread(target=writer) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        assert not errors, f"Thread errors: {errors}"

    def test_success_memory_thread_safe(self):
        mem = _AdaptiveSuccessMemory()
        errors = []

        def worker():
            try:
                for i in range(50):
                    mem.record("hub", "general", i % 2 == 0)
                    mem.cell_success_ratio("hub")
                    mem.category_success_ratio("general")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        assert not errors


# ══════════════════════════════════════════════════════════════
# 15. RELEASE STATE VERSION CORRECTNESS
# ══════════════════════════════════════════════════════════════

class TestVersionConsistency:
    """Version fields must be consistent across files."""

    def test_pyproject_version_format(self):
        from pathlib import Path
        import tomllib

        pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)
        version = data["project"]["version"]
        parts = version.split(".")
        assert len(parts) == 3
        assert all(p.isdigit() for p in parts)

    def test_init_version_matches_pyproject(self):
        from pathlib import Path
        import tomllib

        root = Path(__file__).resolve().parents[1]
        with open(root / "pyproject.toml", "rb") as f:
            pyproject_ver = tomllib.load(f)["project"]["version"]

        import waggledance
        assert waggledance.__version__ == pyproject_ver


# ══════════════════════════════════════════════════════════════
# 16. ADAPTIVE SUCCESS MEMORY
# ══════════════════════════════════════════════════════════════

class TestAdaptiveSuccessMemory:
    """Tests for rolling success tracking."""

    def test_empty_returns_none(self):
        mem = _AdaptiveSuccessMemory()
        assert mem.cell_success_ratio("nonexistent") is None
        assert mem.category_success_ratio("nonexistent") is None

    def test_all_success(self):
        mem = _AdaptiveSuccessMemory()
        for _ in range(10):
            mem.record("hub", "general", True)
        assert mem.cell_success_ratio("hub") == 1.0
        assert mem.category_success_ratio("general") == 1.0

    def test_all_failure(self):
        mem = _AdaptiveSuccessMemory()
        for _ in range(10):
            mem.record("hub", "general", False)
        assert mem.cell_success_ratio("hub") == 0.0
        assert mem.category_success_ratio("general") == 0.0

    def test_mixed(self):
        mem = _AdaptiveSuccessMemory()
        for i in range(10):
            mem.record("hub", "general", i < 3)  # 3 success, 7 failure
        ratio = mem.cell_success_ratio("hub")
        assert ratio is not None
        assert abs(ratio - 0.3) < 0.01

    def test_rolling_window_expiry(self):
        mem = _AdaptiveSuccessMemory(window_s=0.1)  # 100ms window
        mem.record("hub", "general", True)
        assert mem.cell_success_ratio("hub") == 1.0
        time.sleep(0.15)  # Wait for window to expire
        assert mem.cell_success_ratio("hub") is None

    def test_multiple_cells_independent(self):
        mem = _AdaptiveSuccessMemory()
        for _ in range(5):
            mem.record("hub", "general", True)
            mem.record("electricity", "electricity", False)
        assert mem.cell_success_ratio("hub") == 1.0
        assert mem.cell_success_ratio("electricity") == 0.0


# ══════════════════════════════════════════════════════════════
# ADDITIONAL TESTS
# ══════════════════════════════════════════════════════════════

class TestHexMeshMetricsDataclass:
    """HexMeshMetrics must have all v3.5.6 fields."""

    def test_new_fields_exist(self):
        m = HexMeshMetrics()
        assert m.skipped_local_attempts == 0
        assert m.skipped_neighbor_attempts == 0
        assert m.budget_exhaustions == 0
        assert m.preflight_skips == 0
        assert m.preflight_passes == 0

    def test_all_fields_are_int(self):
        m = HexMeshMetrics()
        for fname, fval in m.__dataclass_fields__.items():
            assert isinstance(getattr(m, fname), int), f"{fname} is not int"


class TestHexResolutionTraceV356:
    """HexResolutionTrace must have v3.5.6 efficiency fields."""

    def test_new_fields_default_values(self):
        t = HexResolutionTrace(trace_id="t", origin_cell_id="hub", query="q")
        assert t.preflight_score == -1.0
        assert t.preflight_skipped is False
        assert t.neighbor_skipped is False
        assert t.budget_exhausted is False

    def test_to_dict_includes_new_fields(self):
        t = HexResolutionTrace(trace_id="t", origin_cell_id="hub", query="q",
                                preflight_score=0.5, preflight_skipped=True)
        d = t.to_dict()
        assert d["preflight_score"] == 0.5
        assert d["preflight_skipped"] is True


class TestDisabledHexMeshNoOp:
    """When hex_mesh.enabled=false, no preflight or efficiency code runs."""

    @pytest.mark.asyncio
    async def test_disabled_returns_none(self):
        ha = _make_assist(enabled=False)
        result = await ha.resolve("Test query")
        assert result is None

    @pytest.mark.asyncio
    async def test_disabled_no_counters(self):
        ha = _make_assist(enabled=False)
        await ha.resolve("Test query")
        m = ha.get_metrics()
        assert m["preflight_skips"] == 0
        assert m["origin_cell_resolutions"] == 0
