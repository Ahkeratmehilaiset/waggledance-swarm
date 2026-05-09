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

    def test_all_scores_match_individual_scores(self):
        ta = TrustAdapter()
        for target_id in ("solver-a", "solver-b", "solver-c"):
            ta.record_observation("solver", target_id, True,
                                  confidence=0.9, context="actual")
            ta.record_observation("solver", target_id, False,
                                  confidence=0.4, context="simulated")

        scores = ta.get_all_scores("solver")
        expected = {
            f"solver:{target_id}": ta.get_trust_score("solver", target_id)
            for target_id in ("solver-a", "solver-b", "solver-c")
        }
        assert scores == pytest.approx(expected)

    def test_ranking_uses_single_pass_not_public_per_target_scorer(self, monkeypatch):
        ta = TrustAdapter()
        for i in range(8):
            target_id = f"solver-{i}"
            ta.record_observation("solver", target_id, i % 2 == 0)
            ta.record_observation("solver", target_id, True)

        def fail_if_called(*args, **kwargs):  # pragma: no cover - failure path
            raise AssertionError("get_ranking should not call get_trust_score")

        monkeypatch.setattr(ta, "get_trust_score", fail_if_called)
        ranking = ta.get_ranking("solver", limit=3)

        assert len(ranking) == 3
        assert all(row["target"].startswith("solver:") for row in ranking)

    def test_all_scores_target_type_filter_is_exact_prefix(self):
        ta = TrustAdapter()
        ta.record_observation("solver", "same-id", True)
        ta.record_observation("capability", "same-id", False)

        assert set(ta.get_all_scores("solver")) == {"solver:same-id"}
        assert set(ta.get_all_scores("capability")) == {
            "capability:same-id"
        }

    def test_score_totals_follow_retained_observation_window(self):
        ta = TrustAdapter()
        ta._max_per_target = 3
        for _ in range(3):
            ta.record_observation("solver", "trimmed", False)
        for _ in range(3):
            ta.record_observation("solver", "trimmed", True)

        assert ta.get_trust_score("solver", "trimmed") == 1.0
        assert ta.get_all_scores("solver")["solver:trimmed"] == 1.0

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

    def test_buffer_caps_at_max_and_keeps_newest(self):
        """The deque(maxlen) swap (Phase D Candidate 3) must drop the
        OLDEST entries on overflow, keeping the newest _max_buffer.
        Pin this contract so any future buffer change can't silently
        flip eviction direction."""
        el = EventLogAdapter(path=":memory:")
        el._buffer = type(el._buffer)(maxlen=5)
        el._max_buffer = 5
        for i in range(8):
            el.log_event("evt", source="src", capability_id=f"c{i}")
        assert len(el._buffer) == 5
        cap_ids = [e.capability_id for e in el._buffer]
        # First three (c0,c1,c2) evicted; newest five remain in order.
        assert cap_ids == ["c3", "c4", "c5", "c6", "c7"]

    def test_query_returns_newest_first_after_overflow(self):
        """`query()` reverses the buffer so callers see most-recent
        first. Verify this still holds after deque eviction."""
        el = EventLogAdapter(path=":memory:")
        el._buffer = type(el._buffer)(maxlen=3)
        el._max_buffer = 3
        for i in range(5):
            el.log_event("evt", capability_id=f"c{i}")
        results = el.query(limit=10)
        # Newest -> oldest in the retained window
        assert [e.capability_id for e in results] == ["c4", "c3", "c2"]

    def test_log_event_avoids_O_N_trim_under_burst(self):
        """Regression guard for the headline win: bursting 5x maxlen
        events must complete in well under the old impl's tail-copy
        cost. Old impl trimmed via `self._buffer[-N:]` on every
        over-cap append (O(N) per append). New impl is O(1) per
        append. Uses a no-op legacy ledger so the timing measures
        the buffer path only — same shape the microbench uses."""
        import time

        class _NoopLedger:
            def log(self, **kwargs):
                pass

        el = EventLogAdapter(legacy_ledger=_NoopLedger())
        # Default maxlen is 1000; burst 5000 events so the old impl
        # would have done 4000 tail-copy trims.
        n = 5000
        start = time.perf_counter()
        for i in range(n):
            el.log_event("burst", capability_id=f"c{i}")
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert len(el._buffer) == 1000
        # 50ms is generous headroom; old impl on the scout machine
        # measured ~81ms (with the same no-op ledger), so deque
        # should beat that comfortably. Anything past 50ms means
        # the trim path regressed back to O(N).
        assert elapsed_ms < 50.0, (
            f"5000-event burst took {elapsed_ms:.2f}ms — "
            f"expected O(1) per-append trim with deque"
        )
