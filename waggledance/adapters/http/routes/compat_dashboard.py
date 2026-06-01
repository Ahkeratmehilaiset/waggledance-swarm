"""Compatibility bridge: legacy dashboard endpoints for hologram menus.

Maps hexagonal AutonomyService stats → legacy /api/* JSON formats
so that the hologram-brain-v6 HTML menus populate correctly.
"""

import asyncio
from collections.abc import Mapping, Sequence
import json
import logging
import math
import re
import time
from datetime import datetime
from pathlib import Path

import psutil
from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from waggledance.adapters.http.deps import get_autonomy_service, get_container
from waggledance.adapters.http.routes._capability_state import derive_capability_state
from waggledance.adapters.http.routes._dashboard_shared import _ws_clients
from waggledance.adapters.http.routes.auth_session import validate_session
from waggledance.adapters.http.routes.chat import CHAT_ROUTE_STAGE_ORDER
from waggledance.core.magma.share_manifest import (
    build_magma_share_import_handoff_status_summary,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["compat-dashboard"])


# ── Helpers ──────────────────────────────────────────────

def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def _detect_degraded(request: Request) -> list[str]:
    """Detect degraded components from circuit breaker state."""
    degraded = []
    try:
        container = request.app.state.container
        llm = container.llm
        # OllamaAdapter: circuit breaker is open if _circuit_open_since is set
        if getattr(llm, "_circuit_open_since", None) is not None:
            degraded.append("llm")
        vs = container.vector_store
        # ChromaVectorStore: circuit breaker state
        breaker = getattr(vs, "_breaker", None)
        if breaker and getattr(breaker, "state", "closed") != "closed":
            degraded.append("embeddings")
    except Exception:
        pass
    return degraded


def _hybrid_status(request: Request) -> dict:
    """Get hybrid retrieval status from container."""
    try:
        container = request.app.state.container
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
def api_status(
    request: Request,
    service=Depends(get_autonomy_service),
):
    """Legacy status endpoint for hologram overview menu."""
    st = service.get_status()
    rk = st.get("resource_kernel", {})
    rt = st.get("runtime", {})
    lifecycle = st.get("lifecycle", {})

    # Derive degraded-mode flags from adapter circuit breaker state
    degraded_components = _detect_degraded(request)

    # v3.4: hybrid retrieval status
    hybrid_info = _hybrid_status(request)

    # v3.5: backfill + candidate lab summary
    container = request.app.state.container
    backfill_summary = _safe(lambda: {
        "indexed_ids": container.hybrid_backfill.status().get("indexed_ids_count", 0),
        "total_runs": container.hybrid_backfill.status().get("total_runs", 0),
    }, {"indexed_ids": 0, "total_runs": 0})
    candidate_lab_summary = _safe(lambda: {
        "total_candidates": container.solver_candidate_lab.registry.count(),
        "total_analyses": container.solver_candidate_lab.status().get("total_analyses", 0),
    }, {"total_candidates": 0, "total_analyses": 0})

    # v3.5.1: gemma profile metrics
    gemma_metrics = _safe(lambda: container.gemma_router.get_metrics(), {"enabled": False})

    # v3.5.2: parallel LLM dispatch metrics
    parallel_metrics = _safe(
        lambda: container.parallel_dispatcher.get_metrics(), {"enabled": False})

    # v3.5.4: hex neighbor mesh metrics
    hex_mesh_metrics = _safe(
        lambda: container.hex_neighbor_assist.get_metrics(), {"enabled": False})

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
        "degraded": len(degraded_components) > 0,
        "degraded_components": degraded_components,
        "hybrid_retrieval": hybrid_info,
        "backfill": backfill_summary,
        "candidate_lab": candidate_lab_summary,
        "gemma_profiles": gemma_metrics,
        "llm_parallel": parallel_metrics,
        "hex_mesh": hex_mesh_metrics,
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
def api_learning(
    request: Request,
    service=Depends(get_autonomy_service),
):
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

    # Derive LLM degraded flag for truthful learning status
    degraded = _detect_degraded(request)
    llm_degraded = "llm" in degraded

    return {
        "status": {
            "queue_size": grades.get("quarantine", 0),
            "pending": grades.get("bronze", 0),
            "trained_models": grades.get("gold", 0) + grades.get("silver", 0),
        },
        "leaderboard": leaderboard,
        "gold_rate": cb.get("gold_rate", 0),
        "total_cases": cb.get("total", 0),
        "llm_degraded": llm_degraded,
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


def _autogrowth_disabled_section() -> dict:
    section = {
        "enabled": False,
        "up": False,
        "running": False,
        "interval_seconds": None,
        "max_ticks_per_wake": None,
        "wakeups_total": 0,
        "non_idle_ticks": 0,
        "errors_total": 0,
    }
    return _with_autogrowth_alert_state(section)


def _safe_getattr(obj, name: str, default=None):  # noqa: ANN001
    try:
        return getattr(obj, name, default)
    except Exception:
        return default


def _number_or_none(value):  # noqa: ANN001
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        return None
    return normalized if math.isfinite(normalized) else None


def _int_or_zero(value) -> int:  # noqa: ANN001
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _autogrowth_alert_state(section: dict) -> dict:
    """Build read-only alert status from the local Ops snapshot."""

    active_alerts = []
    if not section.get("up", False):
        active_alerts.append({
            "id": "AutogrowthSourceDown",
            "severity": "warning",
            "summary": "Autogrowth metrics source is unavailable.",
        })

    if _int_or_zero(section.get("errors_total", 0)) > 0:
        active_alerts.append({
            "id": "AutogrowthErrorsObserved",
            "severity": "warning",
            "summary": "Autogrowth ticker errors have been observed.",
        })

    return {
        "status": "warning" if active_alerts else "nominal",
        "severity": "warning" if active_alerts else "none",
        "source": "local_ops_snapshot",
        "prometheus_alertmanager_feed": False,
        "active_count": len(active_alerts),
        "active": active_alerts,
        "deferred_rules": [
            "AutogrowthErrorBurst",
            "AutogrowthWakeupStalled",
            "AutogrowthWakeupBurst",
            "AutogrowthNonIdleBurst",
        ],
        "controls_present": False,
    }


def _with_autogrowth_alert_state(section: dict) -> dict:
    section = dict(section)
    section["alert_state"] = _autogrowth_alert_state(section)
    return section


ROUTE_STAGE_LATENCY_PANEL_QUERIES = {
    "route_stage_latency_p95_ms": (
        "histogram_quantile(0.95, sum by (le, stage) "
        "(rate(waggledance_route_stage_request_latency_histogram_ms_bucket[5m])))"
    ),
    "route_stage_latency_p99_ms": (
        "histogram_quantile(0.99, sum by (le, stage) "
        "(rate(waggledance_route_stage_request_latency_histogram_ms_bucket[5m])))"
    ),
    "route_stage_request_rate": (
        "sum by (stage) "
        "(rate(waggledance_route_stage_observations_total[5m]))"
    ),
}

ROUTE_STAGE_LATENCY_PANEL_META = {
    "route_stage_latency_p95_ms": {
        "title": "Route-stage p95 latency",
        "unit": "ms",
        "warning_ms": 2500.0,
    },
    "route_stage_latency_p99_ms": {
        "title": "Route-stage p99 latency",
        "unit": "ms",
        "critical_ms": 5000.0,
    },
    "route_stage_request_rate": {
        "title": "Route-stage request rate",
        "unit": "requests/s",
    },
}

ROUTE_STAGE_LATENCY_ALERT_IDS = {
    "RouteStageLatencyP95Warning": "warning",
    "RouteStageLatencyP99Critical": "critical",
}

ROUTE_STAGE_LATENCY_SEVERITY_RANK = {
    "none": 0,
    "nominal": 0,
    "warning": 1,
    "critical": 2,
}
ROUTE_STAGE_LATENCY_UPDATED_AT_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
    r"(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$"
)
ROUTE_STAGE_LATENCY_FEED_HEALTH_REASONS = frozenset({
    "none",
    "BACKOFF_ACTIVE",
    "NETWORK_TIMEOUT",
    "NETWORK_REQUEST_FAILED",
    "RESPONSE_SHAPE_REFUSED",
    "RESPONSE_BODY_REFUSED",
    "RESPONSE_STATUS_REFUSED",
    "RESPONSE_JSON_REFUSED",
    "RESPONSE_TOO_LARGE",
    "RESPONSE_CONTENT_TYPE_REFUSED",
    "RESPONSE_SOURCE_URL_REFUSED",
    "PROMETHEUS_STATUS_REFUSED",
    "PROMETHEUS_DATA_REFUSED",
    "PROMETHEUS_RESULT_REFUSED",
    "ALERTMANAGER_RESULT_REFUSED",
    "ROUTE_STAGE_LATENCY_FEED_UNAVAILABLE",
    "HTTP_STATUS_REFUSED",
    "FEED_READ_FAILED",
})
ROUTE_STAGE_LATENCY_FEED_SLO_PANELS = (
    {
        "id": "route_stage_latency_feed_availability_5m",
        "title": "Route-stage latency feed availability",
        "metric": "waggledance_route_stage_latency_feed_available",
        "query": (
            "avg_over_time("
            "waggledance_route_stage_latency_feed_available[5m])"
        ),
        "window": "5m",
        "objective": "available == 1",
    },
    {
        "id": "route_stage_latency_feed_fetch_failures_15m",
        "title": "Route-stage latency feed fetch failures",
        "metric": "waggledance_route_stage_latency_feed_fetch_failures_total",
        "query": (
            "increase("
            "waggledance_route_stage_latency_feed_fetch_failures_total[15m])"
        ),
        "window": "15m",
        "objective": "increase == 0",
    },
    {
        "id": "route_stage_latency_feed_backoff_15m",
        "title": "Route-stage latency feed backoff active",
        "metric": "waggledance_route_stage_latency_feed_backoff_active",
        "query": (
            "max_over_time("
            "waggledance_route_stage_latency_feed_backoff_active[15m])"
        ),
        "window": "15m",
        "objective": "max == 0",
    },
    {
        "id": "route_stage_latency_feed_cache_stale_15m",
        "title": "Route-stage latency feed cache stale",
        "metric": "waggledance_route_stage_latency_feed_cache_stale",
        "query": (
            "max_over_time("
            "waggledance_route_stage_latency_feed_cache_stale[15m])"
        ),
        "window": "15m",
        "objective": "max == 0",
    },
)


def _route_stage_latency_empty_feed_state(source: str) -> dict:
    return {
        "source": source,
        "status": "not_configured",
        "severity": "none",
        "prometheus_alertmanager_feed": False,
        "updated_at": None,
        "panel_values": [],
        "active_count": 0,
        "active": [],
        "controls_present": False,
        "feed_health": _route_stage_latency_feed_health_default(source),
    }


def _route_stage_latency_feed_health_default(source: str) -> dict:
    configured = source != "not_configured"
    return {
        "source": source,
        "status": "not_configured" if not configured else "warning",
        "configured": configured,
        "available": False,
        "cache_enabled": False,
        "cache_present": False,
        "cache_stale": False,
        "backoff_active": False,
        "cache_ttl_seconds": 0.0,
        "failure_backoff_seconds": 0.0,
        "timeout_seconds": 0.0,
        "max_response_bytes": 0.0,
        "last_response_bytes": 0.0,
        "cache_hit_count": 0.0,
        "cache_miss_count": 0.0,
        "fetch_success_count": 0.0,
        "fetch_failure_count": 0.0,
        "backoff_skip_count": 0.0,
        "last_success_at": None,
        "last_failure_at": None,
        "last_failure_reason": None,
        "controls_present": False,
        "runtime_authority_granted": False,
        "external_writes_applied": False,
    }


def _route_stage_latency_feed_reason(reason) -> str | None:  # noqa: ANN001
    if not isinstance(reason, str) or not reason.strip():
        return None
    raw = reason.strip()
    if raw.lower() == "none":
        return "none"
    clean = raw.upper()
    if clean.startswith("HTTP_STATUS_"):
        return "HTTP_STATUS_REFUSED"
    if clean in ROUTE_STAGE_LATENCY_FEED_HEALTH_REASONS:
        return clean
    return "FEED_READ_FAILED"


def _route_stage_latency_feed_health(provider, snapshot=None) -> dict:  # noqa: ANN001
    if provider is None:
        return _route_stage_latency_feed_health_default("not_configured")
    raw_health = None
    if isinstance(snapshot, Mapping):
        raw_health = snapshot.get("provider_health")
    if not isinstance(raw_health, Mapping):
        method = _safe_getattr(provider, "provider_health", None)
        if callable(method):
            try:
                raw_health = method()
            except Exception:
                raw_health = None
    if not isinstance(raw_health, Mapping):
        raw_health = {}

    health = _route_stage_latency_feed_health_default(
        "prometheus_alertmanager_adapter"
    )
    for key in (
        "configured",
        "available",
        "cache_enabled",
        "cache_present",
        "cache_stale",
        "backoff_active",
    ):
        if key in raw_health:
            health[key] = bool(raw_health.get(key))
    for key in (
        "cache_ttl_seconds",
        "failure_backoff_seconds",
        "timeout_seconds",
        "max_response_bytes",
        "last_response_bytes",
        "cache_hit_count",
        "cache_miss_count",
        "fetch_success_count",
        "fetch_failure_count",
        "backoff_skip_count",
    ):
        value = _number_or_none(raw_health.get(key))
        if value is not None and value >= 0:
            health[key] = round(value, 3)
    for key in ("last_success_at", "last_failure_at"):
        value = raw_health.get(key)
        health[key] = (
            _route_stage_latency_updated_at({"updated_at": value})
            if isinstance(value, str)
            else None
        )
    health["last_failure_reason"] = _route_stage_latency_feed_reason(
        raw_health.get("last_failure_reason")
    )
    raw_status = raw_health.get("status")
    if raw_status in {"not_configured", "nominal", "warning"}:
        health["status"] = raw_status
    elif health["backoff_active"] or health["fetch_failure_count"] > 0:
        health["status"] = "warning"
    elif health["configured"]:
        health["status"] = "nominal"
    return health


def _route_stage_latency_feed_nonnegative_float(value) -> float:  # noqa: ANN001
    numeric = _number_or_none(value)
    if numeric is None or numeric < 0:
        return 0.0
    return float(numeric)


def _route_stage_latency_feed_slo_panel_status(
    panel_id: str,
    feed_health: Mapping[str, object],
) -> str:
    if not feed_health.get("configured"):
        return "not_configured"
    if panel_id == "route_stage_latency_feed_availability_5m":
        return "nominal" if feed_health.get("available") else "warning"
    if panel_id == "route_stage_latency_feed_fetch_failures_15m":
        failures = _route_stage_latency_feed_nonnegative_float(
            feed_health.get("fetch_failure_count")
        )
        return "warning" if failures > 0 else "nominal"
    if panel_id == "route_stage_latency_feed_backoff_15m":
        return "warning" if feed_health.get("backoff_active") else "nominal"
    if panel_id == "route_stage_latency_feed_cache_stale_15m":
        return "warning" if feed_health.get("cache_stale") else "nominal"
    return "nominal"


def _route_stage_latency_feed_slo_current_value(
    panel_id: str,
    feed_health: Mapping[str, object],
) -> float:
    if panel_id == "route_stage_latency_feed_availability_5m":
        return 1.0 if feed_health.get("available") else 0.0
    if panel_id == "route_stage_latency_feed_fetch_failures_15m":
        return _route_stage_latency_feed_nonnegative_float(
            feed_health.get("fetch_failure_count")
        )
    if panel_id == "route_stage_latency_feed_backoff_15m":
        return 1.0 if feed_health.get("backoff_active") else 0.0
    if panel_id == "route_stage_latency_feed_cache_stale_15m":
        return 1.0 if feed_health.get("cache_stale") else 0.0
    return 0.0


def _route_stage_latency_feed_slo_panels(
    feed_health: Mapping[str, object],
) -> list[dict[str, object]]:
    return [
        {
            **panel,
            "current_value": _route_stage_latency_feed_slo_current_value(
                str(panel["id"]),
                feed_health,
            ),
            "status": _route_stage_latency_feed_slo_panel_status(
                str(panel["id"]),
                feed_health,
            ),
            "controls_present": False,
        }
        for panel in ROUTE_STAGE_LATENCY_FEED_SLO_PANELS
    ]


def _route_stage_latency_feed_drill_evidence() -> dict[str, object]:
    return {
        "source": "operator_runbook",
        "required_artifacts": [
            {
                "id": "metrics_scrape",
                "source": "/metrics",
                "fields": [
                    "waggledance_route_stage_latency_feed_status",
                    "waggledance_route_stage_latency_feed_failure_reason",
                    "waggledance_route_stage_latency_feed_backoff_active",
                    "waggledance_route_stage_latency_feed_cache_stale",
                ],
            },
            {
                "id": "ops_snapshot",
                "source": "/api/ops",
                "fields": [
                    "route_stage_latency.feed_state.feed_health",
                    "route_stage_latency.feed_state.slo_panels",
                ],
            },
            {
                "id": "runtime_window_logs",
                "source": "operator_log_window",
                "fields": ["timestamp", "commit", "sanitized_reason"],
            },
        ],
        "privacy_exclusions": [
            "urls",
            "hosts",
            "headers",
            "filesystem_paths",
            "exception_text",
            "raw_queries",
            "raw_labels",
            "annotations",
        ],
        "controls_present": False,
        "runtime_authority_granted": False,
        "external_writes_applied": False,
    }


def _route_stage_latency_feed_provider(container):  # noqa: ANN001
    for name in (
        "route_stage_latency_feed",
        "route_stage_latency_alert_feed",
        "prometheus_alertmanager_feed",
    ):
        provider = _safe_getattr(container, name, None)
        if provider is not None:
            return provider
    return None


def _route_stage_latency_feed_snapshot(container):  # noqa: ANN001
    provider = _route_stage_latency_feed_provider(container)
    if provider is None:
        return None, "not_configured", None
    if isinstance(provider, dict):
        return provider, "", provider
    for method_name in ("snapshot", "get_status", "status"):
        method = _safe_getattr(provider, method_name, None)
        if callable(method):
            try:
                snapshot = method()
            except Exception:
                return None, "unavailable", provider
            return (
                snapshot if isinstance(snapshot, dict) else None,
                "" if isinstance(snapshot, dict) else "invalid",
                provider,
            )
    return None, "invalid", provider


def _route_stage_latency_stage(item: dict) -> str | None:
    labels = item.get("labels")
    if not isinstance(labels, dict):
        labels = {}
    stage = item.get("stage", labels.get("stage"))
    if isinstance(stage, str) and stage in CHAT_ROUTE_STAGE_ORDER:
        return stage
    return None


def _route_stage_latency_value(item: dict) -> float | None:
    for key in ("value_ms", "value", "current_value"):
        value = _number_or_none(item.get(key))
        if value is not None:
            return round(value, 3)
    return None


def _route_stage_latency_updated_at(snapshot: dict) -> str | None:
    value = snapshot.get("updated_at")
    if not isinstance(value, str) or len(value) > 40 or value.strip() != value:
        return None
    if ROUTE_STAGE_LATENCY_UPDATED_AT_RE.fullmatch(value) is None:
        return None
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value


def _route_stage_latency_panel_status(panel_id: str, value: float) -> str:
    meta = ROUTE_STAGE_LATENCY_PANEL_META.get(panel_id, {})
    critical = _number_or_none(meta.get("critical_ms"))
    if critical is not None and value > critical:
        return "critical"
    warning = _number_or_none(meta.get("warning_ms"))
    if warning is not None and value > warning:
        return "warning"
    return "nominal"


def _sanitize_route_stage_latency_panel_values(snapshot: dict) -> list[dict]:
    raw_items = snapshot.get("panel_values", snapshot.get("panels", []))
    if not isinstance(raw_items, list):
        return []
    items = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        panel_id = raw.get("id", raw.get("panel_id"))
        if (
            not isinstance(panel_id, str)
            or panel_id not in ROUTE_STAGE_LATENCY_PANEL_META
        ):
            continue
        stage = _route_stage_latency_stage(raw)
        value = _route_stage_latency_value(raw)
        if stage is None or value is None:
            continue
        meta = ROUTE_STAGE_LATENCY_PANEL_META[panel_id]
        items.append({
            "id": panel_id,
            "title": meta["title"],
            "stage": stage,
            "value": value,
            "unit": meta["unit"],
            "status": _route_stage_latency_panel_status(panel_id, value),
        })
    return items


def _sanitize_route_stage_latency_active_alerts(snapshot: dict) -> list[dict]:
    raw_alerts = snapshot.get(
        "active",
        snapshot.get("active_alerts", snapshot.get("alerts", [])),
    )
    if not isinstance(raw_alerts, list):
        return []
    alerts = []
    for raw in raw_alerts:
        if not isinstance(raw, dict):
            continue
        labels = raw.get("labels")
        if not isinstance(labels, dict):
            labels = {}
        alert_id = raw.get("id", raw.get("alertname", labels.get("alertname")))
        if (
            not isinstance(alert_id, str)
            or alert_id not in ROUTE_STAGE_LATENCY_ALERT_IDS
        ):
            continue
        state = raw.get("state", raw.get("status", raw.get("alert_state")))
        if isinstance(state, str) and state.lower() not in {"active", "firing"}:
            continue
        stage = _route_stage_latency_stage(raw)
        if stage is None:
            continue
        severity = raw.get("severity", labels.get("severity"))
        if severity not in {"warning", "critical"}:
            severity = ROUTE_STAGE_LATENCY_ALERT_IDS[alert_id]
        value = _route_stage_latency_value(raw)
        item = {
            "id": alert_id,
            "stage": stage,
            "severity": severity,
            "summary": f"{alert_id} active for {stage}.",
        }
        if value is not None:
            item["value_ms"] = value
        alerts.append(item)
    return alerts


def _route_stage_latency_max_severity(items: list[dict]) -> str:
    severity = "none"
    for item in items:
        candidate = item.get("severity", item.get("status", "none"))
        if (
            isinstance(candidate, str)
            and ROUTE_STAGE_LATENCY_SEVERITY_RANK.get(candidate, 0)
            > ROUTE_STAGE_LATENCY_SEVERITY_RANK.get(severity, 0)
        ):
            severity = candidate
    return severity


def _route_stage_latency_feed_state_with_operator_evidence(
    state: Mapping[str, object],
    feed_health: Mapping[str, object],
) -> dict:
    return {
        **dict(state),
        "feed_health": dict(feed_health),
        "slo_panels": _route_stage_latency_feed_slo_panels(feed_health),
        "drill_evidence": _route_stage_latency_feed_drill_evidence(),
    }


def _route_stage_latency_feed_state(container) -> dict:  # noqa: ANN001
    snapshot, state, provider = _route_stage_latency_feed_snapshot(container)
    feed_health = _route_stage_latency_feed_health(provider, snapshot)
    if state == "not_configured":
        return _route_stage_latency_feed_state_with_operator_evidence(
            _route_stage_latency_empty_feed_state("not_configured"),
            feed_health,
        )
    if snapshot is None:
        return _route_stage_latency_feed_state_with_operator_evidence(
            {
                **_route_stage_latency_empty_feed_state(
                    "prometheus_alertmanager_unavailable"
                ),
                "status": "warning",
                "severity": "warning",
                "active_count": 1,
                "active": [{
                    "id": "RouteStageLatencyFeedUnavailable",
                    "severity": "warning",
                    "summary": "Route-stage latency feed snapshot is unavailable.",
                }],
            },
            feed_health,
        )

    panel_values = _sanitize_route_stage_latency_panel_values(snapshot)
    active = _sanitize_route_stage_latency_active_alerts(snapshot)
    severity = _route_stage_latency_max_severity(active + panel_values)
    return _route_stage_latency_feed_state_with_operator_evidence(
        {
            "source": "prometheus_alertmanager_snapshot",
            "status": severity if severity != "none" else "nominal",
            "severity": severity,
            "prometheus_alertmanager_feed": True,
            "updated_at": _route_stage_latency_updated_at(snapshot),
            "panel_values": panel_values,
            "active_count": len(active),
            "active": active,
            "controls_present": False,
        },
        feed_health,
    )


def _route_stage_latency_panels(container=None) -> dict:
    """Read-only PromQL panel and alert templates for route-stage latency."""

    p95_query = ROUTE_STAGE_LATENCY_PANEL_QUERIES[
        "route_stage_latency_p95_ms"
    ]
    p99_query = ROUTE_STAGE_LATENCY_PANEL_QUERIES[
        "route_stage_latency_p99_ms"
    ]
    feed_state = _route_stage_latency_feed_state(container)
    return {
        "source": "prometheus_query_templates",
        "prometheus_alertmanager_feed": feed_state[
            "prometheus_alertmanager_feed"
        ],
        "controls_present": False,
        "feed_state": feed_state,
        "metrics": [
            "waggledance_route_stage_request_latency_histogram_ms_bucket",
            "waggledance_route_stage_request_latency_histogram_ms_sum",
            "waggledance_route_stage_request_latency_histogram_ms_count",
            "waggledance_route_stage_observations_total",
        ],
        "panels": [
            {
                "id": "route_stage_latency_p95_ms",
                "title": "Route-stage p95 latency",
                "unit": "ms",
                "query": p95_query,
            },
            {
                "id": "route_stage_latency_p99_ms",
                "title": "Route-stage p99 latency",
                "unit": "ms",
                "query": p99_query,
            },
            {
                "id": "route_stage_request_rate",
                "title": "Route-stage request rate",
                "unit": "requests/s",
                "query": ROUTE_STAGE_LATENCY_PANEL_QUERIES[
                    "route_stage_request_rate"
                ],
            },
        ],
        "alert_thresholds": [
            {
                "id": "RouteStageLatencyP95Warning",
                "expr": f"{p95_query} > 2500",
                "for": "10m",
                "severity": "warning",
            },
            {
                "id": "RouteStageLatencyP99Critical",
                "expr": f"{p99_query} > 5000",
                "for": "10m",
                "severity": "critical",
            },
        ],
    }


def _autogrowth_section(container) -> dict:
    """Build read-only low-risk autogrowth status for the Ops panel."""
    try:
        ticker = getattr(container, "autogrowth_background_ticker", None)
    except Exception as exc:
        logger.debug("Autogrowth ticker lookup failed: %s", exc)
        return _autogrowth_disabled_section()
    if ticker is None:
        return _autogrowth_disabled_section()

    section = _autogrowth_disabled_section()
    section["enabled"] = True
    section["running"] = bool(_safe_getattr(ticker, "is_running", False))

    interval = _number_or_none(_safe_getattr(ticker, "interval_seconds", None))
    if interval is not None:
        section["interval_seconds"] = interval
    max_ticks = _safe_getattr(ticker, "max_ticks_per_wake", None)
    if max_ticks is not None:
        section["max_ticks_per_wake"] = _int_or_zero(max_ticks)

    stats = _safe_getattr(ticker, "stats", None)
    if stats is None:
        return _with_autogrowth_alert_state(section)

    section["up"] = True
    section["wakeups_total"] = _int_or_zero(
        _safe_getattr(stats, "wakeups_total", 0)
    )
    section["non_idle_ticks"] = _int_or_zero(
        _safe_getattr(stats, "non_idle_ticks", 0)
    )
    section["errors_total"] = _int_or_zero(
        _safe_getattr(stats, "errors_total", 0)
    )
    return _with_autogrowth_alert_state(section)


def _magma_share_import_handoff_snapshot(container):  # noqa: ANN001
    for name in (
        "magma_share_import_handoff_history",
        "magma_share_import_peer_review_handoff_history",
        "magma_share_import_handoff_status",
        "magma_share_import_peer_review_handoff_status",
        "magma_share_import_peer_review_handoff",
    ):
        provider = _safe_getattr(container, name, None)
        if provider is not None:
            break
    else:
        return None, "not_configured"
    if isinstance(provider, Mapping):
        return dict(provider), ""
    if _is_magma_share_import_handoff_history(provider):
        return list(provider), ""
    for method_name in (
        "snapshot",
        "get_status",
        "status",
        "history",
        "get_history",
    ):
        method = _safe_getattr(provider, method_name, None)
        if callable(method):
            try:
                snapshot = method()
            except Exception:
                return None, "unavailable"
            if isinstance(snapshot, Mapping):
                return snapshot, ""
            if _is_magma_share_import_handoff_history(snapshot):
                return list(snapshot), ""
            return None, "invalid"
    return None, "invalid"


def _is_magma_share_import_handoff_history(value) -> bool:  # noqa: ANN001
    return isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    )


def _magma_share_import_handoff_snapshot_kind(snapshot) -> str:  # noqa: ANN001
    if snapshot is None:
        return "none"
    if _is_magma_share_import_handoff_history(snapshot):
        return "history"
    if isinstance(snapshot, Mapping):
        for field in ("history", "handoffs"):
            if _is_magma_share_import_handoff_history(snapshot.get(field)):
                return "history"
        if isinstance(snapshot.get("latest"), Mapping):
            return "latest"
        return "handoff"
    return "invalid"


def _magma_share_import_handoff_snapshot_count(snapshot) -> int:  # noqa: ANN001
    if _is_magma_share_import_handoff_history(snapshot):
        return len(snapshot)
    if isinstance(snapshot, Mapping):
        for field in ("history", "handoffs"):
            value = snapshot.get(field)
            if _is_magma_share_import_handoff_history(value):
                return len(value)
        if isinstance(snapshot.get("latest"), Mapping):
            return 1
        return 1
    return 0


MAGMA_HANDOFF_PROVIDER_FRESHNESS_WARNING_SECONDS = 24 * 60 * 60
MAGMA_HANDOFF_PROVIDER_RETENTION_DROPPED_WARNING_COUNT = 1
MAGMA_HANDOFF_FRESHNESS_SOURCE = "operator_peer_review_handoff_feed"
MAGMA_HANDOFF_FRESHNESS_STATES = frozenset({"fresh", "stale", "unknown"})
MAGMA_HANDOFF_METRICS_ALERT_IDS = {
    "MagmaHandoffMetricsSourceDown": "warning",
    "MagmaHandoffSnapshotInvalid": "warning",
    "MagmaHandoffFreshnessStale": "warning",
    "MagmaHandoffRetentionDropped": "warning",
    "MagmaHandoffPrivateMaterialRecorded": "critical",
    "MagmaHandoffRuntimeAuthorityReported": "critical",
    "MagmaHandoffPayloadImported": "critical",
    "MagmaHandoffProviderUnavailable": "warning",
    "MagmaHandoffFreshnessSourceUnavailable": "warning",
}
MAGMA_HANDOFF_METRICS_ALERT_METRICS = {
    "MagmaHandoffMetricsSourceDown": "waggledance_magma_handoff_provider_up",
    "MagmaHandoffSnapshotInvalid": "waggledance_magma_handoff_snapshot_valid",
    "MagmaHandoffFreshnessStale": (
        "waggledance_magma_handoff_freshness_source_stale"
    ),
    "MagmaHandoffRetentionDropped": (
        "waggledance_magma_handoff_history_dropped_count"
    ),
    "MagmaHandoffPrivateMaterialRecorded": (
        "waggledance_magma_handoff_local_paths_recorded"
    ),
    "MagmaHandoffRuntimeAuthorityReported": (
        "waggledance_magma_handoff_runtime_authority_granted"
    ),
    "MagmaHandoffPayloadImported": (
        "waggledance_magma_handoff_payload_files_imported"
    ),
    "MagmaHandoffProviderUnavailable": (
        "waggledance_magma_handoff_provider_alert_active"
    ),
    "MagmaHandoffFreshnessSourceUnavailable": (
        "waggledance_magma_handoff_provider_alert_active"
    ),
}
MAGMA_HANDOFF_METRICS_ALERT_SUMMARIES = {
    "MagmaHandoffMetricsSourceDown": (
        "MAGMA handoff metrics source is unavailable."
    ),
    "MagmaHandoffSnapshotInvalid": (
        "MAGMA handoff metrics report an invalid snapshot."
    ),
    "MagmaHandoffFreshnessStale": (
        "MAGMA handoff freshness source reports stale state."
    ),
    "MagmaHandoffRetentionDropped": (
        "MAGMA handoff history dropped entries from the retained window."
    ),
    "MagmaHandoffPrivateMaterialRecorded": (
        "MAGMA handoff metrics report private material."
    ),
    "MagmaHandoffRuntimeAuthorityReported": (
        "MAGMA handoff metrics report runtime authority."
    ),
    "MagmaHandoffPayloadImported": (
        "MAGMA handoff metrics report imported payload files."
    ),
    "MagmaHandoffProviderUnavailable": (
        "MAGMA handoff provider alert is active."
    ),
    "MagmaHandoffFreshnessSourceUnavailable": (
        "MAGMA handoff freshness source alert is active."
    ),
}
MAGMA_HANDOFF_METRICS_ALERT_FEED_HEALTH_REASONS = frozenset({
    "BACKOFF_ACTIVE",
    "NETWORK_TIMEOUT",
    "NETWORK_REQUEST_FAILED",
    "RESPONSE_SHAPE_REFUSED",
    "RESPONSE_BODY_REFUSED",
    "RESPONSE_STATUS_REFUSED",
    "RESPONSE_JSON_REFUSED",
    "RESPONSE_TOO_LARGE",
    "RESPONSE_CONTENT_TYPE_REFUSED",
    "RESPONSE_SOURCE_URL_REFUSED",
    "ALERTMANAGER_RESULT_REFUSED",
    "MAGMA_HANDOFF_METRICS_ALERT_FEED_UNAVAILABLE",
    "FEED_READ_FAILED",
})
MAGMA_HANDOFF_METRICS_ALERT_FEED_SLO_PANELS = (
    {
        "id": "magma_alert_feed_availability_5m",
        "title": "MAGMA alert feed availability",
        "metric": "waggledance_magma_handoff_alert_feed_available",
        "query": (
            "avg_over_time("
            "waggledance_magma_handoff_alert_feed_available[5m])"
        ),
        "window": "5m",
        "objective": "available == 1",
    },
    {
        "id": "magma_alert_feed_fetch_failures_15m",
        "title": "MAGMA alert feed fetch failures",
        "metric": "waggledance_magma_handoff_alert_feed_fetch_failures_total",
        "query": (
            "increase("
            "waggledance_magma_handoff_alert_feed_fetch_failures_total[15m])"
        ),
        "window": "15m",
        "objective": "increase == 0",
    },
    {
        "id": "magma_alert_feed_backoff_15m",
        "title": "MAGMA alert feed backoff active",
        "metric": "waggledance_magma_handoff_alert_feed_backoff_active",
        "query": (
            "max_over_time("
            "waggledance_magma_handoff_alert_feed_backoff_active[15m])"
        ),
        "window": "15m",
        "objective": "max == 0",
    },
    {
        "id": "magma_alert_feed_cache_stale_15m",
        "title": "MAGMA alert feed cache stale",
        "metric": "waggledance_magma_handoff_alert_feed_cache_stale",
        "query": (
            "max_over_time("
            "waggledance_magma_handoff_alert_feed_cache_stale[15m])"
        ),
        "window": "15m",
        "objective": "max == 0",
    },
)


def _magma_handoff_metrics_alert_feed_health_default(source: str) -> dict:
    configured = source != "not_configured"
    return {
        "source": source,
        "status": "not_configured" if not configured else "warning",
        "configured": configured,
        "available": False,
        "cache_enabled": False,
        "cache_present": False,
        "cache_stale": False,
        "backoff_active": False,
        "cache_ttl_seconds": 0.0,
        "failure_backoff_seconds": 0.0,
        "timeout_seconds": 0.0,
        "max_response_bytes": 0.0,
        "last_response_bytes": 0.0,
        "cache_hit_count": 0.0,
        "cache_miss_count": 0.0,
        "fetch_success_count": 0.0,
        "fetch_failure_count": 0.0,
        "backoff_skip_count": 0.0,
        "last_success_at": None,
        "last_failure_at": None,
        "last_failure_reason": None,
        "controls_present": False,
        "runtime_authority_granted": False,
        "external_writes_applied": False,
    }


def _magma_handoff_metrics_alert_feed_health(provider, snapshot=None) -> dict:  # noqa: ANN001
    if provider is None:
        return _magma_handoff_metrics_alert_feed_health_default("not_configured")
    raw_health = None
    if isinstance(snapshot, Mapping):
        raw_health = snapshot.get("provider_health")
    if not isinstance(raw_health, Mapping):
        method = _safe_getattr(provider, "provider_health", None)
        if callable(method):
            try:
                raw_health = method()
            except Exception:
                raw_health = None
    if not isinstance(raw_health, Mapping):
        raw_health = {}

    health = _magma_handoff_metrics_alert_feed_health_default(
        "alertmanager_adapter"
    )
    for key in (
        "configured",
        "available",
        "cache_enabled",
        "cache_present",
        "cache_stale",
        "backoff_active",
        "controls_present",
        "runtime_authority_granted",
        "external_writes_applied",
    ):
        if key in raw_health:
            health[key] = bool(raw_health.get(key))
    for key in (
        "cache_ttl_seconds",
        "failure_backoff_seconds",
        "timeout_seconds",
        "max_response_bytes",
        "last_response_bytes",
        "cache_hit_count",
        "cache_miss_count",
        "fetch_success_count",
        "fetch_failure_count",
        "backoff_skip_count",
    ):
        value = _number_or_none(raw_health.get(key))
        if value is not None and value >= 0:
            health[key] = round(value, 3)
    for key in ("last_success_at", "last_failure_at"):
        value = raw_health.get(key)
        health[key] = (
            _route_stage_latency_updated_at({"updated_at": value})
            if isinstance(value, str)
            else None
        )
    reason = raw_health.get("last_failure_reason")
    if isinstance(reason, str) and reason in (
        MAGMA_HANDOFF_METRICS_ALERT_FEED_HEALTH_REASONS
    ):
        health["last_failure_reason"] = reason
    raw_status = raw_health.get("status")
    if raw_status in {"not_configured", "nominal", "warning"}:
        health["status"] = raw_status
    elif health["backoff_active"] or health["fetch_failure_count"] > 0:
        health["status"] = "warning"
    elif health["configured"]:
        health["status"] = "nominal"
    return health


def _magma_handoff_alert_feed_slo_panel_status(
    panel_id: str,
    feed_health: Mapping[str, object],
) -> str:
    if not feed_health.get("configured"):
        return "not_configured"
    if panel_id == "magma_alert_feed_availability_5m":
        return "nominal" if feed_health.get("available") else "warning"
    if panel_id == "magma_alert_feed_fetch_failures_15m":
        failures = _magma_handoff_alert_feed_nonnegative_float(
            feed_health.get("fetch_failure_count")
        )
        return (
            "warning"
            if failures > 0
            else "nominal"
        )
    if panel_id == "magma_alert_feed_backoff_15m":
        return "warning" if feed_health.get("backoff_active") else "nominal"
    if panel_id == "magma_alert_feed_cache_stale_15m":
        return "warning" if feed_health.get("cache_stale") else "nominal"
    return "nominal"


def _magma_handoff_alert_feed_slo_current_value(
    panel_id: str,
    feed_health: Mapping[str, object],
) -> float:
    if panel_id == "magma_alert_feed_availability_5m":
        return 1.0 if feed_health.get("available") else 0.0
    if panel_id == "magma_alert_feed_fetch_failures_15m":
        return _magma_handoff_alert_feed_nonnegative_float(
            feed_health.get("fetch_failure_count")
        )
    if panel_id == "magma_alert_feed_backoff_15m":
        return 1.0 if feed_health.get("backoff_active") else 0.0
    if panel_id == "magma_alert_feed_cache_stale_15m":
        return 1.0 if feed_health.get("cache_stale") else 0.0
    return 0.0


def _magma_handoff_alert_feed_nonnegative_float(value) -> float:  # noqa: ANN001
    numeric = _number_or_none(value)
    if numeric is None or numeric < 0:
        return 0.0
    return float(numeric)


def _magma_handoff_alert_feed_slo_panels(
    feed_health: Mapping[str, object],
) -> list[dict[str, object]]:
    return [
        {
            **panel,
            "current_value": _magma_handoff_alert_feed_slo_current_value(
                str(panel["id"]),
                feed_health,
            ),
            "status": _magma_handoff_alert_feed_slo_panel_status(
                str(panel["id"]),
                feed_health,
            ),
            "controls_present": False,
        }
        for panel in MAGMA_HANDOFF_METRICS_ALERT_FEED_SLO_PANELS
    ]


def _magma_handoff_alert_feed_drill_evidence() -> dict[str, object]:
    return {
        "source": "operator_runbook",
        "required_artifacts": [
            {
                "id": "metrics_scrape",
                "source": "/metrics",
                "fields": [
                    "waggledance_magma_handoff_alert_feed_status",
                    "waggledance_magma_handoff_alert_feed_failure_reason",
                    "waggledance_magma_handoff_alert_feed_backoff_active",
                ],
            },
            {
                "id": "ops_snapshot",
                "source": "/api/ops",
                "fields": [
                    "provider_health.metrics_alert_state.feed_health",
                    "provider_health.metrics_alert_state.slo_panels",
                ],
            },
            {
                "id": "runtime_window_logs",
                "source": "operator_log_window",
                "fields": ["timestamp", "commit", "sanitized_reason"],
            },
        ],
        "privacy_exclusions": [
            "urls",
            "hosts",
            "headers",
            "filesystem_paths",
            "exception_text",
            "raw_alertmanager_labels",
        ],
        "controls_present": False,
        "runtime_authority_granted": False,
        "external_writes_applied": False,
    }


def _magma_share_import_handoff_freshness_snapshot(container):  # noqa: ANN001
    for name in (
        "magma_share_import_handoff_feed_freshness",
        "magma_share_import_peer_review_handoff_feed_freshness",
        "magma_share_import_handoff_freshness_source",
        "magma_share_import_peer_review_handoff_freshness_source",
        "magma_share_import_handoff_feed_state",
        "magma_share_import_peer_review_handoff_feed_state",
    ):
        provider = _safe_getattr(container, name, None)
        if provider is not None:
            break
    else:
        return None, "not_configured"
    if isinstance(provider, Mapping):
        return dict(provider), ""
    for method_name in (
        "snapshot",
        "freshness",
        "get_freshness",
        "get_status",
        "status",
        "state",
    ):
        method = _safe_getattr(provider, method_name, None)
        if callable(method):
            try:
                snapshot = method()
            except Exception:
                return None, "unavailable"
            if isinstance(snapshot, Mapping):
                return dict(snapshot), ""
            return None, "invalid"
    return None, "invalid"


def _magma_share_import_handoff_valid_timestamp(value) -> str | None:  # noqa: ANN001
    if not isinstance(value, str) or len(value) > 40 or value.strip() != value:
        return None
    if ROUTE_STAGE_LATENCY_UPDATED_AT_RE.fullmatch(value) is None:
        return None
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value


def _magma_share_import_handoff_first_timestamp(
    snapshot: Mapping[str, object],
    keys: Sequence[str],
) -> str | None:
    for key in keys:
        value = snapshot.get(key)
        if value is None:
            continue
        timestamp = _magma_share_import_handoff_valid_timestamp(value)
        if timestamp is None:
            return None
        return timestamp
    return None


def _magma_share_import_handoff_nonnegative_int(value) -> int | None:  # noqa: ANN001
    normalized = _number_or_none(value)
    if normalized is None or normalized < 0 or not normalized.is_integer():
        return None
    return int(normalized)


def _magma_share_import_handoff_nonnegative_number(value):  # noqa: ANN001
    normalized = _number_or_none(value)
    if normalized is None or normalized < 0:
        return None
    return int(normalized) if normalized.is_integer() else round(normalized, 3)


def _sanitize_magma_share_import_handoff_freshness_snapshot(
    snapshot: Mapping[str, object],
) -> dict | None:
    latest = _magma_share_import_handoff_first_timestamp(
        snapshot,
        (
            "feed_latest_created_at_utc",
            "latest_created_at_utc",
            "latest_handoff_created_at_utc",
            "created_at_utc",
        ),
    )
    observed = _magma_share_import_handoff_first_timestamp(
        snapshot,
        (
            "feed_observed_at_utc",
            "observed_at_utc",
            "updated_at_utc",
            "updated_at",
        ),
    )
    latest_obj = snapshot.get("latest")
    if latest is None and isinstance(latest_obj, Mapping):
        value = latest_obj.get("created_at_utc")
        if value is not None:
            latest = _magma_share_import_handoff_valid_timestamp(value)
            if latest is None:
                return None

    for key in (
        "feed_latest_created_at_utc",
        "latest_created_at_utc",
        "latest_handoff_created_at_utc",
        "created_at_utc",
    ):
        if key in snapshot and snapshot.get(key) is not None and latest is None:
            return None
    for key in (
        "feed_observed_at_utc",
        "observed_at_utc",
        "updated_at_utc",
        "updated_at",
    ):
        if key in snapshot and snapshot.get(key) is not None and observed is None:
            return None

    raw_state = snapshot.get(
        "staleness_state",
        snapshot.get("freshness_state", snapshot.get("state")),
    )
    if raw_state is None:
        staleness_state = "unknown"
    elif isinstance(raw_state, str) and raw_state in MAGMA_HANDOFF_FRESHNESS_STATES:
        staleness_state = raw_state
    else:
        return None

    item_count = None
    for key in ("feed_item_count", "item_count", "handoff_count", "history_count"):
        if key in snapshot:
            item_count = _magma_share_import_handoff_nonnegative_int(
                snapshot.get(key)
            )
            if item_count is None:
                return None
            break

    window_seconds = None
    for key in (
        "feed_window_seconds",
        "window_seconds",
        "freshness_window_seconds",
    ):
        if key in snapshot:
            window_seconds = _magma_share_import_handoff_nonnegative_number(
                snapshot.get(key)
            )
            if window_seconds is None:
                return None
            break

    if latest is None and observed is None and item_count is None:
        return None

    return {
        "source": MAGMA_HANDOFF_FRESHNESS_SOURCE,
        "latest_created_at_utc": latest,
        "observed_at_utc": observed,
        "item_count": item_count,
        "window_seconds": window_seconds,
        "staleness_state": staleness_state,
    }


def _magma_share_import_handoff_freshness_source(container):  # noqa: ANN001
    snapshot, state = _magma_share_import_handoff_freshness_snapshot(container)
    if state == "not_configured":
        return None, "not_configured"
    if snapshot is None:
        return None, state or "invalid"
    sanitized = _sanitize_magma_share_import_handoff_freshness_snapshot(snapshot)
    if sanitized is None:
        return None, "invalid"
    return sanitized, "valid"


def _magma_share_import_handoff_latest_created_at(
    summary: Mapping[str, object] | None,
) -> str | None:
    latest = summary.get("latest") if summary is not None else None
    if isinstance(latest, Mapping):
        value = latest.get("created_at_utc")
        if isinstance(value, str):
            return value
    return None


def _magma_share_import_handoff_provider_alert_thresholds(
    summary: Mapping[str, object] | None,
    freshness_source: Mapping[str, object] | None = None,
) -> list[dict[str, object]]:
    history_limit = summary.get("history_limit") if summary is not None else None
    feed_latest = (
        freshness_source.get("latest_created_at_utc")
        if freshness_source is not None
        else None
    )
    latest_created_at = (
        feed_latest
        if isinstance(feed_latest, str)
        else _magma_share_import_handoff_latest_created_at(summary)
    )
    freshness_metric_source = (
        f"{MAGMA_HANDOFF_FRESHNESS_SOURCE}.latest_created_at_utc"
        if isinstance(feed_latest, str)
        else "latest.created_at_utc"
    )
    return [
        {
            "id": "MagmaShareImportHandoffProviderFreshnessWarning",
            "severity": "warning",
            "metric": "latest_handoff_age_seconds",
            "source": freshness_metric_source,
            "warning_after_seconds": (
                MAGMA_HANDOFF_PROVIDER_FRESHNESS_WARNING_SECONDS
            ),
            "latest_created_at_utc": latest_created_at,
            "wall_clock_dependent": True,
        },
        {
            "id": "MagmaShareImportHandoffProviderRetentionDropped",
            "severity": "warning",
            "metric": "history_dropped_count",
            "warning_threshold": (
                MAGMA_HANDOFF_PROVIDER_RETENTION_DROPPED_WARNING_COUNT
            ),
        },
        {
            "id": "MagmaShareImportHandoffProviderRetentionLimitReached",
            "severity": "warning",
            "metric": "history_retained_count",
            "warning_threshold": history_limit,
        },
    ]


def _magma_share_import_handoff_provider_retention_alerts(
    summary: Mapping[str, object] | None,
) -> list[dict[str, object]]:
    if summary is None:
        return []
    history_limit = _int_or_zero(summary.get("history_limit"))
    retained = _int_or_zero(summary.get("history_retained_count"))
    dropped = _int_or_zero(summary.get("history_dropped_count"))
    truncated = bool(summary.get("history_truncated"))
    alerts: list[dict[str, object]] = []
    if dropped >= MAGMA_HANDOFF_PROVIDER_RETENTION_DROPPED_WARNING_COUNT:
        alerts.append({
            "id": "MagmaShareImportHandoffProviderRetentionDropped",
            "severity": "warning",
            "summary": "MAGMA handoff history exceeded the retained summary window.",
            "history_dropped_count": dropped,
        })
    if not alerts and truncated:
        alerts.append({
            "id": "MagmaShareImportHandoffProviderRetentionTruncated",
            "severity": "warning",
            "summary": "MAGMA handoff history was truncated for the Ops summary.",
        })
    if not alerts and history_limit > 0 and retained >= history_limit:
        alerts.append({
            "id": "MagmaShareImportHandoffProviderRetentionLimitReached",
            "severity": "warning",
            "summary": "MAGMA handoff history is at the retained summary limit.",
            "history_limit": history_limit,
        })
    return alerts


def _magma_handoff_metrics_alert_empty_state(source: str) -> dict:
    return {
        "source": source,
        "status": "not_configured",
        "severity": "none",
        "prometheus_alertmanager_feed": False,
        "updated_at": None,
        "active_count": 0,
        "active": [],
        "controls_present": False,
    }


def _magma_handoff_metrics_alert_feed_provider(container):  # noqa: ANN001
    for name in (
        "magma_share_import_handoff_metrics_alert_feed",
        "magma_handoff_metrics_alert_feed",
        "magma_share_import_handoff_alert_feed",
        "magma_handoff_alert_feed",
        "magma_handoff_prometheus_alertmanager_feed",
    ):
        provider = _safe_getattr(container, name, None)
        if provider is not None:
            return provider
    return None


def _magma_handoff_metrics_alert_feed_snapshot(container):  # noqa: ANN001
    provider = _magma_handoff_metrics_alert_feed_provider(container)
    if provider is None:
        return None, "not_configured"
    if isinstance(provider, Mapping):
        return dict(provider), ""
    for method_name in ("snapshot", "get_status", "status"):
        method = _safe_getattr(provider, method_name, None)
        if callable(method):
            try:
                snapshot = method()
            except Exception:
                return None, "unavailable"
            if isinstance(snapshot, Mapping):
                return dict(snapshot), ""
            return None, "invalid"
    return None, "invalid"


def _magma_handoff_metrics_alert_feed_status(container):  # noqa: ANN001
    provider = _magma_handoff_metrics_alert_feed_provider(container)
    if provider is None:
        return None, "not_configured", (
            _magma_handoff_metrics_alert_feed_health_default("not_configured")
        )
    if isinstance(provider, Mapping):
        snapshot = dict(provider)
        return snapshot, "", _magma_handoff_metrics_alert_feed_health(
            provider,
            snapshot,
        )
    for method_name in ("snapshot", "get_status", "status"):
        method = _safe_getattr(provider, method_name, None)
        if callable(method):
            try:
                snapshot = method()
            except Exception:
                return None, "unavailable", (
                    _magma_handoff_metrics_alert_feed_health(provider)
                )
            if isinstance(snapshot, Mapping):
                snapshot = dict(snapshot)
                return snapshot, "", _magma_handoff_metrics_alert_feed_health(
                    provider,
                    snapshot,
                )
            return None, "invalid", (
                _magma_handoff_metrics_alert_feed_health(provider)
            )
    return None, "invalid", _magma_handoff_metrics_alert_feed_health(provider)


def _magma_handoff_metrics_alert_id(item: dict) -> str | None:
    labels = item.get("labels")
    if not isinstance(labels, Mapping):
        labels = {}
    alert_id = item.get("id", item.get("alertname", labels.get("alertname")))
    if isinstance(alert_id, str) and alert_id in MAGMA_HANDOFF_METRICS_ALERT_IDS:
        return alert_id
    return None


def _magma_handoff_metrics_alert_value(item: dict) -> float | None:
    for key in ("value", "current_value", "sample_value"):
        value = _number_or_none(item.get(key))
        if value is not None:
            return round(value, 3)
    return None


def _sanitize_magma_handoff_metrics_active_alerts(
    snapshot: dict,
) -> list[dict[str, object]]:
    raw_alerts = snapshot.get(
        "active",
        snapshot.get("active_alerts", snapshot.get("alerts", [])),
    )
    if not isinstance(raw_alerts, list):
        return []
    alerts: list[dict[str, object]] = []
    for raw in raw_alerts:
        if not isinstance(raw, dict):
            continue
        labels = raw.get("labels")
        if not isinstance(labels, Mapping):
            labels = {}
        alert_id = _magma_handoff_metrics_alert_id(raw)
        if alert_id is None:
            continue
        state = raw.get("state", raw.get("status", raw.get("alert_state")))
        if isinstance(state, Mapping):
            state = state.get("state")
        if isinstance(state, str) and state.lower() not in {"active", "firing"}:
            continue
        severity = raw.get("severity", labels.get("severity"))
        if severity not in {"warning", "critical"}:
            severity = MAGMA_HANDOFF_METRICS_ALERT_IDS[alert_id]
        item = {
            "id": alert_id,
            "severity": severity,
            "summary": MAGMA_HANDOFF_METRICS_ALERT_SUMMARIES[alert_id],
            "metric": MAGMA_HANDOFF_METRICS_ALERT_METRICS[alert_id],
        }
        value = _magma_handoff_metrics_alert_value(raw)
        if value is not None:
            item["value"] = value
        alerts.append(item)
    return alerts


def _magma_handoff_metrics_alert_max_severity(items: list[dict]) -> str:
    severity = "none"
    for item in items:
        candidate = item.get("severity", "none")
        if (
            isinstance(candidate, str)
            and ROUTE_STAGE_LATENCY_SEVERITY_RANK.get(candidate, 0)
            > ROUTE_STAGE_LATENCY_SEVERITY_RANK.get(severity, 0)
        ):
            severity = candidate
    return severity


def _magma_handoff_metrics_alert_state(container) -> dict:  # noqa: ANN001
    snapshot, state, feed_health = _magma_handoff_metrics_alert_feed_status(
        container
    )
    if state == "not_configured":
        return {
            **_magma_handoff_metrics_alert_empty_state("not_configured"),
            "feed_health": feed_health,
            "slo_panels": _magma_handoff_alert_feed_slo_panels(feed_health),
            "drill_evidence": _magma_handoff_alert_feed_drill_evidence(),
        }
    if snapshot is None:
        invalid = state == "invalid"
        alert_id = (
            "MagmaHandoffMetricsAlertFeedInvalid"
            if invalid
            else "MagmaHandoffMetricsAlertFeedUnavailable"
        )
        return {
            **_magma_handoff_metrics_alert_empty_state(
                "prometheus_alertmanager_invalid"
                if invalid
                else "prometheus_alertmanager_unavailable"
            ),
            "status": "warning",
            "severity": "warning",
            "active_count": 1,
            "active": [{
                "id": alert_id,
                "severity": "warning",
                "summary": (
                    "MAGMA handoff metrics alert feed snapshot is invalid."
                    if invalid
                    else (
                        "MAGMA handoff metrics alert feed snapshot is "
                        "unavailable."
                    )
                ),
            }],
            "feed_health": feed_health,
            "slo_panels": _magma_handoff_alert_feed_slo_panels(feed_health),
            "drill_evidence": _magma_handoff_alert_feed_drill_evidence(),
        }

    active = _sanitize_magma_handoff_metrics_active_alerts(snapshot)
    severity = _magma_handoff_metrics_alert_max_severity(active)
    return {
        "source": "prometheus_alertmanager_snapshot",
        "status": severity if severity != "none" else "nominal",
        "severity": severity,
        "prometheus_alertmanager_feed": True,
        "updated_at": _route_stage_latency_updated_at(snapshot),
        "active_count": len(active),
        "active": active,
        "feed_health": feed_health,
        "slo_panels": _magma_handoff_alert_feed_slo_panels(feed_health),
        "drill_evidence": _magma_handoff_alert_feed_drill_evidence(),
        "controls_present": False,
    }


def _magma_share_import_handoff_provider_health(
    *,
    reason: str,
    snapshot=None,  # noqa: ANN001
    summary: Mapping[str, object] | None = None,
    freshness_source: Mapping[str, object] | None = None,
    freshness_state: str = "not_configured",
    metrics_alert_state: Mapping[str, object] | None = None,
) -> dict:
    provider_configured = reason != "not_configured"
    snapshot_available = reason in {"valid_snapshot", "snapshot_invalid"}
    snapshot_valid = reason == "valid_snapshot"
    freshness_source_configured = freshness_state != "not_configured"
    freshness_source_available = freshness_state in {"valid", "invalid"}
    freshness_source_valid = freshness_state == "valid"
    feed_latest_created_at = (
        freshness_source.get("latest_created_at_utc")
        if freshness_source_valid and freshness_source is not None
        else None
    )
    feed_observed_at = (
        freshness_source.get("observed_at_utc")
        if freshness_source_valid and freshness_source is not None
        else None
    )
    feed_item_count = (
        freshness_source.get("item_count")
        if freshness_source_valid and freshness_source is not None
        else None
    )
    feed_window_seconds = (
        freshness_source.get("window_seconds")
        if freshness_source_valid and freshness_source is not None
        else None
    )
    feed_staleness_state = (
        freshness_source.get("staleness_state")
        if freshness_source_valid and freshness_source is not None
        else "unknown"
    )
    history_feed_present = (
        snapshot_valid
        and _magma_share_import_handoff_snapshot_kind(snapshot) == "history"
    )
    status = "nominal" if snapshot_valid else "not_configured"
    severity = "none"
    active = []
    if reason in {"provider_unavailable", "snapshot_invalid"}:
        status = "warning"
        severity = "warning"
        alert_id = (
            "MagmaShareImportHandoffProviderUnavailable"
            if reason == "provider_unavailable"
            else "MagmaShareImportHandoffProviderInvalid"
        )
        active.append({
            "id": alert_id,
            "severity": "warning",
            "summary": (
                "MAGMA share import handoff provider is unavailable."
                if reason == "provider_unavailable"
                else "MAGMA share import handoff provider snapshot is invalid."
            ),
        })
    if snapshot_valid:
        active.extend(
            _magma_share_import_handoff_provider_retention_alerts(summary)
        )
    if freshness_source_configured:
        if freshness_state == "unavailable":
            active.append({
                "id": "MagmaShareImportHandoffFreshnessSourceUnavailable",
                "severity": "warning",
                "summary": (
                    "MAGMA handoff freshness source is unavailable."
                ),
            })
        elif not freshness_source_valid:
            active.append({
                "id": "MagmaShareImportHandoffFreshnessSourceInvalid",
                "severity": "warning",
                "summary": "MAGMA handoff freshness source is invalid.",
            })
        elif feed_staleness_state == "stale":
            active.append({
                "id": "MagmaShareImportHandoffProviderFreshnessWarning",
                "severity": "warning",
                "summary": "MAGMA handoff feed freshness source is stale.",
                "feed_latest_created_at_utc": feed_latest_created_at,
                "feed_observed_at_utc": feed_observed_at,
            })
    if active:
        status = (
            "warning"
            if snapshot_valid or freshness_source_configured
            else status
        )
        severity = "warning"
    return {
        "source": "local_ops_snapshot" if provider_configured else "not_configured",
        "status": status,
        "severity": severity,
        "reason": reason,
        "provider_configured": provider_configured,
        "snapshot_available": snapshot_available,
        "snapshot_valid": snapshot_valid,
        "snapshot_kind": _magma_share_import_handoff_snapshot_kind(snapshot),
        "snapshot_count": _magma_share_import_handoff_snapshot_count(snapshot),
        "history_feed_present": history_feed_present,
        "freshness_source": (
            MAGMA_HANDOFF_FRESHNESS_SOURCE
            if freshness_source_configured
            else "not_configured"
        ),
        "freshness_source_configured": freshness_source_configured,
        "freshness_source_available": freshness_source_available,
        "freshness_source_valid": freshness_source_valid,
        "freshness_source_reason": freshness_state,
        "freshness_source_precedence": (
            "operator_feed"
            if freshness_source_valid
            and isinstance(feed_latest_created_at, str)
            else "latest.created_at_utc"
        ),
        "feed_latest_created_at_utc": feed_latest_created_at,
        "feed_observed_at_utc": feed_observed_at,
        "feed_item_count": feed_item_count,
        "feed_window_seconds": feed_window_seconds,
        "feed_staleness_state": feed_staleness_state,
        "history_limit": (
            summary.get("history_limit") if summary is not None else None
        ),
        "history_retained_count": (
            summary.get("history_retained_count") if summary is not None else 0
        ),
        "history_dropped_count": (
            summary.get("history_dropped_count") if summary is not None else 0
        ),
        "history_truncated": (
            bool(summary.get("history_truncated"))
            if summary is not None
            else False
        ),
        "latest_created_at_utc": (
            _magma_share_import_handoff_latest_created_at(summary)
        ),
        "freshness_warning_after_seconds": (
            MAGMA_HANDOFF_PROVIDER_FRESHNESS_WARNING_SECONDS
        ),
        "retention_dropped_warning_count": (
            MAGMA_HANDOFF_PROVIDER_RETENTION_DROPPED_WARNING_COUNT
        ),
        "alert_thresholds": (
            _magma_share_import_handoff_provider_alert_thresholds(
                summary,
                freshness_source if freshness_source_valid else None,
            )
        ),
        "active_count": len(active),
        "active": active,
        "metrics_alert_state": (
            dict(metrics_alert_state)
            if metrics_alert_state is not None
            else _magma_handoff_metrics_alert_empty_state("not_configured")
        ),
        "controls_present": False,
        "runtime_authority_granted": False,
        "payload_files_imported": 0,
        "local_paths_recorded": False,
    }


def _with_magma_share_import_handoff_provider_health(
    section: dict,
    *,
    reason: str,
    snapshot=None,  # noqa: ANN001
    freshness_source: Mapping[str, object] | None = None,
    freshness_state: str = "not_configured",
    metrics_alert_state: Mapping[str, object] | None = None,
) -> dict:
    section["provider_health"] = (
        _magma_share_import_handoff_provider_health(
            reason=reason,
            snapshot=snapshot,
            summary=section,
            freshness_source=freshness_source,
            freshness_state=freshness_state,
            metrics_alert_state=metrics_alert_state,
        )
    )
    return section


def _magma_share_import_handoff_section(container=None) -> dict:
    """Build read-only MAGMA share-import handoff status for /api/ops."""
    snapshot, state = _magma_share_import_handoff_snapshot(container)
    freshness_source, freshness_state = (
        _magma_share_import_handoff_freshness_source(container)
    )
    metrics_alert_state = _magma_handoff_metrics_alert_state(container)
    if state == "not_configured":
        section = build_magma_share_import_handoff_status_summary(None)
        return _with_magma_share_import_handoff_provider_health(
            section,
            reason="not_configured",
            freshness_source=freshness_source,
            freshness_state=freshness_state,
            metrics_alert_state=metrics_alert_state,
        )
    if snapshot is None:
        invalid = state == "invalid"
        section = build_magma_share_import_handoff_status_summary(None)
        section.update({
            "source": (
                "magma_share_import_handoff_invalid"
                if invalid
                else "magma_share_import_handoff_unavailable"
            ),
            "status": "warning",
            "severity": "warning",
            "active_count": 1,
            "active": [{
                "id": (
                    "MagmaShareImportHandoffInvalid"
                    if invalid
                    else "MagmaShareImportHandoffUnavailable"
                ),
                "severity": "warning",
                "summary": (
                    "MAGMA share import handoff snapshot is invalid."
                    if invalid
                    else (
                        "MAGMA share import handoff snapshot is unavailable."
                    )
                ),
            }],
        })
        return _with_magma_share_import_handoff_provider_health(
            section,
            reason=(
                "snapshot_invalid"
                if invalid
                else "provider_unavailable"
            ),
            freshness_source=freshness_source,
            freshness_state=freshness_state,
            metrics_alert_state=metrics_alert_state,
        )
    try:
        section = build_magma_share_import_handoff_status_summary(snapshot)
        return _with_magma_share_import_handoff_provider_health(
            section,
            reason="valid_snapshot",
            snapshot=snapshot,
            freshness_source=freshness_source,
            freshness_state=freshness_state,
            metrics_alert_state=metrics_alert_state,
        )
    except (TypeError, ValueError):
        section = build_magma_share_import_handoff_status_summary(None)
        section.update({
            "source": "magma_share_import_handoff_invalid",
            "status": "warning",
            "severity": "warning",
            "active_count": 1,
            "active": [{
                "id": "MagmaShareImportHandoffInvalid",
                "severity": "warning",
                "summary": "MAGMA share import handoff snapshot is invalid.",
            }],
        })
        return _with_magma_share_import_handoff_provider_health(
            section,
            reason="snapshot_invalid",
            snapshot=snapshot,
            freshness_source=freshness_source,
            freshness_state=freshness_state,
            metrics_alert_state=metrics_alert_state,
        )


@router.get("/api/ops")
def api_ops(service=Depends(get_autonomy_service),
            container=Depends(get_container)):
    """Ops status for hologram ops menu."""
    st = service.get_status()
    rk = st.get("resource_kernel", {})
    adm = st.get("admission", {})
    kpis = _safe(lambda: service.get_kpis(), {})

    # v3.4: hybrid retrieval metrics
    hybrid_stats = _safe(lambda: container.hybrid_retrieval.stats(), {})

    # v3.5: backfill + accelerator metrics
    backfill_metrics = _safe(lambda: container.hybrid_backfill.status(), {})
    accelerator_metrics = _safe(lambda: container.synthetic_accelerator.status(), {})

    # v3.5.1: gemma profile metrics
    gemma_metrics = _safe(lambda: container.gemma_router.get_metrics(), {"enabled": False})

    # v3.5.2: parallel LLM dispatch metrics
    parallel_metrics = _safe(
        lambda: container.parallel_dispatcher.get_metrics(), {"enabled": False})

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
        "hybrid_retrieval": hybrid_stats,
        "backfill": backfill_metrics,
        "accelerator": accelerator_metrics,
        "autogrowth": _autogrowth_section(container),
        "route_stage_latency": _route_stage_latency_panels(container),
        "magma_share_import_handoff": (
            _magma_share_import_handoff_section(container)
        ),
        "gemma_profiles": gemma_metrics,
        "llm_parallel": parallel_metrics,
        "hex_mesh": _safe(
            lambda: container.hex_neighbor_assist.get_metrics(), {"enabled": False}),
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


#: Terminal upstream states that must NOT be downgraded by ChromaDB enrichment.
#: If the feed is already reported as unwired/framework/failed by the feed
#: plumbing, real rows in Chroma never override that truth.
_TERMINAL_FEED_STATES = frozenset({"unwired", "framework", "failed"})

#: Per-source fetch window for enrichment. Much larger than 5 so that
#: items_count, freshness_s, and latest_items reflect the full tail of the
#: source instead of the first-5-insertion-order window. (NEWS-001/002)
_ENRICH_FETCH_LIMIT = 500

#: How many latest items to return in latest_items[] for RSS sources.
_LATEST_ITEMS_RETURN = 5


def _enrich_from_chroma(source: dict, chroma_collection) -> None:
    """Enrich a feed source with live data from ChromaDB.

    NEWS-001: fetch a much larger per-source window (``_ENRICH_FETCH_LIMIT``)
    and sort by timestamp descending, so items_count / freshness_s /
    latest_items reflect the true tail of the feed instead of the
    insertion-order-capped first-5 window.

    NEWS-002: compute ``items_count`` from the full sorted result set, not
    from a capped slice. Compute ``freshness_s`` from the newest item
    (``timestamps[0]`` after descending sort) — not from ``max(...)`` of a
    stale window.

    NEWS-003: promote the source state from ``idle`` to ``active`` when the
    newest item is fresher than 2× interval_s, or to ``stale`` when it's
    older. Terminal upstream states (``unwired``, ``framework``, ``failed``)
    are never overwritten — those are the plumbing's truth about whether
    this source is wired at all, and real rows must not mask that.

    Truthful empty state: if there are no rows, leave the defaults
    (``items_count=0``, ``freshness_s=None``, ``latest_items=[]``,
    ``latest_value=None``) untouched.
    """
    agent_id = FEED_AGENT_IDS.get(source["type"])
    if not agent_id:
        return
    feed_id = source["id"]
    terminal_state = source.get("state") in _TERMINAL_FEED_STATES

    try:
        # Query with source-level filter: agent_id + feed_id.
        where_filter = {"$and": [
            {"agent_id": agent_id},
            {"feed_id": feed_id},
        ]}
        results = chroma_collection.get(
            where=where_filter,
            limit=_ENRICH_FETCH_LIMIT,
            include=["documents", "metadatas"],
        )
        docs = results.get("documents", []) or []
        metas = results.get("metadatas", []) or []

        # Fallback for non-RSS: entries written before feed_id was added
        # used only agent_id. RSS is excluded to prevent cross-source bleed.
        if not docs and source["type"] not in ("rss",):
            results = chroma_collection.get(
                where={"agent_id": agent_id},
                limit=_ENRICH_FETCH_LIMIT,
                include=["documents", "metadatas"],
            )
            docs = results.get("documents", []) or []
            metas = results.get("metadatas", []) or []

        if not docs:
            return  # truthful empty state — do not touch defaults

        # NEWS-001: pair docs with metas and sort by parsed timestamp DESC.
        pairs = list(zip(docs, metas))
        pairs.sort(
            key=lambda dm: _parse_ts((dm[1] or {}).get("timestamp", "") or ""),
            reverse=True,
        )
        sorted_docs = [d for d, _ in pairs]
        sorted_metas = [m for _, m in pairs]

        # NEWS-002: real items_count from the full sorted set.
        source["items_count"] = len(sorted_docs)

        # NEWS-002: freshness_s from the newest item (top of desc sort).
        newest_ts = ""
        for m in sorted_metas:
            ts = (m or {}).get("timestamp")
            if ts:
                newest_ts = ts
                break
        if newest_ts:
            source["last_success_at"] = newest_ts
            newest_epoch = _parse_ts(newest_ts)
            if newest_epoch > 0:
                source["freshness_s"] = max(0, int(time.time() - newest_epoch))

        # Type-specific enrichment — built from newest-first slice.
        if source["type"] == "rss":
            source["latest_items"] = [
                {"title": (d or "")[:120], "published": (m or {}).get("timestamp")}
                for d, m in zip(
                    sorted_docs[:_LATEST_ITEMS_RETURN],
                    sorted_metas[:_LATEST_ITEMS_RETURN],
                )
            ]
        elif source["type"] in ("weather", "electricity"):
            source["latest_value"] = _parse_latest_value(
                source["type"], sorted_docs[0], sorted_metas[0] or {}
            )

        # NEWS-003: state promotion. Never downgrade terminal states.
        if not terminal_state:
            freshness_s = source.get("freshness_s")
            interval_min = source.get("interval_min") or 0
            interval_s = int(interval_min) * 60 if interval_min else 0
            if freshness_s is not None and interval_s > 0:
                if freshness_s <= interval_s * 2:
                    source["state"] = "active"
                else:
                    source["state"] = "stale"
            elif freshness_s is not None:
                # Unknown interval: treat any live row as active.
                source["state"] = "active"

    except Exception:
        pass  # Leave defaults (truthful empty state)


def _get_chroma_collection(container):
    """Get ChromaDB collection for feed enrichment."""
    try:
        vs = container.vector_store
        # ChromaVectorStore owns a dict of named collections; prefer that.
        collections = getattr(vs, "_collections", None)
        if isinstance(collections, dict):
            return collections.get("waggle_memory")
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

_SUPPORTED_PROFILES = ["gadget", "cottage", "home", "factory"]


@router.get("/api/profiles")
def api_profiles(service=Depends(get_autonomy_service)):
    """List supported profiles, the running runtime profile, and the saved config profile."""
    st = service.get_status()
    active = (st.get("profile") or "home").lower()
    cfg = _load_settings_yaml()
    configured = (cfg.get("profile") or "home").lower()
    return {
        "profiles": _SUPPORTED_PROFILES,
        "active": active,
        "configured": configured,
        "restart_required": active != configured,
    }


class _ProfileSwitchBody(BaseModel):
    profile: str


@router.post("/api/profiles/active")
def api_profiles_switch(body: _ProfileSwitchBody, request: Request):
    """Persist profile selection to settings.yaml. Takes effect on next restart.

    The runtime profile is set once at construction and cannot be hot-reloaded.
    This endpoint only persists the choice for the next service start.
    """
    if not _is_request_authenticated(request):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    profile = body.profile.lower()
    if profile not in _SUPPORTED_PROFILES:
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=400,
            content={"error": f"Unknown profile: {profile}",
                      "allowed": _SUPPORTED_PROFILES},
        )

    import os
    import tempfile
    import yaml

    cfg = _load_settings_yaml()
    cfg["profile"] = profile

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

    return {"ok": True, "profile": profile, "restart_required": True}


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


# ── Analytics endpoints (file-based, no runtime dependency) ──────

_METRICS_FILE = Path("data/learning_metrics.jsonl")
_MORNING_FILE = Path("data/morning_reports.jsonl")


def _load_metrics(max_lines: int = 6000) -> list:
    """Load learning_metrics.jsonl (last max_lines)."""
    if not _METRICS_FILE.exists():
        return []
    try:
        rows = []
        with open(_METRICS_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        return rows[-max_lines:]
    except Exception as exc:
        logger.warning("Failed to load metrics: %s", exc)
        return []


@router.get("/api/analytics/trends")
async def analytics_trends():
    """Hallucination rate, cache hit rate, response time — 7-day trend."""
    from collections import defaultdict

    rows = _load_metrics()
    chat_rows = [r for r in rows if r.get("method") or r.get("route")]

    if not chat_rows:
        return {"days": [], "halluc_trend": [], "cache_trend": [],
                "rt_trend": [], "total_queries": 0}

    by_day: dict = defaultdict(list)
    for r in chat_rows:
        ts = r.get("ts", "")
        day = ts[:10] if len(ts) >= 10 else "unknown"
        by_day[day].append(r)

    days = sorted(by_day.keys())[-7:]
    halluc_trend = []
    cache_trend = []
    rt_trend = []

    for day in days:
        dr = by_day[day]
        n = len(dr)
        halluc_trend.append(
            round(sum(1 for r in dr if r.get("was_hallucination")) / max(n, 1), 3))
        cache_trend.append(
            round(sum(1 for r in dr if r.get("cache_hit")) / max(n, 1), 3))
        rt_trend.append(
            round(sum(r.get("response_time_ms", 0) for r in dr) / max(n, 1)))

    return {
        "days": days,
        "halluc_trend": halluc_trend,
        "cache_trend": cache_trend,
        "rt_trend": rt_trend,
        "total_queries": len(chat_rows),
    }


@router.get("/api/analytics/routes")
async def analytics_routes():
    """Route breakdown — how queries are served."""
    from collections import Counter

    rows = _load_metrics()
    chat_rows = [r for r in rows if r.get("route")]
    route_counts = Counter(r.get("route", "unknown") for r in chat_rows)
    method_counts = Counter(r.get("method", "unknown") for r in chat_rows)
    return {
        "routes": dict(route_counts.most_common(10)),
        "methods": dict(method_counts.most_common(10)),
        "total": len(chat_rows),
    }


@router.get("/api/analytics/models")
async def analytics_models():
    """Model usage breakdown — which models handle queries."""
    from collections import Counter

    rows = _load_metrics()
    chat_rows = [r for r in rows if r.get("model_used") is not None]
    model_counts = Counter(
        r.get("model_used", "unknown") or "micro/cache" for r in chat_rows)
    return {
        "models": dict(model_counts.most_common(10)),
        "total": len(chat_rows),
    }


@router.get("/api/analytics/facts")
async def analytics_facts():
    """Fact growth timeline from enrichment events."""
    from collections import defaultdict

    rows = _load_metrics()
    enrich_rows = [r for r in rows if r.get("event") == "enrichment_cycle"]

    by_day: dict = defaultdict(int)
    for r in enrich_rows:
        ts = r.get("ts", "")
        day = ts[:10] if len(ts) >= 10 else "unknown"
        by_day[day] += r.get("facts_stored", 0) + r.get("ext_stored", 0)

    days = sorted(by_day.keys())[-14:]
    counts = [by_day[d] for d in days]

    categories: dict = {}
    if _MORNING_FILE.exists():
        try:
            with open(_MORNING_FILE, encoding="utf-8") as f:
                lines = [ln.strip() for ln in f if ln.strip()]
            if lines:
                last = json.loads(lines[-1])
                categories = last.get("per_agent", {})
        except Exception as exc:
            logger.warning("Failed to read morning reports: %s", exc)

    return {
        "days": days,
        "facts_per_day": counts,
        "total_enriched": sum(counts),
        "per_agent": dict(sorted(categories.items(),
                                 key=lambda x: -x[1])[:20]) if categories else {},
    }
