# SPDX-License-Identifier: Apache-2.0
"""Reality View autonomy_self_starting_kpis panel tests (Phase 12)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from waggledance.core.autonomy_growth import (
    AutogrowthScheduler,
    GapSignal,
    LowRiskGrower,
    RuntimeGapDetector,
    digest_signals_into_intents,
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


def test_self_starting_panel_unavailable_when_cp_detached() -> None:
    panels = build_scale_aware_panels(control_plane=None)
    p = panels.autonomy_self_starting_kpis
    assert p.available is False
    assert p.panel_id == "autonomy_self_starting_kpis"
    assert p.rationale_if_unavailable == "control_plane_db_not_attached"


def test_self_starting_panel_unavailable_on_empty_loop(cp: ControlPlaneDB) -> None:
    """No queue, no intents, no runs — never fabricate."""

    p = build_scale_aware_panels(control_plane=cp).autonomy_self_starting_kpis
    assert p.available is False
    assert (
        p.rationale_if_unavailable
        == "no_self_starting_activity_recorded_yet"
    )


def _seed_one(cp: ControlPlaneDB, family: str, cell: str, name: str,
              spec_seed: dict) -> None:
    detector = RuntimeGapDetector(cp)
    sig = GapSignal(
        kind="miss", family_kind=family, cell_coord=cell, intent_seed=name,
        weight=1.0, spec_seed=spec_seed,
    )
    detector.record(sig)
    digest_signals_into_intents(
        cp, candidate_signals=[sig],
        min_signals_per_intent=1, autoenqueue=True,
    )


def test_self_starting_panel_aggregates_after_run(cp: ControlPlaneDB) -> None:
    g = LowRiskGrower(cp)
    g.ensure_low_risk_policies()
    _seed_one(
        cp, "scalar_unit_conversion", "thermal", "celsius_to_kelvin",
        {
            # scalar_unit_conversion requires from_unit + to_unit + factor
            "spec": {"from_unit": "C", "to_unit": "K",
                     "factor": 1.0, "offset": 273.15},
            "validation_cases": [
                {"inputs": {"x": 0.0}, "expected": 273.15},
                {"inputs": {"x": 100.0}, "expected": 373.15},
            ],
            "shadow_samples": [{"x": float(i)} for i in range(10)],
            "solver_name_seed": "celsius_to_kelvin",
            "cell_id": "thermal",
        },
    )
    sched = AutogrowthScheduler(cp)
    sched.run_until_idle()

    p = build_scale_aware_panels(control_plane=cp).autonomy_self_starting_kpis
    assert p.available is True
    metrics = {item["metric"]: item["value"] for item in p.items}
    assert metrics["intents_fulfilled"] == 1
    assert metrics["self_starting_promotions_total"] == 1
    # Truth: zero teacher-assisted, zero provider jobs in the inner loop
    assert metrics["teacher_assisted_promotions_total"] == 0
    assert metrics["provider_jobs_total"] == 0

    per_family = json.loads(metrics["per_family_self_starting"])
    assert per_family.get("scalar_unit_conversion") == 1
    per_cell = json.loads(metrics["per_cell_self_starting"])
    assert per_cell.get("thermal") == 1


def test_self_starting_panel_is_aggregate_not_per_solver(
    cp: ControlPlaneDB,
) -> None:
    """Scale invariant: no per-solver_name rows leak into the panel."""

    g = LowRiskGrower(cp)
    g.ensure_low_risk_policies()
    for i in range(4):
        _seed_one(
            cp, "lookup_table", "general", f"color_v{i}",
            {
                "spec": {"table": {"red": "stop", "green": "go"},
                          "default": "wait"},
                "validation_cases": [
                    {"inputs": {"key": "red"}, "expected": "stop"},
                    {"inputs": {"key": "x"}, "expected": "wait"},
                ],
                "shadow_samples": [{"key": k}
                                    for k in ("red", "green", "x")],
                "solver_name_seed": f"color_v{i}",
                "cell_id": "general",
            },
        )
    sched = AutogrowthScheduler(cp)
    sched.run_until_idle()
    p = build_scale_aware_panels(control_plane=cp).autonomy_self_starting_kpis
    assert p.available is True
    for item in p.items:
        assert "metric" in item
        # Should never see solver_name leak
        assert "solver_name" not in item


def test_self_starting_panel_unaffected_by_phase11_panel(cp: ControlPlaneDB) -> None:
    """The Phase 11 autonomy_low_risk_kpis panel still works alongside
    the new Phase 12 panel; both are present in the bundle."""

    panels = build_scale_aware_panels(control_plane=cp)
    assert hasattr(panels, "autonomy_low_risk_kpis")
    assert hasattr(panels, "autonomy_self_starting_kpis")
