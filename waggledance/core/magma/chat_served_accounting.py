# SPDX-License-Identifier: BUSL-1.1
"""Read-only claim-coverage accounting over the chat-served ledger (P2 S1b, T4).

This is the READ side: it walks a ``chat_served_ledger`` and DERIVES the receipt
coverage of served chat queries -- it never writes and never flips ``claim_safe``
(that is the operator-signed S4 flip, which also folds in the S3 solver-first
ratio). It answers one question honestly: over the entries given, does EVERY served
query carry a receipt, with no gap and no unresolved pending, on an intact ledger?

Discipline (rco-1 F-C / #1495):
* the DENOMINATOR is ``served`` = distinct served_ids that have a ``served_pending``
  (the crash-safe count; a pending is written synchronously before the response, so
  the denominator cannot be narrowed after the fact);
* the NUMERATOR is ``receipts`` = served_ids that have BOTH a pending AND a receipt
  terminal -- a strict SUBSET of the denominator, so ``receipts / served <= 1.0`` by
  construction (never an asserted counter);
* it is an ATTESTATION, so it ENFORCES (does not assume a correct writer): a broken
  chain OR a lifecycle violation (duplicate pending, terminal without a pending, a
  second terminal) makes coverage NOT eligible, fail-closed -- the same enforce-don't-
  trust rule the ledger verifier and the sink loader use.

Eligible (for the receipt-coverage half of claim-safety) iff: chain intact AND
lifecycle valid AND served > 0 AND receipts == served AND no gaps AND no unresolved
pending. Anything short -> not eligible, with a specific reason. Default/empty -> not
eligible. This module is DORMANT until S1b wires the ledger.
"""
from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping
from typing import Any, NamedTuple

from waggledance.core.magma.chat_served_ledger import (
    GAP_TERMINAL,
    RECEIPT_TERMINAL,
    SERVED_PENDING,
    head_hash,
    is_ledger_hash,
    read_entries,
    verify_chain,
)
from waggledance.core.magma.chat_served_metadata import is_conforming_token

PENDING_APPEND_FAILURE_SCHEMA = "magma.chat_served_pending_append_failure.v0"
PENDING_APPEND_FAILURE_REASONS = frozenset({
    "metadata_rejected",
    "sink_write_failed",
})
REQUIRED_CHAT_SERVED_POINTS = frozenset({
    "hotcache",
    "solver",
    "hybrid_retrieval",
    "hex_mesh",
    "llm",
})

# derived per-served_id lifecycle states
_PENDING = "pending"
_RECEIPT = "receipt"
_GAP = "gap"


class ClaimCoverageReport(NamedTuple):
    served: int                 # distinct served_ids with a pending (the denominator)
    receipts: int               # served_ids with pending AND a receipt terminal (numerator)
    gaps: int                   # served_ids with pending AND a gap terminal
    unresolved_pending: int     # served_ids with a pending and no terminal (crash/in-flight)
    pending_append_failures: int # served queries whose sync pending append failed
    ratio: float | None         # receipts/served, or None when served == 0
    chain_ok: bool
    lifecycle_ok: bool
    eligible: bool              # receipt-coverage half of claim-safety
    reason: str | None          # why not eligible (None when eligible)


class ClaimWindowReport(NamedTuple):
    """Fail-closed gate report for a named chat-served claim window.

    This is a measurement/gate object only. It never flips ``claim_safe``; it
    explains whether a window has enough evidence to be considered eligible by a
    later operator-signed claim step.
    """

    coverage: ClaimCoverageReport
    actual_head: str | None
    expected_head: str | None
    enabled_across_window: bool
    clean_shutdown: bool
    torn_tail: bool
    required_served_points: tuple[str, ...]
    instrumented_served_points: tuple[str, ...]
    missing_served_points: tuple[str, ...]
    eligible: bool
    reason: str | None
    read_error: str | None


def valid_pending_append_failure(entry: object) -> bool:
    """Return True iff ``entry`` is a sanitized durable pending-append failure."""
    if not isinstance(entry, Mapping):
        return False
    if entry.get("schema_version") != PENDING_APPEND_FAILURE_SCHEMA:
        return False
    reason = entry.get("reason")
    if reason not in PENDING_APPEND_FAILURE_REASONS:
        return False
    served_id_hash = entry.get("served_id_hash")
    if not (
        isinstance(served_id_hash, str)
        and len(served_id_hash) == 71
        and served_id_hash.startswith("sha256:")
        and all(c in "0123456789abcdef" for c in served_id_hash[7:])
    ):
        return False
    ts_utc = entry.get("ts_utc")
    if not isinstance(ts_utc, str) or not ts_utc:
        return False
    metadata = entry.get("metadata")
    if not isinstance(metadata, Mapping):
        return False
    for key, value in metadata.items():
        if not is_conforming_token(key) or not is_conforming_token(value):
            return False
    return True


def read_pending_append_failures(path: str | None) -> int:
    """Count durable pending-append failures. Corrupt lines fail closed as failures."""
    if not path or not os.path.exists(path):
        return 0
    count = 0
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                count += 1
                continue
            # Every durable line represents one served query whose sync pending
            # append failed. Invalid/corrupt content still counts fail-closed.
            count += 1
    return count


def _lifecycle_violation(state: str | None, entry_type: str) -> str | None:
    """The bc3 rule as a pure predicate (mirrors the sink's live guard); None == ok."""
    if entry_type == SERVED_PENDING:
        if state is not None:
            return "duplicate_pending"
    elif entry_type in (RECEIPT_TERMINAL, GAP_TERMINAL):
        if state is None:
            return "terminal_without_pending"
        if state in (_RECEIPT, _GAP):
            return "second_terminal"
    else:
        return "unknown_entry_type"
    return None


def derive_coverage(
    entries: list[Mapping[str, Any]],
    *,
    pending_append_failures: int = 0,
) -> ClaimCoverageReport:
    """Derive the claim-coverage report by WALKING the ledger. Fail-closed on any
    chain or lifecycle defect (reported as not-eligible, never raised)."""
    chain = verify_chain(entries)
    chain_ok = chain.ok

    state: dict[str, str] = {}
    lifecycle_ok = True
    lifecycle_reason: str | None = None
    for entry in entries:
        served_id = str(entry.get("served_id"))
        etype = str(entry.get("entry_type"))
        violation = _lifecycle_violation(state.get(served_id), etype)
        if violation is not None:
            lifecycle_ok = False
            lifecycle_reason = violation
            break
        if etype == SERVED_PENDING:
            state[served_id] = _PENDING
        elif etype == RECEIPT_TERMINAL:
            state[served_id] = _RECEIPT
        elif etype == GAP_TERMINAL:
            state[served_id] = _GAP

    failure_count = max(0, int(pending_append_failures))
    served = len(state) + failure_count
    receipts = sum(1 for v in state.values() if v == _RECEIPT)
    gaps = sum(1 for v in state.values() if v == _GAP) + failure_count
    unresolved = sum(1 for v in state.values() if v == _PENDING)
    ratio = (receipts / served) if served > 0 else None

    # eligibility (fail-closed): every short-fall names itself, most-fundamental first
    reason: str | None = None
    if not chain_ok:
        reason = f"chain_invalid:{chain.reason}"
    elif not lifecycle_ok:
        reason = f"lifecycle_invalid:{lifecycle_reason}"
    elif served == 0:
        reason = "no_served_queries"
    elif failure_count > 0:
        reason = "pending_append_failures"
    elif unresolved > 0:
        reason = "unresolved_pending"
    elif gaps > 0:
        reason = "gaps_present"
    elif receipts != served:
        reason = "receipts_below_served"  # defensive: implied by no-gaps + no-unresolved
    eligible = reason is None

    return ClaimCoverageReport(
        served=served,
        receipts=receipts,
        gaps=gaps,
        unresolved_pending=unresolved,
        pending_append_failures=failure_count,
        ratio=ratio,
        chain_ok=chain_ok,
        lifecycle_ok=lifecycle_ok,
        eligible=eligible,
        reason=reason,
    )


def coverage_from_ledger(
    ledger_path: str,
    *,
    pending_failure_ledger_path: str | None = None,
) -> ClaimCoverageReport:
    """Read a ledger file and derive its claim-coverage report (a torn tail leaves an
    unresolved pending -> not eligible, correctly)."""
    entries, _torn_tail = read_entries(ledger_path)
    return derive_coverage(
        entries,
        pending_append_failures=read_pending_append_failures(
            pending_failure_ledger_path
        ),
    )


def _normalized_point_set(points: Iterable[str] | None) -> tuple[str, ...]:
    if points is None:
        return ()
    return tuple(sorted({str(point) for point in points if str(point)}))


def derive_claim_window(
    entries: list[Mapping[str, Any]],
    *,
    expected_head: str | None,
    enabled_across_window: bool,
    clean_shutdown: bool,
    instrumented_served_points: Iterable[str] | None,
    required_served_points: Iterable[str] | None = REQUIRED_CHAT_SERVED_POINTS,
    pending_append_failures: int = 0,
    torn_tail: bool = False,
    read_error: str | None = None,
) -> ClaimWindowReport:
    """Evaluate the outer claim-window gates around receipt coverage.

    The inner coverage walk can prove a ledger segment is gapless, but it cannot
    prove the operator's named window stayed enabled, was cleanly shut down, kept
    its externally anchored head, or had every ChatService served point wired.
    Those are explicit inputs to this function and each fails closed.
    """
    coverage = derive_coverage(
        entries,
        pending_append_failures=pending_append_failures,
    )
    actual = None if read_error is not None else head_hash(entries)
    required = _normalized_point_set(required_served_points)
    instrumented = _normalized_point_set(instrumented_served_points)
    instrumented_set = set(instrumented)
    missing = tuple(point for point in required if point not in instrumented_set)

    reason: str | None = None
    if read_error:
        reason = f"ledger_read_failed:{read_error}"
    elif expected_head is None:
        reason = "missing_expected_head_anchor"
    elif not is_ledger_hash(expected_head):
        reason = "invalid_expected_head_anchor"
    elif actual != expected_head:
        reason = "head_anchor_mismatch"
    elif not enabled_across_window:
        reason = "not_enabled_across_window"
    elif not clean_shutdown:
        reason = "unclean_shutdown_window_invalid"
    elif torn_tail:
        reason = "ledger_torn_tail"
    elif missing:
        reason = "served_point_instrumentation_incomplete"
    elif not coverage.eligible:
        reason = f"coverage_not_eligible:{coverage.reason}"

    return ClaimWindowReport(
        coverage=coverage,
        actual_head=actual,
        expected_head=expected_head,
        enabled_across_window=bool(enabled_across_window),
        clean_shutdown=bool(clean_shutdown),
        torn_tail=bool(torn_tail),
        required_served_points=required,
        instrumented_served_points=instrumented,
        missing_served_points=missing,
        eligible=reason is None,
        reason=reason,
        read_error=read_error,
    )


def claim_window_from_ledger(
    ledger_path: str,
    *,
    expected_head: str | None,
    enabled_across_window: bool,
    clean_shutdown: bool,
    instrumented_served_points: Iterable[str] | None,
    required_served_points: Iterable[str] | None = REQUIRED_CHAT_SERVED_POINTS,
    pending_failure_ledger_path: str | None = None,
) -> ClaimWindowReport:
    """Read a ledger and evaluate the complete fail-closed claim-window gate."""
    pending_failures: int | None = None
    try:
        pending_failures = read_pending_append_failures(pending_failure_ledger_path)
        entries, torn_tail = read_entries(ledger_path)
        return derive_claim_window(
            entries,
            expected_head=expected_head,
            enabled_across_window=enabled_across_window,
            clean_shutdown=clean_shutdown,
            instrumented_served_points=instrumented_served_points,
            required_served_points=required_served_points,
            pending_append_failures=pending_failures,
            torn_tail=torn_tail,
        )
    except Exception as exc:  # noqa: BLE001 - claim-window reads fail closed
        if pending_failures is None:
            pending_failures = 1
        return derive_claim_window(
            [],
            expected_head=expected_head,
            enabled_across_window=enabled_across_window,
            clean_shutdown=clean_shutdown,
            instrumented_served_points=instrumented_served_points,
            required_served_points=required_served_points,
            pending_append_failures=pending_failures,
            read_error=exc.__class__.__name__,
        )


__all__ = [
    "ClaimCoverageReport",
    "ClaimWindowReport",
    "PENDING_APPEND_FAILURE_REASONS",
    "PENDING_APPEND_FAILURE_SCHEMA",
    "REQUIRED_CHAT_SERVED_POINTS",
    "claim_window_from_ledger",
    "coverage_from_ledger",
    "derive_claim_window",
    "derive_coverage",
    "read_pending_append_failures",
    "valid_pending_append_failure",
]
