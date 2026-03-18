"""
Tests for ResourceKernel wiring into AutonomyRuntime.
"""

from __future__ import annotations

import pytest

from waggledance.core.autonomy.resource_kernel import (
    AdmissionControl,
    AdmissionDecision,
    LoadLevel,
    ResourceKernel,
    ResourceTier,
)


class TestResourceKernelInRuntime:
    def test_runtime_has_resource_kernel(self):
        from waggledance.core.autonomy.runtime import AutonomyRuntime

        rt = AutonomyRuntime(profile="VALIDATION")
        assert rt.resource_kernel is not None
        assert isinstance(rt.resource_kernel, ResourceKernel)

    def test_runtime_has_admission_control(self):
        from waggledance.core.autonomy.runtime import AutonomyRuntime

        rt = AutonomyRuntime(profile="VALIDATION")
        assert rt.admission_control is not None
        assert isinstance(rt.admission_control, AdmissionControl)

    def test_start_starts_kernel(self):
        from waggledance.core.autonomy.runtime import AutonomyRuntime

        rt = AutonomyRuntime(profile="VALIDATION")
        rt.start()
        assert rt.resource_kernel.is_running
        rt.stop()

    def test_stop_stops_kernel(self):
        from waggledance.core.autonomy.runtime import AutonomyRuntime

        rt = AutonomyRuntime(profile="VALIDATION")
        rt.start()
        rt.stop()
        assert not rt.resource_kernel.is_running

    def test_query_tracks_task_start_end(self):
        from waggledance.core.autonomy.runtime import AutonomyRuntime

        rt = AutonomyRuntime(profile="VALIDATION")
        rt.start()

        assert rt.resource_kernel._active_tasks == 0
        rt.handle_query("2+2")
        # After handle_query completes, task should be ended
        assert rt.resource_kernel._active_tasks == 0

        rt.stop()

    def test_stats_include_resource_kernel(self):
        from waggledance.core.autonomy.runtime import AutonomyRuntime

        rt = AutonomyRuntime(profile="VALIDATION")
        s = rt.stats()
        assert "resource_kernel" in s
        assert s["resource_kernel"]["tier"] == "standard"

    def test_stats_include_admission_control(self):
        from waggledance.core.autonomy.runtime import AutonomyRuntime

        rt = AutonomyRuntime(profile="VALIDATION")
        s = rt.stats()
        assert "admission_control" in s
        assert "accepted" in s["admission_control"]


class TestAdmissionGating:
    def test_query_accepted_under_normal_load(self):
        kernel = ResourceKernel()
        ac = AdmissionControl(kernel=kernel)
        result = ac.check(work_type="query")
        assert result.decision == AdmissionDecision.ACCEPT

    def test_query_rejected_at_critical_load(self):
        kernel = ResourceKernel()
        kernel.start()
        # Saturate: fill all query slots
        for _ in range(kernel.limits.max_concurrent_queries):
            kernel.record_task_start()
        assert kernel.load_level == LoadLevel.CRITICAL

        ac = AdmissionControl(kernel=kernel)
        result = ac.check(work_type="query")
        # Should defer or reject, not accept
        assert result.decision in (AdmissionDecision.DEFER, AdmissionDecision.REJECT)

    def test_high_priority_always_accepted(self):
        kernel = ResourceKernel()
        # Saturate
        for _ in range(kernel.limits.max_concurrent_queries):
            kernel.record_task_start()

        ac = AdmissionControl(kernel=kernel)
        result = ac.check(work_type="query", priority=95)
        assert result.decision == AdmissionDecision.ACCEPT
        assert result.priority_override

    def test_learning_deferred_under_load(self):
        kernel = ResourceKernel()
        # Push to moderate load
        for _ in range(kernel.limits.max_concurrent_queries // 2 + 1):
            kernel.record_task_start()

        ac = AdmissionControl(kernel=kernel)
        result = ac.check(work_type="learning")
        assert result.decision == AdmissionDecision.DEFER

    def test_training_deferred_without_night_mode(self):
        kernel = ResourceKernel()
        ac = AdmissionControl(kernel=kernel)
        result = ac.check(work_type="training")
        assert result.decision == AdmissionDecision.DEFER
        assert "night" in result.reason.lower()

    def test_training_accepted_in_night_mode_idle(self):
        kernel = ResourceKernel()
        kernel.set_night_mode(True)
        ac = AdmissionControl(kernel=kernel)
        result = ac.check(work_type="training")
        assert result.decision == AdmissionDecision.ACCEPT

    def test_queue_full_rejects(self):
        kernel = ResourceKernel()
        ac = AdmissionControl(kernel=kernel, max_queue_depth=2)
        ac.record_enqueue()
        ac.record_enqueue()
        result = ac.check(work_type="query")
        assert result.decision == AdmissionDecision.REJECT
        assert "Queue full" in result.reason


class TestResourceKernelNightMode:
    def test_night_mode_reduces_query_capacity(self):
        kernel = ResourceKernel(tier="standard")
        original = kernel.limits.max_concurrent_queries
        kernel.set_night_mode(True)
        assert kernel.limits.max_concurrent_queries < original

    def test_night_mode_increases_learning_capacity(self):
        kernel = ResourceKernel(tier="standard")
        original = kernel.limits.max_concurrent_learning
        kernel.set_night_mode(True)
        assert kernel.limits.max_concurrent_learning >= original

    def test_disable_night_mode_restores_limits(self):
        kernel = ResourceKernel(tier="standard")
        original_queries = kernel.limits.max_concurrent_queries
        kernel.set_night_mode(True)
        kernel.set_night_mode(False)
        assert kernel.limits.max_concurrent_queries == original_queries


class TestResourceKernelSnapshot:
    def test_take_snapshot_returns_data(self):
        kernel = ResourceKernel()
        snap = kernel.take_snapshot()
        assert snap.tier == ResourceTier.STANDARD
        assert snap.load_level == LoadLevel.IDLE

    def test_snapshot_to_dict(self):
        kernel = ResourceKernel()
        d = kernel.take_snapshot().to_dict()
        assert "tier" in d
        assert "load_level" in d
        assert d["tier"] == "standard"
