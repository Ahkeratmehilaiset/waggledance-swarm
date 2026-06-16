"""Offline tests for tools/run_routing_misroute_diagnostic.py.

Diagnostic (not a safety gate): asserts structure, accuracy consistency, the
no-raw-query-leak guarantee, and forbidden-vocabulary cleanliness against the
in-repo apiary profile.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location(
    "run_routing_misroute_diagnostic",
    REPO_ROOT / "tools" / "run_routing_misroute_diagnostic.py",
)
mod = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(mod)  # type: ignore[union-attr]

# q1 carries a unique sentinel token that is NOT a capsule keyword, so it must
# never appear in the report (proves the raw query is not emitted).
SAMPLE = [
    {"id": "q1", "query": "how much honey yield xyzzysentinel this season",
     "expected_route": "model_based"},
    {"id": "q2", "query": "asdf qwerty zxcvsentinel", "expected_route": "llm_reasoning"},
    {"id": "q3", "query": "prepare the hive for winter", "expected_route": "retrieval"},
]


def test_diagnose_structure_and_accuracy_consistency():
    r = mod.diagnose("apiary", SAMPLE)
    assert r["ok"] is True
    assert r["corpus_size"] == len(SAMPLE)
    assert 0.0 <= r["overall_accuracy"] <= 1.0
    assert r["misroute_count"] == len(r["misroute_leads"])
    correct = round(r["overall_accuracy"] * r["corpus_size"])
    assert correct + r["misroute_count"] == r["corpus_size"]
    for lead in r["misroute_leads"]:
        assert lead["expected"] != lead["predicted"]
        assert "matched_keywords" in lead


def test_no_raw_query_leak():
    r = mod.diagnose("apiary", SAMPLE)
    blob = json.dumps(r, ensure_ascii=False)
    # The unique sentinel tokens (not capsule keywords) must never be emitted.
    assert "xyzzysentinel" not in blob
    assert "zxcvsentinel" not in blob
    assert r["invariants"]["raw_query_not_emitted"] is True


def test_numeric_query_token_redacted_not_emitted():
    # Regression for the #1263 privacy leak: the math keyword-classifier captures
    # raw query digits (\d+\s*[+\-*/] -> m.group(0), e.g. "9090901*"); that
    # query-derived token must be redacted, never reach the emitted report.
    sample = [{"id": "num", "query": "9090901*x", "expected_route": "retrieval"}]
    r = mod.diagnose("apiary", sample)
    blob = json.dumps(r, ensure_ascii=False)
    assert "9090901" not in blob  # raw query digits never emitted
    assert r["invariants"]["raw_query_not_emitted"] is True
    leads = r["misroute_leads"]
    assert len(leads) == 1  # it misrouted (model_based != retrieval)
    lead = leads[0]
    assert lead["reason"] == "keyword_classifier:math"
    assert lead["matched_keywords"] == []  # raw capture removed
    assert lead["redacted_query_derived_keyword_count"] >= 1


def test_capsule_declared_keywords_are_kept():
    # Capsule-side keywords (declared vocabulary) are safe and must survive
    # redaction so the diagnostic keeps its tuning value.
    sample = [{"id": "k", "query": "how much honey yield this season",
               "expected_route": "retrieval"}]
    r = mod.diagnose("apiary", sample)
    leads = r["misroute_leads"]
    assert len(leads) == 1
    assert set(leads[0]["matched_keywords"]) <= {"honey", "yield"}
    assert leads[0]["matched_keywords"]  # non-empty: capsule keywords kept
    assert leads[0]["redacted_query_derived_keyword_count"] == 0


def test_raw_query_invariant_is_derived_fails_closed():
    # The privacy invariant is DERIVED, not hardcoded: if a query-derived token
    # survived in matched_keywords (a future regression), it flips False.
    vocab = {"honey", "yield"}
    captured = {"9090901*"}
    leaked = [{"id": "x", "expected": "retrieval", "predicted": "model_based",
               "reason": "keyword_classifier:math", "decision_id": None,
               "matched_keywords": ["9090901*"],
               "redacted_query_derived_keyword_count": 0}]
    assert mod._derive_raw_query_not_emitted(leaked, {}, vocab, captured) is False
    clean = [{**leaked[0], "matched_keywords": [],
              "redacted_query_derived_keyword_count": 1}]
    assert mod._derive_raw_query_not_emitted(clean, {}, vocab, captured) is True


def test_empty_corpus_not_ok():
    r = mod.diagnose("apiary", [])
    assert r["ok"] is False
    assert r["misroute_count"] == 0


def test_skips_rows_missing_fields():
    r = mod.diagnose("apiary", SAMPLE + [{"id": "bad", "query": ""}])
    assert r["corpus_size"] == len(SAMPLE)


def test_invariants_and_clean_summary():
    r = mod.diagnose("apiary", SAMPLE)
    inv = r["invariants"]
    for flag in ("deterministic_offline", "raw_query_not_emitted",
                 "tuning_leads_not_defects", "no_superiority_claim"):
        assert inv[flag] is True
    assert list(inv["forbidden_vocabulary_excluded"]) == list(mod.FORBIDDEN_VOCABULARY)
    summary = mod.render_summary(r)
    mod.assert_vocabulary_clean(summary)


def test_main_json_exit0():
    assert mod.main(["--json"]) == 0
