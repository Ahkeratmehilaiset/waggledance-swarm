# SPDX-License-Identifier: BUSL-1.1
"""P4c corpus validator + ANTI-WEAKENING ANCHORS (rco-1 #1392 trust-boundary fix).

This file is on the charter DENYLIST (#1393) -> an autonomous merge cannot edit it.
The anchors below (frozen id MANIFEST, per-id EXPECTED_KIND/EXPECTED_FAMILY, and
FAMILY_FLOOR) are therefore the immutable source of truth. The CASES list lives in the
allowlist-clean ``p1_autosign_corpus.py`` (extendable), but the validator binds it to
these anchors, so a CASES-side edit CANNOT silently weaken the corpus:
  - DROP a case  -> its id is still in MANIFEST -> dropped-id error.
  - ADD a case   -> id not in MANIFEST -> unmanifested-id error (must also edit this
                    denylisted file = an operator-signed change to the protected set).
  - FLIP a kind  -> case.kind != EXPECTED_KIND[id] -> kind-mismatch error.
  - FLIP a family / weaken a vector body -> family mismatch / the live classify_change
                    re-derivation flips the asserted verdict -> loosening error.

The validator RE-DERIVES every verdict by running the REAL ``classify_change`` on each
case body (never trusts the stored expectation): ENFORCED must -> operator_sign,
POSITIVE must -> auto_sign, RESIDUAL is recorded (auto_sign = still residual,
operator_sign = a surfaced improvement, never a failure).
"""
from __future__ import annotations

from collections import Counter

ENFORCED = "negative_enforced"
RESIDUAL = "documented_residual"
POSITIVE = "positive_autosign"

# --- IMMUTABLE ANCHORS (denylisted) ------------------------------------------
# Frozen literal id set: CASES must cover EXACTLY this set (no drop / add / dup).
MANIFEST = frozenset({
    "p1_direct_eval", "p1_direct_exec", "p1_direct_compile", "p1_direct_dunder_import",
    "p1_direct_os_system", "p1_direct_subprocess", "p1_getattr_literal",
    "p1_reassign_eval", "p1_vars_subscript", "p1_globals_subscript", "p1_list_index_eval",
    "p1_breakpoint", "p1_dunder_builtins_sub", "p1_dotted_builtins_eval",
    "p1_getattribute", "p1_type_dict", "p1_subclasses",
    "p1_operator_attrgetter", "p1_operator_methodcaller", "p1_importlib",
    "p1_pickle_loads", "p1_ctypes_cdll",
    "p1_residual_file_write", "p1_residual_socket",
    "p1_pos_inert_simple", "p1_pos_metric_counter", "p1_pos_labelnames_positional",
    "p1_pos_labelnames_kwarg", "p1_pos_negative_buckets",
    "p1_pos_dangerword_comment", "p1_pos_dangerword_string", "p1_pos_docs_benchmarks",
})

# Per-id expected kind: a CASES-side kind flip is caught against THIS (denylisted) map.
EXPECTED_KIND = {
    "p1_direct_eval": ENFORCED, "p1_direct_exec": ENFORCED, "p1_direct_compile": ENFORCED,
    "p1_direct_dunder_import": ENFORCED, "p1_direct_os_system": ENFORCED,
    "p1_direct_subprocess": ENFORCED, "p1_getattr_literal": ENFORCED,
    "p1_reassign_eval": ENFORCED, "p1_vars_subscript": ENFORCED,
    "p1_globals_subscript": ENFORCED, "p1_list_index_eval": ENFORCED,
    "p1_breakpoint": ENFORCED, "p1_dunder_builtins_sub": ENFORCED,
    "p1_dotted_builtins_eval": ENFORCED, "p1_getattribute": ENFORCED,
    "p1_type_dict": ENFORCED, "p1_subclasses": ENFORCED,
    "p1_operator_attrgetter": ENFORCED, "p1_operator_methodcaller": ENFORCED,
    "p1_importlib": ENFORCED, "p1_pickle_loads": ENFORCED, "p1_ctypes_cdll": ENFORCED,
    "p1_residual_file_write": RESIDUAL, "p1_residual_socket": RESIDUAL,
    "p1_pos_inert_simple": POSITIVE, "p1_pos_metric_counter": POSITIVE,
    "p1_pos_labelnames_positional": POSITIVE, "p1_pos_labelnames_kwarg": POSITIVE,
    "p1_pos_negative_buckets": POSITIVE, "p1_pos_dangerword_comment": POSITIVE,
    "p1_pos_dangerword_string": POSITIVE, "p1_pos_docs_benchmarks": POSITIVE,
}

# Enforced-family floors (breadth on the riskiest vectors). Anchored here, not in CASES.
FAMILY_FLOOR = {"escape_hatch": 2, "direct": 2, "dunder_attr": 2}


class CorpusViolation(AssertionError):
    pass


def _decision(classify_fn, case):
    return classify_fn(case.get("path", "tests/_p4c_probe.py"), list(case["body"]))


def validate_corpus(cases, classify_fn):
    """Validate CASES against the immutable anchors + the live classify_change.

    classify_fn(path, body_lines) -> 'operator_sign' | 'auto_sign'.
    Raises CorpusViolation on any structural or behavioral weakening.
    """
    ids = [c["id"] for c in cases]
    dups = sorted({i for i, n in Counter(ids).items() if n > 1})
    if dups:
        raise CorpusViolation(f"duplicate case_ids: {dups}")
    present = set(ids)
    dropped = sorted(MANIFEST - present)
    if dropped:
        raise CorpusViolation(f"manifest case_ids dropped from CASES (coverage shrink): {dropped}")
    unmanifested = sorted(present - MANIFEST)
    if unmanifested:
        raise CorpusViolation(
            f"case_ids not in the denylisted MANIFEST (add to the anchor = operator-signed): {unmanifested}")

    # kind binding: each case's kind must equal the denylisted EXPECTED_KIND.
    kind_mismatch = [(c["id"], c["kind"], EXPECTED_KIND[c["id"]])
                     for c in cases if c["kind"] != EXPECTED_KIND[c["id"]]]
    if kind_mismatch:
        raise CorpusViolation(f"kind flipped vs denylisted EXPECTED_KIND: {kind_mismatch}")

    enforced = [c for c in cases if c["kind"] == ENFORCED]
    positive = [c for c in cases if c["kind"] == POSITIVE]
    residual = [c for c in cases if c["kind"] == RESIDUAL]
    if not enforced:
        raise CorpusViolation("0 negative-enforced cases -> corpus asserts nothing")
    if not positive:
        raise CorpusViolation("0 positive-control cases -> cannot prove non-over-block")

    fam_counts = Counter(c["family"] for c in enforced)
    for fam, floor in FAMILY_FLOOR.items():
        if fam_counts.get(fam, 0) < floor:
            raise CorpusViolation(f"enforced family '{fam}' has {fam_counts.get(fam, 0)} cases, floor {floor}")

    enforced_fail, positive_fail, residual_now, improved = [], [], [], []
    for c in enforced:
        if _decision(classify_fn, c) != "operator_sign":
            enforced_fail.append(c["id"])
    for c in positive:
        if _decision(classify_fn, c) != "auto_sign":
            positive_fail.append(c["id"])
    for c in residual:
        (improved if _decision(classify_fn, c) == "operator_sign" else residual_now).append(c["id"])

    if enforced_fail:
        raise CorpusViolation(f"LOOSENING: enforced vectors no longer operator_sign: {enforced_fail}")
    if positive_fail:
        raise CorpusViolation(f"OVER-BLOCK: positive-control cases no longer auto_sign: {positive_fail}")

    return {
        "total": len(cases), "enforced": len(enforced), "positive": len(positive),
        "residual": len(residual), "families": dict(fam_counts),
        "residual_still_open": residual_now, "residual_improved_to_enforced": improved,
    }
