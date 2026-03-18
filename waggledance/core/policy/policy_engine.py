"""
Policy Engine — deny-by-default evaluation of all actions.

Every write or act operation must pass through the policy engine before
execution. The engine evaluates:
  1. RiskScore dimensions
  2. Constitution rules (global + profile-specific)
  3. Approval requirements
  4. Safety cases

Returns a PolicyDecision: approved/denied/needs_approval with reason.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from waggledance.core.domain.autonomy import (
    Action,
    ActionStatus,
    CapabilityCategory,
    CapabilityContract,
    RiskScore,
)
from waggledance.core.policy.constitution import (
    Constitution,
    ConstitutionRule,
    ProfileThresholds,
    load_constitution,
)
from waggledance.core.policy.risk_scoring import RiskScorer

log = logging.getLogger("waggledance.policy.engine")

# Read-only categories that bypass policy checks
_READ_ONLY_CATEGORIES = frozenset({
    "sense", "retrieve", "normalize", "estimate",
    "detect", "explain", "verify",
})


@dataclass
class PolicyDecision:
    """Result of a policy evaluation."""
    approved: bool
    reason: str
    risk_score: Optional[RiskScore] = None
    matched_rules: List[str] = field(default_factory=list)
    needs_approval: bool = False
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "approved": self.approved,
            "reason": self.reason,
            "risk_score": self.risk_score.to_dict() if self.risk_score else None,
            "matched_rules": self.matched_rules,
            "needs_approval": self.needs_approval,
            "timestamp": self.timestamp,
        }


class PolicyEngine:
    """
    Deny-by-default policy engine.

    All actions are DENIED unless a constitution rule explicitly allows them.
    """

    def __init__(
        self,
        constitution: Optional[Constitution] = None,
        risk_scorer: Optional[RiskScorer] = None,
        profile: str = "DEFAULT",
        constitution_path: Optional[Path] = None,
    ):
        self._constitution = constitution or load_constitution(constitution_path)
        self._risk_scorer = risk_scorer or RiskScorer()
        self._profile = profile
        self._decision_log: List[PolicyDecision] = []

        # Safety case builder for non-readonly actions
        self._safety_builder = None
        try:
            from waggledance.core.policy.safety_cases import SafetyCaseBuilder
            self._safety_builder = SafetyCaseBuilder()
        except Exception:
            pass

    @property
    def profile(self) -> str:
        return self._profile

    @profile.setter
    def profile(self, value: str):
        self._profile = value

    def evaluate(
        self,
        action: Action,
        capability: Optional[CapabilityContract] = None,
        quality_path: str = "bronze",
        context: Optional[Dict[str, Any]] = None,
    ) -> PolicyDecision:
        """
        Evaluate whether an action should be allowed.

        Args:
            action: the action to evaluate
            capability: the capability being invoked
            quality_path: gold/silver/bronze quality path
            context: additional context for evaluation

        Returns:
            PolicyDecision with approved/denied/needs_approval
        """
        context = context or {}
        category = capability.category.value if capability else "act"

        # Read-only operations are always allowed
        if category in _READ_ONLY_CATEGORIES:
            decision = PolicyDecision(
                approved=True,
                reason=f"Read-only category '{category}' — auto-approved",
                matched_rules=["auto_approve_readonly"],
            )
            self._record(decision)
            return decision

        # Compute risk score
        risk = self._risk_scorer.score(capability, quality_path, context)
        action.risk_score = risk

        # Build evaluation context
        eval_ctx = self._build_eval_context(action, capability, risk, quality_path, context)

        # Get applicable rules (global + profile)
        rules = self._constitution.get_rules_for_profile(self._profile)

        # Evaluate rules — find first matching allow or deny
        matched: List[str] = []
        allow_found = False
        deny_found = False
        needs_approval = False
        deny_reason = ""
        allow_reason = ""

        for rule in rules:
            if not rule.matches_category(category):
                continue
            if not rule.evaluate_conditions(eval_ctx):
                continue

            matched.append(rule.rule_id)

            if rule.enforcement == "allow" or rule.enforcement == "allow_emergency":
                allow_found = True
                allow_reason = rule.description or rule.rule_id
            elif rule.enforcement == "deny":
                # Check if 'unless' condition is met
                if rule.unless and eval_ctx.get(rule.unless, False):
                    continue
                deny_found = True
                deny_reason = rule.description or rule.rule_id
            elif rule.enforcement == "require_approval":
                needs_approval = True
            elif rule.enforcement == "require_verifier":
                if quality_path != "gold":
                    needs_approval = True

        # Apply profile thresholds for auto-approve
        thresholds = self._constitution.get_thresholds(self._profile)
        threshold_auto = False
        if (not deny_found and not needs_approval
                and risk.composite <= thresholds.max_composite_auto_approve
                and risk.severity <= thresholds.max_severity_auto_approve):
            threshold_auto = True
            if not allow_found:
                allow_found = True
                allow_reason = (
                    f"Within profile thresholds "
                    f"(composite={risk.composite:.2f}<="
                    f"{thresholds.max_composite_auto_approve})")
                matched.append("profile_threshold_auto_approve")

        # Also check RiskScore approval requirement
        if risk.requires_approval:
            needs_approval = True

        # Decision logic: deny beats allow (safety-first)
        if deny_found:
            decision = PolicyDecision(
                approved=False,
                reason=f"Denied by rule: {deny_reason}",
                risk_score=risk,
                matched_rules=matched,
            )
        elif needs_approval:
            decision = PolicyDecision(
                approved=False,
                reason="Requires human approval",
                risk_score=risk,
                matched_rules=matched,
                needs_approval=True,
            )
        elif allow_found:
            decision = PolicyDecision(
                approved=True,
                reason=f"Allowed by rule: {allow_reason}",
                risk_score=risk,
                matched_rules=matched,
            )
        else:
            # Default action from constitution
            if self._constitution.default_action == "allow":
                decision = PolicyDecision(
                    approved=True,
                    reason="Default allow (no matching rules)",
                    risk_score=risk,
                    matched_rules=matched,
                )
            else:
                decision = PolicyDecision(
                    approved=False,
                    reason="Denied by default (no explicit allow rule)",
                    risk_score=risk,
                    matched_rules=matched,
                )

        # Build safety case for audit trail (non-readonly actions only)
        if self._safety_builder:
            try:
                self._safety_builder.build_case(
                    action_id=action.capability_id,
                    capability_id=action.capability_id,
                    claim=f"Action '{action.capability_id}' is safe to execute",
                    risk_score=risk,
                )
            except Exception as exc:
                log.debug("Safety case build failed: %s", exc)

        self._record(decision)
        return decision

    def approve_action(self, action: Action) -> PolicyDecision:
        """Manually approve an action that required approval."""
        action.status = ActionStatus.APPROVED
        decision = PolicyDecision(
            approved=True,
            reason="Manually approved by operator",
            risk_score=action.risk_score,
            matched_rules=["manual_approval"],
        )
        self._record(decision)
        return decision

    def recent_decisions(self, limit: int = 50) -> List[PolicyDecision]:
        """Get recent policy decisions."""
        return self._decision_log[-limit:]

    def stats(self) -> dict:
        """Policy engine statistics."""
        total = len(self._decision_log)
        approved = sum(1 for d in self._decision_log if d.approved)
        denied = sum(1 for d in self._decision_log if not d.approved and not d.needs_approval)
        pending = sum(1 for d in self._decision_log if d.needs_approval)
        thresholds = self._constitution.get_thresholds(self._profile)
        return {
            "total_decisions": total,
            "approved": approved,
            "denied": denied,
            "pending_approval": pending,
            "profile": self._profile,
            "constitution_version": self._constitution.version,
            "max_composite_auto_approve": thresholds.max_composite_auto_approve,
            "max_severity_auto_approve": thresholds.max_severity_auto_approve,
        }

    # ── Internal ──────────────────────────────────────────

    def _build_eval_context(
        self,
        action: Action,
        capability: Optional[CapabilityContract],
        risk: RiskScore,
        quality_path: str,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Build flat context dict for rule evaluation."""
        ctx = {
            "severity": risk.severity,
            "reversibility": risk.reversibility,
            "observability": risk.observability,
            "uncertainty": risk.uncertainty,
            "blast_radius": risk.blast_radius,
            "composite": risk.composite,
            "approval_required": risk.approval_required,
            "approval_granted": context.get("approval_granted", False),
            "quality_path": quality_path,
            "capability_id": action.capability_id,
            "goal_id": action.goal_id,
            **context,
        }
        if capability:
            ctx["category"] = capability.category.value
            ctx["rollback_possible"] = capability.rollback_possible
        return ctx

    def _record(self, decision: PolicyDecision):
        """Record decision in internal log (bounded)."""
        self._decision_log.append(decision)
        if len(self._decision_log) > 1000:
            self._decision_log = self._decision_log[-500:]
