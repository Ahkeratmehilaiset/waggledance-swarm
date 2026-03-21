"""Integration tests for legacy capability adapters.

Tests registry binding, capability loader wiring, and end-to-end
capability invocation through the autonomy runtime pipeline.
"""

import pytest
from unittest.mock import MagicMock

from waggledance.core.capabilities.registry import CapabilityRegistry
from waggledance.bootstrap.capability_loader import bind_executors
from waggledance.core.capabilities.selector import CapabilitySelector


# ── Registry binding tests ───────────────────────────────────────

class TestRegistryBindings:
    """Verify that bind_executors wires up the new legacy adapters."""

    @pytest.fixture
    def loaded_registry(self):
        reg = CapabilityRegistry()
        bind_executors(reg)
        return reg

    def test_bind_executors_returns_positive(self, loaded_registry):
        # Should have at least the 6 reasoning engine adapters
        assert loaded_registry.executor_count() >= 6

    def test_intent_classifier_bound(self, loaded_registry):
        exec_ = loaded_registry.get_executor("sense.intent_classify")
        assert exec_ is not None
        assert exec_.CAPABILITY_ID == "sense.intent_classify"

    def test_hallucination_checker_bound_if_available(self, loaded_registry):
        exec_ = loaded_registry.get_executor("verify.hallucination")
        # May or may not be bound depending on whether core.hallucination_checker imports
        if exec_ is not None:
            assert exec_.CAPABILITY_ID == "verify.hallucination"

    def test_english_validator_bound_if_available(self, loaded_registry):
        exec_ = loaded_registry.get_executor("verify.english_output")
        if exec_ is not None:
            assert exec_.CAPABILITY_ID == "verify.english_output"

    def test_consensus_bound(self, loaded_registry):
        exec_ = loaded_registry.get_executor("verify.consensus")
        assert exec_ is not None
        assert exec_.CAPABILITY_ID == "verify.consensus"

    def test_finnish_normalizer_bound_if_available(self, loaded_registry):
        exec_ = loaded_registry.get_executor("normalize.finnish")
        if exec_ is not None:
            assert exec_.CAPABILITY_ID == "normalize.finnish"

    def test_seasonal_guard_bound_if_available(self, loaded_registry):
        exec_ = loaded_registry.get_executor("detect.seasonal_rules")
        if exec_ is not None:
            assert exec_.CAPABILITY_ID == "detect.seasonal_rules"

    def test_hot_cache_bound_if_available(self, loaded_registry):
        exec_ = loaded_registry.get_executor("retrieve.hot_cache")
        if exec_ is not None:
            assert exec_.CAPABILITY_ID == "retrieve.hot_cache"

    def test_vector_search_bound_if_available(self, loaded_registry):
        exec_ = loaded_registry.get_executor("retrieve.vector_search")
        if exec_ is not None:
            assert exec_.CAPABILITY_ID == "retrieve.vector_search"


# ── Registry capability definitions ──────────────────────────────

class TestRegistryCapabilities:
    """Verify all 25+ capability definitions are present."""

    @pytest.fixture
    def registry(self):
        return CapabilityRegistry()

    def test_all_capability_ids_present(self, registry):
        expected_ids = [
            "solve.math", "solve.symbolic", "solve.constraints",
            "solve.pattern_match", "solve.neural_classifier",
            "solve.thermal", "solve.stats", "solve.causal",
            "retrieve.hot_cache", "retrieve.semantic_search", "retrieve.vector_search",
            "normalize.finnish", "normalize.translate_fi_en",
            "sense.intent_classify", "sense.mqtt_ingest",
            "sense.home_assistant", "sense.camera_frigate",
            "verify.hallucination", "verify.consensus", "verify.english_output",
            "explain.llm_reasoning",
            "detect.seasonal_rules", "detect.anomaly",
            "optimize.schedule",
            "analyze.routing",
        ]
        for cap_id in expected_ids:
            assert registry.has(cap_id), f"Missing capability: {cap_id}"

    def test_total_count_at_least_25(self, registry):
        assert registry.count() >= 25

    def test_all_categories_present(self, registry):
        cats = registry.categories()
        for expected in ["solve", "retrieve", "normalize", "sense",
                         "verify", "explain", "detect", "optimize"]:
            assert expected in cats, f"Missing category: {expected}"


# ── Selector routing with new capabilities ───────────────────────

class TestSelectorWithLegacyCapabilities:
    """Verify that the selector can route to legacy capabilities."""

    @pytest.fixture
    def selector(self):
        reg = CapabilityRegistry()
        return CapabilitySelector(reg), reg

    def test_retrieval_routes_to_retrievers(self, selector):
        sel, _ = selector
        result = sel.select("retrieval", available_conditions={
            "embeddings_available": True, "index_loaded": True,
        })
        assert result.quality_path == "silver"
        ids = [c.capability_id for c in result.selected]
        assert any("retrieve." in i for i in ids)

    def test_seasonal_routes_to_detection(self, selector):
        sel, _ = selector
        result = sel.select("seasonal", available_conditions={
            "calendar_available": True,
        })
        assert result.quality_path == "gold"
        ids = [c.capability_id for c in result.selected]
        assert "detect.seasonal_rules" in ids

    def test_anomaly_routes_to_detection(self, selector):
        sel, _ = selector
        result = sel.select("anomaly", available_conditions={
            "baselines_available": True,
        })
        ids = [c.capability_id for c in result.selected]
        assert "detect.anomaly" in ids


# ── End-to-end capability invocation ─────────────────────────────

class TestEndToEndInvocation:
    """Verify that executors can be retrieved and invoked end-to-end."""

    @pytest.fixture
    def loaded_registry(self):
        reg = CapabilityRegistry()
        bind_executors(reg)
        return reg

    def test_intent_classifier_e2e(self, loaded_registry):
        exec_ = loaded_registry.get_executor("sense.intent_classify")
        if exec_ is None:
            pytest.skip("IntentClassifier not bound")
        result = exec_.execute(query="calculate 2+2")
        assert result["success"] is True
        assert "intent" in result

    def test_consensus_e2e(self, loaded_registry):
        exec_ = loaded_registry.get_executor("verify.consensus")
        if exec_ is None:
            pytest.skip("Consensus not bound")
        result = exec_.execute(topic="varroa treatment best practices")
        assert result["success"] is True
        assert result["deferred"] is True

    def test_hallucination_e2e(self, loaded_registry):
        exec_ = loaded_registry.get_executor("verify.hallucination")
        if exec_ is None:
            pytest.skip("HallucinationChecker not bound")
        result = exec_.execute(question="what is varroa?",
                                answer="Varroa is a parasitic mite.")
        assert result["success"] is True
        assert "passed" in result

    def test_english_validator_e2e(self, loaded_registry):
        exec_ = loaded_registry.get_executor("verify.english_output")
        if exec_ is None:
            pytest.skip("EnglishValidator not bound")
        result = exec_.execute(text="Use remedy for bee sickness")
        assert result["success"] is True
        assert "corrected" in result

    def test_seasonal_guard_e2e(self, loaded_registry):
        exec_ = loaded_registry.get_executor("detect.seasonal_rules")
        if exec_ is None:
            pytest.skip("SeasonalGuard not bound")
        result = exec_.execute(text="inspect the hive frames")
        assert result["success"] is True
        assert "has_violations" in result

    def test_stats_on_all_executors(self, loaded_registry):
        """All bound executors should support .stats() returning a dict."""
        for cap_id in loaded_registry.list_ids():
            exec_ = loaded_registry.get_executor(cap_id)
            if exec_ is not None:
                s = exec_.stats()
                assert isinstance(s, dict), f"{cap_id}.stats() should return dict"
                assert "capability_id" in s or "available" in s
