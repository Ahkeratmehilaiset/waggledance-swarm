"""Offline tests for tools/run_router_determinism_proof.py.

Proof (not a safety gate) that SmartRouterV2.route() is deterministic over the
canonical corpus. Tests assert the real proof passes, that the determinism and
privacy verdicts are DERIVED (forge a run2 drift + a raw-query leak), that
volatile timing is excluded, and that no raw query leaks.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location(
    "run_router_determinism_proof",
    REPO_ROOT / "tools" / "run_router_determinism_proof.py",
)
mod = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(mod)  # type: ignore[union-attr]

# A numeric+word sentinel query (also exercises the math classifier capture)
# whose text must never appear in the report.
SAMPLE = [
    {"id": "q1", "query": "how much honey yield xyzzysentinel this season"},
    {"id": "q2", "query": "9090901*x sentineltoken"},
    {"id": "q3", "query": "prepare the hive for winter"},
]


def test_real_proof_is_deterministic():
    r = mod.diagnose("apiary", SAMPLE)
    assert r["ok"] is True
    assert r["corpus_size"] == len(SAMPLE)
    assert r["all_deterministic"] is True
    assert r["deterministic_ratio"] == 1.0
    assert r["nondeterministic_count"] == 0
    assert r["invariants"]["deterministic_offline"] is True


def test_full_corpus_deterministic():
    corpus = mod.load_corpus(REPO_ROOT / "configs" / "benchmarks.yaml")
    r = mod.diagnose("apiary", corpus)
    assert r["ok"] is True
    assert r["all_deterministic"] is True
    assert r["deterministic_ratio"] == 1.0


def test_no_raw_query_leak():
    r = mod.diagnose("apiary", SAMPLE)
    blob = json.dumps(r, ensure_ascii=False)
    for sentinel in ("xyzzysentinel", "9090901", "sentineltoken"):
        assert sentinel not in blob
    assert r["invariants"]["raw_query_not_emitted"] is True
    # emitted per-query keys are the safe set (no inputs / matched_keywords / query)
    for dec in r["routing_decisions"]:
        assert set(dec) == {
            "id", "layer", "reason", "decision_id", "fallback", "deterministic"
        }


def test_volatile_timing_excluded():
    assert "routing_time_ms" not in mod.STABLE_DECISION_FIELDS
    assert "inputs" not in mod.STABLE_DECISION_FIELDS
    assert "matched_keywords" not in mod.STABLE_DECISION_FIELDS
    r = mod.diagnose("apiary", SAMPLE)
    assert "routing_time_ms" not in json.dumps(r)
    assert r["invariants"]["volatile_timing_excluded"] is True


def test_forge_nondeterministic_fails_closed(monkeypatch):
    # Make the stable view drift between the two runs of every query: the DERIVED
    # determinism verdict must flip False and fail the proof closed.
    counter = {"n": 0}

    def _drift(result):  # noqa: ANN001
        counter["n"] += 1
        return {"layer": "model_based" if counter["n"] % 2 else "retrieval"}

    monkeypatch.setattr(mod, "_stable_decision", _drift)
    r = mod.diagnose("apiary", SAMPLE)
    assert r["ok"] is False
    assert r["all_deterministic"] is False
    assert r["deterministic_ratio"] == 0.0
    assert "nondeterministic_routing" in r["blockers"]
    assert r["invariants"]["deterministic_offline"] is False


def test_raw_query_invariant_is_derived_fails_closed():
    # The privacy invariant is DERIVED by VALUE-ALLOWLIST: each emitted field must
    # be a known router output, else it fails closed - complete and FP-free.
    capsule = mod.DomainCapsule.load("apiary")
    layers = mod._allowed_layers(capsule)
    dids = mod._allowed_decision_ids(capsule)

    def derive(dec):
        return mod._derive_raw_query_not_emitted([dec], layers, dids)

    base = {"id": "x", "layer": "model_based", "reason": "capsule_decision_match",
            "decision_id": "honey_yield", "fallback": "llm_reasoning"}
    assert derive(base) is True
    # None decision_id / None fallback are allowed.
    assert derive({**base, "decision_id": None, "fallback": None,
                   "reason": "keyword_classifier:seasonal"}) is True
    # Forged values in each field fail closed.
    assert derive({**base, "decision_id": "SECRETZTOKEN"}) is False
    assert derive({**base, "layer": "alpha"}) is False
    assert derive({**base, "fallback": "alpha"}) is False
    assert derive({**base, "reason": "does alice keep bees"}) is False
    # SHORT ALPHA token leak (#1267 gap): a 5-char name in decision_id is caught,
    # because the value-allowlist does not depend on token length.
    assert derive({**base, "decision_id": "alice"}) is False
    # reason is matched against the EXACT closed set: a forged keyword_classifier
    # suffix must NOT over-claim as known (the #1267 reason-suffix gap).
    assert derive({**base, "reason": "keyword_classifier:alice"}) is False
    assert derive({**base, "reason": "keyword_classifier:math"}) is True
    assert derive({**base, "reason": "capsule_decision_fallback"}) is True
    # emitted corpus id must be a safe identifier: a raw query (spaces) fails.
    assert derive({**base, "id": "how much honey yield"}) is False
    assert derive({**base, "id": "bee_qa_honey_yield"}) is True
    assert derive({**base, "id": ""}) is True  # empty id allowed


def test_empty_corpus_not_ok():
    r = mod.diagnose("apiary", [])
    assert r["ok"] is False
    assert "empty_corpus" in r["blockers"]
    assert r["all_deterministic"] is False
    assert r["deterministic_ratio"] == 0.0


def test_skips_rows_missing_query():
    r = mod.diagnose("apiary", SAMPLE + [{"id": "bad"}])
    assert r["corpus_size"] == len(SAMPLE)


def test_invariants_and_clean_summary():
    r = mod.diagnose("apiary", SAMPLE)
    inv = r["invariants"]
    for flag in ("deterministic_offline", "raw_query_not_emitted",
                 "volatile_timing_excluded", "no_superiority_claim",
                 "forbidden_vocabulary_clean"):
        assert inv[flag] is True
    # The JSON report no longer lists the forbidden terms, so the report itself
    # (not just the human summary) must be vocabulary-clean.
    assert "forbidden_vocabulary_excluded" not in inv
    mod.assert_vocabulary_clean(mod.render_summary(r))
    mod.assert_vocabulary_clean(json.dumps(r))


def test_token_injection_into_stable_field_fails_closed(monkeypatch):
    # Reviewer forge: a single raw-query TOKEN injected into a stable emitted
    # field (here `reason`) must flip the DERIVED privacy invariant False - not
    # just a whole-query leak. Constant return -> deterministic, isolating the
    # privacy blocker.
    def _leak(result):  # noqa: ANN001
        return {
            "layer": "model_based", "reason": "SECRETTOKEN42",
            "decision_id": "honey_yield", "fallback": "llm_reasoning",
            "model": "honey_yield", "confidence": 0.5, "rules": [],
        }

    monkeypatch.setattr(mod, "_stable_decision", _leak)
    r = mod.diagnose("apiary", [{"id": "x", "query": "alpha SECRETTOKEN42 omega"}])
    assert r["all_deterministic"] is True  # constant view -> deterministic
    assert r["invariants"]["raw_query_not_emitted"] is False
    assert "raw_query_emitted" in r["blockers"]
    assert r["ok"] is False


def test_allowed_sets_cover_real_corpus_outputs():
    # FP-free guarantee: every genuine router output over the real corpus is in
    # the value-allowlist, so the privacy derive never false-positives on real
    # data (even when a query word coincides with a route label / decision id).
    capsule = mod.DomainCapsule.load("apiary")
    layers = mod._allowed_layers(capsule)
    dids = mod._allowed_decision_ids(capsule)
    corpus = mod.load_corpus(REPO_ROOT / "configs" / "benchmarks.yaml")
    r = mod.diagnose("apiary", corpus)
    assert r["invariants"]["raw_query_not_emitted"] is True
    for dec in r["routing_decisions"]:
        assert dec["layer"] in layers
        assert dec["fallback"] is None or dec["fallback"] in layers
        assert dec["decision_id"] is None or dec["decision_id"] in dids
        assert mod._reason_is_known(dec["reason"])


def test_main_json_exit0():
    assert mod.main(["--json"]) == 0
