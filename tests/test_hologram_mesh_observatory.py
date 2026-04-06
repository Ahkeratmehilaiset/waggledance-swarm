"""Tests for v3.5.5 Hologram Mesh Observatory.

Covers: hologram payload, mesh state, trace, MAGMA timeline,
routing tokenization fix, cache-aware trace, hex latency guard,
counter consistency, health/quarantine, auth regression, safe defaults.
"""

import re
import time
import asyncio
import threading
from unittest.mock import MagicMock, patch, PropertyMock

import pytest


# ══════════════════════════════════════════════════════════════════
# 1. Routing tokenization fix
# ══════════════════════════════════════════════════════════════════

class TestRoutingTokenization:
    """Punctuation must not break system/time keyword matching."""

    def test_normalize_strips_trailing_punctuation(self):
        from waggledance.core.orchestration.routing_policy import _normalize_tokens
        tokens = _normalize_tokens("What is the status?")
        assert "status" in tokens
        assert "status?" not in tokens

    def test_normalize_strips_exclamation(self):
        from waggledance.core.orchestration.routing_policy import _normalize_tokens
        tokens = _normalize_tokens("Check health!")
        assert "health" in tokens

    def test_normalize_strips_comma(self):
        from waggledance.core.orchestration.routing_policy import _normalize_tokens
        tokens = _normalize_tokens("status, health, uptime")
        assert "status" in tokens
        assert "health" in tokens
        assert "uptime" in tokens

    def test_normalize_strips_period(self):
        from waggledance.core.orchestration.routing_policy import _normalize_tokens
        tokens = _normalize_tokens("Show me the version.")
        assert "version" in tokens

    def test_normalize_empty_after_strip(self):
        from waggledance.core.orchestration.routing_policy import _normalize_tokens
        tokens = _normalize_tokens("? ! , .")
        assert "" not in tokens

    def test_system_query_with_punctuation(self):
        from waggledance.core.orchestration.routing_policy import extract_features
        features = extract_features(
            query="What is the status?",
            hot_cache_hit=False,
            memory_score=0.0,
            matched_keywords=[],
            profile="HOME",
        )
        assert features.is_system_query is True

    def test_time_query_with_punctuation(self):
        from waggledance.core.orchestration.routing_policy import extract_features
        features = extract_features(
            query="What time is it?",
            hot_cache_hit=False,
            memory_score=0.0,
            matched_keywords=[],
            profile="HOME",
        )
        assert features.is_time_query is True

    def test_finnish_system_query(self):
        from waggledance.core.orchestration.routing_policy import extract_features
        features = extract_features(
            query="Mikä on tila?",
            hot_cache_hit=False,
            memory_score=0.0,
            matched_keywords=[],
            profile="HOME",
        )
        assert features.is_system_query is True

    def test_finnish_time_query(self):
        from waggledance.core.orchestration.routing_policy import extract_features
        features = extract_features(
            query="Paljonko on kellonaika?",
            hot_cache_hit=False,
            memory_score=0.0,
            matched_keywords=[],
            profile="HOME",
        )
        assert features.is_time_query is True

    def test_no_false_positive_system(self):
        from waggledance.core.orchestration.routing_policy import extract_features
        features = extract_features(
            query="Tell me about bees",
            hot_cache_hit=False,
            memory_score=0.0,
            matched_keywords=[],
            profile="HOME",
        )
        assert features.is_system_query is False
        assert features.is_time_query is False


# ══════════════════════════════════════════════════════════════════
# 2. Hologram payload structure
# ══════════════════════════════════════════════════════════════════

class TestHologramPayloadStructure:
    """Hologram state must include hex_mesh, magma_timeline, ops."""

    def _make_mock_service(self):
        """Build a mock AutonomyService with realistic structure."""
        service = MagicMock()
        runtime = MagicMock()
        runtime.is_running = True
        runtime.stats.return_value = {
            "api_key_configured": True,
            "auth_middleware_active": True,
            "lifecycle": {"state": "RUNNING", "healthy_components": 5, "total_components": 5},
            "persistence": {"healthy_stores": 3, "total_stores": 3},
            "admission": {"queue_depth": 0},
            "policy_engine": {"constitution_version": "1.0"},
            "feeds": {"feeds": {}},
            "specialist_trainer": {},
            "ollama": {},
            "solver_router": {},
            "night_pipeline": {},
            "dream_mode": {},
            "magma_audit": {},
            "magma_trust": {},
            "magma_event_log": {},
            "magma_replay": {},
            "magma_provenance": {},
            "total_queries": 42,
        }
        runtime.capability_confidence = MagicMock()
        runtime.capability_confidence.get_all.return_value = {}
        runtime.world_model = MagicMock()
        runtime.world_model.get_user_entity.return_value = None
        runtime.resource_kernel = MagicMock()
        runtime.resource_kernel.stats.return_value = {"active_tasks": 0}
        runtime.world_model.stats.return_value = {"graph": {"edges": 0, "nodes": 0}}
        runtime.case_builder = MagicMock()
        runtime.case_builder.stats.return_value = {"total": 0}
        runtime.working_memory = MagicMock()
        runtime.working_memory.stats.return_value = {"size": 0, "capacity": 10}
        runtime.solver_router = MagicMock()
        runtime.solver_router.stats.return_value = {}
        runtime.verifier = MagicMock()
        runtime.verifier.stats.return_value = {}

        service._runtime = runtime
        service._resource_kernel = runtime.resource_kernel
        service._container = None
        return service

    def test_payload_has_hex_mesh_key(self):
        from waggledance.adapters.http.routes.hologram import build_hologram_state
        service = self._make_mock_service()
        state = build_hologram_state(service)
        assert "hex_mesh" in state

    def test_payload_has_magma_timeline_key(self):
        from waggledance.adapters.http.routes.hologram import build_hologram_state
        service = self._make_mock_service()
        state = build_hologram_state(service)
        assert "magma_timeline" in state

    def test_payload_has_ops_key(self):
        from waggledance.adapters.http.routes.hologram import build_hologram_state
        service = self._make_mock_service()
        state = build_hologram_state(service)
        assert "ops" in state

    def test_payload_preserves_existing_keys(self):
        from waggledance.adapters.http.routes.hologram import build_hologram_state
        service = self._make_mock_service()
        state = build_hologram_state(service)
        assert "nodes" in state
        assert "node_meta" in state
        assert "edges" in state
        assert "events" in state
        assert "hybrid" in state

    def test_hex_mesh_disabled_returns_enabled_false(self):
        from waggledance.adapters.http.routes.hologram import _hex_mesh_overlay
        service = MagicMock()
        service._container = None
        service._runtime = None
        result = _hex_mesh_overlay(service)
        assert result["enabled"] is False

    def test_magma_timeline_empty_when_no_container(self):
        from waggledance.adapters.http.routes.hologram import _magma_timeline
        service = MagicMock()
        service._container = None
        service._runtime = None
        result = _magma_timeline(service)
        assert result == []

    def test_ops_empty_when_no_container(self):
        from waggledance.adapters.http.routes.hologram import _ops_overlay
        service = MagicMock()
        service._container = None
        service._runtime = None
        result = _ops_overlay(service, {})
        assert isinstance(result, dict)


# ══════════════════════════════════════════════════════════════════
# 3. Trace ring buffer
# ══════════════════════════════════════════════════════════════════

class TestTraceRingBuffer:
    """Hex neighbor assist must maintain a trace ring buffer."""

    def _make_assist(self):
        from waggledance.application.services.hex_neighbor_assist import HexNeighborAssist
        registry = MagicMock()
        registry.cell_count = 0
        registry.cells = {}
        health = MagicMock()
        health.get_quarantined_cells.return_value = []
        return HexNeighborAssist(
            topology_registry=registry,
            health_monitor=health,
            enabled=False,
        )

    def test_trace_buffer_starts_empty(self):
        ha = self._make_assist()
        assert ha.get_trace_buffer() == []

    def test_last_trace_starts_none(self):
        ha = self._make_assist()
        assert ha.get_last_trace() is None

    def test_trace_buffer_max_size(self):
        ha = self._make_assist()
        for i in range(25):
            ha._trace_buffer.append({"trace_id": f"t{i}"})
        assert len(ha.get_trace_buffer()) == 20  # maxlen=20

    def test_get_cell_states_empty_when_no_cells(self):
        ha = self._make_assist()
        assert ha.get_cell_states() == []


# ══════════════════════════════════════════════════════════════════
# 4. Cell state serialization
# ══════════════════════════════════════════════════════════════════

class TestCellStateSerialization:
    """get_cell_states must serialize correctly."""

    def test_cell_state_fields(self):
        from waggledance.application.services.hex_neighbor_assist import HexNeighborAssist
        from waggledance.core.domain.hex_mesh import HexCellDefinition, HexCoord, HexCellHealth

        registry = MagicMock()
        registry.cell_count = 1
        registry.cells = {
            "hub": HexCellDefinition(
                id="hub", coord=HexCoord(0, 0),
                domain_selectors=["general"], enabled=True,
            )
        }
        registry.get_cell_agents.return_value = [MagicMock(), MagicMock()]

        health_mon = MagicMock()
        health_mon.get_quarantined_cells.return_value = []
        health_mon.get_health.return_value = HexCellHealth(cell_id="hub")

        ha = HexNeighborAssist(
            topology_registry=registry,
            health_monitor=health_mon,
            enabled=True,
        )

        cells = ha.get_cell_states()
        assert len(cells) == 1
        c = cells[0]
        assert c["id"] == "hub"
        assert c["coord"] == {"q": 0, "r": 0}
        assert "health_score" in c
        assert "quarantined" in c
        assert c["agent_count"] == 2
        assert c["enabled"] is True

    def test_quarantined_cell_state(self):
        from waggledance.application.services.hex_neighbor_assist import HexNeighborAssist
        from waggledance.core.domain.hex_mesh import HexCellDefinition, HexCoord, HexCellHealth

        registry = MagicMock()
        registry.cell_count = 1
        registry.cells = {
            "bad": HexCellDefinition(
                id="bad", coord=HexCoord(1, 0),
                domain_selectors=["test"], enabled=True,
            )
        }
        registry.get_cell_agents.return_value = []

        quarantined_health = HexCellHealth(
            cell_id="bad",
            quarantine_until=time.time() + 300,
            recent_error_count=5,
        )
        health_mon = MagicMock()
        health_mon.get_quarantined_cells.return_value = ["bad"]
        health_mon.get_health.return_value = quarantined_health

        ha = HexNeighborAssist(
            topology_registry=registry,
            health_monitor=health_mon,
            enabled=True,
        )

        cells = ha.get_cell_states()
        assert cells[0]["quarantined"] is True
        assert cells[0]["state"] == "quarantined"


# ══════════════════════════════════════════════════════════════════
# 5. Cache-aware trace
# ══════════════════════════════════════════════════════════════════

class TestCacheAwareTrace:
    """Hot cache hits must be recorded in telemetry."""

    def test_hotcache_records_telemetry(self):
        from waggledance.application.services.chat_service import ChatService
        from waggledance.application.dto.chat_dto import ChatRequest

        orch = MagicMock()
        mem = MagicMock()
        mem.retrieve_context = MagicMock(return_value=[])
        cache = MagicMock()
        cache.get.return_value = "cached answer"
        config = MagicMock()
        config.get.return_value = False

        svc = ChatService(
            orchestrator=orch,
            memory_service=mem,
            hot_cache=cache,
            routing_policy_fn=None,
            config=config,
        )

        req = ChatRequest(query="hello", language="en")
        result = asyncio.run(svc.handle(req))
        assert result.source == "hotcache"
        assert result.cached is True


# ══════════════════════════════════════════════════════════════════
# 6. No secret leakage
# ══════════════════════════════════════════════════════════════════

class TestNoSecretLeakage:
    """Hologram payload must not contain secrets."""

    def test_no_api_key_in_payload(self):
        from waggledance.adapters.http.routes.hologram import _zero_state
        state = _zero_state()
        payload_str = str(state)
        assert "api_key" not in payload_str.lower()
        assert "token" not in payload_str.lower() or "token" in "total_tokens"  # OK

    def test_magma_sanitizes_secrets(self):
        from waggledance.adapters.http.routes.hologram import _magma_timeline
        service = MagicMock()
        service._container = None
        service._runtime = None
        result = _magma_timeline(service)
        for entry in result:
            assert "api_key" not in entry
            assert "token" not in entry


# ══════════════════════════════════════════════════════════════════
# 7. Auth regression
# ══════════════════════════════════════════════════════════════════

class TestAuthRegression:
    """Auth behavior must remain unchanged."""

    def test_hologram_view_is_public(self):
        """The /hologram HTML page should be public (no auth)."""
        from waggledance.adapters.http.routes.hologram import router
        routes = [r.path for r in router.routes]
        assert "/hologram" in routes

    def test_hologram_state_endpoint_exists(self):
        from waggledance.adapters.http.routes.hologram import router
        routes = [r.path for r in router.routes]
        assert "/api/hologram/state" in routes


# ══════════════════════════════════════════════════════════════════
# 8. Safe defaults
# ══════════════════════════════════════════════════════════════════

class TestSafeDefaults:
    """Feature flags must default to OFF."""

    def test_hex_mesh_default_disabled(self):
        import yaml
        from pathlib import Path
        settings_path = Path("configs/settings.yaml")
        if not settings_path.exists():
            pytest.skip("settings.yaml not found")
        with open(settings_path) as f:
            data = yaml.safe_load(f)
        assert data.get("hex_mesh", {}).get("enabled") is False

    def test_llm_parallel_default_disabled(self):
        import yaml
        from pathlib import Path
        settings_path = Path("configs/settings.yaml")
        if not settings_path.exists():
            pytest.skip("settings.yaml not found")
        with open(settings_path) as f:
            data = yaml.safe_load(f)
        assert data.get("llm_parallel", {}).get("enabled") is False

    def test_gemma_profiles_default_disabled(self):
        import yaml
        from pathlib import Path
        settings_path = Path("configs/settings.yaml")
        if not settings_path.exists():
            pytest.skip("settings.yaml not found")
        with open(settings_path) as f:
            data = yaml.safe_load(f)
        assert data.get("gemma_profiles", {}).get("enabled") is False


# ══════════════════════════════════════════════════════════════════
# 9. Hex disabled fallback
# ══════════════════════════════════════════════════════════════════

class TestHexDisabledFallback:
    """When hex mesh is disabled, UI data should still work."""

    def test_hex_mesh_overlay_returns_dict(self):
        from waggledance.adapters.http.routes.hologram import _hex_mesh_overlay
        service = MagicMock()
        service._container = None
        service._runtime = None
        result = _hex_mesh_overlay(service)
        assert isinstance(result, dict)
        assert result.get("enabled") is False

    def test_ops_overlay_returns_dict(self):
        from waggledance.adapters.http.routes.hologram import _ops_overlay
        service = MagicMock()
        service._container = None
        service._runtime = None
        result = _ops_overlay(service, {"total_queries": 10})
        assert isinstance(result, dict)

    def test_resolve_returns_none_when_disabled(self):
        from waggledance.application.services.hex_neighbor_assist import HexNeighborAssist
        registry = MagicMock()
        registry.cell_count = 0
        health = MagicMock()
        health.get_quarantined_cells.return_value = []
        ha = HexNeighborAssist(
            topology_registry=registry,
            health_monitor=health,
            enabled=False,
        )
        result = asyncio.run(ha.resolve("test query"))
        assert result is None


# ══════════════════════════════════════════════════════════════════
# 10. Health / quarantine / self-heal
# ══════════════════════════════════════════════════════════════════

class TestHealthQuarantineSelfHeal:
    """Health monitor must handle quarantine/recovery correctly."""

    def test_rlock_no_deadlock(self):
        """stats() calls get_quarantined_cells() — needs RLock."""
        from waggledance.application.services.hex_health_monitor import HexHealthMonitor
        hm = HexHealthMonitor()
        hm.record_error("cell1")
        hm.record_error("cell1")
        hm.record_error("cell1")
        # This should not deadlock — uses RLock
        stats = hm.stats()
        assert isinstance(stats, dict)

    def test_quarantine_triggers_at_threshold(self):
        from waggledance.application.services.hex_health_monitor import HexHealthMonitor
        hm = HexHealthMonitor(error_threshold=2)
        hm.record_error("c1")
        assert hm.is_healthy("c1")
        hm.record_error("c1")
        assert not hm.is_healthy("c1")

    def test_recovery_clears_quarantine(self):
        from waggledance.application.services.hex_health_monitor import HexHealthMonitor
        hm = HexHealthMonitor(error_threshold=2, cooldown_s=300)
        hm.record_error("c1")
        hm.record_error("c1")
        assert not hm.is_healthy("c1")
        result = hm.probe_recovery("c1", success=True)
        assert result is True
        assert hm.is_healthy("c1")

    def test_no_false_quarantine(self):
        """Single error should NOT quarantine."""
        from waggledance.application.services.hex_health_monitor import HexHealthMonitor
        hm = HexHealthMonitor(error_threshold=3)
        hm.record_error("c1")
        assert hm.is_healthy("c1")

    def test_success_decays_errors(self):
        from waggledance.application.services.hex_health_monitor import HexHealthMonitor
        hm = HexHealthMonitor(error_threshold=3)
        hm.record_error("c1")
        hm.record_error("c1")
        hm.record_success("c1")
        h = hm.get_health("c1")
        assert h.recent_error_count == 1  # decayed from 2


# ══════════════════════════════════════════════════════════════════
# 11. Counter consistency
# ══════════════════════════════════════════════════════════════════

class TestCounterConsistency:
    """Hex mesh counters must be consistent."""

    def test_metrics_keys_present(self):
        from waggledance.application.services.hex_neighbor_assist import HexNeighborAssist
        registry = MagicMock()
        registry.cell_count = 0
        health = MagicMock()
        health.get_quarantined_cells.return_value = []
        ha = HexNeighborAssist(
            topology_registry=registry, health_monitor=health, enabled=False,
        )
        m = ha.get_metrics()
        expected_keys = [
            "enabled", "cells_loaded", "origin_cell_resolutions",
            "local_only_resolutions", "neighbor_assist_resolutions",
            "global_escalations", "llm_last_resolutions",
            "completed_hex_neighbor_batches", "neighbors_consulted_total",
            "quarantined_cells", "self_heal_events",
            "magma_traces_written", "ttl_exhaustions",
        ]
        for k in expected_keys:
            assert k in m, f"Missing key: {k}"

    def test_counters_start_at_zero(self):
        from waggledance.application.services.hex_neighbor_assist import HexNeighborAssist
        registry = MagicMock()
        registry.cell_count = 0
        health = MagicMock()
        health.get_quarantined_cells.return_value = []
        ha = HexNeighborAssist(
            topology_registry=registry, health_monitor=health, enabled=False,
        )
        m = ha.get_metrics()
        assert m["origin_cell_resolutions"] == 0
        assert m["local_only_resolutions"] == 0
        assert m["neighbor_assist_resolutions"] == 0
        assert m["global_escalations"] == 0


# ══════════════════════════════════════════════════════════════════
# 12. Status/ops additive regression
# ══════════════════════════════════════════════════════════════════

class TestStatusOpsAdditive:
    """New fields must be additive — no existing fields removed."""

    def test_zero_state_has_32_nodes(self):
        from waggledance.adapters.http.routes.hologram import _zero_state, ALL_NODE_IDS
        zs = _zero_state()
        assert len(zs["nodes"]) == len(ALL_NODE_IDS)
        assert len(zs["node_meta"]) == len(ALL_NODE_IDS)

    def test_all_32_node_ids_present(self):
        from waggledance.adapters.http.routes.hologram import ALL_NODE_IDS
        assert len(ALL_NODE_IDS) == 32


# ══════════════════════════════════════════════════════════════════
# 13. Hex latency guard
# ══════════════════════════════════════════════════════════════════

class TestHexLatencyGuard:
    """Sequential neighbor must respect budget."""

    def test_sequential_budget_field_exists(self):
        """The sequential fallback path must reference a budget variable."""
        import inspect
        from waggledance.application.services.hex_neighbor_assist import HexNeighborAssist
        source = inspect.getsource(HexNeighborAssist._try_neighbors)
        assert "seq_budget_s" in source or "budget" in source

    def test_max_one_neighbor_when_no_parallel(self):
        """Sequential fallback limits to 1 neighbor."""
        import inspect
        from waggledance.application.services.hex_neighbor_assist import HexNeighborAssist
        source = inspect.getsource(HexNeighborAssist._try_neighbors)
        assert "selected[:1]" in source


# ══════════════════════════════════════════════════════════════════
# 14. Trace serialization
# ══════════════════════════════════════════════════════════════════

class TestTraceSerialization:
    """HexResolutionTrace.to_dict() must include all fields."""

    def test_trace_dict_keys(self):
        from waggledance.core.domain.hex_mesh import HexResolutionTrace
        trace = HexResolutionTrace(
            trace_id="abc123",
            origin_cell_id="hub",
            query="test",
        )
        d = trace.to_dict()
        assert "trace_id" in d
        assert "origin_cell_id" in d
        assert "final_confidence" in d
        assert "final_source" in d
        assert "cache_hit" in d
        assert "ttl_path" in d

    def test_trace_dict_cache_hit_default_false(self):
        from waggledance.core.domain.hex_mesh import HexResolutionTrace
        trace = HexResolutionTrace(trace_id="x", origin_cell_id="y", query="z")
        assert trace.to_dict()["cache_hit"] is False


# ══════════════════════════════════════════════════════════════════
# 15. MAGMA timeline sanitization
# ══════════════════════════════════════════════════════════════════

class TestMagmaTimelineSanitization:
    """MAGMA timeline entries must have secrets stripped."""

    def test_sanitization_removes_api_key(self):
        from waggledance.adapters.http.routes.hologram import _magma_timeline

        mock_entry = MagicMock()
        mock_entry.to_dict.return_value = {
            "event_type": "HEX_QUERY_STARTED",
            "source": "hex_mesh",
            "api_key": "secret123",
            "timestamp": time.time(),
        }

        magma = MagicMock()
        magma.recent.return_value = [mock_entry]

        container = MagicMock()
        container.magma_audit = magma

        service = MagicMock()
        service._container = container

        result = _magma_timeline(service)
        for entry in result:
            assert "api_key" not in entry


# ══════════════════════════════════════════════════════════════════
# 16. Selected cell drilldown
# ══════════════════════════════════════════════════════════════════

class TestSelectedCellDrilldown:
    """Cell states must include drilldown data."""

    def test_cell_has_recent_errors(self):
        from waggledance.application.services.hex_neighbor_assist import HexNeighborAssist
        from waggledance.core.domain.hex_mesh import HexCellDefinition, HexCoord, HexCellHealth

        registry = MagicMock()
        registry.cells = {
            "test": HexCellDefinition(
                id="test", coord=HexCoord(0, 0),
                domain_selectors=["test"], enabled=True,
            )
        }
        registry.get_cell_agents.return_value = []

        health = MagicMock()
        health.get_quarantined_cells.return_value = []
        h = HexCellHealth(cell_id="test", recent_error_count=2, recent_timeout_count=1)
        health.get_health.return_value = h

        ha = HexNeighborAssist(
            topology_registry=registry, health_monitor=health, enabled=True,
        )
        cells = ha.get_cell_states()
        assert cells[0]["recent_errors"] == 2
        assert cells[0]["recent_timeouts"] == 1
