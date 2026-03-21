"""
Phase 4 Tests: Safe Action Bus.

Tests cover:
- Deny-by-default behavior (no executor registered)
- Executor registration and execution
- Rollback support
- Policy integration (approved/denied/needs_approval)
- Audit trail
- Listener notifications
- ActionResult serialization
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from waggledance.core.actions.action_bus import ActionResult, SafeActionBus
from waggledance.core.domain.autonomy import (
    Action,
    ActionStatus,
    CapabilityCategory,
    CapabilityContract,
    RiskScore,
)
from waggledance.core.policy.constitution import Constitution, ConstitutionRule
from waggledance.core.policy.policy_engine import PolicyEngine


def _make_allow_engine():
    """Engine that allows low-risk actions."""
    constitution = Constitution(
        default_action="deny",
        global_rules=[
            ConstitutionRule(
                rule_id="allow_readonly",
                applies_to=["sense", "retrieve", "normalize", "verify", "explain"],
                enforcement="allow",
            ),
            ConstitutionRule(
                rule_id="allow_low_risk",
                applies_to=["act", "learn", "solve"],
                conditions={"composite_lt": 0.5},
                enforcement="allow",
            ),
            ConstitutionRule(
                rule_id="require_approval_high",
                applies_to=["act"],
                conditions={"composite_gt": 0.7},
                enforcement="require_approval",
            ),
        ],
    )
    return PolicyEngine(constitution=constitution, profile="TEST")


def _make_deny_engine():
    """Engine that denies everything (pure deny-by-default)."""
    constitution = Constitution(default_action="deny", global_rules=[])
    return PolicyEngine(constitution=constitution, profile="TEST")


class TestSafeActionBusBasic:
    def test_submit_readonly_approved(self):
        bus = SafeActionBus(_make_allow_engine())
        action = Action(capability_id="sense.mqtt")
        cap = CapabilityContract("sense.mqtt", CapabilityCategory.SENSE)
        result = bus.submit(action, cap)
        assert result.decision.approved is True

    def test_submit_denied_by_default(self):
        bus = SafeActionBus(_make_deny_engine())
        action = Action(capability_id="act.test")
        cap = CapabilityContract("act.test", CapabilityCategory.ACT)
        result = bus.submit(action, cap)
        assert result.decision.approved is False
        assert action.status == ActionStatus.DENIED

    def test_submit_no_executor_dry_run(self):
        bus = SafeActionBus(_make_allow_engine())
        action = Action(capability_id="solve.math")
        cap = CapabilityContract("solve.math", CapabilityCategory.SOLVE)
        result = bus.submit(action, cap, quality_path="gold")
        # Allowed by policy but no executor → dry run
        if result.decision.approved:
            assert result.executed is False
            assert result.error == "No executor registered"


class TestSafeActionBusExecution:
    @pytest.fixture
    def bus(self):
        engine = _make_allow_engine()
        bus = SafeActionBus(engine)

        def math_executor(action):
            return {"answer": 42}

        def math_rollback(action):
            pass

        bus.register_executor("solve.math", math_executor, math_rollback)
        return bus

    def test_execute_with_registered_executor(self, bus):
        action = Action(capability_id="solve.math")
        cap = CapabilityContract("solve.math", CapabilityCategory.SOLVE)
        result = bus.submit(action, cap, quality_path="gold")
        assert result.executed is True
        assert result.result == {"answer": 42}
        assert action.status == ActionStatus.EXECUTED

    def test_rollback_executed_action(self, bus):
        action = Action(capability_id="solve.math")
        cap = CapabilityContract("solve.math", CapabilityCategory.SOLVE)
        bus.submit(action, cap, quality_path="gold")
        assert action.status == ActionStatus.EXECUTED
        assert bus.rollback(action, "Verifier failed") is True
        assert action.status == ActionStatus.ROLLED_BACK

    def test_rollback_non_executed_fails(self, bus):
        action = Action(capability_id="solve.math")
        assert bus.rollback(action) is False

    def test_rollback_no_handler(self, bus):
        bus.register_executor("act.no_rollback", lambda a: {"ok": True})
        action = Action(capability_id="act.no_rollback")
        action.status = ActionStatus.EXECUTED
        assert bus.rollback(action) is False

    def test_executor_failure(self, bus):
        def failing_executor(action):
            raise RuntimeError("Boom!")

        bus.register_executor("act.fail", failing_executor)
        action = Action(capability_id="act.fail")
        cap = CapabilityContract("act.fail", CapabilityCategory.SOLVE)  # solve = low risk
        result = bus.submit(action, cap, quality_path="gold")
        if result.decision.approved:
            assert result.executed is False
            assert "Boom!" in result.error

    def test_execute_approved_action(self, bus):
        action = Action(capability_id="solve.math")
        action.status = ActionStatus.APPROVED
        result = bus.execute_approved(action)
        assert result.executed is True

    def test_execute_not_approved_fails(self, bus):
        action = Action(capability_id="solve.math")
        # Status is DRY_RUN, not APPROVED
        result = bus.execute_approved(action)
        assert result.executed is False
        assert "not approved" in result.decision.reason.lower()


class TestSafeActionBusAudit:
    def test_audit_trail(self):
        bus = SafeActionBus(_make_allow_engine())
        for i in range(5):
            action = Action(capability_id="sense.test")
            cap = CapabilityContract("sense.test", CapabilityCategory.SENSE)
            bus.submit(action, cap)
        trail = bus.audit_trail()
        assert len(trail) == 5

    def test_stats(self):
        bus = SafeActionBus(_make_allow_engine())
        action = Action(capability_id="sense.test")
        cap = CapabilityContract("sense.test", CapabilityCategory.SENSE)
        bus.submit(action, cap)
        s = bus.stats()
        assert s["total_actions"] == 1
        assert "registered_executors" in s

    def test_listener_called(self):
        bus = SafeActionBus(_make_allow_engine())
        results = []
        bus.add_listener(lambda r: results.append(r))

        action = Action(capability_id="sense.test")
        cap = CapabilityContract("sense.test", CapabilityCategory.SENSE)
        bus.submit(action, cap)
        assert len(results) == 1

    def test_action_result_to_dict(self):
        bus = SafeActionBus(_make_allow_engine())
        action = Action(capability_id="sense.test")
        cap = CapabilityContract("sense.test", CapabilityCategory.SENSE)
        result = bus.submit(action, cap)
        d = result.to_dict()
        assert d["capability_id"] == "sense.test"
        assert "decision" in d


class TestSafeActionBusApprovalFlow:
    def test_high_risk_queued_for_approval(self):
        engine = _make_allow_engine()
        bus = SafeActionBus(engine)
        # Force high risk via overrides
        engine._risk_scorer.set_override("act.critical", {
            "severity": 0.9, "reversibility": 0.1,
            "observability": 0.3, "uncertainty": 0.8, "blast_radius": 0.8,
        })
        action = Action(capability_id="act.critical")
        cap = CapabilityContract("act.critical", CapabilityCategory.ACT, rollback_possible=False)
        result = bus.submit(action, cap, quality_path="bronze")
        # Should be queued for approval or denied
        assert result.executed is False
        assert result.decision.needs_approval or not result.decision.approved
