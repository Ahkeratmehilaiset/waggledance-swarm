# SPDX-License-Identifier: Apache-2.0
"""Tests for memory consolidator — Ebbinghaus retention + summarization (v3.2)."""

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from waggledance.core.learning.consolidator import (
    compute_retention, compute_significance, evaluate_episodes,
    EpisodeRecord, ConsolidationResult,
)


class TestComputeRetention:
    def test_fresh_episode_high_retention(self):
        # 1 hour old, 0 accesses, bronze
        ret = compute_retention(3600, 0, "bronze")
        assert ret > 0.9

    def test_old_episode_low_retention(self):
        # 180 days old, 0 accesses, bronze
        ret = compute_retention(180 * 86400, 0, "bronze")
        assert ret < 0.1

    def test_gold_decays_slower_than_bronze(self):
        age = 60 * 86400  # 60 days
        ret_gold = compute_retention(age, 0, "gold")
        ret_bronze = compute_retention(age, 0, "bronze")
        assert ret_gold > ret_bronze

    def test_more_accesses_slower_decay(self):
        age = 90 * 86400
        ret_0 = compute_retention(age, 0, "bronze")
        ret_5 = compute_retention(age, 5, "bronze")
        assert ret_5 > ret_0

    def test_retention_bounded(self):
        assert 0.0 <= compute_retention(0, 0, "gold") <= 1.0
        assert 0.0 <= compute_retention(999999999, 0, "bronze") <= 1.0


class TestComputeSignificance:
    def test_high_impact_protects(self):
        sig = compute_significance(retention=0.05, impact_on_world_model=0.8)
        assert sig == 0.8

    def test_high_retention_protects(self):
        sig = compute_significance(retention=0.9, impact_on_world_model=0.1)
        assert sig == 0.9

    def test_both_low_allows_consolidation(self):
        sig = compute_significance(retention=0.05, impact_on_world_model=0.05)
        assert sig < 0.7


class TestEvaluateEpisodes:
    def _ep(self, age_days, access=0, grade="bronze", significance=0.0,
            consolidated=False, episode_id=None):
        now = time.time()
        return EpisodeRecord(
            episode_id=episode_id or f"ep_{age_days}",
            created_at=now - age_days * 86400,
            access_count=access,
            quality_grade=grade,
            significance_score=significance,
            consolidated=consolidated,
            metadata={"goal_description": "test goal", "outcome": "test outcome"},
        )

    def test_young_episodes_not_consolidated(self):
        result = evaluate_episodes([self._ep(10)], min_age_days=90)
        assert result.summarized == 0

    def test_old_low_retention_gets_summarized(self):
        result = evaluate_episodes([self._ep(180)], min_age_days=90)
        assert result.summarized == 1

    def test_high_significance_protected(self):
        result = evaluate_episodes(
            [self._ep(180, significance=0.8)],
            min_age_days=90, significance_threshold=0.7,
        )
        assert result.protected == 1
        assert result.summarized == 0

    def test_already_consolidated_skipped(self):
        result = evaluate_episodes([self._ep(180, consolidated=True)])
        assert result.evaluated == 0

    def test_eviction_only_when_enabled(self):
        result = evaluate_episodes([self._ep(180)], evict_enabled=False)
        assert result.evicted == 0

        result = evaluate_episodes([self._ep(180)], evict_enabled=True)
        assert result.evicted == 1

    def test_summary_generated(self):
        result = evaluate_episodes([self._ep(180)])
        assert len(result.summaries) == 1
        assert "test goal" in result.summaries[0]["summary"]

    def test_summary_only_mode(self):
        """consolidation_evict_enabled=false → summarize but don't evict."""
        result = evaluate_episodes(
            [self._ep(180), self._ep(200)],
            evict_enabled=False,
        )
        assert result.summarized == 2
        assert result.evicted == 0
