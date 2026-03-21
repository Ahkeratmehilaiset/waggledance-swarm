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

import asyncio
import inspect
import logging
import time
from typing import Any, Dict, List, Optional

from waggledance.core.actions.action_bus import SafeActionBus
from waggledance.core.capabilities.registry import CapabilityRegistry
from waggledance.core.domain.autonomy import (
    Action,
    Goal,
)
from waggledance.core.goals.goal_engine import GoalEngine
from waggledance.core.learning.case_builder import CaseTrajectoryBuilder
from waggledance.core.memory.working_memory import WorkingMemory
from waggledance.core.planning.planner import Planner
from waggledance.core.policy.policy_engine import PolicyEngine
from waggledance.core.reasoning.solver_router import SolverRouter
from waggledance.core.reasoning.verifier import Verifier
from waggledance.core.world.world_model import WorldModel

try:
    from waggledance.core.magma.audit_projector import AuditEntry
except ImportError:
    AuditEntry = None

log = logging.getLogger("waggledance.autonomy.runtime")


import concurrent.futures

_ASYNC_POOL = concurrent.futures.ThreadPoolExecutor(
    max_workers=2, thread_name_prefix="autonomy-async",
)


def _run_maybe_async(result):
    """If *result* is a coroutine, run it to completion and return the value."""
    if not inspect.isawaitable(result):
        return result
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is None:
        return asyncio.run(result)
    # Already inside an event loop — run in shared pool to avoid deadlock
    return _ASYNC_POOL.submit(asyncio.run, result).result()


def _make_adapter_executor(adapter):
    """Bridge adapter.execute(...) to ActionBus Executor(action) protocol.

    ActionBus calls ``executor(action: Action) -> dict``.
    Adapters expose ``adapter.execute(query=..., **kwargs) -> dict``.
    This bridge extracts the payload from the Action and forwards it.
    Handles both sync and async adapter.execute() transparently.
    """

    def executor(action: Action) -> Dict[str, Any]:
        payload = action.payload or {}
        # Try full payload as kwargs (most flexible — works for
        # specialized adapters that accept metric=, entity=, etc.)
        try:
            result = adapter.execute(**payload)
        except TypeError:
            # Fallback: positional query string (MathSolverAdapter etc.)
            query = payload.get("query", "")
            result = adapter.execute(query)
        return _run_maybe_async(result)

    return executor


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
        resource_kernel=None,
    ):
        self.profile = profile

        # Core components — let WorldModel use _UNSET sentinel for proper
        # CognitiveGraph lazy-init (passing None explicitly disables it)
        self.world_model = world_model or WorldModel(profile=profile)
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

        # GraphBuilder — records query→capability relationships
        self.graph_builder = None
        try:
            from waggledance.core.world.graph_builder import GraphBuilder
            if self.world_model.graph is not None:
                self.graph_builder = GraphBuilder(self.world_model.graph)
                self.graph_builder.ensure_capability_nodes(self.capability_registry)
        except Exception as exc:
            log.debug("GraphBuilder unavailable: %s", exc)

        # Seed self-entity in the CognitiveGraph
        if self.world_model.graph is not None:
            self.world_model.ensure_self_entity(profile=profile)

        # Executor binding
        self._query_count = 0
        self._graph_health_logged = False
        self._executor_count = 0
        try:
            from waggledance.bootstrap.capability_loader import bind_executors
            self._executor_count = bind_executors(self.capability_registry)
        except Exception as exc:
            log.debug("Capability loader unavailable: %s", exc)

        # Wire registry executors → action bus (bridge adapter interface)
        for cap_id in self.capability_registry.executor_ids():
            adapter = self.capability_registry.get_executor(cap_id)
            if adapter is not None:
                self.action_bus.register_executor(
                    cap_id, _make_adapter_executor(adapter)
                )

        # MAGMA adapters (audit, event log, trust)
        self.audit = None
        self.event_log = None
        self.trust = None
        try:
            from waggledance.core.magma.audit_projector import AuditProjector
            self.audit = AuditProjector()
        except Exception as exc:
            log.debug("AuditProjector unavailable: %s", exc)
        try:
            from waggledance.core.magma.event_log_adapter import EventLogAdapter
            self.event_log = EventLogAdapter()
        except Exception as exc:
            log.debug("EventLogAdapter unavailable: %s", exc)
        try:
            from waggledance.core.magma.trust_adapter import TrustAdapter
            self.trust = TrustAdapter()
        except Exception as exc:
            log.debug("TrustAdapter unavailable: %s", exc)
        self.replay = None
        try:
            from waggledance.core.magma.replay_engine import ReplayAdapter
            self.replay = ReplayAdapter()
        except Exception as exc:
            log.debug("ReplayAdapter unavailable: %s", exc)
        self.provenance = None
        try:
            from waggledance.core.magma.provenance import ProvenanceAdapter
            self.provenance = ProvenanceAdapter()
        except Exception as exc:
            log.debug("ProvenanceAdapter unavailable: %s", exc)

        # Persistence adapters
        self.world_store = None
        self.procedural_store = None
        self.case_store = None
        self.verifier_store = None
        try:
            from waggledance.adapters.persistence.sqlite_world_store import SQLiteWorldStore
            self.world_store = SQLiteWorldStore()
        except Exception as exc:
            log.debug("SQLiteWorldStore unavailable: %s", exc)
        try:
            from waggledance.adapters.persistence.sqlite_procedural_store import SQLiteProceduralStore
            self.procedural_store = SQLiteProceduralStore()
        except Exception as exc:
            log.debug("SQLiteProceduralStore unavailable: %s", exc)
        try:
            from waggledance.adapters.persistence.sqlite_case_store import SQLiteCaseStore
            self.case_store = SQLiteCaseStore()
        except Exception as exc:
            log.debug("SQLiteCaseStore unavailable: %s", exc)
        try:
            from waggledance.adapters.persistence.sqlite_verifier_store import SQLiteVerifierStore
            self.verifier_store = SQLiteVerifierStore()
        except Exception as exc:
            log.debug("SQLiteVerifierStore unavailable: %s", exc)

        # Domain capsule (profile-specific reasoning config)
        self.capsule = None
        try:
            from core.domain_capsule import DomainCapsule
            self.capsule = DomainCapsule.load(profile.lower())
        except Exception as exc:
            log.debug("DomainCapsule unavailable for %s: %s", profile, exc)

        magma_ok = any([self.audit, self.event_log, self.trust, self.replay, self.provenance])
        persist_ok = any([self.world_store, self.procedural_store,
                          self.case_store, self.verifier_store])
        log.info(
            "%d executors bound, MAGMA: %s, persistence: %s, capsule: %s",
            self._executor_count,
            "yes" if magma_ok else "no",
            "yes" if persist_ok else "no",
            self.capsule.domain if self.capsule else "none",
        )

        self._night_pipeline = None  # lazy-initialized on first night learning run

        # Prediction Error Ledger + Capability Confidence
        self.prediction_ledger = None
        self.capability_confidence = None
        try:
            from waggledance.core.learning.prediction_error_ledger import PredictionErrorLedger
            self.prediction_ledger = PredictionErrorLedger()
        except Exception as exc:
            log.debug("PredictionErrorLedger unavailable: %s", exc)
        try:
            from waggledance.core.learning.capability_confidence import CapabilityConfidenceTracker
            self.capability_confidence = CapabilityConfidenceTracker()
        except Exception as exc:
            log.debug("CapabilityConfidenceTracker unavailable: %s", exc)

        # ResourceKernel + AdmissionControl (load management)
        self.resource_kernel = None
        self.admission_control = None
        try:
            from waggledance.core.autonomy.resource_kernel import (
                AdmissionControl,
                ResourceKernel,
            )
            self.resource_kernel = resource_kernel or ResourceKernel()
            self.admission_control = AdmissionControl(kernel=self.resource_kernel)
        except Exception as exc:
            log.debug("ResourceKernel unavailable: %s", exc)

        # ResourceGuard — OOM protection + throttling
        self.resource_guard = None
        try:
            from core.resource_guard import ResourceGuard
            self.resource_guard = ResourceGuard()
        except Exception as exc:
            log.debug("ResourceGuard unavailable: %s", exc)

        self._started = False
        log.info("AutonomyRuntime initialised (profile=%s)", profile)

    # ── Lifecycle ─────────────────────────────────────────

    def start(self):
        """Start the runtime."""
        if self.resource_kernel:
            self.resource_kernel.start()
        self._started = True
        log.info("AutonomyRuntime started")

    def stop(self):
        """Stop the runtime and persist state."""
        self._started = False
        if self.resource_kernel:
            self.resource_kernel.stop()
        self.world_model.save()
        for store_name in ("world_store", "procedural_store",
                           "case_store", "verifier_store"):
            store = getattr(self, store_name, None)
            if store:
                self._persist_safe(f"close.{store_name}", store.close)
        _ASYNC_POOL.shutdown(wait=False)
        log.info("AutonomyRuntime stopped")

    @property
    def is_running(self) -> bool:
        return self._started

    # ── MAGMA helpers ─────────────────────────────────────

    def _magma_safe(self, label: str, fn, *args, **kwargs):
        """Call a MAGMA adapter method safely — never break the hot path."""
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            log.debug("MAGMA %s failed: %s", label, exc)
            return None

    def _persist_safe(self, label: str, fn, *args, **kwargs):
        """Call a persistence adapter method safely — never break the hot path."""
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            log.debug("Persist %s failed: %s", label, exc)
            return None

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

        # 0. Admission check (resource-gated)
        if self.admission_control:
            from waggledance.core.autonomy.resource_kernel import AdmissionDecision
            admission = self.admission_control.check(work_type="query")
            if admission.decision == AdmissionDecision.REJECT:
                return {
                    "intent": "unknown",
                    "error": f"Rejected: {admission.reason}",
                    "deferred": False,
                    "elapsed_ms": round((time.time() - t0) * 1000, 2),
                }
            elif admission.decision == AdmissionDecision.DEFER:
                return {
                    "intent": "unknown",
                    "error": f"Deferred: {admission.reason}",
                    "deferred": True,
                    "wait_ms": admission.wait_ms,
                    "elapsed_ms": round((time.time() - t0) * 1000, 2),
                }

        # ResourceGuard — OOM protection
        if self.resource_guard:
            if self.resource_guard.is_critical():
                self.resource_guard.trigger_emergency_gc()
                log.warning("ResourceGuard: critical memory — emergency GC triggered")

        # Track task start for load management
        if self.resource_kernel:
            self.resource_kernel.record_task_start()

        # 1. Intent classification
        intent = SolverRouter.classify_intent(query)

        # 1b. Capsule decision matching (enriches context for routing)
        capsule_match = None
        if self.capsule:
            try:
                capsule_match = self.capsule.match_decision(query)
                if capsule_match:
                    context["capsule_decision"] = capsule_match.decision_id
                    context["capsule_layer"] = capsule_match.layer
                    context["capsule_confidence"] = capsule_match.confidence
            except Exception as exc:
                log.debug("Capsule match failed: %s", exc)

        # 2. World model enrichment
        snapshot_before = self.world_model.take_snapshot()
        context["profile"] = self.profile

        # Persist: save snapshot_before
        if self.world_store:
            self._persist_safe("world.snapshot_before",
                self.world_store.save_snapshot,
                snapshot_before.snapshot_id,
                snapshot_before.to_dict(),
                profile=self.profile,
                source_type="observed")

        # 3. Capability selection
        route_result = self.solver_router.route(intent, query, context)

        # MAGMA: record capability selection
        if self.audit and AuditEntry is not None:
            cap_ids = [c.capability_id for c in route_result.selection.selected]
            self._magma_safe("audit.capability_selected", self.audit.record,
                AuditEntry(
                    event_type="capability.selected",
                    payload={"intent": intent, "capabilities": cap_ids,
                             "quality_path": route_result.quality_path},
                    capability_id=cap_ids[0] if cap_ids else "",
                ))

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
        action = Action(
            capability_id=primary_cap.capability_id,
            payload={"query": query},
        )

        # 5. Submit through action bus (policy + execution)
        action_result = self.action_bus.submit(
            action, primary_cap,
            quality_path=route_result.quality_path,
            context=context,
        )

        # MAGMA: record policy decision + action outcome
        decision_str = "approved" if action_result.decision.approved else "denied"
        if self.audit:
            self._magma_safe("audit.policy", self.audit.record_policy_decision,
                action.action_id, decision_str,
                reason=action_result.decision.reason or "")
            action_status = "executed" if action_result.executed else "denied"
            self._magma_safe("audit.action", self.audit.record_action_event,
                action.action_id, action_status,
                capability_id=primary_cap.capability_id)
        if self.event_log:
            self._magma_safe("event_log.policy", self.event_log.log_policy_decision,
                action.action_id, decision_str,
                reason=action_result.decision.reason or "")

        # 6. Verification (if executed)
        verifier_result = None
        if action_result.executed:
            verifier_result = self.verifier.verify_simple(
                action_result.result or {},
                expected_fields=primary_cap.success_criteria,
            )
            # MAGMA: record verification outcome
            if self.audit and verifier_result and AuditEntry is not None:
                v_status = "passed" if verifier_result.passed else "failed"
                self._magma_safe("audit.verification", self.audit.record,
                    AuditEntry(
                        event_type=f"verification.{v_status}",
                        payload={"confidence": verifier_result.confidence},
                        action_id=action.action_id,
                        capability_id=primary_cap.capability_id,
                    ))

        # Persist: save verifier result
        if self.verifier_store and verifier_result:
            self._persist_safe("verifier.result",
                self.verifier_store.save_result,
                verifier_result.to_dict(),
                action_id=action.action_id,
                capability_id=primary_cap.capability_id)

        # 6b. Record prediction error + update capability confidence
        if action_result.executed:
            verified_ok = verifier_result.passed if verifier_result else False
            v_confidence = verifier_result.confidence if verifier_result else 0.0
            if self.prediction_ledger:
                self._persist_safe("ledger.record",
                    self.prediction_ledger.record,
                    query_id=action.action_id,
                    solver_used=primary_cap.capability_id,
                    verified=verified_ok,
                    confidence=v_confidence,
                    intent=intent)
            if self.capability_confidence:
                self._persist_safe("confidence.update",
                    self.capability_confidence.update,
                    primary_cap.capability_id, verified_ok)
                # Sync confidence scores to solver_router for tiebreaking
                self.solver_router.set_capability_confidence(
                    self.capability_confidence.get_all()
                )

        # 7. Case trajectory
        case = self.case_builder.build(
            query=query,
            intent=intent,
            capabilities=route_result.selection.selected,
            actions=[action],
            verifier_result=verifier_result,
            snapshot_before=snapshot_before,
        )

        # MAGMA: record case + trust + provenance
        if self.audit:
            self._magma_safe("audit.case", self.audit.record_case,
                case.trajectory_id, case.quality_grade.value,
                detail={"intent": intent, "capability": primary_cap.capability_id})
        if self.event_log:
            self._magma_safe("event_log.case", self.event_log.log_case_trajectory,
                case.trajectory_id, case.quality_grade.value,
                intent=intent, capability=primary_cap.capability_id)
        verified_ok = verifier_result.passed if verifier_result else False
        if self.trust:
            self._magma_safe("trust.capability", self.trust.record_observation,
                "capability", primary_cap.capability_id,
                success=action_result.executed and verified_ok,
                quality_path=route_result.quality_path)
        if self.provenance:
            src = "confirmed_by_verifier" if verified_ok else "proposed_by_llm"
            conf = verifier_result.confidence if verifier_result else 0.5
            self._magma_safe("provenance.case", self.provenance.record_provenance,
                fact_id=f"case:{case.trajectory_id}",
                source_type=src,
                capability_id=primary_cap.capability_id,
                quality_grade=case.quality_grade.value,
                confidence=conf)

        # 8. Record in CognitiveGraph
        if self.graph_builder:
            self._magma_safe("graph.record", self.graph_builder.record,
                query=query, intent=intent,
                capability_id=primary_cap.capability_id,
                executed=action_result.executed,
                quality_grade=case.quality_grade.value,
                quality_path=route_result.quality_path)

        # Persist: save case trajectory
        if self.case_store:
            self._persist_safe("case.trajectory",
                self.case_store.save_case,
                case.to_dict(),
                intent=intent,
                elapsed_ms=round((time.time() - t0) * 1000, 2))

        # Graph health check — log once after 10 queries
        self._query_count += 1
        if self._query_count == 10 and not self._graph_health_logged:
            self._graph_health_logged = True
            self._check_graph_health()

        elapsed = round((time.time() - t0) * 1000, 2)

        # Track task end for load management
        if self.resource_kernel:
            self.resource_kernel.record_task_end(
                latency_ms=elapsed, success=action_result.executed)

        result = {
            "intent": intent,
            "quality_path": route_result.quality_path,
            "capability": primary_cap.capability_id,
            "executed": action_result.executed,
            "result": action_result.result,
            "approved": action_result.decision.approved,
            "elapsed_ms": elapsed,
        }
        if capsule_match:
            result["capsule_decision"] = capsule_match.decision_id
            result["capsule_layer"] = capsule_match.layer
        return result

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
        t0 = time.time()

        # 1. Create and accept goal
        goal = self.goal_engine.propose(goal_type, description, priority)
        if self.audit:
            self._magma_safe("audit.goal.proposed", self.audit.record_goal_event,
                goal.goal_id, "proposed",
                {"type": goal_type, "priority": priority})
        self.goal_engine.accept(goal.goal_id)
        if self.audit:
            self._magma_safe("audit.goal.accepted", self.audit.record_goal_event,
                goal.goal_id, "accepted")
        if self.replay:
            self._magma_safe("replay.goal.proposed", self.replay.record_mission_event,
                goal.goal_id, "goal.proposed",
                payload={"type": goal_type, "description": description[:200]},
                step_order=0)

        # 2. Create plan
        plan = self.planner.create_plan(goal, context)
        self.goal_engine.mark_planned(goal.goal_id)
        if self.audit and AuditEntry is not None:
            self._magma_safe("audit.plan.created", self.audit.record,
                AuditEntry(
                    event_type="plan.created",
                    payload={"steps": len(plan.steps)},
                    goal_id=goal.goal_id,
                ))
            self._magma_safe("audit.goal.planned", self.audit.record_goal_event,
                goal.goal_id, "planned")
        if self.replay:
            self._magma_safe("replay.plan", self.replay.record_mission_event,
                goal.goal_id, "plan.created",
                payload={"steps": len(plan.steps)},
                step_order=0)

        # 3. Take snapshot before execution
        snapshot_before = self.world_model.take_snapshot()

        # Persist: save snapshot_before
        if self.world_store:
            self._persist_safe("mission.snapshot_before",
                self.world_store.save_snapshot,
                snapshot_before.snapshot_id,
                snapshot_before.to_dict(),
                profile=self.profile,
                source_type="observed")

        self.goal_engine.start_execution(goal.goal_id)
        if self.audit:
            self._magma_safe("audit.goal.executing", self.audit.record_goal_event,
                goal.goal_id, "executing")
        if self.replay:
            try:
                sb_dict = snapshot_before.to_dict()
            except Exception:
                sb_dict = {"snapshot_id": getattr(snapshot_before, "snapshot_id", "")}
            self._magma_safe("replay.snapshot_before", self.replay.set_mission_metadata,
                goal.goal_id, goal_type=goal_type, status="executing",
                world_before=sb_dict)

        # 4. Execute steps
        executed_actions: List[Action] = []
        all_succeeded = True

        for step_idx, step in enumerate(plan.steps):
            cap = self.capability_registry.get(step.capability_id)
            if cap is None:
                log.warning("Capability not found: %s (step %d/%d)",
                            step.capability_id, step_idx + 1, len(plan.steps))
                all_succeeded = False
                break

            action = Action(
                capability_id=step.capability_id,
                goal_id=goal.goal_id,
                payload={"query": description},
            )

            result = self.action_bus.submit(
                action, cap,
                quality_path=self.planner.estimate_quality_path(plan),
                context=context,
            )
            executed_actions.append(action)

            # MAGMA: record step execution
            action_status = "executed" if result.executed else "denied"
            if self.audit:
                self._magma_safe("audit.step", self.audit.record_action_event,
                    action.action_id, action_status,
                    capability_id=step.capability_id)
            if self.trust:
                self._magma_safe("trust.step", self.trust.record_observation,
                    "capability", step.capability_id,
                    success=result.executed,
                    quality_path=self.planner.estimate_quality_path(plan))
            if self.replay:
                self._magma_safe("replay.step", self.replay.record_mission_event,
                    goal.goal_id, f"action.{action_status}",
                    payload={"capability_id": step.capability_id},
                    step_order=step_idx + 1,
                    capability_id=step.capability_id,
                    action_id=action.action_id,
                    result="ok" if result.executed else "failed")

            if result.executed:
                step.completed = True
            else:
                all_succeeded = False
                break  # Stop on first failure

        # 5. Take snapshot after
        snapshot_after = self.world_model.take_snapshot()

        # Persist: save snapshot_after
        if self.world_store:
            self._persist_safe("mission.snapshot_after",
                self.world_store.save_snapshot,
                snapshot_after.snapshot_id,
                snapshot_after.to_dict(),
                profile=self.profile,
                source_type="observed")

        # 6. Verify and update goal status
        if all_succeeded and plan.is_complete:
            self.goal_engine.mark_verified(goal.goal_id)
            if self.audit:
                self._magma_safe("audit.goal.verified", self.audit.record_goal_event,
                    goal.goal_id, "verified")
        elif not all_succeeded:
            self.goal_engine.mark_failed(goal.goal_id, "Step execution failed")
            if self.audit:
                self._magma_safe("audit.goal.failed", self.audit.record_goal_event,
                    goal.goal_id, "failed",
                    {"reason": "Step execution failed"})

        # MAGMA: finalize replay metadata
        elapsed_ms = round((time.time() - t0) * 1000, 2)
        if self.replay:
            try:
                sa_dict = snapshot_after.to_dict()
            except Exception:
                sa_dict = {"snapshot_id": getattr(snapshot_after, "snapshot_id", "")}
            final_status = goal.status.value
            self._magma_safe("replay.finalize", self.replay.set_mission_metadata,
                goal.goal_id, status=final_status,
                world_after=sa_dict, duration_ms=elapsed_ms)

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

        # MAGMA: record case + event log + provenance
        if self.audit:
            self._magma_safe("audit.case", self.audit.record_case,
                case.trajectory_id, case.quality_grade.value,
                goal_id=goal.goal_id,
                detail={"quality_path": quality_path,
                        "steps": len(plan.steps)})
        if self.event_log:
            self._magma_safe("event_log.case", self.event_log.log_case_trajectory,
                case.trajectory_id, case.quality_grade.value,
                intent=goal_type, goal_id=goal.goal_id)
        if self.provenance:
            self._magma_safe("provenance.case", self.provenance.record_provenance,
                fact_id=f"mission:{case.trajectory_id}",
                source_type="learned_from_case",
                quality_grade=case.quality_grade.value,
                confidence=0.75 if all_succeeded else 0.3)
        if self.replay:
            self._magma_safe("replay.grade", self.replay.set_mission_metadata,
                goal.goal_id, quality_grade=case.quality_grade.value)

        # Persist: save case trajectory
        if self.case_store:
            self._persist_safe("mission.case",
                self.case_store.save_case,
                case.to_dict(),
                intent=goal_type,
                elapsed_ms=elapsed_ms)

        return {
            "goal_id": goal.goal_id,
            "status": goal.status.value,
            "plan_steps": len(plan.steps),
            "steps_completed": sum(1 for s in plan.steps if s.completed),
            "quality_path": quality_path,
            "quality_grade": case.quality_grade.value,
        }

    # ── Proactive goals ────────────────────────────────────

    def check_proactive_goals(
        self,
        observations: Optional[Dict[str, float]] = None,
        threshold: float = 2.0,
    ) -> List[Goal]:
        """Check world model residuals and propose proactive goals for deviations.

        Args:
            observations: {entity.metric: current_value} readings
            threshold: absolute residual above which a goal is proposed

        Returns:
            List of newly proposed Goal objects.
        """
        if not observations:
            return []

        residuals = self.world_model.compute_all_residuals(observations)
        proposed: List[Goal] = []

        for key, residual in residuals.items():
            if abs(residual) < threshold:
                continue
            parts = key.split(".", 1)
            entity_id = parts[0] if parts else key
            metric = parts[1] if len(parts) > 1 else "metric"

            direction = "high" if residual > 0 else "low"
            goal = self.goal_engine.propose(
                goal_type="diagnose",
                description=(
                    f"Investigate {entity_id}.{metric}: "
                    f"residual {residual:+.2f} ({direction})"
                ),
                priority=70 if abs(residual) > threshold * 2 else 50,
            )
            proposed.append(goal)

            if self.audit:
                self._magma_safe(
                    "audit.proactive_goal",
                    self.audit.record_goal_event,
                    goal.goal_id, "proposed",
                    {"proactive": True, "entity": entity_id,
                     "metric": metric, "residual": residual},
                )

        if proposed:
            log.info("Proposed %d proactive goals from %d residuals",
                     len(proposed), len(residuals))
        return proposed

    # ── Night learning ─────────────────────────────────────

    def run_night_learning(
        self,
        legacy_records: Optional[List[Any]] = None,
    ) -> Dict[str, Any]:
        """Drain accumulated day cases and run a night learning cycle.

        Returns the NightLearningResult as a dict.
        """
        from waggledance.core.learning.night_learning_pipeline import (
            NightLearningPipeline,
        )

        if self._night_pipeline is None:
            self._night_pipeline = NightLearningPipeline(
                profile=self.profile,
                case_builder=self.case_builder,
            )

        day_cases = self.case_builder.drain_cases()
        result = self._night_pipeline.run_cycle(
            day_cases=day_cases,
            legacy_records=legacy_records,
        )

        log.info(
            "Night learning: %d cases → %d gold, %d silver, %d bronze, %d quarantine",
            len(day_cases),
            result.gold_count,
            result.silver_count,
            result.bronze_count,
            result.quarantine_count,
        )
        return result.to_dict()

    # ── Graph health ──────────────────────────────────────

    def _check_graph_health(self):
        """Log a graph health summary after initial queries."""
        g = self.world_model.graph
        if g is None:
            log.warning("Graph health: CognitiveGraph is None — learning loop inactive")
            return
        stats = g.stats()
        nodes = stats.get("nodes", 0)
        edges = stats.get("edges", 0)
        if edges == 0:
            log.warning("Graph health: %d nodes, 0 edges after %d queries — graph may not be recording",
                        nodes, self._query_count)
        else:
            log.info("Graph health: %d nodes, %d edges after %d queries — OK",
                     nodes, edges, self._query_count)

    # ── Stats ─────────────────────────────────────────────

    def stats(self) -> dict:
        s = {
            "profile": self.profile,
            "running": self._started,
            "capsule": self.capsule.to_dict() if self.capsule else None,
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
        if self.resource_kernel:
            s["resource_kernel"] = self.resource_kernel.stats()
        if self.admission_control:
            s["admission_control"] = self.admission_control.stats()
        if self.resource_guard:
            s["resource_guard"] = self.resource_guard.stats
        if self.audit:
            s["magma_audit"] = self._magma_safe("stats.audit", self.audit.stats) or {}
        if self.event_log:
            s["magma_event_log"] = self._magma_safe("stats.event_log", self.event_log.stats) or {}
        if self.trust:
            s["magma_trust"] = self._magma_safe("stats.trust", self.trust.stats) or {}
        if self.replay:
            s["magma_replay"] = self._magma_safe("stats.replay", self.replay.stats) or {}
        if self.provenance:
            s["magma_provenance"] = self._magma_safe("stats.provenance", self.provenance.stats) or {}
        if self.world_store:
            s["persist_world"] = self._persist_safe("stats.world_store", self.world_store.stats) or {}
        if self.procedural_store:
            s["persist_procedural"] = self._persist_safe("stats.procedural_store", self.procedural_store.stats) or {}
        if self.case_store:
            s["persist_cases"] = self._persist_safe("stats.case_store", self.case_store.stats) or {}
        if self.verifier_store:
            s["persist_verifier"] = self._persist_safe("stats.verifier_store", self.verifier_store.stats) or {}
        if self.prediction_ledger:
            s["prediction_ledger"] = self.prediction_ledger.stats()
        if self.capability_confidence:
            s["capability_confidence"] = self.capability_confidence.stats()
        return s
