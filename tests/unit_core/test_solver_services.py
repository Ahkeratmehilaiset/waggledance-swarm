"""Shared opt-in service hub coverage for current and generated solvers."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from waggledance.core.game_theory_contracts import (
    DecisionObservation,
    FiniteGame,
    ForwardGameRequest,
    GameHypothesis,
    InverseGameRequest,
    make_payoffs,
)
from waggledance.core.ports.game_theory_port import GameTheoryPort
from waggledance.core.reasoning.solver_services import (
    SolverServiceUnavailable,
    SolverServices,
    default_solver_services,
    invoke_solver_callable,
    solver_services_opt_in,
)


def _typed_port(port: GameTheoryPort) -> GameTheoryPort:
    return port


def _coordination_game() -> FiniteGame:
    return FiniteGame(
        players=("left", "right"),
        actions=(("A", "B"), ("A", "B")),
        payoffs=make_payoffs((
            (("A", "A"), (2, 2)),
            (("A", "B"), (0, 0)),
            (("B", "A"), (0, 0)),
            (("B", "B"), (1, 1)),
        )),
    )


def test_service_hub_defaults_off_and_fails_explicitly() -> None:
    services = SolverServices()

    assert services.has_game_theory is False
    with pytest.raises(SolverServiceUnavailable, match="not injected"):
        services.require_game_theory()


def test_default_service_hub_is_pure_cached_and_advisory_only() -> None:
    first = default_solver_services()
    second = default_solver_services()

    assert first is second
    assert first.has_game_theory is True
    port = _typed_port(first.require_game_theory())
    assert not hasattr(port, "execute_action")
    assert not hasattr(port, "apply_external_write")
    with pytest.raises(AttributeError):
        port.solve_forward = lambda _request: None


def test_unrelated_solver_types_can_opt_into_the_same_forward_port() -> None:
    @dataclass
    class TacticalSolver:
        services: SolverServices

        def solve(self, game: FiniteGame):
            return self.services.require_game_theory().solve_forward(
                ForwardGameRequest(game, concept="pure_nash")
            )

    class GeneratedSolver:
        def __init__(self, services: SolverServices) -> None:
            self.services = services

        def evaluate(self, game: FiniteGame):
            request = ForwardGameRequest(game, concept="pure_nash")
            return self.services.require_game_theory().solve_forward(request)

    services = default_solver_services()
    tactical = TacticalSolver(services).solve(_coordination_game())
    generated = GeneratedSolver(services).evaluate(_coordination_game())

    assert tactical.to_mapping() == generated.to_mapping()
    assert tactical.advisory_only is True
    assert {item.profile for item in tactical.pure_equilibria} == {
        ("A", "A"),
        ("B", "B"),
    }


def test_solver_can_opt_into_inverse_reasoning_through_same_hub() -> None:
    game = _coordination_game()

    class OpponentModelSolver:
        def __init__(self, services: SolverServices) -> None:
            self.services = services

        def infer(self):
            return self.services.require_game_theory().infer_inverse(
                InverseGameRequest(
                    hypotheses=(GameHypothesis("candidate", game),),
                    observations=(DecisionObservation(
                        "left",
                        ("A", "A"),
                        "opponents_observed_before_choice",
                    ),),
                )
            )

    result = OpponentModelSolver(default_solver_services()).infer()

    assert result.identifiability == "catalog_identified"
    assert result.identification_scope == (
        "finite_supplied_catalog_with_observed_opponents"
    )
    assert result.runtime_authority_granted is False
    assert result.external_writes_applied is False


def test_runtime_injects_services_only_into_explicitly_marked_callable() -> None:
    @solver_services_opt_in
    def marked(payload, *, solver_services):
        return payload, solver_services

    def unmarked(payload, *, solver_services=None):
        return payload, solver_services

    services = SolverServices(game_theory=object())

    assert invoke_solver_callable(
        marked,
        "payload",
        injected_services=services,
    ) == ("payload", services)
    assert invoke_solver_callable(
        unmarked,
        "payload",
        injected_services=services,
    ) == ("payload", None)
    with pytest.raises(ValueError, match="reserved"):
        invoke_solver_callable(
            marked,
            "payload",
            solver_services="forged-payload-service",
        )


def test_opt_in_marker_supports_bound_solver_methods() -> None:
    class Solver:
        @solver_services_opt_in
        def solve(self, payload, *, solver_services):
            return payload, solver_services.has_game_theory

    assert invoke_solver_callable(Solver().solve, "x") == ("x", True)


def test_opt_in_decorator_requires_keyword_only_service_parameter() -> None:
    with pytest.raises(TypeError, match="keyword-only"):
        solver_services_opt_in(lambda payload: payload)
