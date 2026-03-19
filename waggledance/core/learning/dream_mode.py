# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
# See LICENSE-BUSL.txt and LICENSE-CORE.md
"""
Dream Mode — offline counterfactual simulation engine (v3.2).

Nightly process:
1. Select top uncertainty/failure cases from the day
2. For each: generate alternative capability chains
3. Simulate via CognitiveGraph + WorldModel (no real execution)
4. Produce synthetic CaseTrajectories (trajectory_origin="simulated", synthetic=True)
5. LLM assists only as bronze-grade counterfactual suggestion generator

Invariant: simulated cases NEVER contaminate production success metrics.
They are always marked with trajectory_origin="simulated" and synthetic=True.

Insight scoring:
  insight_score = specialist_accuracy_with_dream - specialist_accuracy_without_dream
  If negative for 3 consecutive nights → reduce simulation count by 50%
  If positive > 0.05 for 3 nights → increase by 25% up to configured max
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from waggledance.core.domain.autonomy import (
    CaseTrajectory, Goal, QualityGrade, WorldSnapshot,
)

log = logging.getLogger("waggledance.learning.dream_mode")


# ── Config defaults ──────────────────────────────────────────

DEFAULT_MAX_SIMULATIONS = 20
DEFAULT_MIN_SIMULATIONS = 4
DEFAULT_INSIGHT_BACKOFF_WINDOW = 3  # nights


# ── Data types ───────────────────────────────────────────────

@dataclass
class DreamCandidate:
    """A case selected for counterfactual simulation."""
    original_trajectory_id: str
    goal_description: str
    original_capabilities: List[str]
    failure_reason: str = ""
    uncertainty_score: float = 0.0


@dataclass
class CounterfactualResult:
    """Result of a single counterfactual simulation."""
    original_trajectory_id: str
    alternative_chain: List[str]  # capability IDs tried
    simulated_outcome: str  # "success" | "failure" | "inconclusive"
    insight: str = ""  # what was learned
    confidence: float = 0.0


@dataclass
class DreamSession:
    """Result of a nightly dream mode run."""
    night_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    candidates_evaluated: int = 0
    simulations_run: int = 0
    insights_found: int = 0
    simulated_trajectories: List[CaseTrajectory] = field(default_factory=list)
    insight_score: float = 0.0  # positive = helpful, negative = unhelpful


@dataclass
class InsightHistory:
    """Track insight scores across nights for adaptive simulation count."""
    scores: List[float] = field(default_factory=list)

    def add(self, score: float):
        self.scores.append(score)

    def should_reduce(self, window: int = DEFAULT_INSIGHT_BACKOFF_WINDOW) -> bool:
        """Reduce if negative for `window` consecutive nights."""
        if len(self.scores) < window:
            return False
        return all(s < 0 for s in self.scores[-window:])

    def should_increase(self, window: int = DEFAULT_INSIGHT_BACKOFF_WINDOW) -> bool:
        """Increase if positive > 0.05 for `window` consecutive nights."""
        if len(self.scores) < window:
            return False
        return all(s > 0.05 for s in self.scores[-window:])

    def adapt_count(
        self,
        current: int,
        min_count: int = DEFAULT_MIN_SIMULATIONS,
        max_count: int = DEFAULT_MAX_SIMULATIONS,
    ) -> int:
        """Adapt simulation count based on insight history."""
        if self.should_reduce():
            new = max(min_count, int(current * 0.5))
            log.info("Dream mode: reducing simulations %d → %d (negative insight)", current, new)
            return new
        if self.should_increase():
            new = min(max_count, int(current * 1.25))
            log.info("Dream mode: increasing simulations %d → %d (positive insight)", current, new)
            return new
        return current


# ── Core functions ───────────────────────────────────────────

def select_candidates(
    trajectories: List[CaseTrajectory],
    max_candidates: int = 5,
) -> List[DreamCandidate]:
    """
    Select top uncertainty/failure cases for counterfactual simulation.

    Priority: failures first, then low-quality, then high-uncertainty.
    """
    candidates = []

    for ct in trajectories:
        # Skip already-simulated
        if ct.trajectory_origin == "simulated":
            continue

        score = 0.0
        reason = ""

        if ct.quality_grade == QualityGrade.QUARANTINE:
            score = 1.0
            reason = "quarantine"
        elif ct.quality_grade == QualityGrade.BRONZE:
            score = 0.7
            reason = "bronze (unverified)"
        elif ct.verifier_result.get("passed") is False:
            score = 0.9
            reason = "verifier failed"

        if score > 0:
            candidates.append(DreamCandidate(
                original_trajectory_id=ct.trajectory_id,
                goal_description=ct.goal.description if ct.goal else "",
                original_capabilities=[c.capability_id for c in ct.selected_capabilities],
                failure_reason=reason,
                uncertainty_score=score,
            ))

    # Sort by uncertainty/failure score, take top N
    candidates.sort(key=lambda c: c.uncertainty_score, reverse=True)
    return candidates[:max_candidates]


def simulate_counterfactual(
    candidate: DreamCandidate,
    available_capabilities: List[str],
) -> CounterfactualResult:
    """
    Simulate an alternative capability chain for a failed/uncertain case.

    This is a simplified simulation — real execution is never triggered.
    The CognitiveGraph + WorldModel are queried but not modified.
    """
    # Generate alternative chain: swap capabilities
    alt_chain = []
    for cap_id in candidate.original_capabilities:
        # Simple strategy: try alternatives from available pool
        alternatives = [c for c in available_capabilities if c != cap_id]
        if alternatives:
            alt_chain.append(alternatives[0])
        else:
            alt_chain.append(cap_id)

    # Simulated outcome is always inconclusive without real execution
    return CounterfactualResult(
        original_trajectory_id=candidate.original_trajectory_id,
        alternative_chain=alt_chain,
        simulated_outcome="inconclusive",
        insight=f"Alternative chain for: {candidate.failure_reason}",
        confidence=0.3,  # low confidence — simulated only
    )


def create_simulated_trajectory(
    candidate: DreamCandidate,
    result: CounterfactualResult,
) -> CaseTrajectory:
    """
    Create a synthetic CaseTrajectory from a counterfactual simulation.

    Always marked as:
    - trajectory_origin = "simulated"
    - synthetic = True
    - quality_grade = BRONZE (never gold/silver)
    """
    return CaseTrajectory(
        goal=Goal(description=candidate.goal_description) if candidate.goal_description else None,
        quality_grade=QualityGrade.BRONZE,
        trajectory_origin="simulated",
        synthetic=True,
        counterfactual_alternatives=result.alternative_chain,
        verifier_result={
            "simulated": True,
            "outcome": result.simulated_outcome,
            "original_trajectory_id": result.original_trajectory_id,
        },
    )


def run_dream_session(
    day_trajectories: List[CaseTrajectory],
    available_capabilities: List[str],
    max_simulations: int = DEFAULT_MAX_SIMULATIONS,
    max_candidates: int = 5,
) -> DreamSession:
    """
    Run a complete nightly dream mode session.

    Returns a DreamSession with all simulated trajectories.
    """
    session = DreamSession()

    candidates = select_candidates(day_trajectories, max_candidates)
    session.candidates_evaluated = len(candidates)

    for candidate in candidates:
        if session.simulations_run >= max_simulations:
            break

        result = simulate_counterfactual(candidate, available_capabilities)
        trajectory = create_simulated_trajectory(candidate, result)
        session.simulated_trajectories.append(trajectory)
        session.simulations_run += 1

        if result.simulated_outcome != "inconclusive":
            session.insights_found += 1

    log.info("Dream session %s: %d candidates, %d simulations, %d insights",
             session.night_id, session.candidates_evaluated,
             session.simulations_run, session.insights_found)

    return session
