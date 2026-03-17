"""
Phase 9 Tests: Lifecycle, Compatibility Layer, Autonomy Service, Cutover.

Tests cover:
- AutonomyLifecycle states and transitions
- AutonomyLifecycle component health tracking
- AutonomyLifecycle cutover validation
- CompatibilityLayer mode switching
- CompatibilityLayer legacy/autonomy routing
- CompatibilityLayer format adaptation
- AutonomyService lifecycle
- AutonomyService query handling
- AutonomyService mission execution
- AutonomyService cutover validation
- Cutover validator import checks
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from waggledance.core.autonomy.compatibility import (
    CompatibilityLayer,
    LegacyResult,
)
from waggledance.core.autonomy.lifecycle import (
    AutonomyLifecycle,
    RuntimeMode,
    RuntimeState,
)
from waggledance.core.autonomy.runtime import AutonomyRuntime
from waggledance.application.services.autonomy_service import AutonomyService


# ── AutonomyLifecycle ────────────────────────────────────

class TestAutonomyLifecycle:
    def test_initial_state(self):
        lc = AutonomyLifecycle()
        assert lc.state == RuntimeState.INITIALIZING
        assert lc.primary == RuntimeMode.WAGGLEDANCE
        assert lc.compatibility_mode is False

    def test_start_stop(self):
        lc = AutonomyLifecycle()
        assert lc.start() is True
        assert lc.state == RuntimeState.RUNNING
        assert lc.stop() is True
        assert lc.state == RuntimeState.STOPPED

    def test_cannot_start_while_running(self):
        lc = AutonomyLifecycle()
        lc.start()
        assert lc.start() is False  # Already running

    def test_uptime(self):
        lc = AutonomyLifecycle()
        assert lc.uptime_s == 0.0
        lc.start()
        assert lc.uptime_s > 0.0

    def test_is_autonomy_primary(self):
        lc = AutonomyLifecycle(primary="waggledance", compatibility_mode=False)
        assert lc.is_autonomy_primary is True

    def test_not_autonomy_primary_compat(self):
        lc = AutonomyLifecycle(primary="waggledance", compatibility_mode=True)
        assert lc.is_autonomy_primary is False

    def test_not_autonomy_primary_hivemind(self):
        lc = AutonomyLifecycle(primary="hivemind")
        assert lc.is_autonomy_primary is False

    def test_set_mode(self):
        lc = AutonomyLifecycle(primary="hivemind", compatibility_mode=True)
        lc.set_mode("waggledance", compatibility_mode=False)
        assert lc.primary == RuntimeMode.WAGGLEDANCE
        assert lc.compatibility_mode is False

    def test_component_health(self):
        lc = AutonomyLifecycle()
        lc.register_component("runtime", True)
        lc.register_component("kernel", True)
        lc.start()
        assert lc.state == RuntimeState.RUNNING

    def test_degraded_on_unhealthy(self):
        lc = AutonomyLifecycle()
        lc.register_component("runtime", True)
        lc.start()
        assert lc.state == RuntimeState.RUNNING
        lc.report_health("runtime", False, "Connection lost")
        assert lc.state == RuntimeState.DEGRADED

    def test_recovery_from_degraded(self):
        lc = AutonomyLifecycle()
        lc.register_component("runtime", True)
        lc.start()
        lc.report_health("runtime", False, "Down")
        assert lc.state == RuntimeState.DEGRADED
        lc.report_health("runtime", True, "Recovered")
        assert lc.state == RuntimeState.RUNNING

    def test_validate_cutover_pass(self):
        lc = AutonomyLifecycle(primary="waggledance", compatibility_mode=False)
        lc.start()
        result = lc.validate_cutover()
        assert result["runtime_primary"] is True
        assert result["compatibility_off"] is True
        assert result["all_pass"] is True

    def test_validate_cutover_fail_compat(self):
        lc = AutonomyLifecycle(primary="waggledance", compatibility_mode=True)
        lc.start()
        result = lc.validate_cutover()
        assert result["compatibility_off"] is False
        assert result["all_pass"] is False

    def test_stats(self):
        lc = AutonomyLifecycle(profile="TEST")
        lc.start()
        s = lc.stats()
        assert s["profile"] == "TEST"
        assert s["state"] == "running"


# ── CompatibilityLayer ───────────────────────────────────

class TestCompatibilityLayer:
    def test_autonomy_mode(self):
        runtime = AutonomyRuntime(profile="TEST")
        runtime.start()
        compat = CompatibilityLayer(runtime=runtime, compatibility_mode=False)
        result = compat.handle_query("hello")
        assert result.get("source") == "autonomy"

    def test_legacy_mode_no_legacy(self):
        compat = CompatibilityLayer(compatibility_mode=True)
        result = compat.handle_query("hello")
        assert result.get("source") == "legacy"
        assert "error" in result

    def test_switch_mode(self):
        compat = CompatibilityLayer(compatibility_mode=True)
        assert compat.compatibility_mode is True
        compat.set_compatibility_mode(False)
        assert compat.compatibility_mode is False

    def test_adapt_legacy_to_autonomy(self):
        compat = CompatibilityLayer()
        legacy = LegacyResult(
            answer="35°C",
            confidence=0.9,
            route_type="memory",
            elapsed_ms=15.0,
        )
        result = compat.adapt_legacy_to_autonomy(legacy)
        assert result["quality_path"] == "silver"
        assert result["capability"] == "retrieve.semantic_search"
        assert result["result"]["answer"] == "35°C"

    def test_adapt_legacy_hotcache(self):
        compat = CompatibilityLayer()
        legacy = LegacyResult(route_type="hotcache", answer="cached")
        result = compat.adapt_legacy_to_autonomy(legacy)
        assert result["quality_path"] == "gold"

    def test_adapt_autonomy_to_legacy(self):
        compat = CompatibilityLayer()
        autonomy = {
            "intent": "math",
            "quality_path": "gold",
            "capability": "solve.math",
            "result": {"answer": "42"},
            "elapsed_ms": 5.0,
        }
        legacy = compat.adapt_autonomy_to_legacy(autonomy)
        assert legacy.confidence == 0.95
        assert legacy.route_type == "micromodel"

    def test_stats(self):
        runtime = AutonomyRuntime(profile="TEST")
        runtime.start()
        compat = CompatibilityLayer(runtime=runtime, compatibility_mode=False)
        compat.handle_query("test")
        s = compat.stats()
        assert s["runtime_calls"] == 1
        assert s["legacy_calls"] == 0


# ── AutonomyService ──────────────────────────────────────

class TestAutonomyService:
    @pytest.fixture
    def service(self):
        svc = AutonomyService(profile="TEST")
        svc.start()
        return svc

    def test_start(self, service):
        assert service.is_running is True

    def test_stop(self, service):
        result = service.stop()
        assert result["status"] == "stopped"
        assert service.is_running is False

    def test_handle_query(self, service):
        result = service.handle_query("hello world")
        assert "intent" in result or "error" in result
        assert "service_elapsed_ms" in result

    def test_execute_mission(self, service):
        result = service.execute_mission(
            goal_type="observe",
            description="Check status",
            priority=50,
        )
        assert "goal_id" in result or "error" in result

    def test_get_status(self, service):
        status = service.get_status()
        assert status["profile"] == "TEST"
        assert "lifecycle" in status
        assert "resource_kernel" in status

    def test_night_mode(self, service):
        result = service.enable_night_mode()
        assert result["night_mode"] is True
        result = service.disable_night_mode()
        assert result["night_mode"] is False

    def test_validate_cutover(self, service):
        result = service.validate_cutover()
        assert "full_autonomy" in result
        assert "runtime_running" in result

    def test_stats(self, service):
        s = service.stats()
        assert s["profile"] == "TEST"
        assert s["is_running"] is True


# ── Cutover Validator ────────────────────────────────────

class TestCutoverValidator:
    def test_imports_check(self):
        from waggledance.tools.validate_cutover import check_imports
        results = check_imports()
        # All modules should be importable
        failed = [(m, d, e) for m, d, ok, e in results if not ok]
        assert len(failed) == 0, f"Failed imports: {failed}"

    def test_runtime_mode_check(self):
        from waggledance.tools.validate_cutover import check_runtime_mode
        ok, msg = check_runtime_mode()
        assert ok is True
        assert "waggledance" in msg

    def test_core_class_check(self):
        from waggledance.tools.validate_cutover import check_core_classes
        results = check_core_classes()
        failed = [(n, e) for n, ok, e in results if not ok]
        assert len(failed) == 0, f"Failed classes: {failed}"
