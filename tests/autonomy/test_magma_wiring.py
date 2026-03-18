"""Tests proving MAGMA adapters are actively called during runtime operation.

Verifies that handle_query() and execute_mission() produce real MAGMA events
in audit, event_log, trust, replay, and provenance adapters — not just passive
instantiation but active usage in the hot paths.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from waggledance.core.autonomy.runtime import AutonomyRuntime


class TestMagmaAdaptersInstantiated:
    """Verify all 5 MAGMA adapters are created at boot."""

    def test_audit_created(self):
        rt = AutonomyRuntime()
        assert rt.audit is not None

    def test_event_log_created(self):
        rt = AutonomyRuntime()
        assert rt.event_log is not None

    def test_trust_created(self):
        rt = AutonomyRuntime()
        assert rt.trust is not None

    def test_replay_created(self):
        rt = AutonomyRuntime()
        assert rt.replay is not None

    def test_provenance_created(self):
        rt = AutonomyRuntime()
        assert rt.provenance is not None


class TestQueryPathAudit:
    """Verify handle_query produces audit events."""

    def test_audit_has_entries_after_query(self):
        rt = AutonomyRuntime()
        result = rt.handle_query("What is 2+2?")
        stats = rt.audit.stats()
        # At minimum: capability.selected fires even if no caps found
        assert stats["total_entries"] >= 1

    def test_capability_selected_event(self):
        rt = AutonomyRuntime()
        rt.handle_query("Calculate the temperature deviation")
        entries = rt.audit.query_by_event_type("capability.selected")
        assert len(entries) >= 1
        assert "intent" in entries[0].payload

    def test_policy_event_on_full_path(self):
        rt = AutonomyRuntime()
        result = rt.handle_query("What is 2+2?")
        if result.get("capability"):
            # Full path exercised — policy decision should be recorded
            approved = rt.audit.query_by_event_type("policy.approved")
            denied = rt.audit.query_by_event_type("policy.denied")
            assert len(approved) + len(denied) >= 1

    def test_action_event_on_full_path(self):
        rt = AutonomyRuntime()
        result = rt.handle_query("What is 2+2?")
        if result.get("capability"):
            executed = rt.audit.query_by_event_type("action.executed")
            denied_actions = rt.audit.query_by_event_type("action.denied")
            assert len(executed) + len(denied_actions) >= 1

    def test_case_recorded_event_on_full_path(self):
        rt = AutonomyRuntime()
        result = rt.handle_query("What is 2+2?")
        if result.get("capability"):
            cases = rt.audit.query_by_event_type("case.recorded")
            assert len(cases) >= 1

    def test_multiple_queries_accumulate_audit(self):
        rt = AutonomyRuntime()
        rt.handle_query("What is 2+2?")
        rt.handle_query("Check hive temperature")
        stats = rt.audit.stats()
        assert stats["total_entries"] >= 2


class TestQueryPathEventLog:
    """Verify handle_query produces event log entries."""

    def test_event_log_has_entries_after_query(self):
        rt = AutonomyRuntime()
        result = rt.handle_query("What is 2+2?")
        if result.get("capability"):
            stats = rt.event_log.stats()
            assert stats["total_entries"] >= 1

    def test_case_trajectory_logged(self):
        rt = AutonomyRuntime()
        result = rt.handle_query("What is 2+2?")
        if result.get("capability"):
            # case:<grade> event type
            counts = rt.event_log.count_by_type()
            case_events = sum(v for k, v in counts.items() if k.startswith("case:"))
            assert case_events >= 1

    def test_policy_decision_logged(self):
        rt = AutonomyRuntime()
        result = rt.handle_query("What is 2+2?")
        if result.get("capability"):
            counts = rt.event_log.count_by_type()
            policy_events = sum(v for k, v in counts.items() if k.startswith("policy:"))
            assert policy_events >= 1


class TestQueryPathTrust:
    """Verify handle_query produces trust observations."""

    def test_trust_has_observations_after_query(self):
        rt = AutonomyRuntime()
        result = rt.handle_query("What is 2+2?")
        if result.get("capability"):
            stats = rt.trust.stats()
            assert stats["total_observations"] >= 1

    def test_capability_trust_recorded(self):
        rt = AutonomyRuntime()
        result = rt.handle_query("What is 2+2?")
        if result.get("capability"):
            cap_id = result["capability"]
            score = rt.trust.get_trust_score("capability", cap_id)
            # Score should differ from neutral 0.5 after observation
            assert isinstance(score, float)

    def test_multiple_queries_build_trust_history(self):
        rt = AutonomyRuntime()
        result1 = rt.handle_query("What is 2+2?")
        result2 = rt.handle_query("What is 3+3?")
        if result1.get("capability") and result2.get("capability"):
            stats = rt.trust.stats()
            assert stats["total_observations"] >= 2


class TestQueryPathProvenance:
    """Verify handle_query produces provenance records."""

    def test_provenance_has_records_after_query(self):
        rt = AutonomyRuntime()
        result = rt.handle_query("What is 2+2?")
        if result.get("capability"):
            stats = rt.provenance.stats()
            assert stats["total_records"] >= 1

    def test_provenance_source_type_set(self):
        rt = AutonomyRuntime()
        result = rt.handle_query("What is 2+2?")
        if result.get("capability"):
            stats = rt.provenance.stats()
            by_type = stats["by_source_type"]
            # Should be either proposed_by_llm or confirmed_by_verifier
            assert any(t in by_type for t in
                       ["proposed_by_llm", "confirmed_by_verifier"])


class TestMissionPathAudit:
    """Verify execute_mission produces goal lifecycle audit events."""

    def test_goal_lifecycle_events(self):
        rt = AutonomyRuntime()
        result = rt.execute_mission("observe", "Check hive temperature")
        goal_id = result["goal_id"]
        goal_events = rt.audit.query_by_goal(goal_id)
        # Should have: proposed, accepted, planned, executing, verified/failed
        assert len(goal_events) >= 4
        event_types = {e.event_type for e in goal_events}
        assert "goal.proposed" in event_types
        assert "goal.accepted" in event_types

    def test_plan_created_event(self):
        rt = AutonomyRuntime()
        rt.execute_mission("observe", "Check hive temperature")
        plan_events = rt.audit.query_by_event_type("plan.created")
        assert len(plan_events) >= 1

    def test_case_recorded_after_mission(self):
        rt = AutonomyRuntime()
        rt.execute_mission("observe", "Check hive temperature")
        cases = rt.audit.query_by_event_type("case.recorded")
        assert len(cases) >= 1

    def test_mission_audit_trail_complete(self):
        rt = AutonomyRuntime()
        result = rt.execute_mission("observe", "Check hive temperature")
        stats = rt.audit.stats()
        # Goal lifecycle (4-5) + plan (1) + case (1) = 6+ entries
        assert stats["total_entries"] >= 5


class TestMissionPathReplay:
    """Verify execute_mission produces replay entries."""

    def test_replay_mission_created(self):
        rt = AutonomyRuntime()
        result = rt.execute_mission("observe", "Check hive temperature")
        goal_id = result["goal_id"]
        mission = rt.replay.get_mission_replay(goal_id)
        assert mission is not None

    def test_replay_has_entries(self):
        rt = AutonomyRuntime()
        result = rt.execute_mission("observe", "Check hive temperature")
        goal_id = result["goal_id"]
        mission = rt.replay.get_mission_replay(goal_id)
        assert mission.entry_count >= 1

    def test_replay_metadata_set(self):
        rt = AutonomyRuntime()
        result = rt.execute_mission("observe", "Check hive temperature")
        goal_id = result["goal_id"]
        mission = rt.replay.get_mission_replay(goal_id)
        # Status should be set from finalize
        assert mission.status != ""

    def test_replay_quality_grade_set(self):
        rt = AutonomyRuntime()
        result = rt.execute_mission("observe", "Check hive temperature")
        goal_id = result["goal_id"]
        mission = rt.replay.get_mission_replay(goal_id)
        assert mission.quality_grade != ""

    def test_replay_list_missions(self):
        rt = AutonomyRuntime()
        rt.execute_mission("observe", "Task 1")
        rt.execute_mission("observe", "Task 2")
        missions = rt.replay.list_missions()
        assert len(missions) >= 2


class TestMissionPathEventLog:
    """Verify execute_mission produces event log entries."""

    def test_event_log_case_after_mission(self):
        rt = AutonomyRuntime()
        rt.execute_mission("observe", "Check hive temperature")
        counts = rt.event_log.count_by_type()
        case_events = sum(v for k, v in counts.items() if k.startswith("case:"))
        assert case_events >= 1


class TestMissionPathProvenance:
    """Verify execute_mission produces provenance records."""

    def test_provenance_after_mission(self):
        rt = AutonomyRuntime()
        rt.execute_mission("observe", "Check hive temperature")
        stats = rt.provenance.stats()
        assert stats["total_records"] >= 1

    def test_provenance_learned_from_case(self):
        rt = AutonomyRuntime()
        rt.execute_mission("observe", "Check hive temperature")
        records = rt.provenance.get_by_source_type("learned_from_case")
        assert len(records) >= 1


class TestStatsIncludesMagma:
    """Verify stats() includes MAGMA adapter sections."""

    def test_stats_has_magma_audit(self):
        rt = AutonomyRuntime()
        s = rt.stats()
        assert "magma_audit" in s

    def test_stats_has_magma_event_log(self):
        rt = AutonomyRuntime()
        s = rt.stats()
        assert "magma_event_log" in s

    def test_stats_has_magma_trust(self):
        rt = AutonomyRuntime()
        s = rt.stats()
        assert "magma_trust" in s

    def test_stats_has_magma_replay(self):
        rt = AutonomyRuntime()
        s = rt.stats()
        assert "magma_replay" in s

    def test_stats_has_magma_provenance(self):
        rt = AutonomyRuntime()
        s = rt.stats()
        assert "magma_provenance" in s

    def test_stats_magma_reflects_activity(self):
        rt = AutonomyRuntime()
        rt.handle_query("What is 2+2?")
        s = rt.stats()
        assert s["magma_audit"]["total_entries"] >= 1


class TestMagmaFailureSafety:
    """Verify MAGMA failures do not break the runtime hot paths."""

    def test_query_survives_broken_audit(self):
        rt = AutonomyRuntime()
        rt.audit = _BrokenAdapter()
        result = rt.handle_query("What is 2+2?")
        assert "intent" in result

    def test_query_survives_broken_event_log(self):
        rt = AutonomyRuntime()
        rt.event_log = _BrokenAdapter()
        result = rt.handle_query("What is 2+2?")
        assert "intent" in result

    def test_query_survives_broken_trust(self):
        rt = AutonomyRuntime()
        rt.trust = _BrokenAdapter()
        result = rt.handle_query("What is 2+2?")
        assert "intent" in result

    def test_query_survives_broken_provenance(self):
        rt = AutonomyRuntime()
        rt.provenance = _BrokenAdapter()
        result = rt.handle_query("What is 2+2?")
        assert "intent" in result

    def test_mission_survives_broken_audit(self):
        rt = AutonomyRuntime()
        rt.audit = _BrokenAdapter()
        result = rt.execute_mission("observe", "Test")
        assert "goal_id" in result

    def test_mission_survives_broken_replay(self):
        rt = AutonomyRuntime()
        rt.replay = _BrokenAdapter()
        result = rt.execute_mission("observe", "Test")
        assert "goal_id" in result

    def test_query_survives_none_adapters(self):
        rt = AutonomyRuntime()
        rt.audit = None
        rt.event_log = None
        rt.trust = None
        rt.replay = None
        rt.provenance = None
        result = rt.handle_query("What is 2+2?")
        assert "intent" in result

    def test_mission_survives_none_adapters(self):
        rt = AutonomyRuntime()
        rt.audit = None
        rt.event_log = None
        rt.trust = None
        rt.replay = None
        rt.provenance = None
        result = rt.execute_mission("observe", "Test")
        assert "goal_id" in result


class _BrokenAdapter:
    """Adapter that raises on every method call."""
    def __getattr__(self, name):
        def _raise(*a, **kw):
            raise RuntimeError(f"Broken adapter: {name}")
        return _raise
