# SPDX-License-Identifier: BUSL-1.1
# BUSL-Change-Date: 2030-12-31
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
# See LICENSE-BUSL.txt and LICENSE-CORE.md
"""End-to-end grower for the low-risk autogrowth lane.

Composes:

* Phase 9 :func:`waggledance.core.solver_synthesis.make_spec` (validates
  required spec keys for the family),
* Phase 9 :class:`waggledance.core.solver_synthesis.solver_family_registry.SolverFamilyRegistry`
  (the registry of known families, populated with the Phase 9 defaults),
* :class:`AutoPromotionEngine` (the closed validation/shadow/decide loop),
* :class:`LowRiskSolverDispatcher` (runtime executable lane).

This is the *primary teacher lane surface* for low-risk solvers in
the early phase. When low-confidence (free-form) synthesis is needed,
callers fall through to
:class:`waggledance.core.solver_synthesis.solver_bootstrap.SolverBootstrap`,
which routes to ``claude_code_builder_lane`` first (Phase 10 chain).

The grower never invokes a paid provider directly; it composes
already-validated artifacts. Free-form provider invocations remain
the responsibility of ``SolverBootstrap`` and respect the session
provider budget.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from waggledance.core.solver_synthesis.declarative_solver_spec import (
    SolverSpec,
    SpecValidationError,
    make_spec,
)
from waggledance.core.solver_synthesis.solver_family_registry import (
    SolverFamilyRegistry,
)
from waggledance.core.storage.control_plane import ControlPlaneDB

from .auto_promotion_engine import (
    AutoPromotionEngine,
    PromotionOutcome,
    PromotionRequest,
)
from .low_risk_policy import LOW_RISK_FAMILY_KINDS, is_low_risk_family
from .shadow_evaluator import OracleFn
from .validation_runner import ValidationCase


PRIMARY_TEACHER_LANE_ID = "claude_code_builder_lane"
"""Teacher-lane identifier preferred by the LLM solver generator
(see ``waggledance/core/solver_synthesis/llm_solver_generator.py``).
This constant is exposed here only so docs/tests can reference the
truthful default; this module does not invoke the provider plane."""


@dataclass(frozen=True)
class GapInput:
    """A minimal description of a need the grower can try to fill.

    For Phase 11 v1, the caller supplies a candidate spec directly. A
    follow-up phase can let the grower pull the spec from a gap-store
    or from the bulk_rule_extractor (Phase 9 §U2)."""

    family_kind: str
    solver_name: str
    cell_id: str
    spec: Mapping[str, Any]
    source: str
    source_kind: str
    validation_cases: Sequence[ValidationCase]
    shadow_samples: Sequence[Mapping[str, Any]]
    oracle: OracleFn
    oracle_kind: str = "external_reference"


@dataclass(frozen=True)
class GapOutcome:
    accepted: bool
    promotion: Optional[PromotionOutcome]
    reason: str  # 'auto_promoted' | 'rejected:<invariant>' | 'spec_invalid'
    error: Optional[str] = None


class LowRiskGrower:
    """High-level orchestrator for the low-risk autogrowth lane."""

    def __init__(
        self,
        control_plane: ControlPlaneDB,
        *,
        registry: Optional[SolverFamilyRegistry] = None,
    ) -> None:
        self._cp = control_plane
        self._engine = AutoPromotionEngine(control_plane)
        self._registry = (
            registry
            if registry is not None
            else SolverFamilyRegistry().register_defaults()
        )

    @property
    def primary_teacher_lane_id(self) -> str:
        return PRIMARY_TEACHER_LANE_ID

    def ensure_low_risk_policies(
        self,
        *,
        max_auto_promote: int = 100,
        min_validation_pass_rate: float = 1.0,
        min_shadow_samples: int = 5,
        min_shadow_agreement_rate: float = 1.0,
    ) -> None:
        """Idempotently install a low-risk policy row for every
        allowlisted family. Safe to call at startup."""

        for kind in LOW_RISK_FAMILY_KINDS:
            self._cp.upsert_family_policy(
                kind,
                is_low_risk=True,
                max_auto_promote=max_auto_promote,
                min_validation_pass_rate=min_validation_pass_rate,
                min_shadow_samples=min_shadow_samples,
                min_shadow_agreement_rate=min_shadow_agreement_rate,
                notes="installed by LowRiskGrower.ensure_low_risk_policies",
            )

    def grow_from_gap(self, gap: GapInput) -> GapOutcome:
        """Try to compile + validate + shadow + auto-promote a candidate.

        Returns a :class:`GapOutcome`. When the gap is outside the
        low-risk envelope, the grower returns
        ``GapOutcome(accepted=False, reason='rejected:I1_family_not_in_allowlist')``
        without consulting any provider — that path is reserved for
        :class:`SolverBootstrap`.
        """

        # Pre-flight: the family must be in the allowlist before we
        # bother building a SolverSpec. This avoids polluting the
        # registry with non-low-risk family kinds during a low-risk
        # call path.
        if not is_low_risk_family(gap.family_kind):
            return GapOutcome(
                accepted=False,
                promotion=None,
                reason="rejected:I1_family_not_in_allowlist",
                error=(
                    f"family {gap.family_kind!r} is not in the low-risk "
                    "allowlist; route this gap through "
                    "solver_synthesis.SolverBootstrap instead"
                ),
            )

        try:
            spec: SolverSpec = make_spec(
                family_kind=gap.family_kind,
                solver_name=gap.solver_name,
                cell_id=gap.cell_id,
                spec=dict(gap.spec),
                source=gap.source,
                source_kind=gap.source_kind,
                registry=self._registry,
            )
        except SpecValidationError as exc:
            return GapOutcome(
                accepted=False,
                promotion=None,
                reason="spec_invalid",
                error=str(exc),
            )

        outcome = self._engine.evaluate_candidate(PromotionRequest(
            spec=spec,
            validation_cases=gap.validation_cases,
            shadow_samples=gap.shadow_samples,
            oracle=gap.oracle,
            oracle_kind=gap.oracle_kind,
        ))
        if outcome.decision == "auto_promoted":
            return GapOutcome(
                accepted=True,
                promotion=outcome,
                reason="auto_promoted",
            )
        return GapOutcome(
            accepted=False,
            promotion=outcome,
            reason=f"rejected:{outcome.invariant_failed}",
            error=outcome.error,
        )


__all__ = [
    "GapInput",
    "GapOutcome",
    "LowRiskGrower",
    "PRIMARY_TEACHER_LANE_ID",
]
