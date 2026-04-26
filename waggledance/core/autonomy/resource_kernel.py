# SPDX-License-Identifier: BUSL-1.1
"""
Resource Kernel — unified resource management for the autonomy runtime.

Wraps existing ElasticScaler and AdaptiveThrottle under a single
interface that the autonomy runtime uses for:
  - Hardware tier detection and model selection
  - Dynamic load management (concurrency, batch sizes, intervals)
  - VRAM/memory pressure monitoring
  - Admission control (accept/reject/defer work based on load)
  - Night learning resource allocation

The ResourceKernel does NOT rewrite elastic_scaler or adaptive_throttle —
it wraps them and adds autonomy-specific resource decisions.
"""

from __future__ import annotations

import copy
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

log = logging.getLogger("waggledance.autonomy.resource_kernel")


class LoadLevel(str, Enum):
    """System load classification."""
    IDLE = "idle"           # < 20% utilization
    LIGHT = "light"         # 20-50%
    MODERATE = "moderate"   # 50-75%
    HEAVY = "heavy"         # 75-90%
    CRITICAL = "critical"   # > 90%


class ResourceTier(str, Enum):
    """Hardware capability tier."""
    MINIMAL = "minimal"         # < 4GB RAM, no GPU
    LIGHT = "light"             # 4-8GB RAM, no/small GPU
    STANDARD = "standard"       # 8-16GB RAM, mid GPU
    PROFESSIONAL = "professional"  # 16-32GB RAM, good GPU
    ENTERPRISE = "enterprise"   # 32GB+ RAM, high-end GPU


@dataclass
class ResourceSnapshot:
    """Current resource state at a point in time."""
    timestamp: float = field(default_factory=time.time)
    tier: ResourceTier = ResourceTier.STANDARD
    load_level: LoadLevel = LoadLevel.LIGHT
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    vram_percent: float = 0.0
    active_tasks: int = 0
    max_concurrent: int = 4
    queue_depth: int = 0
    night_mode: bool = False

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "tier": self.tier.value,
            "load_level": self.load_level.value,
            "cpu_percent": self.cpu_percent,
            "memory_percent": self.memory_percent,
            "vram_percent": self.vram_percent,
            "active_tasks": self.active_tasks,
            "max_concurrent": self.max_concurrent,
            "queue_depth": self.queue_depth,
            "night_mode": self.night_mode,
        }


@dataclass
class ResourceLimits:
    """Tier-based resource limits."""
    max_concurrent_queries: int = 4
    max_concurrent_learning: int = 2
    max_batch_size: int = 32
    max_specialist_training: int = 1
    night_learning_budget_s: float = 28800.0  # 8 hours
    heartbeat_interval_s: float = 30.0


# Tier → default limits mapping
_TIER_LIMITS: Dict[str, ResourceLimits] = {
    "minimal": ResourceLimits(
        max_concurrent_queries=1,
        max_concurrent_learning=1,
        max_batch_size=8,
        max_specialist_training=1,
        heartbeat_interval_s=60.0,
    ),
    "light": ResourceLimits(
        max_concurrent_queries=2,
        max_concurrent_learning=1,
        max_batch_size=16,
        max_specialist_training=1,
        heartbeat_interval_s=45.0,
    ),
    "standard": ResourceLimits(
        max_concurrent_queries=4,
        max_concurrent_learning=2,
        max_batch_size=32,
        max_specialist_training=2,
        heartbeat_interval_s=30.0,
    ),
    "professional": ResourceLimits(
        max_concurrent_queries=8,
        max_concurrent_learning=4,
        max_batch_size=64,
        max_specialist_training=4,
        heartbeat_interval_s=20.0,
    ),
    "enterprise": ResourceLimits(
        max_concurrent_queries=16,
        max_concurrent_learning=8,
        max_batch_size=128,
        max_specialist_training=8,
        heartbeat_interval_s=10.0,
    ),
}


class ResourceKernel:
    """
    Unified resource management for the autonomy runtime.

    Wraps ElasticScaler (HW detection) and AdaptiveThrottle (load management)
    into a single interface with autonomy-specific decisions.
    """

    def __init__(
        self,
        tier: Optional[str] = None,
        elastic_scaler: Optional[Any] = None,
        adaptive_throttle: Optional[Any] = None,
    ):
        self._elastic_scaler = elastic_scaler
        self._adaptive_throttle = adaptive_throttle

        # Determine tier
        if tier:
            self._tier = ResourceTier(tier)
        elif elastic_scaler:
            detected = elastic_scaler.tier
            self._tier = self._map_tier(detected.tier if detected else "standard")
        else:
            self._tier = ResourceTier.STANDARD

        self._limits = copy.copy(_TIER_LIMITS.get(self._tier.value, ResourceLimits()))
        self._load_level = LoadLevel.IDLE
        self._active_tasks = 0
        self._night_mode = False
        self._started = False
        self._snapshots: List[ResourceSnapshot] = []

        log.info("ResourceKernel initialized: tier=%s", self._tier.value)

    # ── Lifecycle ──────────────────────────────────────────

    def start(self):
        """Start resource monitoring."""
        self._started = True
        if self._elastic_scaler:
            try:
                self._elastic_scaler.detect()
            except Exception as e:
                log.warning("ElasticScaler detection failed: %s", e)
        log.info("ResourceKernel started")

    def stop(self):
        """Stop resource monitoring."""
        self._started = False
        log.info("ResourceKernel stopped")

    @property
    def is_running(self) -> bool:
        return self._started

    # ── Resource queries ───────────────────────────────────

    @property
    def tier(self) -> ResourceTier:
        return self._tier

    @property
    def limits(self) -> ResourceLimits:
        return self._limits

    @property
    def load_level(self) -> LoadLevel:
        return self._load_level

    @property
    def night_mode(self) -> bool:
        return self._night_mode

    def set_night_mode(self, enabled: bool):
        """Enable/disable night mode (affects resource allocation)."""
        self._night_mode = enabled
        # Always start from default limits to prevent ratcheting
        defaults = copy.copy(_TIER_LIMITS.get(self._tier.value, ResourceLimits()))
        if enabled:
            # Night mode: reduce query capacity, increase learning capacity
            defaults.max_concurrent_queries = max(
                1, defaults.max_concurrent_queries // 2
            )
            defaults.max_concurrent_learning = min(
                defaults.max_concurrent_learning * 2,
                defaults.max_concurrent_queries + defaults.max_concurrent_learning,
            )
        self._limits = defaults
        log.info("Night mode: %s", "enabled" if enabled else "disabled")

    def take_snapshot(self) -> ResourceSnapshot:
        """Take a snapshot of current resource state."""
        vram_pct = 0.0
        if self._elastic_scaler:
            try:
                vram_pct = self._elastic_scaler.get_vram_usage_pct()
            except Exception as exc:
                log.debug("VRAM monitoring failed: %s", exc)

        snapshot = ResourceSnapshot(
            tier=self._tier,
            load_level=self._load_level,
            vram_percent=vram_pct,
            active_tasks=self._active_tasks,
            max_concurrent=self._limits.max_concurrent_queries,
            night_mode=self._night_mode,
        )
        self._snapshots.append(snapshot)
        if len(self._snapshots) > 1000:
            self._snapshots = self._snapshots[-500:]
        return snapshot

    # ── Load tracking ──────────────────────────────────────

    def record_task_start(self):
        """Record that a task has started."""
        self._active_tasks += 1
        self._update_load_level()

    def record_task_end(self, latency_ms: float = 0.0, success: bool = True):
        """Record that a task has ended."""
        self._active_tasks = max(0, self._active_tasks - 1)
        if self._adaptive_throttle:
            if success:
                self._adaptive_throttle.record_success(latency_ms)
            else:
                self._adaptive_throttle.record_error()
        self._update_load_level()

    def _update_load_level(self):
        """Update load level based on active tasks vs capacity."""
        if self._limits.max_concurrent_queries == 0:
            self._load_level = LoadLevel.CRITICAL
            return

        utilization = self._active_tasks / self._limits.max_concurrent_queries
        if utilization < 0.2:
            self._load_level = LoadLevel.IDLE
        elif utilization < 0.5:
            self._load_level = LoadLevel.LIGHT
        elif utilization < 0.75:
            self._load_level = LoadLevel.MODERATE
        elif utilization < 0.9:
            self._load_level = LoadLevel.HEAVY
        else:
            self._load_level = LoadLevel.CRITICAL

    # ── Admission decisions ────────────────────────────────

    def can_accept_query(self) -> bool:
        """Check if the system can accept a new query."""
        return self._active_tasks < self._limits.max_concurrent_queries

    def can_accept_learning(self) -> bool:
        """Check if the system can accept a learning task."""
        return self._load_level in (LoadLevel.IDLE, LoadLevel.LIGHT)

    def can_train_specialist(self) -> bool:
        """Check if the system can start specialist training."""
        return (
            self._load_level in (LoadLevel.IDLE, LoadLevel.LIGHT)
            and self._night_mode
        )

    def should_defer(self) -> bool:
        """Check if non-critical work should be deferred."""
        return self._load_level in (LoadLevel.HEAVY, LoadLevel.CRITICAL)

    def should_shed_load(self) -> bool:
        """Check if the system should actively shed load."""
        return self._load_level == LoadLevel.CRITICAL

    # ── Stats ──────────────────────────────────────────────

    def stats(self) -> dict:
        throttle_status = {}
        if self._adaptive_throttle:
            try:
                throttle_status = self._adaptive_throttle.get_status()
            except Exception as exc:
                log.debug("Throttle status failed: %s", exc)

        return {
            "tier": self._tier.value,
            "load_level": self._load_level.value,
            "active_tasks": self._active_tasks,
            "night_mode": self._night_mode,
            "limits": {
                "max_queries": self._limits.max_concurrent_queries,
                "max_learning": self._limits.max_concurrent_learning,
                "max_batch": self._limits.max_batch_size,
                "heartbeat_s": self._limits.heartbeat_interval_s,
            },
            "snapshots": len(self._snapshots),
            "throttle": throttle_status,
        }

    # ── Internal ───────────────────────────────────────────

    @staticmethod
    def _map_tier(tier_name: str) -> ResourceTier:
        """Map ElasticScaler tier names to ResourceTier enum."""
        mapping = {
            "minimal": ResourceTier.MINIMAL,
            "light": ResourceTier.LIGHT,
            "standard": ResourceTier.STANDARD,
            "professional": ResourceTier.PROFESSIONAL,
            "enterprise": ResourceTier.ENTERPRISE,
        }
        return mapping.get(tier_name.lower(), ResourceTier.STANDARD)
"""
Admission Control — load-based admission decisions for the autonomy runtime.

Decides whether to accept, defer, or reject incoming work based on
current resource state from the ResourceKernel.
"""


class AdmissionDecision(str, Enum):
    """Admission decision for incoming work."""
    ACCEPT = "accept"
    DEFER = "defer"
    REJECT = "reject"


@dataclass
class AdmissionResult:
    """Result of an admission check."""
    decision: AdmissionDecision
    reason: str = ""
    wait_ms: int = 0  # Suggested wait time if deferred
    priority_override: bool = False


class AdmissionControl:
    """
    Controls admission of work into the autonomy runtime.

    Uses ResourceKernel state to make accept/defer/reject decisions.
    """

    def __init__(
        self,
        kernel: Optional[ResourceKernel] = None,
        max_queue_depth: int = 100,
        defer_wait_ms: int = 1000,
    ):
        self._kernel = kernel or ResourceKernel()
        self._max_queue = max_queue_depth
        self._defer_wait = defer_wait_ms
        self._queue_depth = 0
        self._accepted = 0
        self._deferred = 0
        self._rejected = 0

    def check(
        self,
        work_type: str = "query",
        priority: int = 50,
    ) -> AdmissionResult:
        """
        Check if work should be admitted.

        Args:
            work_type: "query", "learning", "training", "maintenance"
            priority: 0-100 priority level

        Returns:
            AdmissionResult with decision
        """
        # High priority always accepted (emergency/protect goals)
        if priority >= 90:
            self._accepted += 1
            return AdmissionResult(
                decision=AdmissionDecision.ACCEPT,
                reason="High priority override",
                priority_override=True,
            )

        # Check queue depth
        if self._queue_depth >= self._max_queue:
            self._rejected += 1
            return AdmissionResult(
                decision=AdmissionDecision.REJECT,
                reason=f"Queue full ({self._queue_depth}/{self._max_queue})",
            )

        # Type-specific checks
        if work_type == "query":
            return self._check_query(priority)
        elif work_type == "learning":
            return self._check_learning(priority)
        elif work_type == "training":
            return self._check_training(priority)
        else:
            return self._check_maintenance(priority)

    def record_enqueue(self):
        """Record that work has been enqueued."""
        self._queue_depth += 1

    def record_dequeue(self):
        """Record that work has been dequeued."""
        self._queue_depth = max(0, self._queue_depth - 1)

    def stats(self) -> dict:
        return {
            "queue_depth": self._queue_depth,
            "max_queue": self._max_queue,
            "accepted": self._accepted,
            "deferred": self._deferred,
            "rejected": self._rejected,
        }

    # ── Internal checks ────────────────────────────────────

    def _check_query(self, priority: int) -> AdmissionResult:
        if self._kernel.can_accept_query():
            self._accepted += 1
            return AdmissionResult(
                decision=AdmissionDecision.ACCEPT,
                reason="Capacity available",
            )
        if self._kernel.should_shed_load():
            self._rejected += 1
            return AdmissionResult(
                decision=AdmissionDecision.REJECT,
                reason="System at critical load",
            )
        self._deferred += 1
        return AdmissionResult(
            decision=AdmissionDecision.DEFER,
            reason="At capacity, deferring",
            wait_ms=self._defer_wait,
        )

    def _check_learning(self, priority: int) -> AdmissionResult:
        if self._kernel.can_accept_learning():
            self._accepted += 1
            return AdmissionResult(
                decision=AdmissionDecision.ACCEPT,
                reason="Learning capacity available",
            )
        self._deferred += 1
        return AdmissionResult(
            decision=AdmissionDecision.DEFER,
            reason="System too busy for learning",
            wait_ms=self._defer_wait * 5,
        )

    def _check_training(self, priority: int) -> AdmissionResult:
        if self._kernel.can_train_specialist():
            self._accepted += 1
            return AdmissionResult(
                decision=AdmissionDecision.ACCEPT,
                reason="Training capacity available (night mode)",
            )
        if not self._kernel.night_mode:
            self._deferred += 1
            return AdmissionResult(
                decision=AdmissionDecision.DEFER,
                reason="Training deferred to night mode",
                wait_ms=self._defer_wait * 60,
            )
        self._deferred += 1
        return AdmissionResult(
            decision=AdmissionDecision.DEFER,
            reason="Training deferred (high load)",
            wait_ms=self._defer_wait * 10,
        )

    def _check_maintenance(self, priority: int) -> AdmissionResult:
        if self._kernel.should_defer():
            self._deferred += 1
            return AdmissionResult(
                decision=AdmissionDecision.DEFER,
                reason="Maintenance deferred (high load)",
                wait_ms=self._defer_wait * 2,
            )
        self._accepted += 1
        return AdmissionResult(
            decision=AdmissionDecision.ACCEPT,
            reason="Maintenance accepted",
        )
