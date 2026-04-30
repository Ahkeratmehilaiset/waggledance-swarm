# SPDX-License-Identifier: Apache-2.0
"""Grower + teacher-lane wiring tests."""

from __future__ import annotations

import pytest

from waggledance.core.autonomy_growth.low_risk_grower import (
    GapInput,
    LowRiskGrower,
    PRIMARY_TEACHER_LANE_ID,
)
from waggledance.core.autonomy_growth.solver_dispatcher import (
    DispatchQuery,
    LowRiskSolverDispatcher,
)
from waggledance.core.solver_synthesis.llm_solver_generator import (
    _DEFAULT_PRIORITY_LIST,
)
from waggledance.core.storage.control_plane import ControlPlaneDB


@pytest.fixture()
def cp(tmp_path):
    db = ControlPlaneDB(tmp_path / "cp.sqlite")
    db.migrate()
    yield db
    db.close()


def _kelvin_oracle(inputs, artifact):
    return float(inputs["x"]) * float(artifact["factor"]) + float(
        artifact.get("offset", 0.0)
    )


def _kelvin_gap(name="celsius_to_kelvin_v1") -> GapInput:
    return GapInput(
        family_kind="scalar_unit_conversion",
        solver_name=name,
        cell_id="general",
        spec={"from_unit": "C", "to_unit": "K",
              "factor": 1.0, "offset": 273.15},
        source="phase11_test",
        source_kind="hand_authored",
        validation_cases=[
            {"inputs": {"x": 0.0}, "expected": 273.15},
            {"inputs": {"x": 100.0}, "expected": 373.15},
            {"inputs": {"x": -40.0}, "expected": 233.15},
        ],
        shadow_samples=[{"x": float(i) * 1.7} for i in range(20)],
        oracle=_kelvin_oracle,
        oracle_kind="formula_recompute",
    )


def test_grower_ensures_policies_for_all_allowlisted_families(
    cp: ControlPlaneDB,
) -> None:
    g = LowRiskGrower(cp)
    g.ensure_low_risk_policies(min_shadow_samples=1)
    pols = cp.list_family_policies(low_risk_only=True)
    kinds = sorted(p.family_kind for p in pols)
    assert kinds == [
        "bounded_interpolation",
        "interval_bucket_classifier",
        "linear_arithmetic",
        "lookup_table",
        "scalar_unit_conversion",
        "threshold_rule",
    ]
    for p in pols:
        assert p.is_low_risk is True
        assert p.min_shadow_samples == 1


def test_grower_grows_a_real_candidate_end_to_end(cp: ControlPlaneDB) -> None:
    g = LowRiskGrower(cp)
    g.ensure_low_risk_policies()
    out = g.grow_from_gap(_kelvin_gap())
    assert out.accepted is True
    assert out.reason == "auto_promoted"
    assert out.promotion and out.promotion.decision == "auto_promoted"

    # The runtime dispatcher can immediately serve this solver
    disp = LowRiskSolverDispatcher(cp)
    res = disp.dispatch(DispatchQuery(
        family_kind="scalar_unit_conversion", inputs={"x": 0.0}
    ))
    assert res.matched is True
    assert res.output == pytest.approx(273.15)
    assert res.solver_name == "celsius_to_kelvin_v1"


def test_grower_rejects_excluded_family_without_calling_provider(
    cp: ControlPlaneDB,
) -> None:
    g = LowRiskGrower(cp)
    g.ensure_low_risk_policies()
    # temporal_window_rule is not in the allowlist; the grower must
    # refuse without consulting any provider.
    bad = GapInput(
        family_kind="temporal_window_rule",
        solver_name="some_window_rule",
        cell_id="general",
        spec={"window_seconds": 60, "aggregator": "mean",
              "threshold": 1.0, "operator": ">"},
        source="t", source_kind="t",
        validation_cases=[], shadow_samples=[],
        oracle=lambda inp, art: None,
    )
    out = g.grow_from_gap(bad)
    assert out.accepted is False
    assert out.reason == "rejected:I1_family_not_in_allowlist"


def test_grower_returns_spec_invalid_for_malformed_spec(cp: ControlPlaneDB) -> None:
    g = LowRiskGrower(cp)
    g.ensure_low_risk_policies()
    # Missing required spec key 'factor' for scalar_unit_conversion
    bad = GapInput(
        family_kind="scalar_unit_conversion",
        solver_name="missing_factor",
        cell_id="general",
        spec={"from_unit": "C", "to_unit": "K"},
        source="t", source_kind="t",
        validation_cases=[],
        shadow_samples=[],
        oracle=_kelvin_oracle,
    )
    out = g.grow_from_gap(bad)
    assert out.accepted is False
    assert out.reason == "spec_invalid"


def test_primary_teacher_lane_id_matches_phase10_default(cp: ControlPlaneDB) -> None:
    """Regression guard: Phase 10 wired claude_code_builder_lane as the
    first preference in the LLM solver generator's provider priority
    list. Phase 11 documents that as the *primary teacher lane*. If a
    future change demotes Claude Code without an explicit migration,
    this test fails."""

    g = LowRiskGrower(cp)
    assert g.primary_teacher_lane_id == "claude_code_builder_lane"
    assert _DEFAULT_PRIORITY_LIST[0] == "claude_code_builder_lane"


def test_grower_idempotent_policy_install(cp: ControlPlaneDB) -> None:
    g = LowRiskGrower(cp)
    g.ensure_low_risk_policies(min_shadow_samples=2)
    g.ensure_low_risk_policies(min_shadow_samples=7)
    pol = cp.get_family_policy("scalar_unit_conversion")
    assert pol is not None and pol.min_shadow_samples == 7  # last call wins
