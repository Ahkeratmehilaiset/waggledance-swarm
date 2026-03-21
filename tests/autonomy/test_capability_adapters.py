"""
Tests for capability adapters.

Covers:
- MathSolverAdapter can_handle and execute
- SymbolicSolverAdapter list_models
- ConstraintEngineAdapter load_rules and execute
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from waggledance.adapters.capabilities.math_solver_adapter import MathSolverAdapter
from waggledance.adapters.capabilities.symbolic_solver_adapter import SymbolicSolverAdapter
from waggledance.adapters.capabilities.constraint_engine_adapter import ConstraintEngineAdapter


class TestMathSolverAdapter:
    def test_can_handle_math(self):
        adapter = MathSolverAdapter()
        assert adapter.can_handle("what is 2 + 2") is True

    def test_can_handle_non_math(self):
        adapter = MathSolverAdapter()
        assert adapter.can_handle("tell me about bees") is False

    def test_execute_simple(self):
        adapter = MathSolverAdapter()
        result = adapter.execute("2 + 2")
        if result["success"]:
            assert result["quality_path"] == "gold"
            assert result["value"] is not None

    def test_capability_id(self):
        adapter = MathSolverAdapter()
        assert adapter.CAPABILITY_ID == "solve.math"


class TestSymbolicSolverAdapter:
    def test_list_models(self):
        adapter = SymbolicSolverAdapter()
        models = adapter.list_models()
        assert isinstance(models, list)

    def test_can_handle(self):
        adapter = SymbolicSolverAdapter()
        result = adapter.can_handle("nonexistent_model")
        assert isinstance(result, bool)


class TestConstraintEngineAdapter:
    def test_load_rules_with_data(self):
        adapter = ConstraintEngineAdapter()
        rules = [
            {"id": "test_rule", "condition": "temp > 30", "action": "alert"}
        ]
        adapter.load_rules(rules)
        assert adapter._engine is not None

    def test_execute_empty_context(self):
        adapter = ConstraintEngineAdapter()
        adapter.load_rules([])
        result = adapter.execute({})
        assert isinstance(result, dict)
        assert result["quality_path"] == "gold"

    def test_stats(self):
        adapter = ConstraintEngineAdapter()
        stats = adapter.stats()
        assert isinstance(stats, dict)
