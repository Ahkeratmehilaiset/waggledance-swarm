# SPDX-License-Identifier: BUSL-1.1
"""Conformance test for the shared leak-policy module using a locked, versioned corpus.

This test loads tests/core/leak_policy_conformance_corpus.json and asserts that
looks_like_leak_simple rejects every entry in must_reject and accepts every entry
in must_accept. The corpus is the regression lock: any future weakening of
waggledance/core/leak_policy.py (model/provider coverage, secret/path patterns,
false-positive guards) will cause this test to fail.

The corpus enumerates:
- must_reject: every provider/model family (bare + glued forms: gpt4o_hit, mpt7b_case,
  cohere_internal_model, claude_secret_x, command-r, etc.), secrets (Bearer/sk-/AKIA),
  path shapes (drive/unix roots, .. traversal, hf://, org/model:tag).
- must_accept: legit non-leak strings only (language_detection, hot_cache,
  hybrid_retrieval_8_cell, deterministic_solver, feature/normal-branch, main,
  yield_route_case, v3.latency_fixtures.local.v1, command_center).

Deterministic, offline, no network. No raw secrets/tokens beyond minimal stable
shapes required to exercise the allowlist-derived detector. Prefer schema
allowlists over ad-hoc denylists.

All claim gates N/A (pure test asset). This file and its corpus never satisfy
any claim; any consuming artifact must set:
claim_gate_satisfied=false
claim_safe=false
literal_future_claim_safe=false
controls_present=false
runtime_authority_granted=false
external_writes_applied=false
required_runtime_evidence_present=false
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from waggledance.core.leak_policy import (
    CLAIM_GATES,
    looks_like_leak_simple,
)

CORPUS_PATH = Path(__file__).parent / "leak_policy_conformance_corpus.json"


def _load_corpus() -> dict:
    with CORPUS_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)
    # Enforce claim gates are explicitly false in the artifact (pure test asset)
    gates = data.get("claim_gates", {})
    for gate in CLAIM_GATES:
        assert gate in gates, f"missing claim gate declaration for {gate}"
        assert gates[gate] is False, f"claim gate {gate} must be literal false in conformance corpus"
    return data


@pytest.fixture(scope="module")
def corpus() -> dict:
    return _load_corpus()


def test_corpus_is_versioned_and_complete(corpus: dict):
    """Lock the corpus shape and that it declares all gates false."""
    assert corpus["corpus_version"].startswith("wd.leak_policy.conformance_corpus.v")
    assert isinstance(corpus["must_reject"], list) and len(corpus["must_reject"]) >= 20
    assert isinstance(corpus["must_accept"], list) and len(corpus["must_accept"]) >= 5
    # provenance is deterministic label, no wallclock
    assert "hand-authored" in corpus.get("provenance", "").lower() or "stable shapes" in corpus.get("provenance", "").lower()


@pytest.mark.parametrize("leak_value", _load_corpus()["must_reject"])
def test_must_reject_is_rejected_by_simple_checker(leak_value: str):
    """Every must_reject shape must be rejected (True) by looks_like_leak_simple."""
    assert isinstance(leak_value, str)
    assert looks_like_leak_simple(leak_value) is True, f"expected leak but got safe for: {leak_value}"


@pytest.mark.parametrize("safe_value", _load_corpus()["must_accept"])
def test_must_accept_is_accepted_by_simple_checker(safe_value: str):
    """Every must_accept string must be accepted (False = not a leak) by looks_like_leak_simple."""
    assert isinstance(safe_value, str)
    assert looks_like_leak_simple(safe_value) is False, f"expected safe but got leak for: {safe_value}"


def test_all_claim_gates_are_false_in_corpus_artifact(corpus: dict):
    """Explicit audit: the emitted corpus carries all gates as false (no carve-outs)."""
    gates = corpus["claim_gates"]
    for gate in CLAIM_GATES:
        assert gates[gate] is False
