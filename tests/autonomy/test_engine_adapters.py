"""Tests for reasoning engine adapter layer.

6 adapter classes × ~5 tests each = ~30 tests.
"""

import pytest


# ── ThermalSolverAdapter ─────────────────────────────────────────

class TestThermalSolverAdapter:
    def _make(self):
        from waggledance.adapters.capabilities.thermal_solver_adapter import ThermalSolverAdapter
        return ThermalSolverAdapter()

    def test_capability_id(self):
        assert self._make().CAPABILITY_ID == "solve.thermal"

    def test_available(self):
        assert self._make().available is True

    def test_execute_heat_loss(self):
        adapter = self._make()
        result = adapter.execute(
            calc_type="heat_loss",
            inputs={"area_m2": 50, "R_value": 3.0, "T_inside": 21, "T_outside": -10},
        )
        assert result["success"] is True
        assert result["capability_id"] == "solve.thermal"
        assert result["quality_path"] == "gold"
        assert "latency_ms" in result
        assert result["value"] > 0

    def test_execute_frost_risk(self):
        result = self._make().execute(calc_type="frost_risk",
                                       inputs={"T_outdoor": -20, "insulated": False})
        assert result["success"] is True
        assert result["value"] > 0

    def test_execute_infer_from_query(self):
        result = self._make().execute(query="what is the frost risk for pipes")
        assert result["success"] is True

    def test_stats(self):
        adapter = self._make()
        adapter.execute(calc_type="heat_loss", inputs={})
        s = adapter.stats()
        assert s["calls"] == 1
        assert s["available"] is True


# ── AnomalyDetectorAdapter ──────────────────────────────────────

class TestAnomalyDetectorAdapter:
    def _make(self):
        from waggledance.adapters.capabilities.anomaly_detector_adapter import AnomalyDetectorAdapter
        return AnomalyDetectorAdapter()

    def test_capability_id(self):
        assert self._make().CAPABILITY_ID == "detect.anomaly"

    def test_available(self):
        assert self._make().available is True

    def test_execute_basic(self):
        result = self._make().execute(metric="temperature", current=25.0)
        assert result["success"] is True
        assert result["capability_id"] == "detect.anomaly"
        assert result["quality_path"] == "gold"
        assert "checks" in result
        assert isinstance(result["checks"], list)

    def test_execute_with_baseline(self):
        result = self._make().execute(metric="humidity", current=80.0, baseline=60.0)
        assert result["success"] is True
        assert len(result["checks"]) >= 3  # z, iqr, trend + residual

    def test_stats(self):
        adapter = self._make()
        adapter.execute(metric="test", current=1.0)
        s = adapter.stats()
        assert s["calls"] == 1
        assert s["successes"] == 1


# ── StatsEngineAdapter ──────────────────────────────────────────

class TestStatsEngineAdapter:
    def _make(self):
        from waggledance.adapters.capabilities.stats_engine_adapter import StatsEngineAdapter
        return StatsEngineAdapter()

    def test_capability_id(self):
        assert self._make().CAPABILITY_ID == "solve.stats"

    def test_available(self):
        assert self._make().available is True

    def test_execute_summarize(self):
        adapter = self._make()
        # Pre-load some data
        for i in range(10):
            adapter.engine.record("temp", 20.0 + i * 0.5)
        result = adapter.execute(metric="temp", operation="summarize")
        assert result["success"] is True
        assert result["operation"] == "summarize"
        assert result["quality_path"] == "gold"
        assert result["result"]["count"] == 10

    def test_execute_correlation(self):
        adapter = self._make()
        for i in range(10):
            adapter.engine.record("a", float(i))
            adapter.engine.record("b", float(i * 2))
        result = adapter.execute(metric="a", operation="correlation", metric_b="b")
        assert result["success"] is True
        assert result["result"]["correlation"] is not None

    def test_stats(self):
        adapter = self._make()
        adapter.execute(metric="x")
        s = adapter.stats()
        assert s["calls"] == 1


# ── OptimizationAdapter ─────────────────────────────────────────

class TestOptimizationAdapter:
    def _make(self):
        from waggledance.adapters.capabilities.optimization_adapter import OptimizationAdapter
        return OptimizationAdapter()

    def test_capability_id(self):
        assert self._make().CAPABILITY_ID == "optimize.schedule"

    def test_available(self):
        assert self._make().available is True

    def test_execute_energy_cost(self):
        prices = [5, 8, 12, 3, 4, 7, 6, 10, 15, 2, 9, 11,
                  14, 1, 13, 6, 8, 3, 5, 7, 9, 11, 4, 6]
        result = self._make().execute(
            problem_type="energy_cost",
            inputs={"hourly_prices": prices, "required_kwh": 10, "max_power_kw": 3.0},
        )
        assert result["success"] is True
        assert result["capability_id"] == "optimize.schedule"
        assert result["quality_path"] == "gold"
        assert len(result["schedule"]) > 0

    def test_execute_schedule_tasks(self):
        tasks = [
            {"id": "A", "duration_slots": 2, "deadline_slot": 10, "priority": 3},
            {"id": "B", "duration_slots": 1, "deadline_slot": 5, "priority": 5},
        ]
        result = self._make().execute(
            problem_type="schedule_tasks",
            inputs={"tasks": tasks, "time_slots": 12},
        )
        assert result["success"] is True
        assert len(result["schedule"]) == 2

    def test_execute_allocate(self):
        tasks = [
            {"id": "T1", "priority": 10, "demand": 50},
            {"id": "T2", "priority": 5, "demand": 30},
        ]
        result = self._make().execute(
            problem_type="allocate_resources",
            inputs={"tasks": tasks, "capacity": 60},
        )
        assert result["success"] is True

    def test_execute_empty_fails(self):
        result = self._make().execute(
            problem_type="energy_cost",
            inputs={"hourly_prices": [], "required_kwh": 0},
        )
        assert result["success"] is False

    def test_stats(self):
        adapter = self._make()
        adapter.execute(problem_type="energy_cost",
                        inputs={"hourly_prices": [10] * 24, "required_kwh": 5})
        s = adapter.stats()
        assert s["calls"] == 1
        assert s["successes"] == 1


# ── CausalReasonerAdapter ──────────────────────────────────────

class TestCausalReasonerAdapter:
    def _make(self):
        from waggledance.adapters.capabilities.causal_reasoner_adapter import CausalReasonerAdapter
        return CausalReasonerAdapter()

    def test_capability_id(self):
        assert self._make().CAPABILITY_ID == "solve.causal"

    def test_available(self):
        assert self._make().available is True

    def test_execute_root_causes(self):
        result = self._make().execute(entity="temperature_drop", query_type="root_causes")
        assert result["success"] is True
        assert result["query_type"] == "root_causes"
        assert result["capability_id"] == "solve.causal"
        assert result["quality_path"] == "gold"
        assert "result" in result

    def test_execute_impact(self):
        result = self._make().execute(entity="heater", query_type="impact",
                                       inputs={"magnitude": 2.0})
        assert result["success"] is True

    def test_execute_what_if(self):
        result = self._make().execute(entity="price", query_type="what_if",
                                       inputs={"new_value": 15.0, "baselines": {"price": 10.0}})
        assert result["success"] is True

    def test_stats(self):
        adapter = self._make()
        adapter.execute(entity="x")
        s = adapter.stats()
        assert s["calls"] == 1


# ── RouteAnalyzerAdapter ────────────────────────────────────────

class TestRouteAnalyzerAdapter:
    def _make(self):
        from waggledance.adapters.capabilities.route_analyzer_adapter import RouteAnalyzerAdapter
        return RouteAnalyzerAdapter()

    def test_capability_id(self):
        assert self._make().CAPABILITY_ID == "analyze.routing"

    def test_available(self):
        assert self._make().available is True

    def test_execute_metrics(self):
        adapter = self._make()
        # Pre-load some decisions
        adapter.engine.record_decision("solver", "math", "gold", True, 10.0)
        adapter.engine.record_decision("llm", "chat", "bronze", True, 200.0, was_fallback=True)
        result = adapter.execute(query_type="metrics")
        assert result["success"] is True
        assert result["quality_path"] == "gold"
        assert len(result["result"]["metrics"]) >= 1

    def test_execute_accuracy(self):
        adapter = self._make()
        adapter.engine.record_decision("solver", "math", "gold", True, 5.0)
        result = adapter.execute(query_type="accuracy")
        assert result["success"] is True
        assert result["result"]["overall_accuracy"] > 0

    def test_execute_recommendations(self):
        result = self._make().execute(query_type="recommendations")
        assert result["success"] is True
        assert "recommendations" in result["result"]

    def test_stats(self):
        adapter = self._make()
        adapter.execute()
        s = adapter.stats()
        assert s["calls"] == 1
        assert s["successes"] == 1
