# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import importlib.util
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "run_chat_first_hop_corpus",
    ROOT / "tools" / "run_chat_first_hop_corpus.py",
)
mod = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(mod)  # type: ignore[union-attr]

SAMPLE = [
    {
        "id": "solver_math_percent",
        "query": "what is 15% of 300",
        "profile": "HOME",
    },
    {
        "id": "stats_solver_fallback",
        "query": "statistics summary for hive readings SECRET-W1B-STATS-12345",
        "profile": "HOME",
    },
    {
        "id": "general_llm",
        "query": "explain routine hive care SECRET-W1B-LLM-67890",
        "profile": "HOME",
    },
    {
        "id": "cached_excluded",
        "query": "what is varroa SECRET-W1B-CACHE-11111",
        "profile": "HOME",
        "cached_response": "cached varroa answer",
    },
]


def test_diagnose_counts_non_cached_chatservice_first_hops() -> None:
    report = mod.diagnose(SAMPLE)

    assert report["ok"] is True
    assert report["served_query_count"] == 4
    assert report["cached_count"] == 1
    assert report["non_cached_served_first_hop_count"] == 3
    assert report["route_decision_counts"] == {
        "solver_first": 1,
        "fallback": 2,
        "refused": 0,
    }
    assert report["first_hop_counts"] == {
        "authoritative": 1,
        "heuristic": 1,
        "fallback": 1,
        "refused": 0,
        "gap": 0,
    }


def test_records_are_w1a_schema_compatible_and_privacy_safe() -> None:
    report = mod.diagnose(SAMPLE)
    blob = json.dumps(report, sort_keys=True)

    assert report["invariants"]["raw_query_not_emitted"] is True
    assert report["invariants"]["records_allowlisted"] is True
    assert report["claim_safe"] is False
    assert report["runtime_authority_granted"] is False
    assert report["external_writes_applied"] is False
    for marker in ("SECRET-W1B-STATS", "SECRET-W1B-LLM", "SECRET-W1B-CACHE"):
        assert marker not in blob
    for record in report["first_hop_records"]:
        assert set(record) <= mod.SAFE_RECORD_KEYS
        assert record["route_decision"] in mod.ROUTE_DECISIONS
        assert record["first_hop_class"] in mod.FIRST_HOP_CLASSES
        assert record["query_digest"].startswith("sha256:")
        assert record["candidate_receipt_ref"].startswith("sha256:")
        assert isinstance(record["emitted_at_seq"], int)


def test_query_digest_is_domain_separated_and_normalization_versioned() -> None:
    normalized = "Caf\u00e9 Hive"
    expected = "sha256:" + hashlib.sha256(
        b"waggledance.chat_query_digest\x00"
        + mod.QUERY_NORMALIZATION_VERSION.encode("utf-8")
        + b"\x00"
        + normalized.encode("utf-8")
    ).hexdigest()

    assert mod._query_digest("  Cafe\u0301\tHive  ") == expected
    assert mod._query_digest(normalized) == expected
    assert mod._query_digest("Caf\u00e9 Hives") != expected
    assert mod._query_digest(normalized) != mod.sha256_digest({"query": normalized})


def test_report_binds_measurement_run_to_head_corpus_and_schema() -> None:
    report = mod.diagnose(SAMPLE)
    expected_head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    assert report["measurement_run"] == {
        "head_commit_sha": expected_head,
        "corpus_digest": mod._corpus_digest(SAMPLE),
        "schema_version": mod.SCHEMA_VERSION,
        "normalization_version": mod.QUERY_NORMALIZATION_VERSION,
        "run_id": mod._run_id(expected_head, mod._corpus_digest(SAMPLE)),
    }
    assert len(report["measurement_run"]["head_commit_sha"]) == 40
    assert report["measurement_run"]["corpus_digest"].startswith("sha256:")
    assert report["measurement_run"]["run_id"].startswith("sha256:")
    assert report["measurement_not_a_correctness_gate"] is True
    assert report["invariants"]["measurement_header_bound"] is True
    assert report["invariants"]["measurement_not_a_correctness_gate"] is True


def test_cached_rows_are_excluded_from_denominator_but_counted() -> None:
    report = mod.diagnose([SAMPLE[-1]])

    assert report["cached_count"] == 1
    assert report["non_cached_served_first_hop_count"] == 0
    assert report["measurement_available"] is False
    assert report["first_hop_records"] == []
    assert report["ok"] is False


def test_empty_corpus_is_fail_closed_unavailable() -> None:
    report = mod.diagnose([])

    assert report["served_query_count"] == 0
    assert report["measurement_available"] is False
    assert report["ok"] is False


def test_main_json_exits_zero_for_default_corpus(capsys) -> None:
    assert mod.main(["--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["report_version"] == mod.REPORT_VERSION
    assert payload["measurement_available"] is True
    assert payload["claim_safe"] is False
