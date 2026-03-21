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
    capability_confidence: Optional[Dict[str, float]] = None,
) -> List[DreamCandidate]:
    """
    Select top uncertainty/failure cases for counterfactual simulation.

    Priority: failures first, then low-quality, then high-uncertainty.
    When *capability_confidence* is provided, low-confidence capabilities
    boost the candidate's score (prioritised for dream simulation).
    """
    candidates = []
    conf = capability_confidence or {}

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

        # Boost score for low-confidence capabilities
        if conf and ct.selected_capabilities:
            cap_ids = [c.capability_id for c in ct.selected_capabilities]
            min_conf = min(conf.get(cid, 0.5) for cid in cap_ids)
            if min_conf < 0.5:
                # Add up to 0.3 boost for very low confidence
                score += 0.3 * (1.0 - min_conf * 2)
                if not reason:
                    reason = f"low confidence ({min_conf:.2f})"

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
    graph_builder=None,
) -> CounterfactualResult:
    """
    Simulate an alternative capability chain for a failed/uncertain case.

    This is a simplified simulation — real execution is never triggered.
    When a GraphBuilder is provided, the CognitiveGraph success_rate data
    is used to score alternative chains and produce non-inconclusive outcomes.
    """
    original_set = set(candidate.original_capabilities)

    # --- Try graph-informed alternative selection first ---
    alt_chain: List[str] = []
    alt_scores: List[float] = []

    if graph_builder is not None:
        # Infer intent from first capability (best effort)
        _intent = _infer_intent(candidate)
        if _intent:
            graph_alts = graph_builder.find_alternative_paths(
                _intent,
                exclude_capabilities=candidate.original_capabilities,
                min_success_rate=0.0,
            )
            graph_alt_ids = [cap_id for cap_id, _sr in graph_alts]
            graph_alt_map = {cap_id: sr for cap_id, sr in graph_alts}

            for cap_id in candidate.original_capabilities:
                picked = None
                for alt_id in graph_alt_ids:
                    if alt_id != cap_id and alt_id not in alt_chain:
                        picked = alt_id
                        break
                if picked:
                    alt_chain.append(picked)
                    alt_scores.append(graph_alt_map.get(picked, 0.0))
                else:
                    alt_chain.append(cap_id)
                    alt_scores.append(0.0)

    # --- Fallback: simple pool-based swap ---
    if not alt_chain:
        for cap_id in candidate.original_capabilities:
            alternatives = [c for c in available_capabilities if c != cap_id]
            if alternatives:
                alt_chain.append(alternatives[0])
                alt_scores.append(0.0)
            else:
                alt_chain.append(cap_id)
                alt_scores.append(0.0)

    # --- Score the alternative chain ---
    changed = any(a != o for a, o in zip(alt_chain, candidate.original_capabilities))
    avg_score = sum(alt_scores) / len(alt_scores) if alt_scores else 0.0

    if not changed:
        outcome = "inconclusive"
        confidence = 0.1
        insight = f"No alternative found for: {candidate.failure_reason}"
    elif avg_score >= 0.6:
        outcome = "success"
        confidence = min(0.85, 0.4 + avg_score * 0.5)
        insight = (f"High-success alternative chain (avg_rate={avg_score:.2f}) "
                   f"for: {candidate.failure_reason}")
    elif avg_score >= 0.3:
        outcome = "success"
        confidence = 0.3 + avg_score * 0.3
        insight = (f"Moderate alternative chain (avg_rate={avg_score:.2f}) "
                   f"for: {candidate.failure_reason}")
    else:
        outcome = "failure"
        confidence = 0.3
        insight = (f"Low-success alternative (avg_rate={avg_score:.2f}) "
                   f"for: {candidate.failure_reason}")

    return CounterfactualResult(
        original_trajectory_id=candidate.original_trajectory_id,
        alternative_chain=alt_chain,
        simulated_outcome=outcome,
        insight=insight,
        confidence=confidence,
    )


def _infer_intent(candidate: DreamCandidate) -> str:
    """Best-effort intent inference from candidate goal description."""
    desc = (candidate.goal_description or "").lower()
    if not desc:
        return ""
    # Simple keyword-based mapping matching solver_router.classify_intent
    for keyword, intent in [
        ("math", "math"), ("calculate", "math"), ("laske", "math"),
        ("thermal", "thermal"), ("temperature", "thermal"), ("lämpötila", "thermal"),
        ("seasonal", "seasonal"), ("vuodenaika", "seasonal"),
        ("schedule", "optimization"), ("optimize", "optimization"),
        ("anomaly", "anomaly"), ("deviation", "anomaly"),
        ("search", "retrieval"), ("find", "retrieval"),
    ]:
        if keyword in desc:
            return intent
    return "chat"


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


def compute_insight_score(session: DreamSession) -> float:
    """Compute insight_score for a dream session.

    insight_score = alternatives_that_beat_original / total_alternatives
    where "beat" means outcome == "success".

    Ranges from 0.0 (no alternative beat original) to 1.0 (all did).
    Returns negative when failures exceed successes, allowing the adaptive
    simulation count to detect persistently unhelpful sessions.
    """
    if session.simulations_run == 0:
        return 0.0
    successes = 0
    failures = 0
    for traj in session.simulated_trajectories:
        outcome = traj.verifier_result.get("outcome", "inconclusive")
        if outcome == "success":
            successes += 1
        elif outcome == "failure":
            failures += 1
    # Primary metric: fraction of alternatives that beat original
    # Subtract failures to penalise sessions that are actively harmful
    return (successes - failures) / session.simulations_run


def run_dream_session(
    day_trajectories: List[CaseTrajectory],
    available_capabilities: List[str],
    max_simulations: int = DEFAULT_MAX_SIMULATIONS,
    max_candidates: int = 5,
    graph_builder=None,
) -> DreamSession:
    """
    Run a complete nightly dream mode session.

    When *graph_builder* is provided, counterfactual evaluation uses
    CognitiveGraph success_rate data to produce non-inconclusive outcomes.

    Returns a DreamSession with all simulated trajectories.
    """
    session = DreamSession()

    candidates = select_candidates(day_trajectories, max_candidates)
    session.candidates_evaluated = len(candidates)

    for candidate in candidates:
        if session.simulations_run >= max_simulations:
            break

        result = simulate_counterfactual(
            candidate, available_capabilities, graph_builder=graph_builder,
        )
        trajectory = create_simulated_trajectory(candidate, result)
        session.simulated_trajectories.append(trajectory)
        session.simulations_run += 1

        if result.simulated_outcome != "inconclusive":
            session.insights_found += 1

    # Compute insight score for adaptive simulation count
    session.insight_score = compute_insight_score(session)

    log.info("Dream session %s: %d candidates, %d simulations, %d insights (score=%.2f)",
             session.night_id, session.candidates_evaluated,
             session.simulations_run, session.insights_found, session.insight_score)

    return session
