"""
Tests for precondition-based routing fix.

Root cause: _detect_conditions() never analysed the query text to set
numbers_present, so solve.math and solve.thermal were always filtered
out by _filter_by_preconditions().
"""

from __future__ import annotations

import pytest

from waggledance.core.capabilities.registry import CapabilityRegistry
from waggledance.core.capabilities.selector import CapabilitySelector
from waggledance.core.reasoning.solver_router import SolverRouter


@pytest.fixture
def router():
    registry = CapabilityRegistry()
    return SolverRouter(registry=registry)


@pytest.fixture
def selector():
    registry = CapabilityRegistry()
    return CapabilitySelector(registry=registry)


# ── _detect_conditions with query text ────────────────────────


class TestDetectConditionsFromQuery:
    def test_numbers_detected_from_digits(self, router):
        conditions = router._detect_conditions({}, query="2+2")
        assert conditions.get("numbers_present") is True

    def test_numbers_detected_from_decimal(self, router):
        conditions = router._detect_conditions({}, query="150*0.08")
        assert conditions.get("numbers_present") is True

    def test_numbers_detected_from_temperature(self, router):
        conditions = router._detect_conditions({}, query="Is 45C safe?")
        assert conditions.get("numbers_present") is True

    def test_no_numbers_in_text_query(self, router):
        conditions = router._detect_conditions({}, query="varroa schedule")
        assert conditions.get("numbers_present") is not True

    def test_context_override_still_works(self, router):
        """Explicit context flag still sets numbers_present."""
        conditions = router._detect_conditions(
            {"numbers_present": True}, query="no digits here"
        )
        assert conditions.get("numbers_present") is True

    def test_empty_query_no_crash(self, router):
        conditions = router._detect_conditions({}, query="")
        assert isinstance(conditions, dict)


# ── Routing accuracy for user's 6 test queries ───────────────


class TestRoutingAccuracy:
    """Verify the 6 queries from the user's bug report route correctly."""

    def test_what_is_2_plus_2(self, router):
        result = router.route("math", "What is 2+2?")
        cap_ids = [c.capability_id for c in result.selection.selected]
        assert "solve.math" in cap_ids, f"Expected solve.math, got {cap_ids}"

    def test_150_times_008(self, router):
        result = router.route("math", "150*0.08")
        cap_ids = [c.capability_id for c in result.selection.selected]
        assert "solve.math" in cap_ids, f"Expected solve.math, got {cap_ids}"

    def test_paljonko_on_5_plus_3(self, router):
        result = router.route("math", "paljonko on 5+3")
        cap_ids = [c.capability_id for c in result.selection.selected]
        assert "solve.math" in cap_ids, f"Expected solve.math, got {cap_ids}"

    def test_is_45c_safe(self, router):
        result = router.route("thermal", "Is 45C safe?")
        cap_ids = [c.capability_id for c in result.selection.selected]
        assert "solve.thermal" in cap_ids, f"Expected solve.thermal, got {cap_ids}"

    def test_is_45_degrees_too_hot(self, router):
        """'Is 45 degrees too hot?' must classify as thermal, not chat."""
        from waggledance.core.reasoning.solver_router import SolverRouter
        intent = SolverRouter.classify_intent("Is 45 degrees too hot?")
        assert intent == "thermal", f"Expected thermal, got {intent}"
        result = router.route("thermal", "Is 45 degrees too hot?")
        cap_ids = [c.capability_id for c in result.selection.selected]
        assert "solve.thermal" in cap_ids, f"Expected solve.thermal, got {cap_ids}"

    def test_optimize_heating(self, router):
        """'Optimize heating' routes to thermal solvers (has no numbers, so
        solve.thermal filtered out → falls to solve.stats, which is OK)."""
        result = router.route("thermal", "Optimize heating")
        cap_ids = [c.capability_id for c in result.selection.selected]
        # No digits → numbers_present=False → solve.thermal filtered out
        # solve.stats is acceptable fallback
        assert any(cid.startswith("solve.") for cid in cap_ids), (
            f"Expected a solver, got {cap_ids}"
        )


# ── Selector precondition filtering ──────────────────────────


class TestSelectorWithConditions:
    def test_math_available_with_numbers_present(self, selector):
        """solve.math is selectable when numbers_present=True."""
        result = selector.select("math", available_conditions={"numbers_present": True})
        cap_ids = [c.capability_id for c in result.selected]
        assert "solve.math" in cap_ids

    def test_math_filtered_without_numbers(self, selector):
        """solve.math is filtered out when numbers_present is missing."""
        result = selector.select("math", available_conditions={})
        cap_ids = [c.capability_id for c in result.selected]
        assert "solve.math" not in cap_ids

    def test_thermal_available_with_numbers(self, selector):
        result = selector.select("thermal", available_conditions={"numbers_present": True})
        cap_ids = [c.capability_id for c in result.selected]
        assert "solve.thermal" in cap_ids

    def test_stats_always_available(self, selector):
        """solve.stats has no preconditions — always selectable."""
        result = selector.select("stats", available_conditions={})
        cap_ids = [c.capability_id for c in result.selected]
        assert "solve.stats" in cap_ids


# ── End-to-end via AutonomyRuntime.handle_query ──────────────


class TestEndToEndRouting:
    def test_handle_query_math_routes_correctly(self):
        from waggledance.core.autonomy.runtime import AutonomyRuntime

        rt = AutonomyRuntime(profile="VALIDATION")
        rt.start()
        result = rt.handle_query("What is 2+2?")
        rt.stop()

        assert result["capability"] == "solve.math", (
            f"Expected solve.math, got {result['capability']}"
        )

    def test_handle_query_thermal_routes_correctly(self):
        from waggledance.core.autonomy.runtime import AutonomyRuntime

        rt = AutonomyRuntime(profile="VALIDATION")
        rt.start()
        result = rt.handle_query("Is 45C safe?")
        rt.stop()

        assert result["capability"] == "solve.thermal", (
            f"Expected solve.thermal, got {result['capability']}"
        )

    def test_handle_query_math_executes_successfully(self):
        from waggledance.core.autonomy.runtime import AutonomyRuntime

        rt = AutonomyRuntime(profile="VALIDATION")
        rt.start()
        result = rt.handle_query("150*0.08")
        rt.stop()

        assert result["capability"] == "solve.math"
        assert result["executed"] is True
        assert result["result"]["success"] is True
