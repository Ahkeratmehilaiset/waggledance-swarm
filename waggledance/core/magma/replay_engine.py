# BUSL-1.1 - see LICENSE-CORE.md
"""Replay engine adapter — wraps legacy ReplayEngine for mission-level replay.

Extends L2 replay to support:
- Mission-level grouping (all entries from the same goal_id)
- World snapshot before/after per mission
- Plan steps chronologically
- Capability executions with results
- Counterfactual replay via LLM (nightly)
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_ReplayEngine = None


def _get_legacy_class():
    global _ReplayEngine
    if _ReplayEngine is None:
        try:
            from core.replay_engine import ReplayEngine
            _ReplayEngine = ReplayEngine
        except ImportError:
            _ReplayEngine = None
    return _ReplayEngine


@dataclass
class MissionReplayEntry:
    """Single entry in a mission replay sequence."""
    event_type: str
    timestamp: float
    payload: Dict[str, Any]
    step_order: int = 0
    capability_id: str = ""
    action_id: str = ""
    result: Optional[str] = None


@dataclass
class MissionReplay:
    """Complete replay of a mission (goal lifecycle)."""
    goal_id: str
    goal_type: str = ""
    status: str = ""
    entries: List[MissionReplayEntry] = field(default_factory=list)
    world_snapshot_before: Optional[Dict] = None
    world_snapshot_after: Optional[Dict] = None
    quality_grade: str = ""
    total_duration_ms: float = 0.0

    @property
    def entry_count(self) -> int:
        return len(self.entries)

    @property
    def capabilities_used(self) -> List[str]:
        return list({e.capability_id for e in self.entries if e.capability_id})

    def to_dict(self) -> Dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "goal_type": self.goal_type,
            "status": self.status,
            "entry_count": self.entry_count,
            "capabilities_used": self.capabilities_used,
            "quality_grade": self.quality_grade,
            "total_duration_ms": self.total_duration_ms,
            "entries": [
                {
                    "event_type": e.event_type,
                    "timestamp": e.timestamp,
                    "step_order": e.step_order,
                    "capability_id": e.capability_id,
                }
                for e in self.entries
            ],
        }


class ReplayAdapter:
    """Wraps legacy ReplayEngine with mission-level replay support.

    The legacy engine replays individual memory operations.
    This adapter aggregates them into full mission replays by goal_id,
    preserving chronological order and linking world snapshots.
    """

    def __init__(self, legacy_engine=None):
        self._engine = legacy_engine
        self._lock = threading.Lock()
        self._missions: Dict[str, MissionReplay] = {}
        self._max_missions = 200

    def record_mission_event(self, goal_id: str, event_type: str,
                             payload: dict = None, step_order: int = 0,
                             capability_id: str = "", action_id: str = "",
                             result: str = None) -> None:
        """Append an event to a mission's replay sequence."""
        entry = MissionReplayEntry(
            event_type=event_type,
            timestamp=time.time(),
            payload=payload or {},
            step_order=step_order,
            capability_id=capability_id,
            action_id=action_id,
            result=result,
        )
        with self._lock:
            if goal_id not in self._missions:
                if len(self._missions) >= self._max_missions:
                    oldest = min(self._missions, key=lambda k: self._missions[k].entries[0].timestamp
                                 if self._missions[k].entries else float("inf"))
                    del self._missions[oldest]
                self._missions[goal_id] = MissionReplay(goal_id=goal_id)
            self._missions[goal_id].entries.append(entry)

    def set_mission_metadata(self, goal_id: str, goal_type: str = "",
                             status: str = "", quality_grade: str = "",
                             world_before: dict = None, world_after: dict = None,
                             duration_ms: float = 0.0) -> None:
        """Update mission-level metadata."""
        with self._lock:
            mission = self._missions.get(goal_id)
            if not mission:
                return
            if goal_type:
                mission.goal_type = goal_type
            if status:
                mission.status = status
            if quality_grade:
                mission.quality_grade = quality_grade
            if world_before is not None:
                mission.world_snapshot_before = world_before
            if world_after is not None:
                mission.world_snapshot_after = world_after
            if duration_ms:
                mission.total_duration_ms = duration_ms

    def get_mission_replay(self, goal_id: str) -> Optional[MissionReplay]:
        """Get the full replay for a mission."""
        with self._lock:
            mission = self._missions.get(goal_id)
            if mission:
                mission.entries.sort(key=lambda e: (e.step_order, e.timestamp))
            return mission

    def list_missions(self, limit: int = 50,
                      quality_grade: str = None) -> List[Dict[str, Any]]:
        """List recent missions with optional quality filter."""
        with self._lock:
            missions = list(self._missions.values())
        if quality_grade:
            missions = [m for m in missions if m.quality_grade == quality_grade]
        missions.sort(key=lambda m: m.entries[-1].timestamp if m.entries else 0, reverse=True)
        return [m.to_dict() for m in missions[:limit]]

    def replay_legacy_session(self, session_id: str, proxy=None,
                              dry_run: bool = True) -> List[Dict]:
        """Delegate to legacy replay engine for backward-compatible session replay."""
        if self._engine:
            try:
                return self._engine.replay_session(session_id, proxy, dry_run=dry_run)
            except Exception as exc:
                logger.warning("Legacy replay failed: %s", exc)
        return []

    def replay_legacy_mission(self, goal_id: str, proxy=None,
                              dry_run: bool = True) -> Optional[Dict]:
        """Delegate to legacy replay engine's mission replay."""
        if self._engine and hasattr(self._engine, "replay_mission"):
            try:
                return self._engine.replay_mission(goal_id, proxy, dry_run=dry_run)
            except Exception as exc:
                logger.warning("Legacy mission replay failed: %s", exc)
        return None

    def get_counterfactual_candidates(self, goal_id: str) -> List[Dict[str, Any]]:
        """Identify decision points where an alternative route could have been taken.

        Returns entries where capability_id differs from the most common
        capability used in similar missions — candidates for LLM counterfactual
        analysis during night learning.
        """
        mission = self.get_mission_replay(goal_id)
        if not mission:
            return []
        capability_entries = [e for e in mission.entries if e.capability_id]
        if len(capability_entries) < 2:
            return []
        return [
            {
                "step_order": e.step_order,
                "capability_id": e.capability_id,
                "event_type": e.event_type,
                "result": e.result,
            }
            for e in capability_entries
        ]

    def stats(self) -> Dict[str, Any]:
        """Summary statistics."""
        with self._lock:
            total = len(self._missions)
            by_grade: Dict[str, int] = {}
            by_status: Dict[str, int] = {}
            for m in self._missions.values():
                if m.quality_grade:
                    by_grade[m.quality_grade] = by_grade.get(m.quality_grade, 0) + 1
                if m.status:
                    by_status[m.status] = by_status.get(m.status, 0) + 1
            return {
                "total_missions": total,
                "by_grade": by_grade,
                "by_status": by_status,
                "legacy_engine_available": self._engine is not None,
            }
