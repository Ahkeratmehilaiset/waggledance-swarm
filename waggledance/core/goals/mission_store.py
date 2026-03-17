"""
Mission Store — in-memory persistence for goals and mission history.

Goals are stored in-memory with optional JSON serialization.
SQLite persistence can be added later for production use.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Dict, List, Optional

from waggledance.core.domain.autonomy import Goal, GoalStatus, GoalType

log = logging.getLogger("waggledance.goals.store")


class MissionStore:
    """In-memory goal storage with optional JSON persistence."""

    def __init__(self, persist_path: Optional[str] = None):
        self._goals: Dict[str, dict] = {}
        self._persist_path = Path(persist_path) if persist_path else None
        if self._persist_path:
            self._load()

    def save_goal(self, goal: Goal):
        """Save or update a goal."""
        self._goals[goal.goal_id] = goal.to_dict()
        if self._persist_path:
            self._persist()

    def load_goal(self, goal_id: str) -> Optional[Goal]:
        """Load a goal by ID."""
        data = self._goals.get(goal_id)
        if data is None:
            return None
        return self._dict_to_goal(data)

    def list_goals(self, status: Optional[str] = None) -> List[dict]:
        """List all goals as dicts, optionally filtered by status."""
        if status:
            return [g for g in self._goals.values() if g.get("status") == status]
        return list(self._goals.values())

    def count(self) -> int:
        return len(self._goals)

    def delete(self, goal_id: str) -> bool:
        if goal_id in self._goals:
            del self._goals[goal_id]
            if self._persist_path:
                self._persist()
            return True
        return False

    def clear(self):
        self._goals.clear()
        if self._persist_path:
            self._persist()

    # ── Persistence ───────────────────────────────────────

    def _persist(self):
        if not self._persist_path:
            return
        self._persist_path.parent.mkdir(parents=True, exist_ok=True)
        self._persist_path.write_text(
            json.dumps(list(self._goals.values()), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _load(self):
        if not self._persist_path or not self._persist_path.exists():
            return
        try:
            data = json.loads(self._persist_path.read_text(encoding="utf-8"))
            for g in data:
                self._goals[g["goal_id"]] = g
            log.info("Loaded %d goals from %s", len(self._goals), self._persist_path)
        except Exception as e:
            log.warning("Failed to load missions: %s", e)

    @staticmethod
    def _dict_to_goal(data: dict) -> Goal:
        """Reconstruct a Goal from its to_dict() representation."""
        goal = Goal(
            type=GoalType(data["type"]),
            description=data.get("description", ""),
            priority=data.get("priority", 50),
            profile=data.get("profile", ""),
            parent_goal_id=data.get("parent_goal_id", ""),
        )
        goal.goal_id = data["goal_id"]
        goal.status = GoalStatus(data["status"])
        return goal
