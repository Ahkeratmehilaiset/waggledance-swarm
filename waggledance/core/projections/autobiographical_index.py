# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
# See LICENSE-BUSL.txt and LICENSE-CORE.md
"""
Autobiographical Index — episodic memory indexing and retrieval (v3.2).

Query episodic memory by time range, entity type, goal ID, significance.
Respects consolidation state (summarized vs. raw).
No new store — projection over existing episodic memory.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

log = logging.getLogger("waggledance.projections.autobiographical")


# ── Data types ───────────────────────────────────────────────

@dataclass
class EpisodeEntry:
    """An indexed episode for autobiographical retrieval."""
    episode_id: str
    timestamp: float
    goal_id: str = ""
    goal_description: str = ""
    entities: List[str] = field(default_factory=list)
    outcome: str = ""
    significance_score: float = 0.0
    consolidated: bool = False
    consolidated_summary: str = ""
    quality_grade: str = "bronze"

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "episode_id": self.episode_id,
            "timestamp": self.timestamp,
            "goal_id": self.goal_id,
            "goal_description": self.goal_description,
            "entities": self.entities,
            "outcome": self.outcome,
            "significance_score": round(self.significance_score, 4),
            "consolidated": self.consolidated,
            "quality_grade": self.quality_grade,
        }
        if self.consolidated and self.consolidated_summary:
            d["consolidated_summary"] = self.consolidated_summary
        return d


@dataclass
class AutobiographicalSummary:
    """Summary of autobiographical index query."""
    query_time: float = field(default_factory=time.time)
    total_episodes: int = 0
    consolidated_count: int = 0
    raw_count: int = 0
    entries: List[EpisodeEntry] = field(default_factory=list)
    time_range_days: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query_time": self.query_time,
            "total_episodes": self.total_episodes,
            "consolidated_count": self.consolidated_count,
            "raw_count": self.raw_count,
            "time_range_days": self.time_range_days,
            "entries": [e.to_dict() for e in self.entries],
        }


# ── Core function ────────────────────────────────────────────

def query_episodes(
    episodes: List[Dict[str, Any]],
    days: int = 7,
    goal_id: Optional[str] = None,
    min_significance: float = 0.0,
    include_consolidated: bool = True,
    max_results: int = 50,
) -> AutobiographicalSummary:
    """
    Query episodic memory and return an autobiographical summary.

    Args:
        episodes: List of raw episode dicts (from episodic memory store).
        days: Time range in days (episodes newer than this).
        goal_id: Filter by specific goal ID.
        min_significance: Minimum significance score to include.
        include_consolidated: Whether to include consolidated episodes.
        max_results: Maximum entries to return.

    Returns:
        AutobiographicalSummary with matching entries.
    """
    now = time.time()
    cutoff = now - days * 86400

    entries: List[EpisodeEntry] = []
    consolidated_count = 0
    raw_count = 0

    for ep in episodes:
        ts = ep.get("timestamp", ep.get("created_at", 0))
        if ts < cutoff:
            continue

        is_consolidated = ep.get("consolidated", False)
        if is_consolidated:
            consolidated_count += 1
            if not include_consolidated:
                continue
        else:
            raw_count += 1

        sig = ep.get("significance_score", 0.0)
        if sig < min_significance:
            continue

        ep_goal_id = ep.get("goal_id", "")
        if goal_id and ep_goal_id != goal_id:
            continue

        entry = EpisodeEntry(
            episode_id=ep.get("episode_id", ""),
            timestamp=ts,
            goal_id=ep_goal_id,
            goal_description=ep.get("goal_description", ""),
            entities=ep.get("entities", []),
            outcome=ep.get("outcome", ""),
            significance_score=sig,
            consolidated=is_consolidated,
            consolidated_summary=ep.get("consolidated_summary", ""),
            quality_grade=ep.get("quality_grade", "bronze"),
        )
        entries.append(entry)

    # Sort by significance (descending), then timestamp (most recent first)
    entries.sort(key=lambda e: (-e.significance_score, -e.timestamp))
    entries = entries[:max_results]

    return AutobiographicalSummary(
        total_episodes=len(episodes),
        consolidated_count=consolidated_count,
        raw_count=raw_count,
        entries=entries,
        time_range_days=days,
    )
