# SPDX-License-Identifier: BUSL-1.1
"""Independent exact checks for bounded finite-game recommendations."""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from itertools import product
from collections import defaultdict

from waggledance.core.game_theory_contracts import (
    FiniteGame,
    GameTheoryValidationError,
    HypothesisScore,
    InverseGameRequest,
    InverseGameResult,
    MixedStrategy,
    PureEquilibrium,
    Rational,
)


@dataclass(frozen=True)
class MixedStrategyVerification:
    """Exact zero-sum best-response bounds for one strategy profile."""

    value_lower: Rational
    value_upper: Rational
    exploitability: Rational


def payoff_map(game: FiniteGame) -> dict[tuple[str, ...], tuple[int, ...]]:
    return {entry.profile: entry.utilities for entry in game.payoffs}


class UnilateralRegretEvaluator:
    """Reuse one trusted payoff index across bounded regret checks."""

    __slots__ = ("_game", "_payoffs", "_utility_ranges")

    def __init__(self, game: FiniteGame) -> None:
        if not isinstance(game, FiniteGame):
            raise GameTheoryValidationError("game must be FiniteGame")
        self._game = game
        self._payoffs = payoff_map(game)
        self._utility_ranges = tuple(
            max(entry.utilities[player_index] for entry in game.payoffs)
            - min(entry.utilities[player_index] for entry in game.payoffs)
            for player_index in range(len(game.players))
        )

    def regret(self, profile: tuple[str, ...], player_index: int) -> int:
        game = self._game
        if not 0 <= player_index < len(game.players):
            raise GameTheoryValidationError("player_index outside game")
        if len(profile) != len(game.players):
            raise GameTheoryValidationError("profile arity mismatch")
        if profile not in self._payoffs:
            raise GameTheoryValidationError("profile is not legal for game")
        observed = self._payoffs[profile][player_index]
        best = observed
        for action in game.actions[player_index]:
            deviation = list(profile)
            deviation[player_index] = action
            best = max(
                best,
                self._payoffs[tuple(deviation)][player_index],
            )
        return best - observed

    def normalized_regret(
        self,
        profile: tuple[str, ...],
        player_index: int,
    ) -> Fraction:
        """Return scale-invariant regret within the acting player's range."""

        regret = self.regret(profile, player_index)
        utility_range = self._utility_ranges[player_index]
        if utility_range == 0:
            return Fraction(0)
        return Fraction(regret, utility_range)


def unilateral_regret(
    game: FiniteGame,
    profile: tuple[str, ...],
    player_index: int,
) -> int:
    """Return the exact non-negative unilateral regret for one player."""

    return UnilateralRegretEvaluator(game).regret(profile, player_index)


def enumerate_pure_nash(game: FiniteGame) -> tuple[PureEquilibrium, ...]:
    """Enumerate and independently verify all pure Nash equilibria."""

    evaluator = UnilateralRegretEvaluator(game)
    equilibria: list[PureEquilibrium] = []
    for profile in product(*game.actions):
        regrets = tuple(
            evaluator.regret(profile, player_index)
            for player_index in range(len(game.players))
        )
        if all(regret == 0 for regret in regrets):
            equilibria.append(
                PureEquilibrium(profile=tuple(profile), max_regret=max(regrets))
            )
    return tuple(equilibria)


def verify_pure_equilibria(
    game: FiniteGame,
    equilibria: tuple[PureEquilibrium, ...],
) -> bool:
    """Require an exact, complete equilibrium set rather than a sample."""

    return equilibria == enumerate_pure_nash(game)


def verify_zero_sum_mixed_strategy(
    game: FiniteGame,
    strategies: tuple[MixedStrategy, ...],
) -> MixedStrategyVerification:
    """Validate a two-player zero-sum profile and derive exact bounds.

    For row strategy ``p`` and column strategy ``q``, the row player's
    guaranteed payoff is ``min_j p A e_j`` and its best response against q is
    ``max_i e_i A q``. Their difference is the Nash exploitability gap.
    """

    if game.structure != "zero_sum" or len(game.players) != 2:
        raise GameTheoryValidationError(
            "mixed verification requires a two-player zero-sum game"
        )
    if len(strategies) != 2:
        raise GameTheoryValidationError("one mixed strategy per player required")

    probability_vectors: list[tuple[Fraction, ...]] = []
    for player_index, strategy in enumerate(strategies):
        if strategy.player_id != game.players[player_index]:
            raise GameTheoryValidationError("mixed strategy player order mismatch")
        expected_actions = game.actions[player_index]
        actual_actions = tuple(action for action, _ in strategy.probabilities)
        if actual_actions != expected_actions:
            raise GameTheoryValidationError(
                "mixed strategy actions must match canonical game action order"
            )
        probabilities = tuple(
            probability.as_fraction()
            for _, probability in strategy.probabilities
        )
        if any(probability < 0 for probability in probabilities):
            raise GameTheoryValidationError("strategy probabilities must be non-negative")
        if sum(probabilities, Fraction(0)) != 1:
            raise GameTheoryValidationError("strategy probabilities must sum to one")
        probability_vectors.append(probabilities)

    row_probabilities, column_probabilities = probability_vectors
    matrix = _row_payoff_matrix(game)
    row_action_values = tuple(
        sum(
            Fraction(matrix[row_index][column_index])
            * column_probabilities[column_index]
            for column_index in range(len(game.actions[1]))
        )
        for row_index in range(len(game.actions[0]))
    )
    column_action_values = tuple(
        sum(
            row_probabilities[row_index]
            * Fraction(matrix[row_index][column_index])
            for row_index in range(len(game.actions[0]))
        )
        for column_index in range(len(game.actions[1]))
    )
    lower = min(column_action_values)
    upper = max(row_action_values)
    gap = upper - lower
    if gap < 0:
        raise GameTheoryValidationError("invalid negative exploitability gap")
    return MixedStrategyVerification(
        value_lower=Rational.from_fraction(lower),
        value_upper=Rational.from_fraction(upper),
        exploitability=Rational.from_fraction(gap),
    )


def verify_inverse_result(
    request: InverseGameRequest,
    result: InverseGameResult,
) -> bool:
    """Independently rederive finite-catalog inverse scores and identity."""

    if result.request_digest != request.digest:
        return False
    regret_vectors: dict[str, tuple[Fraction, ...]] = {}
    score_rows: list[HypothesisScore] = []
    tolerance = request.regret_tolerance.as_fraction()
    for hypothesis in request.hypotheses:
        game = hypothesis.game
        player_indexes = {
            player_id: index for index, player_id in enumerate(game.players)
        }
        evaluator = UnilateralRegretEvaluator(game)
        regrets = tuple(
            evaluator.normalized_regret(
                observation.joint_profile,
                player_indexes[observation.acting_player],
            )
            for observation in request.observations
        )
        regret_vectors[hypothesis.hypothesis_id] = regrets
        mean = sum(regrets, Fraction(0)) / len(regrets)
        maximum = max(regrets)
        score_rows.append(HypothesisScore(
            hypothesis_id=hypothesis.hypothesis_id,
            mean_regret=Rational.from_fraction(mean),
            max_regret=Rational.from_fraction(maximum),
            compatible=maximum <= tolerance,
        ))

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
    if not compatible:
        identifiability = "inconsistent"
    elif len(compatible) == 1:
        identifiability = "catalog_identified"
    elif len(compatible) < len(request.hypotheses):
        identifiability = "set_identified"
    else:
        identifiability = "not_identified"

    grouped: defaultdict[tuple[Fraction, ...], list[str]] = defaultdict(list)
    for hypothesis_id, vector in regret_vectors.items():
        grouped[vector].append(hypothesis_id)
    equivalence_classes = tuple(sorted(
        (tuple(sorted(group)) for group in grouped.values()),
        key=lambda group: group[0],
    ))
    return (
        result.status == "exact_verified"
        and result.identification_scope
        == "finite_supplied_catalog_with_observed_opponents"
        and result.identifiability == identifiability
        and result.compatible_hypothesis_ids == compatible
        and result.scores == scores
        and result.equivalence_classes == equivalence_classes
        and result.advisory_only is True
        and result.runtime_authority_granted is False
        and result.external_writes_applied is False
    )


def _row_payoff_matrix(game: FiniteGame) -> tuple[tuple[int, ...], ...]:
    payoffs = payoff_map(game)
    return tuple(
        tuple(
            payoffs[(row_action, column_action)][0]
            for column_action in game.actions[1]
        )
        for row_action in game.actions[0]
    )
