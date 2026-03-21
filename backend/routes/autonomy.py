# SPDX-License-Identifier: Apache-2.0
"""
Autonomy API endpoints — v3.2 projections and runtime state.

Endpoints:
  GET /api/autonomy/epistemic-uncertainty
  GET /api/autonomy/attention-budget
  GET /api/autonomy/dream-mode/latest
  GET /api/autonomy/memory/consolidation-stats
  GET /api/autonomy/introspection
  GET /api/autonomy/narrative
"""

import logging
import time

from fastapi import APIRouter

log = logging.getLogger("waggledance.backend.autonomy")
router = APIRouter()


@router.get("/api/autonomy/epistemic-uncertainty")
async def epistemic_uncertainty():
    """Return current epistemic uncertainty score and thresholds."""
    try:
        from waggledance.core.world.epistemic_uncertainty import compute_uncertainty
        # In production, world_model entities come from runtime.
        # Stub returns safe defaults when runtime is not loaded.
        return {
            "score": 0.0,
            "threshold": 0.4,
            "missing_baselines": 0,
            "stale_entities": 0,
            "unresolved_questions": 0,
            "status": "no_runtime",
            "timestamp": time.time(),
        }
    except ImportError:
        return {"error": "epistemic_uncertainty module not available", "score": None}


@router.get("/api/autonomy/attention-budget")
async def attention_budget():
    """Return current attention allocation."""
    try:
        from waggledance.core.autonomy.attention_budget import AttentionBudget
        budget = AttentionBudget()  # defaults when runtime not loaded
        alloc = budget.query()
        alloc["timestamp"] = time.time()
        alloc["status"] = "default_allocation"
        return alloc
    except ImportError:
        return {"error": "attention_budget module not available"}


@router.get("/api/autonomy/dream-mode/latest")
async def dream_mode_latest():
    """Return latest dream mode run stats."""
    return {
        "status": "no_session",
        "simulations_run": 0,
        "candidates_evaluated": 0,
        "insights_found": 0,
        "insight_score": 0.0,
        "timestamp": time.time(),
    }


@router.get("/api/autonomy/memory/consolidation-stats")
async def consolidation_stats():
    """Return memory consolidation metrics."""
    return {
        "consolidation_enabled": True,
        "consolidation_evict_enabled": False,
        "active_episodes": 0,
        "consolidated_count": 0,
        "protected_count": 0,
        "status": "no_data",
        "timestamp": time.time(),
    }


@router.get("/api/autonomy/introspection")
async def introspection(profile: str = "HOME"):
    """Return glass-box introspection view (profile-gated)."""
    try:
        from waggledance.core.projections.introspection_view import (
            build_introspection, filter_by_profile,
        )
        snapshot = build_introspection(uncertainty_score=0.0)
        return filter_by_profile(snapshot, profile)
    except ImportError:
        return {"error": "introspection_view module not available"}


@router.get("/api/autonomy/narrative")
async def narrative_projection(language: str = "en"):
    """Return human-readable system narrative."""
    try:
        from waggledance.core.projections.narrative_projector import project_narrative
        return project_narrative(language=language)
    except ImportError:
        return {"error": "narrative_projector module not available"}
