"""Inverse-game-theory finite-catalog identification coverage."""
from __future__ import annotations

from dataclasses import replace
import json

import pytest

from waggledance.core.game_theory_contracts import (
    DecisionObservation,
    FiniteGame,
    GameHypothesis,
    GameTheoryValidationError,
    InverseGameRequest,
    Rational,
    make_payoffs,
)
from waggledance.core.game_theory_service import BoundedGameTheoryService
from waggledance.core.game_theory_verifier import verify_inverse_result


def _game(*, cooperative: bool, scale: int = 1) -> FiniteGame:
    if cooperative:
        utilities = (
            (("C", "C"), (4, 4)),
            (("C", "D"), (0, 2)),
            (("D", "C"), (2, 0)),
            (("D", "D"), (1, 1)),
        )
    else:
        utilities = (
            (("C", "C"), (1, 1)),
            (("C", "D"), (0, 3)),
            (("D", "C"), (3, 0)),
            (("D", "D"), (2, 2)),
        )
    scaled = tuple(
        (profile, tuple(value * scale for value in payoff))
        for profile, payoff in utilities
    )
    return FiniteGame(
        players=("p1", "p2"),
        actions=(("C", "D"), ("C", "D")),
        payoffs=make_payoffs(scaled),
    )


def _observations() -> tuple[DecisionObservation, ...]:
    return (
        DecisionObservation(
            "p1", ("C", "C"), "opponents_observed_before_choice"
        ),
        DecisionObservation(
            "p2", ("C", "C"), "opponents_observed_before_choice"
        ),
    )


def _rescale_player(
    game: FiniteGame,
    *,
    player_index: int,
    factor: int,
) -> FiniteGame:
    entries = []
    for entry in game.payoffs:
        utilities = list(entry.utilities)
        utilities[player_index] *= factor
        entries.append((entry.profile, tuple(utilities)))
    return FiniteGame(
        players=game.players,
        actions=game.actions,
        payoffs=make_payoffs(entries),
        structure=game.structure,
    )


def test_inverse_catalog_uniquely_identifies_compatible_hypothesis() -> None:
    request = InverseGameRequest(
        hypotheses=(
            GameHypothesis("cooperative", _game(cooperative=True)),
            GameHypothesis("defecting", _game(cooperative=False)),
        ),
        observations=_observations(),
        regret_tolerance=Rational(0),
    )

    result = BoundedGameTheoryService().infer_inverse(request)

    assert result.status == "exact_verified"
    assert result.verifier_status == "verified"
    assert result.identifiability == "catalog_identified"
    assert result.identification_scope == (
        "finite_supplied_catalog_with_observed_opponents"
    )
    assert result.compatible_hypothesis_ids == ("cooperative",)
    assert result.scores[0].hypothesis_id == "cooperative"
    assert result.scores[0].mean_regret == Rational(0)
    assert result.scores[1].max_regret == Rational(2, 3)
    assert "no_hidden_intent_is_asserted" in result.assumptions
    assert result.runtime_authority_granted is False
    assert result.external_writes_applied is False


def test_independent_inverse_verifier_rejects_forged_identification() -> None:
    request = InverseGameRequest(
        hypotheses=(
            GameHypothesis("cooperative", _game(cooperative=True)),
            GameHypothesis("defecting", _game(cooperative=False)),
        ),
        observations=_observations(),
    )
    result = BoundedGameTheoryService().infer_inverse(request)
    forged = replace(
        result,
        identifiability="not_identified",
        compatible_hypothesis_ids=("cooperative", "defecting"),
    )

    assert verify_inverse_result(request, result) is True
    assert verify_inverse_result(request, forged) is False


def test_observationally_equivalent_catalog_is_explicitly_not_identified() -> None:
    same_game = _game(cooperative=True)
    request = InverseGameRequest(
        hypotheses=(
            GameHypothesis("model_a", same_game),
            GameHypothesis("model_b", same_game),
        ),
        observations=_observations(),
    )

    result = BoundedGameTheoryService().infer_inverse(request)

    assert result.identifiability == "not_identified"
    assert result.compatible_hypothesis_ids == ("model_a", "model_b")
    assert result.equivalence_classes == (("model_a", "model_b"),)
    assert "posterior" not in result.to_mapping()
    assert "true_hypothesis" not in result.to_mapping()


def test_inverse_returns_set_identification_when_catalog_is_narrowed() -> None:
    cooperative = _game(cooperative=True)
    request = InverseGameRequest(
        hypotheses=(
            GameHypothesis("cooperative_a", cooperative),
            GameHypothesis("cooperative_b", cooperative),
            GameHypothesis("defecting", _game(cooperative=False)),
        ),
        observations=_observations(),
    )

    result = BoundedGameTheoryService().infer_inverse(request)

    assert result.identifiability == "set_identified"
    assert result.compatible_hypothesis_ids == (
        "cooperative_a",
        "cooperative_b",
    )


def test_inverse_reports_inconsistent_instead_of_selecting_best_bad_model() -> None:
    request = InverseGameRequest(
        hypotheses=(
            GameHypothesis("defecting_a", _game(cooperative=False)),
            GameHypothesis("defecting_b", _game(cooperative=False)),
        ),
        observations=_observations(),
        regret_tolerance=Rational(0),
    )

    result = BoundedGameTheoryService().infer_inverse(request)

    assert result.identifiability == "inconsistent"
    assert result.compatible_hypothesis_ids == ()
    assert all(not score.compatible for score in result.scores)


def test_inverse_contract_rejects_mixed_topology_and_unknown_actions() -> None:
    alternate = FiniteGame(
        players=("p1", "p2"),
        actions=(("C", "D", "WAIT"), ("C", "D")),
        payoffs=make_payoffs(
            ((a, b), (0, 0))
            for a in ("C", "D", "WAIT")
            for b in ("C", "D")
        ),
    )
    with pytest.raises(GameTheoryValidationError, match="topology"):
        InverseGameRequest(
            hypotheses=(
                GameHypothesis("base", _game(cooperative=True)),
                GameHypothesis("alternate", alternate),
            ),
            observations=_observations(),
        )
    with pytest.raises(GameTheoryValidationError, match="unknown action"):
        InverseGameRequest(
            hypotheses=(GameHypothesis("base", _game(cooperative=True)),),
            observations=(DecisionObservation(
                "p1",
                ("UNKNOWN", "C"),
                "opponents_observed_before_choice",
            ),),
        )


def test_inverse_refuses_simultaneous_action_telemetry() -> None:
    with pytest.raises(
        GameTheoryValidationError,
        match="opponents_observed_before_choice",
    ):
        DecisionObservation("p1", ("C", "C"), "simultaneous_actions")


def test_inverse_regret_is_invariant_to_positive_utility_scaling() -> None:
    request = InverseGameRequest(
        hypotheses=(
            GameHypothesis("base_scale", _game(cooperative=False, scale=1)),
            GameHypothesis("ten_x_scale", _game(cooperative=False, scale=10)),
        ),
        observations=_observations(),
        regret_tolerance=Rational(2, 3),
    )

    result = BoundedGameTheoryService().infer_inverse(request)

    assert result.identifiability == "not_identified"
    assert result.scores[0].mean_regret == result.scores[1].mean_regret
    assert result.scores[0].max_regret == result.scores[1].max_regret


def test_inverse_is_invariant_to_one_players_independent_rescaling() -> None:
    base = _game(cooperative=False)
    p1_rescaled = _rescale_player(base, player_index=0, factor=100)
    observation = DecisionObservation(
        "p2",
        ("C", "C"),
        "opponents_observed_before_choice",
    )
    request = InverseGameRequest(
        hypotheses=(
            GameHypothesis("base", base),
            GameHypothesis("p1_rescaled", p1_rescaled),
        ),
        observations=(observation,),
        regret_tolerance=Rational(1, 2),
    )

    result = BoundedGameTheoryService().infer_inverse(request)

    assert result.identifiability == "inconsistent"
    assert result.scores[0].mean_regret == result.scores[1].mean_regret
    assert result.scores[0].max_regret == result.scores[1].max_regret
    assert all(not score.compatible for score in result.scores)


def test_inverse_request_digest_and_result_are_order_deterministic() -> None:
    hypotheses = (
        GameHypothesis("z_model", _game(cooperative=False)),
        GameHypothesis("a_model", _game(cooperative=True)),
    )
    first_request = InverseGameRequest(hypotheses, _observations())
    second_request = InverseGameRequest(tuple(reversed(hypotheses)), _observations())
    service = BoundedGameTheoryService()

    assert first_request.digest == second_request.digest
    assert (
        json.dumps(service.infer_inverse(first_request).to_mapping(), sort_keys=True)
        == json.dumps(service.infer_inverse(second_request).to_mapping(), sort_keys=True)
    )


def test_inverse_magma_summary_omits_telemetry_and_hypothesis_ids() -> None:
    request = InverseGameRequest(
        hypotheses=(
            GameHypothesis("secret_model_name", _game(cooperative=True)),
            GameHypothesis("other_model_name", _game(cooperative=False)),
        ),
        observations=_observations(),
    )
    result = BoundedGameTheoryService().infer_inverse(request)
    encoded = json.dumps(result.magma_summary(request), sort_keys=True)

    for raw_value in (
        "secret_model_name",
        "other_model_name",
        "p1",
        "p2",
        '"C"',
        '"D"',
        "joint_profile",
        "utilities",
    ):
        assert raw_value not in encoded
    assert '"runtime_authority_granted": false' in encoded
    assert '"external_writes_applied": false' in encoded


def test_inverse_magma_summary_rejects_a_different_request() -> None:
    request = InverseGameRequest(
        hypotheses=(GameHypothesis("candidate", _game(cooperative=True)),),
        observations=_observations(),
    )
    result = BoundedGameTheoryService().infer_inverse(request)
    altered = InverseGameRequest(
        hypotheses=(GameHypothesis("candidate", _game(cooperative=True)),),
        observations=tuple(reversed(_observations())),
    )

    with pytest.raises(GameTheoryValidationError, match="digest mismatch"):
        result.magma_summary(altered)
