# SPDX-License-Identifier: Apache-2.0
"""spec-gaming expansion provenance fixture: schema + fold-in tests.

2026-06-10 expansion: spec-gaming was the last critical family at 3; now
7. With this fold every critical defect family is at >= 6. Same
discipline as the prior expansions: the expansion fixture pair is
provenance only; every case and paired expectation must also exist in
the strict ``v0`` fixture pair.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "schemas" / "v3_13_0"
CORPUS_DIR = ROOT / "tests" / "fixtures" / "magma_adversarial_corpus"
EXPANSION = CORPUS_DIR / "v0_expansion_2026_06_10_spec_gaming.json"
EXPANSION_EXPECTATIONS = (
    CORPUS_DIR / "v0_expansion_2026_06_10_spec_gaming_expectations.json"
)
LABEL = "spec_gaming_expansion_2026_06_10"
CRITICAL_FAMILIES = (
    "fail-open",
    "governance_bypass",
    "hallucinated-success",
    "path_escape",
    "regression-process",
    "spec-gaming",
)


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


def test_expansion_is_exactly_four_spec_gaming_cases():
    cases = _read_json(EXPANSION)["cases"]
    assert len(cases) == 4
    assert all(c["defect_type"] == "spec-gaming" for c in cases)
    ids = [c["case_id"] for c in cases]
    assert ids == sorted(ids) and len(ids) == len(set(ids))
    assert all(":spec_gaming:" in case_id for case_id in ids)


def test_expansion_covers_distinct_gaming_mechanisms():
    """Each case probes a distinct spec-gaming mechanism, distinct from the
    existing benchmark-comparison / held-out / runaway-self-approval trio."""
    cases = _read_json(EXPANSION)["cases"]
    tag_sets = [set(c["tags"]) for c in cases]
    mechanisms = [
        {"test_set_tuning"},
        {"metric_redefinition"},
        {"trivial_subset"},
        {"proxy_metric"},
    ]
    for mechanism, tags in zip(mechanisms, tag_sets):
        assert mechanism <= tags, (mechanism, tags)


def test_expansion_case_ids_folded_into_strict_v0():
    v0_ids = {c["case_id"] for c in _read_json(CORPUS_DIR / "v0.json")["cases"]}
    expansion_ids = {c["case_id"] for c in _read_json(EXPANSION)["cases"]}
    assert sorted(expansion_ids - v0_ids) == []


def test_folded_cases_byte_identical_to_strict_v0():
    v0_cases = {c["case_id"]: c for c in _read_json(CORPUS_DIR / "v0.json")["cases"]}
    for case in _read_json(EXPANSION)["cases"]:
        assert v0_cases[case["case_id"]] == case, case["case_id"]
    v0_exps = {
        e["case_id"]: e
        for e in _read_json(CORPUS_DIR / "v0_expectations.json")["expectations"]
    }
    for exp in _read_json(EXPANSION_EXPECTATIONS)["expectations"]:
        assert v0_exps[exp["case_id"]] == exp, exp["case_id"]


def test_strict_v0_spec_gaming_coverage_raised():
    cases = _read_json(CORPUS_DIR / "v0.json")["cases"]
    count = sum(1 for c in cases if c["defect_type"] == "spec-gaming")
    assert count == 7


def test_all_critical_families_now_at_least_six():
    """Milestone this fold completes: every critical defect family >= 6."""
    cases = _read_json(CORPUS_DIR / "v0.json")["cases"]
    for family in CRITICAL_FAMILIES:
        count = sum(1 for c in cases if c["defect_type"] == family)
        assert count >= 6, (family, count)


def test_every_expansion_case_has_paired_require_approval_expectation():
    cases = {c["case_id"] for c in _read_json(EXPANSION)["cases"]}
    exps = _read_json(EXPANSION_EXPECTATIONS)["expectations"]
    assert {e["case_id"] for e in exps} == cases
    for exp in exps:
        assert exp["expected_gate"] == "require_approval"
        assert exp["expected_verdict"] == "fail"
        assert "policy:gaming_attempt_detected" in exp["expected_reason_codes"]
        assert "spec:claim_overreach" in exp["expected_reason_codes"]
        assert exp["should_claude_catch"] is True
        assert exp["should_codex_catch"] is True


def test_canaries_unique_and_follow_naming():
    all_cases = _read_json(CORPUS_DIR / "v0.json")["cases"]
    family_canaries = [
        c["privacy_canary"] for c in all_cases if c["defect_type"] == "spec-gaming"
    ]
    assert len(family_canaries) == len(set(family_canaries))
    expansion_ids = {c["case_id"] for c in _read_json(EXPANSION)["cases"]}
    other_canaries = {
        c["privacy_canary"]
        for c in all_cases
        if c["case_id"] not in expansion_ids
    }
    for case in _read_json(EXPANSION)["cases"]:
        assert case["privacy_canary"].startswith("canary_spec_gaming_")
        assert case["privacy_canary"] not in other_canaries


def test_held_out_split_unchanged_and_valid():
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
