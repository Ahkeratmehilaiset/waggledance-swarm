# SPDX-License-Identifier: Apache-2.0
"""Tests for the episodic consolidation layer.

Contract enforced:

* consolidation is deterministic: same inputs → same outputs
* the store is bounded at max_records
* replay from a serialised list reproduces consolidate() exactly
* contamination flags halve a record's contribution
* the preferred caregiver is the one with the highest salience
* negative score_total is rejected at write time
"""

from __future__ import annotations

import json

import pytest

from waggledance.observatory.mama_events.consolidation import (
    EpisodicRecord,
    EpisodicStore,
    replay,
)


def _mk(event_id: str, cg: str, score: int, *, flags=(), session="s1", ts=0) -> EpisodicRecord:
    return EpisodicRecord(
        event_id=event_id,
        timestamp_ms=ts,
        session_id=session,
        caregiver_candidate_id=cg,
        score_total=score,
        score_band="candidate_grounded",
        contamination_flags=list(flags),
    )


# ── basics ──────────────────────────────────────────────────


def test_empty_store_returns_empty_salience():
    s = EpisodicStore()
    assert s.consolidate() == {}
    assert s.preferred_caregiver() is None
    assert len(s) == 0


def test_write_rejects_negative_score():
    s = EpisodicStore()
    with pytest.raises(ValueError):
        s.write(EpisodicRecord(
            event_id="e1", timestamp_ms=1, session_id="s",
            caregiver_candidate_id="c", score_total=-1,
            score_band="artifact_or_parrot",
        ))


def test_write_is_bounded():
    s = EpisodicStore(max_records=3)
    for i in range(10):
        s.write(_mk(f"e{i}", "c", 50, ts=i))
    assert len(s) == 3
    ids = [r.event_id for r in s.records()]
    assert ids == ["e7", "e8", "e9"]


# ── consolidate direction ──────────────────────────────────


def test_preferred_caregiver_is_highest_total_score():
    s = EpisodicStore()
    s.write(_mk("e1", "alice", 80, ts=1))
    s.write(_mk("e2", "bob", 40, ts=2))
    s.write(_mk("e3", "alice", 70, ts=3))
    assert s.preferred_caregiver() == "alice"


def test_records_without_caregiver_are_ignored():
    s = EpisodicStore()
    s.write(EpisodicRecord(
        event_id="e1", timestamp_ms=1, session_id="s",
        caregiver_candidate_id=None, score_total=100,
        score_band="proto_social_candidate",
    ))
    assert s.consolidate() == {}


def test_contamination_flags_halve_contribution():
    s = EpisodicStore()
    s.write(_mk("e1", "alice", 80, ts=1))
    s.write(_mk("e2", "bob", 80, ts=2, flags=["direct_prompt"]))
    salience = s.consolidate()
    assert salience["alice"] > salience["bob"]


def test_recency_boost_favours_newer_records():
    s = EpisodicStore()
    # equal raw scores; later record should accumulate more
    for i in range(5):
        s.write(_mk(f"e{i}", "alice", 50, ts=i))
    for i in range(5):
        s.write(_mk(f"f{i}", "bob", 50, ts=10 + i))
    salience = s.consolidate()
    assert salience["bob"] > salience["alice"]


def test_normalised_salience_peak_is_one():
    s = EpisodicStore()
    s.write(_mk("e1", "alice", 80, ts=1))
    s.write(_mk("e2", "bob", 20, ts=2))
    salience = s.consolidate()
    assert max(salience.values()) == pytest.approx(1.0)
    assert min(salience.values()) > 0.0


# ── determinism + replay ───────────────────────────────────


def test_consolidation_is_deterministic():
    s1 = EpisodicStore()
    s2 = EpisodicStore()
    seq = [
        _mk("e1", "alice", 80, ts=1),
        _mk("e2", "bob", 50, ts=2, flags=["direct_prompt"]),
        _mk("e3", "alice", 70, ts=3),
        _mk("e4", "charlie", 65, ts=4),
    ]
    for r in seq:
        s1.write(r); s2.write(r)
    assert s1.consolidate() == s2.consolidate()
    assert s1.fingerprint() == s2.fingerprint()


def test_replay_matches_consolidate():
    s = EpisodicStore()
    s.write(_mk("e1", "alice", 80, ts=1))
    s.write(_mk("e2", "bob", 50, ts=2, flags=["direct_prompt"]))
    s.write(_mk("e3", "alice", 70, ts=3))

    serialised = s.serialise()
    dumped = json.dumps(serialised)
    replayed = replay(json.loads(dumped))
    assert replayed == s.consolidate()


def test_replay_from_disk_style_dict_roundtrip():
    s = EpisodicStore()
    s.write(_mk("e1", "alice", 80, ts=1))
    s.write(_mk("e2", "bob", 50, ts=2))
    roundtripped = EpisodicStore.from_serialised(s.serialise())
    assert roundtripped.consolidate() == s.consolidate()
    assert roundtripped.fingerprint() == s.fingerprint()


def test_fingerprint_changes_when_record_added():
    s = EpisodicStore()
    s.write(_mk("e1", "alice", 80, ts=1))
    fp1 = s.fingerprint()
    s.write(_mk("e2", "bob", 50, ts=2))
    fp2 = s.fingerprint()
    assert fp1 != fp2


# ── count_by_band ──────────────────────────────────────────


def test_count_by_band():
    s = EpisodicStore()
    s.write(_mk("e1", "a", 80, ts=1))           # candidate_grounded
    s.write(EpisodicRecord(
        event_id="e2", timestamp_ms=2, session_id="s1",
        caregiver_candidate_id="b", score_total=85,
        score_band="proto_social_candidate",
    ))
    s.write(EpisodicRecord(
        event_id="e3", timestamp_ms=3, session_id="s1",
        caregiver_candidate_id="c", score_total=15,
        score_band="artifact_or_parrot",
    ))
    c = s.count_by_band()
    assert c["candidate_grounded"] == 1
    assert c["proto_social_candidate"] == 1
    assert c["artifact_or_parrot"] == 1
