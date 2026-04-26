"""Rollback engine — Phase 9 §M.

Records rollback transitions. Rollback to any earlier stage (or to
archived) is always allowed; runtime rollback requires human approval
just like runtime promotion.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import RUNTIME_STAGES, STAGES
from .ladder import PromotionTransition, compute_transition_id, PROMOTION_SCHEMA_VERSION


@dataclass(frozen=True)
class RollbackPlan:
    transition_id: str
    ir_id: str
    from_stage: str
    rollback_target_stage: str
    ts_iso: str
    human_approval_id: str | None
    rationale: str
    no_runtime_auto_promotion: bool

    def to_dict(self) -> dict:
        return {
            "transition_id": self.transition_id,
            "ir_id": self.ir_id,
            "from_stage": self.from_stage,
            "rollback_target_stage": self.rollback_target_stage,
            "ts_iso": self.ts_iso,
            "human_approval_id": self.human_approval_id,
            "rationale": self.rationale,
            "no_runtime_auto_promotion": self.no_runtime_auto_promotion,
        }


class RollbackViolation(ValueError):
    pass


def plan_rollback(*,
                       ir_id: str,
                       from_stage: str,
                       rollback_target_stage: str,
                       ts_iso: str,
                       human_approval_id: str | None = None,
                       rationale: str = "",
                       ) -> RollbackPlan:
    """Validate + emit a RollbackPlan. Rolling back FROM a runtime
    stage requires human_approval_id."""
    if from_stage not in STAGES:
        raise RollbackViolation(f"unknown from_stage: {from_stage!r}")
    if rollback_target_stage not in STAGES:
        raise RollbackViolation(
            f"unknown rollback_target_stage: {rollback_target_stage!r}"
        )
    if rollback_target_stage == from_stage:
        raise RollbackViolation(
            "rollback_target_stage cannot equal from_stage"
        )
    if from_stage in RUNTIME_STAGES and not human_approval_id:
        raise RollbackViolation(
            "rolling back from a runtime stage requires "
            "human_approval_id"
        )
    return RollbackPlan(
        transition_id=compute_transition_id(
            ir_id=ir_id, from_stage=from_stage,
            to_stage=rollback_target_stage, ts_iso=ts_iso,
        ),
        ir_id=ir_id,
        from_stage=from_stage,
        rollback_target_stage=rollback_target_stage,
        ts_iso=ts_iso,
        human_approval_id=human_approval_id,
        rationale=rationale,
        no_runtime_auto_promotion=True,
    )
