# SPDX-License-Identifier: Apache-2.0
"""Direct unit tests for waggledance.core.autonomy_growth.shadow_evaluator.

Iteration N+3 Codex-scout Candidate 1. shadow_evaluator runs the
candidate's compiled artifact against a batch of inputs and compares
the result to an *oracle* — an independent reference. Agreement above
the policy minimum (default 100%) gives independent evidence the
artifact behaves correctly. The promotion engine refuses to use the
fallback byte_identity oracle on its own — that is a non-determinism
canary, not a substitute for an independent reference.

Pinned invariants:

- compile_error: when compile_spec raises, ShadowOutcome reports
  sample_count=len(samples), agree_count=0,
  disagree_count=len(samples), and a single disagreement entry
  with kind="compile_error" carrying the repr of the exception.
  The actual samples are never executed.
- executor_error: when execute_artifact raises ExecutorError on a
  sample, ShadowOutcome records a disagreement entry with
  kind="executor_error", sample_index, and the str of the exception.
- oracle_error: when the oracle callable raises, ShadowOutcome
  records a disagreement with kind="oracle_error", sample_index,
  and the repr of the exception.
- mismatch: when execute and oracle both produce values but the
  values differ outside tolerance, ShadowOutcome records
  kind="mismatch" with actual + expected.
- agreement: when execute and oracle agree (within tolerance for
  numeric, equality otherwise), agree_count increments.
- agreement_rate: 0.0 on sample_count=0; otherwise agree/sample_count.
- oracle_kind passes through verbatim to ShadowOutcome.
- byte_identity_oracle just calls execute_artifact and returns
  whatever it returns.
"""
from __future__ import annotations

import pytest

from waggledance.core.autonomy_growth import shadow_evaluator
from waggledance.core.autonomy_growth.shadow_evaluator import (
    ShadowOutcome,
    byte_identity_oracle,
    run_shadow_evaluation,
)
from waggledance.core.autonomy_growth.solver_executor import ExecutorError
from waggledance.core.solver_synthesis.declarative_solver_spec import (
    SolverSpec,
)


# --- helpers -------------------------------------------------------

def _scalar_unit_conversion_spec(
    name: str = "celsius_to_kelvin_v1",
) -> SolverSpec:
    """Produce a real SolverSpec for the scalar_unit_conversion
    family. Same shape as tests/autonomy_growth/test_auto_promotion_engine.py
    so we exercise the real compile_spec/execute_artifact path."""
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


def _correct_oracle(inputs, artifact):
    return float(inputs["x"]) * float(artifact["factor"]) + float(
        artifact.get("offset", 0.0)
    )


# --- ShadowOutcome.agreement_rate ---------------------------------

def test_agreement_rate_zero_when_sample_count_is_zero():
    outcome = ShadowOutcome(
        sample_count=0, agree_count=0, disagree_count=0,
        oracle_kind="external_reference",
    )
    assert outcome.agreement_rate == 0.0


def test_agreement_rate_one_when_all_agree():
    outcome = ShadowOutcome(
        sample_count=10, agree_count=10, disagree_count=0,
        oracle_kind="external_reference",
    )
    assert outcome.agreement_rate == 1.0


def test_agreement_rate_fractional_for_partial_agreement():
    outcome = ShadowOutcome(
        sample_count=4, agree_count=3, disagree_count=1,
        oracle_kind="external_reference",
    )
    assert outcome.agreement_rate == 0.75


# --- happy path: agreement on every sample ------------------------

def test_run_shadow_evaluation_all_agree_with_correct_oracle():
    spec = _scalar_unit_conversion_spec()
    samples = [{"x": 0}, {"x": 100}, {"x": -40}]
    outcome = run_shadow_evaluation(
        spec, samples, _correct_oracle,
    )
    assert outcome.sample_count == 3
    assert outcome.agree_count == 3
    assert outcome.disagree_count == 0
    assert outcome.disagreements == []
    assert outcome.agreement_rate == 1.0


def test_run_shadow_evaluation_oracle_kind_passes_through():
    spec = _scalar_unit_conversion_spec()
    outcome = run_shadow_evaluation(
        spec, [{"x": 0}], _correct_oracle,
        oracle_kind="byte_identity",
    )
    assert outcome.oracle_kind == "byte_identity"


def test_run_shadow_evaluation_default_oracle_kind_is_external_reference():
    spec = _scalar_unit_conversion_spec()
    outcome = run_shadow_evaluation(spec, [{"x": 0}], _correct_oracle)
    assert outcome.oracle_kind == "external_reference"


def test_run_shadow_evaluation_empty_sample_set():
    """Empty samples list: sample_count=0, agree=0, disagree=0,
    agreement_rate=0.0 by definition (not 1.0!)."""
    spec = _scalar_unit_conversion_spec()
    outcome = run_shadow_evaluation(spec, [], _correct_oracle)
    assert outcome.sample_count == 0
    assert outcome.agree_count == 0
    assert outcome.disagree_count == 0
    assert outcome.agreement_rate == 0.0


# --- compile_error -------------------------------------------------

def test_compile_error_short_circuits_with_single_disagreement(monkeypatch):
    """When compile_spec raises, the loop must produce a single
    compile_error disagreement and never execute any sample.
    Patch compile_spec to simulate the error path — SolverSpec's
    __post_init__ already enforces family_kind, so we cannot build
    an invalid spec at construction time."""
    spec = _scalar_unit_conversion_spec()

    def boom_compile_spec(_spec):
        raise RuntimeError("compile pipeline blew up")

    monkeypatch.setattr(
        shadow_evaluator, "compile_spec", boom_compile_spec,
    )

    executed = []
    def trace_execute(artifact, sample):
        executed.append(sample)
        return 0
    monkeypatch.setattr(
        shadow_evaluator, "execute_artifact", trace_execute,
    )

    samples = [{"x": 1}, {"x": 2}, {"x": 3}]
    outcome = run_shadow_evaluation(spec, samples, _correct_oracle)
    assert outcome.sample_count == 3
    assert outcome.agree_count == 0
    assert outcome.disagree_count == 3
    assert len(outcome.disagreements) == 1
    entry = outcome.disagreements[0]
    assert entry["kind"] == "compile_error"
    assert "compile pipeline blew up" in entry["error"]
    # Critical invariant: when compile fails, NO sample is executed.
    assert executed == []


# --- mismatch ------------------------------------------------------

def test_mismatch_disagreement_records_actual_and_expected():
    spec = _scalar_unit_conversion_spec()
    # Oracle returns deliberately wrong value for x=0:
    def wrong_oracle(inputs, artifact):
        return -999.0  # never equals the correct kelvin value
    outcome = run_shadow_evaluation(spec, [{"x": 0}], wrong_oracle)
    assert outcome.agree_count == 0
    assert outcome.disagree_count == 1
    assert len(outcome.disagreements) == 1
    entry = outcome.disagreements[0]
    assert entry["kind"] == "mismatch"
    assert entry["sample_index"] == 0
    assert entry["expected"] == -999.0
    assert entry["actual"] == pytest.approx(273.15)


def test_mismatch_within_tolerance_counts_as_agreement():
    """Numeric tolerance: a tiny difference under tolerance must
    count as agreement — otherwise floating-point noise would fail
    the gate."""
    spec = _scalar_unit_conversion_spec()
    def near_oracle(inputs, artifact):
        # off by 1e-12, well under default tolerance 1e-9
        return _correct_oracle(inputs, artifact) + 1e-12
    outcome = run_shadow_evaluation(spec, [{"x": 0}], near_oracle)
    assert outcome.agree_count == 1


def test_mismatch_outside_tolerance_counts_as_disagreement():
    spec = _scalar_unit_conversion_spec()
    def slightly_off_oracle(inputs, artifact):
        return _correct_oracle(inputs, artifact) + 1.0  # well over 1e-9
    outcome = run_shadow_evaluation(spec, [{"x": 0}], slightly_off_oracle)
    assert outcome.disagree_count == 1
    assert outcome.disagreements[0]["kind"] == "mismatch"


def test_custom_tolerance_widens_agreement_band():
    spec = _scalar_unit_conversion_spec()
    def off_by_half_oracle(inputs, artifact):
        return _correct_oracle(inputs, artifact) + 0.5
    # Default tolerance 1e-9 would fail; explicit 1.0 tolerance passes.
    strict = run_shadow_evaluation(spec, [{"x": 0}], off_by_half_oracle)
    loose = run_shadow_evaluation(
        spec, [{"x": 0}], off_by_half_oracle, tolerance=1.0,
    )
    assert strict.disagree_count == 1
    assert loose.agree_count == 1


# --- oracle_error --------------------------------------------------

def test_oracle_error_records_disagreement_per_sample():
    spec = _scalar_unit_conversion_spec()
    def crashing_oracle(inputs, artifact):
        raise ValueError("oracle blew up")
    samples = [{"x": 0}, {"x": 1}]
    outcome = run_shadow_evaluation(spec, samples, crashing_oracle)
    assert outcome.agree_count == 0
    assert outcome.disagree_count == 2
    assert len(outcome.disagreements) == 2
    for i, entry in enumerate(outcome.disagreements):
        assert entry["kind"] == "oracle_error"
        assert entry["sample_index"] == i
        assert "ValueError" in entry["error"]
        assert "oracle blew up" in entry["error"]


# --- executor_error (via monkeypatch) -----------------------------

def test_executor_error_records_disagreement_with_str_message(monkeypatch):
    """When execute_artifact raises ExecutorError, the loop must
    record kind='executor_error' with the str of the exception
    (not repr like compile/oracle)."""
    spec = _scalar_unit_conversion_spec()

    call_count = {"n": 0}

    def fake_execute_artifact(artifact, sample):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise ExecutorError("simulated executor failure")
        # 2nd sample — succeed normally:
        return _correct_oracle(sample, artifact)

    monkeypatch.setattr(
        shadow_evaluator, "execute_artifact", fake_execute_artifact,
    )

    samples = [{"x": 0}, {"x": 100}]
    outcome = run_shadow_evaluation(spec, samples, _correct_oracle)
    assert outcome.sample_count == 2
    # First sample: executor_error; second: agreement.
    assert outcome.agree_count == 1
    assert outcome.disagree_count == 1
    assert len(outcome.disagreements) == 1
    entry = outcome.disagreements[0]
    assert entry["kind"] == "executor_error"
    assert entry["sample_index"] == 0
    assert "simulated executor failure" in entry["error"]


# --- non-numeric equality (no tolerance applies) ------------------

def test_non_numeric_equality_uses_strict_eq():
    """For non-numeric outputs, _equal falls back to == (no
    tolerance applies). String/dict/list equality is exact."""
    spec = _scalar_unit_conversion_spec()
    # Oracle returns a string instead of a number — _equal should
    # use == and either match or not. Here actual is float, expected
    # is string, so they will not match (mismatch).
    def string_oracle(inputs, artifact):
        return "not_a_number"
    outcome = run_shadow_evaluation(spec, [{"x": 0}], string_oracle)
    assert outcome.disagree_count == 1
    assert outcome.disagreements[0]["kind"] == "mismatch"


# --- byte_identity_oracle -----------------------------------------

def test_byte_identity_oracle_is_pure_re_execute(monkeypatch):
    """byte_identity_oracle returns whatever execute_artifact
    returns. It does NOT compare or judge — it is just a re-run hook
    so the loop can detect non-determinism via mismatches."""
    captured = []

    def fake_execute_artifact(artifact, inputs):
        captured.append(("call", inputs.get("x")))
        return 42.0

    monkeypatch.setattr(
        shadow_evaluator, "execute_artifact", fake_execute_artifact,
    )
    result = byte_identity_oracle({"x": 7}, {"factor": 1.0})
    assert result == 42.0
    assert captured == [("call", 7)]


def test_byte_identity_oracle_works_end_to_end_with_real_compile():
    """Smoke: with the real compile path, byte_identity_oracle on
    a deterministic artifact produces 100% agreement."""
    spec = _scalar_unit_conversion_spec()
    samples = [{"x": 1}, {"x": 2}, {"x": 3}]
    outcome = run_shadow_evaluation(
        spec, samples, byte_identity_oracle,
        oracle_kind="byte_identity",
    )
    assert outcome.agree_count == 3
    assert outcome.oracle_kind == "byte_identity"
