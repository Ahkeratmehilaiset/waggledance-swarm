"""
Tests for reasoning engines: thermal, anomaly, stats, optimization.

Covers:
- ThermalSolver calculations (heat_loss, heating_cost, frost_risk, etc.)
- AnomalyEngine detection methods (z-score, IQR, residual, trend)
- StatsEngine summaries and correlation
- OptimizationEngine energy cost + scheduling
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from waggledance.core.reasoning.thermal_solver import ThermalSolver
from waggledance.core.reasoning.anomaly_engine import AnomalyEngine
from waggledance.core.reasoning.stats_engine import StatsEngine
from waggledance.core.reasoning.optimization_engine import OptimizationEngine


class TestThermalSolver:
    def setup_method(self):
        self.solver = ThermalSolver()

    def test_heat_loss(self):
        result = self.solver.heat_loss_watts(
            area_m2=20.0, r_value=3.0, t_inside=21.0, t_outside=-9.0
        )
        assert result.success is True
        assert result.value == pytest.approx(200.0, rel=0.01)
        assert result.unit == "W"

    def test_heat_loss_zero_r(self):
        result = self.solver.heat_loss_watts(
            area_m2=10.0, r_value=0.0, t_inside=20.0, t_outside=0.0
        )
        assert result.success is False

    def test_heating_cost(self):
        result = self.solver.heating_cost(
            power_watts=2000.0, hours=8.0, price_c_kwh=10.0
        )
        assert result.success is True
        assert result.value == pytest.approx(1.60, rel=0.01)

    def test_frost_risk_safe(self):
        result = self.solver.frost_risk(
            t_outdoor=5.0, pipe_insulated=True, wind_speed_ms=2.0
        )
        assert result.success is True
        assert result.value == 0.0  # Safe

    def test_frost_risk_high(self):
        result = self.solver.frost_risk(
            t_outdoor=-20.0, pipe_insulated=False, wind_speed_ms=10.0
        )
        assert result.success is True
        assert result.value >= 0.6

    def test_hive_thermal_balance(self):
        result = self.solver.hive_thermal_balance(
            cluster_temp=35.0, ambient_temp=10.0, colony_size=1.0
        )
        assert result.success is True
        assert "stress_score" == result.unit

    def test_heat_pump_cop(self):
        result = self.solver.heat_pump_cop(t_source=-10.0, t_sink=45.0)
        assert result.success is True
        assert result.value > 1.0

    def test_heat_pump_cop_invalid(self):
        result = self.solver.heat_pump_cop(t_source=50.0, t_sink=20.0)
        assert result.success is False

    def test_solve_dispatch_known(self):
        result = self.solver.solve(
            "heat_loss", {"area_m2": 15.0, "R_value": 2.5,
                          "T_inside": 21.0, "T_outside": -4.0}
        )
        assert result.success is True

    def test_solve_dispatch_unknown(self):
        result = self.solver.solve("unknown_calc", {})
        assert result.success is False

    def test_stats(self):
        self.solver.heat_loss_watts(10.0, 2.0, 20.0, 0.0)
        stats = self.solver.stats()
        assert stats["total_calculations"] >= 1


class TestAnomalyEngine:
    def setup_method(self):
        self.engine = AnomalyEngine()

    def test_z_score_insufficient_data(self):
        result = self.engine.check_z_score("temp", 10.0)
        assert result.is_anomaly is False
        assert "Insufficient" in result.message

    def test_z_score_normal(self):
        for v in [10.0 + i * 0.1 for i in range(15)]:
            self.engine.record_value("temp", v)
        result = self.engine.check_z_score("temp", 10.5)
        assert result.is_anomaly is False

    def test_z_score_anomaly(self):
        for _ in range(15):
            self.engine.record_value("temp", 10.0)
        result = self.engine.check_z_score("temp", 50.0)
        assert result.is_anomaly is True
        assert result.severity > 0

    def test_iqr_insufficient(self):
        result = self.engine.check_iqr("weight", 33.0)
        assert result.is_anomaly is False

    def test_iqr_normal(self):
        for v in range(25):
            self.engine.record_value("weight", 30.0 + v * 0.1)
        result = self.engine.check_iqr("weight", 31.0)
        assert result.is_anomaly is False

    def test_iqr_anomaly(self):
        for v in range(25):
            self.engine.record_value("weight2", 30.0 + v * 0.1)
        result = self.engine.check_iqr("weight2", 100.0)
        assert result.is_anomaly is True

    def test_residual_normal(self):
        result = self.engine.check_residual("temp", 10.2, baseline=10.0)
        assert result.is_anomaly is False

    def test_residual_anomaly(self):
        result = self.engine.check_residual("temp", 15.0, baseline=10.0)
        assert result.is_anomaly is True

    def test_trend_insufficient(self):
        result = self.engine.check_trend("x")
        assert result.is_anomaly is False

    def test_trend_rising(self):
        for i in range(15):
            self.engine.record_value("rising", float(i))
        result = self.engine.check_trend("rising")
        assert "Upward" in result.message

    def test_trend_falling(self):
        for i in range(15):
            self.engine.record_value("falling", 15.0 - i)
        result = self.engine.check_trend("falling")
        assert "Downward" in result.message

    def test_check_all(self):
        for _ in range(25):
            self.engine.record_value("metric", 10.0)
        results = self.engine.check_all("metric", 10.1, baseline=10.0)
        assert len(results) >= 3  # z, iqr, trend, residual

    def test_stats(self):
        stats = self.engine.stats()
        assert "total_checks" in stats


class TestStatsEngine:
    def setup_method(self):
        self.engine = StatsEngine()

    def test_summarize_empty(self):
        result = self.engine.summarize("empty")
        assert result.count == 0

    def test_summarize(self):
        for v in [1.0, 2.0, 3.0, 4.0, 5.0]:
            self.engine.record("metric", v)
        result = self.engine.summarize("metric")
        assert result.count == 5
        assert result.mean == pytest.approx(3.0)
        assert result.min_val == pytest.approx(1.0)
        assert result.max_val == pytest.approx(5.0)

    def test_compare_insufficient(self):
        self.engine.record("m", 1.0)
        result = self.engine.compare("m")
        assert "error" in result

    def test_compare_windows(self):
        for i in range(200):
            self.engine.record("m2", float(i))
        result = self.engine.compare("m2", window_a=50, window_b=50)
        assert "change_pct" in result
        assert result["direction"] == "up"

    def test_moving_average(self):
        for v in [1.0, 2.0, 3.0, 4.0, 5.0]:
            self.engine.record("ma", v)
        ma = self.engine.moving_average("ma", window=3)
        assert len(ma) == 3
        assert ma[0] == pytest.approx(2.0)

    def test_correlation(self):
        for i in range(10):
            self.engine.record("a", float(i))
            self.engine.record("b", float(i * 2))
        r = self.engine.correlation("a", "b")
        assert r is not None
        assert r == pytest.approx(1.0, abs=0.01)

    def test_correlation_insufficient(self):
        self.engine.record("x", 1.0)
        r = self.engine.correlation("x", "y")
        assert r is None


class TestOptimizationEngine:
    def setup_method(self):
        self.engine = OptimizationEngine()

    def test_minimize_energy_cost(self):
        prices = [10.0, 8.0, 5.0, 3.0, 4.0, 12.0]
        result = self.engine.minimize_energy_cost(
            hourly_prices=prices, required_kwh=6.0, max_power_kw=3.0
        )
        assert result.success is True
        assert len(result.schedule) >= 1
        # Should prefer cheapest hours
        hours_used = [s["hour"] for s in result.schedule]
        assert 3 in hours_used  # index of 3.0 (cheapest)

    def test_minimize_empty_prices(self):
        result = self.engine.minimize_energy_cost([], 5.0)
        assert result.success is False

    def test_allocate_resources(self):
        tasks = [
            {"id": "a", "priority": 90, "demand": 2.0},
            {"id": "b", "priority": 50, "demand": 3.0},
            {"id": "c", "priority": 70, "demand": 2.0},
        ]
        result = self.engine.allocate_resources(tasks=tasks, capacity=5.0)
        assert result.success is True
        allocs = result.solution["allocations"]
        assert allocs["a"] == 2.0  # highest priority, served first

    def test_schedule_tasks(self):
        tasks = [
            {"id": "x", "duration_slots": 2, "deadline_slot": 5},
            {"id": "y", "duration_slots": 1, "deadline_slot": 3},
            {"id": "z", "duration_slots": 3, "deadline_slot": 8},
        ]
        result = self.engine.schedule_tasks(tasks=tasks)
        assert result.success is True
        assert len(result.schedule) >= 1

    def test_schedule_empty(self):
        result = self.engine.schedule_tasks(tasks=[])
        assert result.success is False
