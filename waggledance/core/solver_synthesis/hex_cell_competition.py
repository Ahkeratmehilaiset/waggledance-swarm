# SPDX-License-Identifier: BUSL-1.1
"""Non-authority hex-cell competition contract for solver candidates.

The v0 contract records evidence that multiple solver candidates in the same
hex cell were compared for the same capability. It deliberately does not route
traffic, mutate candidate state, or deprecate losers. Authority transitions stay
behind later operator-gated promotion work.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Mapping

from waggledance.core.magma.canonical import sha256_digest

from .solver_candidate_store import SolverCandidate


HEX_CELL_COMPETITION_RESULT_SCHEMA_VERSION = (
    "hex_cell_competition_result.v0"
)
HEX_CELL_COMPETITION_AUTHORITY_STATUS = "non_authority_contract"
HEX_CELL_COMPETITION_RANKING_RULE = "score_desc_candidate_id_asc"
HEX_CELL_COMPETITION_DIGEST_ALGORITHM = "magma-jcs-subset-v1"


@dataclass(frozen=True)
class CandidateCompetitionScore:
    """Score row used by ``HexCellCompetitionResult``."""

    candidate_id: str
    solver_name: str
    cell_id: str
    capability_id: str
    score: float
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field_name, value in (
            ("candidate_id", self.candidate_id),
            ("solver_name", self.solver_name),
            ("cell_id", self.cell_id),
            ("capability_id", self.capability_id),
        ):
            if not str(value).strip():
                raise ValueError(f"{field_name} must be non-empty")
        if not math.isfinite(self.score):
            raise ValueError("score must be finite")

    def to_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id,
            "solver_name": self.solver_name,
            "cell_id": self.cell_id,
            "capability_id": self.capability_id,
            "score": self.score,
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True)
class HexCellCompetitionResult:
    """Evidence-only comparison result for same-cell solver candidates."""

    schema_version: str
    competition_id: str
    cell_id: str
    capability_id: str
    winner_id: str
    loser_ids: tuple[str, ...]
    candidate_scores: tuple[CandidateCompetitionScore, ...]
    evidence_digest: str
    evidence_digest_algorithm: str
    ranking_rule: str
    authority_status: str
    runtime_traffic_mutation_applied: bool
    candidate_state_mutation_applied: bool
    operator_gate_required_for_authority: bool

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "competition_id": self.competition_id,
            "cell_id": self.cell_id,
            "capability_id": self.capability_id,
            "winner_id": self.winner_id,
            "loser_ids": list(self.loser_ids),
            "candidate_scores": [
                score.to_dict() for score in self.candidate_scores
            ],
            "evidence_digest": self.evidence_digest,
            "evidence_digest_algorithm": self.evidence_digest_algorithm,
            "ranking_rule": self.ranking_rule,
            "authority_status": self.authority_status,
            "runtime_traffic_mutation_applied": (
                self.runtime_traffic_mutation_applied
            ),
            "candidate_state_mutation_applied": (
                self.candidate_state_mutation_applied
            ),
            "operator_gate_required_for_authority": (
                self.operator_gate_required_for_authority
            ),
        }


def build_hex_cell_competition_result(
    *,
    candidates: Iterable[SolverCandidate],
    capability_id: str,
    scores: Mapping[str, float],
    evidence_refs: Mapping[str, Iterable[str]] | None = None,
) -> HexCellCompetitionResult:
    """Build a deterministic evidence-only competition result.

    The caller supplies synthetic candidate records and numeric scores from a
    separate evaluation. This function only validates shape and records the
    winner/loser evidence. It never writes to ``SolverCandidateStore`` and never
    updates provenance activation state.
    """
    candidate_list = list(candidates)
    if len(candidate_list) < 2:
        raise ValueError(
            "hex-cell competition requires at least two candidates"
        )
    normalized_capability = _require_non_empty("capability_id", capability_id)
    evidence_refs = evidence_refs or {}

    candidate_ids = [candidate.candidate_id for candidate in candidate_list]
    duplicate_ids = sorted({
        candidate_id for candidate_id in candidate_ids
        if candidate_ids.count(candidate_id) > 1
    })
    if duplicate_ids:
        raise ValueError(f"duplicate candidate_id values: {duplicate_ids}")

    candidate_id_set = set(candidate_ids)
    missing_scores = sorted(candidate_id_set - set(scores))
    if missing_scores:
        raise ValueError(f"missing scores for candidates: {missing_scores}")
    unknown_scores = sorted(set(scores) - candidate_id_set)
    if unknown_scores:
        raise ValueError(f"scores include unknown candidates: {unknown_scores}")

    cell_ids = {candidate.cell_id for candidate in candidate_list}
    if len(cell_ids) != 1:
        raise ValueError(
            "hex-cell competition requires all candidates to share cell_id"
        )
    cell_id = _require_non_empty("cell_id", next(iter(cell_ids)))

    rows = []
    for candidate in candidate_list:
        _validate_candidate(candidate, normalized_capability)
        refs = tuple(str(ref) for ref in evidence_refs.get(
            candidate.candidate_id, ()
        ))
        rows.append(CandidateCompetitionScore(
            candidate_id=candidate.candidate_id,
            solver_name=candidate.solver_name,
            cell_id=cell_id,
            capability_id=normalized_capability,
            score=float(scores[candidate.candidate_id]),
            evidence_refs=refs,
        ))

    ranked = sorted(
        rows,
        key=lambda row: (-row.score, row.candidate_id),
    )
    winner_id = ranked[0].candidate_id
    loser_ids = tuple(row.candidate_id for row in ranked[1:])
    candidate_scores = tuple(sorted(rows, key=lambda row: row.candidate_id))

    evidence_payload = {
        "schema_version": HEX_CELL_COMPETITION_RESULT_SCHEMA_VERSION,
        "cell_id": cell_id,
        "capability_id": normalized_capability,
        "ranking_rule": HEX_CELL_COMPETITION_RANKING_RULE,
        "candidate_scores": [
            score.to_dict() for score in candidate_scores
        ],
        "winner_id": winner_id,
        "loser_ids": list(loser_ids),
        "authority_status": HEX_CELL_COMPETITION_AUTHORITY_STATUS,
        "runtime_traffic_mutation_applied": False,
        "candidate_state_mutation_applied": False,
        "operator_gate_required_for_authority": True,
    }
    evidence_digest = sha256_digest(evidence_payload)
    competition_id = (
        "hexcellcmp:"
        f"{cell_id}:{normalized_capability}:"
        f"{evidence_digest.removeprefix('sha256:')[:16]}"
    )
    return HexCellCompetitionResult(
        schema_version=HEX_CELL_COMPETITION_RESULT_SCHEMA_VERSION,
        competition_id=competition_id,
        cell_id=cell_id,
        capability_id=normalized_capability,
        winner_id=winner_id,
        loser_ids=loser_ids,
        candidate_scores=candidate_scores,
        evidence_digest=evidence_digest,
        evidence_digest_algorithm=HEX_CELL_COMPETITION_DIGEST_ALGORITHM,
        ranking_rule=HEX_CELL_COMPETITION_RANKING_RULE,
        authority_status=HEX_CELL_COMPETITION_AUTHORITY_STATUS,
        runtime_traffic_mutation_applied=False,
        candidate_state_mutation_applied=False,
        operator_gate_required_for_authority=True,
    )


def _validate_candidate(
    candidate: SolverCandidate,
    capability_id: str,
) -> None:
    candidate_capability = candidate.spec_or_code.get("capability_id")
    if candidate_capability != capability_id:
        raise ValueError(
            "hex-cell competition requires all candidates to share "
            f"capability_id {capability_id!r}; candidate "
            f"{candidate.candidate_id!r} has {candidate_capability!r}"
        )
    if candidate.no_runtime_mutation is not True:
        raise ValueError(
            "hex-cell competition v0 only accepts no_runtime_mutation "
            f"candidates; candidate {candidate.candidate_id!r} is mutable"
        )


def _require_non_empty(field_name: str, value: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{field_name} must be non-empty")
    return normalized


__all__ = [
    "CandidateCompetitionScore",
    "HexCellCompetitionResult",
    "HEX_CELL_COMPETITION_AUTHORITY_STATUS",
    "HEX_CELL_COMPETITION_DIGEST_ALGORITHM",
    "HEX_CELL_COMPETITION_RANKING_RULE",
    "HEX_CELL_COMPETITION_RESULT_SCHEMA_VERSION",
    "build_hex_cell_competition_result",
]
