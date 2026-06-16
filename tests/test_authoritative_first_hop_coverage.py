"""Offline tests for tools/run_authoritative_first_hop_coverage.py.

Diagnostic (not a safety gate): asserts structure, coverage consistency,
provenance-not-destination classification, fail-closed measurement availability,
and the no-raw-query-leak guarantee (DERIVED, never hardcoded).
"""
from __future__ import annotations

import importlib.util
import json
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location(
    "run_authoritative_first_hop_coverage",
    REPO_ROOT / "tools" / "run_authoritative_first_hop_coverage.py",
)
mod = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(mod)  # type: ignore[union-attr]

# Numeric (\d+<op><wordchar>) AND alpha sentinels: neither is a capsule keyword,
# so neither may ever appear in the emitted report.
SAMPLE = [
    {"id": "q1", "query": "how much honey yield 9090901*xyzzy this season",
     "expected_route": "model_based"},
    {"id": "q2", "query": "asdf qwerty zxcvsentinel 50+plus", "expected_route": "llm_reasoning"},
    {"id": "q3", "query": "prepare the hive for winter", "expected_route": "retrieval"},
    {"id": "q4", "query": "what is a brood box", "expected_route": "retrieval"},
]


def test_structure_and_coverage_consistency():
    r = mod.diagnose("apiary", SAMPLE)
    assert r["corpus_size"] == len(SAMPLE)
    assert r["routable_size"] == r["corpus_size"] - r["hot_cache_count"]
    assert (r["authoritative_first_hop_count"] + r["heuristic_first_hop_count"]
            == r["routable_size"])
    cov = r["authoritative_first_hop_coverage"]
    if r["coverage_measurement_available"]:
        assert cov is not None and 0.0 <= cov <= 1.0
        assert r["ok"] is True
    # records carry ONLY allowlisted keys (no raw query field can ride along)
    for rec in r["first_hop_records"]:
        assert set(rec.keys()) <= mod._RECORD_KEYS


def test_classify_provenance_not_destination():
    # authoritative reasons
    for reason in ("capsule_decision_match", "capsule_decision_fallback",
                   "capsule_priority_fallback"):
        assert mod._classify_first_hop(reason) == "authoritative"
    # heuristic: keyword classifier counts NON-authoritative regardless of layer
    for reason in ("keyword_classifier:math", "keyword_classifier:seasonal",
                   "keyword_classifier:retrieval"):
        assert mod._classify_first_hop(reason) == "heuristic"
    # cached excluded
    assert mod._classify_first_hop("hot_cache_hit") == "cached"


def test_empty_corpus_unavailable_and_not_ok():
    r = mod.diagnose("apiary", [])
    assert r["ok"] is False
    assert r["coverage_measurement_available"] is False
    assert r["authoritative_first_hop_coverage"] is None
    assert r["routable_size"] == 0


def test_skips_rows_missing_fields():
    r = mod.diagnose("apiary", SAMPLE + [{"id": "bad", "query": ""},
                                         {"id": "bad2", "expected_route": "retrieval"}])
    assert r["corpus_size"] == len(SAMPLE)


def test_no_raw_query_leak():
    r = mod.diagnose("apiary", SAMPLE)
    blob = json.dumps(r, ensure_ascii=False)
    # numeric AND alpha sentinels (raw query content, not capsule vocab) absent
    for sentinel in ("9090901", "xyzzy", "zxcvsentinel", "50+plus"):
        assert sentinel not in blob
    assert r["invariants"]["raw_query_not_emitted"] is True


def test_full_corpus_raw_query_invariant_stays_clean():
    corpus = mod.load_corpus(REPO_ROOT / "configs" / "benchmarks.yaml")
    r = mod.diagnose("apiary", corpus)
    assert r["invariants"]["raw_query_not_emitted"] is True


def test_derive_raw_query_not_emitted_fails_closed():
    # The invariant must be DERIVED, not hardcoded: if a whole raw query survives
    # anywhere in the emitted structures, the derive flips False.
    queries = ["how much honey can i expect from one hive this season"]
    clean_records = [{"id": "q1", "expected": "model_based", "predicted": "retrieval",
                      "reason": "keyword_classifier:retrieval",
                      "first_hop_class": "heuristic", "decision_id": "d1"}]
    allowed_decisions = {"d1"}
    assert mod._derive_raw_query_not_emitted(
        clean_records, {}, queries, allowed_decisions) is True
    # forge a leak: a record field carries the raw query verbatim
    leaked = [dict(clean_records[0], decision_id=queries[0])]
    assert mod._derive_raw_query_not_emitted(
        leaked, {}, queries, allowed_decisions) is False
    # leak via per_route_summary too
    assert mod._derive_raw_query_not_emitted(
        clean_records, {"model_based": {"note": queries[0]}}, queries,
        allowed_decisions) is False


def test_derive_raw_query_not_emitted_rejects_partial_token_leak():
    queries = ["alpha SECRETZTOKEN omega", "how much honey 9090901*xyzzy"]
    clean_records = [{"id": "q1", "expected": "model_based", "predicted": "retrieval",
                      "reason": "keyword_classifier:retrieval",
                      "first_hop_class": "heuristic", "decision_id": "d1"}]
    allowed_decisions = {"d1"}
    assert mod._derive_raw_query_not_emitted(
        clean_records, {}, queries, allowed_decisions) is True
    assert mod._derive_raw_query_not_emitted(
        [dict(clean_records[0], decision_id="SECRETZTOKEN")], {}, queries,
        allowed_decisions) is False
    assert mod._derive_raw_query_not_emitted(
        [dict(clean_records[0], reason="token 9090901 leaked")], {}, queries,
        allowed_decisions) is False
    assert mod._derive_raw_query_not_emitted(
        [dict(clean_records[0], reason="token 9090901*xyzzy leaked")], {}, queries,
        allowed_decisions
    ) is False


def test_derive_raw_query_not_emitted_rejects_short_alpha_freeform_values():
    queries = ["does alice keep bees", "where are the hunters today"]
    clean_records = [{"id": "q1", "expected": "model_based", "predicted": "retrieval",
                      "reason": "keyword_classifier:retrieval",
                      "first_hop_class": "heuristic", "decision_id": "honey_yield"}]
    assert mod._derive_raw_query_not_emitted(
        clean_records, {}, queries, {"honey_yield"}) is True
    assert mod._derive_raw_query_not_emitted(
        [dict(clean_records[0], decision_id="alice")], {}, queries,
        {"honey_yield"}) is False
    assert mod._derive_raw_query_not_emitted(
        [dict(clean_records[0], reason="route hunters")], {}, queries,
        {"honey_yield"}) is False


def test_no_declared_order_capsule_unavailable():
    # A capsule with no key_decisions and no priority layers cannot be measured.
    stub = types.SimpleNamespace(key_decisions=[], get_layers_by_priority=lambda: [])
    assert mod._capsule_declares_authoritative_order(stub) is False
    has_decisions = types.SimpleNamespace(
        key_decisions=[{"keywords": ["x"]}], get_layers_by_priority=lambda: [])
    assert mod._capsule_declares_authoritative_order(has_decisions) is True


def test_all_cached_corpus_unavailable(monkeypatch):
    # Every first hop served from hot cache -> routable 0 -> not measurable
    # (no div-by-zero, NOT trivially 100%).
    def _fake_route(self, query):  # noqa: ANN001
        return types.SimpleNamespace(
            layer="model_based", reason="hot_cache_hit", decision_id="")
    monkeypatch.setattr(mod.SmartRouterV2, "route", _fake_route)
    r = mod.diagnose("apiary", SAMPLE)
    assert r["hot_cache_count"] == r["corpus_size"]
    assert r["routable_size"] == 0
    assert r["coverage_measurement_available"] is False
    assert r["ok"] is False
    assert r["authoritative_first_hop_coverage"] is None


def test_vocabulary_clean_summary():
    r = mod.diagnose("apiary", SAMPLE)
    mod.assert_vocabulary_clean(mod.render_summary(r))


def test_main_json_exit_ok():
    # apiary declares order + corpus is routable -> measurement available -> exit 0
    assert mod.main(["--json"]) == 0
