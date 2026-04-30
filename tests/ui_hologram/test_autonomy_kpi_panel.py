# SPDX-License-Identifier: Apache-2.0
"""Reality View autonomy_low_risk_kpis panel tests (Phase 11)."""

from __future__ import annotations

from pathlib import Path

import pytest

from waggledance.core.autonomy_growth import (
    DispatchQuery,
    GapInput,
    LowRiskGrower,
    LowRiskSolverDispatcher,
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


def test_autonomy_panel_unavailable_when_control_plane_detached() -> None:
    panels = build_scale_aware_panels(control_plane=None)
    panel = panels.autonomy_low_risk_kpis
    assert panel.available is False
    assert panel.panel_id == "autonomy_low_risk_kpis"
    assert panel.rationale_if_unavailable == "control_plane_db_not_attached"
    assert panel.items == ()


def test_autonomy_panel_unavailable_on_empty_autonomy_lane(cp: ControlPlaneDB) -> None:
    """No promotions, no decisions, no snapshot — never fabricate."""

    panels = build_scale_aware_panels(control_plane=cp)
    panel = panels.autonomy_low_risk_kpis
    assert panel.available is False
    assert panel.rationale_if_unavailable == "no_autonomy_activity_recorded_yet"
    assert panel.items == ()


def _kelvin_oracle(inputs, artifact):
    return float(inputs["x"]) * float(artifact["factor"]) + float(
        artifact.get("offset", 0.0)
    )


def _kelvin_gap(name="celsius_to_kelvin_v1") -> GapInput:
    return GapInput(
        family_kind="scalar_unit_conversion",
        solver_name=name, cell_id="thermal",
        spec={"from_unit": "C", "to_unit": "K",
              "factor": 1.0, "offset": 273.15},
        source="t", source_kind="t",
        validation_cases=[
            {"inputs": {"x": 0.0}, "expected": 273.15},
            {"inputs": {"x": 100.0}, "expected": 373.15},
        ],
        shadow_samples=[{"x": float(i)} for i in range(10)],
        oracle=_kelvin_oracle,
    )


def test_autonomy_panel_aggregates_after_grow_and_dispatch(
    cp: ControlPlaneDB,
) -> None:
    g = LowRiskGrower(cp)
    g.ensure_low_risk_policies()
    out = g.grow_from_gap(_kelvin_gap())
    assert out.accepted is True

    disp = LowRiskSolverDispatcher(cp)
    for _ in range(4):
        disp.dispatch(DispatchQuery(
            family_kind="scalar_unit_conversion", inputs={"x": 25.0}
        ))
    disp.flush_kpi_snapshot()

    panel = build_scale_aware_panels(control_plane=cp).autonomy_low_risk_kpis
    assert panel.available is True
    metrics = {item["metric"]: item["value"] for item in panel.items}
    assert metrics["auto_promoted_solvers_active"] == 1
    assert metrics["promotion_decisions_auto_promoted_total"] == 1
    assert metrics["promotion_decisions_rejected_total"] == 0
    assert metrics["promotion_decisions_rollback_total"] == 0
    assert metrics["dispatcher_hits_total_at_last_snapshot"] == 4
    assert metrics["dispatcher_misses_total_at_last_snapshot"] == 0


def test_autonomy_panel_reflects_rollback_in_aggregate(cp: ControlPlaneDB) -> None:
    from waggledance.core.autonomy_growth import AutoPromotionEngine
    g = LowRiskGrower(cp)
    g.ensure_low_risk_policies()
    g.grow_from_gap(_kelvin_gap())
    AutoPromotionEngine(cp).rollback("celsius_to_kelvin_v1", "test_rollback")

    panel = build_scale_aware_panels(control_plane=cp).autonomy_low_risk_kpis
    metrics = {item["metric"]: item["value"] for item in panel.items}
    assert metrics["auto_promoted_solvers_active"] == 0
    assert metrics["auto_promoted_solvers_deactivated"] == 1
    assert metrics["promotion_decisions_rollback_total"] == 1


def test_autonomy_panel_does_not_list_per_solver_state(cp: ControlPlaneDB) -> None:
    """Scale invariant: the panel is aggregate-only.

    Even if many solvers are auto-promoted, the panel must not return
    one item per solver — every item is a metric_name/value pair.
    """

    g = LowRiskGrower(cp)
    g.ensure_low_risk_policies()
    for i in range(5):
        g.grow_from_gap(_kelvin_gap(name=f"k_to_c_v{i}"))

    panel = build_scale_aware_panels(control_plane=cp).autonomy_low_risk_kpis
    assert panel.available is True
    for item in panel.items:
        assert "metric" in item, (
            "panel must use aggregate metric/value pairs, not per-solver rows"
        )
        # No solver_name in any panel row
        assert "solver_name" not in item
