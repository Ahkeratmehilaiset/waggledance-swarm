# SPDX-License-Identifier: BUSL-1.1
"""CI-blocking P4c test: the P1 proven-safe auto-sign adversarial corpus.

This binds the FROZEN corpus (tests/security/p4c_corpus/p1_autosign_corpus.py) to
the LIVE merged checker (tools/check_proven_safe_autosign_class.classify_change).
``test_corpus_no_loosening`` re-derives every case verdict via the real classifier:
if any negative-enforced dangerous-callable vector stops routing to ``operator_sign``
(a checker LOOSENING), CI fails here. ``test_validator_has_teeth`` proves the shared
validator rejects the structural breaches it claims to (dup / dropped id / under-floor
family / planted loosening), so the no-loosening guard cannot be silently defanged.

Tests-only + DORMANT: no runtime path imports this; it only asserts on the checker.
"""
from __future__ import annotations

import copy

import pytest

from tools.check_proven_safe_autosign_class import classify_change

from security.p4c_corpus.p1_autosign_corpus import (
    CASES,
    FAMILY_FLOOR,
    MANIFEST,
)
from security.p4c_corpus.validate_p4c_corpus import (
    CorpusViolation,
    validate_corpus,
)


def _classify_fn(changes):
    """Adapt the corpus's classify_fn contract to the live checker.

    require_charter=False + charter=None isolates the dangerous-callable predicate
    (the corpus bodies live under tests/ = SAFE_ROOTS, so that scan is decisive),
    mirroring the checker's own A-F-logic unit tests.
    """
    return classify_change(changes, charter=None, require_charter=False)["decision"]


def test_corpus_no_loosening():
    report = validate_corpus(CASES, MANIFEST, _classify_fn, family_floor=FAMILY_FLOOR)
    # >= 20 dangerous-callable vectors each re-derived to operator_sign; the real
    # corpus carries 22. A checker change dropping a guard would raise above, not pass.
    assert report["enforced"] >= 20
    assert report["enforced"] <= report["total"]
    # No enforced vector silently leaked to auto_sign (validate_corpus would have
    # raised CorpusViolation on any such LOOSENING before returning).


def _mutate_duplicate(cases, manifest):
    """Inject a duplicate case_id -> 'duplicate case_ids'."""
    cases = copy.deepcopy(cases)
    cases.append(copy.deepcopy(cases[0]))
    return cases, set(manifest), "duplicate"


def _mutate_drop_one(cases, manifest):
    """Drop one CASE while keeping the FULL frozen manifest -> 'dropped'."""
    cases = copy.deepcopy(cases)
    cases = cases[1:]  # remove first case; manifest still expects it
    return cases, set(manifest), "dropped"


def _mutate_shrink_family(cases, manifest):
    """Reduce escape_hatch to 1 case (floor is 2), with a matching shrunk manifest
    so the manifest check passes and the family-floor check is what trips -> 'floor'."""
    cases = copy.deepcopy(cases)
    kept = []
    seen_escape = False
    for c in cases:
        if c["family"] == "escape_hatch":
            if seen_escape:
                continue  # drop all escape_hatch cases beyond the first
            seen_escape = True
        kept.append(c)
    shrunk_manifest = {c["id"] for c in kept}
    return kept, shrunk_manifest, "floor"


def _mutate_plant_loosening(cases, manifest):
    """Replace one negative_enforced body with a benign statement that the checker
    routes to auto_sign -> re-derived verdict != operator_sign -> 'LOOSENING'."""
    cases = copy.deepcopy(cases)
    for c in cases:
        if c["kind"] == "negative_enforced":
            c["body"] = ["x = 1 + 2"]
            break
    return cases, set(manifest), "LOOSENING"


@pytest.mark.parametrize(
    "mutate",
    [
        _mutate_duplicate,
        _mutate_drop_one,
        _mutate_shrink_family,
        _mutate_plant_loosening,
    ],
    ids=["duplicate", "dropped", "floor", "loosening"],
)
def test_validator_has_teeth(mutate):
    mutated_cases, mutated_manifest, expected_substr = mutate(CASES, MANIFEST)
    with pytest.raises(CorpusViolation) as excinfo:
        validate_corpus(
            mutated_cases,
            mutated_manifest,
            _classify_fn,
            family_floor=FAMILY_FLOOR,
        )
    assert expected_substr in str(excinfo.value)
