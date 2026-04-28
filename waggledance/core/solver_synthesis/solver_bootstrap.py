# SPDX-License-Identifier: BUSL-1.1
# BUSL-Change-Date: 2030-12-31
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
# See LICENSE-BUSL.txt and LICENSE-CORE.md
"""Solver bootstrap orchestrator (Phase 10 P4).

Ties together:

* the Phase 9 declarative pipeline (family registry, declarative spec,
  deterministic compiler, validators);
* the Phase 10 LLM generator (:mod:`llm_solver_generator`);
* the Phase 10 cold/shadow throttler;
* the Phase 10 control plane — solvers and families register through
  :class:`waggledance.core.storage.ControlPlaneDB`;
* the U1→U3 escalation rule (RULE 15: family-first solver growth).

The orchestrator is *opt-in*. Existing call sites that already use
the Phase 9 declarative pipeline directly continue to work unchanged.
This module is what a future "scale to 10k+ solvers" workflow will
go through, because it is the only path that:

1. counts a candidate against the cold/shadow throttler before
   compilation;
2. registers the resulting spec/solver in the control plane;
3. records the provider job (if U3 was triggered) with section,
   purpose, and cost.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from waggledance.core.providers import ProviderPlane

from . import (
    SOLVER_SYNTHESIS_SCHEMA_VERSION,
    U1_HIGH_CONFIDENCE_THRESHOLD,
    U1_LOW_CONFIDENCE_THRESHOLD,
)
from .cold_shadow_throttler import ColdShadowThrottler, ThrottleVerdict
from .declarative_solver_spec import (
    SolverSpec,
    SpecValidationError,
    make_spec,
)
from .llm_solver_generator import (
    GenerationRequest,
    GenerationResult,
    generate as llm_generate,
)
from .solver_family_registry import SolverFamilyRegistry
from waggledance.core.storage import (
    ControlPlaneDB,
    SolverRecord,
    SolverFamilyRecord,
)


@dataclass(frozen=True)
class BootstrapDecision:
    lane: str  # "u1_declarative" | "u3_freeform" | "deferred_throttled" | "rejected"
    reason: str
    spec: Optional[SolverSpec] = None
    generation_result: Optional[GenerationResult] = None
    throttle_verdict: Optional[ThrottleVerdict] = None
    control_plane_solver: Optional[SolverRecord] = None


class SolverBootstrap:
    """U1→U3 escalation orchestrator with throttling + control-plane sync."""

    def __init__(
        self,
        *,
        family_registry: SolverFamilyRegistry,
        control_plane: Optional[ControlPlaneDB] = None,
        provider_plane: Optional[ProviderPlane] = None,
        throttler: Optional[ColdShadowThrottler] = None,
        section: Optional[str] = None,
    ) -> None:
        self._families = family_registry
        self._cp = control_plane
        self._provider_plane = provider_plane
        self._throttler = throttler or ColdShadowThrottler()
        self._section = section

    @property
    def family_registry(self) -> SolverFamilyRegistry:
        return self._families

    @property
    def throttler(self) -> ColdShadowThrottler:
        return self._throttler

    # -- registration in control plane ---------------------------------

    def register_default_families(self) -> tuple[SolverFamilyRecord, ...]:
        """Mirror the Phase 9 default families into the control plane."""

        if self._cp is None:
            return ()
        records: list[SolverFamilyRecord] = []
        for kind in self._families.list_kinds():
            fam = self._families.get(kind)
            if fam is None:
                continue
            rec = self._cp.upsert_solver_family(
                name=fam.kind,
                version=str(fam.schema_version),
                description=fam.description,
                status="active",
            )
            records.append(rec)
        return tuple(records)

    # -- bootstrap path ------------------------------------------------

    def bootstrap_from_gap(
        self,
        *,
        gap_id: str,
        cell_id: str,
        intent: str,
        family_match_confidence: float,
        family_kind: Optional[str],
        spec_payload: Optional[dict] = None,
        examples: Optional[list[dict]] = None,
        family_hints: Optional[list[str]] = None,
        branch_name: str = "phase10/foundation-truth-builder-lane",
        base_commit_hash: str = "",
    ) -> BootstrapDecision:
        """Decide between U1 (declarative) and U3 (free-form) for a gap.

        Routing rule (RULE 15 — family-first growth):

        * confidence >= U1_HIGH_CONFIDENCE_THRESHOLD → declarative
          family compile via the Phase 9 pipeline.
        * confidence < U1_LOW_CONFIDENCE_THRESHOLD → free-form via the
          provider plane (U3).
        * middle band → declarative attempt; on validation failure
          fall through to U3.

        Each path counts against the throttler. ``deferred_throttled``
        is returned when the throttler refuses admission; the caller
        can retry later without state mutation.
        """

        if family_match_confidence >= U1_HIGH_CONFIDENCE_THRESHOLD or (
            U1_LOW_CONFIDENCE_THRESHOLD <= family_match_confidence < U1_HIGH_CONFIDENCE_THRESHOLD
        ):
            verdict = self._throttler.admit("cold")
            if not verdict.admitted:
                return BootstrapDecision(
                    lane="deferred_throttled",
                    reason=verdict.reason,
                    throttle_verdict=verdict,
                )
            try:
                spec = self._compile_declarative(
                    gap_id=gap_id,
                    cell_id=cell_id,
                    family_kind=family_kind,
                    spec_payload=spec_payload,
                    branch_name=branch_name,
                    base_commit_hash=base_commit_hash,
                )
            except SpecValidationError as exc:
                self._throttler.release("cold")
                if family_match_confidence < U1_HIGH_CONFIDENCE_THRESHOLD:
                    # Middle-band fall-through to U3.
                    return self._u3_path(
                        gap_id=gap_id,
                        cell_id=cell_id,
                        intent=intent,
                        examples=examples or [],
                        family_hints=family_hints or [],
                        branch_name=branch_name,
                        base_commit_hash=base_commit_hash,
                        u1_failure_reason=str(exc),
                    )
                return BootstrapDecision(
                    lane="rejected",
                    reason=f"declarative compile failed in high-confidence band: {exc}",
                )
            cp_solver = self._register_solver_in_control_plane(spec)
            self._throttler.release("cold")
            return BootstrapDecision(
                lane="u1_declarative",
                reason=f"high_confidence={family_match_confidence:.2f}",
                spec=spec,
                throttle_verdict=verdict,
                control_plane_solver=cp_solver,
            )

        # Low confidence → straight to U3.
        return self._u3_path(
            gap_id=gap_id,
            cell_id=cell_id,
            intent=intent,
            examples=examples or [],
            family_hints=family_hints or [],
            branch_name=branch_name,
            base_commit_hash=base_commit_hash,
            u1_failure_reason=f"low_confidence={family_match_confidence:.2f}",
        )

    # -- internal -------------------------------------------------------

    def _compile_declarative(
        self,
        *,
        gap_id: str,
        cell_id: str,
        family_kind: Optional[str],
        spec_payload: Optional[dict],
        branch_name: str,
        base_commit_hash: str,
    ) -> SolverSpec:
        if family_kind is None:
            raise SpecValidationError("declarative path requires family_kind")
        if spec_payload is None:
            raise SpecValidationError("declarative path requires spec_payload")
        solver_name = f"u1_{gap_id[:24]}"
        return make_spec(
            family_kind=family_kind,
            solver_name=solver_name,
            cell_id=cell_id,
            spec=spec_payload,
            source=f"gap:{gap_id}",
            source_kind="declarative_match",
            registry=self._families,
            branch_name=branch_name,
            base_commit_hash=base_commit_hash,
            pinned_input_manifest_sha256="sha256:unknown",
        )

    def _u3_path(
        self,
        *,
        gap_id: str,
        cell_id: str,
        intent: str,
        examples: list[dict],
        family_hints: list[str],
        branch_name: str,
        base_commit_hash: str,
        u1_failure_reason: str,
    ) -> BootstrapDecision:
        verdict = self._throttler.admit("shadow")
        if not verdict.admitted:
            return BootstrapDecision(
                lane="deferred_throttled",
                reason=f"shadow_throttled (u1 reason: {u1_failure_reason}): {verdict.reason}",
                throttle_verdict=verdict,
            )
        if self._provider_plane is None:
            self._throttler.release("shadow")
            return BootstrapDecision(
                lane="rejected",
                reason=(
                    f"u3_path required but no ProviderPlane attached (u1 reason: {u1_failure_reason})"
                ),
                throttle_verdict=verdict,
            )
        gen_result = llm_generate(
            self._provider_plane,
            GenerationRequest(
                gap_id=gap_id,
                cell_id=cell_id,
                intent=intent,
                examples=tuple(examples),
                family_hints=tuple(family_hints),
            ),
            branch_name=branch_name,
            base_commit_hash=base_commit_hash,
            section=self._section,
        )
        self._throttler.release("shadow")
        return BootstrapDecision(
            lane="u3_freeform",
            reason=f"u1_failed_or_low_conf: {u1_failure_reason}; gen={gen_result.parse_status}",
            generation_result=gen_result,
            throttle_verdict=verdict,
        )

    def _register_solver_in_control_plane(self, spec: SolverSpec) -> Optional[SolverRecord]:
        if self._cp is None:
            return None
        # Ensure the family is registered first.
        fam = self._families.get(spec.family_kind)
        if fam is not None:
            self._cp.upsert_solver_family(
                name=fam.kind,
                version=str(fam.schema_version),
                description=fam.description,
                status="active",
            )
        return self._cp.upsert_solver(
            name=spec.solver_name,
            version=str(spec.schema_version),
            family_name=spec.family_kind,
            spec_hash=spec.spec_id,
            status="draft",
        )
