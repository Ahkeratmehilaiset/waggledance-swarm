"""S2 -- per-query MAGMA receipt coverage (measurement-only, re-derive-don't-trust).

This layer sits ON TOP of the existing chat_served ledger (served_id-keyed lifecycle) and
binds it to the S1 route-evidence CORPUS (query_digest-keyed) to answer one measurement
question, honestly and un-forgeably:

    per_query_receipt_coverage_present : is EVERY served query in the S1 corpus bound to a
    real, content-addressed, verifier-passing MAGMA receipt for THAT query -- exactly once,
    gap-free -- measured under the SAME code head / corpus / schema as the claim context?

Frozen W1A v3 contract (dual-RCO PASS) + rco-1 MF-1a + the W1B cross-contract amendment.
Every clause is re-derived from primitive fields; NO producer-supplied value is a trust
boundary: the `bound` flag is never counted, the corpus digest is recomputed INTERNALLY with
the canonical W1B algorithm (not a caller-supplied function), and malformed route-evidence
fails CLOSED (never silently filtered). It NEVER reads or writes the claim-safety flag:
coverage_present is EVIDENCE, necessary-not-sufficient, tagged ``measurement_not_a_correctness_gate``.
Any claim-safety flip is a separate (a)-class operator-EXPLICIT step, not anything this module can do.

The receipt resolution is still via injected seams (``resolve_receipt`` / ``verify_receipt`` /
``content_hash`` / ``receipt_query_digest``) so the forgery matrix is exercised directly; the
production wiring binds those to ``ChatServedReceiptSink`` + the receipt bundle store +
``waggledance.core.magma.canonical.sha256_digest`` + ``tools.verify_magma_receipt``.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Mapping, NamedTuple, Optional, Sequence

from waggledance.core.magma.canonical import sha256_digest
from waggledance.core.magma.chat_query_route_evidence import NORMALIZATION_VERSION
from waggledance.core.magma.chat_served_ledger import GAP_REASONS, is_path_safe_token

# The W1C coverage-report schema (this module's output shape).
SCHEMA_VERSION = "wd.chat_served_per_query_receipt_coverage.v1"
# The S1 run-context / route-evidence schema (what a W1B header carries) -- pinned SEPARATELY
# from the coverage-report schema (lead/operator cross-contract blocker 1).
RUN_CONTEXT_SCHEMA_VERSION = "wd.chat_query_route_evidence.v1"
# Domain separator for the canonical corpus digest (must equal W1B; golden vectors in PR #1518).
CORPUS_DIGEST_DOMAIN = "wd.chat_query_route_evidence.corpus_digest.v1"
MEASUREMENT_MARKER = "measurement_not_a_correctness_gate"

# Run-header fields that must match the claim context (rco-2 R2 stale/head binding).
_CONTEXT_KEYS = ("head_commit_sha", "corpus_digest", "schema_version", "normalization_version", "run_id")
# The exact per-row shape of an S1 route-evidence record this module consumes (adapter projects
# a W1B `wd.chat_first_hop_corpus.v1` record down to this shape). Exact keyset -> fail closed.
ROUTE_EVIDENCE_ROW_KEYS = frozenset({"served_id", "query_digest", "normalization_version"})
_RECEIPT_TERMINAL_KEYS = frozenset({"entry_type", "served_id", "receipt_ref"})
_GAP_TERMINAL_KEYS = frozenset({"entry_type", "served_id", "gap_reason"})

_HEX40 = re.compile(r"^[0-9a-f]{40}$")            # full-40 lowercase-hex commit sha
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")    # canonical digest (corpus_digest, run_id, query_digest)

_RECEIPT_TERMINAL = "receipt"
_GAP_TERMINAL = "gap"


class PerQueryCoverageReport(NamedTuple):
    """Re-derived, measurement-only per-query receipt coverage. Never a claim-safety trigger."""

    coverage_present: bool
    corpus_size: int
    verified_bound: int
    gapped: int
    missing: int
    forged_or_unbound: int
    duplicate_terminal: int
    duplicate_query_terminal: int
    orphan_terminals: int
    evidence_ok: bool            # every route-evidence row passed strict validation (no silent filter)
    context_ok: bool             # run-header matches claim context (R2)
    corpus_bound: bool           # run-header corpus_digest == INTERNAL canonical recompute
    pending_append_failures: int
    chain_ok: bool
    reason: Optional[str]
    schema_version: str = SCHEMA_VERSION
    measurement_marker: str = MEASUREMENT_MARKER


def canonical_corpus_digest(query_digests: Sequence[str]) -> str:
    """The canonical W1B corpus digest over the UNIQUE query digests. Recomputed here, never
    accepted from a caller (blocker 2). Must equal W1B's computation (golden vectors PR #1518)."""
    unique_sorted = sorted(set(query_digests))
    return sha256_digest({
        "domain": CORPUS_DIGEST_DOMAIN,
        "schema_version": RUN_CONTEXT_SCHEMA_VERSION,
        "normalization_version": NORMALIZATION_VERSION,
        "query_digests": unique_sorted,
    })


def _wellformed_header(h: Mapping[str, Any]) -> bool:
    """Exact 5-key set + valid shapes. Equality alone is insufficient (malformed-but-equal must
    reject). schema_version is pinned to the RUN-CONTEXT schema (blocker 1), not the report schema."""
    if not isinstance(h, Mapping) or set(h.keys()) != set(_CONTEXT_KEYS):
        return False
    head = h.get("head_commit_sha")
    corpus = h.get("corpus_digest")
    run_id = h.get("run_id")
    if not (isinstance(head, str) and _HEX40.fullmatch(head)):
        return False
    if not (isinstance(corpus, str) and _SHA256.fullmatch(corpus)):
        return False
    if not (isinstance(run_id, str) and _SHA256.fullmatch(run_id)):
        return False
    if h.get("schema_version") != RUN_CONTEXT_SCHEMA_VERSION:
        return False
    if h.get("normalization_version") != NORMALIZATION_VERSION:
        return False
    return True


def _context_matches(run_header: Mapping[str, Any], claim_context: Mapping[str, Any]) -> bool:
    if not _wellformed_header(run_header) or not _wellformed_header(claim_context):
        return False
    for key in _CONTEXT_KEYS:
        if run_header.get(key) != claim_context.get(key):
            return False
    return True


def _validate_route_evidence(
    route_evidence: Sequence[Mapping[str, Any]],
) -> tuple[bool, Optional[str], dict]:
    """Strict, fail-closed validation (blocker 3): exact row shape, nonempty UNIQUE served_ids,
    valid sha256 query_digests, matching per-row normalization_version. Repeated queries are
    distinct served events when their served_ids differ. Any malformed / duplicate served_id /
    missing row -> (False, reason, {}). Never silently filter."""
    served_seen: set[str] = set()
    served_to_query: dict[str, str] = {}
    for item in route_evidence:
        if not isinstance(item, Mapping) or set(item.keys()) != ROUTE_EVIDENCE_ROW_KEYS:
            return False, "row_shape", {}
        sid = item.get("served_id")
        qd = item.get("query_digest")
        nv = item.get("normalization_version")
        if not is_path_safe_token(sid):
            return False, "served_id", {}
        if not (isinstance(qd, str) and _SHA256.fullmatch(qd)):
            return False, "query_digest", {}
        if nv != NORMALIZATION_VERSION:
            return False, "normalization_version", {}
        if sid in served_seen:
            return False, "duplicate_served_id", {}
        served_seen.add(sid)
        served_to_query[sid] = qd
    return True, None, served_to_query


def _validate_ledger_terminals(
    ledger_terminals: Sequence[Mapping[str, Any]],
) -> tuple[bool, Optional[str], list[Mapping[str, Any]]]:
    """Validate terminal primitives all-or-nothing before any coverage accounting."""
    validated: list[Mapping[str, Any]] = []
    for index, term in enumerate(ledger_terminals):
        if not isinstance(term, Mapping):
            return False, f"row_{index}_not_mapping", []
        etype = term.get("entry_type")
        if etype == _RECEIPT_TERMINAL:
            if set(term.keys()) != _RECEIPT_TERMINAL_KEYS:
                return False, f"row_{index}_receipt_keyset", []
            receipt_ref = term.get("receipt_ref")
            if not (
                isinstance(receipt_ref, str)
                and _SHA256.fullmatch(receipt_ref)
            ):
                return False, f"row_{index}_receipt_ref", []
        elif etype == _GAP_TERMINAL:
            if set(term.keys()) != _GAP_TERMINAL_KEYS:
                return False, f"row_{index}_gap_keyset", []
            if term.get("gap_reason") not in GAP_REASONS:
                return False, f"row_{index}_gap_reason", []
        else:
            return False, f"row_{index}_entry_type", []
        if not is_path_safe_token(term.get("served_id")):
            return False, f"row_{index}_served_id", []
        validated.append(term)
    return True, None, validated


def derive_per_query_receipt_coverage(
    *,
    route_evidence: Sequence[Mapping[str, Any]],
    run_header: Mapping[str, Any],
    claim_context: Mapping[str, Any],
    ledger_terminals: Sequence[Mapping[str, Any]],
    resolve_receipt: Callable[[str], Optional[Mapping[str, Any]]],
    verify_receipt: Callable[[Mapping[str, Any]], bool],
    content_hash: Callable[[Mapping[str, Any]], str],
    receipt_query_digest: Callable[[Mapping[str, Any]], Optional[str]],
    pending_append_failures: int = 0,
    chain_ok: bool = False,
) -> PerQueryCoverageReport:
    """Re-derive per-query receipt coverage. Pure; every clause independent of producer flags.

    ``route_evidence`` rows are the exact S2 shape {served_id, query_digest, normalization_version}
    (the adapter projects a W1B record + carries the run normalization_version per row). The corpus
    digest is recomputed internally; malformed evidence fails closed.
    """
    evidence_ok, evidence_reason, served_to_query = _validate_route_evidence(route_evidence)
    corpus_query_digests = set(served_to_query.values())
    corpus_served_ids = set(served_to_query)
    corpus_size = len(corpus_served_ids)

    context_ok = _context_matches(run_header, claim_context)

    # RCO-1-C + blocker 2: recompute the canonical corpus digest INTERNALLY and require the header's
    # corpus_digest to equal it. No caller-supplied digest function is trusted.
    actual_corpus_digest = (
        canonical_corpus_digest(corpus_query_digests) if evidence_ok else None
    )
    corpus_bound = bool(evidence_ok and context_ok and actual_corpus_digest == run_header.get("corpus_digest"))

    terminal_evidence_ok, terminal_reason, validated_terminals = (
        _validate_ledger_terminals(ledger_terminals)
    )
    pending_failures_ok = (
        type(pending_append_failures) is int and pending_append_failures >= 0
    )
    validated_pending_append_failures = (
        pending_append_failures if pending_failures_ok else 0
    )
    validated_chain_ok = type(chain_ok) is bool and chain_ok

    # served_id -> terminal entries (catch >1 terminal / gap-then-bound: MF-3 per served_id).
    terminals_by_served: dict[str, list[Mapping[str, Any]]] = {}
    orphan_terminals = 0
    for term in validated_terminals:
        sid = term["served_id"]
        if sid not in served_to_query:
            orphan_terminals += 1
            continue
        terminals_by_served.setdefault(sid, []).append(term)

    duplicate_terminal = sum(1 for terms in terminals_by_served.values() if len(terms) > 1)
    served_ids_by_receipt_ref: dict[str, set[str]] = {}
    for term in validated_terminals:
        if term["entry_type"] == _RECEIPT_TERMINAL:
            served_ids_by_receipt_ref.setdefault(term["receipt_ref"], set()).add(
                term["served_id"]
            )
    duplicate_receipt_refs = {
        ref for ref, served_ids in served_ids_by_receipt_ref.items()
        if len(served_ids) > 1
    }

    # A repeated query may have multiple served events. Count query-level duplicates only when
    # terminals exceed the number of distinct served_ids for that query; one terminal per served
    # event is valid and is checked independently below.
    served_events_per_query: dict[str, int] = {}
    for q in served_to_query.values():
        served_events_per_query[q] = served_events_per_query.get(q, 0) + 1
    terminals_per_query: dict[str, int] = {}
    for sid, terms in terminals_by_served.items():
        q = served_to_query.get(sid)
        if q is not None:
            terminals_per_query[q] = terminals_per_query.get(q, 0) + len(terms)
    duplicate_query_terminal = sum(
        1
        for q, count in terminals_per_query.items()
        if count > served_events_per_query.get(q, 0)
    )

    bound_served_ids: set[str] = set()
    gapped_served_ids: set[str] = set()
    missing_served_ids: set[str] = set()
    forged_served_ids: set[str] = set()

    if terminal_evidence_ok:
        for sid, q in served_to_query.items():
            terms = terminals_by_served.get(sid, [])
            if not terms:
                missing_served_ids.add(sid)
                continue
            if len(terms) > 1:
                forged_served_ids.add(sid)
                continue
            term = terms[0]
            if term["entry_type"] == _GAP_TERMINAL:
                gapped_served_ids.add(sid)
                continue
            ref = term["receipt_ref"]
            if ref in duplicate_receipt_refs:
                forged_served_ids.add(sid)
                continue
            try:
                receipt = resolve_receipt(ref)
            except Exception:
                forged_served_ids.add(sid)
                continue
            if not isinstance(receipt, Mapping):             # MF-1: nonexistent / not durable
                forged_served_ids.add(sid)
                continue
            try:
                if content_hash(receipt) != ref:             # MF-1a: not content-addressed
                    forged_served_ids.add(sid)
                    continue
                if verify_receipt(receipt) is not True:      # MF-1a: fails verify_magma_receipt
                    forged_served_ids.add(sid)
                    continue
                if receipt_query_digest(receipt) != q:       # MF-1b: wrong query binding
                    forged_served_ids.add(sid)
                    continue
            except Exception:
                forged_served_ids.add(sid)
                continue
            bound_served_ids.add(sid)

    verified_bound = len(bound_served_ids)
    gapped = len(gapped_served_ids)
    missing = len(missing_served_ids)
    forged_or_unbound = len(forged_served_ids)

    reason: Optional[str] = None
    if not evidence_ok:
        reason = "malformed_route_evidence:%s" % evidence_reason
    elif not context_ok:
        reason = "stale_or_wrong_measurement_context"
    elif not corpus_bound:
        reason = "corpus_digest_unbound_from_route_evidence"
    elif corpus_size == 0:
        reason = "empty_corpus"
    elif not pending_failures_ok:
        reason = "malformed_pending_append_failures"
    elif validated_pending_append_failures != 0:
        reason = "pending_append_failures:%d" % validated_pending_append_failures
    elif not validated_chain_ok:
        reason = "ledger_chain_broken"
    elif not terminal_evidence_ok:
        reason = "malformed_ledger_terminal:%s" % terminal_reason
    elif orphan_terminals != 0:
        reason = "orphan_terminals:%d" % orphan_terminals
    elif duplicate_terminal != 0:
        reason = "duplicate_terminal_per_served_id:%d" % duplicate_terminal
    elif duplicate_receipt_refs:
        reason = "duplicate_receipt_ref:%d" % len(duplicate_receipt_refs)
    elif duplicate_query_terminal != 0:
        reason = "duplicate_terminal_per_query:%d" % duplicate_query_terminal
    elif bound_served_ids != corpus_served_ids:
        reason = "bijection_unmet:bound=%d/corpus=%d(gap=%d,missing=%d,forged=%d)" % (
            verified_bound, corpus_size, gapped, missing, forged_or_unbound,
        )

    coverage_present = reason is None

    return PerQueryCoverageReport(
        coverage_present=coverage_present,
        corpus_size=corpus_size,
        verified_bound=verified_bound,
        gapped=gapped,
        missing=missing,
        forged_or_unbound=forged_or_unbound,
        duplicate_terminal=duplicate_terminal,
        duplicate_query_terminal=duplicate_query_terminal,
        orphan_terminals=orphan_terminals,
        evidence_ok=evidence_ok,
        context_ok=context_ok,
        corpus_bound=corpus_bound,
        pending_append_failures=validated_pending_append_failures,
        chain_ok=validated_chain_ok,
        reason=reason,
    )
