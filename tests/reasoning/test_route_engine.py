# SPDX-License-Identifier: Apache-2.0
"""Direct unit tests for waggledance.core.reasoning.route_engine.RouteEngine.

Codex test-coverage scout flagged this file as Candidate 3 (medium risk):
``RouteAnalyzerAdapter`` exposes ``RouteEngine`` to the capability layer
for route health and optimization decisions, but ``tests/autonomy/
test_engine_adapters.py`` only asserts return-shape on metrics/accuracy/
recommendations calls. Threshold-crossing recommendation logic, p50/p99
percentile math, fallback-rate aggregation, quality distribution, and
the bounded-history trim were uncovered.

These tests record deterministic decisions and assert exact metric
values — if a regression flips a comparator or shifts a percentile
index, these assertions break.
"""
from __future__ import annotations

import pytest

from waggledance.core.reasoning.route_engine import (
    RouteDecision,
    RouteEngine,
    RouteMetrics,
)


@pytest.fixture()
def engine():
    return RouteEngine()


# --- record_decision + history -------------------------------------

def test_record_decision_appends_and_updates_quality_counts(engine):
    engine.record_decision("intent_a", "weather", "gold", True, 100.0)
    engine.record_decision("intent_a", "weather", "gold", True, 120.0)
    engine.record_decision("intent_b", "math", "silver", False, 200.0)

    assert len(engine._decisions) == 3
    assert engine._quality_counts == {"gold": 2, "silver": 1}


def test_bounded_history_trims_oldest_when_max_exceeded():
    engine = RouteEngine()
    engine._max_decisions = 5
    for i in range(10):
        engine.record_decision("intent_a", "x", "gold", True, float(i))
    assert len(engine._decisions) == 5
    # Should retain the LAST 5 (latencies 5..9), oldest 5 dropped.
    assert [d.latency_ms for d in engine._decisions] == [5.0, 6.0, 7.0, 8.0, 9.0]


def test_bounded_history_keeps_quality_counts_in_lockstep_with_decisions():
    """Codex review of PR #103: when trimming bounded history, the
    `_quality_counts` tally must drop in lockstep with `_decisions`,
    otherwise stats() and get_quality_distribution() describe different
    history windows (one bounded, one all-time).
    """
    engine = RouteEngine()
    engine._max_decisions = 5
    # First 5 decisions are 'gold'.
    for _ in range(5):
        engine.record_decision("a", "x", "gold", True, 1.0)
    # Next 5 are 'silver' — they should evict the 'gold' decisions entirely.
    for _ in range(5):
        engine.record_decision("a", "x", "silver", True, 1.0)
    # Both views must describe the same window: 5 silver, no gold.
    assert len(engine._decisions) == 5
    assert sum(engine._quality_counts.values()) == 5
    assert engine._quality_counts == {"silver": 5}
    assert engine.get_quality_distribution() == {"silver": 1.0}


def test_stats_total_decisions_matches_quality_distribution_denominator():
    """stats() and get_quality_distribution() must agree on the
    denominator after trim (Codex review of PR #103).
    """
    engine = RouteEngine()
    engine._max_decisions = 4
    # 6 decisions => oldest 2 ('gold', 'silver') evicted; window is the
    # last 4: ['gold', 'bronze', 'silver', 'silver'].
    paths = ["gold", "silver", "gold", "bronze", "silver", "silver"]
    for p in paths:
        engine.record_decision("a", "x", p, True, 1.0)

    s = engine.stats()
    assert s["total_decisions"] == 4
    assert sum(engine._quality_counts.values()) == 4
    assert engine._quality_counts == {"gold": 1, "bronze": 1, "silver": 2}
    dist = engine.get_quality_distribution()
    assert sum(dist.values()) == pytest.approx(1.0)
    assert dist["silver"] == pytest.approx(0.5)


def test_telemetry_record_failure_is_swallowed():
    """Telemetry exceptions must not crash record_decision (silent fallback)."""
    class _BadTelemetry:
        def record(self, *a, **kw):
            raise RuntimeError("telemetry down")

    engine = RouteEngine(telemetry=_BadTelemetry())
    # Should not raise.
    engine.record_decision("intent_a", "x", "gold", True, 50.0)
    assert len(engine._decisions) == 1


# --- aggregate metrics ---------------------------------------------

def test_get_route_metrics_aggregates_counts_and_accuracy(engine):
    # intent_a: 4 queries, 3 successes, 1 fallback => accuracy 0.75, fallback 0.25
    engine.record_decision("intent_a", "x", "gold", True, 100.0)
    engine.record_decision("intent_a", "x", "gold", True, 200.0)
    engine.record_decision("intent_a", "x", "gold", True, 300.0)
    engine.record_decision("intent_a", "x", "silver", False, 400.0, was_fallback=True)
    # intent_b: 2 queries, 1 success
    engine.record_decision("intent_b", "y", "gold", True, 50.0)
    engine.record_decision("intent_b", "y", "bronze", False, 150.0)

    metrics = engine.get_route_metrics()
    by_route = {m.route_type: m for m in metrics}

    assert by_route["intent_a"].total_queries == 4
    assert by_route["intent_a"].successes == 3
    assert by_route["intent_a"].fallbacks == 1
    assert by_route["intent_a"].accuracy == pytest.approx(0.75)
    assert by_route["intent_a"].llm_fallback_rate == pytest.approx(0.25)

    assert by_route["intent_b"].total_queries == 2
    assert by_route["intent_b"].accuracy == pytest.approx(0.5)


def test_get_route_metrics_filters_by_route_type(engine):
    engine.record_decision("intent_a", "x", "gold", True, 100.0)
    engine.record_decision("intent_b", "y", "gold", True, 100.0)
    metrics = engine.get_route_metrics(route_type="intent_a")
    assert len(metrics) == 1
    assert metrics[0].route_type == "intent_a"


def test_get_route_metrics_sorted_by_total_queries_desc(engine):
    for _ in range(2):
        engine.record_decision("a", "x", "gold", True, 1.0)
    for _ in range(5):
        engine.record_decision("b", "x", "gold", True, 1.0)
    for _ in range(3):
        engine.record_decision("c", "x", "gold", True, 1.0)
    metrics = engine.get_route_metrics()
    assert [m.route_type for m in metrics] == ["b", "c", "a"]


def test_get_route_metrics_empty_returns_empty_list(engine):
    assert engine.get_route_metrics() == []


# --- percentile math (the load-bearing index arithmetic) -----------

def test_p50_and_p99_indexes_use_documented_formulas(engine):
    """p50 = latencies[total // 2]; p99 = latencies[int(total * 0.99)]."""
    # 100 evenly-spaced latencies 0, 10, 20, ..., 990
    for i in range(100):
        engine.record_decision("r", "x", "gold", True, float(i * 10))

    m = engine.get_route_metrics()[0]
    # p50 = sorted[100 // 2] = sorted[50] = 500.0
    assert m.p50_latency_ms == pytest.approx(500.0)
    # p99 = sorted[int(100 * 0.99)] = sorted[99] = 990.0
    assert m.p99_latency_ms == pytest.approx(990.0)
    # avg = mean(0..990 step 10) = 495.0
    assert m.avg_latency_ms == pytest.approx(495.0)


# --- overall metrics -----------------------------------------------

def test_get_llm_fallback_rate_overall(engine):
    engine.record_decision("a", "x", "gold", True, 1.0, was_fallback=False)
    engine.record_decision("a", "x", "gold", True, 1.0, was_fallback=True)
    engine.record_decision("b", "x", "gold", True, 1.0, was_fallback=True)
    engine.record_decision("b", "x", "gold", True, 1.0, was_fallback=True)
    # 3 of 4 are fallbacks => 0.75
    assert engine.get_llm_fallback_rate() == pytest.approx(0.75)


def test_get_llm_fallback_rate_empty_returns_zero(engine):
    assert engine.get_llm_fallback_rate() == 0.0


def test_get_route_accuracy_overall(engine):
    engine.record_decision("a", "x", "gold", True, 1.0)
    engine.record_decision("a", "x", "gold", False, 1.0)
    engine.record_decision("b", "x", "gold", True, 1.0)
    assert engine.get_route_accuracy() == pytest.approx(2 / 3)


def test_get_route_accuracy_empty_returns_zero(engine):
    assert engine.get_route_accuracy() == 0.0


# --- quality distribution -----------------------------------------

def test_get_quality_distribution_normalizes_to_one(engine):
    for _ in range(6):
        engine.record_decision("a", "x", "gold", True, 1.0)
    for _ in range(3):
        engine.record_decision("a", "x", "silver", True, 1.0)
    engine.record_decision("a", "x", "bronze", True, 1.0)
    dist = engine.get_quality_distribution()
    assert dist["gold"] == pytest.approx(0.6)
    assert dist["silver"] == pytest.approx(0.3)
    assert dist["bronze"] == pytest.approx(0.1)
    assert sum(dist.values()) == pytest.approx(1.0)


def test_get_quality_distribution_empty_returns_empty_dict(engine):
    assert engine.get_quality_distribution() == {}


# --- specialist accuracy ------------------------------------------

def test_get_specialist_accuracy_groups_by_intent(engine):
    engine.record_decision("r", "weather", "gold", True, 1.0)
    engine.record_decision("r", "weather", "gold", True, 1.0)
    engine.record_decision("r", "weather", "gold", False, 1.0)
    engine.record_decision("r", "math", "gold", True, 1.0)

    sa = engine.get_specialist_accuracy()
    # weather: 2 of 3 = 0.6667
    assert sa["weather"] == pytest.approx(2 / 3)
    # math: 1 of 1 = 1.0
    assert sa["math"] == pytest.approx(1.0)


# --- recommend_improvements (threshold gates) ---------------------

def test_recommend_low_accuracy_fires_below_70_percent_with_min_volume(engine):
    # 11 queries (>10), accuracy = 4/11 ~= 36% (<70%)
    for _ in range(4):
        engine.record_decision("r", "x", "gold", True, 100.0)
    for _ in range(7):
        engine.record_decision("r", "x", "gold", False, 100.0)

    issues = {r["issue"]: r for r in engine.recommend_improvements()}
    assert "low_accuracy" in issues
    assert issues["low_accuracy"]["route"] == "r"


def test_recommend_low_accuracy_does_not_fire_below_min_volume(engine):
    # 10 queries — below the >10 gate, recommendation should not fire even
    # though accuracy is 0%.
    for _ in range(10):
        engine.record_decision("r", "x", "gold", False, 100.0)
    issues = {r["issue"] for r in engine.recommend_improvements()}
    assert "low_accuracy" not in issues


def test_recommend_high_fallback_fires_above_50_percent_with_min_volume(engine):
    # 11 queries (>10), 7 fallbacks => 64% (>50%)
    for _ in range(4):
        engine.record_decision("r", "x", "gold", True, 100.0, was_fallback=False)
    for _ in range(7):
        engine.record_decision("r", "x", "gold", True, 100.0, was_fallback=True)

    issues = {r["issue"]: r for r in engine.recommend_improvements()}
    assert "high_fallback" in issues


def test_recommend_high_latency_fires_above_3000ms_with_min_volume(engine):
    # 6 queries (>5), avg 4000ms (>3000)
    for _ in range(6):
        engine.record_decision("r", "x", "gold", True, 4000.0)
    issues = {r["issue"]: r for r in engine.recommend_improvements()}
    assert "high_latency" in issues


def test_recommend_high_latency_does_not_fire_at_or_below_volume_threshold(engine):
    # Exactly 5 queries — gate is `> 5`, so high-latency must not fire.
    for _ in range(5):
        engine.record_decision("r", "x", "gold", True, 5000.0)
    issues = {r["issue"] for r in engine.recommend_improvements()}
    assert "high_latency" not in issues


def test_recommend_returns_empty_list_when_all_thresholds_clear(engine):
    # Healthy route: 100% accuracy, no fallbacks, fast.
    for _ in range(20):
        engine.record_decision("r", "x", "gold", True, 100.0)
    assert engine.recommend_improvements() == []


# --- stats -------------------------------------------------------

def test_stats_aggregates_overall_state(engine):
    engine.record_decision("r", "x", "gold", True, 100.0)
    engine.record_decision("r", "x", "gold", False, 200.0, was_fallback=True)
    s = engine.stats()
    assert s["total_decisions"] == 2
    assert s["overall_accuracy"] == pytest.approx(0.5)
    assert s["llm_fallback_rate"] == pytest.approx(0.5)
    assert s["routes_tracked"] == 1
    assert s["legacy_router_available"] is False
    assert s["legacy_telemetry_available"] is False


def test_stats_with_legacy_components_reports_them_available():
    engine = RouteEngine(smart_router=object(), telemetry=object())
    s = engine.stats()
    assert s["legacy_router_available"] is True
    assert s["legacy_telemetry_available"] is True
