"""Compatibility bridge: legacy dashboard endpoints for hologram menus.

Maps hexagonal AutonomyService stats → legacy /api/* JSON formats
so that the hologram-brain-v6 HTML menus populate correctly.
"""

import asyncio
import json
import logging
import time

import psutil
from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect

from waggledance.adapters.http.deps import get_autonomy_service
from waggledance.adapters.http.routes._capability_state import derive_capability_state

logger = logging.getLogger(__name__)

router = APIRouter(tags=["compat-dashboard"])


# ── Helpers ──────────────────────────────────────────────

def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def _runtime_stats(service):
    """Get full runtime stats tree, or empty dict."""
    rt = getattr(service, "_runtime", None)
    if rt and getattr(rt, "is_running", False):
        try:
            return rt.stats()
        except Exception:
            pass
    return {}


def _gpu_info():
    """Get GPU utilization via nvidia-smi."""
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


# ── /api/status ──────────────────────────────────────────

@router.get("/api/status")
def api_status(service=Depends(get_autonomy_service)):
    """Legacy status endpoint for hologram overview menu."""
    st = service.get_status()
    rk = st.get("resource_kernel", {})
    rt = st.get("runtime", {})
    lifecycle = st.get("lifecycle", {})

    return {
        "status": "running" if lifecycle.get("state") == "running" else "initializing",
        "profile": st.get("profile", "HOME"),
        "uptime_s": lifecycle.get("uptime_s", 0),
        "load_level": rk.get("load_level", "idle"),
        "active_tasks": rk.get("active_tasks", 0),
        "tier": rk.get("tier", "standard"),
        "requests": st.get("requests", 0),
        "errors": st.get("errors", 0),
        "healthy_components": lifecycle.get("healthy_components", 0),
        "total_components": lifecycle.get("total_components", 0),
        "night_mode": rk.get("night_mode", False),
    }


# ── /api/system ──────────────────────────────────────────

@router.get("/api/system")
def api_system(service=Depends(get_autonomy_service)):
    """System hardware stats for hologram system menu."""
    gpu = _gpu_info()
    mem = psutil.virtual_memory()

    return {
        "cpu_percent": psutil.cpu_percent(interval=0.1),
        "gpu_percent": gpu["gpu_percent"],
        "memory_percent": mem.percent,
        "memory_used_gb": round(mem.used / (1024 ** 3), 1),
        "memory_total_gb": round(mem.total / (1024 ** 3), 1),
        "gpu_mem_used_mb": gpu["gpu_mem_used"],
        "gpu_mem_total_mb": gpu["gpu_mem_total"],
        "cpu_count": psutil.cpu_count(logical=True),
    }


# ── /api/consciousness ───────────────────────────────────

@router.get("/api/consciousness")
def api_consciousness(service=Depends(get_autonomy_service)):
    """Consciousness/memory stats for hologram menus."""
    rs = _runtime_stats(service)
    wm = rs.get("world_model", {})
    vf = rs.get("verifier", {})
    cb = rs.get("cases", {})
    wmem = rs.get("working_memory", {})

    graph = wm.get("graph", wm)
    node_count = graph.get("nodes", graph.get("node_count", 0))
    edge_count = graph.get("edges", graph.get("edge_count", 0))

    # User model data (v3.3)
    try:
        user = service._runtime.world_model.get_user_entity() or {}
    except Exception:
        user = {}
    # GoalEngine is source of truth for promise count
    promise_count = 0
    try:
        promise_count = len(service._runtime.goal_engine.get_promises_to_user())
    except Exception:
        pass

    return {
        "memory_count": node_count,
        "episodes_count": cb.get("total", 0),
        "corrections_count": vf.get("conflicts", 0),
        "hallucination_rate": vf.get("hallucinations", 0),
        "uncertainty_score": round(1.0 - vf.get("pass_rate", 1.0), 3),
        "active_learning_count": wmem.get("size", 0),
        "graph_nodes": node_count,
        "graph_edges": edge_count,
        "user_interaction_count": user.get("interaction_count", 0),
        "user_correction_count": user.get("explicit_correction_count", 0),
        "user_promises_pending": promise_count,
    }


# ── /api/learning ────────────────────────────────────────

@router.get("/api/learning")
def api_learning(service=Depends(get_autonomy_service)):
    """Learning pipeline stats for hologram learning menu."""
    rs = _runtime_stats(service)
    cb = rs.get("cases", {})
    cc = rs.get("capability_confidence", {})

    grades = cb.get("grades", {})
    lowest = cc.get("lowest", [])

    leaderboard = []
    for cap_id, conf in lowest:
        leaderboard.append({
            "model_id": cap_id,
            "name": cap_id,
            "accuracy": round(conf, 3),
        })

    return {
        "status": {
            "queue_size": grades.get("quarantine", 0),
            "pending": grades.get("bronze", 0),
            "trained_models": grades.get("gold", 0) + grades.get("silver", 0),
        },
        "leaderboard": leaderboard,
        "gold_rate": cb.get("gold_rate", 0),
        "total_cases": cb.get("total", 0),
    }


# ── /api/micro_model ─────────────────────────────────────

@router.get("/api/micro_model")
def api_micro_model(service=Depends(get_autonomy_service)):
    """Micromodel stats for hologram micro-model menu."""
    rt = getattr(service, "_runtime", None)
    cc = getattr(rt, "capability_confidence", None) if rt else None
    cc_all = cc.get_all() if cc else {}
    cc_mean = (sum(cc_all.values()) / len(cc_all)) if cc_all else 0.0

    return {
        "available": bool(cc_all),
        "stats": {
            "route": round(cc_all.get("solve.route", cc_all.get("solve.general", cc_mean)), 3),
            "route_accuracy": round(cc_all.get("solve.route", cc_all.get("solve.general", cc_mean)), 3),
            "anomaly": round(cc_all.get("detect.anomaly", cc_mean), 3),
            "anomaly_accuracy": round(cc_all.get("detect.anomaly", cc_mean), 3),
            "thermal": round(cc_all.get("solve.thermal", cc_mean), 3),
            "thermal_accuracy": round(cc_all.get("solve.thermal", cc_mean), 3),
            "stats": round(cc_all.get("solve.stats", cc_mean), 3),
            "stats_accuracy": round(cc_all.get("solve.stats", cc_mean), 3),
            "mean_confidence": round(cc_mean, 3),
        },
        "tracked": len(cc_all),
        "all_capabilities": {k: round(v, 3) for k, v in cc_all.items()},
    }


# ── /api/ops ──────────────────────────────────────────────

@router.get("/api/ops")
def api_ops(service=Depends(get_autonomy_service)):
    """Ops status for hologram ops menu."""
    st = service.get_status()
    rk = st.get("resource_kernel", {})
    adm = st.get("admission", {})
    kpis = _safe(lambda: service.get_kpis(), {})

    return {
        "status": {
            "load": rk.get("load_level", "idle"),
            "tier": rk.get("tier", "standard"),
            "active_tasks": rk.get("active_tasks", 0),
            "queue_depth": adm.get("queue_depth", 0),
            "accepted": adm.get("accepted", 0),
            "deferred": adm.get("deferred", 0),
            "rejected": adm.get("rejected", 0),
            "confidence": kpis.get("route_accuracy", {}).get("value", 0),
            "health": 1.0 if rk.get("load_level") != "critical" else 0.5,
        },
        "recommendation": {
            "throttle": "none" if rk.get("load_level") in ("idle", "light") else "active",
            "night_mode": rk.get("night_mode", False),
        },
    }


# ── /api/feeds ────────────────────────────────────────────

@router.get("/api/feeds")
def api_feeds(service=Depends(get_autonomy_service)):
    """Data feeds for hologram feeds menu."""
    rs = _runtime_stats(service)
    vf = rs.get("verifier", {})

    alerts = []
    if vf.get("hallucinations", 0) > 0:
        alerts.append(f"Hallucinations detected: {vf['hallucinations']}")
    if vf.get("conflicts", 0) > 0:
        alerts.append(f"Verifier conflicts: {vf['conflicts']}")

    return {
        "enabled": True,
        "critical_alerts": alerts,
    }


# ── /api/agent_levels ─────────────────────────────────────

@router.get("/api/agent_levels")
def api_agent_levels(service=Depends(get_autonomy_service)):
    """Agent level badges for hologram reasoning menu."""
    rs = _runtime_stats(service)
    caps = rs.get("capabilities", {})

    # Build levels from capability registry
    registered = caps.get("registered", 0)
    executors = caps.get("bound_executors", 0)

    return {
        "levels": {
            "L1_reactive": registered,
            "L2_deliberative": executors,
            "L3_autonomous": min(registered, executors),
        },
        "total_capabilities": registered,
        "total_executors": executors,
    }


# ── /api/swarm/scores ────────────────────────────────────

@router.get("/api/swarm/scores")
def api_swarm_scores(service=Depends(get_autonomy_service)):
    """Swarm coordination scores for hologram reasoning menu."""
    rs = _runtime_stats(service)
    sr = rs.get("solver_router", {})
    qd = sr.get("quality_distribution", {})

    scores = []
    for quality, count in qd.items():
        scores.append({"quality": quality, "count": count})

    return {
        "scores": scores,
        "total_routed": sr.get("total", 0),
        "avg_time_ms": sr.get("avg_time_ms", 0),
    }


# ── /api/monitor/history ─────────────────────────────────

@router.get("/api/monitor/history")
def api_monitor_history(service=Depends(get_autonomy_service)):
    """Monitor event history for hologram feeds menu."""
    rs = _runtime_stats(service)
    audit = rs.get("magma_audit", {})
    event_log = rs.get("magma_event_log", {})

    events = []
    total_events = event_log.get("total", audit.get("total", 0))
    if total_events > 0:
        events.append({
            "type": "system",
            "message": f"Total events logged: {total_events}",
            "timestamp": time.time(),
        })

    return {
        "events": events,
    }


# ── Profile target mapping ────────────────────────────────
_PROFILE_TARGETS = {
    "GADGET": "embedded",
    "COTTAGE": "low-power",
    "HOME": "general",
    "FACTORY": "industrial",
}


def _derive_protocols(rk: dict) -> list:
    """Derive active protocols from resource kernel stats."""
    protocols = []
    if rk.get("http_active"):
        protocols.append("HTTP")
    if rk.get("ws_active") or rk.get("websocket_active"):
        protocols.append("WebSocket")
    if rk.get("mqtt_active"):
        protocols.append("MQTT")
    return protocols or ["HTTP"]


def _derive_feeds(st: dict) -> list:
    """Derive active feed names from status."""
    feeds_data = st.get("feeds", {})
    feed_list = feeds_data.get("feeds", {})
    return [
        name for name, info in feed_list.items()
        if info.get("active")
    ] if feed_list else []


def _derive_learning_perms(rk: dict) -> dict:
    """Derive learning permission flags from resource kernel."""
    return {
        "night_pipeline": rk.get("night_mode_allowed", True),
        "dream_mode": rk.get("dream_mode_allowed", True),
        "specialist_training": rk.get("training_allowed", True),
    }


# ── /api/profile/impact ─────────────────────────────────

@router.get("/api/profile/impact")
def api_profile_impact(service=Depends(get_autonomy_service)):
    """Profile impact summary for hologram overview panel."""
    st = service.get_status()
    profile = st.get("profile", "HOME")
    rk = st.get("resource_kernel", {})
    lifecycle = st.get("lifecycle", {})
    caps = st.get("capabilities", {})

    registered = caps.get("registered", {})
    cap_names = list(registered.keys())[:20] if isinstance(registered, dict) else []

    return {
        "loaded_profile": profile,
        "effective_profile": profile,
        "source": "config",
        "target_environment": _PROFILE_TARGETS.get(
            profile.upper() if isinstance(profile, str) else "HOME", "general"
        ),
        "enabled_capabilities": cap_names,
        "disabled_capabilities": caps.get("disabled", []),
        "active_protocols": _derive_protocols(rk),
        "active_feeds": _derive_feeds(st),
        "risk_mode": rk.get("risk_mode", "standard"),
        "learning_permissions": _derive_learning_perms(rk),
    }


# ── /api/capabilities/state ─────────────────────────────

@router.get("/api/capabilities/state")
def api_capabilities_state(service=Depends(get_autonomy_service)):
    """Per-family capability state — uses shared derive_capability_state().

    Same derivation as hologram node_meta. Single source of truth.
    """
    rt = getattr(service, "_runtime", None)
    rs = {}
    if rt and getattr(rt, "is_running", False):
        try:
            rs = rt.stats()
        except Exception:
            pass
    states = derive_capability_state(rt, rs)
    return {
        nid: {
            "state": info.state,
            "device": info.device,
            "quality": info.quality,
            "source_class": info.source_class,
        }
        for nid, info in states.items()
    }


# ── /api/learning/state-machine ──────────────────────────

@router.get("/api/learning/state-machine")
def api_learning_state_machine(service=Depends(get_autonomy_service)):
    """Current learning lifecycle state."""
    rs = _runtime_stats(service)
    night = rs.get("night_pipeline", {})
    dream = rs.get("dream_mode", {})
    trainer = rs.get("specialist_trainer", {})

    # Determine current state from runtime flags
    if trainer.get("canary_active"):
        state = "canary"
    elif trainer.get("active_trainers", 0) > 0:
        state = "training"
    elif dream.get("active"):
        state = "dream"
    elif night.get("consolidating"):
        state = "consolidation"
    elif night.get("replaying"):
        state = "replay"
    elif night.get("running"):
        state = "morning_report"
    else:
        state = "awake"

    return {
        "state": state,
        "night_pipeline_running": night.get("running", False),
        "dream_active": dream.get("active", False),
        "active_trainers": trainer.get("active_trainers", 0),
        "canary_active": trainer.get("canary_active", False),
    }


# ── WS broadcast helper ─────────────────────────────────

async def broadcast_ws(message: dict):
    """Broadcast a message to all connected WebSocket clients."""
    dead = set()
    for ws in _ws_clients:
        try:
            await ws.send_json(message)
        except Exception:
            dead.add(ws)
    _ws_clients.difference_update(dead)


# ── /ws WebSocket ─────────────────────────────────────────

# Connected clients for broadcasting
_ws_clients: set = set()


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket for real-time dashboard updates.

    BaseHTTPMiddleware does NOT intercept WebSocket upgrades, so
    token auth must be checked here, not in the auth middleware.
    """
    # Validate token query param before accepting
    container = websocket.app.state.container
    expected_key = container._settings.api_key
    token = websocket.query_params.get("token", "")
    if token != expected_key:
        await websocket.close(code=4001, reason="Unauthorized")
        return

    await websocket.accept()
    _ws_clients.add(websocket)
    logger.info("Hologram WebSocket connected (%d clients)", len(_ws_clients))

    try:
        # Get service from app state
        container = websocket.app.state.container
        service = container.autonomy_service

        while True:
            # Send periodic updates every 3 seconds
            try:
                # System stats
                gpu = _gpu_info()
                mem = psutil.virtual_memory()
                system_data = {
                    "cpu_percent": psutil.cpu_percent(interval=0),
                    "gpu_percent": gpu["gpu_percent"],
                    "memory_percent": mem.percent,
                }

                await websocket.send_json({
                    "type": "system",
                    "data": system_data,
                })

                # Brain state from hologram endpoint
                from waggledance.adapters.http.routes.hologram import build_hologram_state
                brain_state = build_hologram_state(service)

                await websocket.send_json({
                    "type": "brain_update",
                    "brain": brain_state,
                })

                await asyncio.sleep(3)

            except WebSocketDisconnect:
                break
            except Exception as exc:
                logger.debug("WS send error: %s", exc)
                break
    finally:
        _ws_clients.discard(websocket)
        logger.info("Hologram WebSocket disconnected (%d clients)", len(_ws_clients))
