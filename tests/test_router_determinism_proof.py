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
    # The privacy invariant is DERIVED, not hardcoded: a raw query surfacing in
    # any emitted field flips it False; a clean report leaves it True.
    leaked = [{"id": "x", "reason": "secret query text here"}]
    assert mod._derive_raw_query_not_emitted(
        leaked, [], ["secret query text here"]
    ) is False
    clean = [{"id": "x", "reason": "capsule_decision_match"}]
    assert mod._derive_raw_query_not_emitted(
        clean, [], ["secret query text here"]
    ) is True


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
                 "volatile_timing_excluded", "no_superiority_claim"):
        assert inv[flag] is True
    assert list(inv["forbidden_vocabulary_excluded"]) == list(mod.FORBIDDEN_VOCABULARY)
    summary = mod.render_summary(r)
    mod.assert_vocabulary_clean(summary)


def test_main_json_exit0():
    assert mod.main(["--json"]) == 0
