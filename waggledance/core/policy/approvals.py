"""
Approvals — human approval workflow for high-risk actions.

When the PolicyEngine determines an action requires approval,
the ApprovalManager queues the request and waits for human decision.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from waggledance.core.domain.autonomy import Action, ActionStatus, RiskScore

log = logging.getLogger("waggledance.policy.approvals")


@dataclass
class ApprovalRequest:
    """A pending approval request."""
    request_id: str
    action: Action
    risk_score: RiskScore
    reason: str
    created_at: float = field(default_factory=time.time)
    resolved_at: Optional[float] = None
    approved: Optional[bool] = None
    resolved_by: str = ""
    resolution_note: str = ""

    @property
    def is_pending(self) -> bool:
        return self.approved is None

    @property
    def age_seconds(self) -> float:
        return time.time() - self.created_at

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "action_id": self.action.action_id,
            "capability_id": self.action.capability_id,
            "risk_composite": self.risk_score.composite,
            "reason": self.reason,
            "created_at": self.created_at,
            "is_pending": self.is_pending,
            "approved": self.approved,
            "resolved_by": self.resolved_by,
        }


class ApprovalManager:
    """
    Manages human approval workflow for high-risk actions.

    Actions that require approval are queued. Operators can
    approve or deny them via the API or dashboard.
    """

    def __init__(self, timeout_seconds: float = 3600.0):
        self._pending: Dict[str, ApprovalRequest] = {}
        self._history: List[ApprovalRequest] = []
        self._timeout_seconds = timeout_seconds
        self._request_counter = 0

    def request_approval(
        self,
        action: Action,
        risk_score: RiskScore,
        reason: str = "",
    ) -> ApprovalRequest:
        """Queue an action for human approval."""
        self._request_counter += 1
        request_id = f"apr_{self._request_counter:04d}"

        request = ApprovalRequest(
            request_id=request_id,
            action=action,
            risk_score=risk_score,
            reason=reason,
        )
        self._pending[request_id] = request
        log.info("Approval requested: %s for action %s (risk=%.2f)",
                 request_id, action.capability_id, risk_score.composite)
        return request

    def approve(self, request_id: str, resolved_by: str = "operator", note: str = "") -> bool:
        """Approve a pending request."""
        request = self._pending.get(request_id)
        if request is None or not request.is_pending:
            return False

        request.approved = True
        request.resolved_at = time.time()
        request.resolved_by = resolved_by
        request.resolution_note = note
        request.action.status = ActionStatus.APPROVED

        self._finalize(request_id)
        log.info("Approved: %s by %s", request_id, resolved_by)
        return True

    def deny(self, request_id: str, resolved_by: str = "operator", note: str = "") -> bool:
        """Deny a pending request."""
        request = self._pending.get(request_id)
        if request is None or not request.is_pending:
            return False

        request.approved = False
        request.resolved_at = time.time()
        request.resolved_by = resolved_by
        request.resolution_note = note
        request.action.mark_denied(f"Denied by {resolved_by}: {note}")

        self._finalize(request_id)
        log.info("Denied: %s by %s", request_id, resolved_by)
        return True

    def get_pending(self) -> List[ApprovalRequest]:
        """Get all pending approval requests."""
        self._expire_stale()
        return [r for r in self._pending.values() if r.is_pending]

    def get_request(self, request_id: str) -> Optional[ApprovalRequest]:
        return self._pending.get(request_id) or self._find_in_history(request_id)

    def pending_count(self) -> int:
        self._expire_stale()
        return sum(1 for r in self._pending.values() if r.is_pending)

    def stats(self) -> dict:
        self._expire_stale()
        total_resolved = len(self._history)
        approved_count = sum(1 for r in self._history if r.approved)
        denied_count = sum(1 for r in self._history if r.approved is False)
        return {
            "pending": self.pending_count(),
            "total_resolved": total_resolved,
            "approved": approved_count,
            "denied": denied_count,
            "timeout_seconds": self._timeout_seconds,
        }

    # ── Internal ──────────────────────────────────────────

    def _finalize(self, request_id: str):
        """Move from pending to history."""
        request = self._pending.pop(request_id, None)
        if request:
            self._history.append(request)
            if len(self._history) > 500:
                self._history = self._history[-250:]

    def _expire_stale(self):
        """Auto-deny requests that have timed out."""
        now = time.time()
        expired = [
            rid for rid, r in self._pending.items()
            if r.is_pending and (now - r.created_at) > self._timeout_seconds
        ]
        for rid in expired:
            log.warning("Approval request %s timed out, auto-denying", rid)
            self.deny(rid, resolved_by="timeout", note="Request timed out")

    def _find_in_history(self, request_id: str) -> Optional[ApprovalRequest]:
        for r in self._history:
            if r.request_id == request_id:
                return r
        return None
