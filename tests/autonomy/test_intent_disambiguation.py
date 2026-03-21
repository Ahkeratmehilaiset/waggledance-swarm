"""
Tests for intent disambiguation: schedule → retrieval vs optimization.

Root cause: "varroa schedule" was classified as 'optimization' because
"schedule" was in optim_signals. Now schedule without an active verb
(optimize, minimize, create, etc.) maps to 'retrieval'.

Note: thermal/seasonal checks run BEFORE schedule disambiguation in the
priority chain, so "temperature schedule" → thermal, "spring calendar" → seasonal.
"""

from __future__ import annotations

import pytest
from waggledance.core.reasoning.solver_router import SolverRouter


class TestScheduleDisambiguation:
    """Noun + schedule (no active verb) → retrieval."""

    @pytest.mark.parametrize("query,expected", [
        # Pure schedule queries → retrieval
        ("varroa schedule", "retrieval"),
        ("feeding schedule", "retrieval"),
        ("what is the varroa treatment schedule", "retrieval"),
        ("hoitotoimenpiteiden aikataulu", "retrieval"),
        ("inspection timetable", "retrieval"),
    ])
    def test_retrieval_schedule(self, query, expected):
        assert SolverRouter.classify_intent(query) == expected, (
            f"'{query}' should be {expected}"
        )

    @pytest.mark.parametrize("query,expected", [
        # Schedule + active verb → optimization
        ("optimize feeding schedule", "optimization"),
        ("create a new watering schedule", "optimization"),
        ("schedule optimization for next week", "optimization"),
        ("allocate resources for schedule", "optimization"),
        ("maximize schedule efficiency", "optimization"),
    ])
    def test_optimization_with_verb(self, query, expected):
        result = SolverRouter.classify_intent(query)
        assert result == expected, f"'{query}' should be {expected}, got {result}"

    def test_thermal_takes_priority_over_schedule(self):
        """thermal check runs before schedule → thermal wins."""
        assert SolverRouter.classify_intent("temperature schedule for brood box") == "thermal"

    def test_seasonal_takes_priority_over_schedule(self):
        """seasonal check runs before schedule → seasonal wins."""
        assert SolverRouter.classify_intent("calendar for spring inspections") == "seasonal"

    def test_no_regression_on_pure_optimize(self):
        assert SolverRouter.classify_intent("minimize cost") == "optimization"
        assert SolverRouter.classify_intent("cheapest option") == "optimization"
        assert SolverRouter.classify_intent("allocate budget") == "optimization"
