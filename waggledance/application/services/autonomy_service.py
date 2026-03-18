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
        self._resource_kernel = resource_kernel or ResourceKernel()
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

        Emits specialist accuracy metrics (Priority 2) from canary results.
        """
        if self._night_pipeline is None:
            return {"error": "Night learning pipeline not configured"}

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
        return {
            "configured": True,
            "running": self._night_pipeline.is_running,
            "total_cycles": len(self._night_pipeline._history),
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
