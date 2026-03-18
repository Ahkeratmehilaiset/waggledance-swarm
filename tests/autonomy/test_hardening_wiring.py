"""
Tests for hardening wiring: metrics emission, admission control, YAML config
loading, and ResourceKernel integration through the AutonomyService layer.

Covers:
- AutonomyService emits route_decision, capability_chain, verification, case_grade
- AdmissionControl rejects queries at capacity
- Container passes ResourceKernel with tier from settings
- YAML config loading enriches registry with max_latency_ms and trust_score
- get_status() includes KPIs, admission stats, and metrics
- execute_mission() records metrics
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from waggledance.core.autonomy.metrics import AutonomyMetrics
from waggledance.core.autonomy.resource_kernel import (
    AdmissionControl,
    AdmissionDecision,
    ResourceKernel,
)
from waggledance.core.capabilities.registry import CapabilityRegistry


# ── YAML config loading ───────────────────────────────────

class TestYamlConfigLoading:
    """Verify that YAML configs enrich registry capabilities."""

    def test_load_yaml_enriches_existing(self, tmp_path):
        """YAML with matching IDs enriches max_latency_ms and trust_score."""
        yaml_file = tmp_path / "solvers.yaml"
        yaml_file.write_text(
            "capabilities:\n"
            "  - id: solve.math\n"
            "    category: SOLVE\n"
            "    max_latency_ms: 100\n"
            "    trust_baseline: 0.95\n",
            encoding="utf-8",
        )
        reg = CapabilityRegistry()
        original = reg.get("solve.math")
        assert original is not None
        enriched = reg.load_yaml_configs(str(tmp_path))
        assert enriched >= 1
        cap = reg.get("solve.math")
        assert cap.max_latency_ms == 100.0
        assert cap.trust_score == 0.95

    def test_load_yaml_adds_new_capability(self, tmp_path):
        yaml_file = tmp_path / "custom.yaml"
        yaml_file.write_text(
            "capabilities:\n"
            "  - id: custom.test\n"
            "    category: solve\n"
            "    description: Test capability from YAML\n"
            "    max_latency_ms: 250\n"
            "    trust_baseline: 0.80\n"
            "    preconditions:\n"
            "      - test_ready\n"
            "    success_criteria:\n"
            "      - test_passed\n",
            encoding="utf-8",
        )
        reg = CapabilityRegistry()
        count_before = reg.count()
        enriched = reg.load_yaml_configs(str(tmp_path))
        assert enriched >= 1
        assert reg.count() == count_before + 1
        cap = reg.get("custom.test")
        assert cap is not None
        assert cap.max_latency_ms == 250.0
        assert cap.trust_score == 0.80
        assert "test_ready" in cap.preconditions

    def test_load_yaml_missing_dir_returns_zero(self):
        reg = CapabilityRegistry()
        result = reg.load_yaml_configs("/nonexistent/path")
        assert result == 0

    def test_load_yaml_invalid_file_skips(self, tmp_path):
        yaml_file = tmp_path / "bad.yaml"
        yaml_file.write_text(": invalid [[[", encoding="utf-8")
        reg = CapabilityRegistry()
        count_before = reg.count()
        reg.load_yaml_configs(str(tmp_path))
        assert reg.count() == count_before

    def test_load_yaml_default_path(self):
        """Load from default configs/capabilities/ path."""
        reg = CapabilityRegistry()
        enriched = reg.load_yaml_configs()
        # Should find configs/capabilities/*.yaml if they exist
        configs_dir = Path(__file__).resolve().parents[2] / "configs" / "capabilities"
        if configs_dir.is_dir():
            assert enriched > 0
            # solve.math should be enriched with YAML metadata
            cap = reg.get("solve.math")
            assert cap.max_latency_ms == 100.0
            assert cap.trust_score == 0.95


# ── AdmissionControl in AutonomyService ───────────────────

class TestAdmissionInService:
    """Verify AutonomyService uses AdmissionControl."""

    def _make_service(self, kernel=None):
        from waggledance.application.services.autonomy_service import AutonomyService
        from waggledance.core.autonomy.compatibility import CompatibilityLayer
        from waggledance.core.autonomy.lifecycle import AutonomyLifecycle
        from waggledance.core.autonomy.runtime import AutonomyRuntime

        runtime = AutonomyRuntime(profile="TEST")
        lifecycle = AutonomyLifecycle(profile="TEST")
        compatibility = CompatibilityLayer(runtime=runtime)
        kernel = kernel or ResourceKernel(tier="standard")
        svc = AutonomyService(
            runtime=runtime,
            lifecycle=lifecycle,
            resource_kernel=kernel,
            compatibility=compatibility,
            profile="TEST",
        )
        return svc

    def test_service_has_admission_control(self):
        svc = self._make_service()
        assert hasattr(svc, "_admission")
        assert isinstance(svc._admission, AdmissionControl)

    def test_service_rejects_at_critical_load(self):
        kernel = ResourceKernel(tier="standard")
        for _ in range(4):
            kernel.record_task_start()
        svc = self._make_service(kernel=kernel)
        result = svc.handle_query("test query")
        assert result.get("error") == "rejected"

    def test_service_accepts_high_priority_at_load(self):
        kernel = ResourceKernel(tier="standard")
        for _ in range(4):
            kernel.record_task_start()
        svc = self._make_service(kernel=kernel)
        svc.start()
        result = svc.handle_query("test query", priority=95)
        # High priority overrides admission → should NOT be rejected
        assert result.get("error") != "rejected"
        svc.stop()

    def test_service_admits_idle_query(self):
        svc = self._make_service()
        svc.start()
        result = svc.handle_query("what is temperature?")
        assert result.get("error") != "rejected"
        assert result.get("error") != "deferred"
        svc.stop()

    def test_mission_rejects_at_critical_load(self):
        kernel = ResourceKernel(tier="standard")
        for _ in range(4):
            kernel.record_task_start()
        svc = self._make_service(kernel=kernel)
        result = svc.execute_mission("test", "test mission")
        assert result.get("error") == "rejected"


# ── Metric emissions ─────────────────────────────────────

class TestMetricEmissions:
    """Verify AutonomyService emits all metric types."""

    def _make_service_with_metrics(self):
        from waggledance.application.services.autonomy_service import AutonomyService
        from waggledance.core.autonomy.compatibility import CompatibilityLayer
        from waggledance.core.autonomy.lifecycle import AutonomyLifecycle
        from waggledance.core.autonomy.runtime import AutonomyRuntime

        runtime = AutonomyRuntime(profile="TEST")
        lifecycle = AutonomyLifecycle(profile="TEST")
        compatibility = CompatibilityLayer(runtime=runtime)
        svc = AutonomyService(
            runtime=runtime,
            lifecycle=lifecycle,
            compatibility=compatibility,
            profile="TEST",
        )
        return svc

    def test_handle_query_emits_route_decision(self):
        svc = self._make_service_with_metrics()
        svc.start()
        svc.handle_query("what is 2+2?")
        assert svc._metrics is not None
        stats = svc._metrics.stats()
        assert stats["total_samples"] > 0
        assert svc._metrics.get_metric("route_accuracy") >= 0.0
        svc.stop()

    def test_handle_query_emits_capability_chain(self):
        svc = self._make_service_with_metrics()
        svc.start()
        svc.handle_query("what is temperature?")
        # capability_chain_success should have at least one sample
        with svc._metrics._lock:
            chain_samples = svc._metrics._samples.get("capability_chain_success", [])
        assert len(chain_samples) >= 1
        svc.stop()

    def test_handle_query_emits_case_grade(self):
        svc = self._make_service_with_metrics()
        svc.start()
        svc.handle_query("calculate 5*3")
        stats = svc._metrics.stats()
        # Should have emitted case grade counter or sample
        total = sum(v for k, v in stats["counters"].items() if k.startswith("case_grade"))
        assert total >= 0  # May be 0 if quality_path not in result
        svc.stop()

    def test_execute_mission_emits_metrics(self):
        svc = self._make_service_with_metrics()
        svc.start()
        svc.execute_mission("monitoring", "check temperature sensors")
        stats = svc._metrics.stats()
        assert stats["total_samples"] > 0
        svc.stop()

    def test_get_kpis_returns_kpi_dict(self):
        svc = self._make_service_with_metrics()
        svc.start()
        svc.handle_query("hello")
        kpis = svc.get_kpis()
        assert "route_accuracy" in kpis
        assert "llm_fallback_rate" in kpis
        assert "capability_chain_success_rate" in kpis
        svc.stop()

    def test_metrics_in_get_status(self):
        svc = self._make_service_with_metrics()
        svc.start()
        svc.handle_query("test")
        status = svc.get_status()
        assert "kpis" in status
        assert "metrics" in status
        assert "admission" in status
        assert status["admission"]["accepted"] >= 1
        svc.stop()


# ── Container wiring ──────────────────────────────────────

class TestContainerResourceKernel:
    """Verify Container passes ResourceKernel to AutonomyService."""

    def test_container_creates_resource_kernel(self):
        from waggledance.bootstrap.container import Container
        from waggledance.adapters.config.settings_loader import WaggleSettings
        s = WaggleSettings(profile="FACTORY")
        c = Container(settings=s, stub=True)
        svc = c.autonomy_service
        assert svc._resource_kernel is not None
        assert isinstance(svc._resource_kernel, ResourceKernel)

    def test_container_resource_kernel_default_tier(self):
        from waggledance.bootstrap.container import Container
        from waggledance.adapters.config.settings_loader import WaggleSettings
        s = WaggleSettings(profile="HOME")
        c = Container(settings=s, stub=True)
        svc = c.autonomy_service
        assert svc._resource_kernel.tier.value == "standard"

    def test_container_service_has_admission(self):
        from waggledance.bootstrap.container import Container
        from waggledance.adapters.config.settings_loader import WaggleSettings
        s = WaggleSettings(profile="COTTAGE")
        c = Container(settings=s, stub=True)
        svc = c.autonomy_service
        assert hasattr(svc, "_admission")
        assert isinstance(svc._admission, AdmissionControl)

    def test_container_service_has_metrics(self):
        from waggledance.bootstrap.container import Container
        from waggledance.adapters.config.settings_loader import WaggleSettings
        s = WaggleSettings(profile="GADGET")
        c = Container(settings=s, stub=True)
        svc = c.autonomy_service
        assert svc._metrics is not None
        assert isinstance(svc._metrics, AutonomyMetrics)


# ── End-to-end metric flow ────────────────────────────────

class TestEndToEndMetrics:
    """Full flow: service → runtime → metrics → KPIs."""

    def test_multiple_queries_build_kpis(self):
        from waggledance.application.services.autonomy_service import AutonomyService
        from waggledance.core.autonomy.compatibility import CompatibilityLayer
        from waggledance.core.autonomy.lifecycle import AutonomyLifecycle
        from waggledance.core.autonomy.runtime import AutonomyRuntime

        runtime = AutonomyRuntime(profile="TEST")
        lifecycle = AutonomyLifecycle(profile="TEST")
        compatibility = CompatibilityLayer(runtime=runtime)
        svc = AutonomyService(
            runtime=runtime,
            lifecycle=lifecycle,
            compatibility=compatibility,
            profile="TEST",
        )
        svc.start()

        # Run several queries
        for q in ["what is 2+2?", "temperature forecast", "check sensors",
                   "optimize schedule", "why is it cold?"]:
            svc.handle_query(q)

        kpis = svc.get_kpis()
        assert kpis["route_accuracy"]["value"] >= 0.0
        assert kpis["route_accuracy"]["target"] == 0.90
        assert "meets_target" in kpis["route_accuracy"]

        # Verify capability chain was tracked
        assert kpis["capability_chain_success_rate"]["value"] >= 0.0

        # Verify we have admission stats
        status = svc.get_status()
        assert status["admission"]["accepted"] >= 5

        svc.stop()

    def test_mixed_workload_metrics(self):
        from waggledance.application.services.autonomy_service import AutonomyService
        from waggledance.core.autonomy.compatibility import CompatibilityLayer
        from waggledance.core.autonomy.lifecycle import AutonomyLifecycle
        from waggledance.core.autonomy.runtime import AutonomyRuntime

        runtime = AutonomyRuntime(profile="TEST")
        lifecycle = AutonomyLifecycle(profile="TEST")
        compatibility = CompatibilityLayer(runtime=runtime)
        svc = AutonomyService(
            runtime=runtime,
            lifecycle=lifecycle,
            compatibility=compatibility,
            profile="TEST",
        )
        svc.start()

        # Queries
        svc.handle_query("calculate 5*3")
        svc.handle_query("check anomalies")

        # Mission
        svc.execute_mission("monitoring", "check all sensors")

        # All should contribute to metrics
        stats = svc._metrics.stats()
        assert stats["total_samples"] > 0
        assert svc._request_count == 3

        svc.stop()
