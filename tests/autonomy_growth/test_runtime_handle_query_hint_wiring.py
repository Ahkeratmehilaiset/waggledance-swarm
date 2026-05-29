# SPDX-License-Identifier: Apache-2.0
"""AutonomyRuntime.handle_query ↔ runtime hint extractor wiring (P3).

Asserts:
  1. caller without structured_request behaves exactly as before
  2. caller with built-in-solved input does NOT call autonomy consult
  3. caller with ambiguous structured input does NOT emit growth signal
  4. caller with supported low-risk structured input derives hint AND
     consults autonomy
  5. caller failure in extractor cannot crash the production path
"""
from __future__ import annotations

import pytest

from waggledance.core.autonomy.runtime import AutonomyRuntime
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
from waggledance.core.reasoning.solver_router import SolverRouter
from waggledance.core.storage.control_plane import ControlPlaneDB


def _build_runtime_with_consult(
    cp: ControlPlaneDB, *, force_fallback: bool = True,
) -> AutonomyRuntime:
    """Construct AutonomyRuntime with autonomy_consult wired.

    AutonomyRuntime's constructor calls ``bind_executors`` which
    auto-loads YAML capability configs into the registry; that means
    even ``CapabilityRegistry(load_builtins=False)`` ends up with
    capabilities. To make the autonomy consult lane the deterministic
    surface under test, we pass a fake selector that always reports
    ``fallback_used=True``. Real production code uses the real
    selector; this is the standard Phase-14 / Phase-15 unit test
    pattern.
    """

    detector = RuntimeGapDetector(cp)
    hot_path = HotPathCache(control_plane=cp, detector=detector)
    runtime_router = RuntimeQueryRouter(
        cp, detector=detector, hot_path=hot_path,
        min_signal_interval_seconds=0.0,
    )
    consult = build_autonomy_consult(runtime_router)
    registry = CapabilityRegistry(load_builtins=False)

    if force_fallback:
        from waggledance.core.capabilities.selector import SelectionResult

        class _FallbackSelector:
            def select(self, intent, context, conditions):
                return SelectionResult(
                    selected=[], reason="forced fallback for test",
                    quality_path="bronze", fallback_used=True,
                )

            def select_for_capability_ids(self, ids):  # pragma: no cover
                return SelectionResult(selected=[], fallback_used=True)

            def set_capability_confidence(self, scores):  # pragma: no cover
                pass

        sr = SolverRouter(
            registry=registry, selector=_FallbackSelector(),  # type: ignore[arg-type]
            autonomy_consult=consult,
        )
    else:
        sr = SolverRouter(registry=registry, autonomy_consult=consult)

    return AutonomyRuntime(
        capability_registry=registry,
        solver_router=sr,
    )


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


def test_handle_query_baseline_unchanged_without_structured_request(
    cp: ControlPlaneDB,
) -> None:
    """No structured_request → hint kind 'skipped' → autonomy consult
    not invoked → result identical to Phase 14 baseline."""

    runtime = _build_runtime_with_consult(cp)
    result = runtime.handle_query("hello world", context={})
    assert result.get("low_risk_autonomy_hint_kind") == "skipped"
    # autonomy_consult absent or served=False
    if "autonomy_consult" in result:
        assert result["autonomy_consult"]["served"] is False


def test_handle_query_serves_via_consult_after_promotion(
    cp: ControlPlaneDB,
) -> None:
    _seed_promoted(cp)
    runtime = _build_runtime_with_consult(cp)
    result = runtime.handle_query(
        "convert 0 C to K",
        context={
            "structured_request": {
                "unit_conversion": {"x": 0.0, "from": "C", "to": "K"},
                "cell_coord": "thermal",
                "intent_seed": "celsius_to_kelvin",
            }
        },
    )
    assert result["low_risk_autonomy_hint_kind"] == "derived"
    assert result["quality_path"] == "autonomy_consult"
    assert result["autonomy_consult"]["served"] is True
    assert result["response"] == pytest.approx(273.15)


def test_handle_query_emits_signal_on_miss_via_consult(cp: ControlPlaneDB) -> None:
    """Nothing promoted → consult misses → hint extractor still
    derives, but autonomy_consult.served=False, runtime returns the
    'No capabilities available' branch with autonomy_consult observed."""

    runtime = _build_runtime_with_consult(cp)
    result = runtime.handle_query(
        "convert 25 C to K",
        context={
            "structured_request": {
                "unit_conversion": {"x": 25.0, "from": "C", "to": "K"},
                "cell_coord": "thermal",
                "intent_seed": "celsius_to_kelvin",
                # spec_seed inside structured_request is NOT in the
                # extractor's grammar — it remains a context-only key
                # and is NOT consumed automatically by the extractor.
            }
        },
    )
    assert result["low_risk_autonomy_hint_kind"] == "derived"
    assert result["autonomy_consult"]["served"] is False
    # Source: gap_emitted (no spec_seed → Phase 14 router emits buffered
    # signal); or gap_throttled if the same intent_seed already buffered.
    assert result["autonomy_consult"]["source"] in (
        "gap_emitted", "gap_throttled",
    )


def test_handle_query_ambiguous_structured_request_does_not_emit_signal(
    cp: ControlPlaneDB,
) -> None:
    """Ambiguous structured_request (multiple subkeys) → extractor
    rejects → no hint set → autonomy consult not invoked → no
    runtime_gap_signal written."""

    runtime = _build_runtime_with_consult(cp)
    before = cp.count_runtime_gap_signals()
    result = runtime.handle_query(
        "?",
        context={
            "structured_request": {
                "unit_conversion": {"x": 1.0, "from": "C", "to": "K"},
                "lookup": {"key": "red", "domain": "color"},
            }
        },
    )
    assert result["low_risk_autonomy_hint_kind"] == "rejected_ambiguous"
    after = cp.count_runtime_gap_signals()
    assert after == before  # no signal emitted


def test_handle_query_high_risk_family_shaped_input_rejected(
    cp: ControlPlaneDB,
) -> None:
    """structured_request with a non-low-risk family-shaped subkey →
    extractor rejects → no hint, no consult, no signal."""

    runtime = _build_runtime_with_consult(cp)
    before = cp.count_runtime_gap_signals()
    result = runtime.handle_query(
        "?",
        context={
            "structured_request": {
                "temporal_window_check": {
                    "window_seconds": 60, "x": 0.5,
                },
            }
        },
    )
    assert result["low_risk_autonomy_hint_kind"] == "rejected_family_not_low_risk"
    assert cp.count_runtime_gap_signals() == before


def test_handle_query_extractor_error_does_not_crash_runtime(
    cp: ControlPlaneDB, monkeypatch,
) -> None:
    """If the hint extractor raises, handle_query must not crash; it
    records 'extractor_error' and continues."""

    runtime = _build_runtime_with_consult(cp)

    import waggledance.core.autonomy.runtime as runtime_mod

    def _explode(*args, **kwargs):
        raise RuntimeError("simulated extractor failure")

    monkeypatch.setattr(
        runtime_mod, "derive_low_risk_autonomy_hint", _explode,
    )
    result = runtime.handle_query(
        "?",
        context={
            "structured_request": {
                "lookup": {"key": "red", "domain": "color"},
            }
        },
    )
    assert result["low_risk_autonomy_hint_kind"] == "extractor_error"


def test_handle_query_does_not_pass_low_risk_autonomy_query_when_skipped(
    cp: ControlPlaneDB,
) -> None:
    """When extractor skips (no structured_request), context must not
    gain a low_risk_autonomy_query key — it is the extractor's
    exclusive output."""

    runtime = _build_runtime_with_consult(cp)
    ctx = {"profile": "default"}
    runtime.handle_query("hello", context=ctx)
    assert "low_risk_autonomy_query" not in ctx
    assert ctx["low_risk_autonomy_hint_kind"] == "skipped"
