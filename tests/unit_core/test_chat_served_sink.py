# SPDX-License-Identifier: BUSL-1.1
"""Tests for the P2 S1b T3 serialized chat-served sink.

Covers: a sanity baseline, block-condition 3 (unique served_id + exactly-one-terminal),
rebuild-from-ledger (the derived state is a rebuildable cache), the receipts<=served
subset invariant, real multi-thread serialization under the sink lock, crash torn-tail
load tolerance, and refusal to load a chain-broken ledger.
"""
from __future__ import annotations

import threading

import pytest

from waggledance.core.magma import chat_served_ledger as L
from waggledance.core.magma.chat_served_sink import (
    ChatServedReceiptSink,
    LedgerStateError,
    ServedIdCollision,
    TerminalError,
)

_TS = "2026-07-04T07:00:00Z"
_DIGEST = "sha256:" + "ab" * 32
_META = {"route_type": "solver", "profile": "HOME"}


def _sink(tmp_path, **kw):
    return ChatServedReceiptSink(str(tmp_path / "ledger.jsonl"), **kw)


# --- sanity baseline -------------------------------------------------------------
def test_sanity_pending_then_receipt_and_gap(tmp_path) -> None:
    sink = _sink(tmp_path)
    sink.record_pending("q1", _TS, _META)
    sink.resolve_receipt("q1", _TS, _DIGEST)
    sink.record_pending("q2", _TS, _META)
    sink.resolve_gap("q2", _TS, "sink_write_failed")
    assert sink.counts() == {"served": 2, "receipts": 1, "gaps": 1, "pending_unresolved": 0}
    entries, torn = L.read_entries(str(tmp_path / "ledger.jsonl"))
    assert torn is False and L.verify_chain(entries).ok is True


def test_head_advances_and_matches_ledger(tmp_path) -> None:
    sink = _sink(tmp_path)
    h1 = sink.record_pending("q1", _TS, _META)
    h2 = sink.resolve_receipt("q1", _TS, _DIGEST)
    assert h1 != h2 and sink.head == h2
    entries, _ = L.read_entries(str(tmp_path / "ledger.jsonl"))
    assert L.head_hash(entries) == h2


# --- block-condition 3: unique served_id ----------------------------------------
def test_duplicate_served_id_is_collision(tmp_path) -> None:
    sink = _sink(tmp_path)
    sink.record_pending("q1", _TS, _META)
    with pytest.raises(ServedIdCollision):
        sink.record_pending("q1", _TS, _META)          # a second pending would mask a hole
    with pytest.raises(ServedIdCollision):
        sink.resolve_receipt("q1", _TS, _DIGEST)        # resolve first...
        sink.record_pending("q1", _TS, _META)           # ...then re-pending is still a collision


# --- block-condition 3: exactly one terminal ------------------------------------
def test_terminal_without_pending_is_error(tmp_path) -> None:
    sink = _sink(tmp_path)
    with pytest.raises(TerminalError):
        sink.resolve_receipt("ghost", _TS, _DIGEST)
    with pytest.raises(TerminalError):
        sink.resolve_gap("ghost", _TS, "sink_write_failed")


def test_second_terminal_is_refused(tmp_path) -> None:
    sink = _sink(tmp_path)
    sink.record_pending("q1", _TS, _META)
    sink.resolve_receipt("q1", _TS, _DIGEST)
    with pytest.raises(TerminalError):
        sink.resolve_receipt("q1", _TS, _DIGEST)        # double receipt
    with pytest.raises(TerminalError):
        sink.resolve_gap("q1", _TS, "sink_write_failed")  # receipt then gap = never both


def test_receipt_then_gap_and_gap_then_receipt_both_refused(tmp_path) -> None:
    sink = _sink(tmp_path)
    sink.record_pending("q1", _TS, _META)
    sink.resolve_gap("q1", _TS, "sink_write_failed")
    with pytest.raises(TerminalError):
        sink.resolve_receipt("q1", _TS, _DIGEST)        # gap then receipt = never both


# --- rebuild from ledger (derived state is a rebuildable cache) ------------------
def test_fresh_sink_rebuilds_state_from_ledger(tmp_path) -> None:
    sink = _sink(tmp_path)
    sink.record_pending("q1", _TS, _META)
    sink.resolve_receipt("q1", _TS, _DIGEST)
    sink.record_pending("q2", _TS, _META)               # left unresolved (pending)
    reborn = _sink(tmp_path)
    assert reborn.counts() == {"served": 2, "receipts": 1, "gaps": 0, "pending_unresolved": 1}
    assert reborn.head == sink.head
    # a rebuilt sink still enforces bc3 against the reloaded state
    with pytest.raises(ServedIdCollision):
        reborn.record_pending("q1", _TS, _META)
    with pytest.raises(TerminalError):
        reborn.resolve_receipt("q1", _TS, _DIGEST)      # q1 already terminal in the reloaded state


# --- receipts <= served (subset -> ratio <= 1 by construction, #1495) -----------
def test_receipts_never_exceed_served(tmp_path) -> None:
    sink = _sink(tmp_path)
    for i in range(5):
        sink.record_pending(f"q{i}", _TS, _META)
        if i % 2 == 0:
            sink.resolve_receipt(f"q{i}", _TS, _DIGEST)
    c = sink.counts()
    assert c["receipts"] <= c["served"]
    assert c["receipts"] + c["gaps"] + c["pending_unresolved"] == c["served"]


# --- real concurrency: the sink lock serializes writers -------------------------
def test_concurrent_record_pending_is_serialized(tmp_path) -> None:
    path = str(tmp_path / "ledger.jsonl")
    sink = ChatServedReceiptSink(path, fsync_every=0)   # no windowed fsync -> fast
    total = 60
    worker_count = 10
    ids = [f"q{i}" for i in range(total)]
    chunks = [ids[w::worker_count] for w in range(worker_count)]
    barrier = threading.Barrier(worker_count)
    errors: list[Exception] = []

    def work(my_ids: list[str]) -> None:
        try:
            barrier.wait(timeout=10)
        except threading.BrokenBarrierError:
            pass
        for sid in my_ids:
            try:
                sink.record_pending(sid, _TS, _META)
            except Exception as exc:  # noqa: BLE001 - surface any race
                errors.append(exc)

    threads = [threading.Thread(target=work, args=(c,)) for c in chunks]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    entries, torn = L.read_entries(path)
    assert torn is False
    assert L.verify_chain(entries).ok is True            # no interleaved/broken chain
    assert len(entries) == total
    assert sink.counts()["served"] == total
    assert ChatServedReceiptSink(path).counts()["served"] == total  # durable + rebuildable


# --- crash tolerance + corruption ----------------------------------------------
def test_sink_loads_ledger_with_torn_tail(tmp_path) -> None:
    path = str(tmp_path / "ledger.jsonl")
    L.append_entry(path, L.new_served_pending("q1", L.GENESIS_PREV_HASH, _TS, _META), fsync=False)
    with open(path, "ab") as handle:
        handle.write(b'{"partial": "torn crash tail')      # crash mid-append
    sink = ChatServedReceiptSink(path)
    assert sink.torn_tail_on_load is True
    assert sink.counts() == {"served": 1, "receipts": 0, "gaps": 0, "pending_unresolved": 1}


def test_sink_refuses_chain_broken_ledger(tmp_path) -> None:
    path = str(tmp_path / "ledger.jsonl")
    e1 = L.new_served_pending("q1", L.GENESIS_PREV_HASH, _TS, _META)
    L.append_entry(path, e1, fsync=False)
    # second entry links to GENESIS instead of e1's hash -> broken chain (append does not check linkage)
    e2_bad = L.new_served_pending("q2", L.GENESIS_PREV_HASH, _TS, _META)
    L.append_entry(path, e2_bad, fsync=False)
    with pytest.raises(LedgerStateError):
        ChatServedReceiptSink(path)


def test_flush_is_safe_and_idempotent(tmp_path) -> None:
    sink = _sink(tmp_path)
    sink.flush()                                          # empty ledger -> no-op, no error
    sink.record_pending("q1", _TS, _META)
    sink.flush()
    sink.flush()
    assert sink.counts()["served"] == 1


# --- loader replays the lifecycle state machine (lead T3 finding, dual-RCO concur) --
def _write_chain(path, specs):
    """Append chain-linked entries directly (bypassing the sink's live guards) to build
    a chain-VALID ledger with a chosen lifecycle -- so loader tests isolate the
    lifecycle check from the chain check."""
    prev = L.GENESIS_PREV_HASH
    for kind, sid in specs:
        if kind == "pending":
            entry = L.new_served_pending(sid, prev, _TS, _META)
        elif kind == "receipt":
            entry = L.new_receipt_terminal(sid, prev, _TS, _DIGEST)
        else:
            entry = L.new_gap_terminal(sid, prev, _TS, "sink_write_failed")
        L.append_entry(path, entry, fsync=False)
        prev = entry["entry_hash"]


def test_loader_rejects_duplicate_pending_same_served_id(tmp_path) -> None:
    # lead's exact repro: two linked served_pending(q1) verify chain-OK but the loader
    # must NOT collapse them to served=1 (that hides a collision across restart/import).
    path = str(tmp_path / "l.jsonl")
    _write_chain(path, [("pending", "q1"), ("pending", "q1")])
    assert L.verify_chain(L.read_entries(path)[0]).ok is True   # purely lifecycle-invalid
    with pytest.raises(LedgerStateError):
        ChatServedReceiptSink(path)


def test_loader_rejects_terminal_without_pending(tmp_path) -> None:
    path = str(tmp_path / "l.jsonl")
    _write_chain(path, [("receipt", "q1")])
    assert L.verify_chain(L.read_entries(path)[0]).ok is True
    with pytest.raises(LedgerStateError):
        ChatServedReceiptSink(path)


def test_loader_rejects_second_terminal(tmp_path) -> None:
    path = str(tmp_path / "l.jsonl")
    _write_chain(path, [("pending", "q1"), ("receipt", "q1"), ("gap", "q1")])
    assert L.verify_chain(L.read_entries(path)[0]).ok is True
    with pytest.raises(LedgerStateError):
        ChatServedReceiptSink(path)


def test_loader_accepts_valid_multi_query_lifecycle(tmp_path) -> None:
    # LIVENESS (both directions): a legitimate multi-query chain still loads, no over-reject.
    path = str(tmp_path / "l.jsonl")
    _write_chain(path, [
        ("pending", "q1"), ("receipt", "q1"),
        ("pending", "q2"), ("gap", "q2"),
        ("pending", "q3"),
    ])
    sink = ChatServedReceiptSink(path)
    assert sink.counts() == {"served": 3, "receipts": 1, "gaps": 1, "pending_unresolved": 1}
