"""Resource monitoring and OOM protection.

Monitors RAM, disk, and CPU usage. Triggers throttling or graceful
degradation before system runs out of resources.

Usage:
    guard = ResourceGuard(max_memory_percent=85)
    if guard.should_throttle():
        await asyncio.sleep(1)  # Back off
    if guard.is_critical():
        # Disable non-essential agents, flush caches
        guard.trigger_emergency_gc()
"""

import logging
import gc
from dataclasses import dataclass

try:
    import psutil
    _PSUTIL_AVAILABLE = True
except ImportError:
    _PSUTIL_AVAILABLE = False

log = logging.getLogger("waggledance.resource_guard")


@dataclass
class ResourceState:
    memory_percent: float = 0.0
    disk_percent: float = 0.0
    cpu_percent: float = 0.0
    is_throttled: bool = False
    is_critical: bool = False
    is_oom_risk: bool = False


class ResourceGuard:
    """Monitors system resources and triggers protective actions."""

    def __init__(self, max_memory_percent: float = 85.0,
                 critical_memory_percent: float = 93.0,
                 max_disk_percent: float = 90.0,
                 check_interval_s: float = 30.0):
        self._max_mem = max_memory_percent
        self._critical_mem = critical_memory_percent
        self._max_disk = max_disk_percent
        self._check_interval = check_interval_s
        self._gc_count = 0
        self._throttle_count = 0

    def check(self) -> ResourceState:
        """Check current resource usage."""
        if not _PSUTIL_AVAILABLE:
            return ResourceState()

        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        cpu = psutil.cpu_percent(interval=0.1)

        state = ResourceState(
            memory_percent=mem.percent,
            disk_percent=disk.percent,
            cpu_percent=cpu)

        if mem.percent >= self._critical_mem:
            state.is_critical = True
            state.is_oom_risk = True
            state.is_throttled = True
        elif mem.percent >= self._max_mem:
            state.is_throttled = True

        if disk.percent >= self._max_disk:
            state.is_critical = True

        return state

    def should_throttle(self) -> bool:
        """Quick check — should we slow down?"""
        if not _PSUTIL_AVAILABLE:
            return False
        mem = psutil.virtual_memory()
        throttle = mem.percent >= self._max_mem
        if throttle:
            self._throttle_count += 1
        return throttle

    def is_critical(self) -> bool:
        """Quick check — are we about to crash?"""
        if not _PSUTIL_AVAILABLE:
            return False
        mem = psutil.virtual_memory()
        return mem.percent >= self._critical_mem

    def trigger_emergency_gc(self) -> dict:
        """Force garbage collection and report freed memory."""
        if not _PSUTIL_AVAILABLE:
            gc.collect()
            return {"before": 0, "after": 0, "freed": 0}

        before = psutil.virtual_memory().percent
        gc.collect()
        after = psutil.virtual_memory().percent
        self._gc_count += 1
        freed = before - after
        if freed > 0:
            log.warning("Emergency GC freed %.1f%% memory (%.1f%% → %.1f%%)",
                        freed, before, after)
        return {"before": before, "after": after, "freed": freed}

    @property
    def stats(self) -> dict:
        state = self.check()
        return {
            "memory_percent": state.memory_percent,
            "disk_percent": state.disk_percent,
            "cpu_percent": state.cpu_percent,
            "is_throttled": state.is_throttled,
            "is_critical": state.is_critical,
            "gc_runs": self._gc_count,
            "throttle_events": self._throttle_count,
        }
