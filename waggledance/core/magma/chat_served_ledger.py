# SPDX-License-Identifier: BUSL-1.1
"""Durable, hash-chained served->{receipt|gap} ledger for chat claim-safety (P2 S1b, T2).

This is the append-only LEDGER (separate from MAGMA receipt bundles) that records,
for every served chat query, a crash-safe two-phase trail:

  1. a SYNCHRONOUS ``served_pending`` entry, written before the response returns --
     the crash-safe DENOMINATOR: proof a query was served, before we know whether a
     receipt will follow; then
  2. exactly ONE terminal that resolves it OFF the event loop -- either a ``receipt``
     (a MAGMA receipt was written; its digest ref is recorded) or a ``gap`` (no
     receipt: a genuine hole in coverage).

Entries are hash-chained (``prev_ledger_hash`` -> ``entry_hash``), so the ledger is
tamper-evident and a missing terminal (a crash between the pending and its receipt)
stays visible as an UNRESOLVED pending when the chain is walked. The receipts/served
ratio is DERIVED by walking the chain (T4 accounting), never an asserted counter
(#1495 discipline: numerator = served_ids with BOTH a pending and a receipt terminal,
so it is a SUBSET of the denominator and the ratio is <= 1.0 by construction).

Contract (owned by the S1b serve-path wiring; this module only supplies the shape):
the serve path is FAIL-OPEN (a pending-append failure still serves the user) and the
CLAIM is FAIL-CLOSED (that failure is recorded as a gap via an independent channel,
so claim_safe -> false). This module never blocks and never flips claim_safe; it
provides pure builders + a durable append primitive + chain verification. It is
DORMANT until the S1b wiring lands. Concurrency (a single serialized writer) is a T3
concern -- ``append_entry`` here is a single-writer durable primitive.
"""
from __future__ import annotations

import hashlib
import json
import os
from collections import namedtuple
from collections.abc import Mapping
from typing import Any

from waggledance.core.magma.chat_served_metadata import is_conforming_token

PAYLOAD_VERSION = "magma.chat_served_ledger_entry.v0"

# --- entry types -----------------------------------------------------------------
SERVED_PENDING = "served_pending"
RECEIPT_TERMINAL = "receipt"
GAP_TERMINAL = "gap"
ENTRY_TYPES = frozenset({SERVED_PENDING, RECEIPT_TERMINAL, GAP_TERMINAL})
TERMINAL_TYPES = frozenset({RECEIPT_TERMINAL, GAP_TERMINAL})

# The fixed, self-describing set of gap reasons. A gap NEVER carries free-form text
# (that would be a raw-text leak channel, the #1488/#1496 class); it is one of these.
GAP_REASONS = frozenset({
    "sink_write_failed",     # the receipt/sink write failed -> independent gap (bc4)
    "pending_unresolved",    # crash between pending and terminal (bc2)
    "receipts_disabled",     # feature off -> every served query is a gap
    "receipt_build_failed",  # the receipt could not be built
    "metadata_rejected",     # metadata failed the strict shape (should be rare)
})

_HASH_PREFIX = "sha256:"
_HASH_HEX_LEN = 64
# the genesis predecessor of the first entry (well-formed, self-describing hex)
GENESIS_PREV_HASH = _HASH_PREFIX + ("0" * _HASH_HEX_LEN)

_HASH_FIELD = "entry_hash"


class LedgerError(ValueError):
    """Malformed input to a ledger builder/append (fail-closed at construction)."""


class LedgerCorruptionError(RuntimeError):
    """A persisted ledger line is corrupt mid-file (not a tolerable torn tail)."""


ChainResult = namedtuple("ChainResult", ("ok", "broken_at", "reason"))


# --- hashing ---------------------------------------------------------------------
def is_ledger_hash(value: object) -> bool:
    """True iff ``value`` is a ``sha256:<64 lowercase hex>`` digest string."""
    if not isinstance(value, str) or not value.startswith(_HASH_PREFIX):
        return False
    hexpart = value[len(_HASH_PREFIX):]
    if len(hexpart) != _HASH_HEX_LEN:
        return False
    return all(c in "0123456789abcdef" for c in hexpart)


def _canonical_bytes(entry: Mapping[str, Any]) -> bytes:
    """Deterministic serialization of an entry for hashing (EXCLUDES entry_hash)."""
    payload = {k: entry[k] for k in entry if k != _HASH_FIELD}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def compute_entry_hash(entry: Mapping[str, Any]) -> str:
    """The chain hash of an entry: sha256 over every field except entry_hash."""
    return _HASH_PREFIX + hashlib.sha256(_canonical_bytes(entry)).hexdigest()


def verify_entry_self(entry: object) -> bool:
    """True iff the entry's recorded entry_hash matches its content."""
    if not isinstance(entry, Mapping) or _HASH_FIELD not in entry:
        return False
    return entry.get(_HASH_FIELD) == compute_entry_hash(entry)


# --- validation ------------------------------------------------------------------
def _require_token(field: str, value: object) -> str:
    if not is_conforming_token(value):
        raise LedgerError(f"{field} is not a conforming token: {value!r}")
    return value  # type: ignore[return-value]


def _require_ledger_hash(field: str, value: object) -> str:
    if not is_ledger_hash(value):
        raise LedgerError(f"{field} is not a sha256 ledger hash: {value!r}")
    return value  # type: ignore[return-value]


def _validated_metadata(metadata: object) -> dict[str, str]:
    """Every key AND value must be a conforming SCALAR token.

    ``is_conforming_token`` is False for non-str (dict/list/int/None), so a nested
    structure is REJECTED rather than str()'d into the ledger (the #1496 non-scalar
    leak). Callers must pre-normalize (chat_served_metadata) and omit None fields.
    """
    if not isinstance(metadata, Mapping):
        raise LedgerError(f"metadata must be a mapping, got {type(metadata).__name__}")
    out: dict[str, str] = {}
    for key, value in metadata.items():
        if not is_conforming_token(key):
            raise LedgerError(f"metadata key is not a conforming token: {key!r}")
        if not is_conforming_token(value):
            raise LedgerError(f"metadata value for {key!r} is not a conforming scalar token: {value!r}")
        out[str(key)] = str(value)
    return out


def _finalize(entry: dict[str, Any]) -> dict[str, Any]:
    entry[_HASH_FIELD] = compute_entry_hash(entry)
    return entry


# --- builders (pure) -------------------------------------------------------------
def new_served_pending(
    served_id: str,
    prev_ledger_hash: str,
    ts_utc: str,
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    """Phase-1 crash-safe denominator entry (written synchronously on the serve path)."""
    return _finalize({
        "payload_version": PAYLOAD_VERSION,
        "entry_type": SERVED_PENDING,
        "served_id": _require_token("served_id", served_id),
        "ts_utc": _require_token("ts_utc", ts_utc),
        "prev_ledger_hash": _require_ledger_hash("prev_ledger_hash", prev_ledger_hash),
        "metadata": _validated_metadata(metadata),
    })


def new_receipt_terminal(
    served_id: str,
    prev_ledger_hash: str,
    ts_utc: str,
    receipt_ref: str,
) -> dict[str, Any]:
    """Terminal: a MAGMA receipt was written for ``served_id``; record its digest ref."""
    return _finalize({
        "payload_version": PAYLOAD_VERSION,
        "entry_type": RECEIPT_TERMINAL,
        "served_id": _require_token("served_id", served_id),
        "ts_utc": _require_token("ts_utc", ts_utc),
        "prev_ledger_hash": _require_ledger_hash("prev_ledger_hash", prev_ledger_hash),
        "receipt_ref": _require_ledger_hash("receipt_ref", receipt_ref),
    })


def new_gap_terminal(
    served_id: str,
    prev_ledger_hash: str,
    ts_utc: str,
    gap_reason: str,
) -> dict[str, Any]:
    """Terminal: no receipt for ``served_id`` -- a genuine coverage hole (fail-closed claim)."""
    if gap_reason not in GAP_REASONS:
        raise LedgerError(f"gap_reason must be one of {sorted(GAP_REASONS)}, got {gap_reason!r}")
    return _finalize({
        "payload_version": PAYLOAD_VERSION,
        "entry_type": GAP_TERMINAL,
        "served_id": _require_token("served_id", served_id),
        "ts_utc": _require_token("ts_utc", ts_utc),
        "prev_ledger_hash": _require_ledger_hash("prev_ledger_hash", prev_ledger_hash),
        "gap_reason": gap_reason,
    })


# --- durable persistence (single-writer; T3 adds the serializing lock) -----------
def append_entry(ledger_path: str, entry: Mapping[str, Any], *, fsync: bool = True) -> str:
    """Durably append one well-formed entry as a JSONL line; return the new chain head.

    Re-verifies the entry's self-hash before writing (never append an entry whose
    hash does not match its content). Writes one line via O_APPEND (single write) +
    flush, and fsync when requested. Per block-condition 5 the SYNCHRONOUS serve-path
    pending append should pass fsync=False (a windowed/boundary fsync discipline lives
    in the S1b wiring), so a per-request fsync never regresses chat latency.
    """
    if not verify_entry_self(entry):
        raise LedgerError("entry_hash does not match content; refusing to append")
    line = (json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode("ascii")
    # O_BINARY (Windows) stops text-mode LF->CRLF translation, so the durable format
    # is deterministic LF JSONL, no BOM, on every platform (POSIX has no O_BINARY -> 0).
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_BINARY", 0)
    fd = os.open(ledger_path, flags, 0o644)
    try:
        os.write(fd, line)
        if fsync:
            os.fsync(fd)
    finally:
        os.close(fd)
    return entry[_HASH_FIELD]


def read_entries(ledger_path: str) -> tuple[list[dict[str, Any]], bool]:
    """Parse the ledger. Returns (entries, torn_tail).

    A single unparseable FINAL line is tolerated as a crash torn-tail (torn_tail=True,
    that line dropped) -- block-condition 2 then surfaces it as an unresolved pending.
    An unparseable NON-final line is real corruption and raises (never silently
    skipped -- the #1469 do-not-swallow discipline).
    """
    entries: list[dict[str, Any]] = []
    torn_tail = False
    if not os.path.exists(ledger_path):
        return entries, torn_tail
    with open(ledger_path, "r", encoding="utf-8") as handle:
        raw_lines = handle.read().split("\n")
    # a trailing newline yields a final "" element; find the last NON-empty index
    last_nonempty = -1
    for idx in range(len(raw_lines) - 1, -1, -1):
        if raw_lines[idx].strip():
            last_nonempty = idx
            break
    for idx, raw in enumerate(raw_lines):
        text = raw.strip()
        if not text:
            continue
        try:
            entries.append(json.loads(text))
        except json.JSONDecodeError as exc:
            if idx == last_nonempty:
                torn_tail = True  # tolerate a crash torn-tail
            else:
                raise LedgerCorruptionError(f"malformed ledger line {idx + 1}") from exc
    return entries, torn_tail


# --- chain verification ----------------------------------------------------------
def head_hash(entries: list[Mapping[str, Any]]) -> str:
    """The current chain head (last entry's hash) or GENESIS_PREV_HASH if empty."""
    if not entries:
        return GENESIS_PREV_HASH
    return str(entries[-1].get(_HASH_FIELD))


def verify_chain(entries: list[Mapping[str, Any]]) -> ChainResult:
    """Verify every entry's self-hash AND the prev_ledger_hash linkage from genesis.

    A hole (a dropped/tampered entry) breaks the linkage and is reported with the
    0-based index of the first bad entry -- a gapless-looking ledger with a hole is
    impossible.
    """
    prev = GENESIS_PREV_HASH
    for index, entry in enumerate(entries):
        if not verify_entry_self(entry):
            return ChainResult(False, index, "entry_hash_mismatch")
        if entry.get("prev_ledger_hash") != prev:
            return ChainResult(False, index, "prev_hash_broken")
        if entry.get("entry_type") not in ENTRY_TYPES:
            return ChainResult(False, index, "unknown_entry_type")
        prev = str(entry.get(_HASH_FIELD))
    return ChainResult(True, None, None)


__all__ = [
    "PAYLOAD_VERSION",
    "SERVED_PENDING", "RECEIPT_TERMINAL", "GAP_TERMINAL",
    "ENTRY_TYPES", "TERMINAL_TYPES", "GAP_REASONS", "GENESIS_PREV_HASH",
    "LedgerError", "LedgerCorruptionError", "ChainResult",
    "is_ledger_hash", "compute_entry_hash", "verify_entry_self",
    "new_served_pending", "new_receipt_terminal", "new_gap_terminal",
    "append_entry", "read_entries", "head_hash", "verify_chain",
]
