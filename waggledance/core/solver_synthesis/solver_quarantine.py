"""Solver quarantine — Phase 9 §U3.

Per-day quota enforcement for raw_candidate generation, shadow
admission, review_ready, and final_approval transitions. Per
Prompt_1_Master §U3 CRITICAL CALIBRATION OVERLOAD RULE.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from .solver_candidate_store import (
    DEFAULT_QUOTAS,
    SolverCandidate,
    SolverCandidateStore,
)


@dataclass
class QuotaState:
    quotas: dict[str, int] = field(
        default_factory=lambda: dict(DEFAULT_QUOTAS)
    )
    consumed_today: dict[str, int] = field(default_factory=dict)

    def remaining(self, quota_key: str) -> int:
        return max(0, self.quotas.get(quota_key, 0)
                    - self.consumed_today.get(quota_key, 0))

    def can_consume(self, quota_key: str, amount: int = 1) -> bool:
        return self.remaining(quota_key) >= amount

    def consume(self, quota_key: str,
                  amount: int = 1) -> tuple["QuotaState", bool]:
        if not self.can_consume(quota_key, amount):
            return self, False
        self.consumed_today[quota_key] = (
            self.consumed_today.get(quota_key, 0) + amount
        )
        return self, True

    def reset_daily(self) -> "QuotaState":
        self.consumed_today.clear()
        return self

    def to_dict(self) -> dict:
        return {
            "quotas": dict(sorted(self.quotas.items())),
            "consumed_today": dict(sorted(self.consumed_today.items())),
            "remaining": {k: self.remaining(k)
                            for k in sorted(self.quotas)},
        }


# ── Admission policy ─────────────────────────────────────────────-

@dataclass(frozen=True)
class AdmissionDecision:
    candidate_id: str
    admitted: bool
    target_state: str
    rationale: str

    def to_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id,
            "admitted": self.admitted,
            "target_state": self.target_state,
            "rationale": self.rationale,
        }


def admit_to_shadow(state: QuotaState,
                         candidate: SolverCandidate
                         ) -> tuple[QuotaState, AdmissionDecision]:
    """Move candidate raw_candidate → shadow_only iff quota allows."""
    if candidate.state != "test_valid":
        return state, AdmissionDecision(
            candidate_id=candidate.candidate_id, admitted=False,
            target_state=candidate.state,
            rationale=(
                f"shadow admission requires state=test_valid; "
                f"got {candidate.state!r}"
            ),
        )
    state, ok = state.consume("max_new_shadow_solvers_per_day", 1)
    if not ok:
        return state, AdmissionDecision(
            candidate_id=candidate.candidate_id, admitted=False,
            target_state=candidate.state,
            rationale="daily shadow admission quota exhausted",
        )
    return state, AdmissionDecision(
        candidate_id=candidate.candidate_id, admitted=True,
        target_state="shadow_only",
        rationale="shadow admission granted",
    )


def admit_to_review_ready(state: QuotaState,
                                 candidate: SolverCandidate
                                 ) -> tuple[QuotaState, AdmissionDecision]:
    if candidate.state != "cold_solver":
        return state, AdmissionDecision(
            candidate_id=candidate.candidate_id, admitted=False,
            target_state=candidate.state,
            rationale=(
                f"review_ready promotion requires state=cold_solver; "
                f"got {candidate.state!r}"
            ),
        )
    state, ok = state.consume("max_review_ready_solvers_per_day", 1)
    if not ok:
        return state, AdmissionDecision(
            candidate_id=candidate.candidate_id, admitted=False,
            target_state=candidate.state,
            rationale="daily review_ready quota exhausted",
        )
    return state, AdmissionDecision(
        candidate_id=candidate.candidate_id, admitted=True,
        target_state="review_ready",
        rationale="review_ready promotion granted",
    )


def admit_to_approved(state: QuotaState,
                            candidate: SolverCandidate,
                            *,
                            human_approval_id: str | None = None,
                            ) -> tuple[QuotaState, AdmissionDecision]:
    """Final approval is GATED on explicit human_approval_id per
    constitution.no_foundational_auto_promotion."""
    if not human_approval_id:
        return state, AdmissionDecision(
            candidate_id=candidate.candidate_id, admitted=False,
            target_state=candidate.state,
            rationale=(
                "final approval requires explicit human_approval_id "
                "(constitution.no_foundational_auto_promotion)"
            ),
        )
    if candidate.state != "review_ready":
        return state, AdmissionDecision(
            candidate_id=candidate.candidate_id, admitted=False,
            target_state=candidate.state,
            rationale=(
                f"approval requires state=review_ready; "
                f"got {candidate.state!r}"
            ),
        )
    state, ok = state.consume("max_final_approvals_per_day", 1)
    if not ok:
        return state, AdmissionDecision(
            candidate_id=candidate.candidate_id, admitted=False,
            target_state=candidate.state,
            rationale="daily final approval quota exhausted",
        )
    return state, AdmissionDecision(
        candidate_id=candidate.candidate_id, admitted=True,
        target_state="approved",
        rationale=f"approved by {human_approval_id}",
    )
