# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
# See LICENSE-BUSL.txt and LICENSE-CORE.md
"""Audit projector — wraps legacy AuditLog as an autonomy black-box recorder.

Extends L1 audit to capture goal lifecycle, plan steps, action bus events,
policy decisions, capability selections, verifier results, world snapshot
commits, and specialist model promotions.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Lazy import to avoid circular deps; resolved at first use
_AuditLog = None


def _get_audit_log_class():
    global _AuditLog
    if _AuditLog is None:
        try:
            from core.audit_log import AuditLog
            _AuditLog = AuditLog
        except ImportError:
            _AuditLog = None
    return _AuditLog


@dataclass
class AuditEntry:
    """Structured autonomy audit record."""
    event_type: str
    payload: Dict[str, Any]
    source: str = ""
    timestamp: float = field(default_factory=time.time)
    goal_id: Optional[str] = None
    plan_id: Optional[str] = None
    action_id: Optional[str] = None
    capability_id: Optional[str] = None
    quality_grade: Optional[str] = None
    self_reflection: bool = False
    simulated: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_type": self.event_type,
            "payload": self.payload,
            "source": self.source,
            "timestamp": self.timestamp,
            "goal_id": self.goal_id,
            "plan_id": self.plan_id,
            "action_id": self.action_id,
            "capability_id": self.capability_id,
            "quality_grade": self.quality_grade,
            "self_reflection": self.self_reflection,
            "simulated": self.simulated,
        }


class AuditProjector:
    """Projects autonomy events into the legacy AuditLog as black-box records.

    Wraps core.audit_log.AuditLog, adding structured event types for
    the full autonomy lifecycle while preserving backward compatibility
    with existing audit queries.
    """

    AUTONOMY_EVENTS = frozenset({
        "goal.proposed", "goal.accepted", "goal.planned",
        "goal.executing", "goal.verified", "goal.failed",
        "goal.rolled_back", "goal.archived",
        "plan.created", "plan.step_started", "plan.step_completed",
        "action.requested", "action.executed", "action.denied",
        "action.rolled_back",
        "policy.checked", "policy.approved", "policy.denied",
        "capability.selected",
        "verification.passed", "verification.failed",
        "world.snapshot_committed",
        "specialist.canary_start", "specialist.promoted",
        "specialist.rolled_back",
        "case.recorded",
        # v3.2 MAGMA expansion: self-reflection + simulated events
        "reflection.started", "reflection.insight",
        "reflection.completed",
        "learning.case_graded", "learning.consolidated",
        "learning.dream_started", "learning.dream_completed",
        "simulated.trajectory_created", "simulated.counterfactual",
    })

    def __init__(self, audit_log=None, db_path: str = "data/audit_log.db"):
        if audit_log is not None:
            self._audit = audit_log
        else:
            cls = _get_audit_log_class()
            self._audit = cls(db_path=db_path) if cls else None
        self._lock = threading.Lock()
        self._recent: List[AuditEntry] = []
        self._max_recent = 500

    def record(self, entry: AuditEntry) -> None:
        """Record an autonomy event to both the legacy audit log and local buffer."""
        with self._lock:
            self._recent.append(entry)
            if len(self._recent) > self._max_recent:
                self._recent = self._recent[-self._max_recent:]

        if self._audit is not None:
            try:
                detail_parts = [f"{entry.event_type}"]
                if entry.goal_id:
                    detail_parts.append(f"goal={entry.goal_id}")
                if entry.action_id:
                    detail_parts.append(f"action={entry.action_id}")
                if entry.capability_id:
                    detail_parts.append(f"cap={entry.capability_id}")
                if entry.quality_grade:
                    detail_parts.append(f"grade={entry.quality_grade}")

                self._audit.record(
                    action=entry.event_type,
                    doc_id=entry.goal_id or entry.action_id or "",
                    agent_id=entry.source,
                    layer="autonomy",
                    details=" | ".join(detail_parts),
                )
            except Exception as exc:
                logger.warning("AuditProjector: legacy write failed: %s", exc)

    def record_goal_event(self, goal_id: str, status: str, detail: dict = None) -> None:
        """Shortcut for goal lifecycle events."""
        self.record(AuditEntry(
            event_type=f"goal.{status}",
            payload=detail or {},
            goal_id=goal_id,
        ))

    def record_action_event(self, action_id: str, status: str,
                            capability_id: str = "", detail: dict = None) -> None:
        """Shortcut for action bus events."""
        self.record(AuditEntry(
            event_type=f"action.{status}",
            payload=detail or {},
            action_id=action_id,
            capability_id=capability_id,
        ))

    def record_policy_decision(self, action_id: str, decision: str,
                               risk_score: float = 0.0, reason: str = "") -> None:
        """Record a policy engine decision."""
        self.record(AuditEntry(
            event_type=f"policy.{decision}",
            payload={"risk_score": risk_score, "reason": reason},
            action_id=action_id,
        ))

    def record_case(self, trajectory_id: str, quality_grade: str,
                    goal_id: str = "", detail: dict = None) -> None:
        """Record a case trajectory creation."""
        self.record(AuditEntry(
            event_type="case.recorded",
            payload=detail or {},
            goal_id=goal_id,
            quality_grade=quality_grade,
        ))

    def record_self_reflection(self, insight: str, goal_id: str = "",
                               detail: dict = None) -> None:
        """Record a self-reflection event (v3.2 MAGMA expansion)."""
        self.record(AuditEntry(
            event_type="reflection.insight",
            payload={"insight": insight, **(detail or {})},
            goal_id=goal_id,
            self_reflection=True,
        ))

    def record_simulated_event(self, event_type: str, goal_id: str = "",
                               quality_grade: str = "",
                               detail: dict = None) -> None:
        """Record a simulated/dream-mode event (v3.2 MAGMA expansion)."""
        self.record(AuditEntry(
            event_type=event_type,
            payload=detail or {},
            goal_id=goal_id,
            quality_grade=quality_grade,
            simulated=True,
        ))

    def query_self_reflections(self, limit: int = 50) -> List[AuditEntry]:
        """Return recent self-reflection audit entries."""
        with self._lock:
            matches = [e for e in reversed(self._recent) if e.self_reflection]
            return matches[:limit]

    def query_simulated(self, limit: int = 50) -> List[AuditEntry]:
        """Return recent simulated/dream-mode audit entries."""
        with self._lock:
            matches = [e for e in reversed(self._recent) if e.simulated]
            return matches[:limit]

    def query_by_goal(self, goal_id: str) -> List[AuditEntry]:
        """Return all audit entries for a given goal."""
        with self._lock:
            return [e for e in self._recent if e.goal_id == goal_id]

    def query_by_event_type(self, event_type: str, limit: int = 100) -> List[AuditEntry]:
        """Return recent entries of a specific event type."""
        with self._lock:
            matches = [e for e in reversed(self._recent) if e.event_type == event_type]
            return matches[:limit]

    def query_recent(self, limit: int = 50) -> List[AuditEntry]:
        """Return most recent autonomy audit entries."""
        with self._lock:
            return list(reversed(self._recent[-limit:]))

    def stats(self) -> Dict[str, Any]:
        """Aggregate statistics across autonomy events."""
        with self._lock:
            counts: Dict[str, int] = {}
            for e in self._recent:
                counts[e.event_type] = counts.get(e.event_type, 0) + 1
            return {
                "total_entries": len(self._recent),
                "event_counts": counts,
                "legacy_audit_available": self._audit is not None,
            }
