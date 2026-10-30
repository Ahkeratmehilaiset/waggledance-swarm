# SPDX-License-Identifier: BUSL-1.1
"""Deterministic advisory game-theory service with hard operation bounds."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from fractions import Fraction

from waggledance.core.game_theory_contracts import (
    ForwardGameRequest,
    ForwardGameResult,
    GameTheoryValidationError,
    HypothesisScore,
    InverseGameRequest,
    InverseGameResult,
    MixedStrategy,
    Rational,
)
from waggledance.core.game_theory_verifier import (
    UnilateralRegretEvaluator,
    enumerate_pure_nash,
    payoff_map,
    verify_pure_equilibria,
    verify_inverse_result,
    verify_zero_sum_mixed_strategy,
)


_FORWARD_ASSUMPTIONS = (
    "finite_complete_information_normal_form",
    "utilities_are_cardinal_within_the_supplied_model",
    "recommendation_is_advisory_only",
)
_INVERSE_ASSUMPTIONS = (
    "true_model_if_any_is_in_the_supplied_catalog",
    "opponents_actions_were_observed_before_each_choice",
    "regret_is_normalized_by_each_acting_players_utility_range",
    "no_hidden_intent_is_asserted",
    "result_cannot_change_runtime_authority",
)


class BoundedGameTheoryService:
    """Dependency-free implementation of the shared game-theory port.

    General finite games receive exact pure-Nash enumeration. Two-player
    zero-sum games additionally receive deterministic fictitious-play advice
    with an independently recomputed exploitability certificate. Inverse
    reasoning only ranks an explicit finite hypothesis catalog.
    """

    __slots__ = ()

    def solve_forward(self, request: ForwardGameRequest) -> ForwardGameResult:
        if not isinstance(request, ForwardGameRequest):
            raise GameTheoryValidationError("request must be ForwardGameRequest")

        game = request.game
        pure_equilibria = enumerate_pure_nash(game)
        if not verify_pure_equilibria(game, pure_equilibria):
            raise RuntimeError("independent pure-equilibrium verification failed")

        if request.concept == "pure_nash":
            return ForwardGameResult(
                status="exact_verified",
                concept="pure_nash",
                game_digest=game.digest,
                request_digest=request.digest,
                pure_equilibria=pure_equilibria,
                verifier_status="verified",
                assumptions=_FORWARD_ASSUMPTIONS,
            )

        if request.concept == "auto" and pure_equilibria:
            return ForwardGameResult(
                status="exact_verified",
                concept="pure_nash",
                game_digest=game.digest,
                request_digest=request.digest,
                pure_equilibria=pure_equilibria,
                verifier_status="verified",
                assumptions=_FORWARD_ASSUMPTIONS,
            )

        if game.structure != "zero_sum" or len(game.players) != 2:
            return ForwardGameResult(
                status="unsupported",
                concept=request.concept,
                game_digest=game.digest,
                request_digest=request.digest,
                pure_equilibria=pure_equilibria,
                verifier_status="verified",
                abstain_reason="mixed_general_sum_not_supported_in_v1",
                assumptions=_FORWARD_ASSUMPTIONS,
            )

        strategies, iterations = _deterministic_fictitious_play(
            request,
        )
        verification = verify_zero_sum_mixed_strategy(game, strategies)
        converged = (
            verification.exploitability.as_fraction()
            <= request.epsilon.as_fraction()
        )
        return ForwardGameResult(
            status="epsilon_verified" if converged else "budget_exhausted",
            concept="zero_sum_fictitious_play",
            game_digest=game.digest,
            request_digest=request.digest,
            pure_equilibria=pure_equilibria,
            mixed_strategies=strategies,
            value_lower=verification.value_lower,
            value_upper=verification.value_upper,
            exploitability=verification.exploitability,
            iterations=iterations,
            verifier_status="verified",
            abstain_reason=None if converged else "epsilon_not_reached",
            assumptions=_FORWARD_ASSUMPTIONS,
        )

    def infer_inverse(self, request: InverseGameRequest) -> InverseGameResult:
        if not isinstance(request, InverseGameRequest):
            raise GameTheoryValidationError("request must be InverseGameRequest")

        regret_vectors: dict[str, tuple[Fraction, ...]] = {}
        score_rows: list[HypothesisScore] = []
        tolerance = request.regret_tolerance.as_fraction()

        for hypothesis in request.hypotheses:
            game = hypothesis.game
            player_indexes = {
                player_id: index for index, player_id in enumerate(game.players)
            }
            regret_evaluator = UnilateralRegretEvaluator(game)
            regrets = tuple(
                regret_evaluator.normalized_regret(
                    observation.joint_profile,
                    player_indexes[observation.acting_player],
                )
                for observation in request.observations
            )
            regret_vectors[hypothesis.hypothesis_id] = regrets
            mean = sum(regrets, Fraction(0)) / len(regrets)
            maximum = max(regrets)
            score_rows.append(
                HypothesisScore(
                    hypothesis_id=hypothesis.hypothesis_id,
                    mean_regret=Rational.from_fraction(mean),
                    max_regret=Rational.from_fraction(maximum),
                    compatible=maximum <= tolerance,
                )
            )

        scores = tuple(sorted(
            score_rows,
            key=lambda score: (
                score.mean_regret.as_fraction(),
                score.max_regret.as_fraction(),
                score.hypothesis_id,
            ),
        ))
        compatible = tuple(
            score.hypothesis_id for score in scores if score.compatible
        )
        equivalence_classes = _equivalence_classes(regret_vectors)

        if not compatible:
            identifiability = "inconsistent"
        elif len(compatible) == 1:
            identifiability = "catalog_identified"
        elif len(compatible) < len(request.hypotheses):
            identifiability = "set_identified"
        else:
            identifiability = "not_identified"

        result = InverseGameResult(
            status="exact_verified",
            request_digest=request.digest,
            identifiability=identifiability,
            compatible_hypothesis_ids=compatible,
            scores=scores,
            equivalence_classes=equivalence_classes,
            verifier_status="pending",
            assumptions=_INVERSE_ASSUMPTIONS,
        )
        if not verify_inverse_result(request, result):
            raise RuntimeError("independent inverse-result verification failed")
        return replace(result, verifier_status="verified")


def _deterministic_fictitious_play(
    request: ForwardGameRequest,
) -> tuple[tuple[MixedStrategy, ...], int]:
    game = request.game
    payoffs = payoff_map(game)
    row_actions = game.actions[0]
    column_actions = game.actions[1]
    matrix = tuple(
        tuple(
            payoffs[(row_action, column_action)][0]
            for column_action in column_actions
        )
        for row_action in row_actions
    )

    row_counts = [0] * len(row_actions)
    column_counts = [0] * len(column_actions)
    row_score_totals = [0] * len(row_actions)
    column_score_totals = [0] * len(column_actions)
    row_choice = 0
    column_choice = 0
    for iteration in range(1, request.max_iterations + 1):
        selected_row = row_choice
        selected_column = column_choice
        row_counts[selected_row] += 1
        column_counts[selected_column] += 1
        for row_index in range(len(row_actions)):
            row_score_totals[row_index] += matrix[row_index][selected_column]
        for column_index in range(len(column_actions)):
            column_score_totals[column_index] += matrix[selected_row][column_index]

        row_scores = tuple(row_score_totals)
        column_scores = tuple(column_score_totals)
        row_choice = _argmax_lexicographic(row_scores)
        column_choice = _argmin_lexicographic(column_scores)
        exploitability = Fraction(
            max(row_scores) - min(column_scores),
            iteration,
        )
        if exploitability <= request.epsilon.as_fraction():
            return _strategies_from_counts(
                game.players,
                game.actions,
                row_counts,
                column_counts,
                iteration,
            ), iteration

    return _strategies_from_counts(
        game.players,
        game.actions,
        row_counts,
        column_counts,
        request.max_iterations,
    ), request.max_iterations


def _strategies_from_counts(
    players: tuple[str, ...],
    actions: tuple[tuple[str, ...], ...],
    row_counts: list[int],
    column_counts: list[int],
    total: int,
) -> tuple[MixedStrategy, MixedStrategy]:
    return (
        MixedStrategy(
            player_id=players[0],
            probabilities=tuple(
                (action, Rational(count, total))
                for action, count in zip(actions[0], row_counts)
            ),
        ),
        MixedStrategy(
            player_id=players[1],
            probabilities=tuple(
                (action, Rational(count, total))
                for action, count in zip(actions[1], column_counts)
            ),
        ),
    )


def _argmax_lexicographic(values: tuple[int, ...]) -> int:
    best = max(values)
    return values.index(best)


def _argmin_lexicographic(values: tuple[int, ...]) -> int:
    best = min(values)
    return values.index(best)


def _equivalence_classes(
    regret_vectors: dict[str, tuple[Fraction, ...]],
) -> tuple[tuple[str, ...], ...]:
    grouped: defaultdict[tuple[Fraction, ...], list[str]] = defaultdict(list)
    for hypothesis_id, vector in regret_vectors.items():
        grouped[vector].append(hypothesis_id)
    return tuple(sorted(
        (tuple(sorted(group)) for group in grouped.values()),
        key=lambda group: group[0],
    ))
