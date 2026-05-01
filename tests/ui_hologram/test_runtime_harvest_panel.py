# SPDX-License-Identifier: Apache-2.0
"""Reality View autonomy_runtime_harvest_kpis panel tests (Phase 13)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from waggledance.core.autonomy_growth import (
    AutogrowthScheduler,
    GapSignal,
    LowRiskGrower,
    RuntimeGapDetector,
    RuntimeQuery,
    RuntimeQueryRouter,
    digest_signals_into_intents,
    extract_features,
)
from waggledance.core.storage import ControlPlaneDB
from waggledance.ui.hologram.scale_aware_aggregator import (
    build_scale_aware_panels,
)


@pytest.fixture()
def cp(tmp_path: Path):
    db = ControlPlaneDB(db_path=tmp_path / "cp.db")
    yield db
    db.close()


def test_runtime_harvest_panel_unavailable_when_cp_detached() -> None:
    panels = build_scale_aware_panels(control_plane=None)
    p = panels.autonomy_runtime_harvest_kpis
    assert p.available is False
    assert p.panel_id == "autonomy_runtime_harvest_kpis"
    assert p.rationale_if_unavailable == "control_plane_db_not_attached"


def test_runtime_harvest_panel_unavailable_when_no_activity(
    cp: ControlPlaneDB,
) -> None:
    p = build_scale_aware_panels(
        control_plane=cp,
    ).autonomy_runtime_harvest_kpis
    assert p.available is False
    assert (
        p.rationale_if_unavailable == "no_runtime_harvest_activity_recorded_yet"
    )


def _kelvin_seed() -> dict:
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


def test_runtime_harvest_panel_aggregates_after_route_and_harvest(
    cp: ControlPlaneDB,
) -> None:
    g = LowRiskGrower(cp)
    g.ensure_low_risk_policies()

    seed = _kelvin_seed()
    features = extract_features("scalar_unit_conversion", seed["spec"])
    router = RuntimeQueryRouter(cp, min_signal_interval_seconds=0.0)
    # 1) route emits a runtime_miss signal
    res_before = router.route(RuntimeQuery(
        family_kind="scalar_unit_conversion",
        inputs={"x": 0.0}, cell_coord="thermal",
        intent_seed="celsius_to_kelvin",
        features=features, spec_seed=seed,
    ))
    assert res_before.served is False

    # 2) harvest cycle
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

    p = build_scale_aware_panels(
        control_plane=cp,
    ).autonomy_runtime_harvest_kpis
    assert p.available is True
    metrics = {item["metric"]: item["value"] for item in p.items}
    assert metrics["runtime_harvested_signals_total"] == 1
    assert metrics["self_starting_promotions_total"] == 1
    assert metrics["teacher_assisted_promotions_total"] == 0
    assert metrics["provider_jobs_total"] == 0

    per_family = json.loads(metrics["per_family_runtime_miss"])
    assert per_family.get("scalar_unit_conversion") == 1
    per_cell = json.loads(metrics["per_cell_runtime_miss"])
    assert per_cell.get("thermal") == 1


def test_runtime_harvest_panel_surfaces_phase14_capability_metrics(
    cp: ControlPlaneDB,
) -> None:
    """Phase 14 P7 — the (extended) panel exposes capability-indexed
    solver counts. No new panel — same `autonomy_runtime_harvest_kpis`."""

    g = LowRiskGrower(cp)
    g.ensure_low_risk_policies()
    seed = _kelvin_seed()
    detector = RuntimeGapDetector(cp)
    router = RuntimeQueryRouter(cp, detector=detector,
                                  min_signal_interval_seconds=0.0)
    router.route(RuntimeQuery(
        family_kind="scalar_unit_conversion",
        inputs={"x": 0.0}, cell_coord="thermal",
        intent_seed="celsius_to_kelvin",
        features=extract_features("scalar_unit_conversion", seed["spec"]),
        spec_seed=seed,
    ))
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

    p = build_scale_aware_panels(
        control_plane=cp,
    ).autonomy_runtime_harvest_kpis
    assert p.available is True
    metrics = {item["metric"]: item["value"] for item in p.items}
    # Phase 14 metrics present and truthful (one auto-promoted solver
    # with two capability features: from_unit + to_unit).
    assert metrics["live_runtime_capability_indexed_solvers_total"] == 1
    assert metrics["live_runtime_capability_features_total"] == 2


def test_runtime_harvest_panel_is_aggregate_not_per_signal(
    cp: ControlPlaneDB,
) -> None:
    """Scale invariant: never list per-signal rows, only aggregates."""

    detector_router = RuntimeQueryRouter(cp, min_signal_interval_seconds=0.0)
    seed = _kelvin_seed()
    features = extract_features("scalar_unit_conversion", seed["spec"])
    for i in range(15):
        detector_router.route(RuntimeQuery(
            family_kind="scalar_unit_conversion",
            inputs={"x": float(i)}, cell_coord="thermal",
            intent_seed=f"variant_{i}",
            features=features, spec_seed=seed,
        ))
    p = build_scale_aware_panels(
        control_plane=cp,
    ).autonomy_runtime_harvest_kpis
    assert p.available is True
    for item in p.items:
        assert "metric" in item
        # Never per-signal payload leakage
        assert "signal_payload" not in item
