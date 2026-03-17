"""
Regression gate: Resource Kernel tests.

Validates ResourceKernel lifecycle, load levels, tier management,
admission control, and night mode switching.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from waggledance.core.autonomy.resource_kernel import (
    ResourceKernel, AdmissionControl, AdmissionDecision,
    LoadLevel, ResourceTier,
)


class TestResourceKernelLifecycle:
    def test_start_stop(self):
        rk = ResourceKernel()
        rk.start()
        assert rk.is_running is True
        rk.stop()
        assert rk.is_running is False

    def test_default_tier(self):
        rk = ResourceKernel()
        assert rk.tier == ResourceTier.STANDARD

    def test_custom_tier(self):
        rk = ResourceKernel(tier="minimal")
        assert rk.tier == ResourceTier.MINIMAL

    def test_initial_load_idle(self):
        rk = ResourceKernel()
        rk.start()
        assert rk.load_level == LoadLevel.IDLE


class TestResourceKernelLoad:
    def test_task_tracking(self):
        rk = ResourceKernel()
        rk.start()
        rk.record_task_start()
        rk.record_task_end(latency_ms=50.0)
        snap = rk.take_snapshot()
        assert snap.active_tasks == 0  # ended

    def test_snapshot(self):
        rk = ResourceKernel()
        rk.start()
        snap = rk.take_snapshot()
        assert snap.load_level == LoadLevel.IDLE


class TestResourceKernelNightMode:
    def test_night_mode_switch(self):
        rk = ResourceKernel()
        rk.start()
        rk.set_night_mode(True)
        assert rk.night_mode is True
        rk.set_night_mode(False)
        assert rk.night_mode is False


class TestAdmissionControl:
    def test_accept_idle(self):
        rk = ResourceKernel()
        rk.start()
        ac = AdmissionControl(kernel=rk)
        decision = ac.check(work_type="query", priority=50)
        assert decision.decision == AdmissionDecision.ACCEPT

    def test_priority_override(self):
        rk = ResourceKernel()
        rk.start()
        ac = AdmissionControl(kernel=rk)
        decision = ac.check(work_type="query", priority=100)
        assert decision.decision == AdmissionDecision.ACCEPT

    def test_stats(self):
        rk = ResourceKernel()
        rk.start()
        ac = AdmissionControl(kernel=rk)
        ac.check(work_type="query", priority=50)
        s = ac.stats()
        assert s["accepted"] >= 1
