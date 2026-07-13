"""S2 -- per-query MAGMA receipt coverage (measurement-only, re-derive-don't-trust).

This layer sits ON TOP of the existing chat_served ledger (served_id-keyed lifecycle) and
binds it to the S1 route-evidence CORPUS (query_digest-keyed) to answer one measurement
question, honestly and un-forgeably:

    per_query_receipt_coverage_present : is EVERY served query in the S1 corpus bound to a
    real, content-addressed, verifier-passing MAGMA receipt for THAT query -- exactly once,
    gap-free -- measured under the SAME code head / corpus / schema as the claim context?

It is the frozen W1A v3 contract (dual-RCO PASS) with rco-1 MF-1a folded. Every clause is
re-derived from primitive fields; NO producer-supplied `bound` flag is ever counted.
It NEVER reads or writes claim_safe: coverage_present is EVIDENCE, necessary-not-sufficient,
tagged ``measurement_not_a_correctness_gate``. Any claim_safe flip is a separate (a)-class
operator-EXPLICIT step on the full claim-window ladder, not anything this module can trigger.

Design: the derivation is a PURE function over explicit inputs + injected resolver seams
(``resolve_receipt`` / ``verify_receipt`` / ``content_hash`` / ``receipt_query_digest``), so
the adversarial matrix (forged / cross-query / nonexistent receipts, gap-then-bound,
stale-head, corpus-missing) is exercised directly without touching disk or the network.
The production wiring binds those seams to ``ChatServedReceiptSink`` + the receipt bundle
store + ``waggledance.core.magma.canonical.sha256_digest`` + ``tools.verify_magma_receipt``.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Mapping, NamedTuple, Optional, Sequence

SCHEMA_VERSION = "wd.chat_served_per_query_receipt_coverage.v1"
NORMALIZATION_VERSION = "wd.chat_query_normalization.v1"
MEASUREMENT_MARKER = "measurement_not_a_correctness_gate"

# Run-header fields that must match the claim context (rco-2 R2 stale/head binding).
_CONTEXT_KEYS = (
    "head_commit_sha",
    "corpus_digest",
    "schema_version",
    "normalization_version",
    "run_id",
)
_CONTEXT_KEYSET = frozenset(_CONTEXT_KEYS)
_FULL_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

# Ledger terminal entry types (mirrors chat_served_ledger; kept local so this pure layer
# does not import ledger internals just for two string constants).
_RECEIPT_TERMINAL = "receipt"
_GAP_TERMINAL = "gap"


class PerQueryCoverageReport(NamedTuple):
    """Re-derived, measurement-only per-query receipt coverage. Never a claim_safe trigger."""

    coverage_present: bool
    corpus_size: int
    verified_bound: int          # distinct corpus queries fully verified-bound
    gapped: int                  # corpus queries with a served gap
    missing: int                 # corpus queries with a served event but no terminal at all
    forged_or_unbound: int       # corpus queries with a receipt that failed MF-1a/MF-1b
    duplicate_terminal: int      # query digests with >1 terminal (MF-3 violation)
    orphan_terminals: int        # ledger terminals whose served_id is not in the corpus
    context_ok: bool             # run-header matches claim context (R2)
    pending_append_failures: int
    chain_ok: bool
    reason: Optional[str]        # first shortfall, or None when coverage_present
    schema_version: str = SCHEMA_VERSION
    measurement_marker: str = MEASUREMENT_MARKER


def _context_matches(run_header: Mapping[str, Any], claim_context: Mapping[str, Any]) -> bool:
    if frozenset(run_header) != _CONTEXT_KEYSET:
        return False
    head_commit_sha = run_header.get("head_commit_sha")
    corpus_digest = run_header.get("corpus_digest")
    run_id = run_header.get("run_id")
    if not isinstance(head_commit_sha, str) or not _FULL_COMMIT_RE.fullmatch(
        head_commit_sha
    ):
        return False
    if not isinstance(corpus_digest, str) or not _SHA256_RE.fullmatch(corpus_digest):
        return False
    if not isinstance(run_id, str) or not _SHA256_RE.fullmatch(run_id):
        return False
    if run_header.get("schema_version") != SCHEMA_VERSION:
        return False
    if run_header.get("normalization_version") != NORMALIZATION_VERSION:
        return False
    for key in _CONTEXT_KEYS:
        want = claim_context.get(key)
        got = run_header.get(key)
        # fail closed: a missing/None on either side never matches
        if want is None or got is None or want != got:
            return False
    return True


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
    chain_ok: bool = True,
) -> PerQueryCoverageReport:
    """Re-derive per-query receipt coverage. Pure; every clause independent of producer flags.

    ``route_evidence``  : the S1 corpus. Each item MUST carry ``served_id`` and ``query_digest``
                          (matches the W1B ``wd.chat_first_hop_corpus.v1`` record: ``id`` +
                          ``query_digest``; callers map ``id`` -> ``served_id``).
    ``ledger_terminals``: the chat_served ledger's TERMINAL entries (``receipt`` / ``gap``),
                          each with ``served_id`` and (for receipts) ``receipt_ref``.
    ``resolve_receipt`` : receipt_ref -> the durably-persisted receipt object, or None. Raw
                          resolution only; MF-1a verification happens HERE, not in the seam.
    ``verify_receipt``  : receipt -> passes verify_magma_receipt (own chain + wellformedness +
                          NA sentinels). ``content_hash`` : receipt -> its canonical content hash
                          (sha256_digest). ``receipt_query_digest`` : receipt -> its bound query.
    """
    corpus = {str(item.get("query_digest")) for item in route_evidence if item.get("query_digest")}
    corpus_size = len(corpus)

    context_ok = _context_matches(run_header, claim_context)

    # served_id -> query_digest from the corpus (the ONLY authoritative binding of a served
    # event to a query; a gap terminal carries no query_digest of its own).
    served_to_query: dict[str, str] = {}
    for item in route_evidence:
        sid = item.get("served_id")
        qd = item.get("query_digest")
        if sid and qd:
            served_to_query[str(sid)] = str(qd)

    # served_id -> list of terminal entry types (to catch >1 terminal / gap-then-bound: MF-3).
    terminals_by_served: dict[str, list[Mapping[str, Any]]] = {}
    orphan_terminals = 0
    for term in ledger_terminals:
        sid = str(term.get("served_id")) if term.get("served_id") is not None else None
        if sid is None:
            orphan_terminals += 1
            continue
        if sid not in served_to_query:
            orphan_terminals += 1  # a terminal for a served_id not in the corpus (MF-2 no-orphan)
            continue
        terminals_by_served.setdefault(sid, []).append(term)

    # MF-3 is query-scoped, not served-id-scoped. Multiple served events for the same
    # authoritative query still represent multiple terminal states for that query.
    terminal_count_by_query: dict[str, int] = {}
    for sid, terms in terminals_by_served.items():
        query_digest = served_to_query[sid]
        terminal_count_by_query[query_digest] = (
            terminal_count_by_query.get(query_digest, 0) + len(terms)
        )
    duplicate_terminal = sum(
        1 for count in terminal_count_by_query.values() if count > 1
    )

    # Classify each corpus query by the WORST state across all its served events.
    # A query is verified-bound ONLY if EVERY served event for it is a real, content-addressed,
    # verifier-passing receipt bound to THAT query. Any gap / missing / forged -> not bound
    # (honest, conservative; a gap can never be papered over by a sibling bound event).
    q_bound: dict[str, bool] = {q: True for q in corpus}
    q_gapped: set[str] = set()
    q_missing: set[str] = set()
    q_forged: set[str] = set()

    # queries whose served events appear in the corpus
    served_ids_for_query: dict[str, list[str]] = {}
    for sid, q in served_to_query.items():
        served_ids_for_query.setdefault(q, []).append(sid)

    for q in corpus:
        sids = served_ids_for_query.get(q, [])
        if not sids:
            # query present in corpus set but with no served_id mapping: treat as missing
            q_bound[q] = False
            q_missing.add(q)
            continue
        for sid in sids:
            terms = terminals_by_served.get(sid, [])
            if not terms:
                q_bound[q] = False
                q_missing.add(q)
                continue
            if len(terms) > 1:
                # >1 terminal for a served_id (MF-3): never clean
                q_bound[q] = False
                q_forged.add(q)
                continue
            term = terms[0]
            etype = term.get("entry_type")
            if etype == _GAP_TERMINAL:
                q_bound[q] = False
                q_gapped.add(q)
                continue
            if etype != _RECEIPT_TERMINAL:
                q_bound[q] = False
                q_forged.add(q)
                continue
            # RECEIPT terminal -> MF-1 / MF-1a / MF-1b: re-resolve + re-verify + re-bind.
            ref = term.get("receipt_ref")
            receipt = resolve_receipt(str(ref)) if ref is not None else None
            if receipt is None:                                   # nonexistent / not durable
                q_bound[q] = False
                q_forged.add(q)
                continue
            if content_hash(receipt) != str(ref):                 # MF-1a: not content-addressed
                q_bound[q] = False
                q_forged.add(q)
                continue
            if not verify_receipt(receipt):                       # MF-1a: fails verify_magma_receipt
                q_bound[q] = False
                q_forged.add(q)
                continue
            if receipt_query_digest(receipt) != q:                # MF-1b: bound to a DIFFERENT query
                q_bound[q] = False
                q_forged.add(q)
                continue
            # this served event is a genuine, query-bound receipt; keep q_bound[q] True unless
            # another served event for q downgrades it.

    verified_bound_set = {q for q in corpus if q_bound[q]}

    verified_bound = len(verified_bound_set)
    gapped = len(q_gapped)
    missing = len(q_missing)
    forged_or_unbound = len(q_forged)

    # Coverage present iff the bijection holds over the RE-DERIVED bound set and every gate clears.
    reason: Optional[str] = None
    if not context_ok:
        reason = "stale_or_wrong_measurement_context"
    elif corpus_size == 0:
        reason = "empty_corpus"
    elif pending_append_failures != 0:
        reason = "pending_append_failures:%d" % pending_append_failures
    elif not chain_ok:
        reason = "ledger_chain_broken"
    elif orphan_terminals != 0:
        reason = "orphan_terminals:%d" % orphan_terminals
    elif duplicate_terminal != 0:
        reason = "duplicate_terminal_per_query_digest:%d" % duplicate_terminal
    elif verified_bound_set != corpus:
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
        orphan_terminals=orphan_terminals,
        context_ok=context_ok,
        pending_append_failures=pending_append_failures,
        chain_ok=chain_ok,
        reason=reason,
    )
