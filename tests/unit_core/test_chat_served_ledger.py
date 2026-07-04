# SPDX-License-Identifier: BUSL-1.1
"""Tests for the P2 S1b T2 durable served->{receipt|gap} ledger.

Covers: a valid SANITY BASELINE first (so a later "rejected" is never just a harness
call error), chain integrity + tamper/dropped-entry detection (the VIOLATING cases,
#1495/#1496), a shape/range/non-scalar rejection sweep on every builder input, durable
append/read round-trips, and crash torn-tail tolerance vs mid-file corruption.
"""
from __future__ import annotations

import json

import pytest

from waggledance.core.magma import chat_served_ledger as L


# --- helpers ---------------------------------------------------------------------
def _pending(served_id="q1", prev=L.GENESIS_PREV_HASH, ts="2026-07-04T07:00:00Z", metadata=None):
    return L.new_served_pending(served_id, prev, ts, metadata or {"route_type": "solver", "profile": "HOME"})


_A_DIGEST = "sha256:" + "ab" * 32  # 64 hex chars


# --- sanity baseline (run FIRST: prove the happy path builds + chains) -----------
def test_sanity_baseline_pending_receipt_pending_gap_chains() -> None:
    e1 = _pending("q1")
    e2 = L.new_receipt_terminal("q1", L.head_hash([e1]), "2026-07-04T07:00:01Z", _A_DIGEST)
    e3 = _pending("q2", prev=L.head_hash([e1, e2]))
    e4 = L.new_gap_terminal("q2", L.head_hash([e1, e2, e3]), "2026-07-04T07:00:02Z", "sink_write_failed")
    chain = [e1, e2, e3, e4]
    assert L.verify_chain(chain).ok is True
    assert L.head_hash(chain) == e4["entry_hash"]
    # every entry is self-consistent and typed
    for e in chain:
        assert L.verify_entry_self(e) is True
        assert e["entry_type"] in L.ENTRY_TYPES
        assert e["payload_version"] == L.PAYLOAD_VERSION


def test_head_hash_of_empty_is_genesis() -> None:
    assert L.head_hash([]) == L.GENESIS_PREV_HASH


# --- hash validator: shape/range/charset (digest-wellformed discipline) ----------
@pytest.mark.parametrize("good", ["sha256:" + "0" * 64, "sha256:" + "ab" * 32, "sha256:" + "f" * 64])
def test_is_ledger_hash_accepts_wellformed(good: str) -> None:
    assert L.is_ledger_hash(good) is True


@pytest.mark.parametrize("bad", [
    "not-a-hash",
    "sha256:" + "0" * 63,        # too short
    "sha256:" + "0" * 65,        # too long
    "sha256:" + "AB" * 32,       # uppercase hex -> reject (charset)
    "sha256:" + "gg" * 32,       # non-hex charset
    "md5:" + "0" * 64,           # wrong prefix
    "0" * 64,                    # no prefix
    12345, None, b"sha256:" + b"0" * 64,
])
def test_is_ledger_hash_rejects_malformed(bad: object) -> None:
    assert L.is_ledger_hash(bad) is False


# --- tamper detection (the VIOLATING case) ---------------------------------------
def test_tamper_any_field_breaks_self_hash_and_chain() -> None:
    e1 = _pending("q1")
    e2 = L.new_receipt_terminal("q1", L.head_hash([e1]), "2026-07-04T07:00:01Z", _A_DIGEST)
    assert L.verify_chain([e1, e2]).ok is True
    # mutate a field WITHOUT recomputing entry_hash -> self-hash + chain must fail
    tampered = dict(e1)
    tampered["served_id"] = "q1-EVIL"
    assert L.verify_entry_self(tampered) is False
    res = L.verify_chain([tampered, e2])
    assert res.ok is False and res.broken_at == 0 and res.reason == "entry_hash_mismatch"


def test_dropped_middle_entry_breaks_linkage() -> None:
    e1 = _pending("q1")
    e2 = L.new_receipt_terminal("q1", L.head_hash([e1]), "2026-07-04T07:00:01Z", _A_DIGEST)
    e3 = _pending("q2", prev=L.head_hash([e1, e2]))
    # drop e2 -> e3.prev no longer matches e1.hash: a gapless-looking chain with a hole is impossible
    res = L.verify_chain([e1, e3])
    assert res.ok is False and res.broken_at == 1 and res.reason == "prev_hash_broken"


def test_unknown_entry_type_breaks_chain() -> None:
    e1 = _pending("q1")
    bad = dict(e1)
    bad["entry_type"] = "not_a_type"
    bad["entry_hash"] = L.compute_entry_hash(bad)  # re-hash so self-hash passes
    res = L.verify_chain([bad])
    assert res.ok is False and res.reason == "unknown_entry_type"


# --- builder rejection sweep: served_id / ts / prev (shape) -----------------------
@pytest.mark.parametrize("bad_id", ["has space", "bad\nid", "", "x" * 200, 5, None, {"nested": 1}])
def test_pending_rejects_malformed_served_id(bad_id: object) -> None:
    with pytest.raises(L.LedgerError):
        L.new_served_pending(bad_id, L.GENESIS_PREV_HASH, "2026-07-04T07:00:00Z", {"a": "b"})


@pytest.mark.parametrize("bad_prev", ["not-a-hash", "sha256:" + "0" * 63, "sha256:" + "AB" * 32, "", None, 7])
def test_pending_rejects_malformed_prev_hash(bad_prev: object) -> None:
    with pytest.raises(L.LedgerError):
        L.new_served_pending("q1", bad_prev, "2026-07-04T07:00:00Z", {"a": "b"})


def test_pending_rejects_nonconforming_ts() -> None:
    with pytest.raises(L.LedgerError):
        L.new_served_pending("q1", L.GENESIS_PREV_HASH, "2026-07-04 07:00:00", {"a": "b"})  # space -> not a token


# --- builder rejection sweep: metadata (non-scalar is the #1496 blind spot) -------
def test_metadata_rejects_non_mapping() -> None:
    with pytest.raises(L.LedgerError):
        L.new_served_pending("q1", L.GENESIS_PREV_HASH, "2026-07-04T07:00:00Z", ["not", "a", "map"])


@pytest.mark.parametrize("bad_value", [
    {"nested": "dict"},   # NON-SCALAR -> must be dropped/rejected, never str()'d in
    ["a", "list"],        # non-scalar
    123,                  # non-str scalar
    None,                 # None value
    "has space",          # non-conforming str
    "x" * 200,            # over-length
])
def test_metadata_rejects_nonconforming_or_nonscalar_value(bad_value: object) -> None:
    with pytest.raises(L.LedgerError):
        L.new_served_pending("q1", L.GENESIS_PREV_HASH, "2026-07-04T07:00:00Z", {"k": bad_value})
    # and prove the raw value never leaked into any accepted entry text
    # (rejection is the only safe outcome; there is no str() fallback)


def test_metadata_rejects_nonconforming_key() -> None:
    with pytest.raises(L.LedgerError):
        L.new_served_pending("q1", L.GENESIS_PREV_HASH, "2026-07-04T07:00:00Z", {"bad key": "v"})


# --- builder rejection sweep: receipt_ref / gap_reason ----------------------------
@pytest.mark.parametrize("bad_ref", ["not-a-digest", "sha256:" + "0" * 63, "sha256:" + "GG" * 32, None])
def test_receipt_terminal_rejects_malformed_ref(bad_ref: object) -> None:
    with pytest.raises(L.LedgerError):
        L.new_receipt_terminal("q1", L.GENESIS_PREV_HASH, "2026-07-04T07:00:01Z", bad_ref)


@pytest.mark.parametrize("bad_reason", ["arbitrary text", "SINK_WRITE_FAILED", "", None, "unknown_reason"])
def test_gap_terminal_rejects_reason_outside_fixed_set(bad_reason: object) -> None:
    with pytest.raises(L.LedgerError):
        L.new_gap_terminal("q1", L.GENESIS_PREV_HASH, "2026-07-04T07:00:02Z", bad_reason)


def test_gap_terminal_accepts_every_declared_reason() -> None:
    for reason in L.GAP_REASONS:
        e = L.new_gap_terminal("q1", L.GENESIS_PREV_HASH, "2026-07-04T07:00:02Z", reason)
        assert L.verify_entry_self(e) is True and e["gap_reason"] == reason


# --- durable append + read round-trip --------------------------------------------
def test_append_read_roundtrip_and_head(tmp_path) -> None:
    path = str(tmp_path / "ledger.jsonl")
    e1 = _pending("q1")
    h1 = L.append_entry(path, e1, fsync=False)
    e2 = L.new_receipt_terminal("q1", h1, "2026-07-04T07:00:01Z", _A_DIGEST)
    h2 = L.append_entry(path, e2, fsync=False)
    entries, torn = L.read_entries(path)
    assert torn is False
    assert len(entries) == 2
    assert L.verify_chain(entries).ok is True
    assert L.head_hash(entries) == h2 == e2["entry_hash"]


def test_append_refuses_entry_with_mismatched_hash(tmp_path) -> None:
    path = str(tmp_path / "ledger.jsonl")
    e1 = _pending("q1")
    e1["served_id"] = "tampered-after-build"  # entry_hash no longer matches
    with pytest.raises(L.LedgerError):
        L.append_entry(path, e1, fsync=False)


def test_read_missing_file_is_empty(tmp_path) -> None:
    entries, torn = L.read_entries(str(tmp_path / "does-not-exist.jsonl"))
    assert entries == [] and torn is False


# --- crash tolerance: torn tail vs mid-file corruption ---------------------------
def test_torn_final_line_is_tolerated(tmp_path) -> None:
    path = str(tmp_path / "ledger.jsonl")
    L.append_entry(path, _pending("q1"), fsync=False)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write('{"partial": "torn line with no clos')  # crash mid-append, no newline
    entries, torn = L.read_entries(path)
    assert torn is True
    assert len(entries) == 1  # the one complete entry survives; torn tail dropped


def test_midfile_corruption_raises(tmp_path) -> None:
    path = str(tmp_path / "ledger.jsonl")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(_pending("q1")) + "\n")
        handle.write("NOT JSON AT ALL\n")           # corrupt NON-final line
        handle.write(json.dumps(_pending("q2")) + "\n")
    with pytest.raises(L.LedgerCorruptionError):
        L.read_entries(path)


def test_written_ledger_is_utf8_no_bom_lf(tmp_path) -> None:
    path = str(tmp_path / "ledger.jsonl")
    L.append_entry(path, _pending("q1"), fsync=False)
    raw = open(path, "rb").read()
    assert raw[:3] != b"\xef\xbb\xbf"   # no BOM
    assert raw.endswith(b"}\n")          # LF-terminated JSONL
