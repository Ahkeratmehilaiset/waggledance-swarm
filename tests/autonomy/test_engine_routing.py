"""Tests for reasoning engine routing integration.

Tests classify_intent, CapabilitySelector routing, registry bindings,
and end-to-end wiring.
"""

import pytest

from waggledance.core.reasoning.solver_router import SolverRouter
from waggledance.core.capabilities.selector import CapabilitySelector
from waggledance.core.capabilities.registry import CapabilityRegistry
from waggledance.bootstrap.capability_loader import bind_executors


# ── classify_intent ──────────────────────────────────────────────

class TestClassifyIntent:
    def test_thermal_temperature(self):
        assert SolverRouter.classify_intent("what is the temperature?") == "thermal"

    def test_thermal_frost(self):
        assert SolverRouter.classify_intent("frost risk for pipes") == "thermal"

    def test_thermal_heating(self):
        assert SolverRouter.classify_intent("heating cost today") == "thermal"

    def test_thermal_finnish(self):
        assert SolverRouter.classify_intent("lämpötila nyt") == "thermal"

    def test_stats_trend(self):
        assert SolverRouter.classify_intent("show me the trend") == "stats"

    def test_stats_median(self):
        assert SolverRouter.classify_intent("what is the median value?") == "stats"

    def test_stats_correlation(self):
        assert SolverRouter.classify_intent("correlation between A and B") == "stats"

    def test_optimization_optimize(self):
        assert SolverRouter.classify_intent("optimize the schedule") == "optimization"

    def test_optimization_cheapest(self):
        assert SolverRouter.classify_intent("find the cheapest hours") == "optimization"

    def test_optimization_finnish(self):
        assert SolverRouter.classify_intent("optimoi aikatauluta") == "optimization"

    def test_causal_why(self):
        assert SolverRouter.classify_intent("why did the pressure drop?") == "causal"

    def test_causal_root_cause(self):
        assert SolverRouter.classify_intent("find the root cause") == "causal"

    def test_causal_impact(self):
        assert SolverRouter.classify_intent("what is the impact?") == "causal"

    def test_causal_finnish(self):
        assert SolverRouter.classify_intent("miksi näin tapahtui") == "causal"


# ── CapabilitySelector routing ────────────────────────────────────

class TestSelectorRouting:
    def _make(self):
        reg = CapabilityRegistry()
        return CapabilitySelector(reg), reg

    def test_thermal_routes_to_solver(self):
        sel, _ = self._make()
        result = sel.select("thermal", {}, {"numbers_present": True})
        assert result.quality_path == "gold"
        ids = [c.capability_id for c in result.selected]
        assert "solve.thermal" in ids

    def test_stats_routes_to_solver(self):
        sel, _ = self._make()
        result = sel.select("stats")
        assert result.quality_path == "gold"
        ids = [c.capability_id for c in result.selected]
        assert "solve.stats" in ids

    def test_causal_routes_to_solver(self):
        sel, _ = self._make()
        result = sel.select("causal")
        assert result.quality_path == "gold"
        ids = [c.capability_id for c in result.selected]
        assert "solve.causal" in ids

    def test_optimization_routes(self):
        sel, _ = self._make()
        result = sel.select("optimization")
        assert result.quality_path == "gold"
        ids = [c.capability_id for c in result.selected]
        assert "optimize.schedule" in ids

    def test_routing_routes_to_detection(self):
        sel, _ = self._make()
        result = sel.select("routing")
        assert result.quality_path == "gold"
        ids = [c.capability_id for c in result.selected]
        assert "analyze.routing" in ids


# ── Registry executor bindings ────────────────────────────────────

class TestRegistryBindings:
    def _make(self):
        reg = CapabilityRegistry()
        bind_executors(reg)
        return reg

    def test_thermal_executor_bound(self):
        reg = self._make()
        assert reg.get_executor("solve.thermal") is not None

    def test_anomaly_executor_bound(self):
        reg = self._make()
        assert reg.get_executor("detect.anomaly") is not None

    def test_stats_executor_bound(self):
        reg = self._make()
        assert reg.get_executor("solve.stats") is not None

    def test_optimization_executor_bound(self):
        reg = self._make()
        assert reg.get_executor("optimize.schedule") is not None

    def test_causal_executor_bound(self):
        reg = self._make()
        assert reg.get_executor("solve.causal") is not None

    def test_routing_executor_bound(self):
        reg = self._make()
        assert reg.get_executor("analyze.routing") is not None

    def test_at_least_6_new_executors(self):
        reg = self._make()
        # At least the 6 new engines should be bound (legacy may fail)
        new_ids = ["solve.thermal", "detect.anomaly", "solve.stats",
                   "optimize.schedule", "solve.causal", "analyze.routing"]
        bound = sum(1 for cid in new_ids if reg.get_executor(cid) is not None)
        assert bound == 6


# ── End-to-end: SolverRouter ─────────────────────────────────────

class TestEndToEnd:
    def _make(self):
        reg = CapabilityRegistry()
        bind_executors(reg)
        sel = CapabilitySelector(reg)
        return SolverRouter(registry=reg, selector=sel), reg

    def test_thermal_e2e(self):
        router, reg = self._make()
        intent = SolverRouter.classify_intent("what is the temperature?")
        result = router.route(intent, "what is the temperature?",
                              context={"numbers_present": True})
        assert "solve.thermal" in result.selected_ids

    def test_stats_e2e(self):
        router, _ = self._make()
        intent = SolverRouter.classify_intent("show me the trend")
        result = router.route(intent, "show me the trend")
        assert "solve.stats" in result.selected_ids

    def test_optimization_e2e(self):
        router, _ = self._make()
        intent = SolverRouter.classify_intent("optimize the schedule")
        result = router.route(intent, "optimize the schedule")
        assert "optimize.schedule" in result.selected_ids

    def test_causal_e2e(self):
        router, _ = self._make()
        intent = SolverRouter.classify_intent("why did the pressure drop?")
        result = router.route(intent, "why did the pressure drop?")
        assert "solve.causal" in result.selected_ids
