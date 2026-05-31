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
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Sequence

from waggledance.core.magma.canonical import sha256_digest
from waggledance.core.magma.adversarial_gate import verify_adversarial_corpus_gate
from waggledance.core.magma.adversarial_corpus_eval import (
    AdversarialCorpusEvalError,
    REQUIRED_DEFECT_TYPES,
    run_adversarial_corpus_evaluation,
)
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

from .family_features import extract_features
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
PROMOTION_POLICY_VERSION = "policy:auto_promotion_engine:v1"
PROMOTION_CHARTER_VERSION = "charter:v1"
PROMOTION_DOMAIN_THRESHOLD_VERSION = "threshold:auto_promotion_engine:v1"

# T5b: minimum adversarial-corpus size the I11 gate requires. The floor now
# derives from ``len(REQUIRED_DEFECT_TYPES)`` to avoid stale hardcoded thresholds.
ADVERSARIAL_CORPUS_MIN_CASES = len(REQUIRED_DEFECT_TYPES)


@dataclass(frozen=True)
class PromotionRequest:
    spec: SolverSpec
    validation_cases: Sequence[ValidationCase]
    shadow_samples: Sequence[ShadowSample]
    oracle: OracleFn
    oracle_kind: str = "external_reference"
    # T5b adversarial-corpus gate (I11), ON by default + fail-closed. When no
    # report is supplied the engine RUNS the corpus eval inline, bound to the
    # exact artifact being committed, so promotions stay gated AND keep
    # working. require_adversarial_gate may be set False ONLY in tests;
    # disabling it in production is on the charter code-pattern denylist.
    require_adversarial_gate: bool = True
    adversarial_eval_report: Optional[Any] = None


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
    promotion_decision_artifact: Optional[dict[str, Any]] = None
    promotion_decision_digest: Optional[str] = None


class AutoPromotionReceiptEmissionError(ControlPlaneError):
    """Raised when an opt-in promotion receipt sink fails after commit.

    The sink runs after SQLite commit so emitted receipts and receipt-head
    advancement only describe durable control-plane state. A sink failure does
    not roll back the already-committed promotion or rollback decision.
    """


class AutoPromotionEngine:
    """Closed loop: validate → shadow → decide → persist."""

    def __init__(
        self,
        control_plane: ControlPlaneDB,
        *,
        emit_receipt_bundle: Optional[Callable[[dict[str, Any]], None]] = None,
    ) -> None:
        self._cp = control_plane
        self.emit_receipt_bundle = emit_receipt_bundle
        self._last_emitted_receipt: Optional[dict[str, Any]] = None

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

        # I11 (T5b): fail-closed adversarial-corpus gate, bound to the EXACT
        # artifact being committed (compiled1.artifact_id == the spec_hash
        # _commit_promotion records). Default ON. When no report is supplied
        # the engine runs the corpus eval inline (fresh + bound), so the gate
        # is enforced without stranding the autogrowth pipeline. The verdict
        # is re-derived from per-case results (never a bare flag); any
        # missing/incomplete/mis-bound/below-floor result or eval error
        # refuses. Disabling the gate is charter-denylisted; opt-out is
        # test-only.
        if request.require_adversarial_gate:
            report = request.adversarial_eval_report
            if report is None:
                try:
                    report = run_adversarial_corpus_evaluation(
                        bound_solver_hash=compiled1.artifact_id,
                    )
                except AdversarialCorpusEvalError:
                    return self._reject_with_validation_shadow(
                        family_kind, validation, shadow,
                        "I11_adversarial_corpus_eval_error",
                    )
            gate = verify_adversarial_corpus_gate(
                report=report,
                expected_solver_hash=compiled1.artifact_id,
                min_cases=ADVERSARIAL_CORPUS_MIN_CASES,
            )
            if not gate.ok:
                return self._reject_with_validation_shadow(
                    family_kind, validation, shadow,
                    "I11_adversarial_corpus_gate",
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
        try:
            decision_artifact = build_promotion_decision_artifact(
                family_kind=family_kind,
                solver_name=solver_name,
                decision="rollback",
                solver_id=existing.id,
                spec_hash=existing.spec_hash or "",
                rollback_reason=rollback_reason,
            )
            decision_digest = sha256_digest(decision_artifact)
        except Exception as exc:  # noqa: BLE001
            raise ControlPlaneError(
                f"rollback artifact build failed: {exc!r}"
            ) from exc

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
                    evidence=json.dumps(
                        {"promotion_decision_digest": decision_digest},
                        sort_keys=True,
                    ),
                )
                outcome = PromotionOutcome(
                    decided=True,
                    decision="rolled_back",
                    solver_id=existing.id,
                    family_kind=family_kind,
                    invariant_failed=None,
                    validation=None,
                    shadow=None,
                    decision_record_id=decision.id,
                    promotion_decision_artifact=decision_artifact,
                    promotion_decision_digest=decision_digest,
                )
                self._cp._conn.execute("COMMIT")  # type: ignore[attr-defined]
            except Exception as exc:  # noqa: BLE001
                self._cp._conn.execute("ROLLBACK")  # type: ignore[attr-defined]
                raise ControlPlaneError(
                    f"rollback failed: {exc!r}"
                ) from exc
        self._emit_promotion_receipt_bundle(outcome)
        return outcome

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
        try:
            decision_artifact = build_promotion_decision_artifact(
                family_kind=family_kind,
                solver_name=spec.solver_name,
                decision="auto_promoted",
                spec_id=spec.spec_id,
                spec_hash=compiled.artifact_id,
                cell_id=spec.cell_id,
                validation=validation,
                shadow=shadow,
            )
            decision_digest = sha256_digest(decision_artifact)
        except Exception as exc:  # noqa: BLE001
            raise ControlPlaneError(
                f"auto-promotion artifact build failed: {exc!r}"
            ) from exc

        with self._cp._lock:  # type: ignore[attr-defined]
            self._cp._conn.execute("BEGIN IMMEDIATE")  # type: ignore[attr-defined]
            try:
                # Keep solver-family creation inside the promotion transaction.
                self._cp.upsert_solver_family(family_kind, "1.0")
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
                # Phase 13 P3: record capability features for the dispatcher.
                # Use the in-place helper because we are already inside the
                # engine's outer BEGIN IMMEDIATE — SQLite forbids nested
                # transactions.
                features = extract_features(family_kind, dict(spec.spec))
                if features:
                    self._cp._replace_solver_capability_features_inplace(  # type: ignore[attr-defined]
                        solver.id, family_kind, features,
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
                        "promotion_decision_digest": decision_digest,
                    }),
                )
                outcome = PromotionOutcome(
                    decided=True,
                    decision="auto_promoted",
                    solver_id=solver.id,
                    family_kind=family_kind,
                    invariant_failed=None,
                    validation=validation,
                    shadow=shadow,
                    decision_record_id=decision.id,
                    promotion_decision_artifact=decision_artifact,
                    promotion_decision_digest=decision_digest,
                )
                self._cp._conn.execute("COMMIT")  # type: ignore[attr-defined]
            except Exception as exc:  # noqa: BLE001
                self._cp._conn.execute("ROLLBACK")  # type: ignore[attr-defined]
                raise ControlPlaneError(
                    f"auto-promotion commit failed: {exc!r}"
                ) from exc

        self._emit_promotion_receipt_bundle(outcome)
        return outcome

    def _family_kind_for_solver(self, solver: SolverRecord) -> str:
        if solver.family_id is None:
            return "<unknown>"
        with self._cp._lock:  # type: ignore[attr-defined]
            row = self._cp._conn.execute(  # type: ignore[attr-defined]
                "SELECT name FROM solver_families WHERE id = ?",
                (solver.family_id,),
            ).fetchone()
        return str(row["name"]) if row is not None else "<unknown>"

    def _emit_promotion_receipt_bundle(self, outcome: PromotionOutcome) -> None:
        if self.emit_receipt_bundle is None:
            return
        try:
            bundle = build_promotion_decision_receipt(
                outcome,
                previous_receipt=self._last_emitted_receipt,
            )
            self.emit_receipt_bundle(bundle)
        except Exception as exc:  # noqa: BLE001
            raise AutoPromotionReceiptEmissionError(
                "auto-promotion receipt sink failed after durable commit"
            ) from exc
        self._last_emitted_receipt = bundle["receipt"]


def build_promotion_decision_artifact(
    *,
    family_kind: str,
    solver_name: str,
    decision: str,
    spec_id: str = "",
    spec_hash: str = "",
    cell_id: str = "",
    solver_id: int | None = None,
    validation: ValidationOutcome | None = None,
    shadow: ShadowOutcome | None = None,
    invariant_failed: str | None = None,
    rollback_reason: str | None = None,
    ts_utc: str | None = None,
    policy_version: str = PROMOTION_POLICY_VERSION,
    charter_version: str = PROMOTION_CHARTER_VERSION,
) -> dict[str, Any]:
    """Build a payload-free RCO artifact for a promotion authority decision."""
    from waggledance.core.magma.rco_decision_artifact import (
        build_rco_decision_artifact,
    )

    approved = decision in {"auto_promoted", "rollback", "rolled_back"}
    gate_decision = "allow" if approved else "refuse"
    intent = {
        "subject_type": "promotion",
        "family_kind": family_kind,
        "solver_name": solver_name,
        "solver_id": solver_id,
        "spec_id": spec_id,
        "cell_id": cell_id,
    }
    write_payload = {
        "schema_version": "auto_promotion_decision_payload.v1",
        "decision": decision,
        "family_kind": family_kind,
        "solver_name": solver_name,
        "solver_id": solver_id,
        "spec_hash": spec_hash,
        "validation": _validation_summary(validation),
        "shadow": _shadow_summary(shadow),
        "invariant_failed": invariant_failed or "",
        "rollback_reason_digest": (
            sha256_digest({"rollback_reason": rollback_reason})
            if rollback_reason
            else None
        ),
    }
    reason_codes = _promotion_reason_codes(
        decision=decision,
        family_kind=family_kind,
        approved=approved,
        validation=validation,
        shadow=shadow,
        invariant_failed=invariant_failed,
    )
    return build_rco_decision_artifact(
        decision_id=(
            "rco:promotion:"
            f"{_safe_ref(family_kind)}:{_safe_ref(solver_name)}:"
            f"{_safe_ref(decision)}"
        ),
        ts_utc=ts_utc or _utc_iso(),
        intent=intent,
        write_payload=write_payload,
        risk_class="local_artifact",
        gate_decision=gate_decision,
        approved=approved,
        operator_required=False,
        policy_version=policy_version,
        charter_version=charter_version,
        scope_policy_decision="auto_approved" if approved else "denied",
        peer_rco_verdict="not_requested",
        verifier_path=[
            "auto_promotion_engine_v1",
            "low_risk_policy",
            "validation_runner",
            "shadow_evaluator",
            "rco_decision_artifact_v0",
        ],
        reason_codes=reason_codes,
    )


def build_promotion_decision_receipt(
    outcome: PromotionOutcome,
    *,
    ts_utc: str | None = None,
    policy_digest: str | None = None,
    charter_digest: str | None = None,
    world_snapshot_digest: str | None = None,
    solver_contract_digest: str | None = None,
    previous_receipt: dict[str, Any] | None = None,
    domain_threshold_version: str = PROMOTION_DOMAIN_THRESHOLD_VERSION,
) -> dict[str, Any]:
    """Build EvaluationResult + MAGMA receipt for a promotion outcome.

    The receipt payload intentionally contains only IDs, outcome fields, and
    digests. Raw solver specs, seed payloads, validation cases, shadow samples,
    and rollback reasons stay out of the receipt bundle.
    """
    from waggledance.core.magma.evaluation_result import build_evaluation_result
    from waggledance.core.magma.receipt import build_magma_receipt

    artifact = outcome.promotion_decision_artifact
    if artifact is None or outcome.promotion_decision_digest is None:
        raise ValueError("PromotionOutcome is missing promotion decision artifact")

    payload = _promotion_receipt_payload(outcome)
    gate_decision = str(artifact["gate_decision"])
    evaluation = build_evaluation_result(
        case_id=(
            "case:auto_promotion:"
            f"{_safe_ref(outcome.family_kind)}:{_safe_ref(outcome.decision)}"
        ),
        subject_type="promotion",
        target_payload=payload,
        risk_class="local_artifact",
        expected_gate=gate_decision,
        actual_gate=gate_decision,
        verifier_path=[
            "auto_promotion_engine_v1",
            "rco_decision_artifact_v0",
            "magma_receipt_v1",
        ],
        solver_selection=(
            [f"solver:{_safe_ref(str(outcome.solver_id))}"]
            if outcome.solver_id is not None
            else []
        ),
        policy_version=str(artifact["policy_version"]),
        charter_version=str(artifact["charter_version"]),
        domain_threshold_version=domain_threshold_version,
        verdict="pass" if outcome.decided else "refuse",
        reason_codes=list(artifact["reason_codes"]),
        confidence_score=0.95 if outcome.decided else 0.75,
        uncertainty_sources=[],
    )
    receipt = build_magma_receipt(
        event_id=(
            "magma:auto_promotion:"
            f"{_safe_ref(outcome.family_kind)}:{_safe_ref(outcome.decision)}"
        ),
        ts_utc=ts_utc or _utc_iso(),
        risk_class="local_artifact",
        payload=payload,
        evaluation_result=evaluation,
        policy_digest=policy_digest or sha256_digest({
            "policy_version": artifact["policy_version"],
        }),
        charter_digest=charter_digest or sha256_digest({
            "charter_version": artifact["charter_version"],
        }),
        rco_decision_digest=outcome.promotion_decision_digest,
        world_snapshot_digest=world_snapshot_digest or sha256_digest({
            "family_kind": outcome.family_kind,
            "decision": outcome.decision,
            "decision_record_id": outcome.decision_record_id,
        }),
        solver_contract_digest=solver_contract_digest or sha256_digest({
            "solver_id": outcome.solver_id,
            "family_kind": outcome.family_kind,
        }),
        previous_receipt=previous_receipt,
    )
    return {
        "payload": payload,
        "promotion_decision_artifact": artifact,
        "evaluation_result": evaluation,
        "receipt": receipt,
    }


def _promotion_receipt_payload(outcome: PromotionOutcome) -> dict[str, Any]:
    return {
        "schema_version": "auto_promotion_receipt_payload.v1",
        "decision": outcome.decision,
        "decided": outcome.decided,
        "solver_id": outcome.solver_id,
        "family_kind": outcome.family_kind,
        "invariant_failed": outcome.invariant_failed or "",
        "decision_record_id": outcome.decision_record_id,
        "promotion_decision_digest": outcome.promotion_decision_digest,
    }


def _validation_summary(
    validation: ValidationOutcome | None,
) -> dict[str, Any] | None:
    if validation is None:
        return None
    return {
        "case_count": validation.case_count,
        "pass_count": validation.pass_count,
        "fail_count": validation.fail_count,
        "pass_rate": validation.pass_rate,
        "all_passed": validation.all_passed,
    }


def _shadow_summary(shadow: ShadowOutcome | None) -> dict[str, Any] | None:
    if shadow is None:
        return None
    return {
        "sample_count": shadow.sample_count,
        "agree_count": shadow.agree_count,
        "disagree_count": shadow.disagree_count,
        "agreement_rate": shadow.agreement_rate,
        "oracle_kind": shadow.oracle_kind,
    }


def _promotion_reason_codes(
    *,
    decision: str,
    family_kind: str,
    approved: bool,
    validation: ValidationOutcome | None,
    shadow: ShadowOutcome | None,
    invariant_failed: str | None,
) -> list[str]:
    codes = [
        f"promotion:decision:{_safe_ref(decision)}",
        f"promotion:family:{_safe_ref(family_kind)}",
    ]
    if approved:
        codes.append("promotion:approved")
    else:
        codes.append("promotion:refused")
    if validation is not None:
        codes.append(
            "promotion:validation:pass"
            if validation.all_passed
            else "promotion:validation:fail"
        )
    if shadow is not None:
        codes.append(
            "promotion:shadow:pass"
            if shadow.disagree_count == 0
            else "promotion:shadow:review"
        )
    if invariant_failed:
        codes.append(f"promotion:invariant:{_safe_ref(invariant_failed)}")
    return codes


def _safe_ref(value: str) -> str:
    safe = "".join(
        char if char.isalnum() or char in ":._-" else "_"
        for char in str(value)
    ).strip("_")
    if not safe or not safe[0].isalpha():
        safe = f"ref:{safe or 'unknown'}"
    return safe[:160]


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "AutoPromotionEngine",
    "PromotionRequest",
    "PromotionOutcome",
    "AutoPromotionReceiptEmissionError",
    "PROMOTION_DECIDED_BY",
    "build_promotion_decision_artifact",
    "build_promotion_decision_receipt",
]
