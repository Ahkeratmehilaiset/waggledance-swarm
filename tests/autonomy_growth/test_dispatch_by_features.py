# SPDX-License-Identifier: Apache-2.0
"""Capability-aware dispatch tests (Phase 13 P3)."""

from __future__ import annotations

import pytest

from waggledance.core.autonomy_growth import (
    AutogrowthScheduler,
    GapSignal,
    LowRiskGrower,
    LowRiskSolverDispatcher,
    digest_signals_into_intents,
    extract_features,
    feature_dimensions,
)
from waggledance.core.storage.control_plane import ControlPlaneDB


@pytest.fixture()
def cp(tmp_path):
    db = ControlPlaneDB(tmp_path / "cp.sqlite")
    db.migrate()
    g = LowRiskGrower(db)
    g.ensure_low_risk_policies()
    yield db
    db.close()


def _grow_via_loop(cp: ControlPlaneDB, family: str, name: str,
                   spec_seed: dict, cell: str = "thermal") -> int:
    digest_signals_into_intents(
        cp,
        candidate_signals=[GapSignal(
            kind="runtime_miss", family_kind=family,
            cell_coord=cell, intent_seed=name, spec_seed=spec_seed,
        )],
        min_signals_per_intent=1, autoenqueue=True,
    )
    AutogrowthScheduler(cp).run_until_idle()
    # Solver naming: f"{solver_name_seed}_i{intent_id:04d}"; iterate
    # to find the matching solver regardless of intent id.
    for sid in range(1, 100):
        s = cp.get_solver(f"{name}_i{sid:04d}")
        if s is not None and s.status == "auto_promoted":
            return s.id
    raise AssertionError(f"no auto_promoted solver found for {name}")


def _scalar_seed(name: str, from_u: str, to_u: str,
                 factor: float, offset: float = 0.0) -> dict:
    return {
        "spec": {"from_unit": from_u, "to_unit": to_u,
                  "factor": factor, "offset": offset},
        "validation_cases": [
            {"inputs": {"x": 0.0}, "expected": offset},
            {"inputs": {"x": 1.0}, "expected": factor + offset},
        ],
        "shadow_samples": [{"x": float(i)} for i in range(10)],
        "solver_name_seed": name,
        "cell_id": "thermal",
    }


def test_features_recorded_at_promotion_time(cp: ControlPlaneDB) -> None:
    sid = _grow_via_loop(
        cp, "scalar_unit_conversion", "celsius_to_kelvin",
        _scalar_seed("celsius_to_kelvin", "C", "K", 1.0, 273.15),
    )
    feats = cp.get_solver_capability_features(sid)
    by_name = {f.feature_name: f.feature_value for f in feats}
    assert by_name == {"from_unit": "C", "to_unit": "K"}


def test_dispatch_by_features_picks_correct_solver(cp: ControlPlaneDB) -> None:
    """Two solvers in the same family with different features —
    feature-based dispatch must route to the matching one, not the
    most-recently-promoted one."""

    _grow_via_loop(
        cp, "scalar_unit_conversion", "celsius_to_kelvin",
        _scalar_seed("celsius_to_kelvin", "C", "K", 1.0, 273.15),
    )
    _grow_via_loop(
        cp, "scalar_unit_conversion", "fahrenheit_to_celsius",
        _scalar_seed("fahrenheit_to_celsius", "F", "C", 5.0 / 9.0, -160.0 / 9.0),
    )
    disp = LowRiskSolverDispatcher(cp)
    # Family-FIFO would pick the most recent (fahrenheit_to_celsius).
    # Capability-aware lookup picks the first (celsius_to_kelvin).
    res = disp.dispatch_by_features(
        family_kind="scalar_unit_conversion",
        features={"from_unit": "C", "to_unit": "K"},
        inputs={"x": 0.0},
    )
    assert res.matched is True
    assert res.reason == "hit_by_features"
    assert res.solver_name and res.solver_name.startswith("celsius_to_kelvin")
    assert res.output == pytest.approx(273.15)


def test_dispatch_by_features_miss_when_no_match(cp: ControlPlaneDB) -> None:
    _grow_via_loop(
        cp, "scalar_unit_conversion", "celsius_to_kelvin",
        _scalar_seed("celsius_to_kelvin", "C", "K", 1.0, 273.15),
    )
    disp = LowRiskSolverDispatcher(cp)
    res = disp.dispatch_by_features(
        family_kind="scalar_unit_conversion",
        features={"from_unit": "miles", "to_unit": "feet"},
        inputs={"x": 1.0},
    )
    assert res.matched is False
    assert res.reason == "miss_no_solver"


def test_dispatch_by_features_refuses_empty_features(cp: ControlPlaneDB) -> None:
    disp = LowRiskSolverDispatcher(cp)
    res = disp.dispatch_by_features(
        family_kind="scalar_unit_conversion",
        features={},
        inputs={"x": 1.0},
    )
    assert res.matched is False
    assert res.reason == "miss_no_features_supplied"


def test_dispatch_by_features_refuses_non_low_risk(cp: ControlPlaneDB) -> None:
    disp = LowRiskSolverDispatcher(cp)
    res = disp.dispatch_by_features(
        family_kind="temporal_window_rule",
        features={"x": "y"},
        inputs={"x": 1},
    )
    assert res.matched is False
    assert res.reason == "miss_family_not_low_risk"


def test_extract_features_per_family() -> None:
    """Sanity: each allowlisted family yields a non-empty feature set
    when given an example spec, and the dimension list matches."""

    pairs = [
        ("scalar_unit_conversion",
          {"from_unit": "C", "to_unit": "K", "factor": 1.0}),
        ("lookup_table",
          {"table": {"a": 1}, "default": 0, "domain": "color"}),
        ("threshold_rule",
          {"threshold": 30, "operator": ">", "true_label": "hot",
            "false_label": "cool", "subject": "temperature"}),
        ("interval_bucket_classifier",
          {"intervals": [], "subject": "temperature"}),
        ("linear_arithmetic",
          {"coefficients": [1.0], "intercept": 0.0,
            "input_columns": ["a"]}),
        ("bounded_interpolation",
          {"knots": [], "method": "linear", "min_x": 0, "max_x": 1,
            "x_var": "x", "y_var": "y"}),
    ]
    for family, spec in pairs:
        feats = extract_features(family, spec)
        dims = feature_dimensions(family)
        assert feats, f"{family}: no features extracted"
        for d in dims:
            assert d in feats, f"{family}: missing dimension {d}"
