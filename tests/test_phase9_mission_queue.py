"""Targeted tests for waggledance/core/autonomy/mission_queue.py
(Phase 9 §F.mission_queue).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from waggledance.core.autonomy import mission_queue as mq


def _m(*, kind="consultation_request", lane="provider_plane",
         priority=0.5, intent="x intent",
         rationale="y rationale", tick=1) -> mq.Mission:
    return mq.make_mission(
        kind=kind, lane=lane, priority=priority,
        intent=intent, rationale=rationale, created_tick_id=tick,
    )


# ── 1. mission_id structural determinism ──────────────────────────

def test_mission_id_excludes_priority_and_rationale():
    a = mq.compute_mission_id(kind="ingest_request", lane="ingestion",
                                  intent="ingest pinned curiosity log",
                                  capsule_context="neutral_v1")
    b = mq.compute_mission_id(kind="ingest_request", lane="ingestion",
                                  intent="ingest pinned curiosity log",
                                  capsule_context="neutral_v1")
    assert a == b
    assert len(a) == 12
    # Different intent → different id
    c = mq.compute_mission_id(kind="ingest_request", lane="ingestion",
                                  intent="ingest pinned dream curriculum",
                                  capsule_context="neutral_v1")
    assert a != c


def test_two_missions_with_same_structural_id():
    m1 = _m(priority=0.9, rationale="first rationale text")
    m2 = _m(priority=0.1, rationale="different rationale text 2")
    assert m1.mission_id == m2.mission_id


# ── 2. enqueue dedup ─────────────────────────────────────────────-

def test_enqueue_dedups_by_mission_id():
    mission = _m()
    q = []
    q = mq.enqueue(q, mission)
    q = mq.enqueue(q, mission)
    q = mq.enqueue(q, mission)
    assert len(q) == 1


def test_enqueue_does_not_mutate_input_list():
    mission = _m()
    q = []
    q2 = mq.enqueue(q, mission)
    assert q == []
    assert len(q2) == 1


def test_enqueue_appends_distinct():
    a = _m(intent="A intent")
    b = _m(intent="B intent")
    q = mq.enqueue(mq.enqueue([], a), b)
    assert len(q) == 2
    assert q[0].mission_id != q[1].mission_id


# ── 3. deterministic ordering ────────────────────────────────────-

def test_deterministic_order_by_priority_desc_then_id():
    high = _m(intent="HIGH intent text", priority=0.9)
    mid = _m(intent="MIDDLE intent text", priority=0.5)
    low = _m(intent="LOW intent text", priority=0.1)
    # Insert in non-priority order
    ordered = mq.deterministic_order([low, high, mid])
    assert [m.intent for m in ordered] == ["HIGH intent text",
                                              "MIDDLE intent text",
                                              "LOW intent text"]


def test_deterministic_order_ties_broken_by_id():
    # Same priority → ordered by mission_id ascending
    a = _m(intent="alpha intent here", priority=0.5)
    b = _m(intent="bravo intent here", priority=0.5)
    c = _m(intent="charlie intent text", priority=0.5)
    ordered = mq.deterministic_order([c, a, b])
    ids = [m.mission_id for m in ordered]
    assert ids == sorted(ids)


# ── 4. validation ────────────────────────────────────────────────-

def test_make_mission_rejects_unknown_kind():
    with pytest.raises(ValueError, match="unknown mission kind"):
        mq.make_mission(kind="bogus_kind", lane="provider_plane",
                          priority=0.5, intent="x intent",
                          rationale="y rationale", created_tick_id=1)


def test_make_mission_rejects_unknown_lane():
    with pytest.raises(ValueError, match="unknown mission lane"):
        mq.make_mission(kind="consultation_request", lane="bogus_lane",
                          priority=0.5, intent="x intent",
                          rationale="y rationale", created_tick_id=1)


def test_make_mission_rejects_negative_priority():
    with pytest.raises(ValueError, match="priority must be"):
        mq.make_mission(kind="noop", lane="wait",
                          priority=-1.0, intent="x intent",
                          rationale="y rationale", created_tick_id=1)


def test_make_mission_rejects_invalid_max_retries():
    with pytest.raises(ValueError, match="max_retries"):
        mq.make_mission(kind="noop", lane="wait", priority=0.0,
                          intent="x intent", rationale="y rationale",
                          created_tick_id=1, max_retries=11)


# ── 5. circuit breaker filtering ──────────────────────────────────

def test_filter_by_breakers_blocks_open_lane():
    m_provider = _m(kind="consultation_request", lane="provider_plane")
    m_ingest = _m(kind="ingest_request", lane="ingestion",
                    intent="ingest pinned data x")
    breakers = {"provider_plane": "open", "ingestion": "closed"}
    admissible, blocked = mq.filter_by_breakers(
        [m_provider, m_ingest], breakers,
    )
    assert m_ingest in admissible
    assert m_provider in blocked


def test_filter_by_breakers_blocks_quarantined():
    m = _m()
    breakers = {"provider_plane": "quarantined"}
    admissible, blocked = mq.filter_by_breakers([m], breakers)
    assert admissible == []
    assert blocked == [m]


def test_filter_by_breakers_allows_half_open():
    """half_open is admissible (probe state); only open/quarantined block."""
    m = _m()
    breakers = {"provider_plane": "half_open"}
    admissible, blocked = mq.filter_by_breakers([m], breakers)
    assert m in admissible
    assert blocked == []


def test_filter_by_breakers_uses_circuit_breaker_lane_override():
    """If a mission specifies circuit_breaker_lane explicitly, that
    overrides the lane."""
    m = mq.make_mission(
        kind="consultation_request", lane="provider_plane",
        priority=0.5, intent="x intent", rationale="y rationale",
        created_tick_id=1, circuit_breaker_lane="solver_synthesis",
    )
    breakers = {"provider_plane": "closed", "solver_synthesis": "open"}
    admissible, blocked = mq.filter_by_breakers([m], breakers)
    assert blocked == [m]


# ── 6. lifecycle transitions ──────────────────────────────────────

def test_with_lifecycle_changes_status():
    m = _m()
    s2 = mq.with_lifecycle(m, status="completed", completed_tick_id=5)
    assert s2.lifecycle_status == "completed"
    assert s2.completed_tick_id == 5
    # Original unchanged
    assert m.lifecycle_status == "queued"
    assert m.completed_tick_id is None


def test_with_lifecycle_rejects_unknown_status():
    m = _m()
    with pytest.raises(ValueError, match="unknown lifecycle"):
        mq.with_lifecycle(m, status="bogus")


def test_with_lifecycle_can_increment_retry():
    m = _m()
    s2 = mq.with_lifecycle(m, status="queued", retry_count=2)
    assert s2.retry_count == 2
    assert s2.lifecycle_status == "queued"


# ── 7. open_missions filter ──────────────────────────────────────-

def test_open_missions_excludes_completed_and_failed():
    a = _m(intent="A intent here")
    b = mq.with_lifecycle(_m(intent="B intent here"), status="completed",
                              completed_tick_id=2)
    c = mq.with_lifecycle(_m(intent="C intent here"), status="failed",
                              completed_tick_id=3)
    d = _m(intent="D intent here")
    open_ones = mq.open_missions([a, b, c, d])
    assert a in open_ones
    assert d in open_ones
    assert b not in open_ones
    assert c not in open_ones


# ── 8. atomic persistence (R7.5 §G durability rule) ──────────────

def test_save_and_load_round_trip(tmp_path):
    a = _m(intent="A intent here", priority=0.7)
    b = _m(intent="B intent here", priority=0.3)
    p = tmp_path / "missions.jsonl"
    mq.save_missions([a, b], p)
    loaded = mq.load_missions(p)
    assert len(loaded) == 2
    # Persisted in deterministic_order (priority desc, id ascending)
    assert loaded[0].priority >= loaded[1].priority


def test_save_uses_tmp_plus_replace(tmp_path, monkeypatch):
    """save_missions must use tmp + os.replace, not direct write."""
    real = mq.os.replace
    captured = []

    def cap(src, dst):
        captured.append((Path(src).name, Path(dst).name))
        return real(src, dst)

    monkeypatch.setattr(mq.os, "replace", cap)
    mq.save_missions([_m()], tmp_path / "missions.jsonl")
    assert any(dst == "missions.jsonl" for _, dst in captured)
    assert any(src.startswith(".missions.") for src, _ in captured)


def test_save_failure_cleans_up_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(mq.os, "replace",
                          lambda *a, **kw: (_ for _ in ()).throw(
                              OSError("simulated")))
    with pytest.raises(OSError):
        mq.save_missions([_m()], tmp_path / "missions.jsonl")
    leftovers = [p for p in tmp_path.iterdir()
                  if p.name.startswith(".missions.")]
    assert leftovers == []


def test_load_missing_returns_empty(tmp_path):
    assert mq.load_missions(tmp_path / "nope.jsonl") == []


def test_load_skips_malformed_lines(tmp_path):
    a = _m(intent="real intent here")
    p = tmp_path / "missions.jsonl"
    mq.save_missions([a], p)
    with open(p, "a", encoding="utf-8") as f:
        f.write("{ this is not valid json\n")
        f.write('{"missing": "required_fields"}\n')
    loaded = mq.load_missions(p)
    assert len(loaded) == 1
    assert loaded[0].mission_id == a.mission_id


# ── 9. Mission.no_runtime_mutation invariant ──────────────────────

def test_mission_no_runtime_mutation_is_constant_true():
    m = _m()
    assert m.no_runtime_mutation is True
    # Source check
    src = (ROOT / "waggledance" / "core" / "autonomy"
            / "mission_queue.py").read_text(encoding="utf-8")
    assert "no_runtime_mutation=False" not in src
    assert "no_runtime_mutation = False" not in src


# ── 10. canonical_jsonl is byte-stable ────────────────────────────

def test_canonical_jsonl_byte_stable():
    a = _m(intent="A intent text")
    b = _m(intent="B intent text", priority=0.9)
    j1 = mq.to_canonical_jsonl([a, b])
    j2 = mq.to_canonical_jsonl([b, a])   # different input order
    assert j1 == j2   # output is sorted by deterministic_order


# ── 11. domain neutrality of source ──────────────────────────────-

def test_mission_queue_source_is_domain_neutral():
    src = (ROOT / "waggledance" / "core" / "autonomy"
            / "mission_queue.py").read_text(encoding="utf-8").lower()
    forbidden = ["bee ", "hive ", "honeycomb ", "swarm ",
                  "factory ", "pdam", "beverage"]
    for pat in forbidden:
        assert pat not in src
