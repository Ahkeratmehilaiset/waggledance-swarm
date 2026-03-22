# SPDX-License-Identifier: Apache-2.0
"""Tests for impact-prioritized curiosity goals.

Covers:
  - Impact scoring produces correct ranking
  - High-error topics get prioritized over low-error ones
  - Per-profile cap still enforced (GADGET=3, HOME=10 etc)
  - Zero-error topics get deprioritized
  - Low capability confidence triggers domain curiosity
  - Old generate_curiosity_goals still works unchanged
"""

from __future__ import annotations

import time

import pytest

from waggledance.core.world.epistemic_uncertainty import (
    CuriosityGoal,
    DEFAULT_CURIOSITY_MAX_ACTIVE,
    UncertaintyReport,
    _compute_impact,
    generate_curiosity_goals,
    generate_impact_curiosity_goals,
)


# ── Helpers ──────────────────────────────────────────────────


def _report(
    score: float = 0.7,
    stale: list | None = None,
    missing: list | None = None,
) -> UncertaintyReport:
    return UncertaintyReport(
        score=score,
        stale_entity_ids=stale or [],
        missing_baseline_keys=missing or [],
        stale_entities=len(stale or []),
        missing_baselines=len(missing or []),
    )


# ── Impact scoring ──────────────────────────────────────────


class TestComputeImpact:
    def test_all_zero(self):
        score = _compute_impact(
            "topic", {}, {}, {}, set(), max_queries=0,
        )
        # No error freq, default confidence 0.5 → 0.3*(1-0.5) = 0.15
        assert score == pytest.approx(0.15)

    def test_high_error_boosts(self):
        low = _compute_impact("t", {"t": 0.0}, {}, {}, set(), 0)
        high = _compute_impact("t", {"t": 0.9}, {}, {}, set(), 0)
        assert high > low

    def test_low_confidence_boosts(self):
        good = _compute_impact("thermal", {}, {"solve.thermal": 0.95}, {}, set(), 0)
        bad = _compute_impact("thermal", {}, {"solve.thermal": 0.1}, {}, set(), 0)
        assert bad > good

    def test_high_query_freq_boosts(self):
        rare = _compute_impact("t", {}, {}, {"t": 1}, set(), max_queries=100)
        freq = _compute_impact("t", {}, {}, {"t": 100}, set(), max_queries=100)
        assert freq > rare

    def test_staleness_boosts(self):
        fresh = _compute_impact("t", {}, {}, {}, set(), 0)
        stale = _compute_impact("t", {}, {}, {}, {"t"}, 0)
        assert stale > fresh

    def test_max_impact(self):
        score = _compute_impact(
            "t",
            {"t": 1.0},                 # max error freq
            {"solve.t": 0.0},           # min confidence
            {"t": 50},                   # max queries
            {"t"},                       # stale
            max_queries=50,
        )
        assert score == pytest.approx(1.0)


# ── Impact-prioritized goals ────────────────────────────────


class TestGenerateImpactCuriosityGoals:
    def test_no_goals_below_threshold(self):
        report = _report(score=0.3)
        goals = generate_impact_curiosity_goals(report, threshold=0.4)
        assert goals == []

    def test_high_error_prioritized(self):
        report = _report(
            stale=["low_err_topic", "high_err_topic"],
        )
        goals = generate_impact_curiosity_goals(
            report,
            error_frequency={
                "high_err_topic": 0.8,
                "low_err_topic": 0.1,
            },
        )
        assert len(goals) >= 2
        # High-error topic should be first (error topics sorted by impact)
        assert goals[0].entity_id == "high_err_topic"
        assert goals[0].impact_score > goals[1].impact_score

    def test_zero_error_deprioritized(self):
        report = _report(
            stale=["zero_err", "some_err"],
        )
        goals = generate_impact_curiosity_goals(
            report,
            error_frequency={
                "some_err": 0.5,
                "zero_err": 0.0,
            },
        )
        assert len(goals) >= 2
        # Topic with errors should come before zero-error topic
        err_topics = [g.entity_id for g in goals if g.entity_id == "some_err"]
        zero_topics = [g.entity_id for g in goals if g.entity_id == "zero_err"]
        assert goals.index(
            next(g for g in goals if g.entity_id == "some_err")
        ) < goals.index(
            next(g for g in goals if g.entity_id == "zero_err")
        )

    def test_profile_cap_gadget(self):
        report = _report(
            stale=[f"e{i}" for i in range(20)],
        )
        goals = generate_impact_curiosity_goals(
            report,
            profile="GADGET",
        )
        assert len(goals) <= DEFAULT_CURIOSITY_MAX_ACTIVE["GADGET"]
        assert len(goals) == 3

    def test_profile_cap_home(self):
        report = _report(
            stale=[f"e{i}" for i in range(20)],
        )
        goals = generate_impact_curiosity_goals(
            report,
            profile="HOME",
        )
        assert len(goals) <= DEFAULT_CURIOSITY_MAX_ACTIVE["HOME"]
        assert len(goals) == 10

    def test_profile_cap_factory(self):
        report = _report(
            stale=[f"e{i}" for i in range(30)],
        )
        goals = generate_impact_curiosity_goals(
            report,
            profile="FACTORY",
        )
        assert len(goals) <= DEFAULT_CURIOSITY_MAX_ACTIVE["FACTORY"]
        assert len(goals) == 20

    def test_existing_curiosity_reduces_budget(self):
        report = _report(
            stale=[f"e{i}" for i in range(10)],
        )
        goals = generate_impact_curiosity_goals(
            report,
            profile="HOME",
            existing_curiosity_count=8,
        )
        assert len(goals) <= 2  # 10 max - 8 existing = 2

    def test_budget_exhausted(self):
        report = _report(stale=["x"])
        goals = generate_impact_curiosity_goals(
            report,
            profile="GADGET",
            existing_curiosity_count=3,
        )
        assert goals == []

    def test_low_confidence_triggers_domain_curiosity(self):
        report = _report(score=0.6)
        goals = generate_impact_curiosity_goals(
            report,
            capability_confidence={
                "solve.thermal": 0.3,
                "solve.math": 0.95,
            },
        )
        assert len(goals) >= 1
        thermal_goals = [g for g in goals if "thermal" in g.entity_id]
        assert len(thermal_goals) == 1
        assert thermal_goals[0].reason == "low_confidence"
        assert "confidence 0.30" in thermal_goals[0].description

    def test_high_error_intent_creates_goal(self):
        report = _report(score=0.6)
        goals = generate_impact_curiosity_goals(
            report,
            error_frequency={"anomaly": 0.7},
        )
        anomaly_goals = [g for g in goals if g.entity_id == "anomaly"]
        assert len(anomaly_goals) == 1
        assert anomaly_goals[0].reason == "high_error"

    def test_goals_have_impact_score(self):
        report = _report(
            stale=["a", "b"],
        )
        goals = generate_impact_curiosity_goals(
            report,
            error_frequency={"a": 0.9, "b": 0.1},
        )
        assert all(hasattr(g, "impact_score") for g in goals)
        assert all(0.0 <= g.impact_score <= 1.0 for g in goals)

    def test_combined_scoring(self):
        """Topic with high error + low confidence + high query freq should win."""
        report = _report(
            stale=["good_topic", "bad_topic"],
        )
        goals = generate_impact_curiosity_goals(
            report,
            error_frequency={
                "bad_topic": 0.9,
                "good_topic": 0.0,
            },
            capability_confidence={
                "solve.bad_topic": 0.1,
                "solve.good_topic": 0.95,
            },
            query_counts={
                "bad_topic": 50,
                "good_topic": 2,
            },
        )
        assert len(goals) >= 2
        assert goals[0].entity_id == "bad_topic"


# ── Old API still works ─────────────────────────────────────


class TestOldGenerateCuriosityGoals:
    def test_still_works(self):
        report = _report(
            stale=["a"],
            missing=["b"],
        )
        goals = generate_curiosity_goals(report)
        assert len(goals) == 2
        assert goals[0].reason == "stale"
        assert goals[1].reason == "missing_baseline"

    def test_impact_score_defaults_zero(self):
        report = _report(stale=["x"])
        goals = generate_curiosity_goals(report)
        assert goals[0].impact_score == 0.0


# ── Ledger helpers ───────────────────────────────────────────


class TestLedgerHelpers:
    def test_intent_error_frequency(self, tmp_path):
        from waggledance.core.learning.prediction_error_ledger import (
            PredictionErrorLedger,
        )

        ledger = PredictionErrorLedger(str(tmp_path / "e.jsonl"))
        # 5 math queries: 1 error
        for i in range(5):
            ledger.record(f"q{i}", "solve.math", verified=(i != 2), intent="math")
        # 3 thermal queries: 2 errors
        for i in range(3):
            ledger.record(f"t{i}", "solve.thermal", verified=(i == 0), intent="thermal")

        freq = ledger.intent_error_frequency()
        assert "math" in freq
        assert "thermal" in freq
        assert freq["math"] == pytest.approx(0.2)
        assert freq["thermal"] == pytest.approx(2 / 3)

    def test_intent_query_counts(self, tmp_path):
        from waggledance.core.learning.prediction_error_ledger import (
            PredictionErrorLedger,
        )

        ledger = PredictionErrorLedger(str(tmp_path / "e.jsonl"))
        for i in range(10):
            ledger.record(f"q{i}", "solve.math", True, intent="math")
        for i in range(3):
            ledger.record(f"t{i}", "solve.thermal", True, intent="thermal")

        counts = ledger.intent_query_counts()
        assert counts["math"] == 10
        assert counts["thermal"] == 3

    def test_empty_ledger(self, tmp_path):
        from waggledance.core.learning.prediction_error_ledger import (
            PredictionErrorLedger,
        )

        ledger = PredictionErrorLedger(str(tmp_path / "e.jsonl"))
        assert ledger.intent_error_frequency() == {}
        assert ledger.intent_query_counts() == {}
