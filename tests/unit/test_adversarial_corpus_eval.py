# SPDX-License-Identifier: BUSL-1.1
"""Tests for the core (tools-free) adversarial-corpus eval runner (T5b inline)."""
from __future__ import annotations

from pathlib import Path

import pytest

from waggledance.core.magma.adversarial_corpus_eval import (
    AdversarialCorpusEvalError,
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
