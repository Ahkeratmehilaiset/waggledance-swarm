# SPDX-License-Identifier: BUSL-1.1
# BUSL-Change-Date: 2030-12-31
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
# See LICENSE-BUSL.txt and LICENSE-CORE.md
"""Auto-promotion engine for the low-risk autogrowth lane.

The engine implements invariants I1-I9 of
``docs/architecture/LOW_RISK_AUTOGROWTH_POLICY.md``. A candidate is
either auto-promoted (status='auto_promoted') with a positive
``promotion_decisions`` row, or rejected with a negative one — never
partially activated. State writes happen in a single SQLite
transaction.

The engine is the only intended writer of ``status='auto_promoted'``
in the control plane. Rollback flips it back to ``status='deactivated'``
and records a separate decision row.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from waggledance.core.solver_synthesis.declarative_solver_spec import SolverSpec
from waggledance.core.solver_synthesis.deterministic_solver_compiler import (
    CompiledSolver,
    compile_spec,
)
from waggledance.core.storage.control_plane import (
    ControlPlaneDB,
    ControlPlaneError,
    PromotionDecisionRecord,
    SolverRecord,
)

from .low_risk_policy import is_low_risk_family
from .shadow_evaluator import (
    OracleFn,
    ShadowOutcome,
    ShadowSample,
    run_shadow_evaluation,
)
from .validation_runner import (
    ValidationCase,
    ValidationOutcome,
    run_validation,
)


PROMOTION_DECIDED_BY = "autopromotion_engine_v1"


@dataclass(frozen=True)
class PromotionRequest:
    spec: SolverSpec
    validation_cases: Sequence[ValidationCase]
    shadow_samples: Sequence[ShadowSample]
    oracle: OracleFn
    oracle_kind: str = "external_reference"


@dataclass(frozen=True)
class PromotionOutcome:
    decided: bool
    decision: str  # 'auto_promoted' | 'rejected' | 'rolled_back'
    solver_id: Optional[int]
    family_kind: str
    invariant_failed: Optional[str]
    validation: Optional[ValidationOutcome]
    shadow: Optional[ShadowOutcome]
    decision_record_id: Optional[int]
    error: Optional[str] = None


class AutoPromotionEngine:
    """Closed loop: validate → shadow → decide → persist."""

    def __init__(self, control_plane: ControlPlaneDB) -> None:
        self._cp = control_plane

    def evaluate_candidate(
        self, request: PromotionRequest
    ) -> PromotionOutcome:
        spec = request.spec
        family_kind = spec.family_kind

        # I1: family in allowlist
        if not is_low_risk_family(family_kind):
            return self._reject_pre_solver(
                family_kind, "I1_family_not_in_allowlist",
                error=f"family {family_kind!r} not in low-risk allowlist",
            )

        # I2: family policy exists with is_low_risk
        policy = self._cp.get_family_policy(family_kind)
        if policy is None:
            return self._reject_pre_solver(
                family_kind, "I2_family_policy_missing",
                error=(
                    f"no family_policies row for {family_kind!r}; "
                    "register it before auto-promotion"
                ),
            )
        if not policy.is_low_risk:
            return self._reject_pre_solver(
                family_kind, "I2_family_policy_not_low_risk",
                error=(
                    f"family_policies.is_low_risk is False for "
                    f"{family_kind!r}"
                ),
            )

        # I4: deterministic compile twice → same canonical_json
        try:
            compiled1 = compile_spec(spec)
            compiled2 = compile_spec(spec)
        except Exception as exc:  # noqa: BLE001
            return self._reject_pre_solver(
                family_kind, "I3_spec_compile_error",
                error=repr(exc),
            )
        if compiled1.canonical_json != compiled2.canonical_json:
            return self._reject_pre_solver(
                family_kind, "I4_compile_not_deterministic",
                error="canonical_json differs across two compile runs",
            )

        # I5: validation pass rate >= policy.min_validation_pass_rate
        validation = run_validation(spec, request.validation_cases)
        if validation.pass_rate < policy.min_validation_pass_rate:
            return self._reject_with_validation(
                family_kind, validation, "I5_validation_pass_rate_below_min",
            )

        # I6 + I7: shadow samples >= min_shadow_samples
        #         shadow agreement rate >= min_shadow_agreement_rate
        shadow = run_shadow_evaluation(
            spec, request.shadow_samples, request.oracle,
            oracle_kind=request.oracle_kind,
        )
        if shadow.sample_count < policy.min_shadow_samples:
            return self._reject_with_validation_shadow(
                family_kind, validation, shadow,
                "I6_shadow_sample_count_below_min",
            )
        if shadow.agreement_rate < policy.min_shadow_agreement_rate:
            return self._reject_with_validation_shadow(
                family_kind, validation, shadow,
                "I7_shadow_agreement_rate_below_min",
            )

        # I8: no existing solver with same name in auto_promoted
        existing = self._cp.get_solver(spec.solver_name)
        if (
            existing is not None
            and existing.status == "auto_promoted"
        ):
            return self._reject_pre_solver(
                family_kind, "I8_solver_already_auto_promoted",
                error=(
                    f"solver {spec.solver_name!r} is already auto_promoted"
                ),
            )

        # I9: promotion budget for family not exceeded
        already = self._cp.count_auto_promoted_for_family(family_kind)
        if already >= policy.max_auto_promote:
            return self._reject_with_validation_shadow(
                family_kind, validation, shadow,
                "I9_family_promotion_budget_exhausted",
            )

        # All invariants pass — persist promotion atomically
        return self._commit_promotion(
            spec=spec,
            compiled=compiled1,
            family_kind=family_kind,
            validation=validation,
            shadow=shadow,
        )

    def rollback(
        self,
        solver_name: str,
        rollback_reason: str,
    ) -> PromotionOutcome:
        """Flip an auto-promoted solver to deactivated and record decision."""

        existing = self._cp.get_solver(solver_name)
        if existing is None:
            return PromotionOutcome(
                decided=False,
                decision="rejected",
                solver_id=None,
                family_kind="<unknown>",
                invariant_failed="rollback_solver_not_found",
                validation=None,
                shadow=None,
                decision_record_id=None,
                error=f"unknown solver {solver_name!r}",
            )
        if existing.status != "auto_promoted":
            return PromotionOutcome(
                decided=False,
                decision="rejected",
                solver_id=existing.id,
                family_kind=self._family_kind_for_solver(existing),
                invariant_failed="rollback_solver_not_auto_promoted",
                validation=None,
                shadow=None,
                decision_record_id=None,
                error=(
                    f"solver {solver_name!r} status is {existing.status!r}, "
                    "not 'auto_promoted'"
                ),
            )
        family_kind = self._family_kind_for_solver(existing)

        # Single transaction: flip status + record decision
        with self._cp._lock:  # type: ignore[attr-defined]
            self._cp._conn.execute("BEGIN IMMEDIATE")  # type: ignore[attr-defined]
            try:
                self._cp.upsert_solver(
                    name=solver_name,
                    version=existing.version,
                    family_name=family_kind,
                    status="deactivated",
                    spec_hash=existing.spec_hash,
                    spec_path=existing.spec_path,
                )
                decision = self._cp.record_promotion_decision(
                    solver_id=existing.id,
                    family_kind=family_kind,
                    decision="rollback",
                    decided_by=PROMOTION_DECIDED_BY,
                    rollback_reason=rollback_reason,
                )
                self._cp._conn.execute("COMMIT")  # type: ignore[attr-defined]
            except Exception as exc:  # noqa: BLE001
                self._cp._conn.execute("ROLLBACK")  # type: ignore[attr-defined]
                raise ControlPlaneError(
                    f"rollback failed: {exc!r}"
                ) from exc
        return PromotionOutcome(
            decided=True,
            decision="rolled_back",
            solver_id=existing.id,
            family_kind=family_kind,
            invariant_failed=None,
            validation=None,
            shadow=None,
            decision_record_id=decision.id,
        )

    # -- internals -----------------------------------------------------

    def _reject_pre_solver(
        self,
        family_kind: str,
        invariant: str,
        *,
        error: str,
    ) -> PromotionOutcome:
        # No solver row yet exists at this point, so nothing to record
        # against. Caller-side logging uses the engine's return value.
        return PromotionOutcome(
            decided=False,
            decision="rejected",
            solver_id=None,
            family_kind=family_kind,
            invariant_failed=invariant,
            validation=None,
            shadow=None,
            decision_record_id=None,
            error=error,
        )

    def _reject_with_validation(
        self,
        family_kind: str,
        validation: ValidationOutcome,
        invariant: str,
    ) -> PromotionOutcome:
        return PromotionOutcome(
            decided=False,
            decision="rejected",
            solver_id=None,
            family_kind=family_kind,
            invariant_failed=invariant,
            validation=validation,
            shadow=None,
            decision_record_id=None,
        )

    def _reject_with_validation_shadow(
        self,
        family_kind: str,
        validation: ValidationOutcome,
        shadow: ShadowOutcome,
        invariant: str,
    ) -> PromotionOutcome:
        return PromotionOutcome(
            decided=False,
            decision="rejected",
            solver_id=None,
            family_kind=family_kind,
            invariant_failed=invariant,
            validation=validation,
            shadow=shadow,
            decision_record_id=None,
        )

    def _commit_promotion(
        self,
        *,
        spec: SolverSpec,
        compiled: CompiledSolver,
        family_kind: str,
        validation: ValidationOutcome,
        shadow: ShadowOutcome,
    ) -> PromotionOutcome:
        # Ensure family exists in solver_families
        self._cp.upsert_solver_family(family_kind, "1.0")

        with self._cp._lock:  # type: ignore[attr-defined]
            self._cp._conn.execute("BEGIN IMMEDIATE")  # type: ignore[attr-defined]
            try:
                solver = self._cp.upsert_solver(
                    name=spec.solver_name,
                    version="1.0",
                    family_name=family_kind,
                    status="auto_promoted",
                    spec_hash=compiled.artifact_id,
                )
                self._cp.upsert_solver_artifact(
                    solver_id=solver.id,
                    family_kind=family_kind,
                    artifact_id=compiled.artifact_id,
                    spec_canonical_json=compiled.canonical_json,
                    artifact_json=json.dumps(compiled.artifact, sort_keys=True),
                )
                val_run = self._cp.record_validation_run(
                    family_kind=family_kind,
                    solver_id=solver.id,
                    spec_hash=compiled.artifact_id,
                    case_count=validation.case_count,
                    pass_count=validation.pass_count,
                    fail_count=validation.fail_count,
                    status="completed",
                    evidence=json.dumps(
                        {"failures": list(validation.failures)[:5]}
                    ),
                )
                shadow_run = self._cp.record_shadow_evaluation(
                    family_kind=family_kind,
                    solver_id=solver.id,
                    spec_hash=compiled.artifact_id,
                    sample_count=shadow.sample_count,
                    agree_count=shadow.agree_count,
                    disagree_count=shadow.disagree_count,
                    oracle_kind=shadow.oracle_kind,
                    status="completed",
                    evidence=json.dumps(
                        {"disagreements": list(shadow.disagreements)[:5]}
                    ),
                )
                decision = self._cp.record_promotion_decision(
                    solver_id=solver.id,
                    family_kind=family_kind,
                    decision="auto_promoted",
                    decided_by=PROMOTION_DECIDED_BY,
                    validation_run_id=val_run.id,
                    shadow_evaluation_id=shadow_run.id,
                    evidence=json.dumps({
                        "validation_pass_rate": validation.pass_rate,
                        "shadow_agreement_rate": shadow.agreement_rate,
                    }),
                )
                self._cp._conn.execute("COMMIT")  # type: ignore[attr-defined]
            except Exception as exc:  # noqa: BLE001
                self._cp._conn.execute("ROLLBACK")  # type: ignore[attr-defined]
                raise ControlPlaneError(
                    f"auto-promotion commit failed: {exc!r}"
                ) from exc

        return PromotionOutcome(
            decided=True,
            decision="auto_promoted",
            solver_id=solver.id,
            family_kind=family_kind,
            invariant_failed=None,
            validation=validation,
            shadow=shadow,
            decision_record_id=decision.id,
        )

    def _family_kind_for_solver(self, solver: SolverRecord) -> str:
        if solver.family_id is None:
            return "<unknown>"
        with self._cp._lock:  # type: ignore[attr-defined]
            row = self._cp._conn.execute(  # type: ignore[attr-defined]
                "SELECT name FROM solver_families WHERE id = ?",
                (solver.family_id,),
            ).fetchone()
        return str(row["name"]) if row is not None else "<unknown>"


__all__ = [
    "AutoPromotionEngine",
    "PromotionRequest",
    "PromotionOutcome",
    "PROMOTION_DECIDED_BY",
]
