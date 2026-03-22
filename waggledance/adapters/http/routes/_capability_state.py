# SPDX-License-Identifier: Apache-2.0
"""Shared capability state derivation — single source of truth.

Used by BOTH:
- hologram.py:_derive_node_meta() for node_meta.state, device, quality, source_class
- compat_dashboard.py:api_capabilities_state() for /api/capabilities/state

Guarantees: Both callers get identical state, device, quality, and source_class
for the same runtime snapshot.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CapabilityInfo:
    """Per-capability state derived from runtime inspection."""

    state: str  # unavailable|unwired|framework|idle|active|training|failed
    device: Optional[str]  # "GPU"|"CPU"|"UNKNOWN"|None
    quality: Optional[float]  # historical confidence/accuracy; null when unavailable/unwired/framework
    source_class: Optional[str]  # live|simulated|legacy|error|None


# Node IDs that are capability-backed and use this derivation
CAPABILITY_NODE_IDS = frozenset({
    "llm_core", "micro_route", "micro_anom", "micro_therm",
    "micro_stats", "micro_train", "reason_layer", "decision",
})

# States for which quality is gated to None
_NO_QUALITY_STATES = frozenset({"unavailable", "unwired", "framework"})


def derive_capability_state(
    runtime: Any,
    rs: Dict[str, Any],
) -> Dict[str, CapabilityInfo]:
    """Derive capability state for all capability-backed nodes.

    Args:
        runtime: AutonomyRuntime instance (or None).
        rs: Result of runtime.stats() — the full stats dict.

    Returns:
        {node_id: CapabilityInfo} for every node in CAPABILITY_NODE_IDS.
    """
    result: Dict[str, CapabilityInfo] = {}

    if not runtime or not getattr(runtime, "is_running", False):
        for nid in CAPABILITY_NODE_IDS:
            result[nid] = CapabilityInfo(
                state="unavailable", device=None, quality=None, source_class=None,
            )
        return result

    # ── Gather shared stats ─────────────────────────────────────
    sr_stats = rs.get("solver_router", {})
    sr_total = sr_stats.get("total", 0)
    qd = sr_stats.get("quality_distribution", {})
    bronze = qd.get("bronze", 0)
    gold = qd.get("gold", 0)

    cc = getattr(runtime, "capability_confidence", None)
    cc_all = cc.get_all() if cc else {}

    trainer_stats = rs.get("specialist_trainer", {})
    ollama_stats = rs.get("ollama", {})
    vf_stats = rs.get("verifier", {})

    # LLM adapter inspection
    llm_adapter = getattr(runtime, "llm_adapter", None)
    model_store = getattr(runtime, "model_store", None)

    # ── llm_core ────────────────────────────────────────────────
    result["llm_core"] = _derive_llm_core(
        llm_adapter, ollama_stats, sr_total, bronze, gold,
    )

    # ── reason_layer ────────────────────────────────────────────
    result["reason_layer"] = _derive_reason_layer(sr_stats, sr_total, gold)

    # ── decision ────────────────────────────────────────────────
    result["decision"] = _derive_decision(vf_stats)

    # ── micro_train ─────────────────────────────────────────────
    result["micro_train"] = _derive_micro_train(
        model_store, trainer_stats, cc_all,
    )

    # ── micro_route, micro_anom, micro_therm, micro_stats ──────
    for nid, cap_id in [
        ("micro_route", "solve.route"),
        ("micro_anom", "detect.anomaly"),
        ("micro_therm", "solve.thermal"),
        ("micro_stats", "solve.stats"),
    ]:
        result[nid] = _derive_micro_node(
            nid, cap_id, model_store, trainer_stats, cc_all,
        )

    return result


# ── Private helpers ─────────────────────────────────────────────


def _derive_llm_core(
    llm_adapter: Any,
    ollama_stats: Dict[str, Any],
    sr_total: int,
    bronze: int,
    gold: int,
) -> CapabilityInfo:
    """LLM core: state from adapter registration + model load status."""
    if llm_adapter is None:
        return CapabilityInfo("unavailable", None, None, None)

    # Check if model is actually loaded
    model_loaded = False
    try:
        model_loaded = bool(getattr(llm_adapter, "model_loaded", False))
    except Exception:
        pass
    # Fallback: if adapter exists and ollama reports models, treat as loaded
    if not model_loaded and ollama_stats.get("models_loaded", 0) > 0:
        model_loaded = True
    if not model_loaded and ollama_stats.get("model_name"):
        model_loaded = True

    if not model_loaded:
        return CapabilityInfo("unwired", None, None, "live")

    # Determine device from Ollama config
    device = _device_from_ollama(ollama_stats)

    # Active or idle
    active_requests = ollama_stats.get("active_requests", 0)
    state = "active" if active_requests > 0 else "idle"

    # Quality: 1 - (bronze/total) when data exists
    quality = None
    if sr_total > 0:
        quality = round(max(0.0, min(1.0, 1.0 - (bronze / max(sr_total, 1)))), 3)

    return CapabilityInfo(state, device, quality, "live")


def _derive_reason_layer(
    sr_stats: Dict[str, Any],
    sr_total: int,
    gold: int,
) -> CapabilityInfo:
    """Reason layer: state from solver router activity."""
    active_count = sr_stats.get("active_count", 0)
    state = "active" if active_count > 0 else "idle"

    quality = None
    if sr_total > 0:
        quality = round(max(0.0, min(1.0, gold / max(sr_total, 1))), 3)

    return CapabilityInfo(state, None, quality, "live")


def _derive_decision(vf_stats: Dict[str, Any]) -> CapabilityInfo:
    """Decision: state from verifier throughput."""
    recent_checks = vf_stats.get("recent_checks", 0)
    state = "active" if recent_checks > 0 else "idle"

    quality = vf_stats.get("pass_rate") if vf_stats else None
    if quality is not None:
        quality = round(max(0.0, min(1.0, quality)), 3)

    return CapabilityInfo(state, None, quality, "live")


def _derive_micro_train(
    model_store: Any,
    trainer_stats: Dict[str, Any],
    cc_all: Dict[str, float],
) -> CapabilityInfo:
    """Micro-train: state from model store + trainer activity."""
    state, device = _micro_base_state(model_store, trainer_stats)

    # Override state if actively training
    active_trainers = trainer_stats.get("active_trainers", 0)
    if active_trainers > 0 and state not in ("unavailable", "unwired"):
        state = "training"

    # Quality: mean confidence if data exists
    quality = None
    if state not in _NO_QUALITY_STATES and cc_all:
        quality = round(sum(cc_all.values()) / len(cc_all), 3)

    return CapabilityInfo(state, device, quality, "live")


def _derive_micro_node(
    node_id: str,
    cap_id: str,
    model_store: Any,
    trainer_stats: Dict[str, Any],
    cc_all: Dict[str, float],
) -> CapabilityInfo:
    """Individual micro-model node: state from model store + activity."""
    state, device = _micro_base_state(model_store, trainer_stats)

    # Quality: per-capability confidence if data exists
    quality = None
    if state not in _NO_QUALITY_STATES:
        raw = cc_all.get(cap_id)
        if raw is not None:
            quality = round(max(0.0, min(1.0, raw)), 3)

    return CapabilityInfo(state, device, quality, "live")


def _micro_base_state(
    model_store: Any,
    trainer_stats: Dict[str, Any],
) -> tuple[str, Optional[str]]:
    """Base state derivation for micro-model nodes.

    Returns (state, device).
    """
    if model_store is None:
        return ("unwired", None)

    # Check if trained model artifacts exist
    has_model = False
    try:
        if hasattr(model_store, "has_model"):
            has_model = model_store.has_model()
        elif hasattr(model_store, "list_models"):
            has_model = bool(model_store.list_models())
        else:
            # If model_store exists but no check method, assume framework
            has_model = False
    except Exception:
        has_model = False

    if not has_model:
        return ("framework", "CPU")

    # Specialists are sklearn/CPU
    device = "CPU"
    active_trainers = trainer_stats.get("active_trainers", 0)
    state = "training" if active_trainers > 0 else "idle"
    return (state, device)


def _device_from_ollama(ollama_stats: Dict[str, Any]) -> str:
    """Derive device from Ollama GPU config — never hardcoded."""
    gpu_layers = ollama_stats.get("gpu_layers", 0)
    if gpu_layers and gpu_layers > 0:
        return "GPU"
    if ollama_stats.get("gpu_offload"):
        return "GPU"
    # Check if CUDA is available
    if ollama_stats.get("cuda_available"):
        return "GPU"
    return "CPU"
