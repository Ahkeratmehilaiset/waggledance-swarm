"""
Phase 3 Tests: Capability Registry + Capability Selector.

Tests cover:
- CapabilityRegistry builtins loading
- Registration and unregistration
- Category filtering (solvers, retrievers, verifiers, sensors)
- CapabilitySelector solver-first strategy
- Selection with preconditions
- LLM fallback path
- Quality path determination (gold/silver/bronze)
- Explicit capability selection
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from waggledance.core.capabilities.registry import CapabilityRegistry
from waggledance.core.capabilities.selector import CapabilitySelector, SelectionResult
from waggledance.core.domain.autonomy import CapabilityCategory, CapabilityContract


# ── CapabilityRegistry ────────────────────────────────────────

class TestCapabilityRegistry:
    def test_builtins_loaded(self):
        reg = CapabilityRegistry()
        assert reg.count() > 0
        assert reg.has("solve.math")
        assert reg.has("solve.symbolic")
        assert reg.has("retrieve.hot_cache")
        assert reg.has("explain.llm_reasoning")

    def test_no_builtins(self):
        reg = CapabilityRegistry(load_builtins=False)
        assert reg.count() == 0

    def test_register_custom(self):
        reg = CapabilityRegistry(load_builtins=False)
        cap = CapabilityContract(
            capability_id="custom.solver",
            category=CapabilityCategory.SOLVE,
            description="Custom solver",
        )
        reg.register(cap)
        assert reg.has("custom.solver")
        assert reg.get("custom.solver").description == "Custom solver"

    def test_unregister(self):
        reg = CapabilityRegistry()
        assert reg.unregister("solve.math") is True
        assert reg.has("solve.math") is False
        assert reg.unregister("nonexistent") is False

    def test_get_nonexistent(self):
        reg = CapabilityRegistry()
        assert reg.get("nonexistent") is None

    def test_list_all(self):
        reg = CapabilityRegistry()
        all_caps = reg.list_all()
        assert len(all_caps) == reg.count()

    def test_list_ids(self):
        reg = CapabilityRegistry()
        ids = reg.list_ids()
        assert "solve.math" in ids
        assert "explain.llm_reasoning" in ids

    def test_list_by_category(self):
        reg = CapabilityRegistry()
        solvers = reg.list_by_category(CapabilityCategory.SOLVE)
        assert len(solvers) >= 3  # math, symbolic, constraints, pattern, neural
        assert all(c.category == CapabilityCategory.SOLVE for c in solvers)

    def test_solvers_shortcut(self):
        reg = CapabilityRegistry()
        solvers = reg.solvers()
        assert len(solvers) >= 3

    def test_retrievers_shortcut(self):
        reg = CapabilityRegistry()
        retrievers = reg.retrievers()
        assert len(retrievers) >= 3  # hot_cache, semantic, vector

    def test_verifiers_shortcut(self):
        reg = CapabilityRegistry()
        verifiers = reg.verifiers()
        assert len(verifiers) >= 2  # hallucination, consensus

    def test_sensors_shortcut(self):
        reg = CapabilityRegistry()
        sensors = reg.sensors()
        assert len(sensors) >= 2

    def test_categories(self):
        reg = CapabilityRegistry()
        cats = reg.categories()
        assert "solve" in cats
        assert "retrieve" in cats
        assert "verify" in cats
        assert "explain" in cats

    def test_stats(self):
        reg = CapabilityRegistry()
        s = reg.stats()
        assert s["total"] > 0
        assert "solve" in s["categories"]
        assert s["categories"]["solve"] >= 3

    def test_register_replaces(self):
        reg = CapabilityRegistry()
        original = reg.get("solve.math")
        new_cap = CapabilityContract(
            capability_id="solve.math",
            category=CapabilityCategory.SOLVE,
            description="Updated math solver",
        )
        reg.register(new_cap)
        assert reg.get("solve.math").description == "Updated math solver"


# ── CapabilitySelector ────────────────────────────────────────

class TestCapabilitySelector:
    @pytest.fixture
    def selector(self):
        reg = CapabilityRegistry()
        return CapabilitySelector(reg)

    def test_solver_intent_selects_solver(self, selector):
        result = selector.select("math", available_conditions={"numbers_present": True})
        assert result.quality_path == "gold"
        assert any(c.capability_id == "solve.math" for c in result.selected)

    def test_symbolic_intent(self, selector):
        result = selector.select("symbolic", available_conditions={
            "model_available": True, "inputs_present": True,
        })
        assert result.quality_path == "gold"
        assert any(c.capability_id == "solve.symbolic" for c in result.selected)

    def test_constraint_intent(self, selector):
        result = selector.select("constraint", available_conditions={
            "rules_loaded": True, "context_available": True,
        })
        assert result.quality_path == "gold"
        assert any(c.capability_id == "solve.constraints" for c in result.selected)

    def test_solver_with_verifier(self, selector):
        result = selector.select("math", available_conditions={
            "numbers_present": True, "response_available": True,
        })
        assert result.quality_path == "gold"
        # Should have solver + verifier
        categories = {c.category for c in result.selected}
        assert CapabilityCategory.SOLVE in categories
        assert CapabilityCategory.VERIFY in categories

    def test_detection_intent(self, selector):
        result = selector.select("seasonal", available_conditions={
            "calendar_available": True,
        })
        assert result.quality_path == "gold"
        assert any(c.category == CapabilityCategory.DETECT for c in result.selected)

    def test_retrieval_intent(self, selector):
        result = selector.select("retrieval", available_conditions={
            "embeddings_available": True, "index_loaded": True,
        })
        assert result.quality_path == "silver"
        assert len(result.selected) >= 1

    def test_retrieval_priority_order(self, selector):
        result = selector.select("retrieval", available_conditions={
            "embeddings_available": True, "index_loaded": True,
        })
        # hot_cache should come first if available
        ids = [c.capability_id for c in result.selected]
        if "retrieve.hot_cache" in ids and "retrieve.vector_search" in ids:
            assert ids.index("retrieve.hot_cache") < ids.index("retrieve.vector_search")

    def test_llm_fallback(self, selector):
        result = selector.select("chat", available_conditions={
            "ollama_available": True,
        })
        assert result.quality_path == "bronze"
        assert result.fallback_used is True
        assert any(c.capability_id == "explain.llm_reasoning" for c in result.selected)

    def test_no_capabilities_available(self, selector):
        # No conditions met, LLM also needs ollama_available
        result = selector.select("chat")
        assert result.fallback_used is True
        assert len(result.selected) == 0

    def test_solver_preconditions_not_met(self, selector):
        # Math intent but no numbers_present → still matches solvers with no preconditions
        result = selector.select("math", available_conditions={
            "ollama_available": True,
        })
        # Matches a solver (solve.stats/solve.causal have no preconditions)
        assert result.quality_path == "gold"

    def test_select_for_capability_ids(self, selector):
        result = selector.select_for_capability_ids([
            "solve.math", "verify.hallucination",
        ])
        assert len(result.selected) == 2
        assert result.quality_path == "gold"

    def test_select_for_unknown_ids(self, selector):
        result = selector.select_for_capability_ids(["nonexistent.solver"])
        assert len(result.selected) == 0

    def test_selection_result_to_dict(self, selector):
        result = selector.select("math", available_conditions={"numbers_present": True})
        d = result.to_dict()
        assert "selected" in d
        assert "quality_path" in d
        assert "reason" in d
        assert isinstance(d["selected"], list)

    def test_micromodel_fallback(self, selector):
        # Intent that doesn't match solver/detection/retrieval
        # but micromodels have model_loaded → micromodel picks up
        result = selector.select("unknown_intent", available_conditions={
            "model_loaded": True,
        })
        assert result.quality_path == "silver"
        assert any("solve.pattern_match" == c.capability_id or
                    "solve.neural_classifier" == c.capability_id
                    for c in result.selected)
