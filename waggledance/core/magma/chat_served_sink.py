# SPDX-License-Identifier: BUSL-1.1
"""Serialized sink over the chat-served ledger (P2 S1b, T3).

One ``ChatServedReceiptSink`` is the single serialized writer to a
``chat_served_ledger`` file. It runs OFF the chat event loop (in an executor) and
enforces the invariants that build on the T2 primitives:

* **block-condition 3 (rco-1 F-C):** exactly ONE terminal per served_id and a
  UNIQUE served_id -- a second pending for a known id (``ServedIdCollision``) or a
  terminal that has no pending / already has a terminal (``TerminalError``) is
  refused. A collision would silently mask a second served query = a hidden hole,
  so the sink refuses and the serve-path caller (S1b) records a gap instead.
* **block-condition 5 (fsync latency):** WINDOWED fsync -- appends do not fsync
  per request (that would regress chat latency on the synchronous pending write);
  fsync happens on a window boundary (``fsync_every``) or an explicit ``flush()``.
* **serialization:** all mutations hold ``sink_lock`` (a single-process, multi-thread
  serialized writer -- the intended executor model). Cross-process fan-out is out of
  scope; there is exactly one sink instance.

State (served_id -> pending|receipt|gap) is DERIVED from the ledger by walking it on
init -- the ledger is the durable source of truth, the map is a rebuildable cache, so
there is no separate index to keep atomically consistent. An unresolved pending left
by a crash stays ``pending`` and is counted as a gap by the T4 accounting.

The sink NEVER flips claim_safe and does no network / event-loop work. Fail-OPEN on
the serve path is the S1b wiring's responsibility: it catches a sink error, still
serves the user, and records the gap via an independent channel (bc4).
"""
from __future__ import annotations

import os
import threading
from collections.abc import Mapping
from typing import Any

from waggledance.core.magma.chat_served_ledger import (
    GAP_TERMINAL,
    RECEIPT_TERMINAL,
    SERVED_PENDING,
    append_entry,
    head_hash,
    new_gap_terminal,
    new_receipt_terminal,
    new_served_pending,
    read_entries,
    verify_chain,
)

# served_id lifecycle states (derived from the ledger)
_PENDING = "pending"
_RECEIPT = "receipt"
_GAP = "gap"
_TERMINAL_STATES = frozenset({_RECEIPT, _GAP})


class SinkError(RuntimeError):
    """Base for sink invariant violations. The serve-path caller fails OPEN on these."""


class ServedIdCollision(SinkError):
    """A pending was recorded for a served_id that is already known (bc3 uniqueness)."""


class TerminalError(SinkError):
    """A terminal has no pending, or the served_id already has a terminal (bc3 one-terminal)."""


class LedgerStateError(SinkError):
    """The ledger could not be loaded into a consistent state (e.g. chain broken)."""


class ChatServedReceiptSink:
    """A single serialized writer to one chat-served ledger file."""

    def __init__(self, ledger_path: str, *, fsync_every: int = 32) -> None:
        self._path = ledger_path
        self._lock = threading.Lock()
        self._fsync_every = int(fsync_every)
        self._since_fsync = 0
        self._state: dict[str, str] = {}
        # rebuild derived state from the durable ledger (source of truth)
        entries, torn_tail = read_entries(ledger_path)
        chain = verify_chain(entries)
        if not chain.ok:
            raise LedgerStateError(
                f"ledger chain invalid at index {chain.broken_at}: {chain.reason}"
            )
        self._head = head_hash(entries)
        self._torn_tail = torn_tail
        for entry in entries:
            served_id = str(entry.get("served_id"))
            etype = entry.get("entry_type")
            if etype == SERVED_PENDING:
                self._state[served_id] = _PENDING
            elif etype == RECEIPT_TERMINAL:
                self._state[served_id] = _RECEIPT
            elif etype == GAP_TERMINAL:
                self._state[served_id] = _GAP

    # -- properties -----------------------------------------------------------
    @property
    def head(self) -> str:
        with self._lock:
            return self._head

    @property
    def torn_tail_on_load(self) -> bool:
        """True if the ledger had a crash torn-tail when this sink loaded it."""
        return self._torn_tail

    # -- mutations (all serialized) -------------------------------------------
    def record_pending(
        self,
        served_id: str,
        ts_utc: str,
        metadata: Mapping[str, Any],
        *,
        fsync: bool | None = None,
    ) -> str:
        """Append the synchronous crash-safe pending denominator; return the new head.

        Raises ``ServedIdCollision`` if ``served_id`` is already known (bc3 uniqueness).
        Default fsync is WINDOWED (see ``_append``) so the synchronous serve path is
        not blocked on disk per request (bc5).
        """
        with self._lock:
            if served_id in self._state:
                raise ServedIdCollision(
                    f"served_id already recorded as {self._state[served_id]!r}: {served_id!r}"
                )
            entry = new_served_pending(served_id, self._head, ts_utc, metadata)
            self._append_locked(entry, fsync)
            self._state[served_id] = _PENDING
            return self._head

    def resolve_receipt(
        self, served_id: str, ts_utc: str, receipt_ref: str, *, fsync: bool | None = None
    ) -> str:
        """Resolve a pending with a receipt terminal (a MAGMA receipt was written)."""
        return self._resolve(
            served_id, RECEIPT_TERMINAL, ts_utc, receipt_ref=receipt_ref, fsync=fsync
        )

    def resolve_gap(
        self, served_id: str, ts_utc: str, gap_reason: str, *, fsync: bool | None = None
    ) -> str:
        """Resolve a pending with a gap terminal (a genuine coverage hole)."""
        return self._resolve(served_id, GAP_TERMINAL, ts_utc, gap_reason=gap_reason, fsync=fsync)

    def _resolve(
        self,
        served_id: str,
        terminal_type: str,
        ts_utc: str,
        *,
        receipt_ref: str | None = None,
        gap_reason: str | None = None,
        fsync: bool | None = None,
    ) -> str:
        with self._lock:
            state = self._state.get(served_id)
            if state is None:
                raise TerminalError(f"no pending for served_id {served_id!r} (cannot resolve)")
            if state in _TERMINAL_STATES:
                raise TerminalError(
                    f"served_id {served_id!r} already terminal ({state!r}); refuse a second terminal"
                )
            if terminal_type == RECEIPT_TERMINAL:
                entry = new_receipt_terminal(served_id, self._head, ts_utc, receipt_ref)
                new_state = _RECEIPT
            else:
                entry = new_gap_terminal(served_id, self._head, ts_utc, gap_reason)
                new_state = _GAP
            self._append_locked(entry, fsync)
            self._state[served_id] = new_state
            return self._head

    def _append_locked(self, entry: Mapping[str, Any], fsync: bool | None) -> None:
        """Append under the held lock, advancing the head with WINDOWED fsync."""
        do_fsync = bool(fsync) if fsync is not None else False
        self._since_fsync += 1
        if not do_fsync and self._fsync_every > 0 and self._since_fsync >= self._fsync_every:
            do_fsync = True
        self._head = append_entry(self._path, entry, fsync=do_fsync)
        if do_fsync:
            self._since_fsync = 0

    def flush(self) -> None:
        """Force a durability boundary: fsync the ledger file (window flush)."""
        with self._lock:
            if not os.path.exists(self._path):
                return
            # fsync needs a WRITABLE handle on Windows (O_RDONLY -> EBADF); O_APPEND
            # opens one without writing anything and flushes the file's buffers.
            fd = os.open(self._path, os.O_WRONLY | os.O_APPEND | getattr(os, "O_BINARY", 0))
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            self._since_fsync = 0

    # -- read-only accounting seed (T4 formalizes the ratio) ------------------
    def counts(self) -> dict[str, int]:
        """served / receipts / gaps / pending_unresolved, from the derived state.

        ``receipts`` is a SUBSET of ``served`` (every receipt resolves a pending that
        counts in served), so any receipts/served ratio T4 derives is <= 1.0 by
        construction (#1495). An unresolved pending counts against the ratio (a gap).
        """
        with self._lock:
            values = list(self._state.values())
        served = len(values)
        return {
            "served": served,
            "receipts": sum(1 for v in values if v == _RECEIPT),
            "gaps": sum(1 for v in values if v == _GAP),
            "pending_unresolved": sum(1 for v in values if v == _PENDING),
        }


__all__ = [
    "ChatServedReceiptSink",
    "SinkError",
    "ServedIdCollision",
    "TerminalError",
    "LedgerStateError",
]
