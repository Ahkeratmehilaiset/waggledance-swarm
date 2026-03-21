"""Tests for routing_policy — pure routing decisions."""

import pytest
from unittest.mock import MagicMock

from waggledance.core.domain.task import TaskRoute
from waggledance.core.orchestration.routing_policy import (
    ALLOWED_ROUTE_TYPES,
    RoutingFeatures,
    extract_features,
    select_route,
)


@pytest.fixture
def config():
    c = MagicMock()
    c.get.side_effect = lambda key, default=None: {
        "swarm.enabled": True,
    }.get(key, default)
    return c


class TestSelectRoute:
    def test_hot_cache_hit_returns_hotcache(self, config):
        f = RoutingFeatures(has_hot_cache_hit=True)
        route = select_route(f, config)
        assert route.route_type == "hotcache"
        assert route.confidence == 1.0

    def test_high_memory_score_returns_memory(self, config):
        f = RoutingFeatures(memory_score=0.85)
        route = select_route(f, config)
        assert route.route_type == "memory"

    def test_time_query_returns_llm(self, config):
        f = RoutingFeatures(is_time_query=True)
        route = select_route(f, config)
        assert route.route_type == "llm"

    def test_system_query_returns_llm(self, config):
        f = RoutingFeatures(is_system_query=True)
        route = select_route(f, config)
        assert route.route_type == "llm"

    def test_complex_query_routes_to_swarm(self, config):
        f = RoutingFeatures(
            query_length=80,
            matched_keywords=["varroa", "treatment", "spring"],
        )
        route = select_route(f, config)
        assert route.route_type == "swarm"

    def test_default_fallback_is_llm(self, config):
        f = RoutingFeatures()
        route = select_route(f, config)
        assert route.route_type == "llm"

    def test_return_type_is_task_route(self, config):
        f = RoutingFeatures()
        route = select_route(f, config)
        assert isinstance(route, TaskRoute)

    def test_route_type_always_in_allowed(self, config):
        features_list = [
            RoutingFeatures(has_hot_cache_hit=True),
            RoutingFeatures(memory_score=0.9),
            RoutingFeatures(is_time_query=True),
            RoutingFeatures(),
            RoutingFeatures(query_length=100, matched_keywords=["a", "b", "c"]),
        ]
        for f in features_list:
            route = select_route(f, config)
            assert route.route_type in ALLOWED_ROUTE_TYPES


class TestExtractFeatures:
    def test_detects_time_query(self):
        f = extract_features("What time is it now?", False, 0.0, [], "COTTAGE")
        assert f.is_time_query is True

    def test_detects_system_query(self):
        f = extract_features("Show me the status", False, 0.0, [], "COTTAGE")
        assert f.is_system_query is True

    def test_query_length(self):
        f = extract_features("hello", False, 0.0, [], "COTTAGE")
        assert f.query_length == 5
