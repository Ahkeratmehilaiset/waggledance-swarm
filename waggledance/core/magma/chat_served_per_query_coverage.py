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

The pure reducer keeps injected seams (``resolve_receipt`` / ``verify_receipt`` /
``content_hash`` / ``receipt_query_digest``) so the forgery matrix is exercised directly.  A
default-off artifact adapter primitive below can bind a manually anchored ledger window to the
canonical offline verifier.  No trusted P0 window/index wrapper or non-test production caller
exists yet; this module does not claim runtime production wiring.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Callable, Mapping, NamedTuple, Optional, Sequence

from tools.verify_magma_receipt import (
    verify_manifest as _CANONICAL_VERIFY_MAGMA_MANIFEST,
)
from waggledance.core.magma.canonical import sha256_digest
from waggledance.core.magma.chat_query_route_evidence import NORMALIZATION_VERSION
from waggledance.core.magma.chat_served_ledger import (
    GAP_REASONS,
    GAP_TERMINAL,
    RECEIPT_TERMINAL,
    SERVED_PENDING,
    is_ledger_hash,
    is_path_safe_token,
    verify_entry_self,
    wellformed_reason,
)
from waggledance.core.magma.chat_served_receipt import (
    CHAIN_ID as CHAT_SERVED_CHAIN_ID,
    PAYLOAD_VERSION as CHAT_SERVED_PAYLOAD_VERSION,
    validate_chat_served_payload,
)

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

_MANIFEST_KEYS = frozenset({"chain_id", "entries"})
_MANIFEST_ENTRY_KEYS = frozenset({"receipt", "payload", "evaluation_result"})


class ReceiptManifestIndex(NamedTuple):
    """Immutable, ledger-window-bound index of receipt manifests.

    The future P0 window wrapper owns discovery of the complete manifest set.  S2
    accepts only an index bound to the exact byte offsets and ledger heads it reads;
    it never accepts a caller-projected ``receipt_ref -> payload`` map.
    """

    ledger_start_offset: int
    ledger_final_offset: int
    ledger_start_prev_hash: str
    ledger_final_head: str
    manifest_paths: tuple[str | os.PathLike[str], ...]


class _LedgerSegmentSnapshot(NamedTuple):
    entries: tuple[dict[str, Any], ...]
    raw_bytes: bytes
    digest: str


class _ContainedFileSnapshot(NamedTuple):
    lexical_path: Path
    resolved_path: Path
    relative_to: Path
    identity: tuple[int, int]
    raw_bytes: bytes


class _VerifiedReceiptBundle(NamedTuple):
    snapshots: tuple[_ContainedFileSnapshot, ...]


class _VerifiedReceiptWindow(NamedTuple):
    receipts: dict[str, Mapping[str, Any]]
    root_lexical: Path
    root_resolved: Path
    root_identity: tuple[int, int]
    bundles: tuple[_VerifiedReceiptBundle, ...]


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


def derive_per_query_receipt_coverage_from_artifacts(
    *,
    ledger_path: str | os.PathLike[str],
    receipt_root: str | os.PathLike[str],
    receipt_manifest_index: ReceiptManifestIndex,
    run_header: Mapping[str, Any],
    claim_context: Mapping[str, Any],
    verify_manifest: Callable[[Path], Mapping[str, Any]],
    pending_append_failures: int = 0,
) -> PerQueryCoverageReport:
    """Bind one manually anchored ledger window to exact receipt artifacts.

    This is a default-off adapter primitive above the pure reducer, awaiting a
    trusted P0 window/index wrapper.  It has no non-test production caller.  The
    byte offsets and boundary heads select completeness; rows are always read from
    the hash-chained ledger itself.  The supplied manifest index is all-or-nothing
    and must resolve to exactly the receipt refs in that same window.

    Any I/O, shape, containment, mutation, verifier, or binding failure is reduced
    to the existing fixed report schema.  No path, payload, raw query/response, or
    exception text can enter the returned report.
    """
    try:
        normalized_ledger_path = _normalize_path_input(ledger_path)
        normalized_receipt_root = _normalize_path_input(receipt_root)
        normalized_index = _normalize_manifest_index(receipt_manifest_index)
    except Exception:  # noqa: BLE001 -- hostile PathLike/index must fail closed
        normalized_index = None
    if normalized_index is None:
        return _adapter_failure_report(
            run_header=run_header,
            claim_context=claim_context,
            pending_append_failures=pending_append_failures,
        )

    try:
        ledger_snapshot = _read_complete_ledger_segment(
            normalized_ledger_path,
            start_offset=normalized_index.ledger_start_offset,
            final_offset=normalized_index.ledger_final_offset,
            start_prev_hash=normalized_index.ledger_start_prev_hash,
            final_head=normalized_index.ledger_final_head,
        )
    except Exception:  # noqa: BLE001 -- ledger evidence failures are fail-closed
        ledger_snapshot = None
    if ledger_snapshot is None:
        return _adapter_failure_report(
            run_header=run_header,
            claim_context=claim_context,
            pending_append_failures=pending_append_failures,
        )

    route_evidence: list[Mapping[str, Any]] = []
    ledger_terminals: list[Mapping[str, Any]] = []
    for entry in ledger_snapshot.entries:
        entry_type = entry["entry_type"]
        if entry_type == SERVED_PENDING:
            metadata = entry["metadata"]
            route_evidence.append(
                {
                    "served_id": entry["served_id"],
                    "query_digest": metadata.get("query_digest"),
                    "normalization_version": metadata.get(
                        "normalization_version"
                    ),
                }
            )
        elif entry_type == RECEIPT_TERMINAL:
            ledger_terminals.append(
                {
                    "entry_type": _RECEIPT_TERMINAL,
                    "served_id": entry["served_id"],
                    "receipt_ref": entry["receipt_ref"],
                }
            )
        elif entry_type == GAP_TERMINAL:
            ledger_terminals.append(
                {
                    "entry_type": _GAP_TERMINAL,
                    "served_id": entry["served_id"],
                    "gap_reason": entry["gap_reason"],
                }
            )

    expected_receipt_refs = {
        terminal["receipt_ref"]
        for terminal in ledger_terminals
        if terminal["entry_type"] == _RECEIPT_TERMINAL
    }
    try:
        receipt_window = _load_window_receipt_index(
            receipt_root=normalized_receipt_root,
            manifest_paths=normalized_index.manifest_paths,
            expected_receipt_refs=expected_receipt_refs,
            verify_manifest=verify_manifest,
        )
    except Exception:  # noqa: BLE001 -- injected verifier/artifact I/O is evidence
        receipt_window = None
    # The index is atomic evidence: one poisoned/missing/orphan artifact invalidates
    # every resolution rather than letting a caller selectively retain a passing row.
    resolved = receipt_window.receipts if receipt_window is not None else {}

    # Receipt verification can run arbitrary injected code.  Re-read and fully
    # revalidate the exact selected ledger bytes after it finishes, immediately
    # before publishing chain_ok=True, so callback-time mutation cannot authorize a
    # stale denominator/terminal projection.
    try:
        final_ledger_snapshot = _read_complete_ledger_segment(
            normalized_ledger_path,
            start_offset=normalized_index.ledger_start_offset,
            final_offset=normalized_index.ledger_final_offset,
            start_prev_hash=normalized_index.ledger_start_prev_hash,
            final_head=normalized_index.ledger_final_head,
        )
    except Exception:  # noqa: BLE001 -- final ledger evidence is fail-closed
        final_ledger_snapshot = None
    if (
        final_ledger_snapshot is None
        or final_ledger_snapshot.digest != ledger_snapshot.digest
        or final_ledger_snapshot.raw_bytes != ledger_snapshot.raw_bytes
        or final_ledger_snapshot.entries != ledger_snapshot.entries
    ):
        return _adapter_failure_report(
            run_header=run_header,
            claim_context=claim_context,
            pending_append_failures=pending_append_failures,
        )

    # The ledger read above cannot invoke injected code.  Revalidate the retained
    # evidence for EVERY bundle one final time after all supplied callbacks and
    # immediately before the pure reducer.  This pass never calls the supplied
    # verifier seam; it relies only on the exact bytes canonically verified earlier
    # plus live no-follow containment and OS identity.
    if (
        receipt_window is not None
        and not _verified_receipt_window_still_exact(receipt_window)
    ):
        return _adapter_failure_report(
            run_header=run_header,
            claim_context=claim_context,
            pending_append_failures=pending_append_failures,
        )

    return derive_per_query_receipt_coverage(
        route_evidence=route_evidence,
        run_header=run_header,
        claim_context=claim_context,
        ledger_terminals=ledger_terminals,
        resolve_receipt=resolved.get,
        verify_receipt=lambda artifact: artifact.get("verified") is True,
        content_hash=lambda artifact: sha256_digest(artifact["receipt"]),
        receipt_query_digest=lambda artifact: artifact.get("query_digest"),
        pending_append_failures=pending_append_failures,
        chain_ok=True,
    )


def _adapter_failure_report(
    *,
    run_header: Mapping[str, Any],
    claim_context: Mapping[str, Any],
    pending_append_failures: int,
) -> PerQueryCoverageReport:
    """Return the pure reducer's fixed fail-closed shape without error detail."""
    return derive_per_query_receipt_coverage(
        route_evidence=({},),
        run_header=run_header,
        claim_context=claim_context,
        ledger_terminals=(),
        resolve_receipt=lambda _ref: None,
        verify_receipt=lambda _receipt: False,
        content_hash=lambda _receipt: "",
        receipt_query_digest=lambda _receipt: None,
        pending_append_failures=pending_append_failures,
        chain_ok=False,
    )


def _normalize_path_input(path: str | os.PathLike[str]) -> str:
    normalized = os.fspath(path)
    if not isinstance(normalized, str) or not normalized:
        raise ValueError("path input must normalize to a nonempty string")
    return normalized


def _normalize_manifest_index(
    index: object,
) -> ReceiptManifestIndex | None:
    if not isinstance(index, ReceiptManifestIndex):
        return None
    if (
        type(index.ledger_start_offset) is not int
        or type(index.ledger_final_offset) is not int
        or index.ledger_start_offset < 0
        or index.ledger_final_offset <= index.ledger_start_offset
    ):
        return None
    if not is_ledger_hash(index.ledger_start_prev_hash):
        return None
    if not is_ledger_hash(index.ledger_final_head):
        return None
    if not isinstance(index.manifest_paths, tuple):
        return None
    normalized_paths = tuple(
        _normalize_path_input(path) for path in index.manifest_paths
    )
    return ReceiptManifestIndex(
        ledger_start_offset=index.ledger_start_offset,
        ledger_final_offset=index.ledger_final_offset,
        ledger_start_prev_hash=index.ledger_start_prev_hash,
        ledger_final_head=index.ledger_final_head,
        manifest_paths=normalized_paths,
    )


def _read_complete_ledger_segment(
    ledger_path: str | os.PathLike[str],
    *,
    start_offset: int,
    final_offset: int,
    start_prev_hash: str,
    final_head: str,
) -> _LedgerSegmentSnapshot | None:
    """Read stable bytes at exact line boundaries and verify the selected chain."""
    path = Path(ledger_path)
    first = _read_ledger_slice(path, start_offset, final_offset)
    second = _read_ledger_slice(path, start_offset, final_offset)
    if first != second:
        return None
    segment = first
    if not segment or not segment.endswith(b"\n"):
        return None

    raw_lines = segment[:-1].split(b"\n")
    if not raw_lines or any(not raw_line for raw_line in raw_lines):
        return None

    entries: list[dict[str, Any]] = []
    previous = start_prev_hash
    for raw_line in raw_lines:
        value = _strict_json_bytes(raw_line)
        if not isinstance(value, dict):
            return None
        if wellformed_reason(value) is not None or not verify_entry_self(value):
            return None
        if value.get("prev_ledger_hash") != previous:
            return None
        previous = value["entry_hash"]
        entries.append(value)
    if previous != final_head:
        return None
    return _LedgerSegmentSnapshot(
        entries=tuple(entries),
        raw_bytes=segment,
        digest="sha256:" + hashlib.sha256(segment).hexdigest(),
    )


def _read_ledger_slice(path: Path, start_offset: int, final_offset: int) -> bytes:
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        if final_offset > handle.tell():
            raise ValueError("ledger boundary beyond file")
        if start_offset:
            handle.seek(start_offset - 1)
            if handle.read(1) != b"\n":
                raise ValueError("ledger start is not a line boundary")
        handle.seek(start_offset)
        data = handle.read(final_offset - start_offset)
    if len(data) != final_offset - start_offset:
        raise ValueError("short ledger read")
    return data


def _strict_json_bytes(raw: bytes) -> Any:
    return json.loads(
        raw.decode("utf-8", errors="strict"),
        object_pairs_hook=_json_object_without_duplicates,
        parse_constant=_reject_json_constant,
    )


def _json_object_without_duplicates(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _reject_json_constant(_value: str) -> None:
    raise ValueError("non-finite JSON constant")


def _load_window_receipt_index(
    *,
    receipt_root: str | os.PathLike[str],
    manifest_paths: tuple[str | os.PathLike[str], ...],
    expected_receipt_refs: set[str],
    verify_manifest: Callable[[Path], Mapping[str, Any]],
) -> _VerifiedReceiptWindow | None:
    root_lexical, root_resolved = _receipt_root(receipt_root)
    root_identity = _stable_file_identity(os.lstat(root_lexical))
    manifests_seen: set[Path] = set()
    receipts: dict[str, Mapping[str, Any]] = {}
    verified_bundles: list[_VerifiedReceiptBundle] = []

    for indexed_path in manifest_paths:
        manifest_snapshot = _read_contained_file_snapshot(
            root_lexical,
            root_resolved,
            indexed_path,
            relative_to=root_lexical,
        )
        manifest_path = manifest_snapshot.resolved_path
        if manifest_path in manifests_seen:
            return None
        manifests_seen.add(manifest_path)

        manifest_bytes = manifest_snapshot.raw_bytes
        manifest = _strict_json_bytes(manifest_bytes)
        if not isinstance(manifest, dict) or set(manifest) != _MANIFEST_KEYS:
            return None
        if manifest.get("chain_id") != CHAT_SERVED_CHAIN_ID:
            return None
        manifest_entries = manifest.get("entries")
        if not isinstance(manifest_entries, list) or len(manifest_entries) != 1:
            return None
        manifest_entry = manifest_entries[0]
        if (
            not isinstance(manifest_entry, dict)
            or set(manifest_entry) != _MANIFEST_ENTRY_KEYS
        ):
            return None

        artifact_snapshots: dict[str, _ContainedFileSnapshot] = {}
        for field in _MANIFEST_ENTRY_KEYS:
            raw_path = manifest_entry.get(field)
            if not _safe_manifest_relative_path(raw_path):
                return None
            artifact_snapshot = _read_contained_file_snapshot(
                root_lexical,
                root_resolved,
                manifest_path.parent / Path(*str(raw_path).split("/")),
                relative_to=manifest_path.parent,
            )
            artifact_snapshots[field] = artifact_snapshot
        artifacts = {
            field: snapshot.resolved_path
            for field, snapshot in artifact_snapshots.items()
        }
        if (
            len(set(artifacts.values())) != len(_MANIFEST_ENTRY_KEYS)
            or manifest_path in artifacts.values()
        ):
            return None

        if not _verify_exact_bundle_snapshot(
            root_resolved=root_resolved,
            manifest_name=manifest_path.name,
            manifest_bytes=manifest_bytes,
            manifest_entry=manifest_entry,
            artifact_bytes={
                field: snapshot.raw_bytes
                for field, snapshot in artifact_snapshots.items()
            },
            verify_manifest=verify_manifest,
        ):
            return None
        bundle_snapshots = (
            manifest_snapshot,
            *artifact_snapshots.values(),
        )
        if not _bundle_snapshot_still_exact(
            root_lexical=root_lexical,
            root_resolved=root_resolved,
            root_identity=root_identity,
            snapshots=bundle_snapshots,
        ):
            return None

        receipt = _strict_json_bytes(
            artifact_snapshots["receipt"].raw_bytes
        )
        payload = _strict_json_bytes(
            artifact_snapshots["payload"].raw_bytes
        )
        evaluation = _strict_json_bytes(
            artifact_snapshots["evaluation_result"].raw_bytes
        )
        if not all(isinstance(value, dict) for value in (receipt, payload, evaluation)):
            return None
        if payload.get("payload_version") != CHAT_SERVED_PAYLOAD_VERSION:
            return None
        try:
            validate_chat_served_payload(payload)
        except (TypeError, ValueError, OverflowError):
            return None

        payload_digest = sha256_digest(payload)
        if receipt.get("canonical_payload_digest") != payload_digest:
            return None
        receipt_ref = sha256_digest(receipt)
        query_digest = payload.get("query_digest")
        if not isinstance(query_digest, str) or _SHA256.fullmatch(query_digest) is None:
            return None
        if receipt_ref in receipts:
            return None
        receipts[receipt_ref] = {
            "receipt": receipt,
            "query_digest": query_digest,
            "verified": True,
        }
        verified_bundles.append(
            _VerifiedReceiptBundle(snapshots=bundle_snapshots)
        )

    if set(receipts) != expected_receipt_refs:
        return None
    window = _VerifiedReceiptWindow(
        receipts=receipts,
        root_lexical=root_lexical,
        root_resolved=root_resolved,
        root_identity=root_identity,
        bundles=tuple(verified_bundles),
    )
    # A later verifier callback can mutate an earlier, already-checked bundle.
    # Close that cross-bundle gap before handing retained evidence to the caller.
    if not _verified_receipt_window_still_exact(window):
        return None
    return window


def _verify_exact_bundle_snapshot(
    *,
    root_resolved: Path,
    manifest_name: str,
    manifest_bytes: bytes,
    manifest_entry: Mapping[str, str],
    artifact_bytes: Mapping[str, bytes],
    verify_manifest: Callable[[Path], Mapping[str, Any]],
) -> bool:
    """Run the canonical verifier against an isolated copy of the exact bytes.

    The repository verifier is captured at module import and always runs first.
    The injected seam runs only after that canonical PASS and is therefore a
    veto/error-injection surface, never an alternate approval authority.  Scratch
    is created outside the evidence root, and scratch identity as well as bytes is
    compared again after both calls.
    """
    scratch_parent = _verification_scratch_parent(root_resolved)
    with tempfile.TemporaryDirectory(
        dir=str(scratch_parent),
        prefix=".wd-per-query-verify-",
    ) as temporary:
        snapshot_root = Path(temporary)
        snapshot_manifest = snapshot_root / manifest_name
        snapshot_manifest.write_bytes(manifest_bytes)
        for field in _MANIFEST_ENTRY_KEYS:
            relative_path = Path(*manifest_entry[field].split("/"))
            snapshot_path = snapshot_root / relative_path
            snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            snapshot_path.write_bytes(artifact_bytes[field])

        scratch_lexical, scratch_resolved = _receipt_root(snapshot_root)
        scratch_root_identity = _stable_file_identity(
            os.lstat(scratch_lexical)
        )
        scratch_snapshots = [
            _read_contained_file_snapshot(
                scratch_lexical,
                scratch_resolved,
                snapshot_manifest,
                relative_to=scratch_lexical,
            )
        ]
        for field in _MANIFEST_ENTRY_KEYS:
            scratch_snapshots.append(
                _read_contained_file_snapshot(
                    scratch_lexical,
                    scratch_resolved,
                    snapshot_root
                    / Path(*manifest_entry[field].split("/")),
                    relative_to=snapshot_manifest.parent,
                )
            )

        canonical_report = _CANONICAL_VERIFY_MAGMA_MANIFEST(
            snapshot_manifest
        )
        if not _strict_verifier_pass(canonical_report):
            return False

        supplied_report = verify_manifest(snapshot_manifest)
        if not _strict_verifier_pass(supplied_report):
            return False
        if not _bundle_snapshot_still_exact(
            root_lexical=scratch_lexical,
            root_resolved=scratch_resolved,
            root_identity=scratch_root_identity,
            snapshots=tuple(scratch_snapshots),
        ):
            return False
    return True


def _verification_scratch_parent(root_resolved: Path) -> Path:
    """Select an existing scratch parent that is not inside the evidence root."""
    candidates = (Path(tempfile.gettempdir()), root_resolved.parent)
    seen: set[Path] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            continue
        if resolved in seen or not resolved.is_dir():
            continue
        seen.add(resolved)
        try:
            resolved.relative_to(root_resolved)
        except ValueError:
            return resolved
    raise ValueError("no scratch parent outside receipt evidence root")


def _read_contained_file_snapshot(
    root_lexical: Path,
    root_resolved: Path,
    path: str | os.PathLike[str] | Path,
    *,
    relative_to: Path,
) -> _ContainedFileSnapshot:
    """Read one regular file while binding containment, identity, and bytes."""
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = relative_to / candidate
    candidate_lexical = Path(os.path.abspath(os.fspath(candidate)))
    resolved_before = _contained_existing_file(
        root_lexical,
        root_resolved,
        candidate_lexical,
        relative_to=relative_to,
    )
    lexical_before = os.lstat(candidate_lexical)
    if not stat.S_ISREG(lexical_before.st_mode):
        raise ValueError("receipt artifact is not a regular file")

    with candidate_lexical.open("rb") as handle:
        handle_before = os.fstat(handle.fileno())
        first = handle.read()
        handle.seek(0)
        second = handle.read()
        handle_after = os.fstat(handle.fileno())
    if first != second:
        raise ValueError("receipt artifact changed while read")

    resolved_after = _contained_existing_file(
        root_lexical,
        root_resolved,
        candidate_lexical,
        relative_to=relative_to,
    )
    lexical_after = os.lstat(candidate_lexical)
    identities = {
        _stable_file_identity(value)
        for value in (
            lexical_before,
            handle_before,
            handle_after,
            lexical_after,
        )
    }
    if len(identities) != 1 or resolved_after != resolved_before:
        raise ValueError("receipt artifact identity changed while read")
    return _ContainedFileSnapshot(
        lexical_path=candidate_lexical,
        resolved_path=resolved_before,
        relative_to=relative_to,
        identity=identities.pop(),
        raw_bytes=first,
    )


def _bundle_snapshot_still_exact(
    *,
    root_lexical: Path,
    root_resolved: Path,
    root_identity: tuple[int, int],
    snapshots: tuple[_ContainedFileSnapshot, ...],
) -> bool:
    """Revalidate live no-follow containment, file identity, and exact bytes."""
    try:
        current_root_lexical, current_root_resolved = _receipt_root(
            root_lexical
        )
        if (
            current_root_lexical != root_lexical
            or current_root_resolved != root_resolved
            or _stable_file_identity(os.lstat(current_root_lexical))
            != root_identity
        ):
            return False
        for expected in snapshots:
            current = _read_contained_file_snapshot(
                root_lexical,
                root_resolved,
                expected.lexical_path,
                relative_to=expected.relative_to,
            )
            if (
                current.lexical_path != expected.lexical_path
                or current.resolved_path != expected.resolved_path
                or current.identity != expected.identity
                or current.raw_bytes != expected.raw_bytes
            ):
                return False
    except Exception:  # noqa: BLE001 -- any revalidation failure is evidence failure
        return False
    return True


def _verified_receipt_window_still_exact(
    window: _VerifiedReceiptWindow,
) -> bool:
    """Revalidate every canonically verified live bundle without callbacks."""
    return _bundle_snapshot_still_exact(
        root_lexical=window.root_lexical,
        root_resolved=window.root_resolved,
        root_identity=window.root_identity,
        snapshots=tuple(
            snapshot
            for bundle in window.bundles
            for snapshot in bundle.snapshots
        ),
    )


def _stable_file_identity(value: os.stat_result) -> tuple[int, int]:
    """Return the OS file identity; fail closed when the filesystem has none."""
    identity = (int(value.st_dev), int(value.st_ino))
    if identity == (0, 0):
        raise ValueError("filesystem does not expose stable file identity")
    return identity


def _strict_verifier_pass(report: object) -> bool:
    if not isinstance(report, Mapping):
        return False
    try:
        return bool(
            report.get("ok") is True
            and type(report.get("receipt_count")) is int
            and report.get("receipt_count") == 1
            and isinstance(report.get("errors"), list)
            and report.get("errors") == []
        )
    except Exception:
        return False


def _receipt_root(
    receipt_root: str | os.PathLike[str],
) -> tuple[Path, Path]:
    root_lexical = Path(os.path.abspath(os.fspath(receipt_root)))
    if _path_is_link(root_lexical) or not root_lexical.is_dir():
        raise ValueError("invalid receipt root")
    return root_lexical, root_lexical.resolve(strict=True)


def _contained_existing_file(
    root_lexical: Path,
    root_resolved: Path,
    path: str | os.PathLike[str] | Path,
    *,
    relative_to: Path,
) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = relative_to / candidate
    candidate_lexical = Path(os.path.abspath(os.fspath(candidate)))
    try:
        relative = candidate_lexical.relative_to(root_lexical)
    except ValueError as exc:
        raise ValueError("artifact path escapes receipt root") from exc

    cursor = root_lexical
    if _path_is_link(cursor):
        raise ValueError("symlinked receipt root")
    for part in relative.parts:
        cursor = cursor / part
        if _path_is_link(cursor):
            raise ValueError("symlinked receipt artifact")

    resolved = candidate_lexical.resolve(strict=True)
    try:
        resolved.relative_to(root_resolved)
        resolved.relative_to(relative_to.resolve(strict=True))
    except ValueError as exc:
        raise ValueError("artifact path escapes configured boundary") from exc
    if not resolved.is_file():
        raise ValueError("receipt artifact is not a file")
    return resolved


def _safe_manifest_relative_path(value: object) -> bool:
    if not isinstance(value, str) or not value or "\\" in value:
        return False
    if PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute():
        return False
    return all(part not in {"", ".", ".."} for part in value.split("/"))


def _path_is_link(path: Path) -> bool:
    details = os.lstat(path)
    attributes = int(getattr(details, "st_file_attributes", 0))
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return stat.S_ISLNK(details.st_mode) or bool(attributes & reparse_flag)
