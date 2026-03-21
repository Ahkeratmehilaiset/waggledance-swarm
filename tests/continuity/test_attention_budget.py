# SPDX-License-Identifier: Apache-2.0
"""Tests for attention budget — resource-aware allocation (v3.2)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from waggledance.core.autonomy.attention_budget import (
    AttentionBudget, AttentionAllocation,
)


class TestBaseAllocation:
    def test_defaults_sum_to_100(self):
        budget = AttentionBudget()
        alloc = budget.current
        assert alloc.total == 100

    def test_default_percentages(self):
        budget = AttentionBudget()
        alloc = budget.current
        assert alloc.critical == 40
        assert alloc.normal == 35
        assert alloc.background == 15
        assert alloc.reflection == 10

    def test_custom_allocation(self):
        budget = AttentionBudget(critical_pct=50, normal_pct=30,
                                 background_pct=10, reflection_pct=10)
        assert budget.current.critical == 50
        assert budget.current.total == 100

    def test_from_config(self):
        config = {"attention": {
            "critical_pct": 45, "normal_pct": 30,
            "background_pct": 15, "reflection_pct": 10,
            "high_load_threshold": 0.85,
            "emergency_load_threshold": 0.98,
            "reflection_min_pct": 3,
        }}
        budget = AttentionBudget.from_config(config)
        assert budget.current.critical == 45
        assert budget._reflection_min == 3

    def test_query_returns_dict(self):
        budget = AttentionBudget()
        d = budget.query()
        assert isinstance(d, dict)
        assert "critical" in d
        assert "reason" in d


class TestLoadReallocation:
    def test_no_load_keeps_base(self):
        budget = AttentionBudget()
        alloc = budget.reallocate(load_factor=0.3)
        assert alloc.critical == 40
        assert alloc.normal == 35
        assert alloc.background == 15
        assert alloc.reflection == 10
        assert alloc.total == 100

    def test_high_load_reduces_background_and_reflection(self):
        budget = AttentionBudget()
        alloc = budget.reallocate(load_factor=0.85)
        assert alloc.reflection <= 10
        assert alloc.background <= 15
        assert alloc.critical == 40
        assert alloc.total == 100
        assert "high load" in alloc.reason

    def test_emergency_load_sheds_background(self):
        budget = AttentionBudget()
        alloc = budget.reallocate(load_factor=0.96)
        assert alloc.background == 0
        assert alloc.reflection == 2  # min
        assert alloc.critical == 40
        assert alloc.total == 100
        assert "emergency" in alloc.reason

    def test_critical_never_starves(self):
        """Critical allocation never goes below base."""
        budget = AttentionBudget()
        for load in [0.0, 0.5, 0.8, 0.9, 0.95, 0.99, 1.0]:
            alloc = budget.reallocate(load_factor=load)
            assert alloc.critical >= 40, f"Critical starved at load={load}"

    def test_reflection_never_zero(self):
        """Reflection never falls to zero — hard minimum enforced."""
        budget = AttentionBudget()
        for load in [0.0, 0.5, 0.8, 0.9, 0.95, 0.99, 1.0]:
            alloc = budget.reallocate(load_factor=load)
            assert alloc.reflection >= 2, f"Reflection zero at load={load}"


class TestUncertaintyShift:
    def test_high_uncertainty_shifts_to_reflection(self):
        budget = AttentionBudget()
        alloc = budget.reallocate(load_factor=0.3, uncertainty_factor=0.7)
        assert alloc.reflection > 10  # base is 10, should get +5
        assert alloc.total == 100
        assert "uncertainty" in alloc.reason

    def test_low_uncertainty_no_shift(self):
        budget = AttentionBudget()
        alloc = budget.reallocate(load_factor=0.3, uncertainty_factor=0.3)
        assert alloc.reflection == 10  # unchanged
        assert alloc.total == 100

    def test_uncertainty_shift_respects_background_minimum(self):
        """Can't shift more from background than background has."""
        budget = AttentionBudget()
        # Emergency load sets background=0, uncertainty shift can't happen
        alloc = budget.reallocate(load_factor=0.96, uncertainty_factor=0.8)
        assert alloc.background == 0
        assert alloc.total == 100

    def test_combined_high_load_and_uncertainty(self):
        budget = AttentionBudget()
        alloc = budget.reallocate(load_factor=0.85, uncertainty_factor=0.7)
        # High load: background=5, reflection=5
        # Uncertainty: shift 5 from background→reflection → background=0, reflection=10
        assert alloc.reflection >= 5
        assert alloc.total == 100


class TestContinuityFloor:
    def test_floor_held_at_base(self):
        budget = AttentionBudget()
        assert budget.continuity_floor_held() is True

    def test_floor_held_under_load(self):
        budget = AttentionBudget()
        budget.reallocate(load_factor=0.96)
        assert budget.continuity_floor_held() is True

    def test_floor_held_with_uncertainty(self):
        budget = AttentionBudget()
        budget.reallocate(load_factor=0.85, uncertainty_factor=0.8)
        assert budget.continuity_floor_held() is True


class TestAllocationAlwaysValid:
    """Allocation must always sum to 100 and have no negative values."""

    @pytest.mark.parametrize("load,unc", [
        (0.0, 0.0), (0.5, 0.0), (0.8, 0.0), (0.95, 0.0), (1.0, 0.0),
        (0.0, 0.7), (0.5, 0.7), (0.8, 0.7), (0.95, 0.7), (1.0, 0.7),
        (0.0, 1.0), (0.85, 0.9), (0.99, 0.99),
    ])
    def test_always_sums_to_100(self, load, unc):
        budget = AttentionBudget()
        alloc = budget.reallocate(load_factor=load, uncertainty_factor=unc)
        assert alloc.total == 100, f"Sum={alloc.total} at load={load}, unc={unc}"
        assert alloc.critical >= 0
        assert alloc.normal >= 0
        assert alloc.background >= 0
        assert alloc.reflection >= 0
