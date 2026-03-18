"""
Tests for MAGMA adaptation layer adapters.

Covers:
- AuditProjector event recording
- ProvenanceAdapter trust weights
- ReplayAdapter mission recording
- TrustAdapter observation recording and scoring
- EventLogAdapter event logging
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from waggledance.core.magma.audit_projector import AuditProjector, AuditEntry
from waggledance.core.magma.provenance import ProvenanceAdapter
from waggledance.core.magma.replay_engine import ReplayAdapter
from waggledance.core.magma.trust_adapter import TrustAdapter
from waggledance.core.magma.event_log_adapter import EventLogAdapter


class TestAuditProjector:
    def test_record_entry(self):
        proj = AuditProjector(db_path=":memory:")
        entry = AuditEntry(event_type="goal_created", payload={"goal_id": "g1"})
        proj.record(entry)
        results = proj.query_recent(limit=10)
        assert len(results) >= 1

    def test_record_goal_event(self):
        proj = AuditProjector(db_path=":memory:")
        proj.record_goal_event("g1", "created", {"type": "SOLVE"})
        results = proj.query_by_goal("g1")
        assert len(results) >= 1

    def test_record_action_event(self):
        proj = AuditProjector(db_path=":memory:")
        proj.record_action_event("a1", "executed", capability_id="solve.math")
        results = proj.query_recent()
        assert len(results) >= 1

    def test_record_policy_decision(self):
        proj = AuditProjector(db_path=":memory:")
        proj.record_policy_decision("a1", "allow", risk_score=0.2, reason="low risk")
        results = proj.query_recent()
        assert len(results) >= 1

    def test_record_case(self):
        proj = AuditProjector(db_path=":memory:")
        proj.record_case("t1", "gold", goal_id="g1")
        results = proj.query_recent()
        assert len(results) >= 1

    def test_query_by_goal(self):
        proj = AuditProjector(db_path=":memory:")
        proj.record_goal_event("g1", "created")
        proj.record_goal_event("g2", "created")
        proj.record_goal_event("g1", "completed")
        results = proj.query_by_goal("g1")
        assert len(results) == 2

    def test_stats(self):
        proj = AuditProjector(db_path=":memory:")
        proj.record_goal_event("g1", "created")
        proj.record_action_event("a1", "executed")
        stats = proj.stats()
        assert stats["total_entries"] >= 2


class TestProvenanceAdapter:
    def test_record_provenance(self):
        prov = ProvenanceAdapter()
        rec = prov.record_provenance(
            fact_id="fact-1", source_type="observed",
            canonical_id="sensor-a", confidence=0.9,
        )
        assert rec is not None

    def test_trust_weight(self):
        prov = ProvenanceAdapter()
        prov.record_provenance("f1", "confirmed_by_verifier", confidence=0.95)
        prov.record_provenance("f2", "proposed_by_llm", confidence=0.5)
        w1 = prov.get_trust_weight("f1")
        w2 = prov.get_trust_weight("f2")
        assert w1 > w2

    def test_upgrade_source_type(self):
        prov = ProvenanceAdapter()
        prov.record_provenance("f1", "proposed_by_llm")
        ok = prov.upgrade_source_type("f1", "confirmed_by_verifier")
        assert ok is True
        w = prov.get_trust_weight("f1")
        assert w >= 0.8

    def test_unknown_entity_weight(self):
        prov = ProvenanceAdapter()
        # Unknown entity returns default weight (0.5), not 0.0
        w = prov.get_trust_weight("nonexistent")
        assert isinstance(w, float)

    def test_get_verified_facts(self):
        prov = ProvenanceAdapter()
        prov.record_provenance("f1", "confirmed_by_verifier")
        prov.record_provenance("f2", "proposed_by_llm")
        verified = prov.get_verified_facts()
        assert any(r.fact_id == "f1" for r in verified)


class TestReplayAdapter:
    def test_record_mission_event(self):
        ra = ReplayAdapter()
        ra.record_mission_event("g1", "goal_created", {"goal_id": "g1"})
        ra.record_mission_event("g1", "action_executed", {"action_id": "a1"})
        replay = ra.get_mission_replay("g1")
        assert replay is not None
        assert len(replay.entries) >= 2

    def test_set_metadata(self):
        ra = ReplayAdapter()
        ra.record_mission_event("g1", "start", {})
        ra.set_mission_metadata("g1", goal_type="SOLVE", status="completed",
                                quality_grade="gold")
        replay = ra.get_mission_replay("g1")
        assert replay.quality_grade == "gold"

    def test_list_missions(self):
        ra = ReplayAdapter()
        ra.record_mission_event("g1", "start", {})
        ra.record_mission_event("g2", "start", {})
        missions = ra.list_missions()
        assert len(missions) == 2

    def test_nonexistent_mission(self):
        ra = ReplayAdapter()
        assert ra.get_mission_replay("nope") is None

    def test_stats(self):
        ra = ReplayAdapter()
        ra.record_mission_event("g1", "start", {})
        stats = ra.stats()
        assert stats["total_missions"] >= 1


class TestTrustAdapter:
    def test_record_and_score(self):
        ta = TrustAdapter()
        ta.record_observation("capability", "solve.math", True, confidence=0.9)
        ta.record_observation("capability", "solve.math", True, confidence=0.85)
        ta.record_observation("capability", "solve.math", False)
        score = ta.get_trust_score("capability", "solve.math")
        assert 0.0 < score < 1.0

    def test_unknown_entity_score(self):
        ta = TrustAdapter()
        score = ta.get_trust_score("capability", "unknown")
        assert score == 0.5

    def test_ranking(self):
        ta = TrustAdapter()
        ta.record_observation("capability", "a", True)
        ta.record_observation("capability", "a", True)
        ta.record_observation("capability", "b", True)
        ta.record_observation("capability", "b", False)
        ta.record_observation("capability", "c", False)
        ranking = ta.get_ranking("capability")
        assert len(ranking) >= 2

    def test_trend_with_sufficient_data(self):
        ta = TrustAdapter()
        for _ in range(25):
            ta.record_observation("solver", "x", False)
        for _ in range(25):
            ta.record_observation("solver", "x", True)
        trend = ta.get_trend("solver", "x")
        assert isinstance(trend, str)


class TestEventLogAdapter:
    def test_log_event(self):
        el = EventLogAdapter(path=":memory:")
        el.log_event("test_event", source="test")
        events = el.query(event_type="test_event")
        assert len(events) >= 1

    def test_log_case_trajectory(self):
        el = EventLogAdapter(path=":memory:")
        el.log_case_trajectory("t1", "gold", intent="SOLVE")
        # Event type is "case:gold", not "case_trajectory"
        events = el.query(event_type="case:gold")
        assert len(events) >= 1

    def test_log_policy_decision(self):
        el = EventLogAdapter(path=":memory:")
        el.log_policy_decision("a1", "allow", risk_score=0.1, reason="low risk")
        # Event type is "policy:allow"
        events = el.query(event_type="policy:allow")
        assert len(events) >= 1

    def test_quality_distribution(self):
        el = EventLogAdapter(path=":memory:")
        el.log_case_trajectory("t1", "gold")
        el.log_case_trajectory("t2", "gold")
        el.log_case_trajectory("t3", "silver")
        el.log_case_trajectory("t4", "quarantine")
        dist = el.get_quality_distribution()
        assert dist.get("gold", 0) == 2

    def test_count_by_type(self):
        el = EventLogAdapter(path=":memory:")
        el.log_event("event_a")
        el.log_event("event_a")
        el.log_event("event_b")
        counts = el.count_by_type()
        assert counts.get("event_a", 0) == 2

    def test_stats(self):
        el = EventLogAdapter(path=":memory:")
        el.log_event("test")
        stats = el.stats()
        assert isinstance(stats, dict)
