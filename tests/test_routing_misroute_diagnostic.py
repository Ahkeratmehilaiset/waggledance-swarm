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
