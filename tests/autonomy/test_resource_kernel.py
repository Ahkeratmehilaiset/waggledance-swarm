"""
Phase 8 Tests: Resource Kernel + Admission Control.

Tests cover:
- ResourceKernel lifecycle (start, stop, tier detection)
- ResourceKernel load tracking and level classification
- ResourceKernel night mode switching
- ResourceKernel admission decisions
- ResourceKernel snapshots
- AdmissionControl accept/defer/reject decisions
- AdmissionControl priority override
- AdmissionControl queue depth limits
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from waggledance.core.autonomy.resource_kernel import (
    AdmissionControl,
    AdmissionDecision,
    LoadLevel,
    ResourceKernel,
    ResourceLimits,
    ResourceTier,
)


# ── ResourceKernel ───────────────────────────────────────

class TestResourceKernel:
    def test_create_default(self):
        kernel = ResourceKernel()
        assert kernel.tier == ResourceTier.STANDARD
        assert kernel.load_level == LoadLevel.IDLE
        assert kernel.is_running is False

    def test_create_with_tier(self):
        kernel = ResourceKernel(tier="minimal")
        assert kernel.tier == ResourceTier.MINIMAL
        assert kernel.limits.max_concurrent_queries == 1

    def test_create_professional(self):
        kernel = ResourceKernel(tier="professional")
        assert kernel.tier == ResourceTier.PROFESSIONAL
        assert kernel.limits.max_concurrent_queries == 8

    def test_start_stop(self):
        kernel = ResourceKernel()
        kernel.start()
        assert kernel.is_running is True
        kernel.stop()
        assert kernel.is_running is False

    def test_load_level_idle(self):
        kernel = ResourceKernel(tier="standard")
        assert kernel.load_level == LoadLevel.IDLE

    def test_load_level_increases_with_tasks(self):
        kernel = ResourceKernel(tier="standard")  # max_concurrent = 4
        kernel.record_task_start()
        kernel.record_task_start()
        kernel.record_task_start()
        # 3/4 = 75% → HEAVY
        assert kernel.load_level == LoadLevel.HEAVY

    def test_load_level_critical(self):
        kernel = ResourceKernel(tier="standard")  # max_concurrent = 4
        for _ in range(4):
            kernel.record_task_start()
        assert kernel.load_level == LoadLevel.CRITICAL

    def test_load_decreases_on_task_end(self):
        kernel = ResourceKernel(tier="standard")
        for _ in range(4):
            kernel.record_task_start()
        assert kernel.load_level == LoadLevel.CRITICAL
        kernel.record_task_end(latency_ms=50.0)
        assert kernel.load_level == LoadLevel.HEAVY

    def test_can_accept_query(self):
        kernel = ResourceKernel(tier="standard")
        assert kernel.can_accept_query() is True
        for _ in range(4):
            kernel.record_task_start()
        assert kernel.can_accept_query() is False

    def test_can_accept_learning(self):
        kernel = ResourceKernel(tier="standard")
        assert kernel.can_accept_learning() is True
        for _ in range(3):
            kernel.record_task_start()
        # 3/4 = 75% → HEAVY → no learning
        assert kernel.can_accept_learning() is False

    def test_night_mode(self):
        kernel = ResourceKernel(tier="standard")
        assert kernel.night_mode is False
        kernel.set_night_mode(True)
        assert kernel.night_mode is True
        # Query capacity reduced
        assert kernel.limits.max_concurrent_queries < 4

    def test_night_mode_restore(self):
        kernel = ResourceKernel(tier="standard")
        kernel.set_night_mode(True)
        kernel.set_night_mode(False)
        assert kernel.limits.max_concurrent_queries == 4

    def test_can_train_specialist_requires_night(self):
        kernel = ResourceKernel(tier="standard")
        assert kernel.can_train_specialist() is False
        kernel.set_night_mode(True)
        assert kernel.can_train_specialist() is True

    def test_should_defer(self):
        kernel = ResourceKernel(tier="standard")
        assert kernel.should_defer() is False
        for _ in range(3):
            kernel.record_task_start()
        assert kernel.should_defer() is True

    def test_should_shed_load(self):
        kernel = ResourceKernel(tier="standard")
        assert kernel.should_shed_load() is False
        for _ in range(4):
            kernel.record_task_start()
        assert kernel.should_shed_load() is True

    def test_take_snapshot(self):
        kernel = ResourceKernel(tier="standard")
        kernel.record_task_start()
        snap = kernel.take_snapshot()
        assert snap.tier == ResourceTier.STANDARD
        assert snap.active_tasks == 1
        d = snap.to_dict()
        assert "tier" in d
        assert "load_level" in d

    def test_stats(self):
        kernel = ResourceKernel(tier="standard")
        kernel.start()
        s = kernel.stats()
        assert s["tier"] == "standard"
        assert "limits" in s
        assert "load_level" in s

    def test_map_tier(self):
        assert ResourceKernel._map_tier("minimal") == ResourceTier.MINIMAL
        assert ResourceKernel._map_tier("enterprise") == ResourceTier.ENTERPRISE
        assert ResourceKernel._map_tier("unknown") == ResourceTier.STANDARD


# ── AdmissionControl ─────────────────────────────────────

class TestAdmissionControl:
    def test_accept_query_idle(self):
        kernel = ResourceKernel(tier="standard")
        ac = AdmissionControl(kernel=kernel)
        result = ac.check(work_type="query")
        assert result.decision == AdmissionDecision.ACCEPT

    def test_defer_query_at_capacity(self):
        kernel = ResourceKernel(tier="standard")
        for _ in range(3):
            kernel.record_task_start()
        ac = AdmissionControl(kernel=kernel)
        # 3/4 = at capacity but not shedding
        result = ac.check(work_type="query")
        # Still can accept since active < max
        assert result.decision in (AdmissionDecision.ACCEPT, AdmissionDecision.DEFER)

    def test_reject_query_critical(self):
        kernel = ResourceKernel(tier="standard")
        for _ in range(4):
            kernel.record_task_start()
        ac = AdmissionControl(kernel=kernel)
        result = ac.check(work_type="query")
        assert result.decision == AdmissionDecision.REJECT

    def test_high_priority_override(self):
        kernel = ResourceKernel(tier="standard")
        for _ in range(4):
            kernel.record_task_start()
        ac = AdmissionControl(kernel=kernel)
        result = ac.check(work_type="query", priority=95)
        assert result.decision == AdmissionDecision.ACCEPT
        assert result.priority_override is True

    def test_learning_deferred_under_load(self):
        kernel = ResourceKernel(tier="standard")
        for _ in range(3):
            kernel.record_task_start()
        ac = AdmissionControl(kernel=kernel)
        result = ac.check(work_type="learning")
        assert result.decision == AdmissionDecision.DEFER

    def test_training_needs_night_mode(self):
        kernel = ResourceKernel(tier="standard")
        ac = AdmissionControl(kernel=kernel)
        result = ac.check(work_type="training")
        assert result.decision == AdmissionDecision.DEFER
        assert "night" in result.reason.lower()

    def test_training_accepted_in_night_mode(self):
        kernel = ResourceKernel(tier="standard")
        kernel.set_night_mode(True)
        ac = AdmissionControl(kernel=kernel)
        result = ac.check(work_type="training")
        assert result.decision == AdmissionDecision.ACCEPT

    def test_maintenance_accepted_idle(self):
        kernel = ResourceKernel(tier="standard")
        ac = AdmissionControl(kernel=kernel)
        result = ac.check(work_type="maintenance")
        assert result.decision == AdmissionDecision.ACCEPT

    def test_maintenance_deferred_heavy(self):
        kernel = ResourceKernel(tier="standard")
        for _ in range(3):
            kernel.record_task_start()
        ac = AdmissionControl(kernel=kernel)
        result = ac.check(work_type="maintenance")
        assert result.decision == AdmissionDecision.DEFER

    def test_queue_full_rejects(self):
        kernel = ResourceKernel(tier="standard")
        ac = AdmissionControl(kernel=kernel, max_queue_depth=2)
        ac._queue_depth = 2
        result = ac.check(work_type="query")
        assert result.decision == AdmissionDecision.REJECT
        assert "full" in result.reason.lower()

    def test_enqueue_dequeue(self):
        ac = AdmissionControl()
        ac.record_enqueue()
        assert ac._queue_depth == 1
        ac.record_dequeue()
        assert ac._queue_depth == 0

    def test_stats(self):
        kernel = ResourceKernel(tier="standard")
        ac = AdmissionControl(kernel=kernel)
        ac.check(work_type="query")
        s = ac.stats()
        assert s["accepted"] == 1
        assert "queue_depth" in s
