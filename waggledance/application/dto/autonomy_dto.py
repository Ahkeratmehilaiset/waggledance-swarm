"""
Pydantic API schemas for autonomy models — Phase 2 of Full Autonomy v3.

These are used only at the API boundary for request/response validation.
Core domain logic uses pure dataclasses from waggledance.core.domain.autonomy.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


# ── Goal ─────────────────────────────────────────────────────

class GoalCreate(BaseModel):
    """API request to create a new goal."""
    type: str = Field(default="observe", description="observe|diagnose|optimize|protect|plan|act|verify|learn|maintain")
    description: str = Field(..., min_length=1, max_length=2000)
    priority: int = Field(default=50, ge=1, le=100)
    profile: str = Field(default="", max_length=20)
    parent_goal_id: Optional[str] = None

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        allowed = {"observe", "diagnose", "optimize", "protect", "plan", "act", "verify", "learn", "maintain"}
        if v not in allowed:
            raise ValueError(f"type must be one of {allowed}")
        return v


class GoalResponse(BaseModel):
    """API response for a goal."""
    goal_id: str
    type: str
    description: str
    priority: int
    status: str
    source: str
    profile: str
    parent_goal_id: Optional[str]
    created_at: str
    updated_at: str


class GoalStatusUpdate(BaseModel):
    """API request to transition a goal's status."""
    new_status: str

    @field_validator("new_status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        allowed = {"proposed", "accepted", "planned", "executing", "verified", "failed", "rolled_back", "archived"}
        if v not in allowed:
            raise ValueError(f"status must be one of {allowed}")
        return v


# ── Action ───────────────────────────────────────────────────

class ActionCreate(BaseModel):
    """API request to create an action."""
    capability_id: str = Field(..., min_length=1)
    goal_id: str = Field(default="")
    payload: Dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str = Field(default="")


class RiskScoreResponse(BaseModel):
    """Risk assessment in API response."""
    severity: float
    reversibility: float
    observability: float
    uncertainty: float
    blast_radius: float
    composite: float
    approval_required: bool


class ActionResponse(BaseModel):
    """API response for an action."""
    action_id: str
    capability_id: str
    goal_id: str
    payload: Dict[str, Any]
    risk_score: RiskScoreResponse
    status: str
    result: Dict[str, Any]
    error: Optional[str]
    created_at: str
    executed_at: Optional[str]


# ── Capability ───────────────────────────────────────────────

class CapabilityResponse(BaseModel):
    """API response for a capability contract."""
    capability_id: str
    category: str
    description: str
    preconditions: List[str]
    success_criteria: List[str]
    rollback_possible: bool
    max_latency_ms: float
    trust_score: float


# ── Case Trajectory ──────────────────────────────────────────

class CaseTrajectoryResponse(BaseModel):
    """API response for a case trajectory."""
    trajectory_id: str
    goal: Optional[GoalResponse]
    selected_capabilities: List[CapabilityResponse]
    actions: List[ActionResponse]
    verifier_result: Dict[str, Any]
    quality_grade: str
    canonical_id: str
    profile: str
    created_at: str


# ── World Snapshot ───────────────────────────────────────────

class WorldSnapshotResponse(BaseModel):
    """API response for a world snapshot."""
    snapshot_id: str
    timestamp: str
    entities: Dict[str, Any]
    baselines: Dict[str, float]
    residuals: Dict[str, float]
    profile: str
    source_type: str


# ── Plan ─────────────────────────────────────────────────────

class PlanStepResponse(BaseModel):
    """API response for a plan step."""
    step_id: str
    order: int
    capability_id: str
    description: str
    expected_outcome: str
    completed: bool


class PlanResponse(BaseModel):
    """API response for a plan."""
    plan_id: str
    goal_id: str
    steps: List[PlanStepResponse]
    progress: float
    is_complete: bool
    created_at: str
