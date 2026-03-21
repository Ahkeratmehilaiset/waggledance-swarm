"""Hologram brain visualization route — /hologram view + /api/hologram/state."""

import logging
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from waggledance.adapters.http.deps import get_autonomy_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["hologram"])

_HOLOGRAM_PATH = Path(__file__).resolve().parents[4] / "web" / "hologram-brain-v5.html"
_HOLOGRAM_HTML: str | None = None


def _load_html() -> str:
    """Lazy-load the hologram HTML (cached after first read)."""
    global _HOLOGRAM_HTML
    if _HOLOGRAM_HTML is None:
        _HOLOGRAM_HTML = _HOLOGRAM_PATH.read_text(encoding="utf-8")
    return _HOLOGRAM_HTML


@router.get("/hologram", response_class=HTMLResponse)
async def hologram_view():
    """Serve the hologram brain visualization page."""
    return HTMLResponse(_load_html())


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def build_hologram_state(service) -> Dict[str, Any]:
    """Synthesize hologram payload from AutonomyRuntime stats.

    Returns the node/edge/event structure expected by the hologram HTML.
    """
    runtime = service._runtime
    if not runtime or not runtime.is_running:
        # Return zero-state when runtime is not active
        return {
            "nodes": {k: 0.0 for k in (
                "sensory", "waggle_memory", "episodic_memory",
                "semantic_memory", "working_memory", "llm_core",
                "reason_layer", "causal_map", "micro_train",
                "micro_route", "micro_anom", "micro_therm",
                "micro_stats", "decision",
            )},
            "edges": {},
            "events": [],
        }

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

    # Capability confidence
    cc = getattr(runtime, "capability_confidence", None)
    cc_all = cc.get_all() if cc else {}
    cc_mean = (sum(cc_all.values()) / len(cc_all)) if cc_all else 0.5

    # Resource kernel → sensory (active tasks normalized)
    active_tasks = rk.get("active_tasks", 0)
    sensory = _clamp(active_tasks / 10.0) if active_tasks else 0.1

    # World model graph sizes
    graph_stats = wm_stats.get("graph", wm_stats)
    edge_count = graph_stats.get("edges", graph_stats.get("edge_count", 0))
    node_count = graph_stats.get("nodes", graph_stats.get("node_count", 0))
    waggle_memory = _clamp(edge_count / 50.0) if edge_count else 0.1
    causal_map = _clamp(node_count / 30.0) if node_count else 0.1

    # Case builder → episodic memory
    cb_total = cb_stats.get("total", 0)
    episodic_memory = _clamp(cb_total / 20.0) if cb_total else 0.1

    # Working memory
    wm_size = wmem_stats.get("size", 0)
    wm_cap = wmem_stats.get("capacity", 1)
    semantic_memory = _clamp(wm_size / max(wm_cap, 1))
    working_memory = _clamp(wm_size / max(wm_cap, 1) * 0.8 + 0.1)

    # Solver router quality paths
    sr_total = sr_stats.get("total", 0)
    qd = sr_stats.get("quality_distribution", {})
    bronze = qd.get("bronze", 0)
    gold = qd.get("gold", 0)
    llm_core = _clamp(1.0 - (bronze / max(sr_total, 1))) if sr_total else 0.5
    reason_layer = _clamp(gold / max(sr_total, 1)) if sr_total else 0.5

    # Capability confidence → micro nodes
    micro_train = _clamp(cc_mean)
    micro_route = _clamp(cc_all.get("solve.route", cc_all.get("solve.general", cc_mean)))
    micro_anom = _clamp(cc_all.get("detect.anomaly", cc_mean))
    micro_therm = _clamp(cc_all.get("solve.thermal", cc_mean))
    micro_stats_val = _clamp(cc_all.get("solve.stats", cc_mean))

    # Verifier → decision
    pass_rate = vf_stats.get("pass_rate", 0.5)
    decision = _clamp(pass_rate)

    nodes = {
        "sensory": round(sensory, 2),
        "waggle_memory": round(waggle_memory, 2),
        "episodic_memory": round(episodic_memory, 2),
        "semantic_memory": round(semantic_memory, 2),
        "working_memory": round(working_memory, 2),
        "llm_core": round(llm_core, 2),
        "reason_layer": round(reason_layer, 2),
        "causal_map": round(causal_map, 2),
        "micro_train": round(micro_train, 2),
        "micro_route": round(micro_route, 2),
        "micro_anom": round(micro_anom, 2),
        "micro_therm": round(micro_therm, 2),
        "micro_stats": round(micro_stats_val, 2),
        "decision": round(decision, 2),
    }

    # Build edges — intensity from connected node activations
    edges = {
        "sensory->working_memory": round((sensory + working_memory) / 2, 2),
        "working_memory->llm_core": round((working_memory + llm_core) / 2, 2),
        "semantic_memory->llm_core": round((semantic_memory + llm_core) / 2, 2),
        "llm_core->reason_layer": round((llm_core + reason_layer) / 2, 2),
        "reason_layer->causal_map": round((reason_layer + causal_map) / 2, 2),
        "causal_map->micro_train": round((causal_map + micro_train) / 2, 2),
        "causal_map->decision": round((causal_map + decision) / 2, 2),
        "llm_core->decision": round((llm_core + decision) / 2, 2),
    }

    # Active flow events — top 4 most active edges
    sorted_edges = sorted(edges.items(), key=lambda x: x[1], reverse=True)
    events: List[Dict[str, str]] = []
    for edge_key, _val in sorted_edges[:4]:
        src, dst = edge_key.split("->")
        events.append({"from": src, "to": dst})

    return {"nodes": nodes, "edges": edges, "events": events}


@router.get("/api/hologram/state")
def hologram_state(service=Depends(get_autonomy_service)):
    """Return hologram brain state synthesized from runtime stats."""
    return build_hologram_state(service)
