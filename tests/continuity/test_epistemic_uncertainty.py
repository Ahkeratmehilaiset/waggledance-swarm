# SPDX-License-Identifier: Apache-2.0
"""Tests for epistemic uncertainty scoring and curiosity goals (v3.2 Phase 3)."""

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from waggledance.core.world.epistemic_uncertainty import (
    compute_uncertainty,
    generate_curiosity_goals,
    UncertaintyReport,
    DEFAULT_CURIOSITY_MAX_ACTIVE,
)


def _entity(eid, updated_at=None):
    """Helper: fake entity dict."""
    return {"entity_id": eid, "updated_at": updated_at or time.time()}


class TestComputeUncertainty:
    def test_zero_uncertainty_all_fresh(self):
        now = time.time()
        entities = [_entity("hive_1", now), _entity("hive_2", now)]
        baselines = {"hive_1.temp", "hive_2.temp"}
        report = compute_uncertainty(entities, baselines, now=now)
        assert report.score == 0.0
        assert report.missing_baselines == 0
        assert report.stale_entities == 0

    def test_missing_baselines_increase_uncertainty(self):
        now = time.time()
        entities = [_entity("a", now), _entity("b", now), _entity("c", now)]
        baselines = {"a.temp"}  # b and c missing
        report = compute_uncertainty(entities, baselines, now=now)
        assert report.missing_baselines == 2
        assert report.score > 0

    def test_stale_entities_increase_uncertainty(self):
        now = time.time()
        old = now - 7200  # 2 hours ago, TTL=1h
        entities = [_entity("a", old), _entity("b", now)]
        baselines = {"a.temp", "b.temp"}
        report = compute_uncertainty(entities, baselines, stale_ttl_seconds=3600, now=now)
        assert report.stale_entities == 1
        assert "a" in report.stale_entity_ids

    def test_open_goals_increase_uncertainty(self):
        now = time.time()
        entities = [_entity("a", now)]
        baselines = {"a.temp"}
        report = compute_uncertainty(entities, baselines, open_observe_goals=3, now=now)
        assert report.unresolved_questions == 3
        assert report.score > 0

    def test_score_bounded_0_1(self):
        now = time.time()
        old = now - 99999
        entities = [_entity(f"e{i}", old) for i in range(5)]
        baselines = set()  # all missing
        report = compute_uncertainty(entities, baselines, open_observe_goals=100, now=now)
        assert 0.0 <= report.score <= 1.0

    def test_empty_entities(self):
        report = compute_uncertainty([], set())
        assert report.score == 0.0
        assert report.total_tracked == 0

    def test_to_dict(self):
        now = time.time()
        entities = [_entity("x", now - 7200)]
        report = compute_uncertainty(entities, set(), now=now)
        d = report.to_dict()
        assert "score" in d
        assert "stale_entity_ids" in d


class TestGenerateCuriosityGoals:
    def test_no_goals_below_threshold(self):
        report = UncertaintyReport(score=0.3)
        goals = generate_curiosity_goals(report, threshold=0.4)
        assert goals == []

    def test_goals_generated_above_threshold(self):
        report = UncertaintyReport(
            score=0.6,
            stale_entity_ids=["hive_3"],
            missing_baseline_keys=["sensor_5"],
        )
        goals = generate_curiosity_goals(report, threshold=0.4)
        assert len(goals) >= 1
        assert goals[0].entity_id == "hive_3"

    def test_priority_capped(self):
        report = UncertaintyReport(
            score=0.8,
            stale_entity_ids=["a"],
        )
        goals = generate_curiosity_goals(report, max_priority=20)
        assert all(g.priority <= 20 for g in goals)

    def test_respects_profile_max_active(self):
        report = UncertaintyReport(
            score=0.9,
            stale_entity_ids=[f"e{i}" for i in range(50)],
        )
        goals = generate_curiosity_goals(
            report, existing_curiosity_count=0, profile="GADGET",
        )
        assert len(goals) <= DEFAULT_CURIOSITY_MAX_ACTIVE["GADGET"]

    def test_no_goals_when_budget_exhausted(self):
        report = UncertaintyReport(score=0.9, stale_entity_ids=["x"])
        goals = generate_curiosity_goals(
            report, existing_curiosity_count=10, profile="HOME",
        )
        assert goals == []

    def test_stale_before_missing(self):
        report = UncertaintyReport(
            score=0.7,
            stale_entity_ids=["stale_1"],
            missing_baseline_keys=["missing_1"],
        )
        goals = generate_curiosity_goals(report)
        reasons = [g.reason for g in goals]
        if len(reasons) >= 2:
            assert reasons[0] == "stale"


class TestObservabilityGaps:
    """Sensor silence increases uncertainty (observability degradation)."""

    def test_silent_sensor_raises_uncertainty(self):
        now = time.time()
        silent_since = now - 7200  # 2h silent, 1h TTL
        entities = [
            _entity("sensor_active", now),
            _entity("sensor_silent", silent_since),
        ]
        baselines = {"sensor_active.reading", "sensor_silent.reading"}
        report = compute_uncertainty(entities, baselines, stale_ttl_seconds=3600, now=now)
        assert report.stale_entities == 1
        assert "sensor_silent" in report.stale_entity_ids
        assert report.score > 0
