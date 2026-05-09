# SPDX-License-Identifier: Apache-2.0
"""Direct unit tests for waggledance.core.solver_synthesis.solver_quarantine.

Phase 9 §U3 quota enforcement and admission policy. Constitutional
guard `constitution.no_foundational_auto_promotion`: final approval
MUST require an explicit `human_approval_id`. The quarantine module
is the safety boundary between LLM-generated candidates and runtime
promotion.

A regression here can grant runtime approval without human approval,
exhaust quotas without backpressure, or accept candidates from the
wrong intermediate state. Direct test coverage on this file was
zero before this PR.

Pinned invariants:

- `QuotaState`: remaining/can_consume/consume math; consume returns
  (state, ok) and is a no-op when quota exhausted; reset_daily clears
  consumed_today.
- `admit_to_shadow`: requires source state == "test_valid"; consumes
  `max_new_shadow_solvers_per_day`; rejects with rationale otherwise.
- `admit_to_review_ready`: requires source state == "cold_solver";
  consumes `max_review_ready_solvers_per_day`.
- `admit_to_approved`: REQUIRES explicit `human_approval_id`
  (constitutional guard); requires source state == "review_ready";
  consumes `max_final_approvals_per_day`; rationale records the
  approver id.
"""
from __future__ import annotations

import pytest

from waggledance.core.solver_synthesis.solver_candidate_store import (
    DEFAULT_QUOTAS,
    SolverCandidate,
)
from waggledance.core.solver_synthesis.solver_quarantine import (
    AdmissionDecision,
    QuotaState,
    admit_to_approved,
    admit_to_review_ready,
    admit_to_shadow,
)


# --- helpers --------------------------------------------------------

def _candidate(*, state: str, candidate_id: str = "C-1") -> SolverCandidate:
    return SolverCandidate(
        schema_version=1,
        candidate_id=candidate_id,
        state=state,
        solver_name="test_solver",
        cell_id="general",
        spec_or_code={"kind": "scalar_unit_conversion"},
        source_gap_ref="gap_x",
        no_runtime_mutation=True,
        produced_by="test",
        branch_name="test/branch",
        base_commit_hash="deadbeef",
        pinned_input_manifest_sha256="f" * 64,
    )


# --- QuotaState math -----------------------------------------------

def test_quota_state_default_quotas_match_module_constant():
    state = QuotaState()
    assert state.quotas == DEFAULT_QUOTAS
    # remaining == quota when consumed_today is empty.
    for k, v in DEFAULT_QUOTAS.items():
        assert state.remaining(k) == v


def test_quota_state_consume_decrements_remaining():
    state = QuotaState(quotas={"x": 5})
    state, ok = state.consume("x", 2)
    assert ok is True
    assert state.remaining("x") == 3


def test_quota_state_consume_blocked_when_quota_exhausted():
    """consume MUST return (state, False) and NOT decrement when quota
    cannot accommodate the requested amount."""
    state = QuotaState(quotas={"x": 1})
    state, ok = state.consume("x", 1)
    assert ok is True
    state, ok = state.consume("x", 1)
    assert ok is False
    # remaining still 0 — consume did not over-deduct.
    assert state.remaining("x") == 0


def test_quota_state_consume_unknown_quota_key_returns_false():
    state = QuotaState(quotas={"x": 5})
    state, ok = state.consume("not_a_quota", 1)
    assert ok is False


def test_quota_state_reset_daily_clears_consumed_only():
    state = QuotaState(quotas={"x": 10}, consumed_today={"x": 7})
    state.reset_daily()
    assert state.consumed_today == {}
    assert state.quotas == {"x": 10}  # quotas untouched


def test_quota_state_to_dict_contains_remaining_per_quota():
    state = QuotaState(quotas={"a": 10, "b": 5}, consumed_today={"a": 3})
    d = state.to_dict()
    assert d["remaining"] == {"a": 7, "b": 5}
    assert d["quotas"] == {"a": 10, "b": 5}
    assert d["consumed_today"] == {"a": 3}


# --- admit_to_shadow ------------------------------------------------

def test_admit_to_shadow_grants_when_state_test_valid_and_quota_available():
    state = QuotaState(quotas={"max_new_shadow_solvers_per_day": 1})
    cand = _candidate(state="test_valid")
    state, decision = admit_to_shadow(state, cand)
    assert decision.admitted is True
    assert decision.target_state == "shadow_only"
    assert state.remaining("max_new_shadow_solvers_per_day") == 0


def test_admit_to_shadow_rejects_when_state_not_test_valid():
    state = QuotaState(quotas={"max_new_shadow_solvers_per_day": 5})
    cand = _candidate(state="raw_candidate")
    state, decision = admit_to_shadow(state, cand)
    assert decision.admitted is False
    assert "test_valid" in decision.rationale
    # Quota MUST NOT decrement on rejection.
    assert state.remaining("max_new_shadow_solvers_per_day") == 5


def test_admit_to_shadow_rejects_when_quota_exhausted():
    state = QuotaState(quotas={"max_new_shadow_solvers_per_day": 0})
    cand = _candidate(state="test_valid")
    state, decision = admit_to_shadow(state, cand)
    assert decision.admitted is False
    assert "quota exhausted" in decision.rationale.lower()


# --- admit_to_review_ready ------------------------------------------

def test_admit_to_review_ready_grants_when_state_cold_solver_and_quota_available():
    state = QuotaState(quotas={"max_review_ready_solvers_per_day": 1})
    cand = _candidate(state="cold_solver")
    state, decision = admit_to_review_ready(state, cand)
    assert decision.admitted is True
    assert decision.target_state == "review_ready"


def test_admit_to_review_ready_rejects_when_state_wrong():
    state = QuotaState(quotas={"max_review_ready_solvers_per_day": 5})
    cand = _candidate(state="shadow_only")
    state, decision = admit_to_review_ready(state, cand)
    assert decision.admitted is False
    assert "cold_solver" in decision.rationale


def test_admit_to_review_ready_rejects_when_quota_exhausted():
    state = QuotaState(quotas={"max_review_ready_solvers_per_day": 0})
    cand = _candidate(state="cold_solver")
    state, decision = admit_to_review_ready(state, cand)
    assert decision.admitted is False
    assert "quota exhausted" in decision.rationale.lower()


# --- admit_to_approved (constitutional guard) -----------------------

def test_admit_to_approved_REQUIRES_human_approval_id_constitutional_guard():
    """The constitutional guard:
    `constitution.no_foundational_auto_promotion`. Without an explicit
    `human_approval_id`, final approval MUST be refused regardless of
    candidate state or quota."""
    state = QuotaState(quotas={"max_final_approvals_per_day": 100})
    cand = _candidate(state="review_ready")
    # No human_approval_id → reject.
    state, decision = admit_to_approved(state, cand, human_approval_id=None)
    assert decision.admitted is False
    assert "human_approval_id" in decision.rationale
    assert "no_foundational_auto_promotion" in decision.rationale
    # Quota MUST NOT decrement when constitutional guard blocks.
    assert state.remaining("max_final_approvals_per_day") == 100


def test_admit_to_approved_rejects_empty_string_human_approval_id():
    state = QuotaState(quotas={"max_final_approvals_per_day": 5})
    cand = _candidate(state="review_ready")
    state, decision = admit_to_approved(state, cand, human_approval_id="")
    assert decision.admitted is False  # empty string is falsy
    assert state.remaining("max_final_approvals_per_day") == 5


def test_admit_to_approved_grants_with_explicit_approver_id():
    state = QuotaState(quotas={"max_final_approvals_per_day": 1})
    cand = _candidate(state="review_ready")
    state, decision = admit_to_approved(
        state, cand, human_approval_id="operator_jani_2026_05_09",
    )
    assert decision.admitted is True
    assert decision.target_state == "approved"
    # Rationale must record the approver id for the audit trail.
    assert "operator_jani_2026_05_09" in decision.rationale
    assert state.remaining("max_final_approvals_per_day") == 0


def test_admit_to_approved_rejects_when_state_not_review_ready():
    state = QuotaState(quotas={"max_final_approvals_per_day": 5})
    cand = _candidate(state="shadow_only")
    state, decision = admit_to_approved(
        state, cand, human_approval_id="op_x",
    )
    assert decision.admitted is False
    assert "review_ready" in decision.rationale
    assert state.remaining("max_final_approvals_per_day") == 5


def test_admit_to_approved_rejects_when_quota_exhausted_even_with_approval_id():
    state = QuotaState(quotas={"max_final_approvals_per_day": 0})
    cand = _candidate(state="review_ready")
    state, decision = admit_to_approved(
        state, cand, human_approval_id="op_x",
    )
    assert decision.admitted is False
    assert "quota exhausted" in decision.rationale.lower()


# --- AdmissionDecision serialization --------------------------------

def test_admission_decision_to_dict_round_trips_fields():
    d = AdmissionDecision(
        candidate_id="C-1", admitted=True, target_state="shadow_only",
        rationale="ok",
    ).to_dict()
    assert d == {
        "candidate_id": "C-1", "admitted": True,
        "target_state": "shadow_only", "rationale": "ok",
    }
