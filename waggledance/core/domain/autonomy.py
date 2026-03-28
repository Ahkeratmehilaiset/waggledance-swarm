"""
Autonomy Core Data Models — Phase 2 of Full Autonomy v3.

Pure dataclasses (no Pydantic in core layer). These are the fundamental
units of the autonomy runtime:

- Goal: what the system is trying to achieve
- WorldSnapshot: the state of the world at a point in time
- CapabilityContract: what a capability can do and its guarantees
- Action: a discrete step taken by the system
- RiskScore: multi-dimensional risk assessment
- CaseTrajectory: the full record of a goal → action → outcome cycle
- PlanStep: a single step in a plan
- Plan: a sequence of steps to achieve a goal
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Literal, Optional


# ── Enums ────────────────────────────────────────────────────

class GoalType(str, Enum):
    OBSERVE = "observe"
    DIAGNOSE = "diagnose"
    OPTIMIZE = "optimize"
    PROTECT = "protect"
    PLAN = "plan"
    ACT = "act"
    VERIFY = "verify"
    LEARN = "learn"
    MAINTAIN = "maintain"


class GoalStatus(str, Enum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    PLANNED = "planned"
    EXECUTING = "executing"
    VERIFIED = "verified"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"
    ARCHIVED = "archived"


class ActionStatus(str, Enum):
    DRY_RUN = "dry_run"
    APPROVED = "approved"
    EXECUTED = "executed"
    ROLLED_BACK = "rolled_back"
    DENIED = "denied"


class QualityGrade(str, Enum):
    GOLD = "gold"          # solver + verifier confirmed
    SILVER = "silver"      # partial verification
    BRONZE = "bronze"      # LLM-only or unverified
    QUARANTINE = "quarantine"  # conflicting signals


class SourceType(str, Enum):
    OBSERVED = "observed"
    INFERRED_BY_SOLVER = "inferred_by_solver"
    INFERRED_BY_STATS = "inferred_by_stats"
    INFERRED_BY_RULE = "inferred_by_rule"
    PROPOSED_BY_LLM = "proposed_by_llm"
    CONFIRMED_BY_VERIFIER = "confirmed_by_verifier"
    LEARNED_FROM_CASE = "learned_from_case"
    SELF_REFLECTION = "self_reflection"      # v3.2: introspection entries
    SIMULATED = "simulated"                  # v3.2: dream mode outputs


class CapabilityCategory(str, Enum):
    SENSE = "sense"
    NORMALIZE = "normalize"
    ESTIMATE = "estimate"
    SOLVE = "solve"
    DETECT = "detect"
    PREDICT = "predict"
    OPTIMIZE = "optimize"
    PLAN = "plan"
    VERIFY = "verify"
    EXPLAIN = "explain"
    ACT = "act"
    LEARN = "learn"
    RETRIEVE = "retrieve"


# ── Helper ───────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return uuid.uuid4().hex[:12]


# ── Core Models ──────────────────────────────────────────────

@dataclass
class Goal:
    """A discrete objective the system is trying to achieve."""
    goal_id: str = field(default_factory=_uuid)
    type: GoalType = GoalType.OBSERVE
    description: str = ""
    priority: int = 50  # 1-100
    status: GoalStatus = GoalStatus.PROPOSED
    source: str = ""  # who/what created this goal
    profile: str = ""  # COTTAGE/HOME/FACTORY/GADGET
    parent_goal_id: Optional[str] = None
    # v3.2 continuity fields
    carry_forward: bool = False         # survives restart
    promise_to_user: bool = False       # committed to user
    blocked_reason: str = ""            # why not progressing
    resume_after: Optional[datetime] = None  # deferred until
    active_motive_id: str = ""          # which motive drives this
    motive_valence: float = 0.0         # motive priority weight
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)

    # ── Status transitions ───────────────────────────────

    VALID_TRANSITIONS = {
        GoalStatus.PROPOSED: {GoalStatus.ACCEPTED, GoalStatus.ARCHIVED},
        GoalStatus.ACCEPTED: {GoalStatus.PLANNED, GoalStatus.FAILED, GoalStatus.ARCHIVED},
        GoalStatus.PLANNED: {GoalStatus.EXECUTING, GoalStatus.FAILED, GoalStatus.ARCHIVED},
        GoalStatus.EXECUTING: {GoalStatus.VERIFIED, GoalStatus.FAILED, GoalStatus.ROLLED_BACK},
        GoalStatus.VERIFIED: {GoalStatus.ARCHIVED},
        GoalStatus.FAILED: {GoalStatus.ARCHIVED, GoalStatus.PROPOSED},  # retry
        GoalStatus.ROLLED_BACK: {GoalStatus.ARCHIVED, GoalStatus.PROPOSED},
        GoalStatus.ARCHIVED: set(),
    }

    def transition_to(self, new_status: GoalStatus) -> None:
        allowed = self.VALID_TRANSITIONS.get(self.status, set())
        if new_status not in allowed:
            raise ValueError(
                f"Invalid transition: {self.status.value} → {new_status.value}. "
                f"Allowed: {[s.value for s in allowed]}"
            )
        self.status = new_status
        self.updated_at = _now()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "type": self.type.value,
            "description": self.description,
            "priority": self.priority,
            "status": self.status.value,
            "source": self.source,
            "profile": self.profile,
            "parent_goal_id": self.parent_goal_id,
            "carry_forward": self.carry_forward,
            "promise_to_user": self.promise_to_user,
            "blocked_reason": self.blocked_reason,
            "resume_after": self.resume_after.isoformat() if self.resume_after else None,
            "active_motive_id": self.active_motive_id,
            "motive_valence": self.motive_valence,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


@dataclass
class WorldSnapshot:
    """The state of the world at a specific point in time."""
    snapshot_id: str = field(default_factory=_uuid)
    timestamp: datetime = field(default_factory=_now)
    entities: Dict[str, Any] = field(default_factory=dict)
    baselines: Dict[str, float] = field(default_factory=dict)
    residuals: Dict[str, float] = field(default_factory=dict)
    profile: str = ""
    source_type: SourceType = SourceType.OBSERVED

    def compute_residual(self, entity_metric: str, current_value: float) -> float:
        """Compute residual = current - baseline."""
        baseline = self.baselines.get(entity_metric, 0.0)
        residual = current_value - baseline
        self.residuals[entity_metric] = residual
        return residual

    def to_dict(self) -> Dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "timestamp": self.timestamp.isoformat(),
            "entities": self.entities,
            "baselines": self.baselines,
            "residuals": self.residuals,
            "profile": self.profile,
            "source_type": self.source_type.value if hasattr(self.source_type, 'value') else str(self.source_type),
        }


@dataclass
class RiskScore:
    """Multi-dimensional risk assessment for an action."""
    severity: float = 0.0       # 0-1: how bad if it goes wrong
    reversibility: float = 1.0  # 0-1: how easy to undo (1=fully reversible)
    observability: float = 1.0  # 0-1: can we see the outcome (1=fully observable)
    uncertainty: float = 0.0    # 0-1: how uncertain the inputs are
    blast_radius: float = 0.0   # 0-1: how many things are affected
    approval_required: bool = False

    @property
    def composite(self) -> float:
        """Weighted composite risk score (0=safe, 1=dangerous)."""
        return (
            self.severity * 0.30
            + (1.0 - self.reversibility) * 0.25
            + (1.0 - self.observability) * 0.15
            + self.uncertainty * 0.15
            + self.blast_radius * 0.15
        )

    @property
    def requires_approval(self) -> bool:
        """True if human approval is needed."""
        return self.approval_required or self.composite > 0.7

    def to_dict(self) -> Dict[str, Any]:
        return {
            "severity": self.severity,
            "reversibility": self.reversibility,
            "observability": self.observability,
            "uncertainty": self.uncertainty,
            "blast_radius": self.blast_radius,
            "composite": round(self.composite, 4),
            "approval_required": self.requires_approval,
        }


@dataclass
class CapabilityContract:
    """What a capability can do and what guarantees it provides."""
    capability_id: str
    category: CapabilityCategory
    description: str = ""
    preconditions: List[str] = field(default_factory=list)
    success_criteria: List[str] = field(default_factory=list)
    rollback_possible: bool = True
    max_latency_ms: float = 5000.0
    trust_score: float = 0.5

    def to_dict(self) -> Dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "category": self.category.value,
            "description": self.description,
            "preconditions": self.preconditions,
            "success_criteria": self.success_criteria,
            "rollback_possible": self.rollback_possible,
            "max_latency_ms": self.max_latency_ms,
            "trust_score": self.trust_score,
        }


@dataclass
class Action:
    """A discrete step taken by the system through the Safe Action Bus."""
    action_id: str = field(default_factory=_uuid)
    capability_id: str = ""
    goal_id: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)
    risk_score: RiskScore = field(default_factory=RiskScore)
    status: ActionStatus = ActionStatus.DRY_RUN
    result: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    idempotency_key: str = ""
    created_at: datetime = field(default_factory=_now)
    executed_at: Optional[datetime] = None

    def mark_executed(self, result: Dict[str, Any] = None) -> None:
        self.status = ActionStatus.EXECUTED
        self.executed_at = _now()
        if result:
            self.result = result

    def mark_denied(self, reason: str = "") -> None:
        self.status = ActionStatus.DENIED
        self.error = reason

    def mark_rolled_back(self, reason: str = "") -> None:
        self.status = ActionStatus.ROLLED_BACK
        self.error = reason

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action_id": self.action_id,
            "capability_id": self.capability_id,
            "goal_id": self.goal_id,
            "payload": self.payload,
            "risk_score": self.risk_score.to_dict(),
            "status": self.status.value,
            "result": self.result,
            "error": self.error,
            "idempotency_key": self.idempotency_key,
            "created_at": self.created_at.isoformat(),
            "executed_at": self.executed_at.isoformat() if self.executed_at else None,
        }


@dataclass
class SensorObservation:
    """Normalized sensor observation for the world model.

    All sensor adapters produce observations in this format to enable
    uniform baseline updates, residual computation, and anomaly detection.
    """
    sensor_id: str          # e.g., "hive_1.temperature"
    entity_id: str          # e.g., "hive_1"
    metric: str             # e.g., "temperature"
    value: float
    unit: str = ""          # e.g., "°C", "lux", "%"
    source: str = ""        # e.g., "mqtt", "home_assistant", "frigate", "audio"
    timestamp: float = field(default_factory=time.time)
    quality: float = 1.0    # 0-1, confidence in reading
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        """Baseline store key: entity_id.metric."""
        return f"{self.entity_id}.{self.metric}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sensor_id": self.sensor_id,
            "entity_id": self.entity_id,
            "metric": self.metric,
            "value": self.value,
            "unit": self.unit,
            "source": self.source,
            "timestamp": self.timestamp,
            "quality": self.quality,
            "metadata": self.metadata,
        }


@dataclass
class PlanStep:
    """A single step in an execution plan."""
    step_id: str = field(default_factory=_uuid)
    order: int = 0
    capability_id: str = ""
    description: str = ""
    preconditions: List[str] = field(default_factory=list)
    expected_outcome: str = ""
    action: Optional[Action] = None
    completed: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_id": self.step_id,
            "order": self.order,
            "capability_id": self.capability_id,
            "description": self.description,
            "preconditions": self.preconditions,
            "expected_outcome": self.expected_outcome,
            "action": self.action.to_dict() if self.action else None,
            "completed": self.completed,
        }


@dataclass
class Plan:
    """A sequence of steps to achieve a goal."""
    plan_id: str = field(default_factory=_uuid)
    goal_id: str = ""
    steps: List[PlanStep] = field(default_factory=list)
    created_at: datetime = field(default_factory=_now)

    @property
    def is_complete(self) -> bool:
        return all(s.completed for s in self.steps) if self.steps else False

    @property
    def progress(self) -> float:
        if not self.steps:
            return 0.0
        return sum(1 for s in self.steps if s.completed) / len(self.steps)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "goal_id": self.goal_id,
            "steps": [s.to_dict() for s in self.steps],
            "progress": round(self.progress, 2),
            "is_complete": self.is_complete,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class CaseTrajectory:
    """
    The full record of a goal → plan → action → outcome cycle.
    This is the primary learning unit for Night Learning v2.
    """
    trajectory_id: str = field(default_factory=_uuid)
    goal: Optional[Goal] = None
    world_snapshot_before: Optional[WorldSnapshot] = None
    selected_capabilities: List[CapabilityContract] = field(default_factory=list)
    actions: List[Action] = field(default_factory=list)
    world_snapshot_after: Optional[WorldSnapshot] = None
    verifier_result: Dict[str, Any] = field(default_factory=dict)
    quality_grade: QualityGrade = QualityGrade.BRONZE
    canonical_id: str = ""  # agent/capability canonical ID
    profile: str = ""
    # v3.2 dream mode / simulation fields
    counterfactual_alternatives: List[str] = field(default_factory=list)
    trajectory_origin: str = "observed"  # observed | simulated
    synthetic: bool = False
    created_at: datetime = field(default_factory=_now)

    def grade(self) -> QualityGrade:
        """
        Auto-grade based on available evidence.

        Gold: solver + verifier confirmed, residual reduction > 50%
        Silver: capability chain OK, partial verification
        Bronze: LLM-only or unverified
        Quarantine: conflicting signals or hallucination flag
        """
        has_solver = any(
            c.category in (CapabilityCategory.SOLVE, CapabilityCategory.DETECT)
            for c in self.selected_capabilities
        )
        verifier_pass = self.verifier_result.get("passed", False)
        has_conflict = self.verifier_result.get("conflict", False)
        has_hallucination = self.verifier_result.get("hallucination", False)

        if has_conflict or has_hallucination:
            self.quality_grade = QualityGrade.QUARANTINE
        elif has_solver and verifier_pass:
            self.quality_grade = QualityGrade.GOLD
        elif has_solver or verifier_pass:
            self.quality_grade = QualityGrade.SILVER
        else:
            self.quality_grade = QualityGrade.BRONZE

        return self.quality_grade

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trajectory_id": self.trajectory_id,
            "goal": self.goal.to_dict() if self.goal else None,
            "world_snapshot_before": self.world_snapshot_before.to_dict() if self.world_snapshot_before else None,
            "selected_capabilities": [c.to_dict() for c in self.selected_capabilities],
            "actions": [a.to_dict() for a in self.actions],
            "world_snapshot_after": self.world_snapshot_after.to_dict() if self.world_snapshot_after else None,
            "verifier_result": self.verifier_result,
            "quality_grade": self.quality_grade.value,
            "canonical_id": self.canonical_id,
            "profile": self.profile,
            "counterfactual_alternatives": self.counterfactual_alternatives,
            "trajectory_origin": self.trajectory_origin,
            "synthetic": self.synthetic,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_stored_dict(cls, d: Dict[str, Any]) -> "CaseTrajectory":
        """Reconstruct a CaseTrajectory from a stored dict (case_store JSON).

        Restores the fields that the night learning pipeline needs for
        quality grading, specialist training, and procedural memory.
        """
        # Reconstruct capabilities (needed for quality grading logic)
        caps = []
        for c in (d.get("selected_capabilities") or []):
            if isinstance(c, dict):
                cat_val = c.get("category", "retrieve")
                try:
                    cat = CapabilityCategory(cat_val)
                except ValueError:
                    cat = CapabilityCategory.RETRIEVE
                caps.append(CapabilityContract(
                    capability_id=c.get("capability_id", ""),
                    category=cat,
                    description=c.get("description", ""),
                    trust_score=c.get("trust_score", 0.5),
                ))

        # Parse quality grade
        grade_val = d.get("quality_grade", "bronze")
        try:
            grade = QualityGrade(grade_val)
        except ValueError:
            grade = QualityGrade.BRONZE

        # Parse created_at
        created = d.get("created_at", "")
        if isinstance(created, str) and created:
            try:
                dt = datetime.fromisoformat(created)
            except ValueError:
                dt = _now()
        else:
            dt = _now()

        return cls(
            trajectory_id=d.get("trajectory_id", _uuid()),
            quality_grade=grade,
            selected_capabilities=caps,
            verifier_result=d.get("verifier_result") or {},
            canonical_id=d.get("canonical_id", ""),
            profile=d.get("profile", ""),
            trajectory_origin=d.get("trajectory_origin", "observed"),
            synthetic=d.get("synthetic", False),
            created_at=dt,
        )


@dataclass
class MotiveActivation:
    """Runtime motive activation record — logged to audit, not a separate store.

    Tracks which motive influenced a decision, conflicts, and resolution.
    Valence is a prioritization weight, not an emotion.
    """
    motive_id: str
    valence: float = 0.0               # -1.0..+1.0 (negative=avoid, positive=pursue)
    intensity: float = 0.0             # 0.0..1.0
    triggered_by_goal_id: str = ""
    conflict_with: str = ""            # other motive_id if conflict
    resolution: str = ""               # how conflict was resolved
    timestamp: datetime = field(default_factory=_now)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "motive_id": self.motive_id,
            "valence": self.valence,
            "intensity": self.intensity,
            "triggered_by_goal_id": self.triggered_by_goal_id,
            "conflict_with": self.conflict_with,
            "resolution": self.resolution,
            "timestamp": self.timestamp.isoformat(),
        }
