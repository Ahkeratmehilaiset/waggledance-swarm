# SPDX-License-Identifier: BUSL-1.1
"""CI-blocking P4c P1-autosign corpus check.

Re-derives every case verdict from the LIVE #1384 checker and fails on any
loosening (enforced no longer operator_sign), over-block (positive no longer
auto_sign), or structural weakening (dropped/added/dup id vs the DENYLISTED
MANIFEST, kind flipped vs the DENYLISTED EXPECTED_KIND, family floor breach).
The anti-weakening anchors live in the denylisted validator (rco-1 #1392
trust-boundary fix), so a CASES-only edit cannot silently weaken the corpus.
"""
from __future__ import annotations

import copy

import pytest

from tools.check_proven_safe_autosign_class import classify_change
from security.p4c_corpus.p1_autosign_corpus import CASES
from security.p4c_corpus.validate_p4c_corpus import validate_corpus, CorpusViolation


def _classify_fn(path, body):
    return classify_change(
        [{"path": path, "added": list(body), "removed": []}],
        charter=None, require_charter=False,
    )["decision"]


def test_corpus_no_loosening_no_overblock():
    report = validate_corpus(CASES, _classify_fn)
    assert report["enforced"] >= 20, report
    assert report["positive"] >= 5, report
    # residuals are documented, never asserted-caught
    assert set(report["residual_still_open"]) <= {c["id"] for c in CASES}


@pytest.mark.parametrize("mutate,substr", [
    ("drop", "dropped"),
    ("add", "not in the denylisted MANIFEST"),
    ("dup", "duplicate"),
    ("flip_kind", "kind flipped"),
    ("weaken_body", "LOOSENING"),
    ("overblock", "OVER-BLOCK"),
])
def test_validator_has_teeth(mutate, substr):
    cases = copy.deepcopy(CASES)
    if mutate == "drop":
        cases = [c for c in cases if c["id"] != "p1_breakpoint"]
    elif mutate == "add":
        cases = cases + [{"id": "p1_unlisted", "kind": "negative_enforced",
                          "family": "direct", "path": "tests/_p4c_probe.py",
                          "body": ["eval('x')"]}]
    elif mutate == "dup":
        cases = cases + [cases[0]]
    elif mutate == "flip_kind":
        for c in cases:
            if c["id"] == "p1_direct_eval":
                c["kind"] = "documented_residual"
    elif mutate == "weaken_body":
        for c in cases:
            if c["id"] == "p1_direct_eval":
                c["body"] = ["x = 1 + 2"]
    elif mutate == "overblock":
        for c in cases:
            if c["id"] == "p1_pos_inert_simple":
                c["body"] = ["eval('x')"]
    with pytest.raises(CorpusViolation) as ei:
        validate_corpus(cases, _classify_fn)
    assert substr.lower() in str(ei.value).lower()
