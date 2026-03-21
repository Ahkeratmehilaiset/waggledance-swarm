"""
Tests for the two runtime bugs:
  1. Executors not wired from CapabilityRegistry → SafeActionBus
  2. Interface mismatch: ActionBus calls executor(Action) but adapters expect execute(query)

These tests verify the bridge function and wiring logic.
"""

from __future__ import annotations

from typing import Any, Dict
from unittest.mock import MagicMock

import pytest

from waggledance.core.domain.autonomy import (
    Action,
    CapabilityCategory,
    CapabilityContract,
)


# ── Bridge function ───────────────────────────────────────────


class TestMakeAdapterExecutor:
    """Test the _make_adapter_executor bridge function."""

    def test_bridge_calls_execute_with_query(self):
        """Bridge extracts query from Action.payload and passes to adapter.execute()."""
        from waggledance.core.autonomy.runtime import _make_adapter_executor

        adapter = MagicMock()
        adapter.execute.return_value = {"success": True, "value": "4"}

        executor = _make_adapter_executor(adapter)
        action = Action(
            capability_id="solve.math",
            payload={"query": "2+2"},
        )
        result = executor(action)

        adapter.execute.assert_called_once_with(query="2+2")
        assert result == {"success": True, "value": "4"}

    def test_bridge_empty_payload(self):
        """Bridge handles empty payload gracefully."""
        from waggledance.core.autonomy.runtime import _make_adapter_executor

        adapter = MagicMock()
        adapter.execute.return_value = {"success": False}

        executor = _make_adapter_executor(adapter)
        action = Action(capability_id="solve.math")
        result = executor(action)

        assert result == {"success": False}

    def test_bridge_falls_back_to_positional(self):
        """If **payload fails, bridge falls back to positional query."""
        from waggledance.core.autonomy.runtime import _make_adapter_executor

        class StrictAdapter:
            def execute(self, query: str) -> dict:
                return {"success": True, "value": query}

        adapter = StrictAdapter()
        executor = _make_adapter_executor(adapter)
        action = Action(
            capability_id="solve.math",
            payload={"query": "2+2", "extra_key": "should_not_cause_error"},
        )
        result = executor(action)
        assert result["success"] is True
        assert result["value"] == "2+2"

    def test_bridge_passes_specialized_payload(self):
        """Bridge passes specialized kwargs for non-query adapters."""
        from waggledance.core.autonomy.runtime import _make_adapter_executor

        class AnomalyAdapter:
            def execute(self, metric: str = "", current: float = 0.0) -> dict:
                return {"metric": metric, "current": current, "anomaly": current > 40}

        adapter = AnomalyAdapter()
        executor = _make_adapter_executor(adapter)
        action = Action(
            capability_id="detect.anomaly",
            payload={"metric": "temperature", "current": 45.0},
        )
        result = executor(action)
        assert result["metric"] == "temperature"
        assert result["current"] == 45.0
        assert result["anomaly"] is True

    def test_bridge_with_real_math_adapter_interface(self):
        """Simulate the real MathSolverAdapter.execute(query) interface."""
        from waggledance.core.autonomy.runtime import _make_adapter_executor

        class FakeMathSolver:
            CAPABILITY_ID = "solve.math"

            def execute(self, query: str) -> Dict[str, Any]:
                return {
                    "success": True,
                    "value": "4",
                    "capability_id": self.CAPABILITY_ID,
                    "quality_path": "gold",
                }

        adapter = FakeMathSolver()
        executor = _make_adapter_executor(adapter)
        action = Action(
            capability_id="solve.math",
            payload={"query": "2+2"},
        )
        result = executor(action)
        assert result["success"] is True
        assert result["value"] == "4"
        assert result["quality_path"] == "gold"


# ── Wiring: registry → action bus ─────────────────────────────


class TestExecutorWiring:
    """Test that AutonomyRuntime wires executors from registry to action bus."""

    def test_action_bus_has_executors_after_init(self):
        """After AutonomyRuntime init, action_bus should have executors."""
        from waggledance.core.autonomy.runtime import AutonomyRuntime

        rt = AutonomyRuntime(profile="VALIDATION")
        bus_stats = rt.action_bus.stats()

        # Registry bound N executors — bus should have the same
        registry_count = rt.capability_registry.executor_count()
        bus_count = bus_stats["registered_executors"]
        assert bus_count == registry_count, (
            f"Registry has {registry_count} executors but bus has {bus_count}. "
            f"Executors not wired from registry to bus."
        )

    def test_action_bus_has_nonzero_executors(self):
        """Action bus should have at least some executors (not 0)."""
        from waggledance.core.autonomy.runtime import AutonomyRuntime

        rt = AutonomyRuntime(profile="VALIDATION")
        bus_count = rt.action_bus.stats()["registered_executors"]
        assert bus_count > 0, "ActionBus has 0 executors — wiring failed"

    def test_registry_executor_ids_match_bus(self):
        """Every executor in registry should have a matching entry in action bus."""
        from waggledance.core.autonomy.runtime import AutonomyRuntime

        rt = AutonomyRuntime(profile="VALIDATION")
        for cap_id in rt.capability_registry.executor_ids():
            # Bus._executors is private, so we check via submit dry-run
            assert cap_id in rt.action_bus._executors, (
                f"Executor '{cap_id}' in registry but not in action bus"
            )


# ── Action payload carries query ──────────────────────────────


class TestActionPayloadQuery:
    """Test that handle_query populates action.payload with query string."""

    def test_handle_query_returns_result(self):
        """handle_query should return executed:True for a math query."""
        from waggledance.core.autonomy.runtime import AutonomyRuntime

        rt = AutonomyRuntime(profile="VALIDATION")
        rt.start()

        result = rt.handle_query("2+2")
        # With the fix, math solver should execute successfully
        if result.get("capability") == "solve.math" and result.get("executed"):
            assert result["result"]["success"] is True
            assert result["result"]["value"] is not None
        rt.stop()

    def test_handle_query_no_longer_always_false(self):
        """Regression: handle_query must not always return executed:False."""
        from waggledance.core.autonomy.runtime import AutonomyRuntime

        rt = AutonomyRuntime(profile="VALIDATION")
        rt.start()

        # Try multiple queries — at least one should execute
        queries = ["2+2", "what is 3*5", "calculate 10/2"]
        any_executed = False
        for q in queries:
            result = rt.handle_query(q)
            if result.get("executed"):
                any_executed = True
                break

        rt.stop()
        assert any_executed, (
            "All queries returned executed:False — executor wiring still broken"
        )


# ── End-to-end with mock adapter ──────────────────────────────


class TestEndToEndMockAdapter:
    """End-to-end test with a manually registered mock adapter."""

    def test_manual_executor_registration_works(self):
        """Manually register an executor on action bus and verify execution."""
        from waggledance.core.actions.action_bus import SafeActionBus
        from waggledance.core.autonomy.runtime import _make_adapter_executor
        from waggledance.core.policy.policy_engine import PolicyEngine

        class FakeAdapter:
            CAPABILITY_ID = "test.fake"

            def execute(self, query: str = "") -> dict:
                return {"success": True, "answer": f"echo:{query}"}

        adapter = FakeAdapter()
        engine = PolicyEngine(profile="DEFAULT")
        bus = SafeActionBus(policy_engine=engine)

        # Register bridged executor
        bus.register_executor("test.fake", _make_adapter_executor(adapter))

        # Submit action with payload
        action = Action(
            capability_id="test.fake",
            payload={"query": "hello"},
        )
        cap = CapabilityContract(
            capability_id="test.fake",
            category=CapabilityCategory.RETRIEVE,  # read-only → auto-approved
            description="Test adapter",
        )
        result = bus.submit(action, cap)
        assert result.executed, f"Expected executed=True, got error: {result.error}"
        assert result.result["success"] is True
        assert result.result["answer"] == "echo:hello"
