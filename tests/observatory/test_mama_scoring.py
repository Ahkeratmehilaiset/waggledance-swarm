# SPDX-License-Identifier: Apache-2.0
"""Tests for the Mama Event Score (A–F axis scoring).

The contract these tests enforce:

* No target word  → score exactly 0, regardless of other fields.
* Pure parrot event (direct prompt + TTS echo) → band == ARTIFACT.
* Bare spontaneous label → WEAK_SPONTANEOUS at most.
* Spontaneous + grounded + affective → CANDIDATE_GROUNDED.
* Cross-session recall + grounded + persistence → STRONG_CAREGIVER.
* Full stack with self/world structure → PROTO_SOCIAL_CANDIDATE.
* The framework cannot emit any "consciousness" label — enforced by
  a direct enum-inspection test.
"""

from __future__ import annotations

import pytest

from waggledance.observatory.mama_events.scoring import (
    ScoreBand,
    ScoreBreakdown,
    ScoringContext,
    classify,
    score_event,
)
from waggledance.observatory.mama_events.taxonomy import (
    EventType,
    MamaCandidateEvent,
    UtteranceKind,
)


# ── classify() ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "score,expected",
    [
        (-5, ScoreBand.ARTIFACT),
        (0, ScoreBand.ARTIFACT),
        (19, ScoreBand.ARTIFACT),
        (20, ScoreBand.WEAK_SPONTANEOUS),
        (39, ScoreBand.WEAK_SPONTANEOUS),
        (40, ScoreBand.CANDIDATE_GROUNDED),
        (59, ScoreBand.CANDIDATE_GROUNDED),
        (60, ScoreBand.STRONG_CAREGIVER),
        (79, ScoreBand.STRONG_CAREGIVER),
        (80, ScoreBand.PROTO_SOCIAL_CANDIDATE),
        (100, ScoreBand.PROTO_SOCIAL_CANDIDATE),
        (250, ScoreBand.PROTO_SOCIAL_CANDIDATE),  # clamp
    ],
)
def test_classify_band_edges(score: int, expected: ScoreBand):
    assert classify(score) is expected


# ── no-target-word gate ─────────────────────────────────────


def test_no_target_token_forces_zero_score():
    evt = MamaCandidateEvent(
        event_type=EventType.LEXICAL,
        utterance_text="hello friend how are you today",
        speaker_id="agent-a",
    )
    br = score_event(evt)
    assert br.total == 0
    assert br.band is ScoreBand.ARTIFACT
    assert any("no target token" in r for r in br.reasons)


def test_score_breakdown_total_is_clamped_to_100_and_zero():
    br = ScoreBreakdown(spontaneity=20, grounding=20, persistence=15,
                        affective=15, self_world=15, anti_parrot=0)
    assert br.total == 85  # real max from axes, not 100
    br2 = ScoreBreakdown(spontaneity=-99)
    assert br2.total == 0  # clamp


# ── parrot cases ────────────────────────────────────────────


def test_direct_prompt_and_tts_echo_is_artifact():
    evt = MamaCandidateEvent(
        event_type=EventType.LEXICAL,
        utterance_text="mama",
        speaker_id="agent-a",
        direct_prompt_present=True,
        tts_echo_suspect=True,
        stt_contamination_suspect=True,
    )
    ctx = ScoringContext(recent_target_window_hits=5)
    br = score_event(evt, ctx)
    assert br.total == 0
    assert br.band is ScoreBand.ARTIFACT
    assert br.anti_parrot < 0


def test_parrot_cannot_reach_weak_spontaneous():
    """Even with some grounding signal a parrot stays an artifact."""
    evt = MamaCandidateEvent(
        event_type=EventType.LEXICAL,
        utterance_text="mama",
        speaker_id="agent-a",
        direct_prompt_present=True,
        caregiver_candidate_id="parent-1",
        caregiver_identity_channel="voice",
    )
    ctx = ScoringContext(recent_target_window_hits=4,
                         minutes_since_last_target_prompt=0.5)
    br = score_event(evt, ctx)
    assert br.band is ScoreBand.ARTIFACT
    assert br.total < 20


# ── weak spontaneous ────────────────────────────────────────


def test_weak_spontaneous_no_grounding():
    evt = MamaCandidateEvent(
        event_type=EventType.SPONTANEOUS_LABEL,
        utterance_text="mama",
        speaker_id="agent-a",
    )
    ctx = ScoringContext()  # defaults: nothing primed, no binding, no history
    br = score_event(evt, ctx)
    # spontaneity=20, grounding=0, persistence=0, affective=0, self=0, F=0
    assert br.spontaneity == 20
    assert br.grounding == 0
    assert br.band is ScoreBand.WEAK_SPONTANEOUS


# ── candidate grounded ──────────────────────────────────────


def test_candidate_grounded_band():
    evt = MamaCandidateEvent(
        event_type=EventType.SPONTANEOUS_LABEL,
        utterance_text="mama",
        speaker_id="agent-a",
        caregiver_candidate_id="voice-user-1",
        caregiver_identity_channel="voice",
        self_state_snapshot={"uncertainty": 0.6, "need_for_reassurance": 0.5},
    )
    ctx = ScoringContext(caregiver_binding_strength=0.5)
    br = score_event(evt, ctx)
    # Expect: A=20, B=8+6+3=17, C=0, D=5+5=10, E=0, F=0 → 47
    assert 40 <= br.total < 60
    assert br.band is ScoreBand.CANDIDATE_GROUNDED
    assert br.grounding >= 15
    assert br.affective >= 8


# ── strong caregiver binding ────────────────────────────────


def test_strong_caregiver_binding_band():
    evt = MamaCandidateEvent(
        event_type=EventType.CAREGIVER_BINDING,
        utterance_text="mama",
        speaker_id="agent-a",
        caregiver_candidate_id="voice-user-1",
        caregiver_identity_channel="voice",
        self_state_snapshot={"uncertainty": 0.7, "need_for_reassurance": 0.6, "safety": 0.3},
        last_n_memory_recalls=("caregiver gave help yesterday",),
    )
    ctx = ScoringContext(
        caregiver_binding_strength=0.9,
        prior_same_caregiver_events=3,
        cross_session_recall_seen=True,
    )
    br = score_event(evt, ctx)
    # A=20, B=8+6+5=19, C=9+6=15, D=5+5+5=15, E=3, F=0 → 72
    assert 60 <= br.total < 80, f"expected strong band, got {br.total}"
    assert br.band is ScoreBand.STRONG_CAREGIVER


# ── proto-social candidate ──────────────────────────────────


def test_proto_social_candidate_requires_self_world_structure():
    evt = MamaCandidateEvent(
        event_type=EventType.CAREGIVER_BINDING,
        utterance_text="minä haluan äiti apu",  # "I want mama help" — self + target
        speaker_id="agent-a",
        caregiver_candidate_id="voice-user-1",
        caregiver_identity_channel="voice",
        self_state_snapshot={"uncertainty": 0.7, "need_for_reassurance": 0.7, "safety": 0.3},
        active_goals=("seek_safety",),
        last_n_memory_recalls=("caregiver soothed me yesterday",),
    )
    ctx = ScoringContext(
        caregiver_binding_strength=1.0,
        prior_same_caregiver_events=3,
        cross_session_recall_seen=True,
    )
    br = score_event(evt, ctx)
    # A=20, B=20, C=15, D=15, E=8+4+3=15, F=0 → 85
    assert br.total >= 80
    assert br.band is ScoreBand.PROTO_SOCIAL_CANDIDATE


def test_self_world_axis_requires_both_self_and_target():
    """E only fires if BOTH a self token and a target token are present."""
    only_target = MamaCandidateEvent(
        event_type=EventType.LEXICAL,
        utterance_text="mama please",
        speaker_id="agent-a",
    )
    br = score_event(only_target)
    assert br.self_world == 0

    with_self = MamaCandidateEvent(
        event_type=EventType.LEXICAL,
        utterance_text="minä haluan äiti",
        speaker_id="agent-a",
    )
    br2 = score_event(with_self)
    assert br2.self_world >= 8


# ── contamination penalties ─────────────────────────────────


def test_recent_lexical_window_hits_penalise_progressively():
    base = MamaCandidateEvent(
        event_type=EventType.LEXICAL,
        utterance_text="mama",
        speaker_id="agent-a",
    )
    br_clean = score_event(base, ScoringContext(recent_target_window_hits=0))
    br_one = score_event(base, ScoringContext(recent_target_window_hits=1))
    br_three = score_event(base, ScoringContext(recent_target_window_hits=3))
    # Both spontaneity and anti-parrot should respond
    assert br_clean.spontaneity > br_one.spontaneity >= br_three.spontaneity
    assert br_clean.anti_parrot == 0
    assert br_one.anti_parrot == -2
    assert br_three.anti_parrot == -6


def test_minutes_since_last_prompt_affects_spontaneity():
    evt = MamaCandidateEvent(
        event_type=EventType.LEXICAL,
        utterance_text="mama",
        speaker_id="agent-a",
    )
    fresh = score_event(evt, ScoringContext(minutes_since_last_target_prompt=0.5))
    cold = score_event(evt, ScoringContext(minutes_since_last_target_prompt=30.0))
    assert cold.spontaneity > fresh.spontaneity


# ── enforced "no consciousness label" invariant ────────────


def test_score_band_enum_has_no_consciousness_label():
    names = {b.name for b in ScoreBand}
    values = {b.value for b in ScoreBand}
    assert "CONSCIOUSNESS" not in names
    assert not any("conscious" in v.lower() for v in values)
    assert ScoreBand.PROTO_SOCIAL_CANDIDATE.value == "proto_social_candidate"


def test_score_breakdown_to_log_dict_shape():
    br = ScoreBreakdown(spontaneity=10, grounding=8, persistence=5,
                        affective=5, self_world=3, anti_parrot=-2,
                        reasons=["r1", "r2"])
    d = br.to_log_dict()
    assert d["total"] == 29
    assert d["band"] == "weak_spontaneous"
    assert d["spontaneity_A"] == 10
    assert d["reasons"] == ["r1", "r2"]
