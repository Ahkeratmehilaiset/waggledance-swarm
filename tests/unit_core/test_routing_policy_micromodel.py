"""Tests for micromodel route type in routing_policy (v1.17.0)."""

import pytest
from unittest.mock import MagicMock

from waggledance.core.orchestration.routing_policy import (
    ALLOWED_ROUTE_TYPES,
    RoutingFeatures,
    select_route,
    extract_features,
)


def _mock_config(**overrides):
    cfg = MagicMock()
    defaults = {"swarm.enabled": False}
    defaults.update(overrides)
    cfg.get = lambda key, default=None: defaults.get(key, default)
    return cfg


class TestMicromodelInRouteTypes:
    def test_micromodel_allowed(self):
        assert "micromodel" in ALLOWED_ROUTE_TYPES

    def test_five_route_types(self):
        assert len(ALLOWED_ROUTE_TYPES) == 5


class TestRoutingFeaturesMicromodel:
    def test_default_micromodel_disabled(self):
        f = RoutingFeatures()
        assert not f.has_micromodel_hit
        assert f.micromodel_confidence == 0.0
        assert not f.micromodel_enabled

    def test_micromodel_fields_settable(self):
        f = RoutingFeatures(
            has_micromodel_hit=True,
            micromodel_confidence=0.92,
            micromodel_enabled=True,
        )
        assert f.has_micromodel_hit
        assert f.micromodel_confidence == 0.92
        assert f.micromodel_enabled


class TestSelectRouteMicromodel:
    def test_micromodel_route_when_enabled_and_confident(self):
        features = RoutingFeatures(
            micromodel_enabled=True,
            has_micromodel_hit=True,
            micromodel_confidence=0.9,
        )
        route = select_route(features, _mock_config())
        assert route.route_type == "micromodel"
        assert route.confidence == 0.9

    def test_micromodel_skipped_when_disabled(self):
        features = RoutingFeatures(
            micromodel_enabled=False,
            has_micromodel_hit=True,
            micromodel_confidence=0.95,
        )
        route = select_route(features, _mock_config())
        assert route.route_type != "micromodel"

    def test_micromodel_skipped_when_no_hit(self):
        features = RoutingFeatures(
            micromodel_enabled=True,
            has_micromodel_hit=False,
            micromodel_confidence=0.95,
        )
        route = select_route(features, _mock_config())
        assert route.route_type != "micromodel"

    def test_micromodel_skipped_when_low_confidence(self):
        features = RoutingFeatures(
            micromodel_enabled=True,
            has_micromodel_hit=True,
            micromodel_confidence=0.80,  # below 0.85 threshold
        )
        route = select_route(features, _mock_config())
        assert route.route_type != "micromodel"

    def test_hotcache_beats_micromodel(self):
        features = RoutingFeatures(
            has_hot_cache_hit=True,
            micromodel_enabled=True,
            has_micromodel_hit=True,
            micromodel_confidence=0.95,
        )
        route = select_route(features, _mock_config())
        assert route.route_type == "hotcache"

    def test_micromodel_beats_memory(self):
        features = RoutingFeatures(
            micromodel_enabled=True,
            has_micromodel_hit=True,
            micromodel_confidence=0.90,
            memory_score=0.85,
        )
        route = select_route(features, _mock_config())
        assert route.route_type == "micromodel"

    def test_boundary_confidence_085(self):
        features = RoutingFeatures(
            micromodel_enabled=True,
            has_micromodel_hit=True,
            micromodel_confidence=0.85,  # exact boundary — NOT > 0.85
        )
        route = select_route(features, _mock_config())
        # 0.85 is NOT > 0.85, so micromodel should be skipped
        assert route.route_type != "micromodel"

    def test_confidence_above_boundary(self):
        features = RoutingFeatures(
            micromodel_enabled=True,
            has_micromodel_hit=True,
            micromodel_confidence=0.851,
        )
        route = select_route(features, _mock_config())
        assert route.route_type == "micromodel"
