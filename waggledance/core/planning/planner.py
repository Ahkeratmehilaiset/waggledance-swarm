"""
Planner — converts goals into executable plans with capability chains.

The planner:
  1. Takes a goal
  2. Selects relevant capabilities from the registry
  3. Builds a step sequence (capability chain)
  4. Returns a Plan ready for execution via SafeActionBus
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from waggledance.core.capabilities.registry import CapabilityRegistry
from waggledance.core.capabilities.selector import CapabilitySelector
from waggledance.core.domain.autonomy import (
    Action,
    CapabilityCategory,
    CapabilityContract,
    Goal,
    GoalType,
    Plan,
    PlanStep,
)

log = logging.getLogger("waggledance.planning.planner")

# Goal type → required capability categories
_GOAL_CAPABILITY_MAP: Dict[str, List[str]] = {
    "observe": ["sense", "retrieve"],
    "diagnose": ["sense", "detect", "solve", "verify"],
    "optimize": ["sense", "solve", "verify", "act"],
    "protect": ["detect", "act", "verify"],
    "plan": ["sense", "solve"],
    "act": ["act", "verify"],
    "verify": ["verify"],
    "learn": ["learn"],
    "maintain": ["sense", "detect", "act"],
}


class Planner:
    """
    Converts goals into executable plans.

    Strategy: solver-first, LLM-last. Builds capability chains
    based on goal type and available capabilities.
    """

    def __init__(
        self,
        registry: Optional[CapabilityRegistry] = None,
        selector: Optional[CapabilitySelector] = None,
        max_steps: int = 10,
    ):
        self._registry = registry or CapabilityRegistry()
        self._selector = selector or CapabilitySelector(self._registry)
        self._max_steps = max_steps

    def create_plan(
        self,
        goal: Goal,
        context: Optional[Dict[str, Any]] = None,
    ) -> Plan:
        """
        Create an execution plan for a goal.

        Args:
            goal: the goal to plan for
            context: additional context (e.g., available conditions)

        Returns:
            Plan with ordered steps
        """
        context = context or {}

        # 1. Determine required capability categories
        required_categories = _GOAL_CAPABILITY_MAP.get(
            goal.type.value, ["sense", "solve"]
        )

        # 2. Select capabilities for each category
        steps = []
        for i, cat_name in enumerate(required_categories):
            if i >= self._max_steps:
                break

            try:
                category = CapabilityCategory(cat_name)
            except ValueError:
                continue

            # Get best capability for this category
            caps = self._registry.list_by_category(category)
            if not caps:
                continue

            # Pick the best capability (prefer rollback-possible)
            cap = self._select_best(caps, context)

            step = PlanStep(
                order=i,
                capability_id=cap.capability_id,
                description=f"{cat_name}: {cap.description}",
                preconditions=cap.preconditions,
                expected_outcome=f"{cat_name} step completed",
            )
            steps.append(step)

        plan = Plan(goal_id=goal.goal_id, steps=steps)
        log.info("Plan created for goal %s: %d steps", goal.goal_id, len(steps))
        return plan

    def create_plan_from_capabilities(
        self,
        goal: Goal,
        capability_ids: List[str],
    ) -> Plan:
        """
        Create a plan from explicit capability IDs (for pre-determined chains).
        """
        steps = []
        for i, cid in enumerate(capability_ids):
            cap = self._registry.get(cid)
            if cap is None:
                log.warning("Capability %s not found, skipping", cid)
                continue

            step = PlanStep(
                order=i,
                capability_id=cid,
                description=cap.description,
                preconditions=cap.preconditions,
                expected_outcome=f"{cap.category.value} completed",
            )
            steps.append(step)

        return Plan(goal_id=goal.goal_id, steps=steps)

    def estimate_quality_path(self, plan: Plan) -> str:
        """Estimate the quality path (gold/silver/bronze) for a plan."""
        has_solver = False
        has_verifier = False

        for step in plan.steps:
            cap = self._registry.get(step.capability_id)
            if cap is None:
                continue
            if cap.category == CapabilityCategory.SOLVE:
                has_solver = True
            if cap.category == CapabilityCategory.VERIFY:
                has_verifier = True

        if has_solver and has_verifier:
            return "gold"
        if has_solver or has_verifier:
            return "silver"
        return "bronze"

    # ── Internal ──────────────────────────────────────────

    def _select_best(
        self, capabilities: List[CapabilityContract],
        context: Dict[str, Any],
    ) -> CapabilityContract:
        """Select the best capability from a list."""
        # Prefer capabilities with rollback
        rollbackable = [c for c in capabilities if c.rollback_possible]
        if rollbackable:
            return rollbackable[0]
        return capabilities[0]
