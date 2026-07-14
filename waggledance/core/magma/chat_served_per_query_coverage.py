"""S2 -- per-query MAGMA receipt coverage (measurement-only, re-derive-don't-trust).

This layer sits ON TOP of the existing chat_served ledger (served_id-keyed lifecycle) and
binds it to the S1 route-evidence CORPUS (query_digest-keyed) to answer one measurement
question, honestly and un-forgeably:

    per_query_receipt_coverage_present : is EVERY served event in the S1 corpus bound to a
    real, content-addressed, verifier-passing MAGMA receipt for THAT event's query -- exactly
    once, gap-free -- measured under the SAME code head / corpus / schema as the claim context?

Frozen W1A v3 contract (dual-RCO PASS) + rco-1 MF-1a + the W1B cross-contract amendment, plus
A2 fail-closed contract hardening (v2):

  * The BIJECTION is keyed by UNIQUE served_id. Repeated query_digests are ADMITTED -- the same
    query served on N distinct events is legitimate production traffic, NOT a duplicate; each of
    its served events must independently bind to a receipt for that query. (The corpus IDENTITY
    -- and its digest -- is still the SET of unique query_digests.)
  * Every hash field is matched with ``re.fullmatch`` so a trailing-newline (``$`` matches before
    a final ``\\n``) or embedded control character can never sneak a "valid" digest/sha through.
  * ``chain_ok`` is a STRICT bool and DEFAULT-CLOSED: only the literal ``True`` clears the ledger
    chain; any truthy non-bool coercion (1, "yes", [x]) fails closed. An omitted ``chain_ok``
    fails closed.
  * ``ledger_terminals`` rows are EXACT-TYPED (per-entry_type keyset + value shapes); a receipt
    terminal must carry a canonical sha256 ``receipt_ref``. ``served_id`` is PATH-SAFE by charset.

Every clause is re-derived from primitive fields; NO producer-supplied value is a trust boundary:
the corpus digest is recomputed INTERNALLY with the canonical W1B algorithm (not a caller-supplied
function), and malformed route-evidence / terminals fail CLOSED (never silently filtered). It NEVER
reads or writes the claim-safety flag: coverage_present is EVIDENCE, necessary-not-sufficient, tagged
``measurement_not_a_correctness_gate``. Any claim-safety flip is a separate (a)-class operator-EXPLICIT
step, not anything this module can do.

The receipt resolution is still via injected seams (``resolve_receipt`` / ``verify_receipt`` /
``content_hash`` / ``receipt_query_digest``) so the forgery matrix is exercised directly; the
production wiring binds those to ``ChatServedReceiptSink`` + the receipt bundle store +
``waggledance.core.magma.canonical.sha256_digest`` + ``tools.verify_magma_receipt``.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Mapping, NamedTuple, Optional, Sequence

from waggledance.core.magma.canonical import sha256_digest

# The W1C coverage-report schema (this module's OUTPUT shape). Bumped to v2 for the A2
# served-id bijection + fail-closed hardening (report shape/semantics changed).
SCHEMA_VERSION = "wd.chat_served_per_query_receipt_coverage.v2"
# The S1 run-context / route-evidence schema (what a W1B header carries) -- pinned SEPARATELY
# from the coverage-report schema (lead/operator cross-contract blocker 1). Owned by A1 identity.
RUN_CONTEXT_SCHEMA_VERSION = "wd.chat_query_route_evidence.v1"
NORMALIZATION_VERSION = "wd.chat_query_normalization.v1"
# Domain separator for the canonical corpus digest (must equal W1B; golden vectors in PR #1518).
CORPUS_DIGEST_DOMAIN = "wd.chat_query_route_evidence.corpus_digest.v1"
MEASUREMENT_MARKER = "measurement_not_a_correctness_gate"

# Run-header fields that must match the claim context (rco-2 R2 stale/head binding).
_CONTEXT_KEYS = ("head_commit_sha", "corpus_digest", "schema_version", "normalization_version", "run_id")
# The exact per-row shape of an S1 route-evidence record this module consumes (adapter projects
# a W1B `wd.chat_first_hop_corpus.v1` record down to this shape). Exact keyset -> fail closed.
ROUTE_EVIDENCE_ROW_KEYS = frozenset({"served_id", "query_digest", "normalization_version"})

_RECEIPT_TERMINAL = "receipt"
_GAP_TERMINAL = "gap"
# Exact per-entry_type terminal keysets (A2: exact typed terminal rows).
_RECEIPT_TERMINAL_KEYS = frozenset({"entry_type", "served_id", "receipt_ref"})
_GAP_TERMINAL_KEYS = frozenset({"entry_type", "served_id", "gap_reason"})

# Bare patterns matched with re.fullmatch (A2: no `^`/`$`, so a trailing newline cannot pass).
_HEX40 = re.compile(r"[0-9a-f]{40}")            # full-40 lowercase-hex commit sha
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")    # canonical digest (corpus_digest, run_id, query_digest, receipt_ref)
_SERVED_ID = re.compile(r"[A-Za-z0-9_-]+")      # path-safe served_id (no `/`, `\`, `.`, whitespace, control chars)


def _is_hex40(value: Any) -> bool:
    return isinstance(value, str) and _HEX40.fullmatch(value) is not None


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _is_served_id(value: Any) -> bool:
    return isinstance(value, str) and _SERVED_ID.fullmatch(value) is not None


def _is_strict_nonneg_int(value: Any) -> bool:
    """A real int, not a bool (bool is an int subclass -> coercion vector), not negative."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


class PerQueryCoverageReport(NamedTuple):
    """Re-derived, measurement-only per-query receipt coverage. Never a claim-safety trigger."""

    coverage_present: bool
    corpus_size: int             # UNIQUE query digests (corpus identity)
    served_count: int            # number of served events (validated route-evidence rows)
    verified_bound: int          # served events bound to a real receipt for THEIR query
    gapped: int                  # served events whose terminal is a gap
    missing: int                 # served events with no terminal
    forged_or_unbound: int       # served events forged / cross-query / >1-terminal / non-receipt
    duplicate_terminal: int      # served_ids carrying >1 terminal (MF-3 per served_id, fail-close)
    repeated_query_digests: int  # query digests served on >1 event (OBSERVABILITY, NOT a failure)
    orphan_terminals: int        # terminals whose served_id is not in the corpus
    evidence_ok: bool            # every route-evidence row passed strict validation (no silent filter)
    terminals_ok: bool           # every ledger terminal row passed strict typed validation
    context_ok: bool             # run-header matches claim context (R2)
    corpus_bound: bool           # run-header corpus_digest == INTERNAL canonical recompute
    pending_append_failures: int
    chain_ok: bool               # STRICT: the input chain_ok was literally True
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
    if not _is_hex40(h.get("head_commit_sha")):
        return False
    if not _is_sha256(h.get("corpus_digest")):
        return False
    if not _is_sha256(h.get("run_id")):
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
    """Strict, fail-closed validation (blocker 3 + A2): exact row shape, nonempty PATH-SAFE UNIQUE
    served_ids, valid sha256 query_digests, matching per-row normalization_version. REPEATED
    query_digests are ADMITTED (same query on distinct served events is legitimate). Any malformed /
    duplicate-served / missing row -> (False, reason, {}). Never silently filter."""
    served_seen: set[str] = set()
    served_to_query: dict[str, str] = {}
    for item in route_evidence:
        if not isinstance(item, Mapping) or set(item.keys()) != ROUTE_EVIDENCE_ROW_KEYS:
            return False, "row_shape", {}
        sid = item.get("served_id")
        qd = item.get("query_digest")
        nv = item.get("normalization_version")
        if not _is_served_id(sid):
            return False, "served_id", {}
        if not _is_sha256(qd):
            return False, "query_digest", {}
        if nv != NORMALIZATION_VERSION:
            return False, "normalization_version", {}
        if sid in served_seen:
            return False, "duplicate_served_id", {}
        served_seen.add(sid)
        served_to_query[sid] = qd
    return True, None, served_to_query


def _validate_terminals(
    ledger_terminals: Sequence[Mapping[str, Any]],
) -> tuple[bool, Optional[str], list]:
    """Strict, fail-closed, EXACT-TYPED terminal validation (A2). Each row is a Mapping whose
    keyset + value shapes exactly match its entry_type: a receipt terminal is
    {entry_type, served_id, receipt_ref} with a canonical sha256 receipt_ref and a path-safe
    served_id; a gap terminal is {entry_type, served_id, gap_reason} with a nonempty gap_reason.
    Any other shape / type / unknown entry_type -> (False, reason, []). Never silently filter."""
    out: list = []
    for term in ledger_terminals:
        if not isinstance(term, Mapping):
            return False, "terminal_shape", []
        etype = term.get("entry_type")
        if etype == _RECEIPT_TERMINAL:
            if set(term.keys()) != _RECEIPT_TERMINAL_KEYS:
                return False, "receipt_terminal_shape", []
            if not _is_served_id(term.get("served_id")):
                return False, "terminal_served_id", []
            if not _is_sha256(term.get("receipt_ref")):
                return False, "terminal_receipt_ref", []
        elif etype == _GAP_TERMINAL:
            if set(term.keys()) != _GAP_TERMINAL_KEYS:
                return False, "gap_terminal_shape", []
            if not _is_served_id(term.get("served_id")):
                return False, "terminal_served_id", []
            gap_reason = term.get("gap_reason")
            if not (isinstance(gap_reason, str) and gap_reason):
                return False, "terminal_gap_reason", []
        else:
            return False, "terminal_entry_type", []
        out.append(term)
    return True, None, out


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
    (the adapter projects a W1B record + carries the run normalization_version per row); the SAME
    query_digest may recur across distinct served_ids. ``chain_ok`` is default-closed and strict.
    The corpus digest is recomputed internally; malformed evidence/terminals fail closed.
    """
    evidence_ok, evidence_reason, served_to_query = _validate_route_evidence(route_evidence)
    terminals_ok, terminals_reason, valid_terminals = _validate_terminals(ledger_terminals)

    corpus = set(served_to_query.values())          # UNIQUE query digests (corpus identity)
    corpus_size = len(corpus)
    served_count = len(served_to_query)             # number of served events

    context_ok = _context_matches(run_header, claim_context)

    # RCO-1-C + blocker 2: recompute the canonical corpus digest INTERNALLY and require the header's
    # corpus_digest to equal it. No caller-supplied digest function is trusted.
    actual_corpus_digest = canonical_corpus_digest(corpus) if evidence_ok else None
    corpus_bound = bool(evidence_ok and context_ok and actual_corpus_digest == run_header.get("corpus_digest"))

    # served_id -> terminal entries (MF-3 per served_id: exactly one terminal; orphan = not in corpus).
    terminals_by_served: dict[str, list] = {}
    orphan_terminals = 0
    for term in (valid_terminals if terminals_ok else []):
        sid = term.get("served_id")
        if sid not in served_to_query:
            orphan_terminals += 1
            continue
        terminals_by_served.setdefault(sid, []).append(term)

    duplicate_terminal = sum(1 for terms in terminals_by_served.values() if len(terms) > 1)

    # Observability only: query digests that recur across >1 served event (repeats are ADMITTED).
    served_ids_for_query: dict[str, list[str]] = {}
    for sid, q in served_to_query.items():
        served_ids_for_query.setdefault(q, []).append(sid)
    repeated_query_digests = sum(1 for sids in served_ids_for_query.values() if len(sids) > 1)

    # Per-SERVED-ID bijection: EVERY served event must bind to a real receipt for ITS query.
    verified_bound = 0
    gapped = 0
    missing = 0
    forged_or_unbound = 0
    for sid, q in served_to_query.items():
        terms = terminals_by_served.get(sid, [])
        if not terms:
            missing += 1
            continue
        if len(terms) > 1:                               # MF-3: >1 terminal for this served event
            forged_or_unbound += 1
            continue
        term = terms[0]
        etype = term.get("entry_type")
        if etype == _GAP_TERMINAL:
            gapped += 1
            continue
        if etype != _RECEIPT_TERMINAL:
            forged_or_unbound += 1
            continue
        ref = term.get("receipt_ref")
        receipt = resolve_receipt(str(ref)) if ref is not None else None
        if receipt is None:                              # MF-1: nonexistent / not durable
            forged_or_unbound += 1
            continue
        if content_hash(receipt) != str(ref):            # MF-1a: not content-addressed
            forged_or_unbound += 1
            continue
        if not verify_receipt(receipt):                  # MF-1a: fails verify_magma_receipt
            forged_or_unbound += 1
            continue
        if receipt_query_digest(receipt) != q:           # MF-1b: bound to a DIFFERENT query
            forged_or_unbound += 1
            continue
        verified_bound += 1

    reason: Optional[str] = None
    if not evidence_ok:
        reason = "malformed_route_evidence:%s" % evidence_reason
    elif not terminals_ok:
        reason = "malformed_terminals:%s" % terminals_reason
    elif not context_ok:
        reason = "stale_or_wrong_measurement_context"
    elif not corpus_bound:
        reason = "corpus_digest_unbound_from_route_evidence"
    elif corpus_size == 0:
        reason = "empty_corpus"
    elif not _is_strict_nonneg_int(pending_append_failures):
        reason = "malformed_pending_append_failures"
    elif pending_append_failures != 0:
        reason = "pending_append_failures:%d" % pending_append_failures
    elif chain_ok is not True:
        reason = "ledger_chain_broken"
    elif orphan_terminals != 0:
        reason = "orphan_terminals:%d" % orphan_terminals
    elif duplicate_terminal != 0:
        reason = "duplicate_terminal_per_served_id:%d" % duplicate_terminal
    elif verified_bound != served_count:
        reason = "bijection_unmet:bound=%d/served=%d(gap=%d,missing=%d,forged=%d)" % (
            verified_bound, served_count, gapped, missing, forged_or_unbound,
        )

    coverage_present = reason is None

    return PerQueryCoverageReport(
        coverage_present=coverage_present,
        corpus_size=corpus_size,
        served_count=served_count,
        verified_bound=verified_bound,
        gapped=gapped,
        missing=missing,
        forged_or_unbound=forged_or_unbound,
        duplicate_terminal=duplicate_terminal,
        repeated_query_digests=repeated_query_digests,
        orphan_terminals=orphan_terminals,
        evidence_ok=evidence_ok,
        terminals_ok=terminals_ok,
        context_ok=context_ok,
        corpus_bound=corpus_bound,
        pending_append_failures=pending_append_failures if _is_strict_nonneg_int(pending_append_failures) else 0,
        chain_ok=(chain_ok is True),
        reason=reason,
    )
