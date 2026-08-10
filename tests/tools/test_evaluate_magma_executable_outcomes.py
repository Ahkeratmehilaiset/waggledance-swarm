from __future__ import annotations

from dataclasses import replace

import pytest

from core.symbolic_solver import SymbolicSolver
from tools import evaluate_magma_executable_outcomes as gate


SNAPSHOT_ID = "faisscand_" + "a" * 64
SESSION_ID = "faisssession_" + "b" * 32


def _candidate(
    solver_id: str,
    score: float,
    *,
    snapshot_id: str = SNAPSHOT_ID,
    session_id: str = SESSION_ID,
) -> dict:
    return {
        "canonical_solver_id": solver_id,
        "cell_id": "thermal",
        "projection_id": "sha256:" + "c" * 64,
        "snapshot_id": snapshot_id,
        "verification_session_id": session_id,
        "source_commit_reverified": True,
        "source_reverification_scope": "session_open",
        "source_reverified_during_search_call": False,
        "receipt_bound": True,
        "receipt_structure_reverified": True,
        "receipt_authenticity_verified": False,
        "solver_outcome_verified": False,
        "runtime_authority_granted": False,
        "score": score,
    }


def _frozen_retriever(query: str) -> list[dict]:
    case_by_query = {case.query: case.case_id for case in gate.FROZEN_CASES}
    case_id = case_by_query[query]
    if case_id == "heating_cost_96m2_minus5c_12ckwh":
        return [_candidate("heating_cost", 0.604826927)]
    if case_id == "translate_monthly_heating_bill_spanish":
        return [_candidate("heating_cost", 0.742204)]
    return [_candidate("colony_food_reserves", 0.666082)]


def test_frozen_suite_identity_is_stable() -> None:
    assert len(gate.FROZEN_CASES) == 4
    assert gate.FROZEN_SUITE_DIGEST == (
        "sha256:b1874b9a69642808db394c9c5ce5c7f967e8c1894fa3f0514faaa91c5437afcc"
    )


def test_frozen_gate_executes_exact_positive_and_abstains_on_ood() -> None:
    report = gate.run_frozen_outcome_gate(_frozen_retriever)

    assert report["frozen_smoke_gate_pass"] is True
    assert report["positive_case_count"] == 1
    assert report["ood_negative_case_count"] == 3
    assert report["executor_call_count"] == 1
    assert report["negative_zero_executor_calls"] is True
    assert report["route_and_executable_outcome_observed"] is True
    assert report["production_promotion_gate_evaluated"] is False
    assert report["production_promotion_gate_pass"] is False
    assert report["promotion_applied"] is False
    assert report["product_readiness_evidence"] is False
    assert report["runtime_authority_granted"] is False
    assert report["receipt_authenticity_verified"] is False
    assert report["cell_local_pruning_evaluated"] is False
    positive, translation, joke, routing_ood = report["cases"]
    assert positive["admission"]["admitted"] is True
    assert positive["outcome"]["value"] == pytest.approx(71.8848, abs=1.0e-9)
    assert positive["outcome"]["inputs_used"] == {
        "T_outdoor": -5.0,
        "area_m2": 96.0,
        "spot_price_ckwh": 12.0,
    }
    assert [row["value"] for row in positive["outcome"]["derivation"]] == (
        pytest.approx([832.0, 19.968, 2.39616, 71.8848], abs=1.0e-9)
    )
    assert translation["admission"]["reason"] == "non_executable_speech_act"
    assert translation["executor_call_count"] == 0
    assert joke["admission"]["reason"] == "non_executable_speech_act"
    assert joke["executor_call_count"] == 0
    assert routing_ood["admission"]["reason"] == (
        "solver_not_in_frozen_executable_allowlist"
    )
    assert routing_ood["executor_call_count"] == 0


def test_gate_rejects_executor_injection() -> None:
    with pytest.raises(TypeError):
        gate.run_frozen_outcome_gate(  # type: ignore[call-arg]
            _frozen_retriever,
            lambda _solver_id, _query: None,
        )


def test_admission_is_label_blind_and_rejects_translation_with_all_inputs() -> None:
    positive = gate.evaluate_admission(
        gate.FROZEN_CASES[0].query,
        [{"canonical_solver_id": "heating_cost", "score": 0.61}],
    )
    adversarial_translation = gate.evaluate_admission(
        "Translate a heating estimate for 96 m² at -5°C and 12 c/kWh into Spanish.",
        [{"canonical_solver_id": "heating_cost", "score": 0.99}],
    )

    assert positive["admitted"] is True
    assert positive["reason"] == "explicit_heating_contract_satisfied"
    assert adversarial_translation["explicit_inputs"] == {
        "T_outdoor": -5.0,
        "area_m2": 96.0,
        "spot_price_ckwh": 12.0,
    }
    assert adversarial_translation["admitted"] is False
    assert adversarial_translation["reason"] == "non_executable_speech_act"


@pytest.mark.parametrize(
    ("query", "solver_id", "score", "reason"),
    [
        (
            "What is the monthly heating spend?",
            "heating_cost",
            0.9,
            "missing_required_explicit_inputs",
        ),
        (
            gate.FROZEN_CASES[0].query,
            "heating_cost",
            0.2,
            "candidate_below_minimum_score",
        ),
        (
            gate.FROZEN_CASES[0].query,
            "colony_food_reserves",
            0.9,
            "solver_not_in_frozen_executable_allowlist",
        ),
    ],
)
def test_admission_fails_closed(
    query: str, solver_id: str, score: float, reason: str
) -> None:
    decision = gate.evaluate_admission(
        query, [{"canonical_solver_id": solver_id, "score": score}]
    )
    assert decision["admitted"] is False
    assert decision["reason"] == reason


def test_wrong_solver_outcome_fails_all_or_nothing_gate(monkeypatch) -> None:
    real_solver = SymbolicSolver()

    class WrongSolver:
        def solve_for_chat(self, solver_id: str, query: str):
            result = real_solver.solve_for_chat(solver_id, query)
            return replace(result, value=result.value + 1.0)

    monkeypatch.setattr(gate, "SymbolicSolver", WrongSolver)
    report = gate.run_frozen_outcome_gate(_frozen_retriever)

    assert report["frozen_smoke_gate_pass"] is False
    assert report["executor_call_count"] == 1
    assert report["cases"][0]["case_pass"] is False
    assert all(row["case_pass"] is True for row in report["cases"][1:])
    assert report["runtime_authority_granted"] is False


def test_executor_exception_is_evidence_not_a_silent_fallback(monkeypatch) -> None:
    class ExplodingSolver:
        def solve_for_chat(self, _solver_id: str, _query: str):
            raise RuntimeError("simulated deterministic solver failure")

    monkeypatch.setattr(gate, "SymbolicSolver", ExplodingSolver)
    report = gate.run_frozen_outcome_gate(_frozen_retriever)

    assert report["frozen_smoke_gate_pass"] is False
    assert report["executor_call_count"] == 1
    assert report["cases"][0]["execution_error_type"] == "RuntimeError"
    assert report["cases"][0]["outcome"] is None
    assert report["cases"][0]["case_pass"] is False


def test_empty_validation_evidence_fails_closed(monkeypatch) -> None:
    real_solver = SymbolicSolver()

    class EmptyValidationSolver:
        def solve_for_chat(self, solver_id: str, query: str):
            result = real_solver.solve_for_chat(solver_id, query)
            return replace(result, validation=[])

    monkeypatch.setattr(gate, "SymbolicSolver", EmptyValidationSolver)
    report = gate.run_frozen_outcome_gate(_frozen_retriever)

    assert report["frozen_smoke_gate_pass"] is False
    assert report["cases"][0]["execution_error_type"] == (
        "OutcomeGateContractError"
    )
    assert report["cases"][0]["case_pass"] is False


def test_candidate_authority_escalation_is_rejected() -> None:
    def forged_retriever(_query: str) -> list[dict]:
        row = _candidate("heating_cost", 0.9)
        row["runtime_authority_granted"] = True
        return [row]

    with pytest.raises(
        gate.OutcomeGateContractError, match="authority posture"
    ):
        gate.run_frozen_outcome_gate(forged_retriever)


def test_candidate_order_and_session_binding_are_fail_closed() -> None:
    call_count = 0

    def changing_retriever(query: str) -> list[dict]:
        nonlocal call_count
        call_count += 1
        rows = _frozen_retriever(query)
        rows[0]["verification_session_id"] = (
            "faisssession_" + ("d" if call_count == 1 else "e") * 32
        )
        return rows

    with pytest.raises(gate.OutcomeGateContractError, match="different sessions"):
        gate.run_frozen_outcome_gate(changing_retriever)

    def unsorted_retriever(_query: str) -> list[dict]:
        return [
            _candidate("heating_cost", 0.5),
            _candidate("colony_food_reserves", 0.8),
        ]

    with pytest.raises(gate.OutcomeGateContractError, match="ranked"):
        gate.run_frozen_outcome_gate(unsorted_retriever)


def test_no_candidate_never_calls_executor_or_claims_pass() -> None:
    report = gate.run_frozen_outcome_gate(lambda _query: [])

    assert report["executor_call_count"] == 0
    assert report["route_and_executable_outcome_observed"] is False
    assert report["frozen_smoke_gate_pass"] is False
    assert report["negative_zero_executor_calls"] is True
