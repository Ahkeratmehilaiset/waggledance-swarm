# SPDX-License-Identifier: BUSL-1.1
# BUSL-Change-Date: 2030-12-31
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
# See LICENSE-BUSL.txt and LICENSE-CORE.md
"""Bridge between SolverRouter (production reasoning) and the
RuntimeQueryRouter (low-risk autonomy lane).

`SolverRouter.__init__` accepts an optional ``autonomy_consult``
callable. This module provides the canonical adapter:

    consult = build_autonomy_consult(runtime_router)
    SolverRouter(autonomy_consult=consult, ...)

When `SolverRouter.route(...)` decides the built-in capability
selection fell back, it forwards the caller's
`context["low_risk_autonomy_query"]` hint into this adapter, which
calls `RuntimeQueryRouter.route(...)` and translates the result back
into an `AutonomyConsultOutcome` for the production caller.

The adapter is the only sanctioned wiring point. Production code
should not import RuntimeQueryRouter directly into reasoning code —
the indirection keeps the inner-loop autonomy lane swappable.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

from waggledance.core.reasoning.solver_router import AutonomyConsultOutcome

from .runtime_query_router import (
    RuntimeQuery,
    RuntimeQueryRouter,
    RuntimeRouteResult,
)


def build_autonomy_consult(
    runtime_router: RuntimeQueryRouter,
):
    """Return a callable suitable for SolverRouter(autonomy_consult=...)."""

    def _consult(hint: Mapping[str, Any]) -> Optional[AutonomyConsultOutcome]:
        family = hint.get("family_kind")
        if not family:
            return None
        inputs = hint.get("inputs") or {}
        if not isinstance(inputs, dict):
            return AutonomyConsultOutcome(
                served=False,
                source="consult_skipped",
                miss_reason="hint_inputs_not_dict",
            )
        features = hint.get("features")
        if features is not None and not isinstance(features, dict):
            return AutonomyConsultOutcome(
                served=False,
                source="consult_skipped",
                miss_reason="hint_features_not_dict",
            )
        cell_coord = hint.get("cell_coord")
        intent_seed = hint.get("intent_seed")
        spec_seed = hint.get("spec_seed")
        weight = float(hint.get("weight", 1.0))
        query = RuntimeQuery(
            family_kind=str(family),
            inputs=inputs,
            cell_coord=cell_coord,
            intent_seed=intent_seed,
            features=features,
            spec_seed=spec_seed if isinstance(spec_seed, dict) else None,
            weight=weight,
        )
        result: RuntimeRouteResult = runtime_router.route(query)
        return AutonomyConsultOutcome(
            served=result.served,
            source=result.source,
            output=result.output,
            solver_id=result.solver_id,
            solver_name=result.solver_name,
            artifact_id=result.artifact_id,
            signal_id=result.signal_id,
            miss_reason=result.miss_reason,
        )

    return _consult


__all__ = ["build_autonomy_consult"]
