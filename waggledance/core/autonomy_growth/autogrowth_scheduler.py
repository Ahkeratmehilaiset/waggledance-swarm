# SPDX-License-Identifier: BUSL-1.1
# BUSL-Change-Date: 2030-12-31
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
# See LICENSE-BUSL.txt and LICENSE-CORE.md
"""Self-starting autogrowth scheduler (Phase 12).

The scheduler is the consumer side of the gap-intake → growth-intents →
autogrowth_queue pipeline. A single :func:`tick` call:

1. claims the next ready queue row;
2. resolves the intent's spec seed into a Phase 11 :class:`GapInput`;
3. selects the family's reference oracle from
   :mod:`waggledance.core.autonomy_growth.family_oracles`;
4. invokes :class:`LowRiskGrower.grow_from_gap` (Phase 11 closed loop);
5. records an :class:`AutogrowthRunRecord` capturing the outcome, the
   linked promotion / validation / shadow rows;
6. emits append-only ``growth_events`` reflecting the attempt;
7. completes the queue row with optional backoff on failure;
8. updates the intent's status accordingly.

The scheduler does *not* spawn a daemon by itself. The caller drives
ticks (one tick per call). For a real runtime, a small loop or
periodic task can call :meth:`run_until_idle` to drain the queue.
That keeps the scope tight per RULE 10/13: bounded, observable, and
trivially halt-able.
"""
from __future__ import annotations

import json
import socket
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from waggledance.core.storage.control_plane import (
    AutogrowthQueueRecord,
    AutogrowthRunRecord,
    ControlPlaneDB,
    GrowthIntentRecord,
)

from .family_oracles import FAMILY_ORACLES
from .low_risk_grower import GapInput, GapOutcome, LowRiskGrower
from .low_risk_policy import is_low_risk_family


# Outcome strings for autogrowth_runs.outcome
OUTCOME_AUTO_PROMOTED = "auto_promoted"
OUTCOME_REJECTED = "rejected"
OUTCOME_SPEC_INVALID = "spec_invalid"
OUTCOME_NO_INTENT = "no_intent"
OUTCOME_NO_ORACLE = "no_oracle"
OUTCOME_BAD_SEED = "bad_seed"
OUTCOME_FAMILY_NOT_LOW_RISK = "family_not_low_risk"


@dataclass(frozen=True)
class TickResult:
    """Outcome of a single :meth:`AutogrowthScheduler.tick`."""

    claimed: bool
    queue_row_id: Optional[int] = None
    intent_id: Optional[int] = None
    family_kind: Optional[str] = None
    cell_coord: Optional[str] = None
    outcome: Optional[str] = None
    promotion_decision_id: Optional[int] = None
    solver_id: Optional[int] = None
    autogrowth_run_id: Optional[int] = None
    error: Optional[str] = None


@dataclass
class SchedulerStats:
    ticks_total: int = 0
    ticks_idle: int = 0
    auto_promoted: int = 0
    rejected: int = 0
    errored: int = 0
    by_family_promoted: dict[str, int] = field(default_factory=dict)


class AutogrowthScheduler:
    """Local-first self-starting autonomy loop consumer."""

    def __init__(
        self,
        control_plane: ControlPlaneDB,
        *,
        grower: Optional[LowRiskGrower] = None,
        scheduler_id: Optional[str] = None,
        failure_backoff_seconds: float = 60.0,
        max_attempts_before_park: int = 3,
    ) -> None:
        self._cp = control_plane
        self._grower = grower if grower is not None else LowRiskGrower(
            control_plane
        )
        self._scheduler_id = (
            scheduler_id
            if scheduler_id is not None
            else f"autogrowth_scheduler:{socket.gethostname()}:{uuid.uuid4().hex[:8]}"
        )
        self._failure_backoff_seconds = float(failure_backoff_seconds)
        self._max_attempts_before_park = int(max_attempts_before_park)
        self._stats = SchedulerStats()

    @property
    def scheduler_id(self) -> str:
        return self._scheduler_id

    @property
    def stats(self) -> SchedulerStats:
        return self._stats

    def tick(self) -> TickResult:
        self._stats.ticks_total += 1
        claimed = self._cp.claim_next_queue_row(self._scheduler_id)
        if claimed is None:
            self._stats.ticks_idle += 1
            return TickResult(claimed=False)

        intent = self._cp.get_growth_intent(claimed.intent_id)
        if intent is None:
            run = self._record_run(
                claimed, None, OUTCOME_NO_INTENT,
                family_kind="<unknown>",
                cell_coord=None,
                error=f"intent {claimed.intent_id} missing",
            )
            self._cp.complete_queue_row(
                claimed.id,
                status="failed",
                last_error="intent_missing",
            )
            self._stats.errored += 1
            return TickResult(
                claimed=True,
                queue_row_id=claimed.id,
                intent_id=claimed.intent_id,
                outcome=OUTCOME_NO_INTENT,
                autogrowth_run_id=run.id,
                error="intent_missing",
            )

        if not is_low_risk_family(intent.family_kind):
            run = self._record_run(
                claimed, intent, OUTCOME_FAMILY_NOT_LOW_RISK,
                family_kind=intent.family_kind,
                cell_coord=intent.cell_coord,
                error=f"family {intent.family_kind!r} not in low-risk allowlist",
            )
            self._complete_intent_failure(
                claimed, intent, "family_not_low_risk",
                terminal=True,
            )
            self._stats.errored += 1
            return TickResult(
                claimed=True,
                queue_row_id=claimed.id,
                intent_id=intent.id,
                family_kind=intent.family_kind,
                cell_coord=intent.cell_coord,
                outcome=OUTCOME_FAMILY_NOT_LOW_RISK,
                autogrowth_run_id=run.id,
                error="family_not_low_risk",
            )

        # Build a GapInput from intent spec_seed_json
        try:
            gap = self._build_gap_input(intent)
        except (ValueError, KeyError, TypeError) as exc:
            run = self._record_run(
                claimed, intent, OUTCOME_BAD_SEED,
                family_kind=intent.family_kind,
                cell_coord=intent.cell_coord,
                error=f"spec_seed_json malformed: {exc!s}",
            )
            self._complete_intent_failure(
                claimed, intent, "bad_seed", terminal=True,
            )
            self._stats.errored += 1
            return TickResult(
                claimed=True, queue_row_id=claimed.id, intent_id=intent.id,
                family_kind=intent.family_kind,
                cell_coord=intent.cell_coord,
                outcome=OUTCOME_BAD_SEED,
                autogrowth_run_id=run.id,
                error=str(exc),
            )

        if gap.family_kind not in FAMILY_ORACLES:
            run = self._record_run(
                claimed, intent, OUTCOME_NO_ORACLE,
                family_kind=intent.family_kind,
                cell_coord=intent.cell_coord,
                error=f"no oracle registered for {gap.family_kind!r}",
            )
            self._complete_intent_failure(
                claimed, intent, "no_oracle", terminal=True,
            )
            self._stats.errored += 1
            return TickResult(
                claimed=True, queue_row_id=claimed.id, intent_id=intent.id,
                family_kind=intent.family_kind,
                cell_coord=intent.cell_coord,
                outcome=OUTCOME_NO_ORACLE,
                autogrowth_run_id=run.id,
                error=f"no_oracle:{gap.family_kind}",
            )

        outcome: GapOutcome = self._grower.grow_from_gap(gap)
        promotion = outcome.promotion

        if outcome.accepted and outcome.reason == "auto_promoted":
            run = self._record_run(
                claimed, intent, OUTCOME_AUTO_PROMOTED,
                family_kind=gap.family_kind,
                cell_coord=gap.cell_id,
                solver_id=(promotion.solver_id if promotion else None),
                promotion_decision_id=(
                    promotion.decision_record_id if promotion else None
                ),
                evidence=json.dumps({
                    "validation_pass_rate": (
                        promotion.validation.pass_rate
                        if promotion and promotion.validation else None
                    ),
                    "shadow_agreement_rate": (
                        promotion.shadow.agreement_rate
                        if promotion and promotion.shadow else None
                    ),
                }),
            )
            self._cp.complete_queue_row(claimed.id, status="completed")
            self._cp.set_growth_intent_status(intent.id, "fulfilled")
            self._cp.emit_growth_event(
                "solver_auto_promoted",
                entity_kind="solver",
                entity_id=(promotion.solver_id if promotion else None),
                family_kind=gap.family_kind,
                cell_coord=gap.cell_id,
                payload=json.dumps({"intent_id": intent.id}),
            )
            self._stats.auto_promoted += 1
            self._stats.by_family_promoted[gap.family_kind] = (
                self._stats.by_family_promoted.get(gap.family_kind, 0) + 1
            )
            return TickResult(
                claimed=True,
                queue_row_id=claimed.id,
                intent_id=intent.id,
                family_kind=gap.family_kind,
                cell_coord=gap.cell_id,
                outcome=OUTCOME_AUTO_PROMOTED,
                promotion_decision_id=(
                    promotion.decision_record_id if promotion else None
                ),
                solver_id=(promotion.solver_id if promotion else None),
                autogrowth_run_id=run.id,
            )

        # Rejected (or spec_invalid). Decide between transient backoff
        # and terminal park based on attempt count.
        rejection_outcome = (
            OUTCOME_SPEC_INVALID
            if outcome.reason == "spec_invalid"
            else OUTCOME_REJECTED
        )
        run = self._record_run(
            claimed, intent, rejection_outcome,
            family_kind=gap.family_kind,
            cell_coord=gap.cell_id,
            promotion_decision_id=(
                promotion.decision_record_id if promotion else None
            ),
            error=(outcome.error or outcome.reason),
            evidence=outcome.reason,
        )
        # Rejection is deterministic given the spec seed — re-attempting
        # the same seed yields the same outcome. Terminal park; the
        # operator (or a future intent rewrite) can re-seed and re-queue.
        self._complete_intent_failure(
            claimed, intent, outcome.reason, terminal=True,
        )
        self._stats.rejected += 1
        return TickResult(
            claimed=True,
            queue_row_id=claimed.id,
            intent_id=intent.id,
            family_kind=gap.family_kind,
            cell_coord=gap.cell_id,
            outcome=rejection_outcome,
            autogrowth_run_id=run.id,
            error=outcome.error,
        )

    def run_until_idle(
        self, *, max_ticks: int = 200, sleep_seconds: float = 0.0
    ) -> int:
        """Drive ticks until the queue reports idle or max_ticks is hit.

        Returns the number of non-idle ticks executed.
        """

        non_idle = 0
        for _ in range(int(max_ticks)):
            result = self.tick()
            if not result.claimed:
                break
            non_idle += 1
            if sleep_seconds > 0:
                time.sleep(float(sleep_seconds))
        return non_idle

    # -- internals -----------------------------------------------------

    def _build_gap_input(self, intent: GrowthIntentRecord) -> GapInput:
        if not intent.spec_seed_json:
            raise ValueError("intent missing spec_seed_json")
        seed = json.loads(intent.spec_seed_json)
        spec = seed.get("spec")
        if not isinstance(spec, dict):
            raise ValueError("seed['spec'] must be a dict")
        validation_cases = seed.get("validation_cases", [])
        shadow_samples = seed.get("shadow_samples", [])
        solver_seed = seed.get("solver_name_seed") or "solver"
        # Derive a unique solver_name from the intent id to avoid
        # collisions across re-attempts/re-keys.
        solver_name = f"{solver_seed}_i{intent.id:04d}"
        cell_id = (
            seed.get("cell_id")
            or intent.cell_coord
            or "general"
        )
        oracle = FAMILY_ORACLES[intent.family_kind]
        return GapInput(
            family_kind=intent.family_kind,
            solver_name=solver_name,
            cell_id=cell_id,
            spec=spec,
            source=seed.get("source", "runtime_gap_intake"),
            source_kind=seed.get("source_kind", "self_starting_v1"),
            validation_cases=list(validation_cases),
            shadow_samples=list(shadow_samples),
            oracle=oracle,
            oracle_kind=seed.get("oracle_kind", "family_reference_oracle"),
        )

    def _record_run(
        self,
        queue_row: AutogrowthQueueRecord,
        intent: Optional[GrowthIntentRecord],
        outcome: str,
        *,
        family_kind: str,
        cell_coord: Optional[str],
        promotion_decision_id: Optional[int] = None,
        solver_id: Optional[int] = None,
        error: Optional[str] = None,
        evidence: Optional[str] = None,
    ) -> AutogrowthRunRecord:
        return self._cp.record_autogrowth_run(
            family_kind=family_kind,
            outcome=outcome,
            intent_id=intent.id if intent is not None else None,
            queue_row_id=queue_row.id,
            promotion_decision_id=promotion_decision_id,
            cell_coord=cell_coord,
            solver_id=solver_id,
            error=error,
            evidence=evidence,
        )

    def _complete_intent_failure(
        self,
        queue_row: AutogrowthQueueRecord,
        intent: GrowthIntentRecord,
        reason: str,
        *,
        terminal: bool,
    ) -> None:
        if terminal:
            self._cp.complete_queue_row(
                queue_row.id, status="failed", last_error=reason,
            )
            self._cp.set_growth_intent_status(intent.id, "rejected")
        else:
            # Re-queue with backoff so the same row is retried later.
            self._cp.complete_queue_row(
                queue_row.id,
                status="queued",
                last_error=reason,
                backoff_seconds=self._failure_backoff_seconds,
            )


__all__ = [
    "AutogrowthScheduler",
    "SchedulerStats",
    "TickResult",
    "OUTCOME_AUTO_PROMOTED",
    "OUTCOME_REJECTED",
    "OUTCOME_SPEC_INVALID",
    "OUTCOME_NO_INTENT",
    "OUTCOME_NO_ORACLE",
    "OUTCOME_BAD_SEED",
    "OUTCOME_FAMILY_NOT_LOW_RISK",
]
