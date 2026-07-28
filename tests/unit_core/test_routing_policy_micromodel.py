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


class _HostileConfigValue:
    def __bool__(self):
        raise AssertionError("truthiness must not be evaluated")

    def __eq__(self, _other):
        raise AssertionError("equality must not be evaluated")


class TestMicromodelInRouteTypes:
    def test_micromodel_allowed(self):
        assert "micromodel" in ALLOWED_ROUTE_TYPES

    def test_six_route_types(self):
        assert len(ALLOWED_ROUTE_TYPES) == 6
        assert "solver" in ALLOWED_ROUTE_TYPES


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

    @pytest.mark.parametrize(
        ("query", "expected_intent"),
        [
            ("calculate 2+2", "math"),
            ("what is 15% of 300", "math"),
        ],
    )
    def test_deterministic_solver_beats_confident_micromodel(
        self,
        query,
        expected_intent,
    ):
        features = extract_features(
            query=query,
            hot_cache_hit=False,
            memory_score=0.0,
            matched_keywords=[],
            profile="HOME",
            language="en",
            micromodel_enabled=True,
            micromodel_hit=True,
            micromodel_confidence=0.99,
        )

        route = select_route(features, _mock_config())

        assert features.solver_intent == expected_intent
        assert route.route_type == "solver"
        assert route.confidence == 0.95

    def test_explicit_registry_solver_beats_confident_micromodel(self):
        features = RoutingFeatures(
            solver_intent="v3_13_0_solver",
            micromodel_enabled=True,
            has_micromodel_hit=True,
            micromodel_confidence=0.99,
        )

        route = select_route(features, _mock_config())

        assert route.route_type == "solver"
        assert route.confidence == 0.95

    def test_explicit_solver_first_true_beats_confident_micromodel(self):
        features = RoutingFeatures(
            solver_intent="math",
            micromodel_enabled=True,
            has_micromodel_hit=True,
            micromodel_confidence=0.99,
        )

        route = select_route(
            features,
            _mock_config(
                **{"routing.deterministic_solver_first_enabled": True}
            ),
        )

        assert route.route_type == "solver"

    def test_explicit_solver_first_false_restores_micromodel_priority(self):
        features = RoutingFeatures(
            solver_intent="math",
            micromodel_enabled=True,
            has_micromodel_hit=True,
            micromodel_confidence=0.99,
        )

        route = select_route(
            features,
            _mock_config(
                **{"routing.deterministic_solver_first_enabled": False}
            ),
        )

        assert route.route_type == "micromodel"

    @pytest.mark.parametrize(
        "value",
        [
            None,
            0,
            1,
            0.0,
            1.0,
            "false",
            "true",
            [],
            {},
            _HostileConfigValue(),
        ],
    )
    def test_malformed_solver_first_values_keep_safe_default(self, value):
        features = RoutingFeatures(
            solver_intent="math",
            micromodel_enabled=True,
            has_micromodel_hit=True,
            micromodel_confidence=0.99,
        )

        route = select_route(
            features,
            _mock_config(
                **{"routing.deterministic_solver_first_enabled": value}
            ),
        )

        assert route.route_type == "solver"

    def test_solver_first_false_changes_priority_not_solver_availability(self):
        features = RoutingFeatures(
            solver_intent="math",
            micromodel_enabled=True,
            has_micromodel_hit=False,
            micromodel_confidence=0.99,
        )

        route = select_route(
            features,
            _mock_config(
                **{"routing.deterministic_solver_first_enabled": False}
            ),
        )

        assert route.route_type == "solver"

    def test_explicit_registry_solver_is_not_affected_by_precedence_rollback(self):
        features = RoutingFeatures(
            solver_intent="v3_13_0_solver",
            micromodel_enabled=True,
            has_micromodel_hit=True,
            micromodel_confidence=0.99,
        )

        route = select_route(
            features,
            _mock_config(
                **{"routing.deterministic_solver_first_enabled": False}
            ),
        )

        assert route.route_type == "solver"

    @pytest.mark.parametrize(
        ("features", "expected_route"),
        [
            (
                RoutingFeatures(
                    has_hot_cache_hit=True,
                    solver_intent="math",
                    micromodel_enabled=True,
                    has_micromodel_hit=True,
                    micromodel_confidence=0.99,
                ),
                "hotcache",
            ),
            (
                RoutingFeatures(
                    is_time_query=True,
                    solver_intent="math",
                ),
                "llm",
            ),
            (
                RoutingFeatures(
                    is_system_query=True,
                    solver_intent="math",
                ),
                "llm",
            ),
        ],
    )
    def test_precedence_rollback_preserves_unrelated_routing_invariants(
        self,
        features,
        expected_route,
    ):
        route = select_route(
            features,
            _mock_config(
                **{"routing.deterministic_solver_first_enabled": False}
            ),
        )

        assert route.route_type == expected_route

    @pytest.mark.parametrize(
        "confidence",
        [
            float("nan"),
            float("inf"),
            float("-inf"),
            -0.001,
            1.000001,
            10**309,
            -(10**309),
            True,
        ],
    )
    def test_micromodel_rejects_nonfinite_or_out_of_range_confidence(
        self,
        confidence,
    ):
        features = RoutingFeatures(
            micromodel_enabled=True,
            has_micromodel_hit=True,
            micromodel_confidence=confidence,
        )

        route = select_route(features, _mock_config())

        assert route.route_type == "llm"
        assert route.confidence == 0.6

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
