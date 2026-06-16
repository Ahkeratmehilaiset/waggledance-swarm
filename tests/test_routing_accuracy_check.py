"""Offline tests for tools/run_routing_accuracy_check.py.

Uses a tiny in-line corpus + the in-repo `apiary` profile (deterministic,
offline). Asserts structure + the release-gate invariants, not absolute
accuracy (which depends on capsule/corpus alignment).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location(
    "routing_accuracy_check",
    REPO_ROOT / "tools" / "run_routing_accuracy_check.py",
)
mod = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(mod)  # type: ignore[union-attr]

SAMPLE = [
    {"id": "q1", "query": "how much honey this season", "expected_route": "model_based"},
    {"id": "q2", "query": "asdf qwerty zxcv", "expected_route": "llm_reasoning"},
    {"id": "q3", "query": "prepare the hive for winter", "expected_route": "retrieval"},
]


def test_evaluate_structure_and_consistency():
    r = mod.evaluate("apiary", SAMPLE)
    assert r["total"] == len(SAMPLE)
    assert 0.0 <= r["overall_accuracy"] <= 1.0
    assert 0.0 <= r["expensive_path_rate"] <= 1.0
    assert isinstance(r["per_expected_route"], dict)
    # correct count (from accuracy) + mismatches == total
    correct = round(r["overall_accuracy"] * r["total"])
    assert correct + len(r["mismatches"]) == r["total"]
    # each per-route bucket is internally consistent
    for bucket in r["per_expected_route"].values():
        assert 0 <= bucket["correct"] <= bucket["total"]


def test_skips_rows_missing_fields():
    r = mod.evaluate("apiary", SAMPLE + [{"id": "bad", "query": ""}])
    assert r["total"] == len(SAMPLE)  # the empty-query row is skipped


def test_envelope_invariants_and_clean_summary():
    r = mod.evaluate("apiary", SAMPLE)
    env = mod.build_envelope(r, corpus_size=len(SAMPLE))
    inv = env["invariants"]
    assert inv["no_cloud_api_calls_this_session"] is True
    assert inv["deterministic_offline"] is True
    assert inv["no_superiority_claim"] is True
    assert list(inv["forbidden_vocabulary_excluded"]) == list(mod.FORBIDDEN_VOCABULARY)

    summary = mod.render_summary(env)
    mod.assert_vocabulary_clean(summary)
    low = summary.lower()
    for phrase in mod.FORBIDDEN_VOCABULARY:
        assert phrase.lower() not in low
