# SPDX-License-Identifier: Apache-2.0
"""Tests for motive valence scoring and conflict resolution (v3.2 Phase 6)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from waggledance.core.domain.autonomy import Goal, GoalType, GoalStatus
from waggledance.core.goals.motives import (
    MotiveConfig, MotiveRegistry, ConflictResult,
    resolve_conflict, compute_effective_priority,
)


class TestMotiveRegistry:
    def test_from_yaml(self):
        registry = MotiveRegistry.from_yaml()
        assert len(registry) == 6
        assert "reliability" in registry
        assert "minimize_harm" in registry

    def test_get_motive(self):
        registry = MotiveRegistry.from_yaml()
        m = registry.get("user_trust")
        assert m is not None
        assert m.base_valence == 0.9

    def test_all_motives(self):
        registry = MotiveRegistry.from_yaml()
        all_m = registry.all()
        assert len(all_m) == 6
        ids = [m.id for m in all_m]
        assert "keep_promises" in ids

    def test_missing_config(self, tmp_path):
        registry = MotiveRegistry.from_yaml(tmp_path / "missing.yaml")
        assert len(registry) == 0


class TestValenceScoring:
    def test_no_motive_returns_base_priority(self):
        g = Goal(priority=50)
        assert compute_effective_priority(g) == 50.0

    def test_with_motive_scales_priority(self):
        g = Goal(priority=50, active_motive_id="reliability", motive_valence=0.8)
        m = MotiveConfig(id="reliability", base_valence=0.8)
        result = compute_effective_priority(g, m)
        # 50 * 0.8 * 0.8 = 32.0
        assert result == pytest.approx(32.0)

    def test_high_valence_high_priority(self):
        g = Goal(priority=80, active_motive_id="minimize_harm", motive_valence=1.0)
        m = MotiveConfig(id="minimize_harm", base_valence=1.0)
        result = compute_effective_priority(g, m)
        assert result == 80.0  # 80 * 1.0 * 1.0

    def test_zero_valence_uses_base(self):
        g = Goal(priority=50, active_motive_id="x", motive_valence=0.0)
        m = MotiveConfig(id="x", base_valence=0.6)
        result = compute_effective_priority(g, m)
        # valence=0 so uses base_valence: 50 * 0.6 * 0.6 = 18.0
        assert result == pytest.approx(18.0)


class TestConflictResolution:
    def test_minimize_harm_always_wins(self):
        ma = MotiveConfig(id="minimize_harm", base_valence=1.0)
        mb = MotiveConfig(id="keep_promises", base_valence=0.75)
        ga = Goal()
        gb = Goal()
        result = resolve_conflict(ma, mb, ga, gb, "minimize_harm_always")
        assert result.winner_id == "minimize_harm"

    def test_minimize_harm_wins_even_as_b(self):
        ma = MotiveConfig(id="keep_promises", base_valence=0.75)
        mb = MotiveConfig(id="minimize_harm", base_valence=1.0)
        ga = Goal()
        gb = Goal()
        result = resolve_conflict(ma, mb, ga, gb, "minimize_harm_always")
        assert result.winner_id == "minimize_harm"

    def test_user_promise_first(self):
        ma = MotiveConfig(id="reliability", base_valence=0.8)
        mb = MotiveConfig(id="keep_promises", base_valence=0.75)
        ga = Goal(promise_to_user=True)
        gb = Goal(promise_to_user=False)
        result = resolve_conflict(ma, mb, ga, gb, "user_promise_first")
        assert result.winner_id == "reliability"  # goal_a has promise

    def test_highest_valence_wins(self):
        ma = MotiveConfig(id="a", base_valence=0.3)
        mb = MotiveConfig(id="b", base_valence=0.9)
        result = resolve_conflict(ma, mb, Goal(), Goal(), "highest_valence_wins")
        assert result.winner_id == "b"

    def test_conflict_result_has_reason(self):
        ma = MotiveConfig(id="a", base_valence=0.5)
        mb = MotiveConfig(id="b", base_valence=0.5)
        result = resolve_conflict(ma, mb, Goal(), Goal(), "highest_valence_wins")
        assert result.reason != ""
        assert result.strategy == "highest_valence_wins"


class TestGoalEnginePrioritization:
    def test_promise_boosts_priority(self):
        from waggledance.core.goals.goal_engine import GoalEngine
        engine = GoalEngine()
        g1 = engine.propose("observe", "normal goal", priority=50)
        g2 = engine.propose("observe", "promised goal", priority=50)
        g2.promise_to_user = True
        ordered = engine.prioritize()
        assert ordered[0].goal_id == g2.goal_id  # promise_to_user +15

    def test_valence_boosts_priority(self):
        from waggledance.core.goals.goal_engine import GoalEngine
        engine = GoalEngine()
        g1 = engine.propose("observe", "low valence", priority=50)
        g2 = engine.propose("observe", "high valence", priority=50)
        g2.motive_valence = 0.9
        ordered = engine.prioritize()
        assert ordered[0].goal_id == g2.goal_id

    def test_unfinished_business(self):
        from waggledance.core.goals.goal_engine import GoalEngine
        engine = GoalEngine()
        g1 = engine.propose("observe", "carry forward", priority=50)
        g1.carry_forward = True
        g2 = engine.propose("observe", "normal", priority=50)
        ub = engine.get_unfinished_business()
        assert len(ub) == 1
        assert ub[0].goal_id == g1.goal_id

    def test_open_observe_goals(self):
        from waggledance.core.goals.goal_engine import GoalEngine
        engine = GoalEngine()
        engine.propose("observe", "open observe")
        engine.propose("act", "not observe")
        open_obs = engine.get_open_observe_goals()
        assert len(open_obs) == 1
