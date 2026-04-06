"""Hologram brain visualization route — /hologram view + /api/hologram/state.

v6: 32 nodes (core 10 + MAGMA 5 + system 8 + learning 9),
    node_meta with state/device/freshness/source_class/quality,
    no fake activation floors.
"""

import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import psutil
from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse

from waggledance.adapters.http.deps import get_autonomy_service, get_container
from waggledance.adapters.http.routes._capability_state import (
    CAPABILITY_NODE_IDS,
    CapabilityInfo,
    derive_capability_state,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["hologram"])

_HOLOGRAM_PATH = Path(__file__).resolve().parents[4] / "web" / "hologram-brain-v6.html"
_HOLOGRAM_HTML: str | None = None

# All 32 canonical node IDs
ALL_NODE_IDS = (
    # Core cognition (10)
    "sensory", "waggle_memory", "episodic_memory", "semantic_memory",
    "working_memory", "llm_core", "reason_layer", "causal_map",
    "decision", "user_model",
    # MAGMA ring (5)
    "magma_audit", "magma_trust", "magma_event_log", "magma_replay",
    "magma_provenance",
    # System ring (8)
    "sys_auth", "sys_policy", "sys_api", "sys_websocket",
    "sys_feeds", "sys_queues", "sys_storage", "sys_compute",
    # Learning ring (9 = 4 new + 5 micro repositioned)
    "learn_night", "learn_dream", "learn_training", "learn_canary",
    "micro_train", "micro_route", "micro_anom", "micro_therm", "micro_stats",
)

# Timestamp of last runtime stats call (for freshness)
_last_stats_time: float = 0.0


def _load_html() -> str:
    """Lazy-load the hologram HTML (cached after first read)."""
    global _HOLOGRAM_HTML
    if _HOLOGRAM_HTML is None:
        _HOLOGRAM_HTML = _HOLOGRAM_PATH.read_text(encoding="utf-8")
    return _HOLOGRAM_HTML


@router.get("/hologram", response_class=HTMLResponse)
async def hologram_view():
    """Serve hologram page. No secrets. No injection."""
    return HTMLResponse(_load_html())


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def _gpu_info_safe() -> dict:
    """Get GPU utilization via nvidia-smi — never crashes."""
    try:
        import subprocess
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3,
        )
        if r.returncode == 0:
            parts = r.stdout.strip().split(", ")
            return {
                "gpu_percent": float(parts[0]) if len(parts) > 0 else 0,
                "gpu_mem_used": int(parts[1]) if len(parts) > 1 else 0,
                "gpu_mem_total": int(parts[2]) if len(parts) > 2 else 0,
            }
    except Exception:
        pass
    return {"gpu_percent": 0, "gpu_mem_used": 0, "gpu_mem_total": 0}


def _recent_activity(rs: dict, cap_id: str) -> float:
    """Extract recent activity count for a capability.

    Returns normalized 0..1 value.  If runtime doesn't expose
    per-capability activity counters, returns 0 (dark = idle).
    """
    activity = (
        rs.get("solver_router", {})
        .get("recent_activity", {})
        .get(cap_id, 0)
    )
    return activity / 5.0 if activity else 0.0


def _compute_freshness(node_id: str, rs: dict, stats_time: float) -> Optional[int]:
    """Compute freshness_s for a node — seconds since last relevant update."""
    if not stats_time:
        return None
    elapsed = int(time.time() - stats_time)
    return max(0, elapsed)


def _zero_state() -> Dict[str, Any]:
    """32-node zero-state: all 0.0, all meta unavailable, no edges, no events."""
    zero_meta = {
        "state": "unavailable",
        "device": None,
        "freshness_s": None,
        "source_class": None,
        "quality": None,
    }
    return {
        "nodes": {k: 0.0 for k in ALL_NODE_IDS},
        "node_meta": {k: dict(zero_meta) for k in ALL_NODE_IDS},
        "edges": {},
        "events": [],
    }


def _derive_node_meta(
    node_id: str,
    runtime: Any,
    rs: dict,
    cap_states: Dict[str, CapabilityInfo],
    stats_time: float,
) -> dict:
    """Derive per-node metadata from runtime state."""
    meta: Dict[str, Any] = {
        "state": "idle",
        "device": None,
        "freshness_s": None,
        "source_class": "live",
        "quality": None,
    }

    if not runtime or not getattr(runtime, "is_running", False):
        return {
            "state": "unavailable", "device": None,
            "freshness_s": None, "source_class": None, "quality": None,
        }

    # ── System ring (8 nodes) ───────────────────────────────────
    if node_id == "sys_auth":
        has_key = bool(rs.get("api_key_configured", False))
        has_middleware = bool(rs.get("auth_middleware_active", False))
        meta["state"] = "idle" if (has_key and has_middleware) else "failed"
        meta["source_class"] = "live"

    elif node_id == "sys_policy":
        policy = rs.get("policy_engine", {})
        if not policy:
            meta["state"] = "unwired"
        elif not policy.get("constitution_version"):
            meta["state"] = "framework"
        elif policy.get("pending_approval", 0) > 0:
            meta["state"] = "active"
        else:
            meta["state"] = "idle"
        meta["source_class"] = "live"

    elif node_id == "sys_api":
        lifecycle = rs.get("lifecycle", {})
        lc_state = lifecycle.get("state", "STOPPED")
        if isinstance(lc_state, str):
            lc_state = lc_state.upper()
        if lc_state == "RUNNING":
            meta["state"] = "idle"
        elif lc_state == "DEGRADED":
            meta["state"] = "failed"
        else:
            meta["state"] = "unavailable"
        meta["source_class"] = "live"

    elif node_id == "sys_websocket":
        try:
            from waggledance.adapters.http.routes.compat_dashboard import _ws_clients
            client_count = len(_ws_clients)
        except ImportError:
            client_count = 0
        meta["state"] = "active" if client_count > 0 else "idle"
        meta["source_class"] = "live"

    elif node_id == "sys_feeds":
        feeds = rs.get("feeds", {})
        if not feeds:
            meta["state"] = "unwired"
        else:
            feed_list = feeds.get("feeds", {})
            any_active = any(f.get("active") for f in feed_list.values()) if feed_list else False
            all_errored = (
                all(f.get("error_count", 0) > 0 for f in feed_list.values())
                if feed_list else False
            )
            if all_errored and feed_list:
                meta["state"] = "failed"
            elif any_active:
                meta["state"] = "active"
            else:
                meta["state"] = "idle"
        meta["source_class"] = "live"

    elif node_id == "sys_queues":
        admission = rs.get("admission", {})
        if not admission:
            meta["state"] = "unwired"
        elif admission.get("queue_depth", 0) > 0:
            meta["state"] = "active"
        else:
            meta["state"] = "idle"
        meta["source_class"] = "live"

    elif node_id == "sys_storage":
        persist = rs.get("persistence", {})
        if not persist:
            meta["state"] = "unwired"
        else:
            healthy = persist.get("healthy_stores", 0)
            total = persist.get("total_stores", 0)
            io_in_flight = persist.get("io_in_flight", 0)
            if total == 0:
                meta["state"] = "unwired"
            elif healthy < total:
                meta["state"] = "failed"
            elif io_in_flight > 0:
                meta["state"] = "active"
            else:
                meta["state"] = "idle"
        meta["source_class"] = "live"

    elif node_id == "sys_compute":
        gpu = _gpu_info_safe()
        if gpu.get("gpu_percent", 0) > 0:
            meta["state"] = "active"
            meta["device"] = "GPU"
        elif gpu.get("gpu_mem_total", 0) > 0:
            meta["state"] = "idle"
            meta["device"] = "GPU"
        else:
            meta["state"] = "idle"
            meta["device"] = "CPU"
        meta["source_class"] = "live"

    # ── Learning ring (4 new nodes) ──────────────────────────────
    elif node_id == "learn_dream":
        dream = rs.get("dream_mode", {})
        meta["state"] = "active" if dream.get("active") else "idle"
        meta["source_class"] = "simulated" if dream.get("active") else "live"

    elif node_id == "learn_night":
        night = rs.get("night_pipeline", {})
        meta["state"] = "active" if night.get("running") else "idle"
        meta["source_class"] = "live"

    elif node_id == "learn_training":
        trainer = rs.get("specialist_trainer", {})
        active = trainer.get("active_trainers", 0)
        meta["state"] = "active" if active > 0 else "idle"
        meta["device"] = "CPU"
        meta["source_class"] = "live"

    elif node_id == "learn_canary":
        trainer = rs.get("specialist_trainer", {})
        meta["state"] = "active" if trainer.get("canary_active") else "idle"
        meta["source_class"] = "live"

    # ── Capability-backed nodes ──────────────────────────────────
    elif node_id in CAPABILITY_NODE_IDS:
        info = cap_states.get(node_id)
        if info:
            meta["state"] = info.state
            meta["device"] = info.device
            meta["quality"] = info.quality
            meta["source_class"] = info.source_class

    # ── Core cognition nodes (not capability-backed) ─────────────
    # sensory, waggle_memory, episodic_memory, semantic_memory,
    # working_memory, causal_map, user_model — state is idle (running)
    # MAGMA nodes — state is idle (running)
    # Default meta is already idle/live which is correct for these

    # Freshness
    meta["freshness_s"] = _compute_freshness(node_id, rs, stats_time)

    return meta


def build_hologram_state(service, *, container=None) -> Dict[str, Any]:
    """Synthesize hologram payload from AutonomyRuntime stats.

    Returns 32 nodes, 32 node_meta entries, edges, and events.
    """
    global _last_stats_time

    runtime = service._runtime
    if not runtime or not runtime.is_running:
        return _zero_state()

    stats_time = time.time()
    _last_stats_time = stats_time

    # Gather component stats safely
    def _stats(attr: str) -> dict:
        obj = getattr(runtime, attr, None)
        if obj is None:
            return {}
        try:
            return obj.stats()
        except Exception:
            return {}

    rk = _stats("resource_kernel") or service._resource_kernel.stats()
    wm_stats = _stats("world_model")
    cb_stats = _stats("case_builder")
    wmem_stats = _stats("working_memory")
    sr_stats = _stats("solver_router")
    vf_stats = _stats("verifier")

    # Full runtime stats for system/learning nodes
    rs = runtime.stats() if runtime.is_running else {}

    # Capability confidence (for quality in node_meta, NOT for activation)
    cc = getattr(runtime, "capability_confidence", None)
    cc_all = cc.get_all() if cc else {}

    # Shared capability state derivation
    cap_states = derive_capability_state(runtime, rs)

    # Ollama/trainer stats for activation formulas
    ollama_stats = rs.get("ollama", {})
    trainer_stats = rs.get("specialist_trainer", {})

    # ── Core cognition ring (10 nodes) — utilization/occupancy ──

    # sensory: current task load (no fake floor)
    active_tasks = rk.get("active_tasks", 0)
    sensory = _clamp(active_tasks / 10.0)

    # World model graph sizes
    graph_stats = wm_stats.get("graph", wm_stats)
    edge_count = graph_stats.get("edges", graph_stats.get("edge_count", 0))
    node_count = graph_stats.get("nodes", graph_stats.get("node_count", 0))
    waggle_memory = _clamp(edge_count / 50.0)     # occupancy (no fake floor)
    causal_map = _clamp(node_count / 30.0)         # occupancy (no fake floor)

    # Case builder -> episodic memory (no fake floor)
    cb_total = cb_stats.get("total", 0)
    episodic_memory = _clamp(cb_total / 20.0)

    # Working memory (no fake floor, no +0.1 offset)
    wm_size = wmem_stats.get("size", 0)
    wm_cap = wmem_stats.get("capacity", 1)
    semantic_memory = _clamp(wm_size / max(wm_cap, 1))
    working_memory = _clamp(wm_size / max(wm_cap, 1) * 0.8)

    # llm_core: current LLM load, NOT quality ratio (rewritten)
    llm_core = _clamp(ollama_stats.get("active_requests", 0) / 3.0)

    # reason_layer: active solver count, NOT gold ratio (rewritten)
    reason_layer = _clamp(sr_stats.get("active_count", 0) / 5.0)

    # decision: recent verification throughput, NOT pass_rate (rewritten)
    decision = _clamp(vf_stats.get("recent_checks", 0) / 5.0)

    # User model
    user_act = 0.0
    try:
        user_ent = runtime.world_model.get_user_entity()
        if user_ent:
            interactions = user_ent.get("interaction_count", 0)
            user_act = _clamp(interactions / 50.0) if interactions else 0.0
    except Exception:
        pass

    # ── MAGMA ring (5 nodes) — throughput/volume ────────────────
    magma_audit_data = rs.get("magma_audit", {})
    magma_trust_data = rs.get("magma_trust", {})
    magma_event_log_data = rs.get("magma_event_log", {})
    magma_replay_data = rs.get("magma_replay", {})
    magma_provenance_data = rs.get("magma_provenance", {})

    # ── Learning ring micro nodes (5 repositioned) — lifecycle activity ──
    # micro_train: active training jobs, NOT cc_mean (rewritten)
    micro_train = _clamp(trainer_stats.get("active_trainers", 0) / 3.0)

    # micro_route/anom/therm/stats: recent activity, NOT confidence (rewritten)
    micro_route = _clamp(_recent_activity(rs, "solve.route"))
    micro_anom = _clamp(_recent_activity(rs, "detect.anomaly"))
    micro_therm = _clamp(_recent_activity(rs, "solve.thermal"))
    micro_stats_val = _clamp(_recent_activity(rs, "solve.stats"))

    # ── System ring (8 nodes) — health/availability ─────────────
    sys_auth_val = 1.0 if (
        rs.get("api_key_configured", False)
        and rs.get("auth_middleware_active", False)
    ) else 0.0

    policy = rs.get("policy_engine", {})
    sys_policy_val = 1.0 if (policy and policy.get("constitution_version")) else 0.0

    lifecycle = rs.get("lifecycle", {})
    lc_state = lifecycle.get("state", "STOPPED")
    if isinstance(lc_state, str):
        lc_state_upper = lc_state.upper()
    else:
        lc_state_upper = "STOPPED"
    healthy_comp = lifecycle.get("healthy_components", 0)
    total_comp = lifecycle.get("total_components", 0)
    sys_api_val = _clamp(healthy_comp / max(total_comp, 1))

    try:
        from waggledance.adapters.http.routes.compat_dashboard import _ws_clients
        ws_count = len(_ws_clients)
    except ImportError:
        ws_count = 0
    sys_websocket_val = _clamp(ws_count / 5.0)

    feeds = rs.get("feeds", {})
    feed_list = feeds.get("feeds", {}) if feeds else {}
    total_feeds = len(feed_list)
    active_healthy = sum(
        1 for f in feed_list.values()
        if f.get("active") and f.get("error_count", 0) == 0
    ) if feed_list else 0
    sys_feeds_val = _clamp(active_healthy / max(total_feeds, 1))

    admission = rs.get("admission", {})
    sys_queues_val = _clamp(admission.get("queue_depth", 0) / 10.0)

    persist = rs.get("persistence", {})
    p_healthy = persist.get("healthy_stores", 0)
    p_total = persist.get("total_stores", 0)
    sys_storage_val = _clamp(p_healthy / max(p_total, 1))

    gpu = _gpu_info_safe()
    cpu_pct = psutil.cpu_percent(interval=0)
    sys_compute_val = _clamp((cpu_pct + gpu.get("gpu_percent", 0)) / 200.0)

    # ── Learning ring (4 new nodes) — lifecycle activity ────────
    night = rs.get("night_pipeline", {})
    dream = rs.get("dream_mode", {})

    learn_night = 1.0 if night.get("running") else 0.0
    learn_dream = 1.0 if dream.get("active") else 0.0
    learn_training = _clamp(trainer_stats.get("active_trainers", 0) / 3.0)
    learn_canary = 1.0 if trainer_stats.get("canary_active") else 0.0

    # ── Build nodes dict (32 entries) ───────────────────────────
    nodes = {
        # Core cognition (10)
        "sensory": round(sensory, 2),
        "waggle_memory": round(waggle_memory, 2),
        "episodic_memory": round(episodic_memory, 2),
        "semantic_memory": round(semantic_memory, 2),
        "working_memory": round(working_memory, 2),
        "llm_core": round(llm_core, 2),
        "reason_layer": round(reason_layer, 2),
        "causal_map": round(causal_map, 2),
        "decision": round(decision, 2),
        "user_model": round(user_act, 2),
        # MAGMA ring (5)
        "magma_audit": round(_clamp(magma_audit_data.get("total_entries", 0) / 100.0), 2),
        "magma_trust": round(_clamp(magma_trust_data.get("total_observations", 0) / 50.0), 2),
        "magma_event_log": round(_clamp(magma_event_log_data.get("total_entries", 0) / 100.0), 2),
        "magma_replay": round(_clamp(magma_replay_data.get("total_missions", 0) / 20.0), 2),
        "magma_provenance": round(_clamp(magma_provenance_data.get("total_entries", 0) / 50.0), 2),
        # System ring (8)
        "sys_auth": round(sys_auth_val, 2),
        "sys_policy": round(sys_policy_val, 2),
        "sys_api": round(sys_api_val, 2),
        "sys_websocket": round(sys_websocket_val, 2),
        "sys_feeds": round(sys_feeds_val, 2),
        "sys_queues": round(sys_queues_val, 2),
        "sys_storage": round(sys_storage_val, 2),
        "sys_compute": round(sys_compute_val, 2),
        # Learning ring (9 = 4 new + 5 micro)
        "learn_night": round(learn_night, 2),
        "learn_dream": round(learn_dream, 2),
        "learn_training": round(learn_training, 2),
        "learn_canary": round(learn_canary, 2),
        "micro_train": round(micro_train, 2),
        "micro_route": round(micro_route, 2),
        "micro_anom": round(micro_anom, 2),
        "micro_therm": round(micro_therm, 2),
        "micro_stats": round(micro_stats_val, 2),
    }

    # ── Build node_meta dict (32 entries) ───────────────────────
    node_meta = {}
    for nid in ALL_NODE_IDS:
        node_meta[nid] = _derive_node_meta(nid, runtime, rs, cap_states, stats_time)

    # ── Build edges ─────────────────────────────────────────────
    edges = {
        # Original core edges
        "sensory->working_memory": round((sensory + working_memory) / 2, 2),
        "working_memory->llm_core": round((working_memory + llm_core) / 2, 2),
        "semantic_memory->llm_core": round((semantic_memory + llm_core) / 2, 2),
        "llm_core->reason_layer": round((llm_core + reason_layer) / 2, 2),
        "reason_layer->causal_map": round((reason_layer + causal_map) / 2, 2),
        "causal_map->micro_train": round((causal_map + micro_train) / 2, 2),
        "causal_map->decision": round((causal_map + decision) / 2, 2),
        "llm_core->decision": round((llm_core + decision) / 2, 2),
        "user_model->working_memory": round((user_act + working_memory) / 2, 2),
        "sensory->magma_audit": round((sensory + nodes["magma_audit"]) / 2, 2),
        "magma_trust->decision": round((nodes["magma_trust"] + decision) / 2, 2),
        # New system ring edges
        "sensory->sys_feeds": round((sensory + sys_feeds_val) / 2, 2),
        "sys_auth->sys_policy": round((sys_auth_val + sys_policy_val) / 2, 2),
        "sys_policy->decision": round((sys_policy_val + decision) / 2, 2),
        "sys_compute->llm_core": round((sys_compute_val + llm_core) / 2, 2),
        "sys_queues->sensory": round((sys_queues_val + sensory) / 2, 2),
        # New learning ring edges
        "learn_night->learn_dream": round((learn_night + learn_dream) / 2, 2),
        "learn_dream->learn_training": round((learn_dream + learn_training) / 2, 2),
        "learn_training->learn_canary": round((learn_training + learn_canary) / 2, 2),
        "learn_canary->micro_train": round((learn_canary + micro_train) / 2, 2),
        # New MAGMA edges
        "magma_audit->magma_replay": round((nodes["magma_audit"] + nodes["magma_replay"]) / 2, 2),
        "magma_provenance->magma_trust": round((nodes["magma_provenance"] + nodes["magma_trust"]) / 2, 2),
    }

    # Active flow events — top 4 most active edges
    sorted_edges = sorted(edges.items(), key=lambda x: x[1], reverse=True)
    events: List[Dict[str, str]] = []
    for edge_key, _val in sorted_edges[:4]:
        src, dst = edge_key.split("->")
        events.append({"from": src, "to": dst})

    # v3.4: Add hybrid retrieval overlay info (additive, no node changes)
    hybrid_info = _hybrid_overlay(service, container)

    # v3.5.5: Hex mesh observatory — additive sections
    hex_mesh_state = _hex_mesh_overlay(service, container)
    magma_timeline = _magma_timeline(service, container)
    ops_state = _ops_overlay(service, rs, container)

    return {
        "nodes": nodes,
        "node_meta": node_meta,
        "edges": edges,
        "events": events,
        "hybrid": hybrid_info,
        "hex_mesh": hex_mesh_state,
        "magma_timeline": magma_timeline,
        "ops": ops_state,
    }


def _hybrid_overlay(service, container=None) -> dict:
    """Build hybrid retrieval overlay for hologram state (additive only)."""
    try:
        if container is None:
            return {"enabled": False}

        hr = container.hybrid_retrieval
        return {
            "enabled": hr.enabled,
            "total_queries": hr._total_queries,
            "local_hits": hr._local_hits,
            "neighbor_hits": hr._neighbor_hits,
            "global_hits": hr._global_hits,
            "llm_fallbacks": hr._llm_fallbacks,
        }
    except Exception:
        return {"enabled": False}


def _hex_mesh_overlay(service, container=None) -> dict:
    """Build hex mesh state for hologram (additive)."""
    try:
        if container is None:
            return {"enabled": False}

        ha = container.hex_neighbor_assist
        if not ha:
            return {"enabled": False}

        cells = ha.get_cell_states() if ha.enabled else []
        active_trace = ha.get_last_trace()
        counters = ha.get_metrics()
        health_stats = ha._health.stats() if ha.enabled else {}

        # Build link list from topology
        links = []
        if ha.enabled:
            registry = ha._registry
            for cell_id, cell_def in registry.cells.items():
                for neighbor in registry.get_neighbor_cells(cell_id):
                    pair = tuple(sorted([cell_id, neighbor.id]))
                    link = {"source": pair[0], "target": pair[1]}
                    if link not in links:
                        links.append(link)

        return {
            "enabled": ha.enabled,
            "cells": cells,
            "links": links,
            "active_trace": active_trace,
            "counters": counters,
            "health": health_stats,
        }
    except Exception:
        return {"enabled": False}


def _magma_timeline(service, container=None) -> list:
    """Build MAGMA timeline for hologram (last N events, summary form)."""
    try:
        if container is None:
            return []

        magma = getattr(container, "magma_audit", None)
        if magma is None:
            return []

        # Get recent entries — limit to last 20
        entries = []
        try:
            recent = magma.recent(limit=20) if hasattr(magma, "recent") else []
            for e in recent:
                entry = e if isinstance(e, dict) else (
                    e.to_dict() if hasattr(e, "to_dict") else
                    {"event_type": getattr(e, "event_type", "unknown"),
                     "source": getattr(e, "source", ""),
                     "timestamp": getattr(e, "timestamp", 0)}
                )
                # Sanitize — no secrets
                entry.pop("api_key", None)
                entry.pop("token", None)
                entries.append(entry)
        except Exception:
            pass

        return entries
    except Exception:
        return []


def _ops_overlay(service, rs: dict, container=None) -> dict:
    """Build ops overlay for hologram."""
    try:

        # LLM parallel stats
        parallel = {}
        try:
            pd = container.parallel_dispatcher if container else None
            if pd and hasattr(pd, "stats"):
                parallel = pd.stats()
        except Exception:
            pass

        # Hex mesh counters
        hex_counters = {}
        try:
            ha = container.hex_neighbor_assist if container else None
            if ha:
                hex_counters = ha.get_metrics()
        except Exception:
            pass

        # Cache stats from hot cache
        cache = {}
        try:
            hc = container.hot_cache if container else None
            if hc and hasattr(hc, "stats"):
                cache = hc.stats()
            elif hc:
                cache = {
                    "size": len(hc) if hasattr(hc, "__len__") else 0,
                }
        except Exception:
            pass

        # Request counters from runtime stats
        request_counters = {
            "total_queries": rs.get("total_queries", 0),
            "solver_hits": rs.get("solver_router", {}).get("total_solved", 0),
            "llm_calls": rs.get("ollama", {}).get("total_requests", 0),
        }

        return {
            "llm_parallel": parallel,
            "hex_mesh": hex_counters,
            "cache": cache,
            "request_counters": request_counters,
        }
    except Exception:
        return {}


@router.get("/api/hologram/state")
def hologram_state(service=Depends(get_autonomy_service), container=Depends(get_container)):
    """Return hologram brain state synthesized from runtime stats."""
    return build_hologram_state(service, container=container)
