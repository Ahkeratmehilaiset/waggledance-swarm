"""Forward game-theory contract and verifier coverage."""
from __future__ import annotations

from dataclasses import replace
import json
from fractions import Fraction

import pytest

from waggledance.core import game_theory_verifier
from waggledance.core.game_theory_contracts import (
    FiniteGame,
    ForwardGameRequest,
    GameTheoryValidationError,
    MAX_ACTIONS_PER_PLAYER,
    MAX_JOINT_PROFILES,
    MAX_PLAYERS,
    MixedStrategy,
    PayoffEntry,
    Rational,
    make_payoffs,
)
from waggledance.core.game_theory_service import BoundedGameTheoryService
from waggledance.core.game_theory_verifier import (
    verify_zero_sum_mixed_strategy,
)


def _matching_pennies(*, structure: str = "zero_sum") -> FiniteGame:
    return FiniteGame(
        players=("row", "column"),
        actions=(("heads", "tails"), ("heads", "tails")),
        payoffs=make_payoffs((
            (("heads", "heads"), (1, -1)),
            (("heads", "tails"), (-1, 1)),
            (("tails", "heads"), (-1, 1)),
            (("tails", "tails"), (1, -1)),
        )),
        structure=structure,
    )


def _sentinel_after(values):
    yield from values
    raise AssertionError("bounded materializer over-read its input")


def _prisoners_dilemma() -> FiniteGame:
    return FiniteGame(
        players=("alice", "bob"),
        actions=(("cooperate", "defect"), ("cooperate", "defect")),
        payoffs=make_payoffs((
            (("cooperate", "cooperate"), (3, 3)),
            (("cooperate", "defect"), (0, 5)),
            (("defect", "cooperate"), (5, 0)),
            (("defect", "defect"), (1, 1)),
        )),
    )


def test_payoff_order_is_canonical_and_digest_is_deterministic() -> None:
    original = _matching_pennies()
    reordered = FiniteGame(
        players=original.players,
        actions=original.actions,
        payoffs=tuple(reversed(original.payoffs)),
        structure="zero_sum",
    )

    assert reordered.payoffs == original.payoffs
    assert reordered.digest == original.digest
    assert reordered.canonical_bytes() == original.canonical_bytes()


def test_contract_rejects_incomplete_duplicate_and_non_zero_sum_tables() -> None:
    valid = _matching_pennies()
    with pytest.raises(GameTheoryValidationError, match="complete"):
        FiniteGame(
            players=valid.players,
            actions=valid.actions,
            payoffs=valid.payoffs[:-1],
            structure="zero_sum",
        )
    with pytest.raises(GameTheoryValidationError, match="duplicate"):
        FiniteGame(
            players=valid.players,
            actions=valid.actions,
            payoffs=valid.payoffs + (valid.payoffs[0],),
            structure="zero_sum",
        )
    forged = list(valid.payoffs)
    forged[0] = PayoffEntry(forged[0].profile, (1, 1))
    with pytest.raises(GameTheoryValidationError, match="zero_sum"):
        FiniteGame(
            players=valid.players,
            actions=valid.actions,
            payoffs=tuple(forged),
            structure="zero_sum",
        )


@pytest.mark.parametrize(
    "bad_value",
    [True, 1.5, 1_000_001],
)
def test_contract_rejects_non_integer_or_unbounded_utilities(bad_value) -> None:
    with pytest.raises(GameTheoryValidationError):
        PayoffEntry(("a", "b"), (bad_value, 0))


def test_rational_contract_rejects_zero_denominator_and_oversized_components() -> None:
    with pytest.raises(GameTheoryValidationError, match="non-zero"):
        Rational(1, 0)
    with pytest.raises(GameTheoryValidationError, match="component"):
        Rational(10**40, 1)


def test_exact_pure_nash_enumeration_finds_dominant_strategy_profile() -> None:
    result = BoundedGameTheoryService().solve_forward(
        ForwardGameRequest(_prisoners_dilemma(), concept="pure_nash")
    )

    assert result.status == "exact_verified"
    assert result.verifier_status == "verified"
    assert tuple(item.profile for item in result.pure_equilibria) == (
        ("defect", "defect"),
    )
    assert result.mixed_strategies == ()
    assert result.advisory_only is True
    assert result.runtime_authority_granted is False
    assert result.external_writes_applied is False


def test_pure_enumeration_builds_payoff_index_once(monkeypatch) -> None:
    calls = 0
    original = game_theory_verifier.payoff_map

    def counted(game):
        nonlocal calls
        calls += 1
        return original(game)

    monkeypatch.setattr(game_theory_verifier, "payoff_map", counted)

    game_theory_verifier.enumerate_pure_nash(_prisoners_dilemma())

    assert calls == 1


def test_matching_pennies_returns_verified_bounded_mixed_advice() -> None:
    game = _matching_pennies()
    request = ForwardGameRequest(
        game,
        concept="zero_sum_fictitious_play",
        max_iterations=20_000,
        epsilon=Rational(1, 100),
    )

    result = BoundedGameTheoryService().solve_forward(request)
    verification = verify_zero_sum_mixed_strategy(game, result.mixed_strategies)

    assert result.status == "epsilon_verified"
    assert result.iterations <= request.max_iterations
    assert result.pure_equilibria == ()
    assert verification.exploitability == result.exploitability
    assert result.exploitability is not None
    assert result.exploitability.as_fraction() <= Fraction(1, 100)
    assert result.value_lower is not None
    assert result.value_upper is not None
    assert result.value_lower.as_fraction() <= 0 <= result.value_upper.as_fraction()


def test_tiny_budget_returns_verified_partial_evidence_not_a_guessed_pass() -> None:
    result = BoundedGameTheoryService().solve_forward(ForwardGameRequest(
        _matching_pennies(),
        concept="zero_sum_fictitious_play",
        max_iterations=1,
        epsilon=Rational(0),
    ))

    assert result.status == "budget_exhausted"
    assert result.abstain_reason == "epsilon_not_reached"
    assert result.verifier_status == "verified"
    assert result.exploitability is not None
    assert result.exploitability.as_fraction() > 0


def test_general_sum_without_pure_equilibrium_abstains_from_mixed_claim() -> None:
    result = BoundedGameTheoryService().solve_forward(ForwardGameRequest(
        _matching_pennies(structure="general_sum"),
        concept="auto",
    ))

    assert result.status == "unsupported"
    assert result.abstain_reason == "mixed_general_sum_not_supported_in_v1"
    assert result.mixed_strategies == ()


def test_repeated_forward_calls_are_byte_deterministic() -> None:
    request = ForwardGameRequest(
        _matching_pennies(),
        max_iterations=500,
        epsilon=Rational(0),
    )
    service = BoundedGameTheoryService()

    first = json.dumps(service.solve_forward(request).to_mapping(), sort_keys=True)
    second = json.dumps(service.solve_forward(request).to_mapping(), sort_keys=True)

    assert first == second


def test_independent_verifier_rejects_forged_probabilities() -> None:
    game = _matching_pennies()
    forged = (
        MixedStrategy("row", (
            ("heads", Rational(3, 4)),
            ("tails", Rational(3, 4)),
        )),
        MixedStrategy("column", (
            ("heads", Rational(1, 2)),
            ("tails", Rational(1, 2)),
        )),
    )

    with pytest.raises(GameTheoryValidationError, match="sum to one"):
        verify_zero_sum_mixed_strategy(game, forged)


def test_magma_summary_is_path_free_and_omits_raw_game_content() -> None:
    game = _matching_pennies()
    request = ForwardGameRequest(
        game,
        concept="pure_nash",
    )
    result = BoundedGameTheoryService().solve_forward(request)
    encoded = json.dumps(result.magma_summary(request), sort_keys=True)

    for raw_value in (*game.players, *game.actions[0], *game.actions[1]):
        assert raw_value not in encoded
    assert "payoffs" not in encoded
    assert "utilities" not in encoded
    assert "C:\\" not in encoded
    assert "U:\\" not in encoded
    assert '"runtime_authority_granted": false' in encoded
    assert '"external_writes_applied": false' in encoded


def test_magma_summary_rejects_a_different_forward_request() -> None:
    game = _matching_pennies()
    request = ForwardGameRequest(game, concept="pure_nash", max_iterations=10)
    result = BoundedGameTheoryService().solve_forward(request)
    altered = ForwardGameRequest(game, concept="pure_nash", max_iterations=11)

    with pytest.raises(GameTheoryValidationError, match="digest mismatch"):
        result.magma_summary(altered)


def test_magma_summary_rejects_forged_verified_forward_result() -> None:
    game = _matching_pennies()
    request = ForwardGameRequest(
        game,
        concept="zero_sum_fictitious_play",
        max_iterations=10,
    )
    result = BoundedGameTheoryService().solve_forward(request)
    forged_strategy = MixedStrategy(
        player_id=game.players[0],
        probabilities=(
            (game.actions[0][0], Rational(2)),
            (game.actions[0][1], Rational(-1)),
        ),
    )
    forged = replace(
        result,
        mixed_strategies=(forged_strategy, result.mixed_strategies[1]),
        verifier_status="verified",
    )

    with pytest.raises(GameTheoryValidationError, match="verification failed"):
        forged.magma_summary(request)


def test_magma_summary_rejects_mutable_forward_result_collections() -> None:
    request = ForwardGameRequest(_matching_pennies(), concept="pure_nash")
    result = BoundedGameTheoryService().solve_forward(request)
    forged = replace(result, pure_equilibria=list(result.pure_equilibria))

    with pytest.raises(GameTheoryValidationError, match="verification failed"):
        forged.magma_summary(request)


def test_finite_game_rejects_players_without_overreading() -> None:
    players = tuple(f"p{index}" for index in range(MAX_PLAYERS + 1))

    with pytest.raises(GameTheoryValidationError, match="players exceeds bound"):
        FiniteGame(
            players=_sentinel_after(players),
            actions=(),
            payoffs=(),
        )


def test_finite_game_rejects_actions_without_overreading() -> None:
    actions = tuple(
        f"a{index}" for index in range(MAX_ACTIONS_PER_PLAYER + 1)
    )

    with pytest.raises(GameTheoryValidationError, match=r"actions\[0\] exceeds"):
        FiniteGame(
            players=("p1", "p2"),
            actions=(_sentinel_after(actions), ("a",)),
            payoffs=(),
        )


def test_finite_game_rejects_payoffs_without_overreading() -> None:
    entry = PayoffEntry(("a", "a"), (0, 0))

    with pytest.raises(GameTheoryValidationError, match="payoffs exceeds bound"):
        FiniteGame(
            players=("p1", "p2"),
            actions=(("a",), ("a",)),
            payoffs=_sentinel_after((entry,) * (MAX_JOINT_PROFILES + 1)),
        )
