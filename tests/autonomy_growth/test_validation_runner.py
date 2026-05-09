# SPDX-License-Identifier: Apache-2.0
"""Direct unit tests for waggledance.core.autonomy_growth.validation_runner.

Iteration N+3 Codex-scout Candidate 2. validation_runner is the
deterministic-case gate in the low-risk autogrowth lane: a *validation
case* is a pair (inputs, expected_output); the runner compiles the
spec, executes it once per case, and compares. A drifted tolerance,
a bypassed compile failure, or a silently-coerced malformed case
would all let an unqualified solver advance through the validation
gate.

Codex was offline (liveness/sleeping at 08:17Z) when this scout
candidate was being claimed — Claude picked it up under
"work-without-operator-intervention" autonomy. Test shape mirrors
tests/autonomy_growth/test_shadow_evaluator.py so a future refactor
that touches both modules has matching contracts to satisfy.

Pinned invariants:

- compile_error: when compile_spec raises, ValidationOutcome reports
  case_count=len(cases), pass_count=0, fail_count=len(cases), and a
  single failure entry kind="compile_error" with case_index=-1
  carrying repr of the exception. NO case is executed.
- malformed_case: a case dict missing either "inputs" or "expected"
  is flagged kind="malformed_case" with the case_index. Subsequent
  well-formed cases still process.
- executor_error: when execute_artifact raises ExecutorError on a
  case, kind="executor_error" with sample's case_index and the str
  of the exception (NOT repr — matches the source's str(exc)).
- mismatch: actual+expected differ outside tolerance — both values
  recorded under kind="mismatch".
- numeric tolerance: default 1e-9; near-equal floats count as pass.
  Custom tolerance widens.
- non-numeric equality: falls back to == (no tolerance applies).
- pass_rate: 0.0 on case_count=0 (NOT 1.0); otherwise pass/case.
- all_passed: True ONLY when case_count > 0 AND fail_count == 0.
  An empty case set is NOT a pass — promotion gates depend on this
  explicit "no evidence != approval" semantic.
"""
from __future__ import annotations

import pytest

from waggledance.core.autonomy_growth import validation_runner
from waggledance.core.autonomy_growth.solver_executor import ExecutorError
from waggledance.core.autonomy_growth.validation_runner import (
    ValidationOutcome,
    run_validation,
)
from waggledance.core.solver_synthesis.declarative_solver_spec import (
    SolverSpec,
)


# --- helpers -------------------------------------------------------

def _scalar_unit_conversion_spec(
    name: str = "celsius_to_kelvin_v1",
) -> SolverSpec:
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


def _case(x: float, expected: float) -> dict:
    return {"inputs": {"x": x}, "expected": expected}


# --- ValidationOutcome.pass_rate ----------------------------------

def test_pass_rate_zero_when_case_count_is_zero():
    outcome = ValidationOutcome(
        case_count=0, pass_count=0, fail_count=0,
    )
    assert outcome.pass_rate == 0.0


def test_pass_rate_one_when_all_pass():
    outcome = ValidationOutcome(
        case_count=10, pass_count=10, fail_count=0,
    )
    assert outcome.pass_rate == 1.0


def test_pass_rate_fractional_for_partial():
    outcome = ValidationOutcome(
        case_count=4, pass_count=3, fail_count=1,
    )
    assert outcome.pass_rate == 0.75


# --- ValidationOutcome.all_passed --------------------------------

def test_all_passed_false_when_case_count_zero():
    """Empty case set must NEVER be 'all passed' — a candidate with
    no validation evidence cannot promote past the gate."""
    outcome = ValidationOutcome(
        case_count=0, pass_count=0, fail_count=0,
    )
    assert outcome.all_passed is False


def test_all_passed_true_when_some_pass_and_zero_fail():
    outcome = ValidationOutcome(
        case_count=5, pass_count=5, fail_count=0,
    )
    assert outcome.all_passed is True


def test_all_passed_false_when_any_failure():
    outcome = ValidationOutcome(
        case_count=10, pass_count=9, fail_count=1,
    )
    assert outcome.all_passed is False


# --- happy path: agreement on every case --------------------------

def test_run_validation_all_pass_with_correct_expectations():
    spec = _scalar_unit_conversion_spec()
    cases = [
        _case(0,    273.15),
        _case(100,  373.15),
        _case(-40,  233.15),
    ]
    outcome = run_validation(spec, cases)
    assert outcome.case_count == 3
    assert outcome.pass_count == 3
    assert outcome.fail_count == 0
    assert outcome.failures == []
    assert outcome.all_passed is True
    assert outcome.pass_rate == 1.0


def test_run_validation_empty_case_set_returns_zero_outcome():
    spec = _scalar_unit_conversion_spec()
    outcome = run_validation(spec, [])
    assert outcome.case_count == 0
    assert outcome.pass_count == 0
    assert outcome.fail_count == 0
    assert outcome.all_passed is False
    assert outcome.pass_rate == 0.0


# --- compile_error -------------------------------------------------

def test_compile_error_short_circuits_with_single_failure(monkeypatch):
    """When compile_spec raises, the loop must produce a single
    compile_error failure with case_index=-1 and never execute any
    case. Patch compile_spec to simulate the error path —
    SolverSpec.__post_init__ enforces family_kind so we cannot
    build an invalid spec at construction time."""
    spec = _scalar_unit_conversion_spec()

    def boom_compile_spec(_spec):
        raise RuntimeError("compile pipeline blew up")

    monkeypatch.setattr(
        validation_runner, "compile_spec", boom_compile_spec,
    )

    executed = []
    def trace_execute(artifact, sample):
        executed.append(sample)
        return 0
    monkeypatch.setattr(
        validation_runner, "execute_artifact", trace_execute,
    )

    cases = [_case(1, 274.15), _case(2, 275.15), _case(3, 276.15)]
    outcome = run_validation(spec, cases)
    assert outcome.case_count == 3
    assert outcome.pass_count == 0
    assert outcome.fail_count == 3
    assert len(outcome.failures) == 1
    entry = outcome.failures[0]
    assert entry["kind"] == "compile_error"
    assert entry["case_index"] == -1
    assert "compile pipeline blew up" in entry["error"]
    # Critical invariant: when compile fails, NO case is executed.
    assert executed == []


# --- malformed_case ----------------------------------------------

def test_malformed_case_missing_inputs_recorded():
    """A case dict without 'inputs' must be flagged
    kind='malformed_case' — never silently pass, never raise."""
    spec = _scalar_unit_conversion_spec()
    cases = [
        {"expected": 1.0},  # missing 'inputs'
        _case(0, 273.15),   # well-formed, should still process
    ]
    outcome = run_validation(spec, cases)
    assert outcome.case_count == 2
    assert outcome.pass_count == 1
    assert outcome.fail_count == 1
    assert len(outcome.failures) == 1
    entry = outcome.failures[0]
    assert entry["kind"] == "malformed_case"
    assert entry["case_index"] == 0
    assert "inputs" in entry["error"] or "expected" in entry["error"]


def test_malformed_case_missing_expected_recorded():
    spec = _scalar_unit_conversion_spec()
    cases = [
        {"inputs": {"x": 0}},  # missing 'expected'
    ]
    outcome = run_validation(spec, cases)
    assert outcome.fail_count == 1
    assert outcome.failures[0]["kind"] == "malformed_case"


def test_malformed_case_does_not_short_circuit(monkeypatch):
    """A malformed case in the middle must NOT stop the loop —
    each case is independent."""
    spec = _scalar_unit_conversion_spec()
    cases = [
        _case(0, 273.15),       # pass
        {"inputs": {"x": 1}},   # missing expected
        _case(2, 275.15),       # pass
    ]
    outcome = run_validation(spec, cases)
    assert outcome.pass_count == 2
    assert outcome.fail_count == 1


# --- mismatch + tolerance ------------------------------------------

def test_mismatch_records_actual_and_expected():
    spec = _scalar_unit_conversion_spec()
    cases = [_case(0, -999.0)]  # wrong expected — actual is 273.15
    outcome = run_validation(spec, cases)
    assert outcome.pass_count == 0
    assert outcome.fail_count == 1
    entry = outcome.failures[0]
    assert entry["kind"] == "mismatch"
    assert entry["case_index"] == 0
    assert entry["expected"] == -999.0
    assert entry["actual"] == pytest.approx(273.15)


def test_mismatch_within_tolerance_counts_as_pass():
    """Floating-point noise tolerance: difference under tolerance
    must count as pass."""
    spec = _scalar_unit_conversion_spec()
    # 273.15 + 1e-12 vs actual 273.15: well under default 1e-9
    cases = [{"inputs": {"x": 0}, "expected": 273.15 + 1e-12}]
    outcome = run_validation(spec, cases)
    assert outcome.pass_count == 1


def test_mismatch_outside_tolerance_counts_as_fail():
    spec = _scalar_unit_conversion_spec()
    cases = [_case(0, 273.15 + 1.0)]  # 1.0 well over 1e-9
    outcome = run_validation(spec, cases)
    assert outcome.fail_count == 1
    assert outcome.failures[0]["kind"] == "mismatch"


def test_custom_tolerance_widens_pass_band():
    spec = _scalar_unit_conversion_spec()
    cases = [_case(0, 273.65)]  # 0.5 off
    strict = run_validation(spec, cases)  # default 1e-9 fails
    loose = run_validation(spec, cases, tolerance=1.0)  # 1.0 passes
    assert strict.fail_count == 1
    assert loose.pass_count == 1


# --- non-numeric equality ----------------------------------------

def test_non_numeric_equality_uses_strict_eq():
    """For non-numeric expected values, equality is strict ==
    (no tolerance applies). String/dict/list equality is exact."""
    spec = _scalar_unit_conversion_spec()
    # actual is float; expected is string; mismatch.
    cases = [{"inputs": {"x": 0}, "expected": "not_a_number"}]
    outcome = run_validation(spec, cases)
    assert outcome.fail_count == 1
    assert outcome.failures[0]["kind"] == "mismatch"


# --- executor_error (via monkeypatch) ----------------------------

def test_executor_error_recorded_with_str_message(monkeypatch):
    """When execute_artifact raises ExecutorError, kind must be
    'executor_error' carrying str(exc) (not repr — that matches the
    source). Subsequent cases still process."""
    spec = _scalar_unit_conversion_spec()

    call_count = {"n": 0}

    def fake_execute(artifact, sample):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise ExecutorError("simulated executor failure")
        # 2nd case — succeed normally
        return float(sample["x"]) + 273.15

    monkeypatch.setattr(
        validation_runner, "execute_artifact", fake_execute,
    )

    cases = [_case(0, 273.15), _case(100, 373.15)]
    outcome = run_validation(spec, cases)
    assert outcome.case_count == 2
    assert outcome.pass_count == 1   # second case OK
    assert outcome.fail_count == 1
    assert len(outcome.failures) == 1
    entry = outcome.failures[0]
    assert entry["kind"] == "executor_error"
    assert entry["case_index"] == 0
    assert "simulated executor failure" in entry["error"]


# --- mixed: malformed + executor_error + mismatch + pass ---------

def test_mixed_failure_kinds_in_one_run(monkeypatch):
    """Combine all three failure kinds and a pass in one run.
    Verifies independence of each case's classification."""
    spec = _scalar_unit_conversion_spec()

    def fake_execute(artifact, sample):
        if sample.get("x") == "raise":
            raise ExecutorError("boom")
        return 273.15  # pretend everything else returns this

    monkeypatch.setattr(
        validation_runner, "execute_artifact", fake_execute,
    )

    cases = [
        _case(0,         273.15),       # 0: pass
        {"inputs": {"x": 0}},           # 1: malformed (no expected)
        {"inputs": {"x": "raise"},      # 2: executor_error
            "expected": 273.15},
        _case(100,       -1.0),         # 3: mismatch (actual=273.15, expected=-1.0)
    ]
    outcome = run_validation(spec, cases)
    assert outcome.case_count == 4
    assert outcome.pass_count == 1
    assert outcome.fail_count == 3
    kinds = sorted(f["kind"] for f in outcome.failures)
    assert kinds == ["executor_error", "malformed_case", "mismatch"]
