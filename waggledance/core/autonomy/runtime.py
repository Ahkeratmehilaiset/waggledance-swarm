"""
Autonomy Runtime — main entry point for the solver-first autonomy engine.

Integrates all Phase 1-6 components into a cohesive runtime:
  - GoalEngine: lifecycle management
  - Planner: capability chain building
  - SolverRouter: solver-first routing
  - PolicyEngine: deny-by-default evaluation
  - SafeActionBus: safe execution
  - Verifier: outcome validation
  - CaseTrajectoryBuilder: learning data capture
  - WorldModel: unified situation picture
  - WorkingMemory: short-term context
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from waggledance.core.actions.action_bus import SafeActionBus
from waggledance.core.capabilities.registry import CapabilityRegistry
from waggledance.core.domain.autonomy import (
    Action,
    Goal,
    GoalStatus,
    Plan,
)
from waggledance.core.goals.goal_engine import GoalEngine
from waggledance.core.learning.case_builder import CaseTrajectoryBuilder
from waggledance.core.memory.working_memory import WorkingMemory
from waggledance.core.planning.planner import Planner
from waggledance.core.policy.policy_engine import PolicyEngine
from waggledance.core.reasoning.solver_router import SolverRouter
from waggledance.core.reasoning.verifier import Verifier
from waggledance.core.world.world_model import WorldModel

log = logging.getLogger("waggledance.autonomy.runtime")


class AutonomyRuntime:
    """
    Main autonomy runtime that orchestrates all components.

    Two primary call paths:
      1. handle_query() — single query → solver-first → response
      2. execute_mission() — goal → plan → execute → verify
    """

    def __init__(
        self,
        profile: str = "DEFAULT",
        world_model: Optional[WorldModel] = None,
        capability_registry: Optional[CapabilityRegistry] = None,
        policy_engine: Optional[PolicyEngine] = None,
        goal_engine: Optional[GoalEngine] = None,
        planner: Optional[Planner] = None,
        solver_router: Optional[SolverRouter] = None,
        verifier: Optional[Verifier] = None,
        case_builder: Optional[CaseTrajectoryBuilder] = None,
        working_memory: Optional[WorkingMemory] = None,
    ):
        self.profile = profile

        # Core components
        self.world_model = world_model or WorldModel(
            cognitive_graph=None, profile=profile
        )
        self.capability_registry = capability_registry or CapabilityRegistry()
        self.policy_engine = policy_engine or PolicyEngine(profile=profile)
        self.action_bus = SafeActionBus(self.policy_engine)
        self.goal_engine = goal_engine or GoalEngine(profile=profile)
        self.planner = planner or Planner(registry=self.capability_registry)
        self.working_memory = working_memory or WorkingMemory()
        self.solver_router = solver_router or SolverRouter(
            registry=self.capability_registry,
            working_memory=self.working_memory,
        )
        self.verifier = verifier or Verifier()
        self.case_builder = case_builder or CaseTrajectoryBuilder(profile=profile)

        self._started = False
        log.info("AutonomyRuntime initialised (profile=%s)", profile)

    # ── Lifecycle ─────────────────────────────────────────

    def start(self):
        """Start the runtime."""
        self._started = True
        log.info("AutonomyRuntime started")

    def stop(self):
        """Stop the runtime and persist state."""
        self._started = False
        self.world_model.save()
        log.info("AutonomyRuntime stopped")

    @property
    def is_running(self) -> bool:
        return self._started

    # ── Query path ────────────────────────────────────────

    def handle_query(
        self,
        query: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Main query path: query → solver-first → response.

        Steps:
          1. Intent classification
          2. World model context enrichment
          3. Capability selection (solver-first, LLM-last)
          4. Policy check
          5. Execution
          6. Verification
          7. Case trajectory recording
        """
        context = context or {}
        t0 = time.time()

        # 1. Intent classification
        intent = SolverRouter.classify_intent(query)

        # 2. World model enrichment
        snapshot_before = self.world_model.take_snapshot()
        context["profile"] = self.profile

        # 3. Capability selection
        route_result = self.solver_router.route(intent, query, context)

        # 4. Create action for first selected capability
        if not route_result.selection.selected:
            return {
                "intent": intent,
                "quality_path": "bronze",
                "response": None,
                "error": "No capabilities available",
                "elapsed_ms": round((time.time() - t0) * 1000, 2),
            }

        primary_cap = route_result.selection.selected[0]
        action = Action(capability_id=primary_cap.capability_id)

        # 5. Submit through action bus (policy + execution)
        action_result = self.action_bus.submit(
            action, primary_cap,
            quality_path=route_result.quality_path,
            context=context,
        )

        # 6. Verification (if executed)
        verifier_result = None
        if action_result.executed:
            verifier_result = self.verifier.verify_simple(
                action_result.result or {},
                expected_fields=primary_cap.success_criteria,
            )

        # 7. Case trajectory
        self.case_builder.build(
            query=query,
            intent=intent,
            capabilities=route_result.selection.selected,
            actions=[action],
            verifier_result=verifier_result,
            snapshot_before=snapshot_before,
        )

        elapsed = round((time.time() - t0) * 1000, 2)
        return {
            "intent": intent,
            "quality_path": route_result.quality_path,
            "capability": primary_cap.capability_id,
            "executed": action_result.executed,
            "result": action_result.result,
            "approved": action_result.decision.approved,
            "elapsed_ms": elapsed,
        }

    # ── Mission path ──────────────────────────────────────

    def execute_mission(
        self,
        goal_type: str,
        description: str,
        priority: int = 50,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Goal-based action: goal → plan → execute → verify.

        Steps:
          1. Goal creation and acceptance
          2. Plan generation
          3. Step-by-step execution via action bus
          4. Verification per step
          5. World model update
          6. Case trajectory recording
        """
        context = context or {}

        # 1. Create and accept goal
        goal = self.goal_engine.propose(goal_type, description, priority)
        self.goal_engine.accept(goal.goal_id)

        # 2. Create plan
        plan = self.planner.create_plan(goal, context)
        self.goal_engine.mark_planned(goal.goal_id)

        # 3. Take snapshot before execution
        snapshot_before = self.world_model.take_snapshot()
        self.goal_engine.start_execution(goal.goal_id)

        # 4. Execute steps
        executed_actions: List[Action] = []
        all_succeeded = True

        for step in plan.steps:
            cap = self.capability_registry.get(step.capability_id)
            if cap is None:
                continue

            action = Action(
                capability_id=step.capability_id,
                goal_id=goal.goal_id,
            )

            result = self.action_bus.submit(
                action, cap,
                quality_path=self.planner.estimate_quality_path(plan),
                context=context,
            )
            executed_actions.append(action)

            if result.executed:
                step.completed = True
            else:
                all_succeeded = False
                break  # Stop on first failure

        # 5. Take snapshot after
        snapshot_after = self.world_model.take_snapshot()

        # 6. Verify and update goal status
        if all_succeeded and plan.is_complete:
            self.goal_engine.mark_verified(goal.goal_id)
        elif not all_succeeded:
            self.goal_engine.mark_failed(goal.goal_id, "Step execution failed")

        # 7. Record case trajectory
        quality_path = self.planner.estimate_quality_path(plan)
        case = self.case_builder.build(
            query=description,
            intent=goal_type,
            capabilities=[self.capability_registry.get(s.capability_id)
                          for s in plan.steps
                          if self.capability_registry.get(s.capability_id)],
            actions=executed_actions,
            snapshot_before=snapshot_before,
            snapshot_after=snapshot_after,
            goal=goal,
        )

        return {
            "goal_id": goal.goal_id,
            "status": goal.status.value,
            "plan_steps": len(plan.steps),
            "steps_completed": sum(1 for s in plan.steps if s.completed),
            "quality_path": quality_path,
            "quality_grade": case.quality_grade.value,
        }

    # ── Stats ─────────────────────────────────────────────

    def stats(self) -> dict:
        return {
            "profile": self.profile,
            "running": self._started,
            "goals": self.goal_engine.stats(),
            "capabilities": self.capability_registry.stats(),
            "policy": self.policy_engine.stats(),
            "action_bus": self.action_bus.stats(),
            "solver_router": self.solver_router.stats(),
            "verifier": self.verifier.stats(),
            "cases": self.case_builder.stats(),
            "world_model": self.world_model.stats(),
            "working_memory": self.working_memory.stats(),
        }
