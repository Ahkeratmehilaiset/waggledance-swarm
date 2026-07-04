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

from collections.abc import Mapping
from typing import Any, NamedTuple

from waggledance.core.magma.chat_served_ledger import (
    GAP_TERMINAL,
    RECEIPT_TERMINAL,
    SERVED_PENDING,
    read_entries,
    verify_chain,
)

# derived per-served_id lifecycle states
_PENDING = "pending"
_RECEIPT = "receipt"
_GAP = "gap"


class ClaimCoverageReport(NamedTuple):
    served: int                 # distinct served_ids with a pending (the denominator)
    receipts: int               # served_ids with pending AND a receipt terminal (numerator)
    gaps: int                   # served_ids with pending AND a gap terminal
    unresolved_pending: int     # served_ids with a pending and no terminal (crash/in-flight)
    ratio: float | None         # receipts/served, or None when served == 0
    chain_ok: bool
    lifecycle_ok: bool
    eligible: bool              # receipt-coverage half of claim-safety
    reason: str | None          # why not eligible (None when eligible)


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


def derive_coverage(entries: list[Mapping[str, Any]]) -> ClaimCoverageReport:
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

    served = len(state)
    receipts = sum(1 for v in state.values() if v == _RECEIPT)
    gaps = sum(1 for v in state.values() if v == _GAP)
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
        ratio=ratio,
        chain_ok=chain_ok,
        lifecycle_ok=lifecycle_ok,
        eligible=eligible,
        reason=reason,
    )


def coverage_from_ledger(ledger_path: str) -> ClaimCoverageReport:
    """Read a ledger file and derive its claim-coverage report (a torn tail leaves an
    unresolved pending -> not eligible, correctly)."""
    entries, _torn_tail = read_entries(ledger_path)
    return derive_coverage(entries)


__all__ = ["ClaimCoverageReport", "derive_coverage", "coverage_from_ledger"]
