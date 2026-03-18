"""
Tests for AutonomyMetrics — KPI tracking for the autonomy runtime.

Covers:
- Record and retrieve metrics
- Route decision tracking
- Capability chain tracking
- Verification tracking
- Specialist accuracy per model
- Case grade tracking
- Proactive goal counting
- KPI aggregation with targets
- Daily counter reset
"""

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from waggledance.core.autonomy.metrics import AutonomyMetrics, MetricSample


class TestMetricSample:
    def test_create_sample(self):
        s = MetricSample(name="test", value=1.0)
        assert s.name == "test"
        assert s.value == 1.0
        assert s.timestamp > 0
        assert s.tags == {}

    def test_sample_with_tags(self):
        s = MetricSample(name="x", value=0.5, tags={"model": "phi4"})
        assert s.tags["model"] == "phi4"


class TestAutonomyMetrics:
    def test_record_and_get(self):
        m = AutonomyMetrics()
        m.record("test_metric", 1.0)
        m.record("test_metric", 0.5)
        avg = m.get_metric("test_metric")
        assert avg == 0.75

    def test_get_empty_metric(self):
        m = AutonomyMetrics()
        assert m.get_metric("nonexistent") == 0.0

    def test_increment_counter(self):
        m = AutonomyMetrics()
        m.increment("clicks")
        m.increment("clicks")
        m.increment("clicks", 3)
        assert m.get_counter("clicks") == 5

    def test_get_empty_counter(self):
        m = AutonomyMetrics()
        assert m.get_counter("nothing") == 0

    def test_record_route_decision_success(self):
        m = AutonomyMetrics()
        m.record_route_decision(success=True, was_fallback=False, latency_ms=50.0)
        assert m.get_metric("route_accuracy") == 1.0
        assert m.get_metric("llm_fallback") == 0.0

    def test_record_route_decision_fallback(self):
        m = AutonomyMetrics()
        m.record_route_decision(success=True, was_fallback=True, latency_ms=2000.0)
        assert m.get_metric("llm_fallback") == 1.0

    def test_record_capability_chain(self):
        m = AutonomyMetrics()
        m.record_capability_chain(success=True, chain_length=3)
        m.record_capability_chain(success=False, chain_length=1)
        avg = m.get_metric("capability_chain_success")
        assert avg == 0.5

    def test_record_verification(self):
        m = AutonomyMetrics()
        m.record_verification(passed=True, confidence=0.95)
        m.record_verification(passed=True, confidence=0.88)
        m.record_verification(passed=False, confidence=0.30)
        avg = m.get_metric("verifier_pass")
        assert abs(avg - 2.0 / 3.0) < 0.01

    def test_record_specialist_prediction(self):
        m = AutonomyMetrics()
        m.record_specialist_prediction("phi4", correct=True)
        m.record_specialist_prediction("phi4", correct=True)
        m.record_specialist_prediction("phi4", correct=False)
        m.record_specialist_prediction("llama", correct=True)
        per_model = m.get_per_specialist_accuracy()
        assert abs(per_model["phi4"] - 2.0 / 3.0) < 0.01
        assert per_model["llama"] == 1.0

    def test_record_case_grade(self):
        m = AutonomyMetrics()
        m.record_case_grade("gold")
        m.record_case_grade("gold")
        m.record_case_grade("silver")
        m.record_case_grade("quarantine")
        assert m.get_counter("case_grade_gold") == 2
        assert m.get_counter("case_grade_silver") == 1
        assert m.get_counter("case_grade_quarantine") == 1

    def test_record_proactive_goal(self):
        m = AutonomyMetrics()
        m.record_proactive_goal()
        m.record_proactive_goal()
        assert m.get_counter("proactive_goals_today") == 2

    def test_get_kpis(self):
        m = AutonomyMetrics()
        # Record some data
        for _ in range(10):
            m.record_route_decision(success=True, was_fallback=False)
        m.record_route_decision(success=False, was_fallback=True)

        kpis = m.get_kpis()
        assert "route_accuracy" in kpis
        assert "llm_fallback_rate" in kpis
        assert "capability_chain_success_rate" in kpis
        assert "verifier_pass_rate" in kpis
        assert "specialist_accuracy" in kpis
        assert "night_learning_gold_rate" in kpis
        assert "case_grades" in kpis
        assert "proactive_goals_today" in kpis

        # Route accuracy should be ~0.909
        assert kpis["route_accuracy"]["value"] > 0.9
        assert kpis["route_accuracy"]["target"] == 0.90

    def test_kpi_meets_target(self):
        m = AutonomyMetrics()
        for _ in range(100):
            m.record_route_decision(success=True, was_fallback=False)
        kpis = m.get_kpis()
        assert kpis["route_accuracy"]["meets_target"] is True
        assert kpis["llm_fallback_rate"]["meets_target"] is True

    def test_reset_daily_counters(self):
        m = AutonomyMetrics()
        m.record_proactive_goal()
        m.record_proactive_goal()
        assert m.get_counter("proactive_goals_today") == 2
        m.reset_daily_counters()
        assert m.get_counter("proactive_goals_today") == 0

    def test_max_samples_eviction(self):
        m = AutonomyMetrics(max_samples=10)
        for i in range(20):
            m.record("test", float(i))
        with m._lock:
            assert len(m._samples["test"]) == 10

    def test_stats(self):
        m = AutonomyMetrics()
        m.record("a", 1.0)
        m.record("b", 2.0)
        m.increment("c")
        stats = m.stats()
        assert stats["metrics_tracked"] == 2
        assert stats["total_samples"] == 2
        assert stats["counters"]["c"] == 1

    def test_targets_exist(self):
        assert "route_accuracy" in AutonomyMetrics.TARGETS
        assert "llm_fallback_rate" in AutonomyMetrics.TARGETS
        assert "specialist_accuracy" in AutonomyMetrics.TARGETS
        assert len(AutonomyMetrics.TARGETS) >= 8
