# SPDX-License-Identifier: Apache-2.0
"""Hallucinated-success expansion provenance fixture: schema + fold-in tests.

2026-06-10 expansion targeting the lowest critical defect-type coverage
(hallucinated-success had 2 cases; rollup recommended expansion). Same
discipline as the Phase D expansion: the expansion fixture pair is
provenance only; every case and paired expectation must also exist in the
strict ``v0`` fixture pair, which remains the authoritative gate.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "schemas" / "v3_13_0"
CORPUS_DIR = ROOT / "tests" / "fixtures" / "magma_adversarial_corpus"
EXPANSION = CORPUS_DIR / "v0_expansion_2026_06_10.json"
EXPANSION_EXPECTATIONS = CORPUS_DIR / "v0_expansion_2026_06_10_expectations.json"
LABEL = "hallucinated_success_expansion_2026_06_10"


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _try_jsonschema_validator():
    try:
        import jsonschema  # noqa: PLC0415
    except ImportError:
        pytest.skip("jsonschema not installed in this env")
    return jsonschema


# --- file presence + labels ----------------------------------------------


def test_expansion_fixture_exists():
    assert EXPANSION.exists()
    assert EXPANSION_EXPECTATIONS.exists()


def test_expansion_carries_label_and_folded_status():
    fixture = _read_json(EXPANSION)
    assert fixture["corpus_version"] == "magma.synthetic_adversarial_corpus.v0"
    assert fixture["expansion_label"] == LABEL
    assert fixture["expansion_status"] == "folded_into_v0"
    assert "folded into the strict v0 corpus" in fixture["expansion_note"]


def test_expectations_carry_label():
    expectations = _read_json(EXPANSION_EXPECTATIONS)
    assert (
        expectations["expectations_version"]
        == "magma.synthetic_adversarial_expectations.v0"
    )
    assert expectations["expansion_label"] == LABEL


# --- schema validity -------------------------------------------------------


def test_each_expansion_case_validates_against_case_schema():
    jsonschema = _try_jsonschema_validator()
    schema = _read_json(SCHEMAS / "synthetic_adversarial_case.v0.json")
    for case in _read_json(EXPANSION)["cases"]:
        jsonschema.validate(case, schema)


def test_each_expansion_expectation_validates_against_expectation_schema():
    jsonschema = _try_jsonschema_validator()
    schema = _read_json(SCHEMAS / "synthetic_adversarial_expectation.v0.json")
    for exp in _read_json(EXPANSION_EXPECTATIONS)["expectations"]:
        jsonschema.validate(exp, schema)


# --- family focus + case-id discipline --------------------------------------


def test_expansion_is_exactly_four_hallucinated_success_cases():
    cases = _read_json(EXPANSION)["cases"]
    assert len(cases) == 4
    assert all(c["defect_type"] == "hallucinated-success" for c in cases)
    ids = [c["case_id"] for c in cases]
    assert ids == sorted(ids) and len(ids) == len(set(ids))
    assert all(":hallucinated_success:" in case_id for case_id in ids)


def test_expansion_case_ids_folded_into_strict_v0():
    v0_ids = {c["case_id"] for c in _read_json(CORPUS_DIR / "v0.json")["cases"]}
    expansion_ids = {c["case_id"] for c in _read_json(EXPANSION)["cases"]}
    assert sorted(expansion_ids - v0_ids) == []


def test_folded_cases_byte_identical_to_strict_v0():
    """Fold-in must not drift: the strict-v0 copy of each expansion case is
    identical to the provenance copy (same discipline for expectations)."""
    v0_cases = {
        c["case_id"]: c for c in _read_json(CORPUS_DIR / "v0.json")["cases"]
    }
    for case in _read_json(EXPANSION)["cases"]:
        assert v0_cases[case["case_id"]] == case, case["case_id"]
    v0_exps = {
        e["case_id"]: e
        for e in _read_json(CORPUS_DIR / "v0_expectations.json")["expectations"]
    }
    for exp in _read_json(EXPANSION_EXPECTATIONS)["expectations"]:
        assert v0_exps[exp["case_id"]] == exp, exp["case_id"]


def test_strict_v0_hallucinated_success_coverage_raised():
    """The point of the expansion: the lowest critical defect type now has
    6 cases in the strict corpus (was 2)."""
    cases = _read_json(CORPUS_DIR / "v0.json")["cases"]
    count = sum(1 for c in cases if c["defect_type"] == "hallucinated-success")
    assert count == 6


def test_every_expansion_case_has_paired_expectation_with_family_codes():
    cases = {c["case_id"] for c in _read_json(EXPANSION)["cases"]}
    exps = _read_json(EXPANSION_EXPECTATIONS)["expectations"]
    assert {e["case_id"] for e in exps} == cases
    for exp in exps:
        assert exp["expected_gate"] == "review"
        assert exp["expected_verdict"] == "insufficient_evidence"
        assert "evidence:hallucinated_success" in exp["expected_reason_codes"]
        assert exp["should_claude_catch"] is True
        assert exp["should_codex_catch"] is True


def test_canaries_unique_and_follow_naming():
    # Whole-corpus canary uniqueness does NOT hold pre-expansion (the
    # strict v0 has historical duplicate/None canaries), so the guard here
    # is scoped: the hallucinate-family canaries must be unique and the
    # expansion must not reuse any existing canary string.
    all_cases = _read_json(CORPUS_DIR / "v0.json")["cases"]
    hallucinate_canaries = [
        c["privacy_canary"]
        for c in all_cases
        if c["defect_type"] == "hallucinated-success"
    ]
    assert len(hallucinate_canaries) == len(set(hallucinate_canaries))
    expansion_ids = {c["case_id"] for c in _read_json(EXPANSION)["cases"]}
    other_canaries = {
        c["privacy_canary"]
        for c in all_cases
        if c["case_id"] not in expansion_ids
    }
    for case in _read_json(EXPANSION)["cases"]:
        assert case["privacy_canary"].startswith("canary_hallucinate_")
        assert case["privacy_canary"] not in other_canaries


def test_held_out_split_unchanged_and_valid():
    """The expansion adds train-side cases only; the held-out id set is the
    original six and every id still resolves to a case."""
    corpus = _read_json(CORPUS_DIR / "v0.json")
    held_out = corpus["split"]["held_out_case_ids"]
    assert len(held_out) == 6
    case_ids = {c["case_id"] for c in corpus["cases"]}
    assert set(held_out) <= case_ids
    expansion_ids = {c["case_id"] for c in _read_json(EXPANSION)["cases"]}
    assert not expansion_ids & set(held_out)


def test_strict_validator_passes_on_expanded_corpus():
    from tools.validate_synthetic_adversarial_corpus import main as validate_main

    assert validate_main([]) == 0
