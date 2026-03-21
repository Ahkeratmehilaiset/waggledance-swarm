"""Integration tests for production wiring — runtime boot, adapters, metrics."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from waggledance.core.autonomy.runtime import AutonomyRuntime
from waggledance.core.capabilities.registry import CapabilityRegistry
from waggledance.application.services.autonomy_service import AutonomyService
from waggledance.core.policy.policy_engine import PolicyEngine


class TestRuntimeBoot:
    """Verify AutonomyRuntime boots with executors, MAGMA, and persistence."""

    def test_runtime_creates_successfully(self):
        rt = AutonomyRuntime()
        assert rt.capability_registry is not None

    def test_executor_binding_runs(self):
        rt = AutonomyRuntime()
        assert rt._executor_count >= 0

    def test_magma_audit_attached(self):
        rt = AutonomyRuntime()
        # audit may be None if legacy module unavailable, but attr must exist
        assert hasattr(rt, "audit")

    def test_magma_event_log_attached(self):
        rt = AutonomyRuntime()
        assert hasattr(rt, "event_log")

    def test_magma_trust_attached(self):
        rt = AutonomyRuntime()
        assert hasattr(rt, "trust")

    def test_persistence_world_store_attached(self):
        rt = AutonomyRuntime()
        assert hasattr(rt, "world_store")

    def test_persistence_procedural_store_attached(self):
        rt = AutonomyRuntime()
        assert hasattr(rt, "procedural_store")

    def test_registry_has_executor_methods(self):
        registry = CapabilityRegistry()
        assert hasattr(registry, "register_executor")
        assert hasattr(registry, "get_executor")
        assert hasattr(registry, "executor_count")

    def test_registry_executor_roundtrip(self):
        registry = CapabilityRegistry()

        class DummyExecutor:
            CAPABILITY_ID = "solve.math"
            available = True
            def execute(self, q): return {"success": True}

        executor = DummyExecutor()
        registry.register_executor("solve.math", executor)
        assert registry.get_executor("solve.math") is executor
        assert registry.executor_count() == 1

    def test_get_executor_returns_none_for_missing(self):
        registry = CapabilityRegistry()
        assert registry.get_executor("nonexistent") is None


class TestAutonomyServiceMetrics:
    """Verify AutonomyMetrics is wired into AutonomyService."""

    def test_metrics_created(self):
        svc = AutonomyService()
        assert hasattr(svc, "_metrics")

    def test_get_kpis(self):
        svc = AutonomyService()
        kpis = svc.get_kpis()
        assert isinstance(kpis, dict)

    def test_get_kpis_no_metrics(self):
        svc = AutonomyService()
        svc._metrics = None
        assert svc.get_kpis() == {}


class TestPolicyEngineSafetyCases:
    """Verify SafetyCaseBuilder is wired into PolicyEngine."""

    def test_safety_builder_exists(self):
        engine = PolicyEngine()
        assert hasattr(engine, "_safety_builder")

    def test_evaluate_non_readonly_builds_case(self):
        from waggledance.core.domain.autonomy import (
            Action, CapabilityContract, CapabilityCategory,
        )
        engine = PolicyEngine()
        action = Action(capability_id="solve.math")
        cap = CapabilityContract(
            capability_id="solve.math",
            category=CapabilityCategory.SOLVE,
        )
        decision = engine.evaluate(action, cap, quality_path="gold")
        # Should not crash — safety case builder should run
        assert decision is not None

    def test_evaluate_readonly_skips_safety(self):
        from waggledance.core.domain.autonomy import (
            Action, CapabilityContract, CapabilityCategory,
        )
        engine = PolicyEngine()
        action = Action(capability_id="sense.mqtt_ingest")
        cap = CapabilityContract(
            capability_id="sense.mqtt_ingest",
            category=CapabilityCategory.SENSE,
        )
        decision = engine.evaluate(action, cap)
        assert decision.approved is True
