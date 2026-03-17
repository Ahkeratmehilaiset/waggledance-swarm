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
from waggledance.core.autonomy.resource_kernel import ResourceKernel
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
    ) -> Dict[str, Any]:
        """Handle a user query through the autonomy runtime."""
        self._request_count += 1
        t0 = time.time()

        try:
            self._resource_kernel.record_task_start()

            # Route through compatibility layer
            result = self._compatibility.handle_query(query, context)

            elapsed = round((time.time() - t0) * 1000, 2)
            result["service_elapsed_ms"] = elapsed
            return result

        except Exception as e:
            self._error_count += 1
            self._lifecycle.report_health("runtime", False, str(e))
            return {
                "error": str(e),
                "elapsed_ms": round((time.time() - t0) * 1000, 2),
            }
        finally:
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

        try:
            result = self._runtime.execute_mission(
                goal_type=goal_type,
                description=description,
                priority=priority,
                context=context,
            )
            return result
        except Exception as e:
            self._error_count += 1
            return {"error": str(e)}

    # ── Status and health ──────────────────────────────────

    def get_status(self) -> Dict[str, Any]:
        """Get comprehensive service status."""
        return {
            "profile": self._profile,
            "lifecycle": self._lifecycle.stats(),
            "resource_kernel": self._resource_kernel.stats(),
            "runtime": self._runtime.stats() if self._runtime.is_running else {},
            "compatibility": self._compatibility.stats(),
            "requests": self._request_count,
            "errors": self._error_count,
        }

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

    def stats(self) -> dict:
        return {
            "profile": self._profile,
            "requests": self._request_count,
            "errors": self._error_count,
            "is_running": self.is_running,
        }
