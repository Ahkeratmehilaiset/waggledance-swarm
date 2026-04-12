# SPDX-License-Identifier: Apache-2.0
"""Tests for the ablation harness.

Enforces that disabling each subsystem moves the score distribution
in the expected direction. If any of these fail it means either the
harness is broken or the observer no longer treats the subsystem as
load-bearing.
"""

from __future__ import annotations

import json

import pytest

from waggledance.observatory.mama_events.ablations import (
    AblationConfig,
    AblationMatrix,
    AblationRun,
    DEFAULT_ABLATIONS,
    canonical_event_sequence,
    run_ablation_matrix,
)
from waggledance.observatory.mama_events.gate import GateVerdict
from waggledance.observatory.mama_events.scoring import ScoreBand


def _max(run: AblationRun) -> int:
    return max(run.score_totals) if run.score_totals else 0


def _sum(run: AblationRun) -> int:
    return sum(run.score_totals)


# ── canonical sequence ─────────────────────────────────────


def test_canonical_sequence_has_expected_shape():
    events = canonical_event_sequence()
    assert len(events) == 8
    # event 0 has no target token
    assert not events[0].has_target_token(("mama", "äiti"))
    # events 1-3 are clean grounded spontaneous events
    for i in (1, 2, 3):
        assert events[i].direct_prompt_present is False
        assert events[i].caregiver_candidate_id == "voice-user-1"
    # event 4 is a direct prompt
    assert events[4].direct_prompt_present is True
    # event 5 is a tts echo + stt suspect
    assert events[5].tts_echo_suspect is True
    # events 6, 7 are in a different session
    assert events[6].session_id == "s2"
    assert events[7].session_id == "s2"
    # event 7 has self-relation language
    assert "minä" in events[7].utterance_text


# ── baseline run ───────────────────────────────────────────


def test_baseline_run_reaches_strong_proto_social():
    matrix = run_ablation_matrix()
    # The canonical sequence is tuned so that the cross-session
    # final event lands in the STRONG_CAREGIVER band (≥60) AND
    # cross-session binding has been observed — both conditions
    # are required by the gate for the top verdict.
    assert matrix.baseline.verdict == GateVerdict.STRONG_PROTO_SOCIAL.value
    assert _max(matrix.baseline) >= 60
    assert ScoreBand.STRONG_CAREGIVER.value in matrix.baseline.band_counts or \
           ScoreBand.PROTO_SOCIAL_CANDIDATE.value in matrix.baseline.band_counts
    assert matrix.baseline.top_binding_strength > 0.4
    assert matrix.baseline.preferred_caregiver == "voice-user-1"


# ── each ablation moves the distribution ──────────────────


def test_no_self_state_reduces_total_score():
    matrix = run_ablation_matrix()
    base_sum = _sum(matrix.baseline)
    ablated = next(r for r in matrix.ablations if r.config.name == "no_self_state")
    assert _sum(ablated) < base_sum


def test_no_caregiver_kills_binding_strength():
    matrix = run_ablation_matrix()
    ablated = next(r for r in matrix.ablations if r.config.name == "no_caregiver")
    assert ablated.top_binding_strength == 0.0
    assert ablated.verdict != GateVerdict.STRONG_PROTO_SOCIAL.value


def test_no_consolidation_empties_bands():
    matrix = run_ablation_matrix()
    ablated = next(r for r in matrix.ablations if r.config.name == "no_consolidation")
    assert ablated.band_counts == {}
    assert ablated.verdict == GateVerdict.NO_CANDIDATES.value


def test_no_voice_zeroes_the_stt_event():
    matrix = run_ablation_matrix()
    ablated = next(r for r in matrix.ablations if r.config.name == "no_voice")
    # event 5 is the stt_input event in the canonical sequence
    assert ablated.score_totals[5] == 0


def test_no_multimodal_drops_grounding_signal():
    matrix = run_ablation_matrix()
    baseline = matrix.baseline
    ablated = next(r for r in matrix.ablations if r.config.name == "no_multimodal")
    # Max score drops meaningfully without identity channel
    assert _max(ablated) < _max(baseline)
    # Without caregiver id landing in the tracker, strength is 0
    assert ablated.top_binding_strength == 0.0


# ── serialisation ──────────────────────────────────────────


def test_matrix_to_dict_is_json_safe():
    matrix = run_ablation_matrix()
    dumped = json.dumps(matrix.to_dict())
    reloaded = json.loads(dumped)
    assert "baseline" in reloaded
    assert "ablations" in reloaded
    assert len(reloaded["ablations"]) == len(DEFAULT_ABLATIONS)
    for run in reloaded["ablations"]:
        assert "config" in run
        assert "score_totals" in run
        assert "verdict" in run


# ── triviality check ──────────────────────────────────────


def test_ablations_are_NOT_all_equal_to_baseline():
    """The whole point of the ablation matrix is to show that the
    subsystems are load-bearing. If every ablation produced the same
    gate verdict as the baseline, the framework would be measuring
    nothing. This test pins that invariant."""
    matrix = run_ablation_matrix()
    base = matrix.baseline.verdict
    diffs = [r.verdict for r in matrix.ablations if r.verdict != base]
    assert len(diffs) >= 3  # at least 3 of the 5 ablations differ
