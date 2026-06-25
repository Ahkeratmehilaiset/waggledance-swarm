# SPDX-License-Identifier: BUSL-1.1
"""P4c corpus validator — re-derives every case's verdict from the LIVE checker.

The validator never trusts the corpus's stored expectation alone: it RUNS the real
``classify_change`` on each case body and re-derives the decision, then enforces:
  * NEGATIVE-ENFORCED cases must re-derive to ``operator_sign`` (a drop = a LOOSENING).
  * DOCUMENTED-RESIDUAL cases are recorded (auto_sign = still residual; operator_sign
    = an improvement — allowed, surfaced, never a failure).
  * No duplicate case_ids; CASES must cover the frozen MANIFEST exactly (no dropped
    or unmanifested ids); no enforced family with 0 cases; FAMILY_FLOOR honoured.
Raises CorpusViolation on any structural breach; returns a report otherwise.
"""
from __future__ import annotations

from collections import Counter


class CorpusViolation(AssertionError):
    pass


def _decision(classify_fn, body):
    """Run a case body through the live checker as a tests/ file; return its decision."""
    return classify_fn([{"path": "tests/_p4c_probe.py", "added": list(body), "removed": []}])


def validate_corpus(cases, manifest, classify_fn, *, family_floor=None):
    family_floor = family_floor or {}

    ids = [c["id"] for c in cases]
    dups = sorted({i for i, n in Counter(ids).items() if n > 1})
    if dups:
        raise CorpusViolation(f"duplicate case_ids: {dups}")

    present = set(ids)
    dropped = sorted(manifest - present)
    if dropped:
        raise CorpusViolation(f"manifest case_ids dropped from CASES (coverage shrink): {dropped}")
    unmanifested = sorted(present - manifest)
    if unmanifested:
        raise CorpusViolation(f"case_ids not in the frozen MANIFEST (update it deliberately): {unmanifested}")

    enforced = [c for c in cases if c["kind"] == "negative_enforced"]
    residual = [c for c in cases if c["kind"] == "documented_residual"]
    if not enforced:
        raise CorpusViolation("0 negative-enforced cases -> corpus asserts nothing")

    fam_counts = Counter(c["family"] for c in enforced)
    empty = [f for f, n in fam_counts.items() if n == 0]
    if empty:
        raise CorpusViolation(f"enforced family with 0 cases: {empty}")
    for fam, floor in family_floor.items():
        if fam_counts.get(fam, 0) < floor:
            raise CorpusViolation(f"family '{fam}' has {fam_counts.get(fam, 0)} cases, floor is {floor}")

    enforced_fail, residual_now, improved = [], [], []
    for c in enforced:
        d = _decision(classify_fn, c["body"])
        if d != "operator_sign":
            enforced_fail.append((c["id"], d))
    for c in residual:
        d = _decision(classify_fn, c["body"])
        (improved if d == "operator_sign" else residual_now).append(c["id"])

    if enforced_fail:
        raise CorpusViolation(
            "LOOSENING: enforced vectors no longer route to operator_sign "
            f"(checker dropped a guard): {enforced_fail}")

    return {
        "total": len(cases),
        "enforced": len(enforced),
        "residual": len(residual),
        "families": dict(fam_counts),
        "residual_still_open": residual_now,
        "residual_improved_to_enforced": improved,
    }
