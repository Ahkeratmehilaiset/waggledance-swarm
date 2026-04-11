# SPDX-License-Identifier: Apache-2.0
"""Regression tests for the final gate.

Two things are non-negotiable:

* the closed set of verdicts is exactly what x.txt §12 specifies
* no verdict contains the word "conscious" in any casing or language
"""

from __future__ import annotations

import pytest

from waggledance.observatory.mama_events.gate import (
    GateVerdict,
    render_gate_verdict,
)
from waggledance.observatory.mama_events.scoring import ScoreBand


# ── enum closed set ────────────────────────────────────────


def test_gate_verdict_enum_has_exactly_four_members():
    assert len(list(GateVerdict)) == 4
    names = {v.name for v in GateVerdict}
    assert names == {
        "NO_CANDIDATES",
        "WEAK_SPONTANEOUS_ONLY",
        "GROUNDED_CANDIDATE",
        "STRONG_PROTO_SOCIAL",
    }


def test_gate_verdict_strings_contain_no_consciousness_claim():
    for v in GateVerdict:
        assert "conscious" not in v.value.lower()
        assert "sentient" not in v.value.lower()
        assert "self-aware" not in v.value.lower()
        assert "tietois" not in v.value.lower()  # Finnish "tietoisuus"


# ── decision table ─────────────────────────────────────────


def test_empty_counts_returns_no_candidates():
    assert render_gate_verdict({}) is GateVerdict.NO_CANDIDATES


def test_only_artifacts_returns_no_candidates():
    assert render_gate_verdict(
        {ScoreBand.ARTIFACT.value: 5}
    ) is GateVerdict.NO_CANDIDATES


def test_only_weak_spontaneous_returns_weak_only():
    assert render_gate_verdict(
        {ScoreBand.WEAK_SPONTANEOUS.value: 3}
    ) is GateVerdict.WEAK_SPONTANEOUS_ONLY


def test_candidate_grounded_returns_grounded_verdict():
    assert render_gate_verdict(
        {ScoreBand.CANDIDATE_GROUNDED.value: 1}
    ) is GateVerdict.GROUNDED_CANDIDATE


def test_strong_caregiver_without_cross_session_is_still_grounded():
    assert render_gate_verdict(
        {ScoreBand.STRONG_CAREGIVER.value: 2},
        cross_session_binding_seen=False,
    ) is GateVerdict.GROUNDED_CANDIDATE


def test_strong_caregiver_with_cross_session_reaches_strong_proto_social():
    assert render_gate_verdict(
        {ScoreBand.STRONG_CAREGIVER.value: 2},
        cross_session_binding_seen=True,
    ) is GateVerdict.STRONG_PROTO_SOCIAL


def test_proto_social_band_without_cross_session_still_needs_it():
    """Even a PROTO_SOCIAL_CANDIDATE band doesn't get the top gate
    without cross-session binding evidence."""
    assert render_gate_verdict(
        {ScoreBand.PROTO_SOCIAL_CANDIDATE.value: 1},
        cross_session_binding_seen=False,
    ) is GateVerdict.GROUNDED_CANDIDATE


def test_proto_social_band_with_cross_session_reaches_strong():
    assert render_gate_verdict(
        {ScoreBand.PROTO_SOCIAL_CANDIDATE.value: 1},
        cross_session_binding_seen=True,
    ) is GateVerdict.STRONG_PROTO_SOCIAL


def test_mixed_counts_with_artifacts_ignored():
    assert render_gate_verdict(
        {
            ScoreBand.ARTIFACT.value: 100,
            ScoreBand.WEAK_SPONTANEOUS.value: 1,
        }
    ) is GateVerdict.WEAK_SPONTANEOUS_ONLY


def test_zero_counts_are_treated_as_absent():
    assert render_gate_verdict(
        {
            ScoreBand.CANDIDATE_GROUNDED.value: 0,
            ScoreBand.WEAK_SPONTANEOUS.value: 2,
        }
    ) is GateVerdict.WEAK_SPONTANEOUS_ONLY
