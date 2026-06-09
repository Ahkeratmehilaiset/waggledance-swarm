# SPDX-License-Identifier: BUSL-1.1
"""Tests for the core (tools-free) adversarial-corpus eval runner (T5b inline)."""
from __future__ import annotations

from pathlib import Path

import pytest

from waggledance.core.magma.adversarial_corpus_eval import (
    MIN_CRITICAL_DEFECT_CASES,
    AdversarialCorpusEvalError,
    build_per_case_coverage_report,
    run_adversarial_corpus_evaluation,
)

SOLVER = "a" * 64


def test_real_corpus_all_caught_and_bound():
    r = run_adversarial_corpus_evaluation(bound_solver_hash=SOLVER)
    assert r["ok"] is True
    assert r["bound_solver_hash"] == SOLVER
    assert r["case_count"] >= 40
    assert all(c["ok"] for c in r["cases"])
    assert all("defect_class" in c for c in r["cases"])
    assert r["per_case_coverage"]["min_critical_defect_cases"] == 2
    critical_caught = r["per_case_coverage"]["critical_defect_type_caught_counts"]
    assert critical_caught["governance_bypass"] >= 2
    assert critical_caught["path_escape"] >= 2
    assert r["per_case_coverage"]["critical_defect_types_below_floor"] == {}


def test_real_corpus_distinct_coverage_has_no_duplicate_padding():
    cov = run_adversarial_corpus_evaluation(bound_solver_hash=SOLVER)[
        "per_case_coverage"
    ]
    # The committed corpus uses distinct case_ids: distinct == occurrence.
    assert cov["duplicate_case_ids"] == []
    assert cov["distinct_case_count"] >= 40
    assert (
        cov["critical_defect_type_distinct_caught_counts"]
        == cov["critical_defect_type_caught_counts"]
    )
    assert cov["critical_defect_types_below_distinct_floor"] == {}


def test_distinct_coverage_exposes_duplicate_padding():
    # A critical defect's occurrence floor is "met" by re-listing ONE caught
    # case MIN_CRITICAL_DEFECT_CASES times — but distinct coverage is just 1.
    cases = [
        {"case_id": "dup-1", "defect_class": "governance_bypass", "ok": True}
        for _ in range(MIN_CRITICAL_DEFECT_CASES)
    ]
    cov = build_per_case_coverage_report(cases)
    # Occurrence floor looks satisfied...
    assert (
        cov["critical_defect_type_caught_counts"]["governance_bypass"]
        == MIN_CRITICAL_DEFECT_CASES
    )
    assert "governance_bypass" not in cov["critical_defect_types_below_floor"]
    # ...but the distinct view shows only one real case, and flags it.
    assert cov["critical_defect_type_distinct_caught_counts"]["governance_bypass"] == 1
    assert (
        cov["critical_defect_types_below_distinct_floor"]["governance_bypass"] == 1
    )
    assert cov["duplicate_case_ids"] == ["dup-1"]
    assert cov["distinct_case_count"] == 1


def test_distinct_coverage_counts_genuine_distinct_cases():
    cases = [
        {"case_id": "gb-1", "defect_class": "governance_bypass", "ok": True},
        {"case_id": "gb-2", "defect_class": "governance_bypass", "ok": True},
    ]
    cov = build_per_case_coverage_report(cases)
    assert cov["critical_defect_type_distinct_caught_counts"]["governance_bypass"] == 2
    assert "governance_bypass" not in cov["critical_defect_types_below_distinct_floor"]
    assert cov["duplicate_case_ids"] == []
    assert cov["distinct_case_count"] == 2


def test_distinct_coverage_ignores_uncaught_duplicates_for_floor():
    # A repeated case that was NOT caught contributes neither distinct caught
    # coverage nor a false floor pass, but is still reported as a duplicate.
    cases = [
        {"case_id": "gb-1", "defect_class": "governance_bypass", "ok": True},
        {"case_id": "gb-x", "defect_class": "governance_bypass", "ok": False},
        {"case_id": "gb-x", "defect_class": "governance_bypass", "ok": False},
    ]
    cov = build_per_case_coverage_report(cases)
    assert cov["critical_defect_type_distinct_caught_counts"]["governance_bypass"] == 1
    assert cov["critical_defect_types_below_distinct_floor"]["governance_bypass"] == 1
    assert cov["duplicate_case_ids"] == ["gb-x"]
    assert cov["distinct_case_count"] == 2  # gb-1, gb-x


def test_distinct_coverage_handles_missing_case_id_gracefully():
    cases = [
        {"defect_class": "fail-open", "ok": True},  # no case_id
        {"case_id": "", "defect_class": "fail-open", "ok": True},  # blank
    ]
    cov = build_per_case_coverage_report(cases)
    assert cov["distinct_case_count"] == 0
    assert cov["duplicate_case_ids"] == []
    # occurrence coverage still counts the cases by defect class
    assert cov["critical_defect_type_caught_counts"]["fail-open"] == 2


def test_empty_solver_hash_fails_closed():
    for bad in ("", "   "):
        with pytest.raises(AdversarialCorpusEvalError):
            run_adversarial_corpus_evaluation(bound_solver_hash=bad)


def test_missing_corpus_fixture_fails_closed(tmp_path: Path):
    with pytest.raises(AdversarialCorpusEvalError):
        run_adversarial_corpus_evaluation(
            bound_solver_hash=SOLVER,
            corpus_path=tmp_path / "nope.json",
        )


def test_corpus_with_no_cases_fails_closed(tmp_path: Path):
    corpus = tmp_path / "c.json"
    corpus.write_text('{"cases": []}', encoding="utf-8")
    exp = tmp_path / "e.json"
    exp.write_text('{"expectations": [{"case_id": "x"}]}', encoding="utf-8")
    with pytest.raises(AdversarialCorpusEvalError):
        run_adversarial_corpus_evaluation(
            bound_solver_hash=SOLVER, corpus_path=corpus, expectations_path=exp
        )
