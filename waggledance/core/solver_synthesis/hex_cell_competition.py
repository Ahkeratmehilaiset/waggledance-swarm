# SPDX-License-Identifier: BUSL-1.1
"""Non-authority hex-cell competition contract for solver candidates.

The v0 contract records evidence that multiple solver candidates in the same
hex cell were compared for the same capability. It deliberately does not route
traffic, mutate candidate state, or deprecate losers. Authority transitions stay
behind later operator-gated promotion work.
"""
from __future__ import annotations

import math
from collections.abc import Mapping as MappingABC
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from waggledance.core.magma.canonical import sha256_digest

from .solver_candidate_store import SolverCandidate


HEX_CELL_COMPETITION_RESULT_SCHEMA_VERSION = (
    "hex_cell_competition_result.v0"
)
HEX_CELL_COMPETITION_AUTHORITY_STATUS = "non_authority_contract"
HEX_CELL_COMPETITION_RANKING_RULE = "score_desc_candidate_id_asc"
HEX_CELL_COMPETITION_DIGEST_ALGORITHM = "magma-jcs-subset-v1"
HEX_CELL_PROMOTION_ACCEPTANCE_SCHEMA_VERSION = (
    "hex_cell_promotion_acceptance.v0"
)
HEX_CELL_PROMOTION_ACCEPTANCE_STATUS = "operator_gate_required"
HEX_CELL_PROMOTION_ACCEPTANCE_NEXT_GATE = (
    "solver_provenance_operator_activation"
)
HEX_CELL_PROMOTION_ACCEPTANCE_RECEIPT_EVENT_TYPE = (
    "hex_cell.promotion_acceptance"
)
HEX_CELL_OPERATOR_GATE_AUTHORIZATION_SCHEMA_VERSION = (
    "hex_cell_operator_gate_authorization.v0"
)
HEX_CELL_OPERATOR_GATE_AUTHORIZATION_STATUS = (
    "operator_gate_cleared_activation_authorized"
)
HEX_CELL_OPERATOR_GATE_AUTHORIZATION_NEXT_GATE = (
    "solver_provenance_receipt_bound_activation"
)
HEX_CELL_OPERATOR_GATE_AUTHORIZATION_RECEIPT_EVENT_TYPE = (
    "hex_cell.operator_gate_authorization"
)
HEX_CELL_OPERATOR_GATE_DUPLICATE_RETRY_BEHAVIOR = (
    "same_inputs_same_authorization_digest_no_runtime_mutation"
)
HEX_CELL_ACTIVATION_PREFLIGHT_SCHEMA_VERSION = (
    "hex_cell_activation_preflight.v0"
)
HEX_CELL_ACTIVATION_PREFLIGHT_STATUS = (
    "solver_provenance_activation_receipt_verified"
)
HEX_CELL_ACTIVATION_PREFLIGHT_NEXT_GATE = (
    "runtime_authority_activation_commit"
)
HEX_CELL_ACTIVATION_PREFLIGHT_RECEIPT_EVENT_TYPE = (
    "hex_cell.activation_preflight"
)
HEX_CELL_ACTIVATION_PREFLIGHT_TRANSITION = "activation_authorised"
HEX_CELL_ACTIVATION_PREFLIGHT_NEW_STATE = "activated"


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


@dataclass(frozen=True)
class HexCellPromotionAcceptance:
    """Non-authority handoff from competition result to operator gate."""

    schema_version: str
    acceptance_id: str
    competition_id: str
    cell_id: str
    capability_id: str
    accepted_candidate_id: str
    rejected_candidate_ids: tuple[str, ...]
    competition_evidence_digest: str
    acceptance_digest: str
    evidence_digest_algorithm: str
    promotion_acceptance_status: str
    required_next_gate: str
    operator_gate_required: bool
    operator_gate_cleared: bool
    runtime_authority_granted: bool
    runtime_traffic_mutation_applied: bool
    candidate_state_mutation_applied: bool

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "acceptance_id": self.acceptance_id,
            "competition_id": self.competition_id,
            "cell_id": self.cell_id,
            "capability_id": self.capability_id,
            "accepted_candidate_id": self.accepted_candidate_id,
            "rejected_candidate_ids": list(self.rejected_candidate_ids),
            "competition_evidence_digest": self.competition_evidence_digest,
            "acceptance_digest": self.acceptance_digest,
            "evidence_digest_algorithm": self.evidence_digest_algorithm,
            "promotion_acceptance_status": self.promotion_acceptance_status,
            "required_next_gate": self.required_next_gate,
            "operator_gate_required": self.operator_gate_required,
            "operator_gate_cleared": self.operator_gate_cleared,
            "runtime_authority_granted": self.runtime_authority_granted,
            "runtime_traffic_mutation_applied": (
                self.runtime_traffic_mutation_applied
            ),
            "candidate_state_mutation_applied": (
                self.candidate_state_mutation_applied
            ),
        }


@dataclass(frozen=True)
class HexCellOperatorGateAuthorization:
    """Operator approval handoff after non-authority promotion acceptance.

    The artifact clears the operator gate for downstream solver provenance
    activation. It does not apply runtime authority or mutate candidate state.
    """

    schema_version: str
    authorization_id: str
    acceptance_id: str
    competition_id: str
    cell_id: str
    capability_id: str
    accepted_candidate_id: str
    rejected_candidate_ids: tuple[str, ...]
    competition_evidence_digest: str
    acceptance_digest: str
    acceptance_receipt_digest: str
    authorization_digest: str
    evidence_digest_algorithm: str
    operator_approval_id: str
    approved_by: str
    operator_decision: str
    operator_scope: str
    authority_status: str
    required_next_gate: str
    required_receipt_event_type: str
    receipt_ordering_enforced: bool
    authorization_receipt_required_before_runtime_authority: bool
    duplicate_retry_behavior: str
    operator_gate_required: bool
    operator_gate_cleared: bool
    operator_authorized_activation: bool
    runtime_authority_granted: bool
    runtime_traffic_mutation_applied: bool
    candidate_state_mutation_applied: bool

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "authorization_id": self.authorization_id,
            "acceptance_id": self.acceptance_id,
            "competition_id": self.competition_id,
            "cell_id": self.cell_id,
            "capability_id": self.capability_id,
            "accepted_candidate_id": self.accepted_candidate_id,
            "rejected_candidate_ids": list(self.rejected_candidate_ids),
            "competition_evidence_digest": self.competition_evidence_digest,
            "acceptance_digest": self.acceptance_digest,
            "acceptance_receipt_digest": self.acceptance_receipt_digest,
            "authorization_digest": self.authorization_digest,
            "evidence_digest_algorithm": self.evidence_digest_algorithm,
            "operator_approval_id": self.operator_approval_id,
            "approved_by": self.approved_by,
            "operator_decision": self.operator_decision,
            "operator_scope": self.operator_scope,
            "authority_status": self.authority_status,
            "required_next_gate": self.required_next_gate,
            "required_receipt_event_type": self.required_receipt_event_type,
            "receipt_ordering_enforced": self.receipt_ordering_enforced,
            "authorization_receipt_required_before_runtime_authority": (
                self.authorization_receipt_required_before_runtime_authority
            ),
            "duplicate_retry_behavior": self.duplicate_retry_behavior,
            "operator_gate_required": self.operator_gate_required,
            "operator_gate_cleared": self.operator_gate_cleared,
            "operator_authorized_activation": (
                self.operator_authorized_activation
            ),
            "runtime_authority_granted": self.runtime_authority_granted,
            "runtime_traffic_mutation_applied": (
                self.runtime_traffic_mutation_applied
            ),
            "candidate_state_mutation_applied": (
                self.candidate_state_mutation_applied
            ),
        }


@dataclass(frozen=True)
class HexCellActivationPreflight:
    """Receipt-bound preflight before any runtime authority commit.

    This artifact links the cleared operator-gate authorization to a verified
    SolverProvenance ``activation_authorised`` MAGMA receipt. It deliberately
    keeps runtime authority unapplied; the next gate is a separate commit step.
    """

    schema_version: str
    preflight_id: str
    authorization_id: str
    authorization_digest: str
    acceptance_id: str
    competition_id: str
    cell_id: str
    capability_id: str
    accepted_candidate_id: str
    solver_candidate_id: str
    solver_provenance_receipt_digest: str
    solver_provenance_receipt_event_id: str
    activation_transition: str
    activation_state: str
    preflight_digest: str
    evidence_digest_algorithm: str
    activation_preflight_status: str
    required_next_gate: str
    required_receipt_event_type: str
    receipt_bound_activation_verified: bool
    operator_gate_cleared: bool
    runtime_authority_granted: bool
    runtime_traffic_mutation_applied: bool
    candidate_state_mutation_applied: bool

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "preflight_id": self.preflight_id,
            "authorization_id": self.authorization_id,
            "authorization_digest": self.authorization_digest,
            "acceptance_id": self.acceptance_id,
            "competition_id": self.competition_id,
            "cell_id": self.cell_id,
            "capability_id": self.capability_id,
            "accepted_candidate_id": self.accepted_candidate_id,
            "solver_candidate_id": self.solver_candidate_id,
            "solver_provenance_receipt_digest": (
                self.solver_provenance_receipt_digest
            ),
            "solver_provenance_receipt_event_id": (
                self.solver_provenance_receipt_event_id
            ),
            "activation_transition": self.activation_transition,
            "activation_state": self.activation_state,
            "preflight_digest": self.preflight_digest,
            "evidence_digest_algorithm": self.evidence_digest_algorithm,
            "activation_preflight_status": (
                self.activation_preflight_status
            ),
            "required_next_gate": self.required_next_gate,
            "required_receipt_event_type": self.required_receipt_event_type,
            "receipt_bound_activation_verified": (
                self.receipt_bound_activation_verified
            ),
            "operator_gate_cleared": self.operator_gate_cleared,
            "runtime_authority_granted": self.runtime_authority_granted,
            "runtime_traffic_mutation_applied": (
                self.runtime_traffic_mutation_applied
            ),
            "candidate_state_mutation_applied": (
                self.candidate_state_mutation_applied
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

    evidence_payload = _competition_evidence_payload(
        cell_id=cell_id,
        capability_id=normalized_capability,
        candidate_scores=candidate_scores,
        winner_id=winner_id,
        loser_ids=loser_ids,
    )
    evidence_digest = sha256_digest(evidence_payload)
    competition_id = _competition_id(
        cell_id,
        normalized_capability,
        evidence_digest,
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


def build_hex_cell_promotion_acceptance(
    *,
    competition: HexCellCompetitionResult,
    accepted_candidate_id: str | None = None,
) -> HexCellPromotionAcceptance:
    """Build a non-authority acceptance handoff for the winning candidate.

    This function is the boundary between evidence-only competition and later
    operator-gated activation. It accepts only the deterministic winner, keeps
    operator_gate_cleared false, grants no runtime authority, and does not
    mutate candidate or runtime state.
    """
    _validate_competition_is_non_authority(competition)
    accepted_id = _require_non_empty(
        "accepted_candidate_id",
        accepted_candidate_id or competition.winner_id,
    )
    if accepted_id != competition.winner_id:
        raise ValueError(
            "hex-cell promotion acceptance can only accept the competition "
            f"winner {competition.winner_id!r}; got {accepted_id!r}"
        )

    rejected_ids = tuple(competition.loser_ids)
    payload = _promotion_acceptance_payload(
        competition_id=competition.competition_id,
        cell_id=competition.cell_id,
        capability_id=competition.capability_id,
        accepted_candidate_id=accepted_id,
        rejected_candidate_ids=rejected_ids,
        competition_evidence_digest=competition.evidence_digest,
    )
    acceptance_digest = sha256_digest(payload)
    acceptance_id = _promotion_acceptance_id(
        competition.cell_id,
        competition.capability_id,
        acceptance_digest,
    )
    return HexCellPromotionAcceptance(
        schema_version=HEX_CELL_PROMOTION_ACCEPTANCE_SCHEMA_VERSION,
        acceptance_id=acceptance_id,
        competition_id=competition.competition_id,
        cell_id=competition.cell_id,
        capability_id=competition.capability_id,
        accepted_candidate_id=accepted_id,
        rejected_candidate_ids=rejected_ids,
        competition_evidence_digest=competition.evidence_digest,
        acceptance_digest=acceptance_digest,
        evidence_digest_algorithm=HEX_CELL_COMPETITION_DIGEST_ALGORITHM,
        promotion_acceptance_status=HEX_CELL_PROMOTION_ACCEPTANCE_STATUS,
        required_next_gate=HEX_CELL_PROMOTION_ACCEPTANCE_NEXT_GATE,
        operator_gate_required=True,
        operator_gate_cleared=False,
        runtime_authority_granted=False,
        runtime_traffic_mutation_applied=False,
        candidate_state_mutation_applied=False,
    )


def build_hex_cell_operator_gate_authorization(
    *,
    acceptance: HexCellPromotionAcceptance,
    operator_approval_id: str,
    approved_by: str,
    acceptance_receipt_digest: str,
    operator_decision: str = "approved",
    operator_scope: str = HEX_CELL_PROMOTION_ACCEPTANCE_NEXT_GATE,
) -> HexCellOperatorGateAuthorization:
    """Build a deterministic operator-gate authorization artifact.

    The caller must provide the receipt digest for the earlier promotion
    acceptance before this handoff can be built. Duplicate retries with the
    same accepted candidate, approval, and receipt digest return the same
    authorization ID and digest, while runtime authority remains unapplied.
    """
    _validate_promotion_acceptance_for_operator_gate(acceptance)
    normalized_approval_id = _require_non_empty(
        "operator_approval_id",
        operator_approval_id,
    )
    normalized_approved_by = _require_non_empty("approved_by", approved_by)
    normalized_decision = _require_non_empty(
        "operator_decision",
        operator_decision,
    )
    if normalized_decision != "approved":
        raise ValueError(
            "hex-cell operator gate authorization requires "
            "operator_decision 'approved'"
        )
    normalized_scope = _require_non_empty("operator_scope", operator_scope)
    if normalized_scope != HEX_CELL_PROMOTION_ACCEPTANCE_NEXT_GATE:
        raise ValueError(
            "hex-cell operator gate authorization requires operator_scope "
            f"{HEX_CELL_PROMOTION_ACCEPTANCE_NEXT_GATE!r}; got "
            f"{normalized_scope!r}"
        )
    normalized_receipt_digest = _require_sha256_digest(
        "acceptance_receipt_digest",
        acceptance_receipt_digest,
    )
    expected_receipt_digest = _promotion_acceptance_receipt_digest(
        acceptance,
    )
    if normalized_receipt_digest != expected_receipt_digest:
        raise ValueError(
            "hex-cell operator gate authorization requires "
            "acceptance_receipt_digest to match the promotion acceptance"
        )

    payload = _operator_gate_authorization_payload(
        acceptance=acceptance,
        operator_approval_id=normalized_approval_id,
        approved_by=normalized_approved_by,
        operator_decision=normalized_decision,
        operator_scope=normalized_scope,
        acceptance_receipt_digest=normalized_receipt_digest,
    )
    authorization_digest = sha256_digest(payload)
    authorization_id = _operator_gate_authorization_id(
        acceptance.cell_id,
        acceptance.capability_id,
        authorization_digest,
    )
    return HexCellOperatorGateAuthorization(
        schema_version=(
            HEX_CELL_OPERATOR_GATE_AUTHORIZATION_SCHEMA_VERSION
        ),
        authorization_id=authorization_id,
        acceptance_id=acceptance.acceptance_id,
        competition_id=acceptance.competition_id,
        cell_id=acceptance.cell_id,
        capability_id=acceptance.capability_id,
        accepted_candidate_id=acceptance.accepted_candidate_id,
        rejected_candidate_ids=tuple(acceptance.rejected_candidate_ids),
        competition_evidence_digest=acceptance.competition_evidence_digest,
        acceptance_digest=acceptance.acceptance_digest,
        acceptance_receipt_digest=normalized_receipt_digest,
        authorization_digest=authorization_digest,
        evidence_digest_algorithm=HEX_CELL_COMPETITION_DIGEST_ALGORITHM,
        operator_approval_id=normalized_approval_id,
        approved_by=normalized_approved_by,
        operator_decision=normalized_decision,
        operator_scope=normalized_scope,
        authority_status=HEX_CELL_OPERATOR_GATE_AUTHORIZATION_STATUS,
        required_next_gate=HEX_CELL_OPERATOR_GATE_AUTHORIZATION_NEXT_GATE,
        required_receipt_event_type=(
            HEX_CELL_OPERATOR_GATE_AUTHORIZATION_RECEIPT_EVENT_TYPE
        ),
        receipt_ordering_enforced=True,
        authorization_receipt_required_before_runtime_authority=True,
        duplicate_retry_behavior=(
            HEX_CELL_OPERATOR_GATE_DUPLICATE_RETRY_BEHAVIOR
        ),
        operator_gate_required=True,
        operator_gate_cleared=True,
        operator_authorized_activation=True,
        runtime_authority_granted=False,
        runtime_traffic_mutation_applied=False,
        candidate_state_mutation_applied=False,
    )


def build_hex_cell_activation_preflight(
    *,
    authorization: HexCellOperatorGateAuthorization,
    solver_provenance_bundle: Mapping[str, Any],
) -> HexCellActivationPreflight:
    """Bind operator authorization to a SolverProvenance activation receipt.

    The preflight is intentionally a validation artifact, not an authority
    grant. It fail-closes on authorization drift, candidate mismatch,
    malformed receipt shape, digest mismatch, or a non-passing provenance
    activation receipt.
    """
    _validate_operator_gate_authorization_for_preflight(authorization)
    receipt_event_id = _validate_solver_provenance_activation_bundle(
        authorization=authorization,
        solver_provenance_bundle=solver_provenance_bundle,
    )
    receipt_digest = sha256_digest(
        _as_mapping("solver_provenance_bundle.receipt",
                    solver_provenance_bundle.get("receipt"))
    )

    payload = _activation_preflight_payload(
        authorization=authorization,
        solver_provenance_receipt_digest=receipt_digest,
        solver_provenance_receipt_event_id=receipt_event_id,
    )
    preflight_digest = sha256_digest(payload)
    preflight_id = _activation_preflight_id(
        authorization.cell_id,
        authorization.capability_id,
        preflight_digest,
    )
    return HexCellActivationPreflight(
        schema_version=HEX_CELL_ACTIVATION_PREFLIGHT_SCHEMA_VERSION,
        preflight_id=preflight_id,
        authorization_id=authorization.authorization_id,
        authorization_digest=authorization.authorization_digest,
        acceptance_id=authorization.acceptance_id,
        competition_id=authorization.competition_id,
        cell_id=authorization.cell_id,
        capability_id=authorization.capability_id,
        accepted_candidate_id=authorization.accepted_candidate_id,
        solver_candidate_id=authorization.accepted_candidate_id,
        solver_provenance_receipt_digest=receipt_digest,
        solver_provenance_receipt_event_id=receipt_event_id,
        activation_transition=HEX_CELL_ACTIVATION_PREFLIGHT_TRANSITION,
        activation_state=HEX_CELL_ACTIVATION_PREFLIGHT_NEW_STATE,
        preflight_digest=preflight_digest,
        evidence_digest_algorithm=HEX_CELL_COMPETITION_DIGEST_ALGORITHM,
        activation_preflight_status=HEX_CELL_ACTIVATION_PREFLIGHT_STATUS,
        required_next_gate=HEX_CELL_ACTIVATION_PREFLIGHT_NEXT_GATE,
        required_receipt_event_type=(
            HEX_CELL_ACTIVATION_PREFLIGHT_RECEIPT_EVENT_TYPE
        ),
        receipt_bound_activation_verified=True,
        operator_gate_cleared=True,
        runtime_authority_granted=False,
        runtime_traffic_mutation_applied=False,
        candidate_state_mutation_applied=False,
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


def _validate_competition_is_non_authority(
    competition: HexCellCompetitionResult,
) -> None:
    if competition.schema_version != HEX_CELL_COMPETITION_RESULT_SCHEMA_VERSION:
        raise ValueError(
            "hex-cell promotion acceptance requires competition schema "
            f"{HEX_CELL_COMPETITION_RESULT_SCHEMA_VERSION!r}; got "
            f"{competition.schema_version!r}"
        )
    if competition.authority_status != HEX_CELL_COMPETITION_AUTHORITY_STATUS:
        raise ValueError(
            "hex-cell promotion acceptance requires non-authority "
            f"competition; got {competition.authority_status!r}"
        )
    if competition.operator_gate_required_for_authority is not True:
        raise ValueError(
            "hex-cell promotion acceptance requires operator gate for "
            "authority"
        )
    if competition.runtime_traffic_mutation_applied:
        raise ValueError(
            "hex-cell promotion acceptance refuses mutated runtime traffic"
        )
    if competition.candidate_state_mutation_applied:
        raise ValueError(
            "hex-cell promotion acceptance refuses mutated candidate state"
        )
    _validate_competition_evidence_integrity(competition)


def _validate_competition_evidence_integrity(
    competition: HexCellCompetitionResult,
) -> None:
    candidate_scores = tuple(competition.candidate_scores)
    if len(candidate_scores) < 2:
        raise ValueError(
            "hex-cell promotion acceptance requires at least two "
            "competition candidate scores"
        )
    sorted_scores = tuple(
        sorted(candidate_scores, key=lambda row: row.candidate_id)
    )
    if candidate_scores != sorted_scores:
        raise ValueError(
            "hex-cell promotion acceptance requires candidate_scores sorted "
            "by candidate_id"
        )
    candidate_ids = [row.candidate_id for row in candidate_scores]
    duplicate_ids = sorted({
        candidate_id for candidate_id in candidate_ids
        if candidate_ids.count(candidate_id) > 1
    })
    if duplicate_ids:
        raise ValueError(
            "hex-cell promotion acceptance rejects duplicate candidate "
            f"scores: {duplicate_ids}"
        )
    for row in candidate_scores:
        if row.cell_id != competition.cell_id:
            raise ValueError(
                "hex-cell promotion acceptance requires candidate_scores "
                "to match competition cell_id"
            )
        if row.capability_id != competition.capability_id:
            raise ValueError(
                "hex-cell promotion acceptance requires candidate_scores "
                "to match competition capability_id"
            )

    ranked = sorted(
        candidate_scores,
        key=lambda row: (-row.score, row.candidate_id),
    )
    expected_winner_id = ranked[0].candidate_id
    expected_loser_ids = tuple(row.candidate_id for row in ranked[1:])
    if (
        competition.winner_id != expected_winner_id
        or tuple(competition.loser_ids) != expected_loser_ids
    ):
        raise ValueError(
            "hex-cell promotion acceptance requires competition "
            "winner/losers to match candidate_scores"
        )

    expected_digest = sha256_digest(_competition_evidence_payload(
        cell_id=competition.cell_id,
        capability_id=competition.capability_id,
        candidate_scores=candidate_scores,
        winner_id=competition.winner_id,
        loser_ids=tuple(competition.loser_ids),
    ))
    if competition.evidence_digest != expected_digest:
        raise ValueError(
            "hex-cell promotion acceptance requires competition "
            "evidence_digest to match current evidence"
        )

    expected_competition_id = _competition_id(
        competition.cell_id,
        competition.capability_id,
        competition.evidence_digest,
    )
    if competition.competition_id != expected_competition_id:
        raise ValueError(
            "hex-cell promotion acceptance requires competition_id to "
            "match evidence_digest"
        )


def _validate_promotion_acceptance_for_operator_gate(
    acceptance: HexCellPromotionAcceptance,
) -> None:
    if acceptance.schema_version != HEX_CELL_PROMOTION_ACCEPTANCE_SCHEMA_VERSION:
        raise ValueError(
            "hex-cell operator gate authorization requires promotion "
            f"acceptance schema {HEX_CELL_PROMOTION_ACCEPTANCE_SCHEMA_VERSION!r}; "
            f"got {acceptance.schema_version!r}"
        )
    if (
        acceptance.evidence_digest_algorithm
        != HEX_CELL_COMPETITION_DIGEST_ALGORITHM
    ):
        raise ValueError(
            "hex-cell operator gate authorization requires digest algorithm "
            f"{HEX_CELL_COMPETITION_DIGEST_ALGORITHM!r}; got "
            f"{acceptance.evidence_digest_algorithm!r}"
        )
    if acceptance.promotion_acceptance_status != (
        HEX_CELL_PROMOTION_ACCEPTANCE_STATUS
    ):
        raise ValueError(
            "hex-cell operator gate authorization requires acceptance "
            "status operator_gate_required"
        )
    if acceptance.required_next_gate != HEX_CELL_PROMOTION_ACCEPTANCE_NEXT_GATE:
        raise ValueError(
            "hex-cell operator gate authorization requires next gate "
            f"{HEX_CELL_PROMOTION_ACCEPTANCE_NEXT_GATE!r}; got "
            f"{acceptance.required_next_gate!r}"
        )
    if acceptance.operator_gate_required is not True:
        raise ValueError(
            "hex-cell operator gate authorization requires operator gate"
        )
    if acceptance.operator_gate_cleared:
        raise ValueError(
            "hex-cell operator gate authorization refuses pre-cleared "
            "operator gate"
        )
    if acceptance.runtime_authority_granted:
        raise ValueError(
            "hex-cell operator gate authorization refuses pre-granted "
            "runtime authority"
        )
    if acceptance.runtime_traffic_mutation_applied:
        raise ValueError(
            "hex-cell operator gate authorization refuses mutated "
            "runtime traffic"
        )
    if acceptance.candidate_state_mutation_applied:
        raise ValueError(
            "hex-cell operator gate authorization refuses mutated "
            "candidate state"
        )

    accepted_id = _require_non_empty(
        "accepted_candidate_id",
        acceptance.accepted_candidate_id,
    )
    rejected_ids = tuple(
        _require_non_empty("rejected_candidate_id", candidate_id)
        for candidate_id in acceptance.rejected_candidate_ids
    )
    if accepted_id in rejected_ids:
        raise ValueError(
            "hex-cell operator gate authorization refuses acceptance "
            "where accepted candidate is also rejected"
        )
    _require_non_empty("competition_id", acceptance.competition_id)
    _require_non_empty("cell_id", acceptance.cell_id)
    _require_non_empty("capability_id", acceptance.capability_id)
    _require_sha256_digest(
        "competition_evidence_digest",
        acceptance.competition_evidence_digest,
    )
    expected_competition_id = _competition_id(
        acceptance.cell_id,
        acceptance.capability_id,
        acceptance.competition_evidence_digest,
    )
    if acceptance.competition_id != expected_competition_id:
        raise ValueError(
            "hex-cell operator gate authorization requires competition_id "
            "to match competition_evidence_digest"
        )
    _require_sha256_digest("acceptance_digest", acceptance.acceptance_digest)

    expected_digest = sha256_digest(_promotion_acceptance_payload(
        competition_id=acceptance.competition_id,
        cell_id=acceptance.cell_id,
        capability_id=acceptance.capability_id,
        accepted_candidate_id=accepted_id,
        rejected_candidate_ids=rejected_ids,
        competition_evidence_digest=acceptance.competition_evidence_digest,
    ))
    if acceptance.acceptance_digest != expected_digest:
        raise ValueError(
            "hex-cell operator gate authorization requires acceptance_digest "
            "to match current acceptance fields"
        )

    expected_acceptance_id = _promotion_acceptance_id(
        acceptance.cell_id,
        acceptance.capability_id,
        acceptance.acceptance_digest,
    )
    if acceptance.acceptance_id != expected_acceptance_id:
        raise ValueError(
            "hex-cell operator gate authorization requires acceptance_id to "
            "match acceptance_digest"
        )


def _validate_operator_gate_authorization_for_preflight(
    authorization: HexCellOperatorGateAuthorization,
) -> None:
    if (
        authorization.schema_version
        != HEX_CELL_OPERATOR_GATE_AUTHORIZATION_SCHEMA_VERSION
    ):
        raise ValueError(
            "hex-cell activation preflight requires operator authorization "
            "schema "
            f"{HEX_CELL_OPERATOR_GATE_AUTHORIZATION_SCHEMA_VERSION!r}; got "
            f"{authorization.schema_version!r}"
        )
    if (
        authorization.evidence_digest_algorithm
        != HEX_CELL_COMPETITION_DIGEST_ALGORITHM
    ):
        raise ValueError(
            "hex-cell activation preflight requires digest algorithm "
            f"{HEX_CELL_COMPETITION_DIGEST_ALGORITHM!r}; got "
            f"{authorization.evidence_digest_algorithm!r}"
        )
    if authorization.authority_status != (
        HEX_CELL_OPERATOR_GATE_AUTHORIZATION_STATUS
    ):
        raise ValueError(
            "hex-cell activation preflight requires cleared operator gate "
            f"authorization; got {authorization.authority_status!r}"
        )
    if (
        authorization.required_next_gate
        != HEX_CELL_OPERATOR_GATE_AUTHORIZATION_NEXT_GATE
    ):
        raise ValueError(
            "hex-cell activation preflight requires next gate "
            f"{HEX_CELL_OPERATOR_GATE_AUTHORIZATION_NEXT_GATE!r}; got "
            f"{authorization.required_next_gate!r}"
        )
    if (
        authorization.required_receipt_event_type
        != HEX_CELL_OPERATOR_GATE_AUTHORIZATION_RECEIPT_EVENT_TYPE
    ):
        raise ValueError(
            "hex-cell activation preflight requires authorization receipt "
            "event type "
            f"{HEX_CELL_OPERATOR_GATE_AUTHORIZATION_RECEIPT_EVENT_TYPE!r}"
        )
    if authorization.receipt_ordering_enforced is not True:
        raise ValueError(
            "hex-cell activation preflight requires receipt ordering"
        )
    if (
        authorization.authorization_receipt_required_before_runtime_authority
        is not True
    ):
        raise ValueError(
            "hex-cell activation preflight requires authorization receipt "
            "before runtime authority"
        )
    if authorization.operator_gate_required is not True:
        raise ValueError(
            "hex-cell activation preflight requires operator gate"
        )
    if authorization.operator_gate_cleared is not True:
        raise ValueError(
            "hex-cell activation preflight requires cleared operator gate"
        )
    if authorization.operator_authorized_activation is not True:
        raise ValueError(
            "hex-cell activation preflight requires operator-authorized "
            "activation"
        )
    if authorization.runtime_authority_granted:
        raise ValueError(
            "hex-cell activation preflight refuses pre-granted runtime "
            "authority"
        )
    if authorization.runtime_traffic_mutation_applied:
        raise ValueError(
            "hex-cell activation preflight refuses mutated runtime traffic"
        )
    if authorization.candidate_state_mutation_applied:
        raise ValueError(
            "hex-cell activation preflight refuses mutated candidate state"
        )
    expected_digest = sha256_digest(
        _operator_gate_authorization_payload_from_authorization(
            authorization
        )
    )
    if authorization.authorization_digest != expected_digest:
        raise ValueError(
            "hex-cell activation preflight requires authorization_digest "
            "to match current authorization fields"
        )
    expected_id = _operator_gate_authorization_id(
        authorization.cell_id,
        authorization.capability_id,
        authorization.authorization_digest,
    )
    if authorization.authorization_id != expected_id:
        raise ValueError(
            "hex-cell activation preflight requires authorization_id to "
            "match authorization_digest"
        )


def _validate_solver_provenance_activation_bundle(
    *,
    authorization: HexCellOperatorGateAuthorization,
    solver_provenance_bundle: Mapping[str, Any],
) -> str:
    bundle = _as_mapping(
        "solver_provenance_bundle",
        solver_provenance_bundle,
    )
    payload = _as_mapping(
        "solver_provenance_bundle.payload",
        bundle.get("payload"),
    )
    evaluation = _as_mapping(
        "solver_provenance_bundle.evaluation_result",
        bundle.get("evaluation_result"),
    )
    receipt = _as_mapping(
        "solver_provenance_bundle.receipt",
        bundle.get("receipt"),
    )

    candidate_id = _require_mapping_string(
        "solver_provenance_bundle.payload.candidate_id",
        payload.get("candidate_id"),
    )
    if candidate_id != authorization.accepted_candidate_id:
        raise ValueError(
            "hex-cell activation preflight requires solver provenance "
            "candidate_id to match accepted_candidate_id"
        )
    if payload.get("schema_version") != (
        "solver_provenance_transition_payload.v1"
    ):
        raise ValueError(
            "hex-cell activation preflight requires solver provenance "
            "transition payload v1"
        )
    if payload.get("transition") != HEX_CELL_ACTIVATION_PREFLIGHT_TRANSITION:
        raise ValueError(
            "hex-cell activation preflight requires activation_authorised "
            "transition"
        )
    if payload.get("new_state") != HEX_CELL_ACTIVATION_PREFLIGHT_NEW_STATE:
        raise ValueError(
            "hex-cell activation preflight requires activated new_state"
        )
    if payload.get("verification_valid") is not True:
        raise ValueError(
            "hex-cell activation preflight requires valid solver provenance "
            "verification"
        )
    rco_digest = _require_mapping_sha256_digest(
        "solver_provenance_bundle.payload.rco_decision_digest",
        payload.get("rco_decision_digest"),
    )

    if evaluation.get("subject_type") != "solver":
        raise ValueError(
            "hex-cell activation preflight requires solver evaluation"
        )
    if evaluation.get("risk_class") != "local_artifact":
        raise ValueError(
            "hex-cell activation preflight requires local_artifact risk"
        )
    if evaluation.get("expected_gate") != "allow":
        raise ValueError(
            "hex-cell activation preflight requires expected_gate allow"
        )
    if evaluation.get("actual_gate") != "allow":
        raise ValueError(
            "hex-cell activation preflight requires actual_gate allow"
        )
    if evaluation.get("verdict") != "pass":
        raise ValueError(
            "hex-cell activation preflight requires pass verdict"
        )
    if evaluation.get("operator_required") is not False:
        raise ValueError(
            "hex-cell activation preflight requires non-external receipt"
        )
    payload_digest = sha256_digest(payload)
    if evaluation.get("target_digest") != payload_digest:
        raise ValueError(
            "hex-cell activation preflight requires evaluation target_digest "
            "to match payload"
        )

    event_id = _require_mapping_string(
        "solver_provenance_bundle.receipt.event_id",
        receipt.get("event_id"),
    )
    if (
        f":{HEX_CELL_ACTIVATION_PREFLIGHT_TRANSITION}:"
        not in event_id
    ):
        raise ValueError(
            "hex-cell activation preflight requires activation_authorised "
            "receipt event"
        )
    if receipt.get("receipt_version") != "magma.receipt.v1":
        raise ValueError(
            "hex-cell activation preflight requires MAGMA receipt v1"
        )
    if receipt.get("risk_class") != "local_artifact":
        raise ValueError(
            "hex-cell activation preflight requires local_artifact receipt"
        )
    if receipt.get("payload_visibility") != "digest_only":
        raise ValueError(
            "hex-cell activation preflight requires digest-only payload"
        )
    if receipt.get("operator_gate_required") is not False:
        raise ValueError(
            "hex-cell activation preflight requires receipt with no runtime "
            "operator gate"
        )
    if receipt.get("canonical_payload_digest") != payload_digest:
        raise ValueError(
            "hex-cell activation preflight requires receipt payload digest "
            "to match payload"
        )
    if receipt.get("evaluation_result_digest") != sha256_digest(evaluation):
        raise ValueError(
            "hex-cell activation preflight requires receipt evaluation digest "
            "to match evaluation_result"
        )
    if receipt.get("rco_decision_digest") != rco_digest:
        raise ValueError(
            "hex-cell activation preflight requires receipt rco digest to "
            "match payload"
        )
    return event_id


def _promotion_acceptance_payload(
    *,
    competition_id: str,
    cell_id: str,
    capability_id: str,
    accepted_candidate_id: str,
    rejected_candidate_ids: tuple[str, ...],
    competition_evidence_digest: str,
) -> dict:
    return {
        "schema_version": HEX_CELL_PROMOTION_ACCEPTANCE_SCHEMA_VERSION,
        "competition_id": competition_id,
        "cell_id": cell_id,
        "capability_id": capability_id,
        "accepted_candidate_id": accepted_candidate_id,
        "rejected_candidate_ids": list(rejected_candidate_ids),
        "competition_evidence_digest": competition_evidence_digest,
        "promotion_acceptance_status": HEX_CELL_PROMOTION_ACCEPTANCE_STATUS,
        "required_next_gate": HEX_CELL_PROMOTION_ACCEPTANCE_NEXT_GATE,
        "operator_gate_required": True,
        "operator_gate_cleared": False,
        "runtime_authority_granted": False,
        "runtime_traffic_mutation_applied": False,
        "candidate_state_mutation_applied": False,
    }


def _promotion_acceptance_receipt_digest(
    acceptance: HexCellPromotionAcceptance,
) -> str:
    return sha256_digest({
        "event_type": HEX_CELL_PROMOTION_ACCEPTANCE_RECEIPT_EVENT_TYPE,
        "acceptance_id": acceptance.acceptance_id,
        "acceptance_digest": acceptance.acceptance_digest,
    })


def _operator_gate_authorization_payload(
    *,
    acceptance: HexCellPromotionAcceptance,
    operator_approval_id: str,
    approved_by: str,
    operator_decision: str,
    operator_scope: str,
    acceptance_receipt_digest: str,
) -> dict:
    return {
        "schema_version": HEX_CELL_OPERATOR_GATE_AUTHORIZATION_SCHEMA_VERSION,
        "acceptance_id": acceptance.acceptance_id,
        "competition_id": acceptance.competition_id,
        "cell_id": acceptance.cell_id,
        "capability_id": acceptance.capability_id,
        "accepted_candidate_id": acceptance.accepted_candidate_id,
        "rejected_candidate_ids": list(acceptance.rejected_candidate_ids),
        "competition_evidence_digest": acceptance.competition_evidence_digest,
        "acceptance_digest": acceptance.acceptance_digest,
        "acceptance_receipt_digest": acceptance_receipt_digest,
        "evidence_digest_algorithm": HEX_CELL_COMPETITION_DIGEST_ALGORITHM,
        "operator_approval_id": operator_approval_id,
        "approved_by": approved_by,
        "operator_decision": operator_decision,
        "operator_scope": operator_scope,
        "authority_status": HEX_CELL_OPERATOR_GATE_AUTHORIZATION_STATUS,
        "required_next_gate": HEX_CELL_OPERATOR_GATE_AUTHORIZATION_NEXT_GATE,
        "required_receipt_event_type": (
            HEX_CELL_OPERATOR_GATE_AUTHORIZATION_RECEIPT_EVENT_TYPE
        ),
        "receipt_ordering_enforced": True,
        "authorization_receipt_required_before_runtime_authority": True,
        "duplicate_retry_behavior": (
            HEX_CELL_OPERATOR_GATE_DUPLICATE_RETRY_BEHAVIOR
        ),
        "operator_gate_required": True,
        "operator_gate_cleared": True,
        "operator_authorized_activation": True,
        "runtime_authority_granted": False,
        "runtime_traffic_mutation_applied": False,
        "candidate_state_mutation_applied": False,
    }


def _operator_gate_authorization_payload_from_authorization(
    authorization: HexCellOperatorGateAuthorization,
) -> dict:
    return {
        "schema_version": HEX_CELL_OPERATOR_GATE_AUTHORIZATION_SCHEMA_VERSION,
        "acceptance_id": authorization.acceptance_id,
        "competition_id": authorization.competition_id,
        "cell_id": authorization.cell_id,
        "capability_id": authorization.capability_id,
        "accepted_candidate_id": authorization.accepted_candidate_id,
        "rejected_candidate_ids": list(authorization.rejected_candidate_ids),
        "competition_evidence_digest": (
            authorization.competition_evidence_digest
        ),
        "acceptance_digest": authorization.acceptance_digest,
        "acceptance_receipt_digest": (
            authorization.acceptance_receipt_digest
        ),
        "evidence_digest_algorithm": HEX_CELL_COMPETITION_DIGEST_ALGORITHM,
        "operator_approval_id": authorization.operator_approval_id,
        "approved_by": authorization.approved_by,
        "operator_decision": authorization.operator_decision,
        "operator_scope": authorization.operator_scope,
        "authority_status": HEX_CELL_OPERATOR_GATE_AUTHORIZATION_STATUS,
        "required_next_gate": (
            HEX_CELL_OPERATOR_GATE_AUTHORIZATION_NEXT_GATE
        ),
        "required_receipt_event_type": (
            HEX_CELL_OPERATOR_GATE_AUTHORIZATION_RECEIPT_EVENT_TYPE
        ),
        "receipt_ordering_enforced": True,
        "authorization_receipt_required_before_runtime_authority": True,
        "duplicate_retry_behavior": (
            HEX_CELL_OPERATOR_GATE_DUPLICATE_RETRY_BEHAVIOR
        ),
        "operator_gate_required": True,
        "operator_gate_cleared": True,
        "operator_authorized_activation": True,
        "runtime_authority_granted": False,
        "runtime_traffic_mutation_applied": False,
        "candidate_state_mutation_applied": False,
    }


def _activation_preflight_payload(
    *,
    authorization: HexCellOperatorGateAuthorization,
    solver_provenance_receipt_digest: str,
    solver_provenance_receipt_event_id: str,
) -> dict:
    return {
        "schema_version": HEX_CELL_ACTIVATION_PREFLIGHT_SCHEMA_VERSION,
        "authorization_id": authorization.authorization_id,
        "authorization_digest": authorization.authorization_digest,
        "acceptance_id": authorization.acceptance_id,
        "competition_id": authorization.competition_id,
        "cell_id": authorization.cell_id,
        "capability_id": authorization.capability_id,
        "accepted_candidate_id": authorization.accepted_candidate_id,
        "solver_candidate_id": authorization.accepted_candidate_id,
        "solver_provenance_receipt_digest": (
            solver_provenance_receipt_digest
        ),
        "solver_provenance_receipt_event_id": (
            solver_provenance_receipt_event_id
        ),
        "activation_transition": HEX_CELL_ACTIVATION_PREFLIGHT_TRANSITION,
        "activation_state": HEX_CELL_ACTIVATION_PREFLIGHT_NEW_STATE,
        "activation_preflight_status": (
            HEX_CELL_ACTIVATION_PREFLIGHT_STATUS
        ),
        "required_next_gate": HEX_CELL_ACTIVATION_PREFLIGHT_NEXT_GATE,
        "required_receipt_event_type": (
            HEX_CELL_ACTIVATION_PREFLIGHT_RECEIPT_EVENT_TYPE
        ),
        "receipt_bound_activation_verified": True,
        "operator_gate_cleared": True,
        "runtime_authority_granted": False,
        "runtime_traffic_mutation_applied": False,
        "candidate_state_mutation_applied": False,
    }


def _competition_evidence_payload(
    *,
    cell_id: str,
    capability_id: str,
    candidate_scores: tuple[CandidateCompetitionScore, ...],
    winner_id: str,
    loser_ids: tuple[str, ...],
) -> dict:
    return {
        "schema_version": HEX_CELL_COMPETITION_RESULT_SCHEMA_VERSION,
        "cell_id": cell_id,
        "capability_id": capability_id,
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


def _competition_id(
    cell_id: str,
    capability_id: str,
    evidence_digest: str,
) -> str:
    return (
        "hexcellcmp:"
        f"{cell_id}:{capability_id}:"
        f"{evidence_digest.removeprefix('sha256:')[:16]}"
    )


def _promotion_acceptance_id(
    cell_id: str,
    capability_id: str,
    acceptance_digest: str,
) -> str:
    return (
        "hexcellaccept:"
        f"{cell_id}:{capability_id}:"
        f"{acceptance_digest.removeprefix('sha256:')[:16]}"
    )


def _operator_gate_authorization_id(
    cell_id: str,
    capability_id: str,
    authorization_digest: str,
) -> str:
    return (
        "hexcellauth:"
        f"{cell_id}:{capability_id}:"
        f"{authorization_digest.removeprefix('sha256:')[:16]}"
    )


def _activation_preflight_id(
    cell_id: str,
    capability_id: str,
    preflight_digest: str,
) -> str:
    return (
        "hexcellpreflight:"
        f"{cell_id}:{capability_id}:"
        f"{preflight_digest.removeprefix('sha256:')[:16]}"
    )


def _require_non_empty(field_name: str, value: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{field_name} must be non-empty")
    return normalized


def _require_sha256_digest(field_name: str, value: object) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a sha256 digest")
    normalized = _require_non_empty(field_name, value)
    if not normalized.startswith("sha256:"):
        raise ValueError(f"{field_name} must be a sha256 digest")
    suffix = normalized.removeprefix("sha256:")
    hexdigits = "0123456789abcdef"
    if len(suffix) != 64 or any(
        char not in hexdigits for char in suffix.lower()
    ):
        raise ValueError(f"{field_name} must be a sha256 digest")
    return normalized


def _as_mapping(field_name: str, value: object) -> Mapping[str, Any]:
    if not isinstance(value, MappingABC):
        raise ValueError(f"{field_name} must be a mapping")
    return value


def _require_mapping_string(field_name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _require_mapping_sha256_digest(field_name: str, value: object) -> str:
    return _require_sha256_digest(field_name, value)


__all__ = [
    "CandidateCompetitionScore",
    "HexCellActivationPreflight",
    "HexCellCompetitionResult",
    "HexCellOperatorGateAuthorization",
    "HexCellPromotionAcceptance",
    "HEX_CELL_ACTIVATION_PREFLIGHT_NEW_STATE",
    "HEX_CELL_ACTIVATION_PREFLIGHT_NEXT_GATE",
    "HEX_CELL_ACTIVATION_PREFLIGHT_RECEIPT_EVENT_TYPE",
    "HEX_CELL_ACTIVATION_PREFLIGHT_SCHEMA_VERSION",
    "HEX_CELL_ACTIVATION_PREFLIGHT_STATUS",
    "HEX_CELL_ACTIVATION_PREFLIGHT_TRANSITION",
    "HEX_CELL_COMPETITION_AUTHORITY_STATUS",
    "HEX_CELL_COMPETITION_DIGEST_ALGORITHM",
    "HEX_CELL_COMPETITION_RANKING_RULE",
    "HEX_CELL_COMPETITION_RESULT_SCHEMA_VERSION",
    "HEX_CELL_OPERATOR_GATE_AUTHORIZATION_NEXT_GATE",
    "HEX_CELL_OPERATOR_GATE_AUTHORIZATION_RECEIPT_EVENT_TYPE",
    "HEX_CELL_OPERATOR_GATE_AUTHORIZATION_SCHEMA_VERSION",
    "HEX_CELL_OPERATOR_GATE_AUTHORIZATION_STATUS",
    "HEX_CELL_OPERATOR_GATE_DUPLICATE_RETRY_BEHAVIOR",
    "HEX_CELL_PROMOTION_ACCEPTANCE_NEXT_GATE",
    "HEX_CELL_PROMOTION_ACCEPTANCE_RECEIPT_EVENT_TYPE",
    "HEX_CELL_PROMOTION_ACCEPTANCE_SCHEMA_VERSION",
    "HEX_CELL_PROMOTION_ACCEPTANCE_STATUS",
    "build_hex_cell_activation_preflight",
    "build_hex_cell_competition_result",
    "build_hex_cell_operator_gate_authorization",
    "build_hex_cell_promotion_acceptance",
]
