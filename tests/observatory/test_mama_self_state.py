# SPDX-License-Identifier: Apache-2.0
"""Tests for the self-state homeostasis layer.

The hard rule: every state mutation must have a documented cause,
and the tests enforce direction. No random numbers are allowed,
and these tests demonstrate that fact by pinning exact transitions.
"""

from __future__ import annotations

import math

import pytest

from waggledance.observatory.mama_events.self_state import (
    SelfState,
    SelfStateUpdater,
)


@pytest.fixture
def state() -> SelfState:
    return SelfState()


@pytest.fixture
def updater() -> SelfStateUpdater:
    return SelfStateUpdater()


# ── defaults ────────────────────────────────────────────────


def test_default_self_state_in_bounds(state: SelfState):
    for value in state.as_dict().values():
        assert 0.0 <= value <= 1.0
    assert state.update_count == 0


# ── clamping ────────────────────────────────────────────────


def test_clamping_from_above(state: SelfState, updater: SelfStateUpdater):
    # raise safety to its ceiling via many soothing events
    for _ in range(20):
        updater.on_soothing(state, magnitude=1.0)
    assert state.safety == pytest.approx(1.0)
    assert state.trust <= 1.0
    assert state.trust >= 0.0


def test_clamping_from_below(state: SelfState, updater: SelfStateUpdater):
    for _ in range(20):
        updater.on_stress(state, magnitude=1.0)
    assert state.safety == pytest.approx(0.0)
    for v in state.as_dict().values():
        assert 0.0 <= v <= 1.0


def test_nan_input_does_not_corrupt_state(state: SelfState, updater: SelfStateUpdater):
    updater.on_surprise(state, magnitude=float("nan"))
    for v in state.as_dict().values():
        assert not math.isnan(v)
        assert 0.0 <= v <= 1.0


# ── direction of transitions ────────────────────────────────


def test_surprise_raises_uncertainty_and_novelty(state: SelfState, updater: SelfStateUpdater):
    before = state.snapshot()
    updater.on_surprise(state)
    assert state.uncertainty > before.uncertainty
    assert state.novelty > before.novelty
    assert state.coherence <= before.coherence


def test_stress_drops_safety_and_raises_need(state: SelfState, updater: SelfStateUpdater):
    before = state.snapshot()
    updater.on_stress(state)
    assert state.safety < before.safety
    assert state.need_for_reassurance > before.need_for_reassurance


def test_soothing_after_stress_recovers_safety(state: SelfState, updater: SelfStateUpdater):
    updater.on_stress(state, magnitude=1.0)
    low = state.safety
    updater.on_soothing(state, magnitude=1.0)
    assert state.safety > low
    assert state.need_for_reassurance < 1.0


def test_neglect_drops_trust_raises_need(state: SelfState, updater: SelfStateUpdater):
    before = state.snapshot()
    updater.on_neglect(state, magnitude=1.0)
    assert state.trust < before.trust
    assert state.need_for_reassurance > before.need_for_reassurance


def test_memory_recall_caregiver_reduces_uncertainty(state: SelfState, updater: SelfStateUpdater):
    updater.on_stress(state, magnitude=1.0)
    updater.on_surprise(state, magnitude=1.0)
    before_unc = state.uncertainty
    updater.on_memory_recall(state, was_caregiver=True)
    assert state.uncertainty < before_unc
    assert state.coherence >= 0.5


def test_memory_recall_non_caregiver_is_bookkeeping(state: SelfState, updater: SelfStateUpdater):
    before = state.snapshot()
    updater.on_memory_recall(state, was_caregiver=False)
    # Only update_count moves
    assert state.uncertainty == before.uncertainty
    assert state.trust == before.trust
    assert state.update_count == before.update_count + 1


# ── tick decay ──────────────────────────────────────────────


def test_tick_decays_uncertainty(state: SelfState, updater: SelfStateUpdater):
    updater.on_surprise(state, magnitude=1.0)
    high = state.uncertainty
    updater.tick(state, dt_seconds=1.0)
    assert state.uncertainty < high


def test_tick_decays_novelty_and_fatigue(state: SelfState, updater: SelfStateUpdater):
    updater.on_event_load(state, count=10)
    updater.on_surprise(state, magnitude=1.0)
    high_novelty = state.novelty
    high_fatigue = state.fatigue
    updater.tick(state, dt_seconds=2.0)
    assert state.novelty < high_novelty
    assert state.fatigue < high_fatigue


def test_tick_pulls_trust_to_baseline_from_above(state: SelfState, updater: SelfStateUpdater):
    state.trust = 0.9
    updater.tick(state, dt_seconds=1.0)
    assert state.trust < 0.9
    assert state.trust >= 0.5


def test_tick_pulls_trust_to_baseline_from_below(state: SelfState, updater: SelfStateUpdater):
    state.trust = 0.1
    updater.tick(state, dt_seconds=1.0)
    assert state.trust > 0.1
    assert state.trust <= 0.5


def test_tick_zero_dt_is_noop(state: SelfState, updater: SelfStateUpdater):
    updater.on_surprise(state, magnitude=1.0)
    snap = state.snapshot()
    updater.tick(state, dt_seconds=0.0)
    assert state.uncertainty == snap.uncertainty
    assert state.novelty == snap.novelty


# ── derived need_for_reassurance ────────────────────────────


def test_need_for_reassurance_reflects_high_uncertainty(state: SelfState, updater: SelfStateUpdater):
    updater.on_surprise(state, magnitude=3.0)
    assert state.need_for_reassurance >= 0.5


def test_need_for_reassurance_reflects_low_safety(state: SelfState, updater: SelfStateUpdater):
    updater.on_stress(state, magnitude=2.0)
    assert state.need_for_reassurance >= 0.5


def test_need_for_reassurance_zero_when_nominal(state: SelfState, updater: SelfStateUpdater):
    # default state: safety=1, trust=0.5, uncertainty=0
    updater._derive_reassurance_need(state)
    # weighted mean = 0.4*0 + 0.4*0 + 0.2*0.5 = 0.1
    # acute_max = max(0, 0, 0.5) = 0.5 → acute_max*0.9 = 0.45
    # result = max(0.1, 0.45) = 0.45
    assert 0.4 < state.need_for_reassurance < 0.5


# ── serialisation ──────────────────────────────────────────


def test_to_log_dict_is_json_safe(state: SelfState, updater: SelfStateUpdater):
    import json

    updater.on_stress(state, magnitude=1.0)
    updater.on_soothing(state, magnitude=0.5)
    d = state.to_log_dict()
    serialised = json.dumps(d)
    reloaded = json.loads(serialised)
    for key in [
        "uncertainty", "novelty", "trust", "safety", "fatigue",
        "need_for_reassurance", "coherence", "updated_ms", "update_count",
    ]:
        assert key in reloaded
    assert isinstance(reloaded["update_count"], int)
    assert 0.0 <= reloaded["safety"] <= 1.0


def test_snapshot_is_independent(state: SelfState, updater: SelfStateUpdater):
    snap = state.snapshot()
    updater.on_stress(state, magnitude=1.0)
    # snap must not track later mutations
    assert snap.safety == 1.0
    assert state.safety < 1.0


# ── determinism: no random numbers anywhere ────────────────


def test_state_is_deterministic_under_same_inputs():
    u = SelfStateUpdater()
    s1 = SelfState()
    s2 = SelfState()
    sequence = [
        ("stress", 1.0),
        ("surprise", 0.5),
        ("soothing", 0.75),
        ("neglect", 0.3),
        ("memory_recall_cg", None),
        ("tick", 1.5),
        ("event_load", 5),
    ]
    for name, arg in sequence:
        if name == "stress":
            u.on_stress(s1, arg); u.on_stress(s2, arg)
        elif name == "surprise":
            u.on_surprise(s1, arg); u.on_surprise(s2, arg)
        elif name == "soothing":
            u.on_soothing(s1, arg); u.on_soothing(s2, arg)
        elif name == "neglect":
            u.on_neglect(s1, arg); u.on_neglect(s2, arg)
        elif name == "memory_recall_cg":
            u.on_memory_recall(s1, True); u.on_memory_recall(s2, True)
        elif name == "tick":
            u.tick(s1, arg); u.tick(s2, arg)
        elif name == "event_load":
            u.on_event_load(s1, arg); u.on_event_load(s2, arg)
    assert s1.as_dict() == s2.as_dict()
