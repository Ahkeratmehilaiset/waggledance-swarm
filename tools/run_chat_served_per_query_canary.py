# SPDX-License-Identifier: BUSL-1.1
"""Named adversarial canary over per-query MAGMA receipt coverage — offline, deterministic.

Answers ONE question, reproducibly, on demand:

    does ``derive_per_query_receipt_coverage`` still FAIL CLOSED on every known forgery and
    omission, while still passing the honest repeated-query case (liveness)?

The unit matrix in ``tests/unit_core/test_chat_served_per_query_coverage.py`` already locks
this contract at test time. This canary is the same question asked as a RUNNABLE probe with
stable JSON on stdout, so a reviewer, a release checklist, or a future evidence channel can
re-derive the answer at a given head without reading pytest output.

WHAT IT IS NOT
--------------
Measurement-only, tagged ``measurement_not_a_correctness_gate``. It reads nothing from disk,
writes nothing, touches no runtime default, no routing, no claim-safety flag, and carries no
authority. A green canary is EVIDENCE that the fail-closed matrix holds; it is
necessary-not-sufficient and can never flip a claim. Any claim-safety change stays a separate
(a)-class operator-EXPLICIT step.

WHY THE CASES ARE THE CASES
---------------------------
The coverage question is a BIJECTION: every served event in the corpus binds to a real,
content-addressed, verifier-passing receipt for THAT query, exactly once, gap-free. A bijection
fails in two independent directions, and a matrix that only tests one is structurally blind to
the other:

  * SURJECTIVE — every served event is covered. Broken by: a terminal that never arrives
    (``missing_terminal``), or two terminals for one served event (``duplicate_terminal``).
  * INJECTIVE — no receipt is reused across served events. Broken by ``reused_receipt_ref``:
    two distinct served events citing ONE receipt_ref. Without this clause, one receipt would
    falsely cover two served events and the coverage count would over-report.

Plus the content-level forgeries, where the receipt exists but does not say what it claims:
``forged_content_hash`` (not content-addressed), ``wrong_query_binding`` (a genuine receipt for
a DIFFERENT query), and ``callback_exception`` (a seam that raises must not be read as a pass).

The honest case uses a REPEATED query across two distinct served events: repetition is normal
traffic, and it is the case most likely to be mistaken for a duplicate by a naive checker, so
liveness has to be proven precisely there.

Exact validation commands::

    python tools/run_chat_served_per_query_canary.py --json
    python -m pytest tests/tools/test_run_chat_served_per_query_canary.py -q

Exit code is 0 iff every case matched its expectation, 1 otherwise (so it is usable as a probe).
Offline; no model/cloud calls; no filesystem or network access.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.core.magma.chat_served_per_query_coverage import (  # noqa: E402
    MEASUREMENT_MARKER,
    NORMALIZATION_VERSION,
    RUN_CONTEXT_SCHEMA_VERSION,
    canonical_corpus_digest,
    derive_per_query_receipt_coverage,
)

CANARY_NAME = "wd.chat_served_per_query_canary.v1"
AUTHORITY = "measurement_only"

# Fixed, self-describing fixtures. No clock, no randomness, no environment read -> byte-stable JSON.
_QUERY_REPEATED = "sha256:" + ("1" * 64)
_QUERY_OTHER = "sha256:" + ("2" * 64)
_RECEIPT_A = "sha256:" + ("a" * 64)
_RECEIPT_B = "sha256:" + ("b" * 64)
_RECEIPT_OTHER = "sha256:" + ("c" * 64)
_HEAD_SHA = "0" * 40
_RUN_ID = "sha256:" + ("e" * 64)

_SERVED_ONE = "served1"
_SERVED_TWO = "served2"


class _ReceiptStore:
    """In-memory receipt seam. Mirrors the production seam shape (resolve/verify/content_hash/
    query_digest) so the forgery matrix is exercised through the real code path."""

    def __init__(self) -> None:
        self._receipts: dict[str, dict[str, Any]] = {}

    def add(
        self,
        ref: str,
        query_digest: str,
        *,
        content_hash: Optional[str] = None,
        verify_ok: bool = True,
    ) -> str:
        """A receipt is GENUINE when content_hash defaults to its own ref and it verifies."""
        self._receipts[ref] = {
            "_content_hash": ref if content_hash is None else content_hash,
            "_verify_ok": verify_ok,
            "_query_digest": query_digest,
        }
        return ref

    def resolve(self, ref: str) -> Optional[Mapping[str, Any]]:
        return self._receipts.get(ref)

    @staticmethod
    def verify(receipt: Mapping[str, Any]) -> bool:
        return bool(receipt.get("_verify_ok"))

    @staticmethod
    def content_hash(receipt: Mapping[str, Any]) -> str:
        return str(receipt.get("_content_hash"))

    @staticmethod
    def query_digest(receipt: Mapping[str, Any]) -> Optional[str]:
        return receipt.get("_query_digest")


def _route_row(served_id: str, query_digest: str) -> dict[str, str]:
    return {
        "served_id": served_id,
        "query_digest": query_digest,
        "normalization_version": NORMALIZATION_VERSION,
    }


def _receipt_terminal(served_id: str, receipt_ref: str) -> dict[str, str]:
    return {"entry_type": "receipt", "served_id": served_id, "receipt_ref": receipt_ref}


def _context_for(route_evidence: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Bind the run header to the evidence via the module's OWN canonical recompute, so a case
    fails for the reason it names and never incidentally on an unbound corpus digest."""
    return {
        "head_commit_sha": _HEAD_SHA,
        "corpus_digest": canonical_corpus_digest([row["query_digest"] for row in route_evidence]),
        "schema_version": RUN_CONTEXT_SCHEMA_VERSION,
        "normalization_version": NORMALIZATION_VERSION,
        "run_id": _RUN_ID,
    }


def _raising_seam(_: Any) -> Any:
    raise RuntimeError("canary: receipt seam failure")


def _repeated_query_evidence() -> list[dict[str, str]]:
    """The honest shape: ONE repeated query served TWICE -> two distinct served events."""
    return [
        _route_row(_SERVED_ONE, _QUERY_REPEATED),
        _route_row(_SERVED_TWO, _QUERY_REPEATED),
    ]


def _derive(
    route_evidence: Sequence[Mapping[str, Any]],
    terminals: Sequence[Mapping[str, Any]],
    store: _ReceiptStore,
    *,
    resolve_receipt: Optional[Callable[[str], Any]] = None,
) -> Any:
    context = _context_for(route_evidence)
    return derive_per_query_receipt_coverage(
        route_evidence=route_evidence,
        run_header=context,
        claim_context=context,
        ledger_terminals=terminals,
        resolve_receipt=store.resolve if resolve_receipt is None else resolve_receipt,
        verify_receipt=store.verify,
        content_hash=store.content_hash,
        receipt_query_digest=store.query_digest,
        pending_append_failures=0,
        chain_ok=True,
    )


def _case_valid_repeated_query() -> Any:
    """LIVENESS: a repeated query across two served events, each with its own genuine receipt,
    is honest coverage and MUST pass. If this goes red the matrix has become vacuous."""
    evidence = _repeated_query_evidence()
    store = _ReceiptStore()
    store.add(_RECEIPT_A, _QUERY_REPEATED)
    store.add(_RECEIPT_B, _QUERY_REPEATED)
    terminals = [
        _receipt_terminal(_SERVED_ONE, _RECEIPT_A),
        _receipt_terminal(_SERVED_TWO, _RECEIPT_B),
    ]
    return _derive(evidence, terminals, store)


def _case_missing_terminal() -> Any:
    """SURJECTIVE break: the second served event never reached a terminal."""
    evidence = _repeated_query_evidence()
    store = _ReceiptStore()
    store.add(_RECEIPT_A, _QUERY_REPEATED)
    terminals = [_receipt_terminal(_SERVED_ONE, _RECEIPT_A)]
    return _derive(evidence, terminals, store)


def _case_duplicate_terminal() -> Any:
    """SURJECTIVE break: one served event carries two terminals (lifecycle violation)."""
    evidence = _repeated_query_evidence()
    store = _ReceiptStore()
    store.add(_RECEIPT_A, _QUERY_REPEATED)
    store.add(_RECEIPT_B, _QUERY_REPEATED)
    terminals = [
        _receipt_terminal(_SERVED_ONE, _RECEIPT_A),
        _receipt_terminal(_SERVED_ONE, _RECEIPT_B),
        _receipt_terminal(_SERVED_TWO, _RECEIPT_B),
    ]
    return _derive(evidence, terminals, store)


def _case_reused_receipt_ref() -> Any:
    """INJECTIVE break: two distinct served events cite ONE receipt. Each served event has
    exactly one terminal, so the surjective clauses see nothing wrong -- only the uniqueness
    clause catches it. One receipt must never cover two served events."""
    evidence = _repeated_query_evidence()
    store = _ReceiptStore()
    store.add(_RECEIPT_A, _QUERY_REPEATED)
    terminals = [
        _receipt_terminal(_SERVED_ONE, _RECEIPT_A),
        _receipt_terminal(_SERVED_TWO, _RECEIPT_A),
    ]
    return _derive(evidence, terminals, store)


def _case_forged_content_hash() -> Any:
    """CONTENT forgery: the resolved receipt is not content-addressed by the ref that cites it."""
    evidence = _repeated_query_evidence()
    store = _ReceiptStore()
    store.add(_RECEIPT_A, _QUERY_REPEATED)
    store.add(_RECEIPT_B, _QUERY_REPEATED, content_hash=_RECEIPT_OTHER)
    terminals = [
        _receipt_terminal(_SERVED_ONE, _RECEIPT_A),
        _receipt_terminal(_SERVED_TWO, _RECEIPT_B),
    ]
    return _derive(evidence, terminals, store)


def _case_wrong_query_binding() -> Any:
    """BINDING forgery: a real, verifying, content-addressed receipt -- for a DIFFERENT query."""
    evidence = _repeated_query_evidence()
    store = _ReceiptStore()
    store.add(_RECEIPT_A, _QUERY_REPEATED)
    store.add(_RECEIPT_B, _QUERY_OTHER)
    terminals = [
        _receipt_terminal(_SERVED_ONE, _RECEIPT_A),
        _receipt_terminal(_SERVED_TWO, _RECEIPT_B),
    ]
    return _derive(evidence, terminals, store)


def _case_callback_exception() -> Any:
    """SEAM failure: a raising resolver must fail closed, never be read as a pass."""
    evidence = _repeated_query_evidence()
    store = _ReceiptStore()
    store.add(_RECEIPT_A, _QUERY_REPEATED)
    store.add(_RECEIPT_B, _QUERY_REPEATED)
    terminals = [
        _receipt_terminal(_SERVED_ONE, _RECEIPT_A),
        _receipt_terminal(_SERVED_TWO, _RECEIPT_B),
    ]
    return _derive(evidence, terminals, store, resolve_receipt=_raising_seam)


# Ordered matrix. The tuple is (name, expected_coverage_present, expected_reason_prefix, builder).
# expected_reason_prefix pins WHY a case fails: a case that fails for the wrong reason is a red
# canary, not a green one -- otherwise any unrelated breakage would masquerade as fail-closed.
_MATRIX: tuple[tuple[str, bool, Optional[str], Callable[[], Any]], ...] = (
    ("valid_repeated_query_two_served_events", True, None, _case_valid_repeated_query),
    ("missing_terminal", False, "bijection_unmet", _case_missing_terminal),
    ("duplicate_terminal", False, "duplicate_terminal_per_served_id", _case_duplicate_terminal),
    ("reused_receipt_ref", False, "duplicate_receipt_ref", _case_reused_receipt_ref),
    ("forged_content_hash", False, "bijection_unmet", _case_forged_content_hash),
    ("wrong_query_binding", False, "bijection_unmet", _case_wrong_query_binding),
    ("callback_exception", False, "bijection_unmet", _case_callback_exception),
)


def run_canary() -> dict[str, Any]:
    """Run the full matrix and return the deterministic report."""
    cases: list[dict[str, Any]] = []
    for name, expected_present, expected_reason_prefix, builder in _MATRIX:
        report = builder()
        reason = report.reason
        reason_ok = (
            reason is None
            if expected_reason_prefix is None
            else isinstance(reason, str) and reason.startswith(expected_reason_prefix)
        )
        passed = bool(report.coverage_present is expected_present and reason_ok)
        cases.append({
            "case": name,
            "expected_coverage_present": expected_present,
            "actual_coverage_present": bool(report.coverage_present),
            "expected_reason_prefix": expected_reason_prefix,
            "actual_reason": reason,
            "corpus_size": report.corpus_size,
            "verified_bound": report.verified_bound,
            "gapped": report.gapped,
            "missing": report.missing,
            "forged_or_unbound": report.forged_or_unbound,
            "duplicate_terminal": report.duplicate_terminal,
            "duplicate_query_terminal": report.duplicate_query_terminal,
            "orphan_terminals": report.orphan_terminals,
            "passed": passed,
        })

    failed = [case["case"] for case in cases if not case["passed"]]
    return {
        "canary": CANARY_NAME,
        "authority": AUTHORITY,
        "measurement_marker": MEASUREMENT_MARKER,
        "normalization_version": NORMALIZATION_VERSION,
        "run_context_schema_version": RUN_CONTEXT_SCHEMA_VERSION,
        "cases": cases,
        "summary": {
            "total": len(cases),
            "passed": len(cases) - len(failed),
            "failed": len(failed),
            "failed_cases": failed,
        },
        "all_expected": not failed,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Adversarial fail-closed canary over per-query MAGMA receipt coverage "
                    "(measurement-only; never a claim-safety gate).",
    )
    parser.add_argument("--json", action="store_true", help="emit the report as JSON (default)")
    parser.parse_args(argv)

    report = run_canary()
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["all_expected"] else 1


if __name__ == "__main__":  # pragma: no cover - CLI entry
    sys.exit(main())
