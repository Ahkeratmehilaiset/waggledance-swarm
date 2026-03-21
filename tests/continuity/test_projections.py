# SPDX-License-Identifier: Apache-2.0
"""Tests for projections — narrative, introspection, autobiographical, validator (v3.2)."""

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from waggledance.core.projections.narrative_projector import (
    project_narrative, clear_cache,
)
from waggledance.core.projections.projection_validator import (
    validate_projection, ValidationResult,
)
from waggledance.core.projections.introspection_view import (
    build_introspection, filter_by_profile, IntrospectionSnapshot,
)
from waggledance.core.projections.autobiographical_index import (
    query_episodes, EpisodeEntry, AutobiographicalSummary,
)


# ── Narrative Projector ──────────────────────────────────────

class TestNarrativeProjector:
    def setup_method(self):
        clear_cache()

    def test_returns_narrative_string(self):
        result = project_narrative()
        assert isinstance(result["narrative"], str)
        assert result["language"] == "en"
        assert result["cached"] is False

    def test_includes_goals(self):
        goals = [{"status": "executing", "description": "Monitor hive", "priority": 5}]
        result = project_narrative(goals=goals)
        assert "Monitor hive" in result["narrative"]
        assert result["sections"]["goals_count"] == 1

    def test_includes_promises(self):
        promises = [{"description": "Report daily health"}]
        result = project_narrative(promises=promises)
        assert "PROMISED" in result["narrative"]
        assert result["sections"]["promises_count"] == 1

    def test_uncertainty_labels(self):
        low = project_narrative(uncertainty_score=0.1)
        assert "low" in low["narrative"].lower() or "matala" in low["narrative"].lower()
        clear_cache()
        high = project_narrative(uncertainty_score=0.8)
        assert "high" in high["narrative"].lower() or "korkea" in high["narrative"].lower()

    def test_finnish_language(self):
        result = project_narrative(language="fi")
        assert result["language"] == "fi"
        assert "Järjestelmäkertomus" in result["narrative"]

    def test_cache_hit(self):
        r1 = project_narrative(uncertainty_score=0.5)
        assert r1["cached"] is False
        r2 = project_narrative(uncertainty_score=0.5)
        assert r2["cached"] is True

    def test_cache_miss_on_different_input(self):
        r1 = project_narrative(uncertainty_score=0.5)
        clear_cache()
        r2 = project_narrative(uncertainty_score=0.6)
        assert r2["cached"] is False

    def test_attention_allocation_in_narrative(self):
        att = {"critical": 40, "normal": 35, "background": 15, "reflection": 10}
        result = project_narrative(attention_allocation=att)
        assert "40%" in result["narrative"]

    def test_observability_gaps(self):
        gaps = [{"entity": "sensor_1", "reason": "offline"}]
        result = project_narrative(observability_gaps=gaps)
        assert result["sections"]["gaps_count"] == 1


# ── Projection Validator ─────────────────────────────────────

class TestProjectionValidator:
    def test_valid_projection(self):
        proj = {"narrative": "test", "timestamp": time.time()}
        result = validate_projection(proj)
        assert result.valid is True
        assert len(result.errors) == 0

    def test_stale_timestamp_warning(self):
        proj = {"narrative": "test", "timestamp": time.time() - 600}
        result = validate_projection(proj, max_age_seconds=300)
        assert any("stale" in w.lower() for w in result.warnings)

    def test_unknown_entity_error(self):
        proj = {"entity_ids": ["e1", "e2"], "timestamp": time.time()}
        result = validate_projection(proj, known_entity_ids={"e1"})
        assert result.valid is False
        assert any("e2" in e for e in result.errors)

    def test_unknown_goal_error(self):
        proj = {"goal_ids": ["g_unknown"], "timestamp": time.time()}
        result = validate_projection(proj, known_goal_ids={"g1", "g2"})
        assert result.valid is False

    def test_unknown_motive_error(self):
        proj = {"motive_ids": ["m_bad"], "timestamp": time.time()}
        result = validate_projection(proj, known_motive_ids={"reliability", "user_trust"})
        assert result.valid is False

    def test_no_hidden_dependencies(self):
        """Validator works without goal_engine — just pass known_goal_ids or None."""
        proj = {"goal_ids": ["g1"], "timestamp": time.time()}
        # No known_goal_ids passed → no validation on goals
        result = validate_projection(proj, known_goal_ids=None)
        assert result.valid is True  # no error because we didn't pass known set

    def test_missing_narrative_warning(self):
        proj = {"timestamp": time.time()}
        result = validate_projection(proj)
        assert any("narrative" in w.lower() for w in result.warnings)

    def test_to_dict(self):
        proj = {"narrative": "ok", "timestamp": time.time()}
        result = validate_projection(proj)
        d = result.to_dict()
        assert "valid" in d
        assert "errors" in d


# ── Introspection View ───────────────────────────────────────

class TestIntrospectionView:
    def test_build_basic(self):
        snap = build_introspection()
        assert isinstance(snap, IntrospectionSnapshot)
        assert snap.uncertainty_score == 0.0

    def test_build_with_data(self):
        snap = build_introspection(
            observed_facts=[{"type": "temperature", "value": 35.5}],
            promised_to_user=[{"description": "daily report"}],
            uncertainty_score=0.42,
        )
        assert len(snap.observed_facts) == 1
        assert len(snap.promised_to_user) == 1
        assert snap.uncertainty_score == 0.42

    def test_to_dict_has_fact_counts(self):
        snap = build_introspection(
            observed_facts=[{"a": 1}],
            inferred_facts=[{"b": 2}, {"c": 3}],
        )
        d = snap.to_dict()
        assert d["fact_counts"]["observed"] == 1
        assert d["fact_counts"]["inferred"] == 2

    def test_profile_apiary_full_view(self):
        snap = build_introspection(
            simulated_facts=[{"sim": True}],
            self_reflection_entries=[{"entry": "test"}],
        )
        view = filter_by_profile(snap, "APIARY")
        assert len(view["simulated_facts"]) == 1
        assert len(view["self_reflection_entries"]) == 1

    def test_profile_home_hides_simulated(self):
        snap = build_introspection(
            simulated_facts=[{"sim": True}],
            self_reflection_entries=[{"entry": "test"}],
        )
        view = filter_by_profile(snap, "HOME")
        assert len(view["simulated_facts"]) == 0
        assert len(view["self_reflection_entries"]) == 0

    def test_profile_gadget_summary_only(self):
        snap = build_introspection(
            observed_facts=[{"a": 1}],
            promised_to_user=[{"d": "test"}],
        )
        view = filter_by_profile(snap, "GADGET")
        assert "fact_counts" in view
        assert "observed_facts" not in view  # detail stripped
        assert view["promises_count"] == 1


# ── Autobiographical Index ───────────────────────────────────

class TestAutobiographicalIndex:
    def _ep(self, days_ago=1, goal_id="", significance=0.5,
            consolidated=False, episode_id=None):
        return {
            "episode_id": episode_id or f"ep_{days_ago}",
            "timestamp": time.time() - days_ago * 86400,
            "goal_id": goal_id,
            "goal_description": f"goal for ep_{days_ago}",
            "entities": ["entity_a"],
            "outcome": "success",
            "significance_score": significance,
            "consolidated": consolidated,
            "quality_grade": "gold" if significance > 0.7 else "bronze",
        }

    def test_basic_query(self):
        episodes = [self._ep(days_ago=1), self._ep(days_ago=2)]
        summary = query_episodes(episodes, days=7)
        assert summary.total_episodes == 2
        assert len(summary.entries) == 2

    def test_time_range_filter(self):
        episodes = [self._ep(days_ago=1), self._ep(days_ago=30)]
        summary = query_episodes(episodes, days=7)
        assert len(summary.entries) == 1  # only the 1-day-old one

    def test_goal_id_filter(self):
        episodes = [
            self._ep(days_ago=1, goal_id="g1"),
            self._ep(days_ago=2, goal_id="g2"),
        ]
        summary = query_episodes(episodes, days=7, goal_id="g1")
        assert len(summary.entries) == 1
        assert summary.entries[0].goal_id == "g1"

    def test_significance_filter(self):
        episodes = [
            self._ep(days_ago=1, significance=0.9),
            self._ep(days_ago=2, significance=0.1),
        ]
        summary = query_episodes(episodes, days=7, min_significance=0.5)
        assert len(summary.entries) == 1
        assert summary.entries[0].significance_score == 0.9

    def test_consolidated_excluded_when_flag_off(self):
        episodes = [
            self._ep(days_ago=1, consolidated=False),
            self._ep(days_ago=2, consolidated=True),
        ]
        summary = query_episodes(episodes, days=7, include_consolidated=False)
        assert len(summary.entries) == 1
        assert summary.entries[0].consolidated is False

    def test_max_results(self):
        episodes = [self._ep(days_ago=i, episode_id=f"ep_{i}") for i in range(1, 20)]
        summary = query_episodes(episodes, days=30, max_results=5)
        assert len(summary.entries) == 5

    def test_sorted_by_significance(self):
        episodes = [
            self._ep(days_ago=1, significance=0.3, episode_id="low"),
            self._ep(days_ago=2, significance=0.9, episode_id="high"),
        ]
        summary = query_episodes(episodes, days=7)
        assert summary.entries[0].episode_id == "high"

    def test_to_dict(self):
        episodes = [self._ep(days_ago=1)]
        summary = query_episodes(episodes, days=7)
        d = summary.to_dict()
        assert "entries" in d
        assert "total_episodes" in d
        assert d["time_range_days"] == 7


# ── Package import ───────────────────────────────────────────

class TestPackageImport:
    def test_init_exports(self):
        from waggledance.core.projections import (
            project_narrative, validate_projection,
            build_introspection, query_episodes,
        )
        assert callable(project_narrative)
        assert callable(validate_projection)
        assert callable(build_introspection)
        assert callable(query_episodes)
