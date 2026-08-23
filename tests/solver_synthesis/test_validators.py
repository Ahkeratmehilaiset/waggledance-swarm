# SPDX-License-Identifier: Apache-2.0
"""Direct unit tests for waggledance.core.solver_synthesis.validators.

The validators module runs the gate stack on a SolverCandidate:
syntactic / semantic / property tests / regression / shadow eval, then
maps the result through `decide_verdict` to one of the seven verdict
strings. The verdict drives downstream promotion decisions.

A regression in the verdict precedence (which gate wins when multiple
fail), the shadow thresholds (`min_shadow_observations`,
`min_concordance`), or the structural validators can let an invalid
candidate slip through with a passing verdict — or block a good
candidate by returning the wrong verdict for the same input. Direct
test coverage on this file was zero before this PR.

Pinned invariants:

- `syntactic_validate`: empty solver_name / cell_id / non-dict
  spec_or_code → fails; otherwise passes.
- `semantic_validate`: invalid invariants (non-str / empty) and
  non-str expected_output_unit fail.
- `run_property_tests` / `run_regression_tests`: count passed,
  collect failure names; empty input is vacuously passing.
- `evaluate_shadow`: clamps concordance to [0, 1].
- `decide_verdict` precedence (load-bearing):
  syntactic_invalid > semantic_invalid > property_test_failed >
  regression_detected > needs_more_shadow > rejected_low_value >
  pass_all_gates. The `property_test_failed` branch was added in
  the 2026-05-09 post-merge fix after a Codex high-severity
  blocker found that failed property tests previously promoted to
  `pass_all_gates` because the parameter was accepted but never
  read.
"""
from __future__ import annotations

import pytest

from waggledance.core.solver_synthesis import SOLVER_SYNTHESIS_SCHEMA_VERSION
from waggledance.core.solver_synthesis.solver_candidate_store import (
    SolverCandidate,
)
from waggledance.core.solver_synthesis.validators import (
    CountedGateResult,
    GateResult,
    ShadowEvalResult,
    decide_verdict,
    evaluate_shadow,
    run_property_tests,
    run_regression_tests,
    semantic_validate,
    syntactic_validate,
    validate_candidate,
)


# --- helpers --------------------------------------------------------

def _candidate(*, solver_name: str = "test_solver",
               cell_id: str = "general",
               spec: dict | None = None) -> SolverCandidate:
    return SolverCandidate(
        schema_version=1,
        candidate_id="C-1",
        state="raw_candidate",
        solver_name=solver_name,
        cell_id=cell_id,
        spec_or_code=spec if spec is not None else {"kind": "scalar"},
        source_gap_ref="gap_x",
        no_runtime_mutation=True,
        produced_by="test",
        branch_name="test/x",
        base_commit_hash="deadbeef",
        pinned_input_manifest_sha256="f" * 64,
    )


# --- syntactic_validate --------------------------------------------

def test_syntactic_validate_passes_when_all_required_fields_set():
    g = syntactic_validate(_candidate())
    assert g.passed is True
    assert g.errors == ()


def test_syntactic_validate_fails_on_empty_solver_name():
    g = syntactic_validate(_candidate(solver_name=""))
    assert g.passed is False
    assert any("solver_name" in e for e in g.errors)


def test_syntactic_validate_fails_on_empty_cell_id():
    """Force-bypass SolverCandidate's HEX_CELLS post-init check by
    using object.__setattr__ — we are testing validators directly."""
    cand = _candidate()
    object.__setattr__(cand, "cell_id", "")
    g = syntactic_validate(cand)
    assert g.passed is False
    assert any("cell_id" in e for e in g.errors)


def test_syntactic_validate_collects_multiple_errors():
    cand = _candidate(solver_name="")
    object.__setattr__(cand, "cell_id", "")
    g = syntactic_validate(cand)
    assert g.passed is False
    assert len(g.errors) >= 2


# --- semantic_validate ---------------------------------------------

def test_semantic_validate_passes_with_well_formed_invariants_and_unit():
    g = semantic_validate(_candidate(spec={
        "invariants": ["x > 0", "y < 100"],
        "expected_output_unit": "celsius",
    }))
    assert g.passed is True


def test_semantic_validate_passes_when_optional_fields_absent():
    g = semantic_validate(_candidate(spec={"kind": "x"}))
    assert g.passed is True


def test_semantic_validate_fails_on_empty_invariant_string():
    g = semantic_validate(_candidate(spec={"invariants": ["x > 0", "  "]}))
    assert g.passed is False
    assert any("invariant" in e for e in g.errors)


def test_semantic_validate_fails_on_non_str_invariant():
    g = semantic_validate(_candidate(spec={"invariants": ["ok", 42]}))
    assert g.passed is False


def test_semantic_validate_fails_on_non_str_expected_output_unit():
    g = semantic_validate(_candidate(spec={
        "expected_output_unit": 1.0,
    }))
    assert g.passed is False


# --- counted gates -------------------------------------------------

def test_run_property_tests_counts_passed_and_failures():
    tests = [
        {"name": "p1", "passed": True},
        {"name": "p2", "passed": False},
        {"name": "p3", "passed": True},
    ]
    r = run_property_tests(_candidate(), tests)
    assert r.passed == 2
    assert r.total == 3
    assert r.failures == ("p2",)


def test_run_property_tests_empty_is_vacuously_zero():
    r = run_property_tests(_candidate(), [])
    assert r.passed == 0
    assert r.total == 0
    assert r.failures == ()


def test_run_regression_tests_counts_passed_and_failures():
    cases = [
        {"name": "r1", "passed": True},
        {"name": "r2", "passed": False},
    ]
    r = run_regression_tests(_candidate(), cases)
    assert r.passed == 1
    assert r.total == 2
    assert r.failures == ("r2",)


# --- counted gates: literal-True fail-closed rule -------------------
#
# 2026-08-23 product-integrity fix (lead assignment): the counted
# gates used ``bool(t.get("passed", False))`` / ``not t.get(...)``,
# so any truthy stand-in for a boolean — the string "false" from a
# JSON-ish harness, "true", 1, "no" — counted as a PASSED case with an
# empty failure list and validate_candidate promoted the candidate to
# ``pass_all_gates``. Only the literal ``True`` may count as passed.

class _ExplosiveBool:
    """Object whose truthiness must never be consulted."""

    def __bool__(self) -> bool:  # pragma: no cover - must not run
        raise AssertionError("truthiness of a passed value was coerced")


_NOT_LITERAL_TRUE = [
    pytest.param("false", id="str-false"),
    pytest.param("true", id="str-true"),
    pytest.param("True", id="str-True"),
    pytest.param("no", id="str-no"),
    pytest.param(1, id="int-1"),
    pytest.param(1.0, id="float-1"),
    pytest.param(0, id="int-0"),
    pytest.param(None, id="none"),
    pytest.param([True], id="list-true"),
    pytest.param({"passed": True}, id="nested-dict"),
    pytest.param(object(), id="object"),
    pytest.param(_ExplosiveBool(), id="explosive-bool"),
]


@pytest.mark.parametrize("gate", [run_property_tests, run_regression_tests],
                         ids=["property", "regression"])
@pytest.mark.parametrize("value", _NOT_LITERAL_TRUE)
def test_counted_gate_rejects_every_non_literal_true_passed_value(gate, value):
    r = gate(_candidate(), [{"name": "p1", "passed": value}])
    assert r.passed == 0
    assert r.total == 1
    assert r.failures == ("p1",)


@pytest.mark.parametrize("gate", [run_property_tests, run_regression_tests],
                         ids=["property", "regression"])
def test_counted_gate_missing_passed_key_is_a_failure(gate):
    r = gate(_candidate(), [{"name": "p1"}])
    assert r.passed == 0
    assert r.total == 1
    assert r.failures == ("p1",)


@pytest.mark.parametrize("gate", [run_property_tests, run_regression_tests],
                         ids=["property", "regression"])
def test_counted_gate_literal_true_still_counts_beside_truthy_stand_ins(gate):
    r = gate(_candidate(), [
        {"name": "p1", "passed": True},
        {"name": "p2", "passed": "true"},
        {"name": "p3", "passed": 1},
    ])
    assert r.passed == 1
    assert r.total == 3
    assert r.failures == ("p2", "p3")


@pytest.mark.parametrize("gate", [run_property_tests, run_regression_tests],
                         ids=["property", "regression"])
def test_counted_gate_non_mapping_entries_are_named_failures(gate):
    r = gate(_candidate(), [None, "passed", 42, {"name": "ok", "passed": True}])
    assert r.passed == 1
    assert r.total == 4
    assert r.failures == ("<malformed:0>", "<malformed:1>", "<malformed:2>")


@pytest.mark.parametrize("value", ["false", "true", 1],
                         ids=["str-false", "str-true", "int-1"])
def test_validate_candidate_truthy_property_passed_is_property_test_failed(value):
    """The exact lead reproduction: passed:"false" previously yielded
    1/1 and pass_all_gates."""
    report = validate_candidate(
        _candidate(),
        property_tests_input=[{"name": "prop1", "passed": value}],
        regression_input=[],
        shadow_observations=100,
        shadow_concordance=0.95,
    )
    assert report.verdict == "property_test_failed"
    assert report.property_tests.passed == 0
    assert report.property_tests.total == 1
    assert report.property_tests.failures == ("prop1",)


@pytest.mark.parametrize("value", ["false", "true", 1],
                         ids=["str-false", "str-true", "int-1"])
def test_validate_candidate_truthy_regression_passed_is_regression_detected(value):
    report = validate_candidate(
        _candidate(),
        property_tests_input=[{"name": "prop1", "passed": True}],
        regression_input=[{"name": "reg1", "passed": value}],
        shadow_observations=100,
        shadow_concordance=0.95,
    )
    assert report.verdict == "regression_detected"
    assert report.regression.passed == 0
    assert report.regression.total == 1
    assert report.regression.failures == ("reg1",)


# --- evaluate_shadow ------------------------------------------------

def test_evaluate_shadow_clamps_concordance_to_unit_interval():
    r = evaluate_shadow(_candidate(), observations=10, concordance_ratio=1.5)
    assert r.concordance_ratio == 1.0
    r = evaluate_shadow(_candidate(), observations=10, concordance_ratio=-0.5)
    assert r.concordance_ratio == 0.0


def test_evaluate_shadow_passes_through_observations():
    r = evaluate_shadow(_candidate(), observations=42, concordance_ratio=0.7)
    assert r.observations == 42
    assert r.concordance_ratio == pytest.approx(0.7)


# --- decide_verdict precedence (load-bearing) ----------------------

_OK_GATE = GateResult(passed=True, errors=())
_FAIL_GATE = GateResult(passed=False, errors=("e",))
_OK_COUNT = CountedGateResult(passed=0, total=0, failures=())
_FAIL_REG = CountedGateResult(passed=0, total=2, failures=("r1", "r2"))
_GOOD_SHADOW = ShadowEvalResult(observations=100, concordance_ratio=0.95)
_LOW_SHADOW = ShadowEvalResult(observations=100, concordance_ratio=0.50)
_FEW_OBS = ShadowEvalResult(observations=5, concordance_ratio=0.95)


def test_decide_verdict_syntactic_invalid_first():
    """Syntactic failure wins over every other gate failure."""
    v = decide_verdict(
        syntactic=_FAIL_GATE, semantic=_FAIL_GATE,
        property_tests=_OK_COUNT, regression=_FAIL_REG,
        shadow=_LOW_SHADOW,
    )
    assert v == "syntactic_invalid"


def test_decide_verdict_semantic_invalid_when_syntactic_passes():
    v = decide_verdict(
        syntactic=_OK_GATE, semantic=_FAIL_GATE,
        property_tests=_OK_COUNT, regression=_FAIL_REG,
        shadow=_LOW_SHADOW,
    )
    assert v == "semantic_invalid"


def test_decide_verdict_property_test_failed_when_failures_exist():
    """2026-05-09 post-merge fix: property tests are declarative
    invariants. A failure (passed < total) MUST return
    `property_test_failed`, NOT `pass_all_gates`. This is the
    regression test for the high-blocker Codex flagged after
    PR #116 was merged with the gate missing entirely."""
    fail_props = CountedGateResult(passed=0, total=1, failures=("prop1",))
    v = decide_verdict(
        syntactic=_OK_GATE, semantic=_OK_GATE,
        property_tests=fail_props, regression=_OK_COUNT,
        shadow=_GOOD_SHADOW,
    )
    assert v == "property_test_failed"


def test_decide_verdict_property_test_failure_preempts_regression_and_shadow():
    """Precedence: property_test_failed wins over regression/shadow
    even when those would also flag — property tests are structural
    invariants, regression/shadow are performance signals."""
    fail_props = CountedGateResult(passed=1, total=2, failures=("p2",))
    v = decide_verdict(
        syntactic=_OK_GATE, semantic=_OK_GATE,
        property_tests=fail_props, regression=_FAIL_REG,
        shadow=_LOW_SHADOW,
    )
    assert v == "property_test_failed"


def test_decide_verdict_property_total_zero_does_not_trigger_failure():
    """Empty property_tests (total=0) is vacuously passing — must
    NOT return property_test_failed."""
    empty_props = CountedGateResult(passed=0, total=0, failures=())
    v = decide_verdict(
        syntactic=_OK_GATE, semantic=_OK_GATE,
        property_tests=empty_props, regression=_OK_COUNT,
        shadow=_GOOD_SHADOW,
    )
    assert v == "pass_all_gates"


def test_decide_verdict_regression_detected_before_shadow_checks():
    v = decide_verdict(
        syntactic=_OK_GATE, semantic=_OK_GATE,
        property_tests=_OK_COUNT, regression=_FAIL_REG,
        shadow=_GOOD_SHADOW,
    )
    assert v == "regression_detected"


def test_decide_verdict_needs_more_shadow_when_observations_below_threshold():
    v = decide_verdict(
        syntactic=_OK_GATE, semantic=_OK_GATE,
        property_tests=_OK_COUNT, regression=_OK_COUNT,
        shadow=_FEW_OBS,
    )
    assert v == "needs_more_shadow"


def test_decide_verdict_rejected_low_value_when_concordance_below_threshold():
    v = decide_verdict(
        syntactic=_OK_GATE, semantic=_OK_GATE,
        property_tests=_OK_COUNT, regression=_OK_COUNT,
        shadow=_LOW_SHADOW,
    )
    assert v == "rejected_low_value"


def test_decide_verdict_pass_all_gates_when_everything_clears():
    v = decide_verdict(
        syntactic=_OK_GATE, semantic=_OK_GATE,
        property_tests=_OK_COUNT, regression=_OK_COUNT,
        shadow=_GOOD_SHADOW,
    )
    assert v == "pass_all_gates"


def test_decide_verdict_min_shadow_observations_threshold_is_50_default():
    """Pin the default threshold so a future tweak shows up here."""
    just_below = ShadowEvalResult(observations=49, concordance_ratio=0.95)
    just_at = ShadowEvalResult(observations=50, concordance_ratio=0.95)
    v_below = decide_verdict(
        syntactic=_OK_GATE, semantic=_OK_GATE,
        property_tests=_OK_COUNT, regression=_OK_COUNT,
        shadow=just_below,
    )
    v_at = decide_verdict(
        syntactic=_OK_GATE, semantic=_OK_GATE,
        property_tests=_OK_COUNT, regression=_OK_COUNT,
        shadow=just_at,
    )
    assert v_below == "needs_more_shadow"
    assert v_at == "pass_all_gates"


# --- validate_candidate end-to-end ---------------------------------

def test_validate_candidate_assembles_full_report():
    report = validate_candidate(
        _candidate(),
        property_tests_input=[{"name": "p1", "passed": True}],
        regression_input=[],
        shadow_observations=100,
        shadow_concordance=0.95,
        produced_at_iso="2026-05-09T00:00:00Z",
        execution_backend="cpu",
    )
    assert report.candidate_id == "C-1"
    assert report.schema_version == SOLVER_SYNTHESIS_SCHEMA_VERSION
    assert report.verdict == "pass_all_gates"
    assert report.produced_at_iso == "2026-05-09T00:00:00Z"
    assert report.execution_backend == "cpu"
    assert report.syntactic.passed is True
    assert report.shadow_evaluation.observations == 100


def test_validate_candidate_with_failed_property_test_returns_property_test_failed():
    """2026-05-09 post-merge regression test (Codex high-blocker
    runtime probe): a candidate with a failed property test MUST
    return verdict=property_test_failed, not pass_all_gates.

    The exact probe Codex used to surface the bug on `main`:
        validate_candidate(
            property_tests_input=[{"name": "prop1", "passed": False}],
            regression_input=[],
            shadow_observations=100, shadow_concordance=0.95,
        )
    pre-fix returned "pass_all_gates" (autogrowth promotion safety
    bug); post-fix must return "property_test_failed"."""
    report = validate_candidate(
        _candidate(),
        property_tests_input=[{"name": "prop1", "passed": False}],
        regression_input=[],
        shadow_observations=100,
        shadow_concordance=0.95,
    )
    assert report.verdict == "property_test_failed"
    assert report.property_tests.total == 1
    assert report.property_tests.passed == 0
    assert "prop1" in report.property_tests.failures
