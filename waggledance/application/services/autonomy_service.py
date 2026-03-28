"""
Autonomy Service — application-layer facade for the autonomy runtime.

Exposes the autonomy runtime's capabilities to API endpoints:
  - Query handling (solver-first, LLM-last)
  - Mission execution (goal → plan → execute → verify)
  - Runtime status and health
  - Night learning control
  - Resource monitoring

This service wraps AutonomyRuntime with request validation,
error handling, and response formatting.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional

from waggledance.core.autonomy.compatibility import CompatibilityLayer
from waggledance.core.autonomy.lifecycle import AutonomyLifecycle, RuntimeState
from waggledance.core.autonomy.resource_kernel import (
    AdmissionControl,
    AdmissionDecision,
    ResourceKernel,
)
from waggledance.core.autonomy.runtime import AutonomyRuntime

log = logging.getLogger("waggledance.application.autonomy_service")


class AutonomyService:
    """
    Application service for the autonomy runtime.

    Provides a clean API for external consumers (REST, WebSocket, CLI).
    """

    def __init__(
        self,
        runtime: Optional[AutonomyRuntime] = None,
        lifecycle: Optional[AutonomyLifecycle] = None,
        resource_kernel: Optional[ResourceKernel] = None,
        compatibility: Optional[CompatibilityLayer] = None,
        profile: str = "DEFAULT",
        night_pipeline=None,
    ):
        self._profile = profile
        self._runtime = runtime or AutonomyRuntime(profile=profile)
        self._lifecycle = lifecycle or AutonomyLifecycle(profile=profile)
        # Prefer runtime's resource kernel to avoid split-brain
        self._resource_kernel = (
            resource_kernel
            or getattr(self._runtime, "resource_kernel", None)
            or ResourceKernel()
        )
        self._compatibility = compatibility or CompatibilityLayer(
            runtime=self._runtime,
        )
        self._request_count = 0
        self._error_count = 0

        # Night learning pipeline (Priority 1)
        self._night_pipeline = night_pipeline

        # Admission control
        self._admission = AdmissionControl(kernel=self._resource_kernel)

        # Autonomy KPI metrics
        self._metrics = None
        try:
            from waggledance.core.autonomy.metrics import AutonomyMetrics
            self._metrics = AutonomyMetrics()
        except Exception:
            pass

        # Seed KPI metrics with historical data from persistent stores
        self._seed_historical_metrics()

        # Learning scheduler — checks for pending cases periodically
        self._learning_scheduler_interval = 600  # 10 minutes
        self._learning_min_pending = 10  # minimum cases before triggering
        self._learning_scheduler_stop = threading.Event()
        self._learning_scheduler_thread: Optional[threading.Thread] = None
        self._last_auto_trigger_time = 0.0
        # Auto-start scheduler if night pipeline is configured
        if self._night_pipeline is not None:
            self.start_learning_scheduler()

    def _seed_historical_metrics(self) -> None:
        """Seed KPI counters with historical data from persistent stores.

        Without this, KPI case_grades show all zeros on startup even
        when the persistent case store contains thousands of graded
        records from prior sessions.  Only counters are seeded (not
        rate samples, which are session-scoped by design).
        """
        if not self._metrics:
            return

        # Seed case grade counters from the persistent case store
        case_store = getattr(self._runtime, "case_store", None)
        if case_store is not None:
            try:
                grades = case_store.grade_distribution()
                for grade, count in grades.items():
                    self._metrics.increment(f"case_grade_{grade}", count)
                total = sum(grades.values())
                if total:
                    log.info(
                        "Seeded KPI case grade counters with %d historical cases",
                        total,
                    )
            except Exception as exc:
                log.warning("Failed to seed case grade metrics: %s", exc)

    # ── Learning scheduler ────────────────────────────────

    def start_learning_scheduler(self) -> None:
        """Start the background learning scheduler.

        Periodically checks for pending cases and triggers a learning
        cycle when conditions are met:
        - pending_cases >= min_pending (default 10)
        - resource kernel allows learning (idle/light load)
        - night pipeline is not already running
        - at least interval seconds since last auto-trigger
        """
        if self._learning_scheduler_thread is not None:
            return  # already running
        self._learning_scheduler_stop.clear()
        self._learning_scheduler_thread = threading.Thread(
            target=self._learning_scheduler_loop,
            name="learning-scheduler",
            daemon=True,
        )
        self._learning_scheduler_thread.start()
        log.info(
            "Learning scheduler started (interval=%ds, min_pending=%d)",
            self._learning_scheduler_interval,
            self._learning_min_pending,
        )

    def stop_learning_scheduler(self) -> None:
        """Stop the background learning scheduler."""
        self._learning_scheduler_stop.set()
        t = self._learning_scheduler_thread
        if t is not None:
            t.join(timeout=5)
            self._learning_scheduler_thread = None
        log.info("Learning scheduler stopped")

    def _learning_scheduler_loop(self) -> None:
        """Background loop that checks for pending cases."""
        while not self._learning_scheduler_stop.is_set():
            self._learning_scheduler_stop.wait(self._learning_scheduler_interval)
            if self._learning_scheduler_stop.is_set():
                break
            try:
                self._maybe_trigger_learning()
            except Exception as e:
                log.warning("Learning scheduler tick error: %s", e)

    def _maybe_trigger_learning(self) -> None:
        """Check conditions and trigger learning if appropriate."""
        if self._night_pipeline is None:
            return
        if self._night_pipeline.is_running:
            return
        if not self._resource_kernel.can_accept_learning():
            return

        case_store = getattr(self._runtime, "case_store", None)
        if case_store is None:
            return

        pending = case_store.pending_count()
        if pending < self._learning_min_pending:
            return

        elapsed = time.time() - self._last_auto_trigger_time
        if elapsed < self._learning_scheduler_interval:
            return

        log.info(
            "Learning scheduler: %d pending cases, triggering cycle",
            pending,
        )
        self._last_auto_trigger_time = time.time()
        result = self.run_learning_cycle()
        log.info(
            "Learning scheduler cycle result: cases_built=%s, models_trained=%s",
            result.get("cases_built", 0),
            result.get("models_trained", 0),
        )

    # ── Lifecycle ──────────────────────────────────────────

    def start(self) -> Dict[str, Any]:
        """Start the autonomy service."""
        # Register components for health tracking
        self._lifecycle.register_component("runtime", True)
        self._lifecycle.register_component("resource_kernel", True)

        self._lifecycle.start()
        self._runtime.start()
        self._resource_kernel.start()

        return {
            "status": "started",
            "state": self._lifecycle.state.value,
            "profile": self._profile,
            "primary": self._lifecycle.primary.value,
            "compatibility_mode": self._lifecycle.compatibility_mode,
        }

    def stop(self) -> Dict[str, Any]:
        """Stop the autonomy service."""
        self._runtime.stop()
        self._resource_kernel.stop()
        self._lifecycle.stop()

        return {
            "status": "stopped",
            "requests_served": self._request_count,
            "errors": self._error_count,
        }

    @property
    def is_running(self) -> bool:
        return self._lifecycle.state in (
            RuntimeState.RUNNING, RuntimeState.DEGRADED,
        )

    # ── Query handling ─────────────────────────────────────

    def handle_query(
        self,
        query: str,
        context: Optional[Dict[str, Any]] = None,
        priority: int = 50,
    ) -> Dict[str, Any]:
        """Handle a user query through the autonomy runtime."""
        self._request_count += 1
        t0 = time.time()

        # Admission control check
        admission = self._admission.check(work_type="query", priority=priority)
        if admission.decision == AdmissionDecision.REJECT:
            return {
                "error": "rejected",
                "reason": admission.reason,
                "elapsed_ms": round((time.time() - t0) * 1000, 2),
            }
        if admission.decision == AdmissionDecision.DEFER:
            return {
                "error": "deferred",
                "reason": admission.reason,
                "wait_ms": admission.wait_ms,
                "elapsed_ms": round((time.time() - t0) * 1000, 2),
            }

        try:
            self._resource_kernel.record_task_start()
            self._admission.record_enqueue()

            # Route through compatibility layer
            result = self._compatibility.handle_query(query, context)

            elapsed = round((time.time() - t0) * 1000, 2)
            result["service_elapsed_ms"] = elapsed

            # Record all metrics
            if self._metrics:
                is_error = bool(result.get("error"))
                quality_path = result.get("quality_path", "")

                # Route accuracy + LLM fallback
                self._metrics.record_route_decision(
                    success=not is_error,
                    was_fallback=quality_path == "bronze",
                    route_type=result.get("intent", ""),
                    latency_ms=elapsed,
                )

                # Capability chain success
                self._metrics.record_capability_chain(
                    success=result.get("executed", False),
                    chain_length=1,
                )

                # Verification (if result contains verifier data)
                if "verified" in result or "verifier_passed" in result:
                    self._metrics.record_verification(
                        passed=result.get("verified", result.get("verifier_passed", False)),
                        confidence=result.get("verifier_confidence", 0.0),
                    )

                # Case quality grade
                grade = result.get("quality_grade") or result.get("quality_path")
                if grade:
                    self._metrics.record_case_grade(grade)

            return result

        except Exception as e:
            self._error_count += 1
            self._lifecycle.report_health("runtime", False, str(e))
            if self._metrics:
                elapsed = round((time.time() - t0) * 1000, 2)
                self._metrics.record_route_decision(
                    success=False, was_fallback=False, latency_ms=elapsed,
                )
                self._metrics.record_capability_chain(success=False, chain_length=0)
            return {
                "error": str(e),
                "elapsed_ms": round((time.time() - t0) * 1000, 2),
            }
        finally:
            self._admission.record_dequeue()
            self._resource_kernel.record_task_end(
                latency_ms=(time.time() - t0) * 1000,
            )

    # ── Mission handling ───────────────────────────────────

    def execute_mission(
        self,
        goal_type: str,
        description: str,
        priority: int = 50,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Execute a mission through the autonomy runtime."""
        self._request_count += 1
        t0 = time.time()

        # Admission control for mission work
        admission = self._admission.check(work_type="query", priority=priority)
        if admission.decision == AdmissionDecision.REJECT:
            return {
                "error": "rejected",
                "reason": admission.reason,
            }

        try:
            self._resource_kernel.record_task_start()
            self._admission.record_enqueue()

            result = self._runtime.execute_mission(
                goal_type=goal_type,
                description=description,
                priority=priority,
                context=context,
            )

            # Record mission metrics
            if self._metrics:
                elapsed = round((time.time() - t0) * 1000, 2)
                steps_completed = result.get("steps_completed", 0)
                plan_steps = result.get("plan_steps", 1)
                all_ok = steps_completed == plan_steps and plan_steps > 0

                self._metrics.record_route_decision(
                    success=all_ok,
                    was_fallback=result.get("quality_path") == "bronze",
                    route_type=goal_type,
                    latency_ms=elapsed,
                )
                self._metrics.record_capability_chain(
                    success=all_ok,
                    chain_length=plan_steps,
                )
                grade = result.get("quality_grade", "")
                if grade:
                    self._metrics.record_case_grade(grade)
                if result.get("status") == "verified":
                    self._metrics.record_verification(passed=True, confidence=0.8)

            return result
        except Exception as e:
            self._error_count += 1
            if self._metrics:
                elapsed = round((time.time() - t0) * 1000, 2)
                self._metrics.record_route_decision(
                    success=False, was_fallback=False,
                    route_type=goal_type, latency_ms=elapsed,
                )
                self._metrics.record_capability_chain(success=False, chain_length=0)
            return {"error": str(e)}
        finally:
            self._admission.record_dequeue()
            self._resource_kernel.record_task_end(
                latency_ms=(time.time() - t0) * 1000,
            )

    # ── Status and health ──────────────────────────────────

    def get_status(self) -> Dict[str, Any]:
        """Get comprehensive service status."""
        status = {
            "profile": self._profile,
            "lifecycle": self._lifecycle.stats(),
            "resource_kernel": self._resource_kernel.stats(),
            "admission": self._admission.stats(),
            "runtime": self._runtime.stats() if self._runtime.is_running else {},
            "compatibility": self._compatibility.stats(),
            "requests": self._request_count,
            "errors": self._error_count,
        }
        if self._metrics:
            status["kpis"] = self._metrics.get_kpis()
            status["metrics"] = self._metrics.stats()
        return status

    def validate_cutover(self) -> Dict[str, Any]:
        """Validate cutover readiness."""
        lifecycle_check = self._lifecycle.validate_cutover()

        # Additional service-level checks
        checks = {
            **lifecycle_check,
            "runtime_running": self._runtime.is_running,
            "resource_kernel_running": self._resource_kernel.is_running,
            "error_rate_ok": (
                self._error_count / max(1, self._request_count) < 0.1
            ),
        }

        checks["full_autonomy"] = all([
            checks.get("all_pass", False),
            checks["runtime_running"],
            checks["resource_kernel_running"],
            checks["error_rate_ok"],
        ])

        return checks

    def get_kpis(self) -> Dict[str, Any]:
        """Get autonomy KPIs from metrics tracker."""
        if self._metrics:
            return self._metrics.get_kpis()
        return {}

    # ── Night learning ─────────────────────────────────────

    def enable_night_mode(self) -> Dict[str, Any]:
        """Enable night learning mode."""
        self._resource_kernel.set_night_mode(True)
        return {
            "night_mode": True,
            "load_level": self._resource_kernel.load_level.value,
        }

    def disable_night_mode(self) -> Dict[str, Any]:
        """Disable night learning mode."""
        self._resource_kernel.set_night_mode(False)
        return {
            "night_mode": False,
            "load_level": self._resource_kernel.load_level.value,
        }

    # ── Night learning pipeline (Priority 1) ──────────────

    def run_learning_cycle(
        self,
        day_cases: Optional[List] = None,
        legacy_records: Optional[List] = None,
    ) -> Dict[str, Any]:
        """Run a night learning cycle through the hexagonal pipeline.

        If day_cases is not provided, loads pending (unprocessed) cases
        from the case_store using the learning watermark. After a
        successful cycle, advances the watermark so those cases are not
        reprocessed.

        Emits specialist accuracy metrics (Priority 2) from canary results.
        """
        if self._night_pipeline is None:
            return {"error": "Night learning pipeline not configured"}

        # Auto-load pending cases from store when none provided
        case_store = getattr(self._runtime, "case_store", None)
        watermark_before = 0.0
        if day_cases is None and case_store is not None:
            try:
                from waggledance.core.domain.autonomy import CaseTrajectory
                pending_dicts = case_store.fetch_pending(limit=5000)
                if pending_dicts:
                    day_cases = [
                        CaseTrajectory.from_stored_dict(d)
                        for d in pending_dicts
                    ]
                    watermark_before = max(
                        d.get("_stored_at", 0.0) for d in pending_dicts
                    )
                    log.info(
                        "Loaded %d pending cases from store (watermark %.1f)",
                        len(day_cases), watermark_before,
                    )
            except Exception as e:
                log.warning("Failed to load pending cases: %s", e)

        try:
            result = self._night_pipeline.run_cycle(
                day_cases=day_cases,
                legacy_records=legacy_records,
            )

            # Priority 2: Emit specialist accuracy from canary evaluations
            if self._metrics and result.canary_results:
                for model_id, outcome in result.canary_results.items():
                    self._metrics.record_specialist_prediction(
                        model_id, correct=(outcome == "promoted"),
                    )

            # Emit night learning gold rate
            if self._metrics:
                total_graded = result.gold_count + result.silver_count + \
                    result.bronze_count + result.quarantine_count
                if total_graded > 0:
                    for _ in range(result.gold_count):
                        self._metrics.record_case_grade("gold")
                    for _ in range(result.silver_count):
                        self._metrics.record_case_grade("silver")
                    for _ in range(result.bronze_count):
                        self._metrics.record_case_grade("bronze")
                    for _ in range(result.quarantine_count):
                        self._metrics.record_case_grade("quarantine")

            # Advance watermark after successful cycle
            if watermark_before > 0 and case_store is not None and result.success:
                try:
                    case_store.set_watermark(watermark_before)
                    log.info("Advanced learning watermark to %.1f", watermark_before)
                except Exception as e:
                    log.warning("Failed to advance watermark: %s", e)

            return result.to_dict()

        except Exception as e:
            self._error_count += 1
            log.error("Learning cycle failed: %s", e)
            return {"error": str(e)}

    def get_learning_status(self) -> Dict[str, Any]:
        """Get night learning pipeline status."""
        if self._night_pipeline is None:
            return {"configured": False}
        last = self._night_pipeline.last_result()

        # Include pending case count for observability
        pending = 0
        case_store = getattr(self._runtime, "case_store", None)
        if case_store is not None:
            try:
                pending = case_store.pending_count()
            except Exception:
                pass

        return {
            "configured": True,
            "running": self._night_pipeline.is_running,
            "total_cycles": len(self._night_pipeline._history),
            "pending_cases": pending,
            "scheduler": {
                "active": self._learning_scheduler_thread is not None
                    and self._learning_scheduler_thread.is_alive(),
                "interval_s": self._learning_scheduler_interval,
                "min_pending": self._learning_min_pending,
                "last_auto_trigger": self._last_auto_trigger_time,
            },
            "last_result": last.to_dict() if last else None,
            "pipeline_stats": self._night_pipeline.stats(),
        }

    # ── Proactive goals (Priority 3) ──────────────────────

    def check_proactive_goals(
        self,
        observations: Optional[Dict[str, float]] = None,
        threshold: float = 2.0,
    ) -> Dict[str, Any]:
        """Check world model for deviations and propose proactive goals."""
        try:
            goals = self._runtime.check_proactive_goals(
                observations=observations,
                threshold=threshold,
            )

            # Emit proactive goal metrics
            if self._metrics:
                for _ in goals:
                    self._metrics.record_proactive_goal()

            return {
                "goals_proposed": len(goals),
                "goal_ids": [g.goal_id for g in goals],
            }
        except Exception as e:
            self._error_count += 1
            return {"error": str(e)}

    # ── Capability confidence ─────────────────────────────

    def get_capability_confidence(self) -> Dict[str, Any]:
        """Get current capability confidence scores."""
        tracker = getattr(self._runtime, "capability_confidence", None)
        if tracker is None:
            return {"available": False}
        all_scores = tracker.get_all()
        lowest = tracker.get_lowest(5)
        improving, degrading = tracker.get_trends(3)
        return {
            "available": True,
            "scores": {k: round(v, 4) for k, v in all_scores.items()},
            "lowest": [{"capability": c, "confidence": round(v, 4)} for c, v in lowest],
            "improving": [{"capability": c, "delta": round(d, 4)} for c, d in improving],
            "degrading": [{"capability": c, "delta": round(d, 4)} for c, d in degrading],
        }

    # ── Prediction error ledger ─────────────────────────────

    def get_prediction_ledger_analysis(self) -> Dict[str, Any]:
        """Get prediction error ledger analysis."""
        ledger = getattr(self._runtime, "prediction_ledger", None)
        if ledger is None:
            return {"available": False}
        analysis = ledger.analyze()
        result = analysis.to_dict()
        result["available"] = True
        return result

    # ── User model (v3.3) ─────────────────────────────────

    def get_user_model(self) -> Dict[str, Any]:
        """Get lightweight user model from CognitiveGraph.

        Pending promises are derived from GoalEngine (source of truth),
        NOT from cached pending_promise_goal_ids on the user node.
        """
        user = self._runtime.world_model.get_user_entity()
        if user is None:
            return {"available": False}
        # GoalEngine is the source of truth for pending promises
        live_promises = []
        try:
            for g in self._runtime.goal_engine.get_promises_to_user():
                live_promises.append({
                    "goal_id": g.goal_id,
                    "description": g.description,
                    "priority": g.priority,
                    "status": g.status.value,
                })
        except Exception:
            pass
        return {
            "available": True,
            "interaction_count": user.get("interaction_count", 0),
            "explicit_correction_count": user.get("explicit_correction_count", 0),
            "verification_fail_count": user.get("verification_fail_count", 0),
            "promises_pending": live_promises,
            "preferred_language": user.get("preferred_language", ""),
            "last_interaction_at": user.get("last_interaction_at", 0.0),
            "last_user_correction_at": user.get("last_user_correction_at", 0.0),
        }

    # ── Safety cases (Priority 4) ─────────────────────────

    def get_safety_cases(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Get recent safety cases from the policy engine."""
        builder = getattr(self._runtime.policy_engine, "_safety_builder", None)
        if builder is None:
            return []
        return [c.to_dict() for c in builder.get_recent_cases(limit)]

    def get_safety_stats(self) -> Dict[str, Any]:
        """Get safety case statistics."""
        builder = getattr(self._runtime.policy_engine, "_safety_builder", None)
        if builder is None:
            return {"available": False}
        stats = builder.stats()
        stats["available"] = True
        return stats

    def stats(self) -> dict:
        return {
            "profile": self._profile,
            "requests": self._request_count,
            "errors": self._error_count,
            "is_running": self.is_running,
            "night_pipeline": self._night_pipeline is not None,
        }
