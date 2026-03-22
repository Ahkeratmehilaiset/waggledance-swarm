# SPDX-License-Identifier: Apache-2.0
"""Tests for Hologram Brain v6 — 32 nodes, node_meta, ring layout, i18n, auth safety.

Covers:
- No-fake-floor corrections (13 existing nodes)
- Node metadata contract (32 nodes, state/device/freshness/source_class/quality)
- New ring nodes (system 8, learning 4)
- New endpoints (/api/profile/impact, /api/capabilities/state, /api/learning/state-machine)
- Frontend integrity (no APIARY, profile sanitization, branding, auth safety)
- Enum strictness (state, source_class values)
- Shared state derivation consistency
"""

import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── Helpers ──────────────────────────────────────────────────

_V6_HTML_PATH = Path(__file__).resolve().parents[1] / "web" / "hologram-brain-v6.html"
_V5_HTML_PATH = Path(__file__).resolve().parents[1] / "web" / "hologram-brain-v5.html"

_VALID_STATES = {"unavailable", "unwired", "framework", "idle", "active", "training", "failed"}
_VALID_SOURCE_CLASSES = {"live", "simulated", "legacy", "error", None}
_NO_QUALITY_STATES = {"unavailable", "unwired", "framework"}

ALL_32_NODE_IDS = {
    "sensory", "waggle_memory", "episodic_memory", "semantic_memory",
    "working_memory", "llm_core", "reason_layer", "causal_map",
    "decision", "user_model",
    "magma_audit", "magma_trust", "magma_event_log", "magma_replay",
    "magma_provenance",
    "sys_auth", "sys_policy", "sys_api", "sys_websocket",
    "sys_feeds", "sys_queues", "sys_storage", "sys_compute",
    "learn_night", "learn_dream", "learn_training", "learn_canary",
    "micro_train", "micro_route", "micro_anom", "micro_therm", "micro_stats",
}

SYS_NODE_IDS = {"sys_auth", "sys_policy", "sys_api", "sys_websocket",
                "sys_feeds", "sys_queues", "sys_storage", "sys_compute"}
LEARN_NODE_IDS = {"learn_night", "learn_dream", "learn_training", "learn_canary"}


def _make_service(running=True, stats=None, extra_attrs=None):
    """Create a minimal mock AutonomyService for hologram tests."""
    runtime = MagicMock()
    runtime.is_running = running

    # Default stats
    default_stats = {
        "world_model": {"graph": {"edges": 0, "nodes": 0}},
        "cases": {"total": 0},
        "working_memory": {"size": 0, "capacity": 10},
        "solver_router": {"total": 0, "quality_distribution": {}, "active_count": 0, "recent_activity": {}},
        "verifier": {"pass_rate": 0.0, "recent_checks": 0},
        "ollama": {"active_requests": 0},
        "specialist_trainer": {"active_trainers": 0},
        "night_pipeline": {},
        "dream_mode": {},
        "magma_audit": {}, "magma_trust": {}, "magma_event_log": {},
        "magma_replay": {}, "magma_provenance": {},
        "lifecycle": {"state": "RUNNING", "healthy_components": 5, "total_components": 5},
        "admission": {},
        "persistence": {},
        "feeds": {},
        "policy_engine": {},
    }
    if stats:
        default_stats.update(stats)

    runtime.stats.return_value = default_stats

    # Stub component stats
    for attr in ("resource_kernel", "world_model", "case_builder",
                 "working_memory", "solver_router", "verifier"):
        comp = MagicMock()
        comp_stats = default_stats.get(attr, default_stats.get(attr.replace("_", ""), {}))
        comp.stats.return_value = comp_stats
        setattr(runtime, attr, comp)

    # Working memory specific
    runtime.working_memory.stats.return_value = default_stats.get("working_memory", {"size": 0, "capacity": 10})
    runtime.resource_kernel.stats.return_value = {"active_tasks": 0}

    # Capability confidence
    runtime.capability_confidence = None

    # World model user entity
    runtime.world_model.get_user_entity.return_value = None

    # LLM adapter + model store
    runtime.llm_adapter = None
    runtime.model_store = None

    if extra_attrs:
        for k, v in extra_attrs.items():
            setattr(runtime, k, v)

    service = MagicMock()
    service._runtime = runtime
    service._resource_kernel = runtime.resource_kernel
    service.get_status.return_value = {
        "profile": "HOME",
        "resource_kernel": {},
        "runtime": {},
        "lifecycle": default_stats.get("lifecycle", {}),
        "capabilities": {"registered": {}},
    }

    return service


def _build_state(service):
    """Call build_hologram_state with patched imports."""
    from waggledance.adapters.http.routes.hologram import build_hologram_state
    # Patch _ws_clients and _gpu_info_safe to avoid side effects
    with patch("waggledance.adapters.http.routes.hologram._gpu_info_safe",
               return_value={"gpu_percent": 0, "gpu_mem_used": 0, "gpu_mem_total": 0}):
        with patch("waggledance.adapters.http.routes.hologram.psutil") as mock_psutil:
            mock_psutil.cpu_percent.return_value = 0
            try:
                with patch("waggledance.adapters.http.routes.compat_dashboard._ws_clients", set()):
                    return build_hologram_state(service)
            except ImportError:
                return build_hologram_state(service)


def _read_v6_html():
    return _V6_HTML_PATH.read_text(encoding="utf-8")


# ═══════════════════════════════════════════════════════════════
# No-fake-floor tests (existing node corrections)
# ═══════════════════════════════════════════════════════════════

class TestNoFakeFloors:
    """Verify that all 13 fake activation floors have been removed."""

    def test_sensory_zero_when_no_tasks(self):
        """sensory=0.0 when active_tasks=0 (was 0.1)."""
        svc = _make_service()
        svc._runtime.resource_kernel.stats.return_value = {"active_tasks": 0}
        state = _build_state(svc)
        assert state["nodes"]["sensory"] == 0.0

    def test_waggle_memory_zero_when_empty_graph(self):
        """waggle_memory=0.0 when edge_count=0 (was 0.1)."""
        svc = _make_service()
        state = _build_state(svc)
        assert state["nodes"]["waggle_memory"] == 0.0

    def test_episodic_zero_when_no_cases(self):
        """episodic_memory=0.0 when cb_total=0 (was 0.1)."""
        svc = _make_service()
        state = _build_state(svc)
        assert state["nodes"]["episodic_memory"] == 0.0

    def test_causal_map_zero_when_empty(self):
        """causal_map=0.0 when node_count=0 (was 0.1)."""
        svc = _make_service()
        state = _build_state(svc)
        assert state["nodes"]["causal_map"] == 0.0

    def test_working_memory_zero_when_empty(self):
        """working_memory=0.0 when wm_size=0 (was 0.1 from +0.1 offset)."""
        svc = _make_service()
        state = _build_state(svc)
        assert state["nodes"]["working_memory"] == 0.0

    def test_llm_core_zero_when_no_routes(self):
        """llm_core=0.0 when no active requests (was 0.5)."""
        svc = _make_service()
        state = _build_state(svc)
        assert state["nodes"]["llm_core"] == 0.0

    def test_reason_layer_zero_when_no_routes(self):
        """reason_layer=0.0 when active_count=0 (was 0.5)."""
        svc = _make_service()
        state = _build_state(svc)
        assert state["nodes"]["reason_layer"] == 0.0

    def test_micro_nodes_zero_when_no_activity(self):
        """All micro_*=0.0 when no recent activity (not confidence-based)."""
        svc = _make_service()
        state = _build_state(svc)
        for nid in ("micro_train", "micro_route", "micro_anom", "micro_therm", "micro_stats"):
            assert state["nodes"][nid] == 0.0, f"{nid} should be 0.0"

    def test_decision_zero_when_no_recent_checks(self):
        """decision=0.0 when no recent verifications (not historical pass_rate)."""
        svc = _make_service()
        state = _build_state(svc)
        assert state["nodes"]["decision"] == 0.0

    def test_llm_core_activation_is_load_not_quality(self):
        """llm_core activation = active_requests/3, not quality ratio."""
        svc = _make_service(stats={
            "ollama": {"active_requests": 2},
            "solver_router": {"total": 100, "quality_distribution": {"bronze": 30, "gold": 50}, "active_count": 0},
        })
        state = _build_state(svc)
        # active_requests=2 -> 2/3 = 0.67
        assert abs(state["nodes"]["llm_core"] - 0.67) < 0.01

    def test_reason_layer_activation_is_activity_not_gold(self):
        """reason_layer activation = active_count/5, not gold/total."""
        svc = _make_service(stats={
            "solver_router": {"total": 100, "quality_distribution": {"gold": 80}, "active_count": 3},
        })
        state = _build_state(svc)
        # active_count=3 -> 3/5 = 0.6
        assert abs(state["nodes"]["reason_layer"] - 0.6) < 0.01

    def test_quality_in_node_meta_not_activation(self):
        """quality metrics in node_meta.quality, not in nodes dict."""
        svc = _make_service(stats={
            "solver_router": {"total": 100, "quality_distribution": {"bronze": 20, "gold": 60}, "active_count": 0},
            "verifier": {"pass_rate": 0.85, "recent_checks": 0},
        })
        # Give it an llm_adapter so quality can populate
        adapter = MagicMock()
        adapter.model_loaded = True
        svc._runtime.llm_adapter = adapter
        state = _build_state(svc)
        # llm_core activation should be 0 (no active_requests), not quality ratio
        assert state["nodes"]["llm_core"] == 0.0
        # But quality should be in node_meta
        meta = state["node_meta"]["llm_core"]
        assert meta["quality"] is not None


# ═══════════════════════════════════════════════════════════════
# Node metadata contract tests
# ═══════════════════════════════════════════════════════════════

class TestNodeMetadataContract:

    def test_hologram_state_has_32_nodes(self):
        """build_hologram_state returns exactly 32 node keys."""
        svc = _make_service()
        state = _build_state(svc)
        assert set(state["nodes"].keys()) == ALL_32_NODE_IDS

    def test_hologram_state_has_node_meta(self):
        """Returns node_meta dict with 32 entries."""
        svc = _make_service()
        state = _build_state(svc)
        assert "node_meta" in state
        assert set(state["node_meta"].keys()) == ALL_32_NODE_IDS

    def test_node_meta_has_all_fields(self):
        """Each meta entry has state, device, freshness_s, source_class, quality."""
        svc = _make_service()
        state = _build_state(svc)
        required_fields = {"state", "device", "freshness_s", "source_class", "quality"}
        for nid, meta in state["node_meta"].items():
            assert required_fields.issubset(meta.keys()), f"{nid} missing fields: {required_fields - set(meta.keys())}"

    def test_zero_state_32_nodes_all_zero(self):
        """Zero-state: 32 keys, all 0.0."""
        svc = _make_service(running=False)
        state = _build_state(svc)
        assert len(state["nodes"]) == 32
        for nid, val in state["nodes"].items():
            assert val == 0.0, f"{nid} should be 0.0 in zero-state, got {val}"

    def test_zero_state_meta_all_unavailable(self):
        """Zero-state: all node_meta state='unavailable', source_class=None, quality=None."""
        svc = _make_service(running=False)
        state = _build_state(svc)
        for nid, meta in state["node_meta"].items():
            assert meta["state"] == "unavailable", f"{nid} state should be unavailable"
            assert meta["source_class"] is None, f"{nid} source_class should be None"
            assert meta["quality"] is None, f"{nid} quality should be None"

    def test_idle_vs_unavailable_distinct(self):
        """activation=0.0 + state='idle' (running) vs state='unavailable' (not running)."""
        svc_up = _make_service(running=True)
        svc_down = _make_service(running=False)
        state_up = _build_state(svc_up)
        state_down = _build_state(svc_down)
        # sensory is 0.0 in both, but state differs
        assert state_up["nodes"]["sensory"] == 0.0
        assert state_down["nodes"]["sensory"] == 0.0
        # Up: state should be idle (or active depending on context)
        assert state_up["node_meta"]["sensory"]["state"] in ("idle", "active")
        # Down: state should be unavailable
        assert state_down["node_meta"]["sensory"]["state"] == "unavailable"

    def test_sys_compute_zero_valid(self):
        """sys_compute=0.0 when CPU=0% and GPU=0% (no floor)."""
        svc = _make_service()
        state = _build_state(svc)
        assert state["nodes"]["sys_compute"] == 0.0

    def test_sys_compute_device_from_telemetry(self):
        """node_meta['sys_compute'].device derived from nvidia-smi, not hardcoded."""
        from waggledance.adapters.http.routes.hologram import build_hologram_state
        svc = _make_service()
        with patch("waggledance.adapters.http.routes.hologram._gpu_info_safe",
                    return_value={"gpu_percent": 0, "gpu_mem_used": 0, "gpu_mem_total": 8192}):
            with patch("waggledance.adapters.http.routes.hologram.psutil") as mock_psutil:
                mock_psutil.cpu_percent.return_value = 0
                with patch("waggledance.adapters.http.routes.compat_dashboard._ws_clients", set()):
                    state = build_hologram_state(svc)
        assert state["node_meta"]["sys_compute"]["device"] == "GPU"

    def test_llm_core_device_from_runtime(self):
        """node_meta['llm_core'].device derived from Ollama config."""
        adapter = MagicMock()
        adapter.model_loaded = True
        svc = _make_service(
            stats={"ollama": {"active_requests": 0, "gpu_layers": 33, "model_name": "phi4-mini"}},
            extra_attrs={"llm_adapter": adapter},
        )
        state = _build_state(svc)
        assert state["node_meta"]["llm_core"]["device"] == "GPU"

    def test_llm_core_unavailable_when_no_adapter(self):
        """llm_core state='unavailable' when no llm_adapter registered."""
        svc = _make_service()
        svc._runtime.llm_adapter = None
        state = _build_state(svc)
        assert state["node_meta"]["llm_core"]["state"] == "unavailable"

    def test_llm_core_unwired_when_no_model_loaded(self):
        """llm_core state='unwired' when adapter exists but no model loaded."""
        adapter = MagicMock()
        adapter.model_loaded = False
        svc = _make_service(
            stats={"ollama": {"active_requests": 0, "models_loaded": 0}},
            extra_attrs={"llm_adapter": adapter},
        )
        state = _build_state(svc)
        assert state["node_meta"]["llm_core"]["state"] == "unwired"

    def test_llm_core_idle_only_when_model_loaded(self):
        """llm_core state='idle' only when adapter + model actually loaded."""
        adapter = MagicMock()
        adapter.model_loaded = True
        svc = _make_service(
            stats={"ollama": {"active_requests": 0, "model_name": "phi4-mini"}},
            extra_attrs={"llm_adapter": adapter},
        )
        state = _build_state(svc)
        assert state["node_meta"]["llm_core"]["state"] == "idle"

    def test_micro_unwired_when_no_model_store(self):
        """micro_* state='unwired' when no model_store."""
        svc = _make_service()
        svc._runtime.model_store = None
        state = _build_state(svc)
        for nid in ("micro_route", "micro_anom", "micro_therm", "micro_stats", "micro_train"):
            assert state["node_meta"][nid]["state"] == "unwired", f"{nid} should be unwired"

    def test_micro_framework_when_no_trained_model(self):
        """micro_* state='framework' when store exists but no artifact."""
        store = MagicMock()
        store.has_model.return_value = False
        svc = _make_service(extra_attrs={"model_store": store})
        state = _build_state(svc)
        for nid in ("micro_route", "micro_anom", "micro_therm", "micro_stats"):
            assert state["node_meta"][nid]["state"] == "framework", f"{nid} should be framework"

    def test_micro_idle_only_when_trained_model_exists(self):
        """micro_* state='idle' only when model_store.has_model() is true."""
        store = MagicMock()
        store.has_model.return_value = True
        svc = _make_service(extra_attrs={"model_store": store})
        state = _build_state(svc)
        for nid in ("micro_route", "micro_anom", "micro_therm", "micro_stats"):
            assert state["node_meta"][nid]["state"] == "idle", f"{nid} should be idle"

    def test_dream_meta_simulated_when_active(self):
        """learn_dream source_class='simulated' when dream active."""
        svc = _make_service(stats={"dream_mode": {"active": True}})
        state = _build_state(svc)
        assert state["node_meta"]["learn_dream"]["source_class"] == "simulated"

    def test_dream_meta_live_when_idle(self):
        """learn_dream source_class='live' when idle."""
        svc = _make_service(stats={"dream_mode": {"active": False}})
        state = _build_state(svc)
        assert state["node_meta"]["learn_dream"]["source_class"] == "live"


# ═══════════════════════════════════════════════════════════════
# New ring node tests
# ═══════════════════════════════════════════════════════════════

class TestNewRingNodes:

    def test_system_ring_nodes_present(self):
        """sys_auth through sys_compute in nodes dict."""
        svc = _make_service()
        state = _build_state(svc)
        for nid in SYS_NODE_IDS:
            assert nid in state["nodes"], f"{nid} missing from nodes"

    def test_sys_compute_state_from_gpu_telemetry(self):
        """sys_compute state='active' when gpu_percent>0."""
        from waggledance.adapters.http.routes.hologram import build_hologram_state
        svc = _make_service()
        with patch("waggledance.adapters.http.routes.hologram._gpu_info_safe",
                    return_value={"gpu_percent": 45, "gpu_mem_used": 2000, "gpu_mem_total": 8192}):
            with patch("waggledance.adapters.http.routes.hologram.psutil") as mock_psutil:
                mock_psutil.cpu_percent.return_value = 10
                with patch("waggledance.adapters.http.routes.compat_dashboard._ws_clients", set()):
                    state = build_hologram_state(svc)
        assert state["node_meta"]["sys_compute"]["state"] == "active"
        assert state["node_meta"]["sys_compute"]["device"] == "GPU"

    def test_sys_websocket_state_from_client_count(self):
        """sys_websocket state='active' when clients>0."""
        svc = _make_service()
        mock_clients = {MagicMock()}
        with patch("waggledance.adapters.http.routes.hologram._gpu_info_safe",
                    return_value={"gpu_percent": 0, "gpu_mem_used": 0, "gpu_mem_total": 0}):
            with patch("waggledance.adapters.http.routes.hologram.psutil") as mock_psutil:
                mock_psutil.cpu_percent.return_value = 0
                with patch("waggledance.adapters.http.routes.compat_dashboard._ws_clients", mock_clients):
                    from waggledance.adapters.http.routes.hologram import build_hologram_state
                    state = build_hologram_state(svc)
        assert state["node_meta"]["sys_websocket"]["state"] == "active"

    def test_sys_feeds_failed_when_all_errored(self):
        """sys_feeds state='failed' when all feed error_count>0."""
        svc = _make_service(stats={
            "feeds": {"feeds": {"f1": {"active": True, "error_count": 5}, "f2": {"active": True, "error_count": 3}}}
        })
        state = _build_state(svc)
        assert state["node_meta"]["sys_feeds"]["state"] == "failed"

    def test_sys_policy_unwired_when_no_engine(self):
        """sys_policy state='unwired' when no policy_engine in rs."""
        svc = _make_service(stats={"policy_engine": {}})
        state = _build_state(svc)
        assert state["node_meta"]["sys_policy"]["state"] == "unwired"

    def test_sys_queues_active_when_depth_gt_zero(self):
        """sys_queues state='active' when queue_depth>0."""
        svc = _make_service(stats={"admission": {"queue_depth": 3}})
        state = _build_state(svc)
        assert state["node_meta"]["sys_queues"]["state"] == "active"

    def test_sys_storage_failed_when_circuit_open(self):
        """sys_storage state='failed' when healthy<total."""
        svc = _make_service(stats={
            "persistence": {"healthy_stores": 1, "total_stores": 3, "io_in_flight": 0}
        })
        state = _build_state(svc)
        assert state["node_meta"]["sys_storage"]["state"] == "failed"

    def test_sys_ring_quality_always_null(self):
        """All 8 sys_* nodes: quality=null."""
        svc = _make_service()
        state = _build_state(svc)
        for nid in SYS_NODE_IDS:
            assert state["node_meta"][nid]["quality"] is None, f"{nid} quality should be null"

    def test_sys_auth_state_transitions(self):
        """sys_auth: state='idle' when key configured, 'failed' when no key."""
        # Key configured + middleware active
        svc = _make_service(stats={"api_key_configured": True, "auth_middleware_active": True})
        state = _build_state(svc)
        assert state["node_meta"]["sys_auth"]["state"] == "idle"

        # No key
        svc2 = _make_service(stats={"api_key_configured": False, "auth_middleware_active": True})
        state2 = _build_state(svc2)
        assert state2["node_meta"]["sys_auth"]["state"] == "failed"

    def test_sys_api_state_transitions(self):
        """sys_api: state='idle' when RUNNING, 'failed' when DEGRADED."""
        svc = _make_service(stats={"lifecycle": {"state": "RUNNING", "healthy_components": 5, "total_components": 5}})
        state = _build_state(svc)
        assert state["node_meta"]["sys_api"]["state"] == "idle"

        svc2 = _make_service(stats={"lifecycle": {"state": "DEGRADED", "healthy_components": 3, "total_components": 5}})
        state2 = _build_state(svc2)
        assert state2["node_meta"]["sys_api"]["state"] == "failed"

    def test_sys_auth_failed_when_middleware_inactive(self):
        """sys_auth: state='failed' when key configured but middleware inactive."""
        svc = _make_service(stats={"api_key_configured": True, "auth_middleware_active": False})
        state = _build_state(svc)
        assert state["node_meta"]["sys_auth"]["state"] == "failed"

    def test_sys_policy_framework_when_no_constitution(self):
        """sys_policy: state='framework' when engine registered but no constitution."""
        svc = _make_service(stats={"policy_engine": {"registered": True, "constitution_version": ""}})
        state = _build_state(svc)
        assert state["node_meta"]["sys_policy"]["state"] == "framework"

    def test_sys_policy_active_when_pending_approval_gt_zero(self):
        """sys_policy: state='active' when pending_approval > 0."""
        svc = _make_service(stats={
            "policy_engine": {"registered": True, "constitution_version": "1.0", "pending_approval": 2}
        })
        state = _build_state(svc)
        assert state["node_meta"]["sys_policy"]["state"] == "active"

    def test_sys_storage_active_when_store_io_in_flight(self):
        """sys_storage: state='active' when all stores healthy and io_in_flight > 0."""
        svc = _make_service(stats={
            "persistence": {"healthy_stores": 3, "total_stores": 3, "io_in_flight": 2}
        })
        state = _build_state(svc)
        assert state["node_meta"]["sys_storage"]["state"] == "active"

    def test_learning_ring_nodes_present(self):
        """learn_night through learn_canary in nodes dict."""
        svc = _make_service()
        state = _build_state(svc)
        for nid in LEARN_NODE_IDS:
            assert nid in state["nodes"], f"{nid} missing from nodes"

    def test_new_edges_present(self):
        """All ~22 edges in edges dict."""
        svc = _make_service()
        state = _build_state(svc)
        expected_edges = {
            "sensory->working_memory", "working_memory->llm_core", "semantic_memory->llm_core",
            "llm_core->reason_layer", "reason_layer->causal_map", "causal_map->micro_train",
            "causal_map->decision", "llm_core->decision", "user_model->working_memory",
            "sensory->magma_audit", "magma_trust->decision",
            "sensory->sys_feeds", "sys_auth->sys_policy", "sys_policy->decision",
            "sys_compute->llm_core", "sys_queues->sensory",
            "learn_night->learn_dream", "learn_dream->learn_training",
            "learn_training->learn_canary", "learn_canary->micro_train",
            "magma_audit->magma_replay", "magma_provenance->magma_trust",
        }
        for edge_key in expected_edges:
            assert edge_key in state["edges"], f"Edge {edge_key} missing"


# ═══════════════════════════════════════════════════════════════
# Endpoint tests
# ═══════════════════════════════════════════════════════════════

class TestEndpoints:

    def test_profile_impact_endpoint(self):
        """GET /api/profile/impact returns expected shape."""
        from waggledance.adapters.http.routes.compat_dashboard import api_profile_impact
        svc = _make_service()
        result = api_profile_impact(service=svc)
        assert "loaded_profile" in result
        assert "enabled_capabilities" in result
        assert "risk_mode" in result
        assert "target_environment" in result

    def test_capabilities_state_no_hardcoded_device(self):
        """Every family device derived from runtime, not assumed."""
        from waggledance.adapters.http.routes.compat_dashboard import api_capabilities_state
        svc = _make_service()
        result = api_capabilities_state(service=svc)
        for nid, info in result.items():
            # Device should be None or derived, never a hardcoded assumption
            assert info["device"] in (None, "CPU", "GPU", "UNKNOWN"), f"{nid} has unexpected device: {info['device']}"

    def test_learning_state_machine_endpoint(self):
        """Returns valid state from set."""
        from waggledance.adapters.http.routes.compat_dashboard import api_learning_state_machine
        svc = _make_service()
        result = api_learning_state_machine(service=svc)
        valid_states = {"awake", "replay", "consolidation", "dream", "training", "canary", "morning_report"}
        assert result["state"] in valid_states

    def test_new_public_paths_in_auth(self):
        """3 new paths in PUBLIC_PATHS."""
        from waggledance.adapters.http.middleware.auth import PUBLIC_PATHS
        assert "/api/profile/impact" in PUBLIC_PATHS
        assert "/api/capabilities/state" in PUBLIC_PATHS
        assert "/api/learning/state-machine" in PUBLIC_PATHS

    def test_shared_state_derivation_consistency(self):
        """derive_capability_state() returns same results for both callers."""
        from waggledance.adapters.http.routes._capability_state import derive_capability_state
        svc = _make_service()
        runtime = svc._runtime
        rs = runtime.stats()
        # Call twice — should return identical
        result1 = derive_capability_state(runtime, rs)
        result2 = derive_capability_state(runtime, rs)
        for nid in result1:
            assert result1[nid] == result2[nid], f"{nid} inconsistent between calls"

    def test_capabilities_state_returns_source_class(self):
        """GET /api/capabilities/state includes source_class for every family."""
        from waggledance.adapters.http.routes.compat_dashboard import api_capabilities_state
        svc = _make_service()
        result = api_capabilities_state(service=svc)
        for nid, info in result.items():
            assert "source_class" in info, f"{nid} missing source_class"

    def test_legacy_source_class_only_when_explicitly_detected(self):
        """source_class='legacy' only when explicitly set. Default is 'live'."""
        from waggledance.adapters.http.routes._capability_state import derive_capability_state
        svc = _make_service()
        adapter = MagicMock()
        adapter.model_loaded = True
        svc._runtime.llm_adapter = adapter
        rs = svc._runtime.stats()
        rs["ollama"] = {"active_requests": 0, "model_name": "phi4-mini"}
        result = derive_capability_state(svc._runtime, rs)
        # All should be "live" — no legacy detection in default setup
        for nid, info in result.items():
            assert info.source_class != "legacy" or info.source_class is None, \
                f"{nid} should not be legacy in default setup"


# ═══════════════════════════════════════════════════════════════
# Frontend integrity tests
# ═══════════════════════════════════════════════════════════════

class TestFrontendIntegrity:

    def test_no_apiary_in_hologram_html(self):
        """Case-insensitive grep of v6 HTML -> 0 'APIARY' matches."""
        html = _read_v6_html()
        matches = re.findall(r"apiary", html, re.IGNORECASE)
        assert len(matches) == 0, f"Found {len(matches)} APIARY matches in v6 HTML"

    def test_no_raw_profile_in_dom(self):
        """No DOM insertion of profile without localizeProfile().

        Checks that profile values inserted into the DOM always go through
        localizeProfile(). Direct patterns like textContent = ...profile
        without localizeProfile are flagged.
        """
        html = _read_v6_html()
        # Look for textContent or innerHTML assignments with 'profile' but without localizeProfile
        # This is a heuristic check
        lines = html.split('\n')
        violations = []
        for i, line in enumerate(lines, 1):
            # Check for direct profile insertion patterns
            if re.search(r'(textContent|innerHTML)\s*=.*\.profile\b', line):
                if 'localizeProfile' not in line:
                    violations.append(f"Line {i}: {line.strip()}")
        assert len(violations) == 0, f"Raw profile DOM insertions found:\n" + "\n".join(violations)

    def test_brand_in_html(self):
        """'WaggleDance Swarm AI' present."""
        html = _read_v6_html()
        assert "WaggleDance Swarm AI" in html

    def test_chat_tab_reads_localstorage_token(self):
        """Chat panel reads token from localStorage, includes Bearer header."""
        html = _read_v6_html()
        assert "localStorage.getItem('WAGGLE_API_KEY')" in html or 'localStorage.getItem("WAGGLE_API_KEY")' in html
        assert "'Bearer '" in html or '"Bearer "' in html

    def test_no_api_key_placeholder_in_html(self):
        """v6 HTML does NOT contain __WAGGLE_API_KEY__ server-side injection placeholder."""
        html = _read_v6_html()
        assert "__WAGGLE_API_KEY__" not in html

    def test_fi_en_labels_no_apiary(self):
        """Both FI and EN label dicts present, neither contains 'APIARY'."""
        html = _read_v6_html()
        # Check that both language dicts exist
        assert "'en'" in html or '"en"' in html
        assert "'fi'" in html or '"fi"' in html
        # Verify no APIARY in either dict
        assert "apiary" not in html.lower()

    def test_docked_panel_css(self):
        """Grid layout CSS present."""
        html = _read_v6_html()
        assert "grid-template-areas" in html
        assert "panel" in html

    def test_lang_toggle_in_html(self):
        """Language toggle element present."""
        html = _read_v6_html()
        assert "langToggle" in html

    def test_ring_nodes_in_html(self):
        """Node defs include ring assignments for all 32 nodes."""
        html = _read_v6_html()
        for nid in ALL_32_NODE_IDS:
            assert f"'{nid}'" in html or f'"{nid}"' in html, f"Node {nid} not found in v6 HTML"

    def test_v5_preserved(self):
        """v5 file still exists."""
        assert _V5_HTML_PATH.exists(), "hologram-brain-v5.html should be preserved"


# ═══════════════════════════════════════════════════════════════
# Enum strictness tests
# ═══════════════════════════════════════════════════════════════

class TestEnumStrictness:

    def test_state_enum_valid_values_only(self):
        """Every node_meta state is one of the 7 valid values."""
        svc = _make_service()
        state = _build_state(svc)
        for nid, meta in state["node_meta"].items():
            assert meta["state"] in _VALID_STATES, \
                f"{nid} has invalid state '{meta['state']}'. Valid: {_VALID_STATES}"

    def test_source_class_enum_valid_values_only(self):
        """Every node_meta source_class is one of: live, simulated, legacy, error, or None."""
        svc = _make_service()
        state = _build_state(svc)
        for nid, meta in state["node_meta"].items():
            assert meta["source_class"] in _VALID_SOURCE_CLASSES, \
                f"{nid} has invalid source_class '{meta['source_class']}'"

    def test_quality_null_when_unavailable(self):
        """node_meta.quality is null when state in [unavailable, unwired, framework]."""
        svc = _make_service()
        state = _build_state(svc)
        for nid, meta in state["node_meta"].items():
            if meta["state"] in _NO_QUALITY_STATES:
                assert meta["quality"] is None, \
                    f"{nid} should have quality=null when state='{meta['state']}', got {meta['quality']}"


# ═══════════════════════════════════════════════════════════════
# Auth safety regression
# ═══════════════════════════════════════════════════════════════

class TestAuthSafetyRegression:

    def test_no_api_key_value_in_served_hologram(self):
        """GET /hologram response body does NOT contain the API key value or placeholder.

        localStorage.getItem('WAGGLE_API_KEY') is ALLOWED (JS key name for reading).
        __WAGGLE_API_KEY__ is FORBIDDEN (server-side injection placeholder).
        """
        html = _read_v6_html()
        # Must not contain server-side placeholder
        assert "__WAGGLE_API_KEY__" not in html, "Server-side placeholder found in v6 HTML"

        # Must contain localStorage read (JS key name, allowed)
        assert "WAGGLE_API_KEY" in html, "Expected localStorage key name reference"

        # The key name should only appear in localStorage context
        waggle_key_hits = [line.strip() for line in html.split('\n')
                         if 'WAGGLE_API_KEY' in line]
        for hit in waggle_key_hits:
            # Every hit should be a localStorage read, not a value injection
            assert ('localStorage' in hit or
                    'getItem' in hit or
                    hit.strip().startswith('//') or
                    hit.strip().startswith('*')), \
                f"Suspicious WAGGLE_API_KEY usage: {hit}"
