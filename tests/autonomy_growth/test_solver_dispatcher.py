# SPDX-License-Identifier: Apache-2.0
"""Runtime-executable dispatcher tests."""

from __future__ import annotations

import json

import pytest

from waggledance.core.autonomy_growth.solver_dispatcher import (
    DispatchQuery,
    LowRiskSolverDispatcher,
)
from waggledance.core.storage.control_plane import ControlPlaneDB


@pytest.fixture()
def cp(tmp_path):
    db = ControlPlaneDB(tmp_path / "cp.sqlite")
    db.migrate()
    yield db
    db.close()


def _seed_promoted_artifact(
    cp: ControlPlaneDB,
    family_kind: str,
    solver_name: str,
    artifact: dict,
) -> int:
    fam = cp.upsert_solver_family(family_kind, "1.0")
    solver = cp.upsert_solver(
        family_name=fam.name,
        name=solver_name,
        version="1.0",
        spec_hash="hash_" + solver_name,
        status="auto_promoted",
    )
    canonical = json.dumps({"kind": family_kind, "name": solver_name},
                           sort_keys=True)
    cp.upsert_solver_artifact(
        solver_id=solver.id,
        family_kind=family_kind,
        artifact_id=f"art_{solver_name}",
        spec_canonical_json=canonical,
        artifact_json=json.dumps(artifact, sort_keys=True),
    )
    return solver.id


def test_dispatch_hit_scalar_unit_conversion(cp: ControlPlaneDB) -> None:
    _seed_promoted_artifact(
        cp, "scalar_unit_conversion", "celsius_to_kelvin_v1",
        {"kind": "scalar_unit_conversion", "factor": 1.0, "offset": 273.15},
    )
    disp = LowRiskSolverDispatcher(cp)
    result = disp.dispatch(
        DispatchQuery(family_kind="scalar_unit_conversion", inputs={"x": 0.0})
    )
    assert result.matched is True
    assert result.reason == "hit"
    assert result.solver_name == "celsius_to_kelvin_v1"
    assert result.output == pytest.approx(273.15)
    assert disp.stats.hits == 1
    assert disp.stats.misses == 0


def test_dispatch_miss_no_solver(cp: ControlPlaneDB) -> None:
    disp = LowRiskSolverDispatcher(cp)
    result = disp.dispatch(
        DispatchQuery(family_kind="scalar_unit_conversion", inputs={"x": 1})
    )
    assert result.matched is False
    assert result.reason == "miss_no_solver"
    assert disp.stats.hits == 0
    assert disp.stats.misses == 1


def test_dispatch_miss_family_not_low_risk(cp: ControlPlaneDB) -> None:
    disp = LowRiskSolverDispatcher(cp)
    result = disp.dispatch(
        DispatchQuery(family_kind="temporal_window_rule",
                       inputs={"x": 1})
    )
    assert result.matched is False
    assert result.reason == "miss_family_not_low_risk"


def test_dispatch_only_returns_auto_promoted_solvers(cp: ControlPlaneDB) -> None:
    """A solver with status='draft' must not be dispatched."""

    fam = cp.upsert_solver_family("threshold_rule", "1.0")
    draft = cp.upsert_solver(
        family_name=fam.name, name="draft_thr", version="1.0",
        status="draft",
    )
    cp.upsert_solver_artifact(
        solver_id=draft.id, family_kind="threshold_rule",
        artifact_id="art_draft",
        spec_canonical_json="{}",
        artifact_json=json.dumps({
            "kind": "threshold_rule", "threshold": 30,
            "operator": ">", "true_label": "hot", "false_label": "cool",
        }),
    )
    disp = LowRiskSolverDispatcher(cp)
    result = disp.dispatch(
        DispatchQuery(family_kind="threshold_rule", inputs={"x": 50})
    )
    assert result.matched is False
    assert result.reason == "miss_no_solver"


def test_dispatch_by_solver_name_routes_to_specific_solver(
    cp: ControlPlaneDB,
) -> None:
    _seed_promoted_artifact(
        cp, "lookup_table", "color_to_action",
        {"kind": "lookup_table",
         "table": {"red": "stop", "green": "go"},
         "default": "wait"},
    )
    _seed_promoted_artifact(
        cp, "lookup_table", "color_to_speed",
        {"kind": "lookup_table",
         "table": {"red": 0, "green": 100}, "default": 50},
    )
    disp = LowRiskSolverDispatcher(cp)
    r1 = disp.dispatch(DispatchQuery(
        family_kind="lookup_table",
        solver_name="color_to_action",
        inputs={"key": "red"},
    ))
    assert r1.output == "stop"
    r2 = disp.dispatch(DispatchQuery(
        family_kind="lookup_table",
        solver_name="color_to_speed",
        inputs={"key": "green"},
    ))
    assert r2.output == 100


def test_dispatch_executor_error_is_not_a_hit(cp: ControlPlaneDB) -> None:
    """If the artifact is malformed at runtime, dispatcher reports miss."""

    fam = cp.upsert_solver_family("threshold_rule", "1.0")
    solver = cp.upsert_solver(
        family_name=fam.name, name="bad_thr", version="1.0",
        status="auto_promoted",
    )
    cp.upsert_solver_artifact(
        solver_id=solver.id, family_kind="threshold_rule",
        artifact_id="art_bad",
        spec_canonical_json="{}",
        # missing required keys
        artifact_json=json.dumps({"kind": "threshold_rule"}),
    )
    disp = LowRiskSolverDispatcher(cp)
    result = disp.dispatch(
        DispatchQuery(family_kind="threshold_rule", inputs={"x": 1})
    )
    assert result.matched is False
    assert result.reason == "miss_executor_error"
    assert result.error  # message present


def test_flush_kpi_snapshot_records_running_counters(
    cp: ControlPlaneDB,
) -> None:
    _seed_promoted_artifact(
        cp, "scalar_unit_conversion", "k_to_c_v1",
        {"kind": "scalar_unit_conversion", "factor": 1.0, "offset": -273.15},
    )
    disp = LowRiskSolverDispatcher(cp)
    for _ in range(5):
        disp.dispatch(
            DispatchQuery(family_kind="scalar_unit_conversion",
                            inputs={"x": 300.0})
        )
    disp.dispatch(
        DispatchQuery(family_kind="scalar_unit_conversion",
                        inputs={})  # missing input → miss_executor_error
    )
    disp.flush_kpi_snapshot()
    snap = cp.latest_autonomy_kpi()
    assert snap is not None
    assert snap.dispatcher_hits_total == 5
    assert snap.dispatcher_misses_total == 1
    parsed = json.loads(snap.per_family_counts_json or "{}")
    assert parsed.get("scalar_unit_conversion") == 5


def test_dispatcher_does_not_write_to_cp_during_dispatch(
    cp: ControlPlaneDB,
) -> None:
    """Read-only at query time. Counter writes must come from explicit flush."""

    _seed_promoted_artifact(
        cp, "lookup_table", "x", {"kind": "lookup_table", "table": {"a": 1}},
    )
    initial_kpi = cp.latest_autonomy_kpi()
    assert initial_kpi is None
    disp = LowRiskSolverDispatcher(cp)
    for _ in range(20):
        disp.dispatch(
            DispatchQuery(family_kind="lookup_table", inputs={"key": "a"})
        )
    # Still no KPI snapshot until the caller flushes
    assert cp.latest_autonomy_kpi() is None
    disp.flush_kpi_snapshot()
    assert cp.latest_autonomy_kpi() is not None
