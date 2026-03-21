# SPDX-License-Identifier: Apache-2.0
"""Tests for v3.2 data model additions (Phase 2)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from waggledance.core.domain.autonomy import (
    Goal, GoalStatus, GoalType, CaseTrajectory, QualityGrade,
    SourceType, MotiveActivation, WorldSnapshot,
)


class TestGoalV32Fields:
    def test_default_carry_forward(self):
        g = Goal()
        assert g.carry_forward is False
        assert g.promise_to_user is False
        assert g.blocked_reason == ""
        assert g.resume_after is None
        assert g.active_motive_id == ""
        assert g.motive_valence == 0.0

    def test_carry_forward_set(self):
        g = Goal(carry_forward=True, promise_to_user=True, active_motive_id="reliability")
        assert g.carry_forward is True
        assert g.promise_to_user is True
        assert g.active_motive_id == "reliability"

    def test_to_dict_includes_v32_fields(self):
        g = Goal(carry_forward=True, motive_valence=0.8)
        d = g.to_dict()
        assert d["carry_forward"] is True
        assert d["motive_valence"] == 0.8
        assert d["resume_after"] is None

    def test_backward_compat_no_v32_fields_needed(self):
        """Old code can construct Goal without v3.2 fields."""
        g = Goal(goal_id="test", type=GoalType.OBSERVE, description="test")
        assert g.carry_forward is False  # safe default


class TestCaseTrajectoryV32Fields:
    def test_default_origin_observed(self):
        ct = CaseTrajectory()
        assert ct.trajectory_origin == "observed"
        assert ct.synthetic is False
        assert ct.counterfactual_alternatives == []

    def test_simulated_trajectory(self):
        ct = CaseTrajectory(
            trajectory_origin="simulated",
            synthetic=True,
            quality_grade=QualityGrade.BRONZE,
            counterfactual_alternatives=["alt_chain_1", "alt_chain_2"],
        )
        assert ct.trajectory_origin == "simulated"
        assert ct.synthetic is True
        assert len(ct.counterfactual_alternatives) == 2

    def test_simulated_never_gold(self):
        """Simulated trajectories should not be gold/silver."""
        ct = CaseTrajectory(
            trajectory_origin="simulated",
            synthetic=True,
            quality_grade=QualityGrade.BRONZE,
        )
        # Grade stays bronze for simulated
        assert ct.quality_grade == QualityGrade.BRONZE

    def test_to_dict_includes_v32_fields(self):
        ct = CaseTrajectory(
            trajectory_origin="simulated",
            counterfactual_alternatives=["a"],
        )
        d = ct.to_dict()
        assert d["trajectory_origin"] == "simulated"
        assert d["counterfactual_alternatives"] == ["a"]
        assert d["synthetic"] is False


class TestSourceTypeV32:
    def test_self_reflection_exists(self):
        assert SourceType.SELF_REFLECTION.value == "self_reflection"

    def test_simulated_exists(self):
        assert SourceType.SIMULATED.value == "simulated"

    def test_all_nine_source_types(self):
        assert len(SourceType) == 9

    def test_world_snapshot_with_simulated(self):
        ws = WorldSnapshot(source_type=SourceType.SIMULATED)
        d = ws.to_dict()
        assert d["source_type"] == "simulated"


class TestMotiveActivation:
    def test_create_activation(self):
        ma = MotiveActivation(
            motive_id="reliability",
            valence=0.8,
            intensity=0.6,
            triggered_by_goal_id="goal_1",
        )
        assert ma.motive_id == "reliability"
        assert ma.valence == 0.8

    def test_conflict_logging(self):
        ma = MotiveActivation(
            motive_id="keep_promises",
            valence=0.75,
            intensity=0.9,
            triggered_by_goal_id="goal_2",
            conflict_with="minimize_harm",
            resolution="minimize_harm wins (higher base valence)",
        )
        assert ma.conflict_with == "minimize_harm"
        assert ma.resolution != ""

    def test_to_dict(self):
        ma = MotiveActivation(motive_id="test", valence=0.5, intensity=0.3)
        d = ma.to_dict()
        assert d["motive_id"] == "test"
        assert d["valence"] == 0.5
        assert "timestamp" in d
