# SPDX-License-Identifier: Apache-2.0
"""SolverRouter ↔ autonomy consult wiring tests (Phase 14 P2).

Asserts that the production reasoning router (`SolverRouter.route`)
calls the autonomy consult lane when:
1. the built-in capability selection fell back, AND
2. the caller passed a structured `low_risk_autonomy_query` hint.

The seam is backwards-compatible: callers that don't pass the hint
or don't supply an `autonomy_consult` callable see no behaviour
change.
"""
from __future__ import annotations

import pytest

from waggledance.core.autonomy_growth import (
    AutogrowthScheduler,
    GapSignal,
    HotPathCache,
    LowRiskGrower,
    RuntimeGapDetector,
    RuntimeQueryRouter,
    build_autonomy_consult,
    digest_signals_into_intents,
)
from waggledance.core.capabilities.registry import CapabilityRegistry
from waggledance.core.capabilities.selector import CapabilitySelector
from waggledance.core.reasoning.solver_router import SolverRouter
from waggledance.core.storage.control_plane import ControlPlaneDB


@pytest.fixture()
def cp(tmp_path):
    db = ControlPlaneDB(tmp_path / "cp.sqlite")
    db.migrate()
    g = LowRiskGrower(db)
    g.ensure_low_risk_policies()
    yield db
    db.close()


def _kelvin_seed():
    return {
        "spec": {"from_unit": "C", "to_unit": "K",
                  "factor": 1.0, "offset": 273.15},
        "validation_cases": [
            {"inputs": {"x": 0.0}, "expected": 273.15},
            {"inputs": {"x": 100.0}, "expected": 373.15},
        ],
        "shadow_samples": [{"x": float(i)} for i in range(10)],
        "solver_name_seed": "celsius_to_kelvin",
        "cell_id": "thermal",
    }


def _seed_promoted(cp: ControlPlaneDB) -> None:
    seed = _kelvin_seed()
    digest_signals_into_intents(
        cp,
        candidate_signals=[GapSignal(
            kind="runtime_miss", family_kind="scalar_unit_conversion",
            cell_coord="thermal", intent_seed="celsius_to_kelvin",
            spec_seed=seed,
        )],
        min_signals_per_intent=1, autoenqueue=True,
    )
    AutogrowthScheduler(cp).run_until_idle()


def _solver_router_with_consult(cp: ControlPlaneDB) -> SolverRouter:
    detector = RuntimeGapDetector(cp)
    hot_path = HotPathCache(control_plane=cp, detector=detector)
    runtime_router = RuntimeQueryRouter(
        cp, detector=detector, hot_path=hot_path,
        min_signal_interval_seconds=0.0,
    )
    consult = build_autonomy_consult(runtime_router)
    # Empty registry -> any intent falls back -> autonomy consult triggers.
    return SolverRouter(
        registry=CapabilityRegistry(load_builtins=False),
        autonomy_consult=consult,
    )


def test_solver_router_baseline_unchanged_without_hint(
    cp: ControlPlaneDB,
) -> None:
    """No hint, no consult, no autonomy_consult attached -> Phase 13
    behaviour preserved."""

    sr = SolverRouter(registry=CapabilityRegistry())
    res = sr.route(intent="math", query="2 + 2", context={})
    assert res.autonomy_consult is None
    assert res.autonomy_served is False


def test_solver_router_serves_via_autonomy_consult_after_promotion(
    cp: ControlPlaneDB,
) -> None:
    """Built-in registry empty, but autonomy already has the solver
    promoted: route() returns served via autonomy consult."""

    _seed_promoted(cp)
    sr = _solver_router_with_consult(cp)
    res = sr.route(
        intent="math",
        query="convert 0 C to K",
        context={
            "low_risk_autonomy_query": {
                "family_kind": "scalar_unit_conversion",
                "cell_coord": "thermal",
                "intent_seed": "celsius_to_kelvin",
                "inputs": {"x": 0.0},
                "features": {"from_unit": "C", "to_unit": "K"},
            },
        },
    )
    assert res.selection.fallback_used is True  # built-ins didn't solve it
    assert res.autonomy_consult is not None
    assert res.autonomy_served is True
    assert res.autonomy_consult.source == "auto_promoted_solver"
    assert res.autonomy_consult.output == pytest.approx(273.15)
    # to_dict includes the autonomy_consult slice
    d = res.to_dict()
    assert d["autonomy_consult"]["served"] is True


def test_solver_router_consult_emits_signal_on_miss(cp: ControlPlaneDB) -> None:
    """Nothing promoted yet -> autonomy consult reports gap_emitted."""

    sr = _solver_router_with_consult(cp)
    seed = _kelvin_seed()
    res = sr.route(
        intent="math",
        query="convert 25 C to K",
        context={
            "low_risk_autonomy_query": {
                "family_kind": "scalar_unit_conversion",
                "cell_coord": "thermal",
                "intent_seed": "celsius_to_kelvin",
                "inputs": {"x": 25.0},
                "features": {"from_unit": "C", "to_unit": "K"},
                "spec_seed": seed,
            },
        },
    )
    assert res.autonomy_consult is not None
    assert res.autonomy_served is False
    assert res.autonomy_consult.source == "gap_emitted"


def test_solver_router_skips_consult_when_hint_missing(
    cp: ControlPlaneDB,
) -> None:
    """Even with autonomy_consult wired, a route without the hint must
    not invoke the consult."""

    _seed_promoted(cp)
    sr = _solver_router_with_consult(cp)
    res = sr.route(intent="math", query="2 + 2", context={})
    assert res.autonomy_consult is None


def test_solver_router_consult_skips_for_non_low_risk_family(
    cp: ControlPlaneDB,
) -> None:
    """family_kind outside the allowlist -> consult returns
    family_not_low_risk; production reasoning falls through normally."""

    sr = _solver_router_with_consult(cp)
    res = sr.route(
        intent="anything",
        query="x",
        context={
            "low_risk_autonomy_query": {
                "family_kind": "temporal_window_rule",  # excluded
                "inputs": {"x": 1},
            },
        },
    )
    assert res.autonomy_consult is not None
    assert res.autonomy_served is False
    assert res.autonomy_consult.source == "family_not_low_risk"


def test_solver_router_consult_isolates_callable_errors(
    cp: ControlPlaneDB,
) -> None:
    """If the autonomy consult callable raises, SolverRouter records a
    structured 'consult_skipped' rather than propagating."""

    def _explode(_hint):
        raise RuntimeError("simulated downstream failure")

    sr = SolverRouter(
        registry=CapabilityRegistry(load_builtins=False),
        autonomy_consult=_explode,
    )
    res = sr.route(
        intent="math",
        query="x",
        context={
            "low_risk_autonomy_query": {
                "family_kind": "scalar_unit_conversion",
                "inputs": {"x": 1.0},
                "features": {"from_unit": "C", "to_unit": "K"},
            },
        },
    )
    assert res.autonomy_consult is not None
    assert res.autonomy_consult.source == "consult_skipped"
    assert res.autonomy_consult.miss_reason and (
        res.autonomy_consult.miss_reason.startswith("consult_error:")
    )


def test_solver_router_consult_skipped_when_built_ins_succeed(
    cp: ControlPlaneDB,
) -> None:
    """If the built-in selection did NOT fall back, the consult is
    not invoked even with a hint and a wired callable."""

    invocations = []

    def _spy(hint):
        invocations.append(hint)
        return None

    # A registry with at least one matching capability so selection
    # does not fall back. The simplest approach is to use a stub
    # selector that returns fallback_used=False.
    from waggledance.core.capabilities.selector import SelectionResult

    class _FakeSelector:
        def select(self, intent, context, conditions):
            return SelectionResult(
                selected=[], reason="forced gold",
                quality_path="gold", fallback_used=False,
            )

        def select_for_capability_ids(self, ids):  # pragma: no cover
            return SelectionResult(selected=[], fallback_used=False)

    sr = SolverRouter(
        registry=CapabilityRegistry(),
        selector=_FakeSelector(),  # type: ignore[arg-type]
        autonomy_consult=_spy,
    )
    sr.route(
        intent="math", query="x",
        context={
            "low_risk_autonomy_query": {
                "family_kind": "scalar_unit_conversion",
                "inputs": {"x": 1.0},
                "features": {"from_unit": "C", "to_unit": "K"},
            },
        },
    )
    assert invocations == []  # consult never called
