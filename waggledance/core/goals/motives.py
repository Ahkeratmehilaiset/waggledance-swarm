# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
"""
Persistent Motives — config reader + valence scoring + conflict resolution (v3.2).

Motives are prioritization weights, not emotions.
They are:
  - defined in config (configs/autonomy/motives.yaml)
  - activated at runtime (attached to goals)
  - logged to audit (MotiveActivation records)
  - never stored in a separate motive store

Valence scoring: effective_priority = goal.priority * active_motive.valence * intensity
Conflict resolution strategies: highest_valence_wins, user_promise_first, minimize_harm_always
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from waggledance.core.domain.autonomy import Goal, MotiveActivation

log = logging.getLogger("waggledance.goals.motives")


# ── Config types ─────────────────────────────────────────────

@dataclass
class MotiveConfig:
    """A persistent motive as defined in config."""
    id: str
    description: str = ""
    base_valence: float = 0.5  # 0.0..1.0


@dataclass
class ConflictResult:
    """Outcome of a motive conflict resolution."""
    winner_id: str
    loser_id: str
    strategy: str
    reason: str


# ── Resolution strategies ────────────────────────────────────

RESOLUTION_STRATEGIES = frozenset({
    "highest_valence_wins",
    "user_promise_first",
    "minimize_harm_always",
})


def resolve_conflict(
    motive_a: MotiveConfig,
    motive_b: MotiveConfig,
    goal_a: Goal,
    goal_b: Goal,
    strategy: str = "minimize_harm_always",
) -> ConflictResult:
    """
    Resolve conflict between two motives.

    Strategies:
    - minimize_harm_always: minimize_harm motive always wins
    - user_promise_first: promise_to_user goals win
    - highest_valence_wins: higher base_valence wins
    """
    if strategy == "minimize_harm_always":
        if motive_a.id == "minimize_harm":
            return ConflictResult(motive_a.id, motive_b.id, strategy,
                                  "minimize_harm always wins")
        if motive_b.id == "minimize_harm":
            return ConflictResult(motive_b.id, motive_a.id, strategy,
                                  "minimize_harm always wins")
        # Fall through to valence
        strategy = "highest_valence_wins"

    if strategy == "user_promise_first":
        if goal_a.promise_to_user and not goal_b.promise_to_user:
            return ConflictResult(motive_a.id, motive_b.id, strategy,
                                  "goal_a has user promise")
        if goal_b.promise_to_user and not goal_a.promise_to_user:
            return ConflictResult(motive_b.id, motive_a.id, strategy,
                                  "goal_b has user promise")
        # Fall through to valence
        strategy = "highest_valence_wins"

    # highest_valence_wins (default fallback)
    if motive_a.base_valence >= motive_b.base_valence:
        return ConflictResult(motive_a.id, motive_b.id, "highest_valence_wins",
                              f"valence {motive_a.base_valence} >= {motive_b.base_valence}")
    return ConflictResult(motive_b.id, motive_a.id, "highest_valence_wins",
                          f"valence {motive_b.base_valence} > {motive_a.base_valence}")


# ── Valence scoring ──────────────────────────────────────────

def compute_effective_priority(goal: Goal, motive: Optional[MotiveConfig] = None) -> float:
    """
    Compute effective priority using motive valence.

    effective_priority = goal.priority * valence * intensity
    If no motive attached, returns goal.priority unchanged.
    """
    if motive is None or goal.active_motive_id == "":
        return float(goal.priority)

    valence = goal.motive_valence if goal.motive_valence > 0 else motive.base_valence
    # Intensity derived from valence (clamped)
    intensity = min(1.0, max(0.1, valence))
    return goal.priority * valence * intensity


# ── Config loader ────────────────────────────────────────────

class MotiveRegistry:
    """Loads and holds persistent motive definitions from config."""

    def __init__(self, motives: Optional[List[MotiveConfig]] = None):
        self._motives: Dict[str, MotiveConfig] = {}
        if motives:
            for m in motives:
                self._motives[m.id] = m

    @classmethod
    def from_yaml(cls, path: str | Path = "configs/autonomy/motives.yaml") -> MotiveRegistry:
        p = Path(path)
        if not p.exists():
            log.warning("Motives config not found: %s", p)
            return cls()

        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        motives = []
        for item in data.get("motives", []):
            motives.append(MotiveConfig(
                id=item["id"],
                description=item.get("description", ""),
                base_valence=float(item.get("base_valence", 0.5)),
            ))
        log.info("Loaded %d persistent motives", len(motives))
        return cls(motives)

    def get(self, motive_id: str) -> Optional[MotiveConfig]:
        return self._motives.get(motive_id)

    def all(self) -> List[MotiveConfig]:
        return list(self._motives.values())

    def ids(self) -> List[str]:
        return list(self._motives.keys())

    def __len__(self) -> int:
        return len(self._motives)

    def __contains__(self, motive_id: str) -> bool:
        return motive_id in self._motives
