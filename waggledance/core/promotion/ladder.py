# SPDX-License-Identifier: BUSL-1.1
"""Promotion ladder — Phase 9 §M.

Owns stage transitions. NO automatic runtime promotion. NO bypasses.
Every transition into a runtime stage requires human_approval_id and
the SEPARATE Phase Z prompt.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from . import (
    PROMOTION_SCHEMA_VERSION,
    RUNTIME_STAGES,
    STAGE_CRITERIA,
    STAGES,
)
from . import stage_validators as sv


@dataclass(frozen=True)
class PromotionTransition:
    schema_version: int
    transition_id: str
    ir_id: str
    from_stage: str
    to_stage: str
    criteria_satisfied: tuple[str, ...]
    criteria_failed: tuple[str, ...]
    ts_iso: str
    human_approval_id: str | None
    rollback_target_stage: str | None
    no_runtime_auto_promotion: bool
    no_bypass: bool
    rationale: str

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "transition_id": self.transition_id,
            "ir_id": self.ir_id,
            "from_stage": self.from_stage,
            "to_stage": self.to_stage,
            "criteria_satisfied": list(self.criteria_satisfied),
            "criteria_failed": list(self.criteria_failed),
            "ts_iso": self.ts_iso,
            "human_approval_id": self.human_approval_id,
            "rollback_target_stage": self.rollback_target_stage,
            "no_runtime_auto_promotion": self.no_runtime_auto_promotion,
            "no_bypass": self.no_bypass,
            "rationale": self.rationale,
        }


def compute_transition_id(*, ir_id: str, from_stage: str,
                                  to_stage: str, ts_iso: str) -> str:
    canonical = json.dumps({
        "ir_id": ir_id, "from_stage": from_stage,
        "to_stage": to_stage, "ts_iso": ts_iso,
    }, sort_keys=True, separators=(",", ":"))
    return "tr_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:10]


class PromotionViolation(ValueError):
    """Raised when a promotion attempt violates the ladder contract."""


def attempt_promotion(*,
                              ir_id: str,
                              from_stage: str,
                              to_stage: str,
                              ts_iso: str,
                              ctx: dict | None = None,
                              ) -> PromotionTransition:
    """Run all required validators for `to_stage` against `ctx`.

    Returns a PromotionTransition recording exact criteria results.
    Refuses runtime stages without human_approval_id REGARDLESS of
    other criteria.
    """
    if to_stage not in STAGES:
        raise PromotionViolation(f"unknown to_stage: {to_stage!r}")
    if from_stage not in STAGES:
        raise PromotionViolation(f"unknown from_stage: {from_stage!r}")
    ctx = dict(ctx or {})
    ctx.setdefault("from_stage", from_stage)
    required = STAGE_CRITERIA.get(to_stage, ())
    satisfied, failed = sv.run_all(required, ctx)

    # Hard rule: runtime stages require human_approval_id
    if to_stage in RUNTIME_STAGES:
        if not ctx.get("human_approval_id"):
            failed = list(failed)
            failed.append("human_approval_id_required_for_runtime_stage")

    rationale = (
        f"transition {from_stage}→{to_stage}: "
        f"{len(satisfied)}/{len(required)} criteria satisfied"
    )
    if failed:
        rationale += f"; failed: {sorted(set(failed))}"

    return PromotionTransition(
        schema_version=PROMOTION_SCHEMA_VERSION,
        transition_id=compute_transition_id(
            ir_id=ir_id, from_stage=from_stage,
            to_stage=to_stage, ts_iso=ts_iso,
        ),
        ir_id=ir_id, from_stage=from_stage, to_stage=to_stage,
        criteria_satisfied=tuple(satisfied),
        criteria_failed=tuple(failed),
        ts_iso=ts_iso,
        human_approval_id=ctx.get("human_approval_id"),
        rollback_target_stage=None,
        no_runtime_auto_promotion=True,
        no_bypass=True,
        rationale=rationale,
    )


def transition_admitted(t: PromotionTransition) -> bool:
    """A transition is admitted iff zero criteria failed."""
    return len(t.criteria_failed) == 0


# ── Bypass detection ─────────────────────────────────────────────-

def detect_bypass(transition: PromotionTransition) -> str | None:
    """Return a bypass-violation message if from→to skips required
    intermediate stages, else None.

    The expected linear path is the order of STAGES (excluding
    archived). Any to_stage past from_stage by > 1 step is a bypass.
    """
    expected_chain = [s for s in STAGES if s != "archived"]
    try:
        f_idx = expected_chain.index(transition.from_stage)
        t_idx = expected_chain.index(transition.to_stage)
    except ValueError:
        return None   # archived can be reached from anywhere
    if t_idx <= f_idx:
        return None   # rollback / archive handled elsewhere
    if t_idx - f_idx > 1:
        return (
            f"bypass detected: {transition.from_stage}→"
            f"{transition.to_stage} skips "
            f"{expected_chain[f_idx + 1:t_idx]}"
        )
    return None
