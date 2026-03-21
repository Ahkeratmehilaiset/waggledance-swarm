"""
Tests for solver-first routing edge cases found during 10h production run.

Problem: 91% of queries (246/271) went to LLM, only 9% got gold grade.
Root cause: ChatService routing_policy had no solver route — all queries
fell to LLM with confidence=0.6.

Fixes validated:
1. classify_intent: arithmetic/thermal/stats patterns detected correctly
2. routing_policy: solver route added, time/system queries override solver
3. ChatService: MathSolver/ThermalSolver invoked before LLM fallback
4. MathSolver: percentage-of, squared, cubed patterns added
"""

import pytest
from waggledance.core.reasoning.solver_router import SolverRouter


# ── classify_intent edge cases ──────────────────────────────


class TestMathIntentEdgeCases:
    """Arithmetic queries must route to math, not retrieval/chat."""

    @pytest.mark.parametrize("query", [
        "what is 15% of 300",
        "paljonko on 15% sadasta",
        "what is 12 squared",
        "5 cubed",
        "what is 2+2?",
        "calculate 500 / 7",
        "laske 150 * 0.08",
    ])
    def test_math_intent(self, query):
        assert SolverRouter.classify_intent(query) == "math"

    def test_percent_without_number_is_not_math(self):
        """'what percentage of users' has no second number → not math."""
        intent = SolverRouter.classify_intent("what percentage of users are active")
        assert intent != "math"


class TestThermalIntentEdgeCases:
    """Thermal queries must not be hijacked by math or other intents."""

    @pytest.mark.parametrize("query,expected", [
        ("frost risk at -5C tonight", "thermal"),
        ("convert 72F to celsius", "thermal"),
        ("is 45 degrees too hot?", "thermal"),
        ("onko pakkasvaara", "thermal"),
        ("temperature schedule for brood box", "thermal"),
        ("heating system status", "thermal"),
    ])
    def test_thermal_intent(self, query, expected):
        assert SolverRouter.classify_intent(query) == expected

    def test_minus_in_temperature_not_math(self):
        """'-5C' contains '-' but is thermal, not math."""
        assert SolverRouter.classify_intent("frost risk at -5C tonight") == "thermal"

    def test_optimize_heating_not_thermal(self):
        """Optimization verb overrides thermal signal."""
        assert SolverRouter.classify_intent("optimize heating schedule") == "optimization"


class TestStatsIntentEdgeCases:
    """Time-series queries must route to stats, not thermal/math/chat."""

    @pytest.mark.parametrize("query", [
        "average temperature last 7 days",
        "energy cost this month",
        "compare this week to last week",
        "heating cost today",
    ])
    def test_timeseries_stats(self, query):
        assert SolverRouter.classify_intent(query) == "stats"

    @pytest.mark.parametrize("query", [
        "what is the trend",
        "show me the median value",
    ])
    def test_stats_without_time_window(self, query):
        assert SolverRouter.classify_intent(query) == "stats"


class TestLLMQueriesUnchanged:
    """Genuinely LLM queries must NOT be reclassified as solver."""

    @pytest.mark.parametrize("query,expected", [
        ("what should I do today", "chat"),
        ("morning report", "chat"),
        ("hello world", "chat"),
        ("show me recent alerts", "chat"),
        ("what happened yesterday", "chat"),
    ])
    def test_llm_queries_unchanged(self, query, expected):
        assert SolverRouter.classify_intent(query) == expected


# ── Routing policy integration ──────────────────────────────


class TestRoutingPolicySolverRoute:
    """Solver-eligible queries get 'solver' route with high confidence."""

    @pytest.fixture
    def config(self):
        class FakeConfig:
            def get(self, key, default=None):
                return default
        return FakeConfig()

    def _route(self, query, config):
        from waggledance.core.orchestration.routing_policy import (
            extract_features, select_route,
        )
        f = extract_features(query, hot_cache_hit=False, memory_score=0.0,
                            matched_keywords=[], profile="HOME")
        return select_route(f, config), f

    def test_math_routes_to_solver(self, config):
        route, f = self._route("what is 15% of 300", config)
        assert route.route_type == "solver"
        assert route.confidence == 0.95
        assert f.solver_intent == "math"

    def test_thermal_routes_to_solver(self, config):
        route, _ = self._route("frost risk at -5C tonight", config)
        assert route.route_type == "solver"

    def test_stats_routes_to_solver(self, config):
        route, _ = self._route("average temperature last 7 days", config)
        assert route.route_type == "solver"

    def test_chat_routes_to_llm(self, config):
        route, _ = self._route("morning report", config)
        assert route.route_type == "llm"
        assert route.confidence == 0.6

    def test_time_query_overrides_solver(self, config):
        """'Paljonko kello on nyt?' has 'paljonko' (math) but is a time query."""
        route, f = self._route("Paljonko kello on nyt?", config)
        assert route.route_type == "llm"
        assert route.confidence == 0.8

    def test_system_query_overrides_solver(self, config):
        route, _ = self._route("system health status", config)
        assert route.route_type == "llm"


# ── Solver execution ────────────────────────────────────────


class TestSolverExecution:
    """Verify solvers produce correct answers."""

    def _solve(self, query, intent):
        from waggledance.application.services.chat_service import ChatService
        return ChatService._try_solver(query, intent)

    def test_percentage_of(self):
        assert self._solve("what is 15% of 300", "math") == "45"

    def test_percentage_fi(self):
        assert self._solve("paljonko on 15% sadasta", "math") == "15"

    def test_squared(self):
        assert self._solve("what is 12 squared", "math") == "144"

    def test_division(self):
        r = self._solve("calculate 500 / 7", "math")
        assert r is not None
        assert abs(float(r) - 71.4286) < 0.01

    def test_conversion_f_to_c(self):
        r = self._solve("convert 72F to celsius", "thermal")
        assert r is not None and "22.2" in r

    def test_frost_risk(self):
        r = self._solve("frost risk at -5C tonight", "thermal")
        assert r is not None and "Frost risk" in r

    def test_temperature_threshold(self):
        r = self._solve("is 45 degrees too hot?", "thermal")
        assert r is not None and "too hot" in r

    def test_stats_returns_none(self):
        """Stats queries need context — solver returns None, falls to LLM."""
        assert self._solve("energy cost this month", "stats") is None

    def test_chat_returns_none(self):
        assert self._solve("what should I do today", "chat") is None
