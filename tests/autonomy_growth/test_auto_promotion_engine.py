# SPDX-License-Identifier: Apache-2.0
"""End-to-end tests for the no-human auto-promotion engine."""

from __future__ import annotations

import json

import pytest

from waggledance.core.autonomy_growth.auto_promotion_engine import (
    ADVERSARIAL_CORPUS_MIN_CASES,
    AutoPromotionReceiptEmissionError,
    AutoPromotionEngine,
    PROMOTION_DECIDED_BY,
    PromotionRequest,
    build_promotion_decision_receipt,
)
from waggledance.core.autonomy_growth.solver_dispatcher import (
    DispatchQuery,
    LowRiskSolverDispatcher,
)
from waggledance.core.solver_synthesis.declarative_solver_spec import (
    SolverSpec,
)
from waggledance.core.solver_synthesis.deterministic_solver_compiler import (
    compile_spec,
)
from waggledance.core.magma.canonical import sha256_digest
from waggledance.core.magma.adversarial_corpus_eval import REQUIRED_DEFECT_TYPES
from waggledance.core.magma.adversarial_gate import verify_adversarial_corpus_gate
from waggledance.core.storage.control_plane import ControlPlaneDB, ControlPlaneError


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


def _valid_adversarial_cases(count: int) -> list[dict[str, object]]:
    required = sorted(REQUIRED_DEFECT_TYPES)
    return [
        {
            "case_id": f"c{i}",
            "defect_class": required[i % len(required)],
            "ok": True,
        }
        for i in range(count)
    ]


def _assert_adversarial_gate_reason(
    *,
    report: dict[str, object],
    expected_solver_hash: str,
    reason_fragment: str,
) -> None:
    gate = verify_adversarial_corpus_gate(
        report=report,
        expected_solver_hash=expected_solver_hash,
        min_cases=ADVERSARIAL_CORPUS_MIN_CASES,
    )
    assert gate.ok is False
    assert any(reason_fragment in reason for reason in gate.reasons)
    assert not any(
        "missing/invalid defect_class" in reason for reason in gate.reasons
    )


def _promotion_request(name: str) -> PromotionRequest:
    return PromotionRequest(
        spec=_scalar_unit_conversion_spec(name),
        validation_cases=_validation_cases_for_celsius_to_kelvin(),
        shadow_samples=_shadow_samples_simple(),
        oracle=_scalar_unit_conversion_oracle,
        oracle_kind="formula_recompute",
    )


class _CommitFailingConnection:
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, *args, **kwargs):
        if str(sql).strip().upper() == "COMMIT":
            raise RuntimeError("forced commit failure")
        return self._conn.execute(sql, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._conn, name)


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
    assert outcome.promotion_decision_artifact is not None
    assert outcome.promotion_decision_digest == sha256_digest(
        outcome.promotion_decision_artifact
    )

    # Solver row reflects auto_promoted
    solver = cp.get_solver("celsius_to_kelvin_v1")
    assert solver is not None and solver.status == "auto_promoted"

    # Promotion decision is recorded
    decisions = cp.list_promotion_decisions(solver_id=solver.id)
    assert any(d.decision == "auto_promoted" for d in decisions)
    assert all(d.decided_by == PROMOTION_DECIDED_BY for d in decisions)
    decision = next(d for d in decisions if d.decision == "auto_promoted")
    evidence = json.loads(decision.evidence or "{}")
    assert evidence["promotion_decision_digest"] == (
        outcome.promotion_decision_digest
    )

    # Artifact persisted and dispatcher can use it
    artifact = cp.get_solver_artifact(solver.id)
    assert artifact is not None
    disp = LowRiskSolverDispatcher(cp)
    res = disp.dispatch(DispatchQuery(
        family_kind="scalar_unit_conversion", inputs={"x": 0.0}
    ))
    assert res.matched and res.output == pytest.approx(273.15)


def test_auto_promotion_receipt_binding_is_payload_free(
    cp: ControlPlaneDB,
) -> None:
    cp.upsert_family_policy("scalar_unit_conversion", is_low_risk=True)
    base = _scalar_unit_conversion_spec("secret_solver")
    spec = SolverSpec(
        schema_version=base.schema_version,
        spec_id=base.spec_id,
        family_kind=base.family_kind,
        solver_name=base.solver_name,
        cell_id=base.cell_id,
        spec={**base.spec, "private_note": "secret operator text"},
        source=base.source,
        source_kind=base.source_kind,
    )
    eng = AutoPromotionEngine(cp)
    outcome = eng.evaluate_candidate(PromotionRequest(
        spec=spec,
        validation_cases=_validation_cases_for_celsius_to_kelvin(),
        shadow_samples=_shadow_samples_simple(),
        oracle=_scalar_unit_conversion_oracle,
        oracle_kind="formula_recompute",
    ))

    bundle = build_promotion_decision_receipt(
        outcome,
        ts_utc="2026-05-20T17:10:00Z",
    )

    assert bundle["receipt"]["risk_class"] == "local_artifact"
    assert bundle["receipt"]["rco_decision_digest"] == (
        outcome.promotion_decision_digest
    )
    assert bundle["evaluation_result"]["subject_type"] == "promotion"
    assert bundle["evaluation_result"]["verdict"] == "pass"
    assert "secret operator text" not in json.dumps(bundle, sort_keys=True)


def test_auto_promotion_emits_chained_receipts_for_promote_and_rollback(
    cp: ControlPlaneDB,
) -> None:
    cp.upsert_family_policy("scalar_unit_conversion", is_low_risk=True)
    bundles: list[dict] = []
    eng = AutoPromotionEngine(
        cp,
        emit_receipt_bundle=lambda bundle: bundles.append(bundle),
    )

    outcome = eng.evaluate_candidate(PromotionRequest(
        spec=_scalar_unit_conversion_spec("receipt_solver"),
        validation_cases=_validation_cases_for_celsius_to_kelvin(),
        shadow_samples=_shadow_samples_simple(),
        oracle=_scalar_unit_conversion_oracle,
        oracle_kind="formula_recompute",
    ))
    rollback = eng.rollback(
        "receipt_solver",
        rollback_reason="secret rollback reason",
    )

    assert outcome.decision == "auto_promoted"
    assert rollback.decision == "rolled_back"
    assert len(bundles) == 2
    assert bundles[0]["payload"]["decision"] == "auto_promoted"
    assert bundles[0]["receipt"]["prev_receipt_hash"] is None
    assert bundles[1]["payload"]["decision"] == "rolled_back"
    assert bundles[1]["receipt"]["prev_receipt_hash"] == sha256_digest(
        bundles[0]["receipt"]
    )
    assert "secret rollback reason" not in json.dumps(bundles, sort_keys=True)


def test_auto_promotion_receipt_sink_failure_preserves_commit_and_head(
    cp: ControlPlaneDB,
) -> None:
    cp.upsert_family_policy("scalar_unit_conversion", is_low_risk=True)
    bundles: list[dict] = []
    attempts = 0

    def first_emit_fails(bundle: dict) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("receipt boom")
        bundles.append(bundle)

    eng = AutoPromotionEngine(cp, emit_receipt_bundle=first_emit_fails)
    prior_receipt = {"event_id": "magma:auto_promotion:seed"}
    eng._last_emitted_receipt = prior_receipt

    with pytest.raises(
        AutoPromotionReceiptEmissionError,
        match="auto-promotion receipt sink failed",
    ):
        eng.evaluate_candidate(_promotion_request("sink_failure_solver"))

    solver = cp.get_solver("sink_failure_solver")
    assert solver is not None
    assert solver.status == "auto_promoted"
    assert len(cp.list_promotion_decisions(solver_id=solver.id)) == 1
    assert eng._last_emitted_receipt == prior_receipt
    assert bundles == []


def test_auto_promotion_commit_failure_does_not_emit_receipt_or_advance_head(
    cp: ControlPlaneDB,
) -> None:
    cp.upsert_family_policy("scalar_unit_conversion", is_low_risk=True)
    bundles: list[dict] = []
    eng = AutoPromotionEngine(
        cp,
        emit_receipt_bundle=lambda bundle: bundles.append(bundle),
    )
    prior_receipt = {"event_id": "magma:auto_promotion:seed"}
    eng._last_emitted_receipt = prior_receipt
    cp._conn = _CommitFailingConnection(cp._conn)  # type: ignore[assignment]

    with pytest.raises(ControlPlaneError, match="auto-promotion commit failed"):
        eng.evaluate_candidate(_promotion_request("commit_failure_solver"))

    assert bundles == []
    assert eng._last_emitted_receipt == prior_receipt
    assert cp.get_solver("commit_failure_solver") is None
    assert cp.get_solver_family("scalar_unit_conversion") is None
    stats = cp.stats()
    assert stats.table_counts["promotion_decisions"] == 0


def test_auto_promotion_receipt_sink_runs_after_sqlite_commit(
    cp: ControlPlaneDB,
) -> None:
    cp.upsert_family_policy("scalar_unit_conversion", is_low_risk=True)
    observations: list[str] = []

    def assert_committed(bundle: dict) -> None:
        reader = ControlPlaneDB(cp._db_path)  # type: ignore[attr-defined]
        try:
            solver = reader.get_solver("receipt_after_commit_solver")
            assert solver is not None
            assert solver.status == "auto_promoted"
            decisions = reader.list_promotion_decisions(solver_id=solver.id)
            assert any(d.decision == "auto_promoted" for d in decisions)
            observations.append(bundle["payload"]["decision"])
        finally:
            reader.close()

    eng = AutoPromotionEngine(cp, emit_receipt_bundle=assert_committed)

    outcome = eng.evaluate_candidate(
        _promotion_request("receipt_after_commit_solver")
    )

    assert outcome.decision == "auto_promoted"
    assert observations == ["auto_promoted"]
    assert eng._last_emitted_receipt is not None


def test_auto_promotion_rollback_commit_failure_does_not_emit_receipt_or_head(
    cp: ControlPlaneDB,
) -> None:
    cp.upsert_family_policy("scalar_unit_conversion", is_low_risk=True)
    AutoPromotionEngine(cp).evaluate_candidate(
        _promotion_request("rollback_commit_failure_solver")
    )
    bundles: list[dict] = []
    eng = AutoPromotionEngine(
        cp,
        emit_receipt_bundle=lambda bundle: bundles.append(bundle),
    )
    prior_receipt = {"event_id": "magma:auto_promotion:seed"}
    eng._last_emitted_receipt = prior_receipt
    cp._conn = _CommitFailingConnection(cp._conn)  # type: ignore[assignment]

    with pytest.raises(ControlPlaneError, match="rollback failed"):
        eng.rollback("rollback_commit_failure_solver", "forced commit failure")

    assert bundles == []
    assert eng._last_emitted_receipt == prior_receipt
    solver = cp.get_solver("rollback_commit_failure_solver")
    assert solver is not None
    assert solver.status == "auto_promoted"
    assert cp.list_promotion_decisions(
        solver_id=solver.id,
        decision="rollback",
    ) == []


def test_auto_promotion_rollback_receipt_sink_runs_after_sqlite_commit(
    cp: ControlPlaneDB,
) -> None:
    cp.upsert_family_policy("scalar_unit_conversion", is_low_risk=True)
    AutoPromotionEngine(cp).evaluate_candidate(
        _promotion_request("rollback_after_commit_solver")
    )
    observations: list[str] = []

    def assert_committed(bundle: dict) -> None:
        reader = ControlPlaneDB(cp._db_path)  # type: ignore[attr-defined]
        try:
            solver = reader.get_solver("rollback_after_commit_solver")
            assert solver is not None
            assert solver.status == "deactivated"
            decisions = reader.list_promotion_decisions(
                solver_id=solver.id,
                decision="rollback",
            )
            assert len(decisions) == 1
            observations.append(bundle["payload"]["decision"])
        finally:
            reader.close()

    eng = AutoPromotionEngine(cp, emit_receipt_bundle=assert_committed)

    outcome = eng.rollback("rollback_after_commit_solver", "shadow drift")

    assert outcome.decision == "rolled_back"
    assert observations == ["rolled_back"]
    assert eng._last_emitted_receipt is not None


def test_auto_promotion_artifact_failure_blocks_commit(
    cp: ControlPlaneDB,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cp.upsert_family_policy("scalar_unit_conversion", is_low_risk=True)

    def boom(**_kwargs):
        raise ValueError("artifact boom")

    monkeypatch.setattr(
        "waggledance.core.autonomy_growth.auto_promotion_engine."
        "build_promotion_decision_artifact",
        boom,
    )
    eng = AutoPromotionEngine(cp)

    with pytest.raises(ControlPlaneError, match="artifact build failed"):
        eng.evaluate_candidate(PromotionRequest(
            spec=_scalar_unit_conversion_spec("artifact_fail"),
            validation_cases=_validation_cases_for_celsius_to_kelvin(),
            shadow_samples=_shadow_samples_simple(),
            oracle=_scalar_unit_conversion_oracle,
            oracle_kind="formula_recompute",
        ))

    assert cp.get_solver("artifact_fail") is None
    stats = cp.stats()
    assert stats.table_counts["solvers"] == 0
    assert stats.table_counts["promotion_decisions"] == 0


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
    assert rb.promotion_decision_artifact is not None
    assert rb.promotion_decision_digest == sha256_digest(
        rb.promotion_decision_artifact
    )
    assert cp.get_solver("celsius_to_kelvin_v1").status == "deactivated"
    decisions = cp.list_promotion_decisions(
        solver_id=rb.solver_id, decision="rollback"
    )
    assert len(decisions) == 1
    assert decisions[0].rollback_reason == "shadow_drift_in_field"
    evidence = json.loads(decisions[0].evidence or "{}")
    assert evidence["promotion_decision_digest"] == rb.promotion_decision_digest

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


class TestI11AdversarialGateInline:
    """T5b: fail-closed adversarial-corpus gate (I11), ON by default.

    With no report supplied the engine runs the corpus eval inline (bound to
    the committed artifact), so a clean candidate still promotes; a supplied
    report that is mis-bound / below-floor / forged refuses.
    """

    def _request(self, name="adv_inline_solver", **kw):
        return PromotionRequest(
            spec=_scalar_unit_conversion_spec(name),
            validation_cases=_validation_cases_for_celsius_to_kelvin(),
            shadow_samples=_shadow_samples_simple(),
            oracle=_scalar_unit_conversion_oracle,
            oracle_kind="formula_recompute",
            **kw,
        )

    def test_gate_on_default_promotes_via_inline_eval(self, cp: ControlPlaneDB):
        # require_adversarial_gate defaults True, no report -> inline eval runs.
        cp.upsert_family_policy("scalar_unit_conversion", is_low_risk=True)
        out = AutoPromotionEngine(cp).evaluate_candidate(self._request())
        assert out.decision == "auto_promoted"
        assert out.invariant_failed is None

    def test_supplied_report_bound_to_wrong_solver_refuses(self, cp: ControlPlaneDB):
        cp.upsert_family_policy("scalar_unit_conversion", is_low_risk=True)
        spec = _scalar_unit_conversion_spec("adv_inline_solver")
        expected_aid = compile_spec(spec).artifact_id
        report = {
            "bound_solver_hash": "0" * 64,
            "case_count": 42,
            "pass_count": 42,
            "fail_count": 0,
            "cases": _valid_adversarial_cases(42),
            "ok": True,
        }
        _assert_adversarial_gate_reason(
            report=report,
            expected_solver_hash=expected_aid,
            reason_fragment="bound_solver_hash does not match",
        )
        out = AutoPromotionEngine(cp).evaluate_candidate(
            self._request(adversarial_eval_report=report)
        )
        assert out.invariant_failed == "I11_adversarial_corpus_gate"

    def test_supplied_below_floor_report_refuses(self, cp: ControlPlaneDB):
        cp.upsert_family_policy("scalar_unit_conversion", is_low_risk=True)
        eng = AutoPromotionEngine(cp)
        spec = _scalar_unit_conversion_spec("adv_floor_solver")
        aid = compile_spec(spec).artifact_id
        report = {
            "bound_solver_hash": aid,
            "case_count": 5,
            "pass_count": 5,
            "fail_count": 0,
            "cases": _valid_adversarial_cases(5),
            "ok": True,
        }
        _assert_adversarial_gate_reason(
            report=report,
            expected_solver_hash=aid,
            reason_fragment="corpus below floor",
        )
        out = eng.evaluate_candidate(PromotionRequest(
            spec=spec,
            validation_cases=_validation_cases_for_celsius_to_kelvin(),
            shadow_samples=_shadow_samples_simple(),
            oracle=_scalar_unit_conversion_oracle,
            oracle_kind="formula_recompute",
            adversarial_eval_report=report,
        ))
        assert out.invariant_failed == "I11_adversarial_corpus_gate"

    def test_supplied_forged_top_ok_with_uncaught_case_refuses(self, cp: ControlPlaneDB):
        # report['ok']=True but a case is not caught -> re-derive refuses.
        cp.upsert_family_policy("scalar_unit_conversion", is_low_risk=True)
        eng = AutoPromotionEngine(cp)
        spec = _scalar_unit_conversion_spec("adv_forge_solver")
        aid = compile_spec(spec).artifact_id
        cases = _valid_adversarial_cases(42)
        cases[-1]["ok"] = False
        report = {
            "bound_solver_hash": aid,
            "case_count": 42,
            "pass_count": 42,
            "fail_count": 0,
            "cases": cases,
            "ok": True,
        }
        _assert_adversarial_gate_reason(
            report=report,
            expected_solver_hash=aid,
            reason_fragment="adversarial case(s) NOT caught",
        )
        out = eng.evaluate_candidate(PromotionRequest(
            spec=spec,
            validation_cases=_validation_cases_for_celsius_to_kelvin(),
            shadow_samples=_shadow_samples_simple(),
            oracle=_scalar_unit_conversion_oracle,
            oracle_kind="formula_recompute",
            adversarial_eval_report=report,
        ))
        assert out.invariant_failed == "I11_adversarial_corpus_gate"
