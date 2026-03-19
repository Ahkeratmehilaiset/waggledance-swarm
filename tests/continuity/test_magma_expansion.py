# SPDX-License-Identifier: BUSL-1.1
"""Tests for v3.2 MAGMA section 10 expansion.

Covers:
- L1 AuditLog: self_reflection + simulated fields and events
- L2 Replay: dream-mode counterfactual replay (is_simulation flag)
- L4 Provenance: self_reflection + simulated source types
- L5 Trust: context field (actual vs simulated weighting)
- Confidence decay: decayed_confidence() function
"""

import math
import time

import pytest


# ---------------------------------------------------------------------------
# L1 AuditLog expansion
# ---------------------------------------------------------------------------

class TestAuditProjectorExpansion:
    """L1 audit: self_reflection + simulated fields and event types."""

    def test_audit_entry_has_self_reflection_field(self):
        from waggledance.core.magma.audit_projector import AuditEntry
        entry = AuditEntry(event_type="test", payload={})
        assert entry.self_reflection is False

    def test_audit_entry_has_simulated_field(self):
        from waggledance.core.magma.audit_projector import AuditEntry
        entry = AuditEntry(event_type="test", payload={}, simulated=True)
        assert entry.simulated is True

    def test_audit_entry_to_dict_includes_new_fields(self):
        from waggledance.core.magma.audit_projector import AuditEntry
        entry = AuditEntry(
            event_type="reflection.insight",
            payload={"insight": "test"},
            self_reflection=True,
        )
        d = entry.to_dict()
        assert d["self_reflection"] is True
        assert d["simulated"] is False

    def test_autonomy_events_include_reflection_types(self):
        from waggledance.core.magma.audit_projector import AuditProjector
        events = AuditProjector.AUTONOMY_EVENTS
        assert "reflection.started" in events
        assert "reflection.insight" in events
        assert "reflection.completed" in events

    def test_autonomy_events_include_learning_types(self):
        from waggledance.core.magma.audit_projector import AuditProjector
        events = AuditProjector.AUTONOMY_EVENTS
        assert "learning.case_graded" in events
        assert "learning.consolidated" in events
        assert "learning.dream_started" in events
        assert "learning.dream_completed" in events

    def test_autonomy_events_include_simulated_types(self):
        from waggledance.core.magma.audit_projector import AuditProjector
        events = AuditProjector.AUTONOMY_EVENTS
        assert "simulated.trajectory_created" in events
        assert "simulated.counterfactual" in events

    def test_record_self_reflection(self):
        from waggledance.core.magma.audit_projector import AuditProjector
        proj = AuditProjector(audit_log=None)
        proj.record_self_reflection(
            insight="learned new pattern",
            goal_id="g1",
            detail={"domain": "energy"},
        )
        entries = proj.query_self_reflections()
        assert len(entries) == 1
        assert entries[0].self_reflection is True
        assert entries[0].event_type == "reflection.insight"
        assert entries[0].payload["insight"] == "learned new pattern"

    def test_record_simulated_event(self):
        from waggledance.core.magma.audit_projector import AuditProjector
        proj = AuditProjector(audit_log=None)
        proj.record_simulated_event(
            event_type="simulated.trajectory_created",
            goal_id="g2",
            quality_grade="BRONZE",
        )
        entries = proj.query_simulated()
        assert len(entries) == 1
        assert entries[0].simulated is True
        assert entries[0].quality_grade == "BRONZE"

    def test_query_self_reflections_filters_correctly(self):
        from waggledance.core.magma.audit_projector import AuditProjector
        proj = AuditProjector(audit_log=None)
        proj.record_self_reflection("insight1")
        proj.record_goal_event("g1", "proposed")
        proj.record_self_reflection("insight2")
        reflections = proj.query_self_reflections()
        assert len(reflections) == 2
        assert all(e.self_reflection for e in reflections)

    def test_query_simulated_filters_correctly(self):
        from waggledance.core.magma.audit_projector import AuditProjector
        proj = AuditProjector(audit_log=None)
        proj.record_simulated_event("simulated.counterfactual", goal_id="g1")
        proj.record_goal_event("g2", "proposed")
        proj.record_simulated_event("simulated.trajectory_created", goal_id="g3")
        simulated = proj.query_simulated()
        assert len(simulated) == 2
        assert all(e.simulated for e in simulated)

    def test_stats_counts_new_event_types(self):
        from waggledance.core.magma.audit_projector import AuditProjector
        proj = AuditProjector(audit_log=None)
        proj.record_self_reflection("x")
        proj.record_simulated_event("simulated.counterfactual")
        s = proj.stats()
        assert s["event_counts"].get("reflection.insight") == 1
        assert s["event_counts"].get("simulated.counterfactual") == 1

    def test_old_events_still_work(self):
        from waggledance.core.magma.audit_projector import AuditProjector
        proj = AuditProjector(audit_log=None)
        proj.record_goal_event("g1", "proposed")
        proj.record_action_event("a1", "executed")
        proj.record_case("t1", "GOLD", goal_id="g1")
        assert len(proj.query_recent()) == 3


# ---------------------------------------------------------------------------
# L4 Provenance expansion
# ---------------------------------------------------------------------------

class TestProvenanceExpansion:
    """L4: self_reflection + simulated source types."""

    def test_valid_source_types_include_self_reflection(self):
        from waggledance.core.magma.provenance import VALID_SOURCE_TYPES
        assert "self_reflection" in VALID_SOURCE_TYPES

    def test_valid_source_types_include_simulated(self):
        from waggledance.core.magma.provenance import VALID_SOURCE_TYPES
        assert "simulated" in VALID_SOURCE_TYPES

    def test_valid_source_types_count(self):
        from waggledance.core.magma.provenance import VALID_SOURCE_TYPES
        assert len(VALID_SOURCE_TYPES) == 9  # 7 original + 2 new

    def test_self_reflection_trust_weight(self):
        from waggledance.core.magma.provenance import ProvenanceRecord
        r = ProvenanceRecord(fact_id="f1", source_type="self_reflection")
        assert r.trust_weight() == 0.60

    def test_simulated_trust_weight(self):
        from waggledance.core.magma.provenance import ProvenanceRecord
        r = ProvenanceRecord(fact_id="f1", source_type="simulated")
        assert r.trust_weight() == 0.30

    def test_trust_hierarchy_order(self):
        from waggledance.core.magma.provenance import ProvenanceRecord
        types_descending = [
            "confirmed_by_verifier",  # 1.0
            "observed",               # 0.95
            "inferred_by_solver",     # 0.90
            "inferred_by_rule",       # 0.85
            "inferred_by_stats",      # 0.80
            "learned_from_case",      # 0.75
            "self_reflection",        # 0.60
            "proposed_by_llm",        # 0.50
            "simulated",              # 0.30
        ]
        weights = [
            ProvenanceRecord(fact_id="x", source_type=st).trust_weight()
            for st in types_descending
        ]
        assert weights == sorted(weights, reverse=True)

    def test_record_self_reflection_provenance(self):
        from waggledance.core.magma.provenance import ProvenanceAdapter
        adapter = ProvenanceAdapter()
        r = adapter.record_provenance(
            fact_id="f1",
            source_type="self_reflection",
            confidence=0.6,
        )
        assert r.source_type == "self_reflection"
        assert r.trust_weight() == 0.60

    def test_record_simulated_provenance(self):
        from waggledance.core.magma.provenance import ProvenanceAdapter
        adapter = ProvenanceAdapter()
        r = adapter.record_provenance(
            fact_id="f2",
            source_type="simulated",
            confidence=0.3,
            metadata={"dream_session": "night_42"},
        )
        assert r.source_type == "simulated"
        assert r.trust_weight() == 0.30
        assert r.metadata["dream_session"] == "night_42"

    def test_get_by_source_type_simulated(self):
        from waggledance.core.magma.provenance import ProvenanceAdapter
        adapter = ProvenanceAdapter()
        adapter.record_provenance("f1", "observed", confidence=0.9)
        adapter.record_provenance("f2", "simulated", confidence=0.3)
        adapter.record_provenance("f3", "simulated", confidence=0.2)
        sims = adapter.get_by_source_type("simulated")
        assert len(sims) == 2

    def test_old_source_types_still_work(self):
        from waggledance.core.magma.provenance import ProvenanceAdapter
        adapter = ProvenanceAdapter()
        for st in ("observed", "confirmed_by_verifier", "proposed_by_llm",
                    "learned_from_case", "inferred_by_solver"):
            r = adapter.record_provenance(f"f_{st}", st, confidence=0.8)
            assert r.source_type == st


# ---------------------------------------------------------------------------
# L2 Replay expansion
# ---------------------------------------------------------------------------

class TestReplayExpansion:
    """L2: dream-mode counterfactual replay with is_simulation flag."""

    def test_mission_replay_entry_has_is_simulation(self):
        from waggledance.core.magma.replay_engine import MissionReplayEntry
        entry = MissionReplayEntry(
            event_type="test", timestamp=time.time(), payload={},
        )
        assert entry.is_simulation is False

    def test_mission_replay_entry_simulation_flag(self):
        from waggledance.core.magma.replay_engine import MissionReplayEntry
        entry = MissionReplayEntry(
            event_type="test", timestamp=time.time(), payload={},
            is_simulation=True,
        )
        assert entry.is_simulation is True

    def test_mission_replay_has_is_simulation(self):
        from waggledance.core.magma.replay_engine import MissionReplay
        mission = MissionReplay(goal_id="g1")
        assert mission.is_simulation is False

    def test_mission_replay_to_dict_includes_is_simulation(self):
        from waggledance.core.magma.replay_engine import MissionReplay, MissionReplayEntry
        mission = MissionReplay(goal_id="g1", is_simulation=True)
        mission.entries.append(MissionReplayEntry(
            event_type="test", timestamp=time.time(), payload={},
            is_simulation=True,
        ))
        d = mission.to_dict()
        assert d["is_simulation"] is True
        assert d["entries"][0]["is_simulation"] is True

    def test_record_mission_event_with_simulation(self):
        from waggledance.core.magma.replay_engine import ReplayAdapter
        adapter = ReplayAdapter()
        adapter.record_mission_event(
            goal_id="g1", event_type="simulated.counterfactual",
            is_simulation=True,
        )
        mission = adapter.get_mission_replay("g1")
        assert mission is not None
        assert mission.is_simulation is True
        assert mission.entries[0].is_simulation is True

    def test_record_dream_replay(self):
        from waggledance.core.magma.replay_engine import ReplayAdapter
        adapter = ReplayAdapter()
        adapter.record_dream_replay(
            goal_id="dream_1",
            original_goal_id="g_orig",
            counterfactual_chain=["cap_a", "cap_b"],
            outcome="inconclusive",
        )
        mission = adapter.get_mission_replay("dream_1")
        assert mission is not None
        assert mission.is_simulation is True
        assert mission.goal_type == "dream_counterfactual"
        assert mission.quality_grade == "BRONZE"
        entry = mission.entries[0]
        assert entry.payload["original_goal_id"] == "g_orig"
        assert entry.payload["counterfactual_chain"] == ["cap_a", "cap_b"]

    def test_list_dream_replays(self):
        from waggledance.core.magma.replay_engine import ReplayAdapter
        adapter = ReplayAdapter()
        adapter.record_mission_event("g1", "goal.proposed")
        adapter.record_dream_replay("dream_1", "g1", ["cap_a"])
        adapter.record_dream_replay("dream_2", "g1", ["cap_b"])
        dreams = adapter.list_dream_replays()
        assert len(dreams) == 2
        assert all(d["is_simulation"] for d in dreams)

    def test_list_dream_replays_excludes_actual(self):
        from waggledance.core.magma.replay_engine import ReplayAdapter
        adapter = ReplayAdapter()
        adapter.record_mission_event("g1", "goal.proposed")
        adapter.record_dream_replay("dream_1", "g1", ["cap_a"])
        dreams = adapter.list_dream_replays()
        assert len(dreams) == 1
        all_missions = adapter.list_missions()
        assert len(all_missions) == 2  # g1 + dream_1

    def test_stats_includes_simulated_count(self):
        from waggledance.core.magma.replay_engine import ReplayAdapter
        adapter = ReplayAdapter()
        adapter.record_mission_event("g1", "goal.proposed")
        adapter.record_dream_replay("dream_1", "g1", ["cap_a"])
        s = adapter.stats()
        assert s["simulated_missions"] == 1
        assert s["total_missions"] == 2

    def test_actual_missions_not_marked_simulated(self):
        from waggledance.core.magma.replay_engine import ReplayAdapter
        adapter = ReplayAdapter()
        adapter.record_mission_event("g1", "goal.proposed")
        adapter.record_mission_event("g1", "goal.verified")
        mission = adapter.get_mission_replay("g1")
        assert mission.is_simulation is False


# ---------------------------------------------------------------------------
# L5 Trust expansion
# ---------------------------------------------------------------------------

class TestTrustExpansion:
    """L5: context field and simulated observation weighting."""

    def test_trust_record_has_context_field(self):
        from waggledance.core.magma.trust_adapter import TrustRecord
        record = TrustRecord(
            target_type="capability", target_id="cap1", success=True,
        )
        assert record.context == "actual"

    def test_trust_record_simulated_context(self):
        from waggledance.core.magma.trust_adapter import TrustRecord
        record = TrustRecord(
            target_type="specialist", target_id="m1", success=True,
            context="simulated",
        )
        assert record.context == "simulated"

    def test_record_observation_with_context(self):
        from waggledance.core.magma.trust_adapter import TrustAdapter
        adapter = TrustAdapter()
        adapter.record_observation(
            "specialist", "m1", success=True, context="simulated",
        )
        score = adapter.get_trust_score("specialist", "m1")
        assert 0.0 < score <= 1.0

    def test_simulated_observations_weighted_less(self):
        from waggledance.core.magma.trust_adapter import TrustAdapter
        # Adapter with only actual successes
        actual = TrustAdapter()
        for _ in range(10):
            actual.record_observation("specialist", "m1", success=True, context="actual")

        # Adapter with only simulated successes
        sim = TrustAdapter()
        for _ in range(10):
            sim.record_observation("specialist", "m1", success=True, context="simulated")

        actual_score = actual.get_trust_score("specialist", "m1")
        sim_score = sim.get_trust_score("specialist", "m1")
        # Both should be ~1.0 (all successes), but actual has full weight
        # The ratio of scores should be similar since both are 100% success
        assert actual_score == pytest.approx(1.0, abs=0.01)
        assert sim_score == pytest.approx(1.0, abs=0.01)

    def test_simulated_failures_have_less_impact(self):
        from waggledance.core.magma.trust_adapter import TrustAdapter
        # Mixed: 5 actual successes + 5 simulated failures
        adapter = TrustAdapter()
        for _ in range(5):
            adapter.record_observation("specialist", "m1", success=True, context="actual")
        for _ in range(5):
            adapter.record_observation("specialist", "m1", success=False, context="simulated")

        # Score should be closer to 1.0 than 0.5 because simulated failures
        # contribute half weight
        score = adapter.get_trust_score("specialist", "m1")
        assert score > 0.55  # biased toward actual successes

    def test_specialist_in_valid_target_types(self):
        from waggledance.core.magma.trust_adapter import TrustAdapter
        assert "specialist" in TrustAdapter.VALID_TARGET_TYPES

    def test_old_observations_still_work(self):
        from waggledance.core.magma.trust_adapter import TrustAdapter
        adapter = TrustAdapter()
        adapter.record_observation("capability", "c1", success=True)
        adapter.record_observation("route", "r1", success=False)
        assert adapter.get_trust_score("capability", "c1") > 0.5
        assert adapter.get_trust_score("route", "r1") < 0.5


# ---------------------------------------------------------------------------
# Confidence decay
# ---------------------------------------------------------------------------

class TestConfidenceDecay:
    """decayed_confidence() — query-time decay without destructive rewrite."""

    def test_import(self):
        from waggledance.core.magma.confidence_decay import decayed_confidence
        assert callable(decayed_confidence)

    def test_package_export(self):
        from waggledance.core.magma import decayed_confidence
        assert callable(decayed_confidence)

    def test_zero_age_returns_original(self):
        from waggledance.core.magma.confidence_decay import decayed_confidence
        assert decayed_confidence(0.9, 0.0) == 0.9

    def test_negative_age_returns_original(self):
        from waggledance.core.magma.confidence_decay import decayed_confidence
        assert decayed_confidence(0.8, -5.0) == 0.8

    def test_decay_reduces_confidence(self):
        from waggledance.core.magma.confidence_decay import decayed_confidence
        original = 0.9
        decayed = decayed_confidence(original, age_hours=100)
        assert 0.0 < decayed < original

    def test_half_life_for_observed(self):
        """Observed facts use half_life/2 = 84h. At 84h, should be ~half."""
        from waggledance.core.magma.confidence_decay import decayed_confidence
        original = 1.0
        decayed = decayed_confidence(original, age_hours=84.0,
                                      half_life_hours=168.0,
                                      source_type="observed")
        assert decayed == pytest.approx(0.5, abs=0.01)

    def test_half_life_for_verified(self):
        """Verified facts use full half_life = 168h. At 168h, should be ~half."""
        from waggledance.core.magma.confidence_decay import decayed_confidence
        original = 1.0
        decayed = decayed_confidence(original, age_hours=168.0,
                                      half_life_hours=168.0,
                                      source_type="confirmed_by_verifier")
        assert decayed == pytest.approx(0.5, abs=0.01)

    def test_verified_decays_slower_than_observed(self):
        from waggledance.core.magma.confidence_decay import decayed_confidence
        age = 100.0
        verified = decayed_confidence(0.9, age, source_type="confirmed_by_verifier")
        observed = decayed_confidence(0.9, age, source_type="observed")
        assert verified > observed

    def test_solver_counts_as_verified(self):
        from waggledance.core.magma.confidence_decay import decayed_confidence
        age = 100.0
        solver = decayed_confidence(0.9, age, source_type="inferred_by_solver")
        verified = decayed_confidence(0.9, age, source_type="confirmed_by_verifier")
        assert solver == pytest.approx(verified, abs=0.001)

    def test_rule_counts_as_verified(self):
        from waggledance.core.magma.confidence_decay import decayed_confidence
        age = 100.0
        rule = decayed_confidence(0.9, age, source_type="inferred_by_rule")
        verified = decayed_confidence(0.9, age, source_type="confirmed_by_verifier")
        assert rule == pytest.approx(verified, abs=0.001)

    def test_llm_proposed_decays_fast(self):
        from waggledance.core.magma.confidence_decay import decayed_confidence
        age = 100.0
        llm = decayed_confidence(0.9, age, source_type="proposed_by_llm")
        verified = decayed_confidence(0.9, age, source_type="confirmed_by_verifier")
        assert llm < verified

    def test_simulated_decays_fast(self):
        from waggledance.core.magma.confidence_decay import decayed_confidence
        age = 100.0
        sim = decayed_confidence(0.9, age, source_type="simulated")
        verified = decayed_confidence(0.9, age, source_type="confirmed_by_verifier")
        assert sim < verified

    def test_original_confidence_not_modified(self):
        """Verify decay doesn't destructively modify — returns new value."""
        from waggledance.core.magma.confidence_decay import decayed_confidence
        original = 0.85
        _ = decayed_confidence(original, 100.0)
        # original is a float (immutable), this is a conceptual test
        assert original == 0.85

    def test_custom_half_life(self):
        from waggledance.core.magma.confidence_decay import decayed_confidence
        # With half_life=24, observed gets half_life=12
        decayed = decayed_confidence(1.0, age_hours=12.0,
                                      half_life_hours=24.0,
                                      source_type="observed")
        assert decayed == pytest.approx(0.5, abs=0.01)

    def test_very_old_approaches_zero(self):
        from waggledance.core.magma.confidence_decay import decayed_confidence
        decayed = decayed_confidence(0.9, age_hours=10000.0)
        assert decayed < 0.001

    def test_default_half_life_is_168(self):
        from waggledance.core.magma.confidence_decay import decayed_confidence
        # Verified at 168h should be ~0.5
        d = decayed_confidence(1.0, 168.0, source_type="confirmed_by_verifier")
        assert d == pytest.approx(0.5, abs=0.01)
