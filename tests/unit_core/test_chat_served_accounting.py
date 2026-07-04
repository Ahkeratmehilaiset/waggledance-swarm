# SPDX-License-Identifier: BUSL-1.1
"""Tests for the P2 S1b T4 read-only claim-coverage accounting.

Covers: eligibility only when EVERY served query has a receipt on an intact ledger,
the receipts<=served bounded ratio (#1495), fail-closed on chain/lifecycle defects,
honest not-eligible reasons, and a CROSS-CHECK that the read-side walk agrees with
the T3 sink's derived counts (anti-drift between writer and reader).
"""
from __future__ import annotations

import json

import pytest

from waggledance.core.magma import chat_served_ledger as L
from waggledance.core.magma.chat_served_accounting import (
    PENDING_APPEND_FAILURE_SCHEMA,
    coverage_from_ledger,
    derive_coverage,
    read_pending_append_failures,
    valid_pending_append_failure,
)
from waggledance.core.magma.chat_served_sink import ChatServedReceiptSink

_TS = "2026-07-04T07:00:00Z"
_DIGEST = "sha256:" + "ab" * 32
_META = {"route_type": "solver"}


def _chain(specs):
    """Build chain-linked ledger entries (kind, served_id) from genesis."""
    entries = []
    prev = L.GENESIS_PREV_HASH
    for kind, sid in specs:
        if kind == "pending":
            entry = L.new_served_pending(sid, prev, _TS, _META)
        elif kind == "receipt":
            entry = L.new_receipt_terminal(sid, prev, _TS, _DIGEST)
        else:
            entry = L.new_gap_terminal(sid, prev, _TS, "sink_write_failed")
        entries.append(entry)
        prev = entry["entry_hash"]
    return entries


# --- eligibility -----------------------------------------------------------------
def test_all_receipts_is_eligible_ratio_one() -> None:
    report = derive_coverage(_chain([
        ("pending", "q1"), ("receipt", "q1"),
        ("pending", "q2"), ("receipt", "q2"),
    ]))
    assert report.eligible is True and report.reason is None
    assert report.served == 2 and report.receipts == 2
    assert report.ratio == 1.0 and report.gaps == 0 and report.unresolved_pending == 0


def test_a_gap_makes_ineligible() -> None:
    report = derive_coverage(_chain([
        ("pending", "q1"), ("receipt", "q1"),
        ("pending", "q2"), ("gap", "q2"),
    ]))
    assert report.eligible is False and report.reason == "gaps_present"
    assert report.ratio == 0.5 and report.gaps == 1


def test_pending_append_failure_extends_denominator_and_fails_closed() -> None:
    report = derive_coverage(
        _chain([("pending", "q1"), ("receipt", "q1")]),
        pending_append_failures=1,
    )
    assert report.served == 2
    assert report.receipts == 1
    assert report.gaps == 1
    assert report.pending_append_failures == 1
    assert report.ratio == 0.5
    assert report.eligible is False
    assert report.reason == "pending_append_failures"


def test_pending_append_failure_validator_rejects_raw_or_nested_metadata() -> None:
    base = {
        "schema_version": PENDING_APPEND_FAILURE_SCHEMA,
        "served_id_hash": "sha256:" + "ab" * 32,
        "ts_utc": _TS,
        "reason": "sink_write_failed",
        "metadata": {"route_type": "solver", "profile": "HOME"},
    }
    assert valid_pending_append_failure(base) is True

    raw = dict(base)
    raw["metadata"] = {"profile": "RAW SECRET USER QUERY with spaces"}
    assert valid_pending_append_failure(raw) is False

    nested = dict(base)
    nested["metadata"] = {"route_type": "solver", "nested": {"raw": "X"}}
    assert valid_pending_append_failure(nested) is False


def test_read_pending_append_failures_counts_invalid_and_corrupt_lines(tmp_path) -> None:
    path = tmp_path / "pending_append_failures.jsonl"
    valid = {
        "schema_version": PENDING_APPEND_FAILURE_SCHEMA,
        "served_id_hash": "sha256:" + "ab" * 32,
        "ts_utc": _TS,
        "reason": "sink_write_failed",
        "metadata": {"route_type": "solver"},
    }
    invalid = dict(valid)
    invalid["metadata"] = {"profile": "RAW SECRET USER QUERY with spaces"}
    path.write_text(
        "\n".join([json.dumps(valid), json.dumps(invalid), '{"partial":'])
        + "\n",
        encoding="utf-8",
    )

    assert read_pending_append_failures(str(path)) == 3


def test_unresolved_pending_makes_ineligible() -> None:
    report = derive_coverage(_chain([("pending", "q1"), ("receipt", "q1"), ("pending", "q2")]))
    assert report.eligible is False and report.reason == "unresolved_pending"
    assert report.unresolved_pending == 1 and report.ratio == 0.5


def test_empty_ledger_is_ineligible_no_served() -> None:
    report = derive_coverage([])
    assert report.eligible is False and report.reason == "no_served_queries"
    assert report.served == 0 and report.ratio is None


# --- bounded ratio (#1495): numerator subset of denominator ---------------------
def test_ratio_never_exceeds_one() -> None:
    report = derive_coverage(_chain([
        ("pending", "q1"), ("receipt", "q1"),
        ("pending", "q2"), ("gap", "q2"),
        ("pending", "q3"),
    ]))
    assert report.receipts <= report.served
    assert report.ratio is not None and report.ratio <= 1.0
    assert report.receipts + report.gaps + report.unresolved_pending == report.served


# --- fail-closed: the accounting ENFORCES (does not trust a correct writer) ------
def test_chain_broken_is_ineligible() -> None:
    # second entry links to GENESIS instead of the first entry's hash
    e1 = L.new_served_pending("q1", L.GENESIS_PREV_HASH, _TS, _META)
    e2 = L.new_receipt_terminal("q1", L.GENESIS_PREV_HASH, _TS, _DIGEST)  # wrong prev
    report = derive_coverage([e1, e2])
    assert report.eligible is False and report.reason.startswith("chain_invalid")
    assert report.chain_ok is False


@pytest.mark.parametrize("specs,violation", [
    ([("pending", "q1"), ("pending", "q1")], "duplicate_pending"),
    ([("receipt", "q1")], "terminal_without_pending"),
    ([("pending", "q1"), ("receipt", "q1"), ("gap", "q1")], "second_terminal"),
])
def test_lifecycle_invalid_is_ineligible(specs, violation) -> None:
    entries = _chain(specs)
    assert L.verify_chain(entries).ok is True          # chain is fine; purely lifecycle-invalid
    report = derive_coverage(entries)
    assert report.eligible is False
    assert report.reason == f"lifecycle_invalid:{violation}"
    assert report.lifecycle_ok is False


# --- cross-check with the T3 sink (anti-drift between writer and reader) ---------
def test_accounting_matches_sink_counts(tmp_path) -> None:
    path = str(tmp_path / "ledger.jsonl")
    sink = ChatServedReceiptSink(path, fsync_every=0)
    sink.record_pending("q1", _TS, _META); sink.resolve_receipt("q1", _TS, _DIGEST)
    sink.record_pending("q2", _TS, _META); sink.resolve_gap("q2", _TS, "sink_write_failed")
    sink.record_pending("q3", _TS, _META)  # unresolved
    sink_counts = sink.counts()
    report = coverage_from_ledger(path)
    assert report.served == sink_counts["served"]
    assert report.receipts == sink_counts["receipts"]
    assert report.gaps == sink_counts["gaps"]
    assert report.unresolved_pending == sink_counts["pending_unresolved"]
    assert report.eligible is False and report.reason == "unresolved_pending"


def test_coverage_from_ledger_torn_tail_is_ineligible(tmp_path) -> None:
    path = str(tmp_path / "ledger.jsonl")
    L.append_entry(path, L.new_served_pending("q1", L.GENESIS_PREV_HASH, _TS, _META), fsync=False)
    with open(path, "ab") as handle:
        handle.write(b'{"partial": "torn')             # crash torn tail -> q1 stays unresolved
    report = coverage_from_ledger(path)
    assert report.served == 1 and report.unresolved_pending == 1
    assert report.eligible is False and report.reason == "unresolved_pending"
