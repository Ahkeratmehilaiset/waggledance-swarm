# SPDX-License-Identifier: BUSL-1.1
"""Bounded contracts for advisory game-theory reasoning.

The v1 contract intentionally covers finite normal-form games only. It has no
controller, network, filesystem, subprocess, credential, or random-sampling
surface. Operational callers may use the returned distributions as advice,
but runtime authority remains outside this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from itertools import product
import re
from typing import Any, Iterable

from waggledance.core.magma.canonical import sha256_digest


SCHEMA_VERSION = "wd.finite_game.v1"
BACKEND_ID = "wd.bounded_finite_game"
BACKEND_VERSION = "1"

MAX_PLAYERS = 4
MAX_ACTIONS_PER_PLAYER = 16
MAX_JOINT_PROFILES = 4096
MAX_MATRIX_ACTIONS = 32
MAX_UTILITY_MAGNITUDE = 1_000_000
MAX_ITERATIONS = 50_000
MAX_HYPOTHESES = 32
MAX_OBSERVATIONS = 1024
MAX_RATIONAL_COMPONENT = (
    (2 * MAX_UTILITY_MAGNITUDE) ** MAX_PLAYERS * MAX_OBSERVATIONS
)
MAX_CANONICAL_INPUT_BYTES = 1_048_576

_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
_FORWARD_CONCEPTS = frozenset({
    "auto",
    "pure_nash",
    "zero_sum_fictitious_play",
})
_STRUCTURES = frozenset({"general_sum", "zero_sum"})


class GameTheoryValidationError(ValueError):
    """The request is outside the bounded finite-game contract."""


def _token(value: str, label: str) -> str:
    if not isinstance(value, str) or not _TOKEN_RE.fullmatch(value):
        raise GameTheoryValidationError(
            f"{label} must match {_TOKEN_RE.pattern}, got {value!r}"
        )
    return value


def _bounded_int(value: int, label: str, *, magnitude: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise GameTheoryValidationError(f"{label} must be an integer")
    if abs(value) > magnitude:
        raise GameTheoryValidationError(
            f"{label} exceeds magnitude bound {magnitude}"
        )
    return value


@dataclass(frozen=True, order=True)
class Rational:
    """Canonical JSON-safe rational number."""

    numerator: int
    denominator: int = 1

    def __post_init__(self) -> None:
        if (
            isinstance(self.numerator, bool)
            or not isinstance(self.numerator, int)
            or isinstance(self.denominator, bool)
            or not isinstance(self.denominator, int)
        ):
            raise GameTheoryValidationError("rational values must be integers")
        if self.denominator == 0:
            raise GameTheoryValidationError("rational denominator must be non-zero")
        if (
            abs(self.numerator) > MAX_RATIONAL_COMPONENT
            or abs(self.denominator) > MAX_RATIONAL_COMPONENT
        ):
            raise GameTheoryValidationError(
                f"rational components exceed bound {MAX_RATIONAL_COMPONENT}"
            )
        value = Fraction(self.numerator, self.denominator)
        object.__setattr__(self, "numerator", value.numerator)
        object.__setattr__(self, "denominator", value.denominator)

    @classmethod
    def from_fraction(cls, value: Fraction | int) -> "Rational":
        fraction = Fraction(value)
        return cls(fraction.numerator, fraction.denominator)

    def as_fraction(self) -> Fraction:
        return Fraction(self.numerator, self.denominator)

    def to_mapping(self) -> dict[str, int]:
        return {
            "numerator": self.numerator,
            "denominator": self.denominator,
        }


@dataclass(frozen=True)
class PayoffEntry:
    """Utilities for one complete joint-action profile."""

    profile: tuple[str, ...]
    utilities: tuple[int, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "profile", tuple(self.profile))
        object.__setattr__(self, "utilities", tuple(self.utilities))
        for index, utility in enumerate(self.utilities):
            _bounded_int(
                utility,
                f"utilities[{index}]",
                magnitude=MAX_UTILITY_MAGNITUDE,
            )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "profile": list(self.profile),
            "utilities": list(self.utilities),
        }


@dataclass(frozen=True)
class FiniteGame:
    """A complete, bounded normal-form game.

    Player and action order are semantic because utility tuples and strategy
    vectors refer to that order. Payoff-entry order is not semantic and is
    canonicalized to Cartesian action order during construction.
    """

    players: tuple[str, ...]
    actions: tuple[tuple[str, ...], ...]
    payoffs: tuple[PayoffEntry, ...]
    structure: str = "general_sum"
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        players = tuple(self.players)
        actions = tuple(tuple(player_actions) for player_actions in self.actions)
        payoffs = tuple(self.payoffs)
        object.__setattr__(self, "players", players)
        object.__setattr__(self, "actions", actions)

        if self.schema_version != SCHEMA_VERSION:
            raise GameTheoryValidationError("finite-game schema_version refused")
        if self.structure not in _STRUCTURES:
            raise GameTheoryValidationError(
                f"structure must be one of {sorted(_STRUCTURES)}"
            )
        if not 2 <= len(players) <= MAX_PLAYERS:
            raise GameTheoryValidationError(
                f"players must contain 2..{MAX_PLAYERS} entries"
            )
        if len(set(players)) != len(players):
            raise GameTheoryValidationError("player IDs must be unique")
        for index, player_id in enumerate(players):
            _token(player_id, f"players[{index}]")

        if len(actions) != len(players):
            raise GameTheoryValidationError(
                "actions must contain one action tuple per player"
            )
        for player_index, player_actions in enumerate(actions):
            if not 1 <= len(player_actions) <= MAX_ACTIONS_PER_PLAYER:
                raise GameTheoryValidationError(
                    f"actions[{player_index}] must contain "
                    f"1..{MAX_ACTIONS_PER_PLAYER} entries"
                )
            if len(set(player_actions)) != len(player_actions):
                raise GameTheoryValidationError(
                    f"actions[{player_index}] must be unique"
                )
            for action_index, action_id in enumerate(player_actions):
                _token(action_id, f"actions[{player_index}][{action_index}]")

        expected_profiles = tuple(product(*actions))
        if len(expected_profiles) > MAX_JOINT_PROFILES:
            raise GameTheoryValidationError(
                f"joint profiles exceed bound {MAX_JOINT_PROFILES}"
            )
        by_profile: dict[tuple[str, ...], PayoffEntry] = {}
        for entry in payoffs:
            if not isinstance(entry, PayoffEntry):
                raise GameTheoryValidationError(
                    "payoffs must contain PayoffEntry values"
                )
            if len(entry.profile) != len(players):
                raise GameTheoryValidationError("payoff profile arity mismatch")
            if len(entry.utilities) != len(players):
                raise GameTheoryValidationError("payoff utility arity mismatch")
            profile = tuple(entry.profile)
            if profile in by_profile:
                raise GameTheoryValidationError(
                    f"duplicate payoff profile: {profile!r}"
                )
            by_profile[profile] = entry

        expected_set = set(expected_profiles)
        actual_set = set(by_profile)
        if actual_set != expected_set:
            missing = len(expected_set - actual_set)
            extra = len(actual_set - expected_set)
            raise GameTheoryValidationError(
                f"payoff table must be complete (missing={missing}, extra={extra})"
            )
        canonical_payoffs = tuple(by_profile[profile] for profile in expected_profiles)
        object.__setattr__(self, "payoffs", canonical_payoffs)

        if self.structure == "zero_sum":
            if len(players) != 2:
                raise GameTheoryValidationError(
                    "zero_sum v1 requires exactly two players"
                )
            if any(sum(entry.utilities) != 0 for entry in canonical_payoffs):
                raise GameTheoryValidationError(
                    "zero_sum utilities must sum to zero at every profile"
                )

        if len(self.canonical_bytes()) > MAX_CANONICAL_INPUT_BYTES:
            raise GameTheoryValidationError(
                f"canonical game exceeds {MAX_CANONICAL_INPUT_BYTES} bytes"
            )

    @property
    def digest(self) -> str:
        return sha256_digest(self.to_canonical_mapping())

    @property
    def profile_count(self) -> int:
        return len(self.payoffs)

    def to_canonical_mapping(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "structure": self.structure,
            "players": list(self.players),
            "actions": [list(player_actions) for player_actions in self.actions],
            "payoffs": [entry.to_mapping() for entry in self.payoffs],
        }

    def canonical_bytes(self) -> bytes:
        from waggledance.core.magma.canonical import canonical_json_bytes

        return canonical_json_bytes(self.to_canonical_mapping())


@dataclass(frozen=True)
class ForwardGameRequest:
    game: FiniteGame
    concept: str = "auto"
    max_iterations: int = 10_000
    epsilon: Rational = Rational(1, 100)

    def __post_init__(self) -> None:
        if not isinstance(self.game, FiniteGame):
            raise GameTheoryValidationError("game must be FiniteGame")
        if self.concept not in _FORWARD_CONCEPTS:
            raise GameTheoryValidationError(
                f"concept must be one of {sorted(_FORWARD_CONCEPTS)}"
            )
        if (
            isinstance(self.max_iterations, bool)
            or not isinstance(self.max_iterations, int)
            or not 1 <= self.max_iterations <= MAX_ITERATIONS
        ):
            raise GameTheoryValidationError(
                f"max_iterations must be within 1..{MAX_ITERATIONS}"
            )
        if not isinstance(self.epsilon, Rational):
            raise GameTheoryValidationError("epsilon must be Rational")
        if self.epsilon.as_fraction() < 0:
            raise GameTheoryValidationError("epsilon must be non-negative")

    @property
    def digest(self) -> str:
        return sha256_digest(self.to_canonical_mapping())

    def to_canonical_mapping(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "operation": "forward_game_theory",
            "game": self.game.to_canonical_mapping(),
            "concept": self.concept,
            "max_iterations": self.max_iterations,
            "epsilon": self.epsilon.to_mapping(),
        }


@dataclass(frozen=True)
class PureEquilibrium:
    profile: tuple[str, ...]
    max_regret: int

    def to_mapping(self) -> dict[str, Any]:
        return {"profile": list(self.profile), "max_regret": self.max_regret}


@dataclass(frozen=True)
class MixedStrategy:
    player_id: str
    probabilities: tuple[tuple[str, Rational], ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "probabilities",
            tuple((action, probability) for action, probability in self.probabilities),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "player_id": self.player_id,
            "probabilities": [
                {"action_id": action, "probability": probability.to_mapping()}
                for action, probability in self.probabilities
            ],
        }


@dataclass(frozen=True)
class ForwardGameResult:
    status: str
    concept: str
    game_digest: str
    request_digest: str
    pure_equilibria: tuple[PureEquilibrium, ...] = ()
    mixed_strategies: tuple[MixedStrategy, ...] = ()
    value_lower: Rational | None = None
    value_upper: Rational | None = None
    exploitability: Rational | None = None
    iterations: int = 0
    verifier_status: str = "not_run"
    abstain_reason: str | None = None
    assumptions: tuple[str, ...] = ()
    backend_id: str = BACKEND_ID
    backend_version: str = BACKEND_VERSION
    advisory_only: bool = True
    runtime_authority_granted: bool = False
    external_writes_applied: bool = False

    def __post_init__(self) -> None:
        if not self.advisory_only:
            raise GameTheoryValidationError("game-theory result must be advisory")
        if self.runtime_authority_granted or self.external_writes_applied:
            raise GameTheoryValidationError(
                "game-theory result cannot grant authority or apply writes"
            )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "concept": self.concept,
            "game_digest": self.game_digest,
            "request_digest": self.request_digest,
            "pure_equilibria": [item.to_mapping() for item in self.pure_equilibria],
            "mixed_strategies": [item.to_mapping() for item in self.mixed_strategies],
            "value_lower": _rational_mapping(self.value_lower),
            "value_upper": _rational_mapping(self.value_upper),
            "exploitability": _rational_mapping(self.exploitability),
            "iterations": self.iterations,
            "verifier_status": self.verifier_status,
            "abstain_reason": self.abstain_reason,
            "assumptions": list(self.assumptions),
            "backend_id": self.backend_id,
            "backend_version": self.backend_version,
            "advisory_only": self.advisory_only,
            "runtime_authority_granted": self.runtime_authority_granted,
            "external_writes_applied": self.external_writes_applied,
        }

    def magma_summary(self, request: ForwardGameRequest) -> dict[str, Any]:
        if request.digest != self.request_digest:
            raise GameTheoryValidationError("forward request digest mismatch")
        game = request.game
        if game.digest != self.game_digest:
            raise GameTheoryValidationError("forward game digest mismatch")
        strategy_payload = {
            "pure_equilibria": [
                item.to_mapping() for item in self.pure_equilibria
            ],
            "mixed_strategies": [
                item.to_mapping() for item in self.mixed_strategies
            ],
        }
        return {
            "schema_version": SCHEMA_VERSION,
            "operation": "forward_game_theory",
            "game_digest": self.game_digest,
            "request_digest": self.request_digest,
            "result_digest": sha256_digest(self.to_mapping()),
            "strategy_digest": sha256_digest(strategy_payload),
            "backend_id": self.backend_id,
            "backend_version": self.backend_version,
            "player_count": len(game.players),
            "action_counts": [len(actions) for actions in game.actions],
            "profile_count": game.profile_count,
            "concept": self.concept,
            "max_iterations": request.max_iterations,
            "epsilon": request.epsilon.to_mapping(),
            "status": self.status,
            "iterations": self.iterations,
            "exploitability": _rational_mapping(self.exploitability),
            "verifier_status": self.verifier_status,
            "advisory_only": True,
            "runtime_authority_granted": False,
            "external_writes_applied": False,
        }


@dataclass(frozen=True)
class GameHypothesis:
    hypothesis_id: str
    game: FiniteGame

    def __post_init__(self) -> None:
        _token(self.hypothesis_id, "hypothesis_id")
        if not isinstance(self.game, FiniteGame):
            raise GameTheoryValidationError("hypothesis game must be FiniteGame")


@dataclass(frozen=True)
class DecisionObservation:
    """A choice made after the other players' actions were observable.

    V1 deliberately refuses simultaneous-action telemetry because ex-post
    regret would otherwise misclassify rational mixed play. Belief- or
    information-set-aware observations require a later extensive-form schema.
    """

    acting_player: str
    joint_profile: tuple[str, ...]
    information_state: str

    def __post_init__(self) -> None:
        _token(self.acting_player, "acting_player")
        object.__setattr__(self, "joint_profile", tuple(self.joint_profile))
        if self.information_state != "opponents_observed_before_choice":
            raise GameTheoryValidationError(
                "inverse v1 requires opponents_observed_before_choice"
            )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "acting_player": self.acting_player,
            "joint_profile": list(self.joint_profile),
            "information_state": self.information_state,
        }


@dataclass(frozen=True)
class InverseGameRequest:
    hypotheses: tuple[GameHypothesis, ...]
    observations: tuple[DecisionObservation, ...]
    regret_tolerance: Rational = Rational(0)

    def __post_init__(self) -> None:
        hypotheses = tuple(sorted(self.hypotheses, key=lambda item: item.hypothesis_id))
        observations = tuple(self.observations)
        object.__setattr__(self, "hypotheses", hypotheses)
        object.__setattr__(self, "observations", observations)

        if not 1 <= len(hypotheses) <= MAX_HYPOTHESES:
            raise GameTheoryValidationError(
                f"hypotheses must contain 1..{MAX_HYPOTHESES} entries"
            )
        if len({item.hypothesis_id for item in hypotheses}) != len(hypotheses):
            raise GameTheoryValidationError("hypothesis IDs must be unique")
        if not 1 <= len(observations) <= MAX_OBSERVATIONS:
            raise GameTheoryValidationError(
                f"observations must contain 1..{MAX_OBSERVATIONS} entries"
            )
        if not isinstance(self.regret_tolerance, Rational):
            raise GameTheoryValidationError("regret_tolerance must be Rational")
        if self.regret_tolerance.as_fraction() < 0:
            raise GameTheoryValidationError(
                "regret_tolerance must be non-negative"
            )
        if self.regret_tolerance.as_fraction() > 1:
            raise GameTheoryValidationError(
                "normalized regret_tolerance must not exceed one"
            )

        reference = hypotheses[0].game
        for hypothesis in hypotheses[1:]:
            game = hypothesis.game
            if game.players != reference.players or game.actions != reference.actions:
                raise GameTheoryValidationError(
                    "all hypotheses must share player/action topology"
                )
        for index, observation in enumerate(observations):
            if observation.acting_player not in reference.players:
                raise GameTheoryValidationError(
                    f"observations[{index}] has unknown acting_player"
                )
            if len(observation.joint_profile) != len(reference.players):
                raise GameTheoryValidationError(
                    f"observations[{index}] profile arity mismatch"
                )
            for player_index, action_id in enumerate(observation.joint_profile):
                if action_id not in reference.actions[player_index]:
                    raise GameTheoryValidationError(
                        f"observations[{index}] has unknown action"
                    )

        if len(self.canonical_bytes()) > MAX_CANONICAL_INPUT_BYTES:
            raise GameTheoryValidationError(
                f"canonical inverse request exceeds {MAX_CANONICAL_INPUT_BYTES} bytes"
            )

    @property
    def digest(self) -> str:
        return sha256_digest(self.to_canonical_mapping())

    def to_canonical_mapping(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "operation": "inverse_game_theory",
            "hypotheses": [
                {
                    "hypothesis_id": item.hypothesis_id,
                    "game": item.game.to_canonical_mapping(),
                }
                for item in self.hypotheses
            ],
            "observations": [item.to_mapping() for item in self.observations],
            "regret_tolerance": self.regret_tolerance.to_mapping(),
        }

    def canonical_bytes(self) -> bytes:
        from waggledance.core.magma.canonical import canonical_json_bytes

        return canonical_json_bytes(self.to_canonical_mapping())


@dataclass(frozen=True)
class HypothesisScore:
    hypothesis_id: str
    mean_regret: Rational
    max_regret: Rational
    compatible: bool

    def to_mapping(self) -> dict[str, Any]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "mean_regret": self.mean_regret.to_mapping(),
            "max_regret": self.max_regret.to_mapping(),
            "compatible": self.compatible,
        }


@dataclass(frozen=True)
class InverseGameResult:
    status: str
    request_digest: str
    identifiability: str
    compatible_hypothesis_ids: tuple[str, ...]
    scores: tuple[HypothesisScore, ...]
    equivalence_classes: tuple[tuple[str, ...], ...]
    identification_scope: str = (
        "finite_supplied_catalog_with_observed_opponents"
    )
    verifier_status: str = "not_run"
    assumptions: tuple[str, ...] = ()
    backend_id: str = BACKEND_ID
    backend_version: str = BACKEND_VERSION
    advisory_only: bool = True
    runtime_authority_granted: bool = False
    external_writes_applied: bool = False

    def __post_init__(self) -> None:
        if not self.advisory_only:
            raise GameTheoryValidationError("inverse result must be advisory")
        if self.runtime_authority_granted or self.external_writes_applied:
            raise GameTheoryValidationError(
                "inverse result cannot grant authority or apply writes"
            )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "request_digest": self.request_digest,
            "identifiability": self.identifiability,
            "compatible_hypothesis_ids": list(self.compatible_hypothesis_ids),
            "scores": [score.to_mapping() for score in self.scores],
            "equivalence_classes": [list(group) for group in self.equivalence_classes],
            "identification_scope": self.identification_scope,
            "verifier_status": self.verifier_status,
            "assumptions": list(self.assumptions),
            "backend_id": self.backend_id,
            "backend_version": self.backend_version,
            "advisory_only": self.advisory_only,
            "runtime_authority_granted": self.runtime_authority_granted,
            "external_writes_applied": self.external_writes_applied,
        }

    def magma_summary(self, request: InverseGameRequest) -> dict[str, Any]:
        if request.digest != self.request_digest:
            raise GameTheoryValidationError("inverse request digest mismatch")
        return {
            "schema_version": SCHEMA_VERSION,
            "operation": "inverse_game_theory",
            "request_digest": self.request_digest,
            "result_digest": sha256_digest(self.to_mapping()),
            "backend_id": self.backend_id,
            "backend_version": self.backend_version,
            "hypothesis_count": len(request.hypotheses),
            "observation_count": len(request.observations),
            "status": self.status,
            "identifiability": self.identifiability,
            "identification_scope": self.identification_scope,
            "verifier_status": self.verifier_status,
            "advisory_only": True,
            "runtime_authority_granted": False,
            "external_writes_applied": False,
        }


def make_payoffs(
    entries: Iterable[tuple[Iterable[str], Iterable[int]]],
) -> tuple[PayoffEntry, ...]:
    """Small convenience constructor for solver and test call sites."""

    return tuple(
        PayoffEntry(tuple(profile), tuple(utilities))
        for profile, utilities in entries
    )


def _rational_mapping(value: Rational | None) -> dict[str, int] | None:
    return value.to_mapping() if value is not None else None
