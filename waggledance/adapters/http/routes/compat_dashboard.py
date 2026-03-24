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

from waggledance.adapters.http.deps import get_autonomy_service, get_container
from waggledance.adapters.http.routes._capability_state import derive_capability_state
from waggledance.adapters.http.routes.auth_session import validate_session

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

def _flexhw_section(container) -> dict:
    """Build FlexHW hardware detection section from ElasticScaler."""
    try:
        scaler = container.elastic_scaler
        hw = scaler.hardware
        tier_cfg = scaler.tier
        tier_name = tier_cfg.tier

        from core.elastic_scaler import TIERS
        tier_order = ["minimal", "light", "standard", "professional", "enterprise"]
        tiers_list = []
        for i, t in enumerate(tier_order):
            spec = TIERS[t]
            tiers_list.append({
                "name": t,
                "vram_gb": spec["min_vram_gb"],
                "model": spec["chat_model"] or "none",
            })

        active_idx = tier_order.index(tier_name) if tier_name in tier_order else 0

        return {
            "tier": tier_name,
            "reason": tier_cfg.reason,
            "gpu_name": hw.gpu_name or "none",
            "gpu_vram_gb": round(hw.gpu_vram_gb, 1),
            "gpu_vram_used_pct": round(scaler.get_vram_usage_pct(), 1),
            "cpu_name": hw.cpu_name,
            "cpu_cores": hw.cpu_cores,
            "ram_gb": round(hw.ram_gb, 1),
            "disk_free_gb": round(hw.disk_free_gb, 1),
            "tiers": tiers_list,
            "active_tier_index": active_idx,
        }
    except Exception as exc:
        logger.debug("FlexHW section failed: %s", exc)
        return {}


def _throttle_section(container) -> dict:
    """Build throttle section from AdaptiveThrottle."""
    try:
        throttle = container.adaptive_throttle
        return throttle.get_status()
    except Exception as exc:
        logger.debug("Throttle section failed: %s", exc)
        return {}


@router.get("/api/ops")
def api_ops(service=Depends(get_autonomy_service),
            container=Depends(get_container)):
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
        "flexhw": _flexhw_section(container),
        "throttle": _throttle_section(container),
        "recommendation": {
            "throttle": "none" if rk.get("load_level") in ("idle", "light") else "active",
            "night_mode": rk.get("night_mode", False),
        },
    }


# ── /api/feeds ────────────────────────────────────────────

FEED_AGENT_IDS = {
    "weather": "weather_feed",
    "electricity": "electricity_feed",
    "rss": "rss_feed",
}


def _is_request_authenticated(request: Request) -> bool:
    """Check if request has valid session cookie or Bearer token."""
    sid = request.cookies.get("waggle_session", "")
    if validate_session(sid):
        return True
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        try:
            container = request.app.state.container
            return auth_header[7:] == container._settings.api_key
        except Exception:
            pass
    return False


def _derive_feed_state(feed_type: str) -> tuple[str, str | None]:
    """Derive state for a feed source. Returns (state, last_error)."""
    import importlib.util

    module_map = {
        "weather": "integrations.weather_feed",
        "electricity": "integrations.electricity_feed",
        "rss": "integrations.rss_feed",
    }
    module_name = module_map.get(feed_type)
    if not module_name:
        return "unwired", f"Unknown feed type: {feed_type}"

    spec = importlib.util.find_spec(module_name)
    if spec is None:
        return "unwired", f"{module_name} not found"

    # Check dependencies
    if feed_type == "rss":
        try:
            import feedparser  # noqa: F401
        except ImportError:
            return "framework", "feedparser not installed"

    return "idle", None


def _build_feed_sources(feeds_cfg: dict) -> list[dict]:
    """Build source list from feeds configuration."""
    sources = []

    # Weather
    weather_cfg = feeds_cfg.get("weather", {})
    if weather_cfg.get("enabled", True):
        state, last_error = _derive_feed_state("weather")
        sources.append({
            "id": "weather_fmi",
            "name": "FMI Weather",
            "type": "weather",
            "provider": "fmi",
            "protocol": "HTTPS/REST",
            "interval_min": weather_cfg.get("interval_min", 30),
            "critical": False,
            "enabled": True,
            "configured": True,
            "state": state,
            "source_class": "live",
            "freshness_s": None,
            "last_success_at": None,
            "last_error_at": None,
            "last_error": last_error,
            "items_count": 0,
            "latest_value": None,
            "latest_items": [],
        })

    # Electricity
    elec_cfg = feeds_cfg.get("electricity", {})
    if elec_cfg.get("enabled", True):
        state, last_error = _derive_feed_state("electricity")
        sources.append({
            "id": "electricity_porssisahko",
            "name": "Spot Electricity",
            "type": "electricity",
            "provider": "porssisahko",
            "protocol": "HTTPS/REST",
            "interval_min": elec_cfg.get("interval_min", 15),
            "critical": False,
            "enabled": True,
            "configured": True,
            "state": state,
            "source_class": "live",
            "freshness_s": None,
            "last_success_at": None,
            "last_error_at": None,
            "last_error": last_error,
            "items_count": 0,
            "latest_value": None,
            "latest_items": [],
        })

    # RSS feeds
    rss_cfg = feeds_cfg.get("rss", {})
    if rss_cfg.get("enabled", True):
        rss_feeds = rss_cfg.get("feeds", [])
        state, last_error = _derive_feed_state("rss")
        for feed in rss_feeds:
            name = feed.get("name", "Unknown RSS")
            feed_id = "rss_" + name.lower().replace("-", "_").replace(" ", "_")
            sources.append({
                "id": feed_id,
                "name": name,
                "type": "rss",
                "url": feed.get("url", ""),
                "protocol": "RSS/Atom",
                "interval_min": rss_cfg.get("interval_min", 60),
                "critical": feed.get("critical", False),
                "enabled": True,
                "configured": True,
                "state": state,
                "source_class": "live",
                "freshness_s": None,
                "last_success_at": None,
                "last_error_at": None,
                "last_error": last_error,
                "items_count": 0,
                "latest_value": None,
                "latest_items": [],
            })

    return sources


def _parse_ts(ts_str: str) -> float:
    """Parse ISO timestamp string to epoch float."""
    from datetime import datetime, timezone
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return dt.timestamp()
    except Exception:
        return 0.0


def _parse_latest_value(feed_type: str, doc: str, meta: dict) -> dict | None:
    """Extract structured latest_value from ChromaDB document."""
    import json as _json
    try:
        val = meta.get("latest_value")
        if val and isinstance(val, str):
            return _json.loads(val)
        if val and isinstance(val, dict):
            return val
    except Exception:
        pass
    # Fallback: parse from document text
    if feed_type == "weather":
        return None  # Can't reliably parse Finnish weather text
    if feed_type == "electricity":
        return None
    return None


def _enrich_from_chroma(source: dict, chroma_collection) -> None:
    """Add latest_items/latest_value from ChromaDB. Truthful: empty if no data.

    Uses BOTH agent_id (feed type) AND feed_id (source-specific) to ensure
    each RSS source gets only its own entries — not a shared pool.
    """
    agent_id = FEED_AGENT_IDS.get(source["type"])
    if not agent_id:
        return
    feed_id = source["id"]
    try:
        # Query with source-level filter: agent_id + feed_id
        where_filter = {"$and": [
            {"agent_id": agent_id},
            {"feed_id": feed_id},
        ]}
        results = chroma_collection.get(
            where=where_filter,
            limit=5,
            include=["documents", "metadatas"],
        )
        docs = results.get("documents", [])
        metas = results.get("metadatas", [])

        # Fallback: if no feed_id-tagged entries, try agent_id only
        # (backwards compat with data stored before feed_id was added)
        # NOT for RSS — prevents cross-contamination between sources
        if not docs and source["type"] not in ("rss",):
            results = chroma_collection.get(
                where={"agent_id": agent_id},
                limit=5,
                include=["documents", "metadatas"],
            )
            docs = results.get("documents", [])
            metas = results.get("metadatas", [])

        source["items_count"] = len(docs)
        if docs:
            # Derive freshness from most recent timestamp
            timestamps = [m.get("timestamp") for m in metas if m.get("timestamp")]
            if timestamps:
                latest_ts = max(timestamps)
                source["last_success_at"] = latest_ts
                source["freshness_s"] = int(time.time() - _parse_ts(latest_ts))
            # Type-specific enrichment
            if source["type"] == "rss":
                source["latest_items"] = [
                    {"title": d[:120], "published": m.get("timestamp")}
                    for d, m in zip(docs[:5], metas[:5])
                ]
            elif source["type"] in ("weather", "electricity"):
                source["latest_value"] = _parse_latest_value(
                    source["type"], docs[0], metas[0]
                )
    except Exception:
        pass  # Leave defaults (truthful empty state)


def _get_chroma_collection(container):
    """Get ChromaDB collection for feed enrichment."""
    try:
        vs = container.vector_store
        if hasattr(vs, "_collection"):
            return vs._collection
        if hasattr(vs, "collection"):
            return vs.collection
    except Exception:
        pass
    return None


@router.get("/api/feeds")
def api_feeds(request: Request, service=Depends(get_autonomy_service),
              container=Depends(get_container)):
    """Data feeds for hologram feeds menu — config-based with ChromaDB enrichment."""
    settings = container._settings
    feeds_cfg = settings.get("feeds") if hasattr(settings, "get") else {}
    if feeds_cfg is None:
        feeds_cfg = {}

    # Build sources from config
    sources = _build_feed_sources(feeds_cfg)

    # Enrich with ChromaDB data if authenticated
    is_authed = _is_request_authenticated(request)
    if is_authed:
        chroma = _get_chroma_collection(container)
        if chroma:
            for source in sources:
                _enrich_from_chroma(source, chroma)

    # Verifier alerts (existing)
    rs = _runtime_stats(service)
    vf = rs.get("verifier", {})
    alerts = []
    if vf.get("hallucinations", 0) > 0:
        alerts.append(f"Hallucinations detected: {vf['hallucinations']}")
    if vf.get("conflicts", 0) > 0:
        alerts.append(f"Verifier conflicts: {vf['conflicts']}")

    return {
        "enabled": feeds_cfg.get("enabled", False),
        "source_count": len(sources),
        "sources": sources,
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
    # Validate token query param or session cookie before accepting
    container = websocket.app.state.container
    expected_key = container._settings.api_key
    token = websocket.query_params.get("token", "")
    session_id = websocket.cookies.get("waggle_session", "")
    if token != expected_key and not validate_session(session_id):
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


# ── /api/settings ────────────────────────────────────────

from pathlib import Path

_SETTINGS_YAML_PATH = Path("configs/settings.yaml")

# Feature keys that can be toggled via POST /api/settings/toggle
_TOGGLEABLE = {
    "feeds.enabled", "feeds.weather.enabled", "feeds.electricity.enabled",
    "feeds.rss.enabled", "mqtt.enabled", "home_assistant.enabled",
    "frigate.enabled", "alerts.enabled", "voice.enabled", "audio.enabled",
    "micro_model.v2.enabled", "micro_model.v3.enabled",
}


def _load_settings_yaml() -> dict:
    if not _SETTINGS_YAML_PATH.exists():
        return {}
    try:
        import yaml
        return yaml.safe_load(_SETTINGS_YAML_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _get_nested(d: dict, path: str):
    for k in path.split("."):
        if isinstance(d, dict):
            d = d.get(k)
        else:
            return None
    return d


def _set_nested(d: dict, path: str, value):
    keys = path.split(".")
    for k in keys[:-1]:
        if k not in d or not isinstance(d[k], dict):
            d[k] = {}
        d = d[k]
    d[keys[-1]] = value


@router.get("/api/settings")
def api_settings():
    """Return current feature toggles from settings.yaml."""
    cfg = _load_settings_yaml()
    toggles = {}
    for path in sorted(_TOGGLEABLE):
        val = _get_nested(cfg, path)
        toggles[path] = bool(val) if val is not None else False
    return {
        "toggles": toggles,
        "elastic_scaling": cfg.get("elastic_scaling", {}),
        "heartbeat_interval": cfg.get("hivemind", {}).get("heartbeat_interval", 30),
    }


from pydantic import BaseModel


class _SettingsToggleBody(BaseModel):
    key: str
    value: bool


@router.post("/api/settings/toggle")
def api_settings_toggle(body: _SettingsToggleBody,
                         request: Request):
    """Toggle a feature on/off. Requires authentication."""
    if not _is_request_authenticated(request):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    key = body.key
    value = body.value

    if key not in _TOGGLEABLE:
        return {"error": f"Key '{key}' is not toggleable", "allowed": sorted(_TOGGLEABLE)}

    import os
    import tempfile
    import yaml

    cfg = _load_settings_yaml()
    _set_nested(cfg, key, value)

    # Atomic write
    fd, tmp = tempfile.mkstemp(dir=str(_SETTINGS_YAML_PATH.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True,
                      sort_keys=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, str(_SETTINGS_YAML_PATH))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

    return {"ok": True, "key": key, "value": value}
