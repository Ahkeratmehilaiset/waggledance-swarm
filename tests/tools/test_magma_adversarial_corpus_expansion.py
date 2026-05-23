# SPDX-License-Identifier: Apache-2.0
"""Phase D PR1 expansion fixture: schema + structure tests.

The expansion lives in a separate fixture pair
(``tests/fixtures/magma_adversarial_corpus/v0_expansion_2026_05_23.json``
plus its expectations) so the strict ``v0`` corpus accuracy assertion in
``test_magma_adversarial_eval.py`` keeps protecting against regression on
the original strict v0 floor. The expansion is evaluated separately; adding it
to ``v0`` directly would also require deliberately refreshing downstream
baseline/proof artifacts in a later fold-in PR.

These tests validate the expansion fixture itself -- schema, case-id
discipline, family coverage, paired expectations -- while the separate
adversarial-eval test proves the deterministic gate now catches the new
attack vectors.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "schemas" / "v3_13_0"
EXPANSION = (
    ROOT
    / "tests"
    / "fixtures"
    / "magma_adversarial_corpus"
    / "v0_expansion_2026_05_23.json"
)
EXPANSION_EXPECTATIONS = (
    ROOT
    / "tests"
    / "fixtures"
    / "magma_adversarial_corpus"
    / "v0_expansion_2026_05_23_expectations.json"
)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _try_jsonschema_validator():
    try:
        import jsonschema  # noqa: PLC0415
    except ImportError:
        pytest.skip("jsonschema not installed in this env")
    return jsonschema


# --- file presence + corpus_version contract ----------------------------

def test_expansion_fixture_exists():
    assert EXPANSION.exists()
    assert EXPANSION_EXPECTATIONS.exists()


def test_expansion_carries_label_and_covered_status():
    fixture = _read_json(EXPANSION)
    assert fixture["corpus_version"] == "magma.synthetic_adversarial_corpus.v0"
    assert fixture["expansion_label"] == "phase_d_expansion_2026_05_23"
    assert fixture["expansion_status"] == "gate_extension_covered"
    # The note must explicitly identify itself as a separate partial-coverage
    # target kept out of the strict v0 floor until an intentional fold-in PR.
    assert "separate partial-coverage target" in fixture["expansion_note"]
    assert "strict v0 corpus remains" in fixture["expansion_note"]


def test_expectations_carry_label():
    expectations = _read_json(EXPANSION_EXPECTATIONS)
    assert (
        expectations["expectations_version"]
        == "magma.synthetic_adversarial_expectations.v0"
    )
    assert (
        expectations["expansion_label"] == "phase_d_expansion_2026_05_23"
    )


# --- schema validity ----------------------------------------------------

def test_each_expansion_case_validates_against_case_schema():
    jsonschema = _try_jsonschema_validator()
    schema = _read_json(SCHEMAS / "synthetic_adversarial_case.v0.json")
    fixture = _read_json(EXPANSION)
    for case in fixture["cases"]:
        jsonschema.validate(case, schema)


def test_each_expansion_expectation_validates_against_expectation_schema():
    jsonschema = _try_jsonschema_validator()
    schema = _read_json(SCHEMAS / "synthetic_adversarial_expectation.v0.json")
    expectations = _read_json(EXPANSION_EXPECTATIONS)
    for exp in expectations["expectations"]:
        jsonschema.validate(exp, schema)


# --- case-id discipline + family coverage -------------------------------

def test_expansion_case_count_is_eight():
    fixture = _read_json(EXPANSION)
    assert len(fixture["cases"]) == 8


def test_expansion_case_ids_unique_and_disjoint_from_v0():
    """No case_id collides with the strict v0 corpus."""
    v0 = _read_json(
        ROOT / "tests" / "fixtures" / "magma_adversarial_corpus" / "v0.json"
    )
    fixture = _read_json(EXPANSION)
    v0_ids = {c["case_id"] for c in v0["cases"]}
    expansion_ids = [c["case_id"] for c in fixture["cases"]]
    # uniqueness within the expansion
    assert len(expansion_ids) == len(set(expansion_ids)), expansion_ids
    # disjointness from v0
    overlap = set(expansion_ids) & v0_ids
    assert overlap == set(), overlap


def test_expansion_covers_all_four_phase_d_families():
    """Each Phase D family (per the 100h plan) has at least one case in the
    expansion: charter-bypass (charter_violation OR policy_bypass),
    overclaim (subtle_drift OR evidence_spoofing on truth-claim),
    receipt-chain spoofing (evidence_spoofing on receipt mechanics),
    sanitization-contract (payload_leak)."""
    fixture = _read_json(EXPANSION)
    defects = Counter(c["defect_type"] for c in fixture["cases"])
    # charter-bypass family
    assert defects.get("charter_violation", 0) >= 1, defects
    # overclaim family (subtle_drift catches early-tag + consensus_grade flip)
    assert defects.get("subtle_drift", 0) >= 1, defects
    # receipt-chain spoofing family
    assert defects.get("evidence_spoofing", 0) >= 1, defects
    # sanitization-contract family
    assert defects.get("payload_leak", 0) >= 1, defects


def test_expansion_uses_only_enum_trap_markers():
    """Defense-in-depth: the schema test above already enforces this, but
    keep an explicit assertion so a future schema relaxation doesn't let
    free-form trap markers creep in via this expansion."""
    allowed = {
        "none",
        "ambiguous_allow_language",
        "hidden_write_intent",
        "authority_confusion",
        "digest_without_payload",
        "approval_wording_trap",
        "state_window_blindspot",
        "privacy_redaction_trap",
    }
    fixture = _read_json(EXPANSION)
    for case in fixture["cases"]:
        marker = case.get("peer_review_trap_marker")
        assert marker in allowed, (case["case_id"], marker)


# --- paired-expectation invariants --------------------------------------

def test_every_expansion_case_has_a_paired_expectation():
    fixture = _read_json(EXPANSION)
    expectations = _read_json(EXPANSION_EXPECTATIONS)
    case_ids = {c["case_id"] for c in fixture["cases"]}
    expectation_ids = {e["case_id"] for e in expectations["expectations"]}
    assert case_ids == expectation_ids, case_ids ^ expectation_ids


def test_expectations_include_false_positive_allow_case():
    """The expansion seeds at least one ALLOW expectation -- the
    sanitization-contract false-positive guard (RFC 2606 example domain).
    Without an allow case the corpus would only train refuse behaviour,
    which inflates false positives in production."""
    expectations = _read_json(EXPANSION_EXPECTATIONS)
    allows = [
        e for e in expectations["expectations"] if e["expected_gate"] == "allow"
    ]
    assert allows, expectations


def test_expectations_majority_are_refusal_attacks():
    """Most cases are refusal-tier attack vectors (consistent with
    Phase D's adversarial focus)."""
    expectations = _read_json(EXPANSION_EXPECTATIONS)
    gates = Counter(e["expected_gate"] for e in expectations["expectations"])
    refuse_or_higher = gates.get("refuse", 0) + gates.get("require_approval", 0)
    assert refuse_or_higher >= len(expectations["expectations"]) // 2
