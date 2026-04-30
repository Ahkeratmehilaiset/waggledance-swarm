# SPDX-License-Identifier: Apache-2.0
"""End-to-end tests for the no-human auto-promotion engine."""

from __future__ import annotations

import json

import pytest

from waggledance.core.autonomy_growth.auto_promotion_engine import (
    AutoPromotionEngine,
    PROMOTION_DECIDED_BY,
    PromotionRequest,
)
from waggledance.core.autonomy_growth.solver_dispatcher import (
    DispatchQuery,
    LowRiskSolverDispatcher,
)
from waggledance.core.solver_synthesis.declarative_solver_spec import (
    SolverSpec,
)
from waggledance.core.storage.control_plane import ControlPlaneDB


@pytest.fixture()
def cp(tmp_path):
    db = ControlPlaneDB(tmp_path / "cp.sqlite")
    db.migrate()
    yield db
    db.close()


def _scalar_unit_conversion_spec(name: str = "celsius_to_kelvin_v1") -> SolverSpec:
    return SolverSpec(
        schema_version=1,
        spec_id=f"spec_{name}",
        family_kind="scalar_unit_conversion",
        solver_name=name,
        cell_id="general",
        spec={"from_unit": "C", "to_unit": "K",
              "factor": 1.0, "offset": 273.15},
        source="phase11_test",
        source_kind="hand_authored",
    )


def _scalar_unit_conversion_oracle(inputs, artifact):
    """Independent reference implementation."""

    return float(inputs["x"]) * float(artifact["factor"]) + float(
        artifact.get("offset", 0.0)
    )


def _validation_cases_for_celsius_to_kelvin():
    return [
        {"inputs": {"x": 0.0}, "expected": 273.15},
        {"inputs": {"x": 100.0}, "expected": 373.15},
        {"inputs": {"x": -40.0}, "expected": 233.15},
        {"inputs": {"x": 25.0}, "expected": 298.15},
    ]


def _shadow_samples_simple():
    return [{"x": float(i) * 1.7} for i in range(20)]


def test_happy_path_auto_promotes_low_risk_candidate(cp: ControlPlaneDB) -> None:
    cp.upsert_family_policy("scalar_unit_conversion", is_low_risk=True)
    eng = AutoPromotionEngine(cp)
    outcome = eng.evaluate_candidate(PromotionRequest(
        spec=_scalar_unit_conversion_spec(),
        validation_cases=_validation_cases_for_celsius_to_kelvin(),
        shadow_samples=_shadow_samples_simple(),
        oracle=_scalar_unit_conversion_oracle,
        oracle_kind="formula_recompute",
    ))
    assert outcome.decided is True
    assert outcome.decision == "auto_promoted"
    assert outcome.solver_id is not None
    assert outcome.invariant_failed is None
    assert outcome.validation is not None and outcome.validation.all_passed
    assert outcome.shadow is not None
    assert outcome.shadow.agreement_rate == 1.0

    # Solver row reflects auto_promoted
    solver = cp.get_solver("celsius_to_kelvin_v1")
    assert solver is not None and solver.status == "auto_promoted"

    # Promotion decision is recorded
    decisions = cp.list_promotion_decisions(solver_id=solver.id)
    assert any(d.decision == "auto_promoted" for d in decisions)
    assert all(d.decided_by == PROMOTION_DECIDED_BY for d in decisions)

    # Artifact persisted and dispatcher can use it
    artifact = cp.get_solver_artifact(solver.id)
    assert artifact is not None
    disp = LowRiskSolverDispatcher(cp)
    res = disp.dispatch(DispatchQuery(
        family_kind="scalar_unit_conversion", inputs={"x": 0.0}
    ))
    assert res.matched and res.output == pytest.approx(273.15)


def test_rejects_when_family_not_in_allowlist(cp: ControlPlaneDB) -> None:
    eng = AutoPromotionEngine(cp)
    spec = SolverSpec(
        schema_version=1,
        spec_id="spec_temporal_x",
        family_kind="temporal_window_rule",  # excluded
        solver_name="not_allowed",
        cell_id="general",
        spec={
            "window_seconds": 60, "aggregator": "mean",
            "threshold": 1.0, "operator": ">",
        },
        source="phase11_test",
        source_kind="hand_authored",
    )
    outcome = eng.evaluate_candidate(PromotionRequest(
        spec=spec, validation_cases=[], shadow_samples=[],
        oracle=lambda inp, art: None,
    ))
    assert outcome.decided is False
    assert outcome.decision == "rejected"
    assert outcome.invariant_failed == "I1_family_not_in_allowlist"


def test_rejects_when_family_policy_missing(cp: ControlPlaneDB) -> None:
    # No upsert_family_policy call
    eng = AutoPromotionEngine(cp)
    outcome = eng.evaluate_candidate(PromotionRequest(
        spec=_scalar_unit_conversion_spec(),
        validation_cases=_validation_cases_for_celsius_to_kelvin(),
        shadow_samples=_shadow_samples_simple(),
        oracle=_scalar_unit_conversion_oracle,
    ))
    assert outcome.decided is False
    assert outcome.invariant_failed == "I2_family_policy_missing"


def test_rejects_when_family_policy_not_low_risk(cp: ControlPlaneDB) -> None:
    cp.upsert_family_policy("scalar_unit_conversion", is_low_risk=False)
    eng = AutoPromotionEngine(cp)
    outcome = eng.evaluate_candidate(PromotionRequest(
        spec=_scalar_unit_conversion_spec(),
        validation_cases=_validation_cases_for_celsius_to_kelvin(),
        shadow_samples=_shadow_samples_simple(),
        oracle=_scalar_unit_conversion_oracle,
    ))
    assert outcome.decided is False
    assert outcome.invariant_failed == "I2_family_policy_not_low_risk"


def test_rejects_when_validation_fails(cp: ControlPlaneDB) -> None:
    cp.upsert_family_policy("scalar_unit_conversion", is_low_risk=True)
    bad_cases = [
        {"inputs": {"x": 0.0}, "expected": 999.99},  # wrong oracle answer
    ]
    eng = AutoPromotionEngine(cp)
    outcome = eng.evaluate_candidate(PromotionRequest(
        spec=_scalar_unit_conversion_spec(),
        validation_cases=bad_cases,
        shadow_samples=_shadow_samples_simple(),
        oracle=_scalar_unit_conversion_oracle,
    ))
    assert outcome.decided is False
    assert outcome.invariant_failed == "I5_validation_pass_rate_below_min"
    assert cp.get_solver("celsius_to_kelvin_v1") is None  # no partial commit


def test_rejects_when_shadow_sample_count_below_min(cp: ControlPlaneDB) -> None:
    cp.upsert_family_policy(
        "scalar_unit_conversion", is_low_risk=True, min_shadow_samples=10
    )
    eng = AutoPromotionEngine(cp)
    outcome = eng.evaluate_candidate(PromotionRequest(
        spec=_scalar_unit_conversion_spec(),
        validation_cases=_validation_cases_for_celsius_to_kelvin(),
        shadow_samples=[{"x": 1.0}, {"x": 2.0}, {"x": 3.0}],  # only 3 < 10
        oracle=_scalar_unit_conversion_oracle,
    ))
    assert outcome.decided is False
    assert outcome.invariant_failed == "I6_shadow_sample_count_below_min"


def test_rejects_when_shadow_agreement_below_min(cp: ControlPlaneDB) -> None:
    cp.upsert_family_policy("scalar_unit_conversion", is_low_risk=True)

    def lying_oracle(inputs, artifact):
        # Disagree on every sample
        return _scalar_unit_conversion_oracle(inputs, artifact) + 1.0

    eng = AutoPromotionEngine(cp)
    outcome = eng.evaluate_candidate(PromotionRequest(
        spec=_scalar_unit_conversion_spec(),
        validation_cases=_validation_cases_for_celsius_to_kelvin(),
        shadow_samples=_shadow_samples_simple(),
        oracle=lying_oracle,
    ))
    assert outcome.decided is False
    assert outcome.invariant_failed == "I7_shadow_agreement_rate_below_min"
    # No solver was created because invariant fired before commit
    assert cp.get_solver("celsius_to_kelvin_v1") is None


def test_rejects_when_solver_already_auto_promoted(cp: ControlPlaneDB) -> None:
    cp.upsert_family_policy("scalar_unit_conversion", is_low_risk=True)
    eng = AutoPromotionEngine(cp)
    request = PromotionRequest(
        spec=_scalar_unit_conversion_spec(),
        validation_cases=_validation_cases_for_celsius_to_kelvin(),
        shadow_samples=_shadow_samples_simple(),
        oracle=_scalar_unit_conversion_oracle,
    )
    first = eng.evaluate_candidate(request)
    assert first.decision == "auto_promoted"
    second = eng.evaluate_candidate(request)
    assert second.decided is False
    assert second.invariant_failed == "I8_solver_already_auto_promoted"


def test_rejects_when_promotion_budget_exhausted(cp: ControlPlaneDB) -> None:
    cp.upsert_family_policy(
        "scalar_unit_conversion", is_low_risk=True, max_auto_promote=1
    )
    eng = AutoPromotionEngine(cp)
    spec_a = _scalar_unit_conversion_spec("solver_a")
    spec_b = _scalar_unit_conversion_spec("solver_b")
    a = eng.evaluate_candidate(PromotionRequest(
        spec=spec_a,
        validation_cases=_validation_cases_for_celsius_to_kelvin(),
        shadow_samples=_shadow_samples_simple(),
        oracle=_scalar_unit_conversion_oracle,
    ))
    assert a.decision == "auto_promoted"
    b = eng.evaluate_candidate(PromotionRequest(
        spec=spec_b,
        validation_cases=_validation_cases_for_celsius_to_kelvin(),
        shadow_samples=_shadow_samples_simple(),
        oracle=_scalar_unit_conversion_oracle,
    ))
    assert b.decided is False
    assert b.invariant_failed == "I9_family_promotion_budget_exhausted"


def test_rollback_flips_status_and_records_decision(cp: ControlPlaneDB) -> None:
    cp.upsert_family_policy("scalar_unit_conversion", is_low_risk=True)
    eng = AutoPromotionEngine(cp)
    eng.evaluate_candidate(PromotionRequest(
        spec=_scalar_unit_conversion_spec(),
        validation_cases=_validation_cases_for_celsius_to_kelvin(),
        shadow_samples=_shadow_samples_simple(),
        oracle=_scalar_unit_conversion_oracle,
    ))
    assert cp.get_solver("celsius_to_kelvin_v1").status == "auto_promoted"

    rb = eng.rollback("celsius_to_kelvin_v1", "shadow_drift_in_field")
    assert rb.decision == "rolled_back"
    assert cp.get_solver("celsius_to_kelvin_v1").status == "deactivated"
    decisions = cp.list_promotion_decisions(
        solver_id=rb.solver_id, decision="rollback"
    )
    assert len(decisions) == 1
    assert decisions[0].rollback_reason == "shadow_drift_in_field"

    # Dispatcher must not return a deactivated solver
    disp = LowRiskSolverDispatcher(cp)
    res = disp.dispatch(DispatchQuery(
        family_kind="scalar_unit_conversion", inputs={"x": 0.0}
    ))
    assert res.matched is False


def test_rollback_refuses_unknown_or_non_promoted_solver(
    cp: ControlPlaneDB,
) -> None:
    eng = AutoPromotionEngine(cp)
    miss = eng.rollback("does_not_exist", "no_reason")
    assert miss.invariant_failed == "rollback_solver_not_found"
    fam = cp.upsert_solver_family("scalar_unit_conversion", "1.0")
    cp.upsert_solver(
        family_name=fam.name, name="draft_only", version="1.0",
        status="draft",
    )
    not_promoted = eng.rollback("draft_only", "stale")
    assert not_promoted.invariant_failed == "rollback_solver_not_auto_promoted"


def test_threshold_rule_round_trip(cp: ControlPlaneDB) -> None:
    """Second family — verify the loop is family-agnostic within allowlist."""

    cp.upsert_family_policy("threshold_rule", is_low_risk=True)
    eng = AutoPromotionEngine(cp)
    spec = SolverSpec(
        schema_version=1,
        spec_id="spec_hot",
        family_kind="threshold_rule",
        solver_name="hot_threshold_v1",
        cell_id="general",
        spec={"threshold": 30.0, "operator": ">",
              "true_label": "hot", "false_label": "cool"},
        source="phase11_test",
        source_kind="hand_authored",
    )
    cases = [
        {"inputs": {"x": 50}, "expected": "hot"},
        {"inputs": {"x": 25}, "expected": "cool"},
        {"inputs": {"x": 30}, "expected": "cool"},
    ]
    samples = [{"x": float(i)} for i in range(-10, 50)]

    def oracle(inputs, artifact):
        return artifact["true_label"] if float(inputs["x"]) > float(
            artifact["threshold"]
        ) else artifact["false_label"]

    outcome = eng.evaluate_candidate(PromotionRequest(
        spec=spec, validation_cases=cases, shadow_samples=samples,
        oracle=oracle, oracle_kind="formula_recompute",
    ))
    assert outcome.decision == "auto_promoted"


def test_no_partial_activation_on_shadow_failure(cp: ControlPlaneDB) -> None:
    """If shadow fails after compile + validation pass, no rows leak."""

    cp.upsert_family_policy("scalar_unit_conversion", is_low_risk=True)
    eng = AutoPromotionEngine(cp)

    def liar(inputs, artifact):
        return _scalar_unit_conversion_oracle(inputs, artifact) + 100.0

    outcome = eng.evaluate_candidate(PromotionRequest(
        spec=_scalar_unit_conversion_spec("partial_test"),
        validation_cases=_validation_cases_for_celsius_to_kelvin(),
        shadow_samples=_shadow_samples_simple(),
        oracle=liar,
    ))
    assert outcome.decided is False
    # No solver row, no artifact row, no validation_run row, no shadow_eval row
    assert cp.get_solver("partial_test") is None
    stats = cp.stats()
    assert stats.table_counts["solvers"] == 0
    assert stats.table_counts["solver_artifacts"] == 0
    assert stats.table_counts["validation_runs"] == 0
    assert stats.table_counts["shadow_evaluations"] == 0
    assert stats.table_counts["promotion_decisions"] == 0
