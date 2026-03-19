"""
Goal Engine — lifecycle management, decomposition, prioritization.

The GoalEngine manages the full lifecycle of goals:
  proposed → accepted → planned → executing → verified/failed → archived

It integrates with:
  - Goal state machine (Phase 2 data models)
  - WorldModel (context for decomposition)
  - CapabilityRegistry (what can we do?)
  - MissionStore (persistence)
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from waggledance.core.domain.autonomy import (
    Goal,
    GoalStatus,
    GoalType,
)
from waggledance.core.goals.mission_store import MissionStore

log = logging.getLogger("waggledance.goals.engine")

# Goal type → typical sub-goal patterns
_DECOMPOSITION_PATTERNS: Dict[str, List[str]] = {
    "diagnose": ["observe", "diagnose", "verify"],
    "optimize": ["observe", "diagnose", "plan", "act", "verify"],
    "protect": ["observe", "act", "verify"],
    "plan": ["observe", "diagnose"],
    "maintain": ["observe", "diagnose", "act"],
}


class GoalEngine:
    """
    Central goal lifecycle manager.

    Manages: proposal → acceptance → planning → execution → verification → archival.
    """

    def __init__(
        self,
        mission_store: Optional[MissionStore] = None,
        profile: str = "DEFAULT",
    ):
        self._store = mission_store or MissionStore()
        self._profile = profile
        self._active_goals: Dict[str, Goal] = {}

    # ── Goal creation ─────────────────────────────────────

    def propose(
        self,
        goal_type: str,
        description: str,
        priority: int = 50,
        parent_goal_id: str = "",
    ) -> Goal:
        """Create a new goal in PROPOSED state."""
        gtype = GoalType(goal_type) if isinstance(goal_type, str) else goal_type
        goal = Goal(
            type=gtype,
            description=description,
            priority=priority,
            profile=self._profile,
            parent_goal_id=parent_goal_id,
        )
        self._active_goals[goal.goal_id] = goal
        self._store.save_goal(goal)
        log.info("Goal proposed: %s [%s] priority=%d",
                 goal.goal_id, gtype.value, priority)
        return goal

    # ── Lifecycle transitions ─────────────────────────────

    def accept(self, goal_id: str) -> Goal:
        """Accept a proposed goal."""
        goal = self._get_goal(goal_id)
        goal.transition_to(GoalStatus.ACCEPTED)
        self._store.save_goal(goal)
        return goal

    def mark_planned(self, goal_id: str) -> Goal:
        """Mark goal as planned (plan has been created)."""
        goal = self._get_goal(goal_id)
        goal.transition_to(GoalStatus.PLANNED)
        self._store.save_goal(goal)
        return goal

    def start_execution(self, goal_id: str) -> Goal:
        """Start executing a planned goal."""
        goal = self._get_goal(goal_id)
        goal.transition_to(GoalStatus.EXECUTING)
        self._store.save_goal(goal)
        return goal

    def mark_verified(self, goal_id: str) -> Goal:
        """Mark goal as verified (success)."""
        goal = self._get_goal(goal_id)
        goal.transition_to(GoalStatus.VERIFIED)
        self._store.save_goal(goal)
        return goal

    def mark_failed(self, goal_id: str, reason: str = "") -> Goal:
        """Mark goal as failed."""
        goal = self._get_goal(goal_id)
        goal.transition_to(GoalStatus.FAILED)
        self._store.save_goal(goal)
        log.warning("Goal failed: %s — %s", goal_id, reason)
        return goal

    def rollback(self, goal_id: str) -> Goal:
        """Rollback a failed goal."""
        goal = self._get_goal(goal_id)
        goal.transition_to(GoalStatus.ROLLED_BACK)
        self._store.save_goal(goal)
        return goal

    def archive(self, goal_id: str) -> Goal:
        """Archive a completed goal."""
        goal = self._get_goal(goal_id)
        goal.transition_to(GoalStatus.ARCHIVED)
        self._store.save_goal(goal)
        self._active_goals.pop(goal_id, None)
        return goal

    def retry(self, goal_id: str) -> Goal:
        """Retry a failed goal (transition back to PROPOSED)."""
        goal = self._get_goal(goal_id)
        goal.transition_to(GoalStatus.PROPOSED)
        self._store.save_goal(goal)
        return goal

    # ── Decomposition ─────────────────────────────────────

    def decompose(self, goal_id: str) -> List[Goal]:
        """
        Decompose a goal into sub-goals based on type patterns.

        Returns list of sub-goals in execution order.
        """
        goal = self._get_goal(goal_id)
        pattern = _DECOMPOSITION_PATTERNS.get(goal.type.value, [])

        if not pattern:
            return []  # Atomic goal, no decomposition

        sub_goals = []
        for i, sub_type in enumerate(pattern):
            sub = self.propose(
                goal_type=sub_type,
                description=f"{goal.description} — step {i + 1}: {sub_type}",
                priority=goal.priority,
                parent_goal_id=goal.goal_id,
            )
            sub_goals.append(sub)

        log.info("Decomposed %s into %d sub-goals", goal_id, len(sub_goals))
        return sub_goals

    # ── Prioritization (v3.2: motive valence) ─────────────

    def prioritize(self, goal_ids: Optional[List[str]] = None) -> List[Goal]:
        """
        Return goals sorted by effective priority (highest first).

        v3.2: effective_priority = base + urgency_boost + valence_boost
        Promise-to-user goals get a +15 boost.
        Carry-forward goals get a +5 boost.
        """
        if goal_ids:
            goals = [self._get_goal(gid) for gid in goal_ids]
        else:
            goals = list(self._active_goals.values())

        # Type urgency boost
        urgency_boost = {
            GoalType.PROTECT: 20,
            GoalType.ACT: 10,
            GoalType.DIAGNOSE: 5,
        }

        def sort_key(g: Goal) -> float:
            base = g.priority
            base += urgency_boost.get(g.type, 0)
            # v3.2: valence boost
            if g.motive_valence > 0:
                base += g.motive_valence * 10
            # v3.2: promise and carry-forward boosts
            if g.promise_to_user:
                base += 15
            if g.carry_forward:
                base += 5
            # Penalize non-actionable states
            if g.status in (GoalStatus.ARCHIVED, GoalStatus.ROLLED_BACK):
                base -= 100
            return base

        return sorted(goals, key=sort_key, reverse=True)

    # ── Unfinished business (v3.2) ──────────────────────────

    def get_unfinished_business(self) -> List[Goal]:
        """Return carry-forward or promise-to-user goals not yet archived."""
        return [
            g for g in self._active_goals.values()
            if (g.carry_forward or g.promise_to_user)
            and g.status not in (GoalStatus.ARCHIVED, GoalStatus.ROLLED_BACK)
        ]

    def get_open_observe_goals(self) -> List[Goal]:
        """Return open observe-type goals (for epistemic uncertainty)."""
        return [
            g for g in self._active_goals.values()
            if g.type == GoalType.OBSERVE
            and g.status not in (GoalStatus.VERIFIED, GoalStatus.ARCHIVED,
                                 GoalStatus.ROLLED_BACK, GoalStatus.FAILED)
        ]

    # ── Queries ───────────────────────────────────────────

    def get(self, goal_id: str) -> Optional[Goal]:
        return self._active_goals.get(goal_id)

    def active_goals(self) -> List[Goal]:
        return [g for g in self._active_goals.values()
                if g.status not in (GoalStatus.ARCHIVED, GoalStatus.ROLLED_BACK)]

    def goals_by_status(self, status: GoalStatus) -> List[Goal]:
        return [g for g in self._active_goals.values() if g.status == status]

    def count(self, status: Optional[GoalStatus] = None) -> int:
        if status is None:
            return len(self._active_goals)
        return sum(1 for g in self._active_goals.values() if g.status == status)

    def stats(self) -> dict:
        status_counts: Dict[str, int] = {}
        for g in self._active_goals.values():
            s = g.status.value
            status_counts[s] = status_counts.get(s, 0) + 1
        return {
            "total": len(self._active_goals),
            "active": len(self.active_goals()),
            "by_status": status_counts,
            "profile": self._profile,
        }

    # ── Internal ──────────────────────────────────────────

    def _get_goal(self, goal_id: str) -> Goal:
        goal = self._active_goals.get(goal_id)
        if goal is None:
            # Try loading from store
            goal = self._store.load_goal(goal_id)
            if goal:
                self._active_goals[goal_id] = goal
            else:
                raise KeyError(f"Goal not found: {goal_id}")
        return goal
