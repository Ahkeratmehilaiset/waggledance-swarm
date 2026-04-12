# SPDX-License-Identifier: Apache-2.0
"""Tests for the caregiver-binding tracker.

Contract enforced by these tests:
* single reinforcement yields non-zero but small strength
* strength is monotonic in reinforcement count (up to saturation)
* distractions weaken the tracked candidate without wiping history
* cross-session flag flips only after ≥2 distinct session ids
* caregiver-vs-distractor experiment: stable caregiver outranks
  a noisy distractor after a realistic event sequence
"""

from __future__ import annotations

import pytest

from waggledance.observatory.mama_events.caregiver_binding import (
    CaregiverBindingTracker,
    CaregiverProfile,
)


@pytest.fixture
def tracker() -> CaregiverBindingTracker:
    return CaregiverBindingTracker()


# ── profile basics ──────────────────────────────────────────


def test_unknown_candidate_has_zero_strength(tracker: CaregiverBindingTracker):
    assert tracker.strength_for("nobody") == 0.0
    assert tracker.cross_session_for("nobody") is False
    assert tracker.profile("nobody") is None


def test_reinforce_rejects_empty_candidate(tracker: CaregiverBindingTracker):
    with pytest.raises(ValueError):
        tracker.reinforce(candidate_id="", session_id="s1")


def test_single_reinforcement_yields_small_strength(tracker: CaregiverBindingTracker):
    tracker.reinforce(
        candidate_id="voice-user-1",
        session_id="s1",
        identity_channel="voice",
        timestamp_ms=1_000_000,
    )
    s = tracker.strength_for("voice-user-1")
    assert 0.0 < s <= 0.3


def test_strength_is_monotonic_in_reinforcements(tracker: CaregiverBindingTracker):
    strengths: list[float] = []
    for i in range(10):
        tracker.reinforce(
            candidate_id="voice-user-1",
            session_id=f"s{i}",
            identity_channel="voice",
            event_id=f"evt-{i}",
            timestamp_ms=1_000_000 + i,
        )
        strengths.append(tracker.strength_for("voice-user-1"))
    for a, b in zip(strengths, strengths[1:]):
        assert b >= a
    # should approach 1 but never exceed it
    assert strengths[-1] <= 1.0
    assert strengths[-1] >= strengths[0]


def test_strength_saturates_below_one():
    t = CaregiverBindingTracker()
    for i in range(50):
        t.reinforce(candidate_id="c", session_id=f"s{i}", timestamp_ms=i)
    s = t.strength_for("c")
    assert 0.95 < s <= 1.0


# ── distractions ────────────────────────────────────────────


def test_distraction_lowers_net_strength(tracker: CaregiverBindingTracker):
    for i in range(3):
        tracker.reinforce(candidate_id="c", session_id=f"s{i}", timestamp_ms=i)
    strong = tracker.strength_for("c")
    for i in range(2):
        tracker.distract(candidate_id="c", session_id="s-dist", timestamp_ms=100 + i)
    weak = tracker.strength_for("c")
    assert weak < strong


def test_distract_rejects_empty_candidate(tracker: CaregiverBindingTracker):
    with pytest.raises(ValueError):
        tracker.distract(candidate_id="", session_id="s1")


# ── cross-session ──────────────────────────────────────────


def test_cross_session_requires_two_distinct_sessions(tracker: CaregiverBindingTracker):
    tracker.reinforce(candidate_id="c", session_id="s1", timestamp_ms=1)
    assert tracker.cross_session_for("c") is False
    tracker.reinforce(candidate_id="c", session_id="s1", timestamp_ms=2)
    assert tracker.cross_session_for("c") is False
    tracker.reinforce(candidate_id="c", session_id="s2", timestamp_ms=3)
    assert tracker.cross_session_for("c") is True


# ── caregiver vs distractor experiment ─────────────────────


def test_stable_caregiver_outranks_noisy_distractor():
    t = CaregiverBindingTracker()
    # Stable caregiver: 6 reinforcements across 3 sessions, no distractions
    for sess in ("s1", "s2", "s3"):
        for i in range(2):
            t.reinforce(
                candidate_id="caregiver",
                session_id=sess,
                identity_channel="voice",
                timestamp_ms=hash((sess, i)) & 0xFFFFFF,
            )
    # Distractor: 3 reinforcements across 2 sessions, 2 distractions
    for sess in ("s1", "s2"):
        t.reinforce(candidate_id="distractor", session_id=sess,
                    identity_channel="voice", timestamp_ms=1)
    t.reinforce(candidate_id="distractor", session_id="s2",
                identity_channel="voice", timestamp_ms=2)
    t.distract(candidate_id="distractor", session_id="s2", timestamp_ms=3)
    t.distract(candidate_id="distractor", session_id="s3", timestamp_ms=4)

    ranked = t.rank(top_n=2)
    assert ranked[0].candidate_id == "caregiver"
    assert ranked[1].candidate_id == "distractor"
    assert ranked[0].strength > ranked[1].strength
    assert ranked[0].cross_session is True


def test_neutral_control_has_no_binding():
    """A neutral control agent with only distractions should never build strength."""
    t = CaregiverBindingTracker()
    for i in range(5):
        t.distract(candidate_id="neutral", session_id="s1", timestamp_ms=i)
    assert t.strength_for("neutral") == 0.0


# ── recent event ids ───────────────────────────────────────


def test_last_event_ids_are_ring_buffered():
    t = CaregiverBindingTracker(max_recent_event_ids=3)
    for i in range(5):
        t.reinforce(
            candidate_id="c",
            session_id="s",
            event_id=f"evt-{i}",
            timestamp_ms=i,
        )
    prof = t.profile("c")
    assert prof is not None
    assert prof.last_event_ids == ["evt-2", "evt-3", "evt-4"]


# ── to_log_dict shape ──────────────────────────────────────


def test_to_log_dict_is_json_safe(tracker: CaregiverBindingTracker):
    import json
    tracker.reinforce(candidate_id="c1", session_id="s1",
                      identity_channel="voice", event_id="e1", timestamp_ms=10)
    tracker.reinforce(candidate_id="c1", session_id="s2",
                      identity_channel="voice", event_id="e2", timestamp_ms=20)
    tracker.distract(candidate_id="c2", session_id="s1", timestamp_ms=30)
    dumped = json.dumps(tracker.to_log_dict())
    reloaded = json.loads(dumped)
    assert "profiles" in reloaded
    assert "top" in reloaded
    # profiles are serialisable
    for p in reloaded["profiles"]:
        assert isinstance(p["strength"], float)
        assert isinstance(p["cross_session"], bool)
        assert 0.0 <= p["strength"] <= 1.0
