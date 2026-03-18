"""Autonomy API routes — learning, safety cases, proactive goals, KPIs."""

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional

from waggledance.adapters.http.deps import get_autonomy_service, require_auth

router = APIRouter(tags=["autonomy"])


# ── Request models ───────────────────────────────────────

class LearningCycleRequest(BaseModel):
    """Request to run a night learning cycle."""
    # day_cases and legacy_records are typically provided by the system,
    # but can be empty for a dry run
    pass


class ProactiveGoalRequest(BaseModel):
    """Request to check for proactive goals."""
    observations: Dict[str, float] = Field(
        default_factory=dict,
        description="Current observations as {entity.metric: value}",
    )
    threshold: float = Field(default=2.0, description="Residual threshold for goal proposal")


# ── Status & KPIs ────────────────────────────────────────

@router.get("/autonomy/status")
def autonomy_status(service=Depends(get_autonomy_service)):
    """Get comprehensive autonomy runtime status."""
    return service.get_status()


@router.get("/autonomy/kpis")
def autonomy_kpis(service=Depends(get_autonomy_service)):
    """Get autonomy KPIs."""
    return service.get_kpis()


# ── Night learning (Priority 1) ─────────────────────────

@router.post("/autonomy/learning/run")
def run_learning_cycle(
    service=Depends(get_autonomy_service),
    _auth=Depends(require_auth),
):
    """Run a night learning cycle."""
    return service.run_learning_cycle()


@router.get("/autonomy/learning/status")
def learning_status(service=Depends(get_autonomy_service)):
    """Get night learning pipeline status."""
    return service.get_learning_status()


# ── Proactive goals (Priority 3) ────────────────────────

@router.post("/autonomy/goals/check-proactive")
def check_proactive_goals(
    req: ProactiveGoalRequest,
    service=Depends(get_autonomy_service),
    _auth=Depends(require_auth),
):
    """Check world model for deviations and propose proactive goals."""
    return service.check_proactive_goals(
        observations=req.observations,
        threshold=req.threshold,
    )


# ── Safety cases (Priority 4) ───────────────────────────

@router.get("/autonomy/safety-cases")
def get_safety_cases(
    limit: int = 20,
    service=Depends(get_autonomy_service),
):
    """Get recent safety cases."""
    return {"cases": service.get_safety_cases(limit=limit)}


@router.get("/autonomy/safety-cases/stats")
def safety_cases_stats(service=Depends(get_autonomy_service)):
    """Get safety case statistics."""
    return service.get_safety_stats()
