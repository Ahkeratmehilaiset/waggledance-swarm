"""
Safe Action Bus — deny-by-default execution pipeline for all actions.

Every action flows through:
  1. PolicyEngine evaluation → approved/denied/needs_approval
  2. If approved → execute capability
  3. Post-execution → verify outcome
  4. Record in audit trail

No action bypasses the policy check.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from waggledance.core.domain.autonomy import (
    Action,
    ActionStatus,
    CapabilityContract,
    RiskScore,
)
from waggledance.core.policy.approvals import ApprovalManager
from waggledance.core.policy.policy_engine import PolicyDecision, PolicyEngine

log = logging.getLogger("waggledance.actions.bus")


@dataclass
class ActionResult:
    """Result of an action bus execution."""
    action: Action
    decision: PolicyDecision
    executed: bool = False
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    rolled_back: bool = False

    def to_dict(self) -> dict:
        return {
            "action_id": self.action.action_id,
            "capability_id": self.action.capability_id,
            "status": self.action.status.value,
            "executed": self.executed,
            "result": self.result,
            "error": self.error,
            "rolled_back": self.rolled_back,
            "decision": self.decision.to_dict(),
        }


# Type for capability executor functions
Executor = Callable[[Action], Dict[str, Any]]
Rollback = Callable[[Action], None]


class SafeActionBus:
    """
    Deny-by-default action bus.

    All actions must pass through policy evaluation before execution.
    Executors are registered per capability_id.
    """

    def __init__(
        self,
        policy_engine: PolicyEngine,
        approval_manager: Optional[ApprovalManager] = None,
    ):
        self._policy = policy_engine
        self._approvals = approval_manager or ApprovalManager()
        self._executors: Dict[str, Executor] = {}
        self._rollbacks: Dict[str, Rollback] = {}
        self._audit_trail: List[ActionResult] = []
        self._listeners: List[Callable[[ActionResult], None]] = []

    # ── Executor registration ─────────────────────────────

    def register_executor(
        self,
        capability_id: str,
        executor: Executor,
        rollback: Optional[Rollback] = None,
    ):
        """Register an executor (and optional rollback) for a capability."""
        self._executors[capability_id] = executor
        if rollback:
            self._rollbacks[capability_id] = rollback
        log.debug("Registered executor for %s", capability_id)

    def add_listener(self, listener: Callable[[ActionResult], None]):
        """Add a listener that gets called after every action result."""
        self._listeners.append(listener)

    # ── Main execution path ───────────────────────────────

    def submit(
        self,
        action: Action,
        capability: Optional[CapabilityContract] = None,
        quality_path: str = "bronze",
        context: Optional[Dict[str, Any]] = None,
    ) -> ActionResult:
        """
        Submit an action for policy evaluation and (if approved) execution.

        This is the ONLY way to execute actions in the system.
        """
        context = context or {}

        # 1. Policy evaluation
        decision = self._policy.evaluate(action, capability, quality_path, context)

        if decision.needs_approval:
            # Queue for approval — do not execute yet
            self._approvals.request_approval(
                action, decision.risk_score or RiskScore(), decision.reason,
            )
            result = ActionResult(action=action, decision=decision)
            self._record(result)
            return result

        if not decision.approved:
            # Denied — mark action as denied
            action.mark_denied(decision.reason)
            result = ActionResult(action=action, decision=decision)
            self._record(result)
            return result

        # 2. Execute
        return self._execute(action, decision)

    def execute_approved(self, action: Action) -> ActionResult:
        """Execute an action that has already been approved."""
        if action.status != ActionStatus.APPROVED:
            decision = PolicyDecision(
                approved=False,
                reason=f"Action not approved (status={action.status.value})",
            )
            result = ActionResult(action=action, decision=decision)
            self._record(result)
            return result

        decision = PolicyDecision(approved=True, reason="Pre-approved action")
        return self._execute(action, decision)

    def rollback(self, action: Action, reason: str = "") -> bool:
        """Attempt to roll back an executed action."""
        if action.status != ActionStatus.EXECUTED:
            log.warning("Cannot rollback action %s (status=%s)",
                        action.action_id, action.status.value)
            return False

        rollback_fn = self._rollbacks.get(action.capability_id)
        if rollback_fn is None:
            log.warning("No rollback handler for %s", action.capability_id)
            return False

        try:
            rollback_fn(action)
            action.mark_rolled_back(reason)
            log.info("Rolled back action %s: %s", action.action_id, reason)
            return True
        except Exception as e:
            log.error("Rollback failed for %s: %s", action.action_id, e)
            return False

    # ── Query ─────────────────────────────────────────────

    def audit_trail(self, limit: int = 100) -> List[ActionResult]:
        return self._audit_trail[-limit:]

    def stats(self) -> dict:
        total = len(self._audit_trail)
        executed = sum(1 for r in self._audit_trail if r.executed)
        denied = sum(1 for r in self._audit_trail if not r.executed and not r.decision.needs_approval)
        pending = sum(1 for r in self._audit_trail if r.decision.needs_approval)
        rolled = sum(1 for r in self._audit_trail if r.rolled_back)
        return {
            "total_actions": total,
            "executed": executed,
            "denied": denied,
            "pending_approval": pending,
            "rolled_back": rolled,
            "registered_executors": len(self._executors),
        }

    # ── Internal ──────────────────────────────────────────

    def _execute(self, action: Action, decision: PolicyDecision) -> ActionResult:
        """Actually execute an approved action."""
        executor = self._executors.get(action.capability_id)
        if executor is None:
            # No executor registered — dry run
            log.info("No executor for %s — dry run only", action.capability_id)
            result = ActionResult(
                action=action,
                decision=decision,
                executed=False,
                error="No executor registered",
            )
            self._record(result)
            return result

        try:
            exec_result = executor(action)
            action.mark_executed(exec_result)
            result = ActionResult(
                action=action,
                decision=decision,
                executed=True,
                result=exec_result,
            )
        except Exception as e:
            action.mark_denied(f"Execution failed: {e}")
            result = ActionResult(
                action=action,
                decision=decision,
                executed=False,
                error=str(e),
            )
            log.error("Action %s execution failed: %s", action.action_id, e)

        self._record(result)
        return result

    def _record(self, result: ActionResult):
        """Record result and notify listeners."""
        self._audit_trail.append(result)
        if len(self._audit_trail) > 2000:
            self._audit_trail = self._audit_trail[-1000:]

        for listener in self._listeners:
            try:
                listener(result)
            except Exception as e:
                log.warning("Listener error: %s", e)
