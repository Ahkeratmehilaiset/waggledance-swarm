# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
# See LICENSE-BUSL.txt and LICENSE-CORE.md
"""
Consolidator — Ebbinghaus forgetting + summarization for episodic memory (v3.2).

Nightly process:
1. Score each episode: retention = e^(-t/strength)
2. If retention < threshold AND age > min_age_days → summarize
3. If eviction enabled → evict raw from active index
4. Raw data always preserved in MAGMA audit log

Invariant: raw data never destroyed, only active index shrinks.
Significance protection: never consolidate episodes where significance > 0.7.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

log = logging.getLogger("waggledance.learning.consolidator")


# ── Config defaults ──────────────────────────────────────────

DEFAULT_RETENTION_THRESHOLD = 0.1
DEFAULT_MIN_AGE_DAYS = 90
DEFAULT_SIGNIFICANCE_THRESHOLD = 0.7


# ── Episode model ────────────────────────────────────────────

@dataclass
class EpisodeRecord:
    """An episode in the active index."""
    episode_id: str
    created_at: float  # unix timestamp
    last_accessed_at: float = 0.0
    access_count: int = 0
    quality_grade: str = "bronze"
    significance_score: float = 0.0
    consolidated: bool = False
    summary: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ConsolidationResult:
    """Result of a nightly consolidation run."""
    total_episodes: int = 0
    evaluated: int = 0
    summarized: int = 0
    evicted: int = 0
    protected: int = 0  # kept due to significance
    summaries: List[Dict[str, Any]] = field(default_factory=list)


# ── Core functions ───────────────────────────────────────────

def compute_retention(
    age_seconds: float,
    access_count: int,
    quality_grade: str,
) -> float:
    """
    Ebbinghaus-style retention score.

    retention = e^(-t / strength)
    strength grows with access_count and quality_grade.
    """
    grade_weights = {"gold": 3.0, "silver": 2.0, "bronze": 1.0, "quarantine": 0.5}
    grade_w = grade_weights.get(quality_grade, 1.0)

    # Strength: base 1 day + access boost + grade boost
    strength_seconds = (86400  # 1 day base
                        + access_count * 43200  # each access adds 12h
                        + grade_w * 86400)  # grade adds 1-3 days

    if strength_seconds <= 0:
        return 0.0

    retention = math.exp(-age_seconds / strength_seconds)
    return max(0.0, min(1.0, retention))


def compute_significance(
    retention: float,
    impact_on_world_model: float = 0.0,
) -> float:
    """
    Significance = max(retention, impact_on_world_model).
    Never consolidate episodes with significance > threshold.
    """
    return max(retention, impact_on_world_model)


def evaluate_episodes(
    episodes: List[EpisodeRecord],
    retention_threshold: float = DEFAULT_RETENTION_THRESHOLD,
    min_age_days: float = DEFAULT_MIN_AGE_DAYS,
    significance_threshold: float = DEFAULT_SIGNIFICANCE_THRESHOLD,
    evict_enabled: bool = False,
    now: float | None = None,
) -> ConsolidationResult:
    """
    Evaluate episodes for consolidation.

    Returns a ConsolidationResult with:
    - summarized: episodes that should get a summary
    - evicted: episodes to remove from active index (only if evict_enabled)
    - protected: episodes kept due to significance
    """
    if now is None:
        now = time.time()

    min_age_seconds = min_age_days * 86400
    result = ConsolidationResult(total_episodes=len(episodes))

    for ep in episodes:
        if ep.consolidated:
            continue

        result.evaluated += 1
        age = now - ep.created_at

        if age < min_age_seconds:
            continue  # too young

        retention = compute_retention(age, ep.access_count, ep.quality_grade)
        significance = compute_significance(
            retention,
            ep.significance_score,
        )

        if significance > significance_threshold:
            result.protected += 1
            continue

        if retention < retention_threshold:
            # Generate summary
            summary = _generate_summary(ep)
            result.summaries.append({
                "episode_id": ep.episode_id,
                "summary": summary,
                "retention": round(retention, 4),
                "significance": round(significance, 4),
            })
            result.summarized += 1

            if evict_enabled:
                result.evicted += 1

    return result


def _generate_summary(ep: EpisodeRecord) -> str:
    """
    Generate a template-based summary for an episode.
    Uses pattern: "{goal} → {outcome} → {learned}"
    LLM-assisted summarization is a future enhancement.
    """
    goal = ep.metadata.get("goal_description", "unknown goal")
    outcome = ep.metadata.get("outcome", "unknown outcome")
    grade = ep.quality_grade
    return f"[{grade}] {goal} → {outcome}"
