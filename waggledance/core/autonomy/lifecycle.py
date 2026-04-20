"""
Autonomy Lifecycle — manages runtime mode and component orchestration.

Controls the transition from legacy (HiveMind) to full autonomy:
  - compatibility_mode=True: legacy path active, new runtime shadow
  - compatibility_mode=False: new runtime primary, legacy as fallback

Runtime states:
  INITIALIZING → STARTING → RUNNING → STOPPING → STOPPED
  RUNNING can transition to DEGRADED if critical components fail.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

log = logging.getLogger("waggledance.autonomy.lifecycle")


class RuntimeState(str, Enum):
    """Runtime lifecycle states."""
    INITIALIZING = "initializing"
    STARTING = "starting"
    RUNNING = "running"
    DEGRADED = "degraded"
    STOPPING = "stopping"
    STOPPED = "stopped"


class RuntimeMode(str, Enum):
    """Runtime operating mode."""
    WAGGLEDANCE = "waggledance"  # New autonomy runtime (primary)
    HIVEMIND = "hivemind"        # Legacy HiveMind (compat)
    SHADOW = "shadow"            # Both running, new runtime shadows


@dataclass
class HealthCheck:
    """Result of a component health check."""
    component: str
    healthy: bool
    message: str = ""
    timestamp: float = field(default_factory=time.time)


class AutonomyLifecycle:
    """
    Manages the autonomy runtime lifecycle and mode transitions.

    Controls startup/shutdown sequence and monitors component health.
    """

    def __init__(
        self,
        primary: str = "waggledance",
        compatibility_mode: bool = False,
        profile: str = "DEFAULT",
    ):
        self._primary = RuntimeMode(primary)
        self._compatibility_mode = compatibility_mode
        self._profile = profile
        self._state = RuntimeState.INITIALIZING
        self._started_at: Optional[float] = None
        self._components: Dict[str, bool] = {}
        self._health_history: List[HealthCheck] = []

    # ── Properties ─────────────────────────────────────────

    @property
    def state(self) -> RuntimeState:
        return self._state

    @property
    def primary(self) -> RuntimeMode:
        return self._primary

    @property
    def compatibility_mode(self) -> bool:
        return self._compatibility_mode

    @property
    def profile(self) -> str:
        return self._profile

    @property
    def is_autonomy_primary(self) -> bool:
        """True if the new autonomy runtime is the primary path."""
        return (
            self._primary == RuntimeMode.WAGGLEDANCE
            and not self._compatibility_mode
        )

    @property
    def uptime_s(self) -> float:
        if self._started_at is None:
            return 0.0
        return time.perf_counter() - self._started_at

    # ── Lifecycle ──────────────────────────────────────────

    def start(self) -> bool:
        """Start the runtime. Returns True if started successfully."""
        if self._state not in (RuntimeState.INITIALIZING, RuntimeState.STOPPED):
            log.warning("Cannot start from state %s", self._state.value)
            return False

        self._state = RuntimeState.STARTING
        self._started_at = time.perf_counter()

        # Check all registered components
        all_healthy = self._check_components()

        if all_healthy:
            self._state = RuntimeState.RUNNING
            log.info("AutonomyLifecycle started: mode=%s, compat=%s",
                     self._primary.value, self._compatibility_mode)
        else:
            self._state = RuntimeState.DEGRADED
            log.warning("AutonomyLifecycle started DEGRADED: some components unhealthy")

        return True

    def stop(self) -> bool:
        """Stop the runtime."""
        if self._state in (RuntimeState.STOPPED, RuntimeState.INITIALIZING):
            return False

        self._state = RuntimeState.STOPPING
        log.info("AutonomyLifecycle stopping")
        self._state = RuntimeState.STOPPED
        return True

    def set_mode(self, primary: str, compatibility_mode: bool = False):
        """Switch runtime mode (requires restart for full effect)."""
        self._primary = RuntimeMode(primary)
        self._compatibility_mode = compatibility_mode
        log.info("Runtime mode set: primary=%s, compat=%s",
                 primary, compatibility_mode)

    # ── Component registration ─────────────────────────────

    def register_component(self, name: str, healthy: bool = True):
        """Register a component for health tracking."""
        self._components[name] = healthy

    def report_health(self, component: str, healthy: bool, message: str = ""):
        """Report health status for a component."""
        self._components[component] = healthy
        check = HealthCheck(
            component=component, healthy=healthy, message=message,
        )
        self._health_history.append(check)
        if len(self._health_history) > 1000:
            self._health_history = self._health_history[-500:]

        # Update state if a component becomes unhealthy
        if not healthy and self._state == RuntimeState.RUNNING:
            self._state = RuntimeState.DEGRADED
            log.warning("Component %s unhealthy: %s", component, message)
        elif healthy and self._state == RuntimeState.DEGRADED:
            if self._check_components():
                self._state = RuntimeState.RUNNING

    # ── Cutover validation ─────────────────────────────────

    def validate_cutover(self) -> Dict[str, Any]:
        """
        Validate that all cutover criteria are met.

        Returns dict with pass/fail per criterion.
        """
        results = {
            "runtime_primary": self._primary == RuntimeMode.WAGGLEDANCE,
            "compatibility_off": not self._compatibility_mode,
            "state_running": self._state in (RuntimeState.RUNNING, RuntimeState.DEGRADED),
            "all_components_healthy": self._check_components(),
            "components": dict(self._components),
        }
        results["all_pass"] = all([
            results["runtime_primary"],
            results["compatibility_off"],
            results["state_running"],
            results["all_components_healthy"],
        ])
        return results

    # ── Stats ──────────────────────────────────────────────

    def stats(self) -> dict:
        return {
            "state": self._state.value,
            "primary": self._primary.value,
            "compatibility_mode": self._compatibility_mode,
            "is_autonomy_primary": self.is_autonomy_primary,
            "profile": self._profile,
            "uptime_s": round(self.uptime_s, 1),
            "components": dict(self._components),
            "healthy_components": sum(1 for v in self._components.values() if v),
            "total_components": len(self._components),
        }

    # ── Internal ───────────────────────────────────────────

    def _check_components(self) -> bool:
        """Check if all registered components are healthy."""
        if not self._components:
            return True
        return all(self._components.values())
