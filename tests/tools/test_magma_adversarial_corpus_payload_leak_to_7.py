# SPDX-License-Identifier: Apache-2.0
"""payload-leak tail-floor expansion provenance fixture tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "schemas" / "v3_13_0"
CORPUS_DIR = ROOT / "tests" / "fixtures" / "magma_adversarial_corpus"
EXPANSION = CORPUS_DIR / "v0_expansion_2026_06_11_payload_leak_to_7.json"
EXPANSION_EXPECTATIONS = (
    CORPUS_DIR / "v0_expansion_2026_06_11_payload_leak_to_7_expectations.json"
)
LABEL = "payload_leak_to_7_2026_06_11"


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _try_jsonschema_validator():
    try:
        import jsonschema  # noqa: PLC0415
    except ImportError:
        pytest.skip("jsonschema not installed in this env")
    return jsonschema


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


def test_expansion_is_exactly_one_payload_leak_case():
    cases = _read_json(EXPANSION)["cases"]
    assert len(cases) == 1
    case = cases[0]
    assert case["case_id"] == "case:adv:payload_leak:007"
    assert case["defect_type"] == "payload_leak"
    assert {"incident_export", "evaluator_replay", "secondary_artifact"} <= set(
        case["tags"]
    )


def test_expansion_case_ids_folded_into_strict_v0():
    v0_ids = {c["case_id"] for c in _read_json(CORPUS_DIR / "v0.json")["cases"]}
    expansion_ids = {c["case_id"] for c in _read_json(EXPANSION)["cases"]}
    assert sorted(expansion_ids - v0_ids) == []


def test_folded_cases_byte_identical_to_strict_v0():
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


def test_strict_v0_payload_leak_coverage_raised_to_seven():
    cases = _read_json(CORPUS_DIR / "v0.json")["cases"]
    count = sum(1 for c in cases if c["defect_type"] == "payload_leak")
    assert count == 7


def test_every_expansion_case_has_paired_refuse_expectation():
    cases = {c["case_id"] for c in _read_json(EXPANSION)["cases"]}
    exps = _read_json(EXPANSION_EXPECTATIONS)["expectations"]
    assert {e["case_id"] for e in exps} == cases
    for exp in exps:
        assert exp["expected_gate"] == "refuse"
        assert exp["expected_verdict"] == "refuse"
        assert "privacy:payload_must_not_leak" in exp["expected_reason_codes"]
        assert "digest:only" in exp["expected_reason_codes"]
        assert exp["should_claude_catch"] is True
        assert exp["should_codex_catch"] is True


def test_canaries_unique_and_follow_naming():
    all_cases = _read_json(CORPUS_DIR / "v0.json")["cases"]
    family_canaries = [
        c["privacy_canary"]
        for c in all_cases
        if c["defect_type"] == "payload_leak"
    ]
    assert len(family_canaries) == len(set(family_canaries))
    case = _read_json(EXPANSION)["cases"][0]
    assert case["privacy_canary"] == "canary_payload_007_DO_NOT_LEAK"


def test_held_out_split_unchanged_and_valid():
    corpus = _read_json(CORPUS_DIR / "v0.json")
    held_out = corpus["split"]["held_out_case_ids"]
    assert len(held_out) == 6
    case_ids = {c["case_id"] for c in corpus["cases"]}
    assert set(held_out) <= case_ids
    assert "case:adv:payload_leak:007" not in held_out


def test_strict_validator_passes_on_expanded_corpus():
    from tools.validate_synthetic_adversarial_corpus import main as validate_main

    assert validate_main([]) == 0
