"""Unit tests for legacy capability adapters.

10 adapter classes × ~5 tests each = ~50 tests.
Tests use direct instantiation with mock/stub backends where the legacy
module may not be importable in the test environment.
"""

import pytest
from unittest.mock import MagicMock, patch
from collections import OrderedDict


# ── HotCacheAdapter ──────────────────────────────────────────────

class TestHotCacheAdapter:
    def _make(self):
        from waggledance.adapters.capabilities.hot_cache_adapter import HotCacheAdapter
        # Create a minimal mock HotCache
        cache = MagicMock()
        cache.get.return_value = {"answer": "test answer", "score": 0.95}
        cache.stats = {"size": 1, "max_size": 500, "hit_rate": 1.0,
                       "total_hits": 1, "total_misses": 0}
        return HotCacheAdapter(hot_cache=cache), cache

    def test_capability_id(self):
        from waggledance.adapters.capabilities.hot_cache_adapter import HotCacheAdapter
        assert HotCacheAdapter(hot_cache=MagicMock()).CAPABILITY_ID == "retrieve.hot_cache"

    def test_available_with_cache(self):
        adapter, _ = self._make()
        assert adapter.available is True

    def test_available_without_cache(self):
        from waggledance.adapters.capabilities.hot_cache_adapter import HotCacheAdapter
        # When hot_cache=None and legacy module is available, adapter creates its own
        # When legacy module is NOT importable, it falls back to None
        adapter = HotCacheAdapter.__new__(HotCacheAdapter)
        adapter._cache = None
        adapter._call_count = 0
        adapter._hit_count = 0
        assert adapter.available is False

    def test_execute_hit(self):
        adapter, cache = self._make()
        result = adapter.execute(query="miten hoitaa varroa")
        assert result["success"] is True
        assert result["answer"] == "test answer"
        assert result["quality_path"] == "gold"
        assert "latency_ms" in result

    def test_execute_miss(self):
        adapter, cache = self._make()
        cache.get.return_value = None
        result = adapter.execute(query="unknown question")
        assert result["success"] is False
        assert result["error"] == "cache_miss"

    def test_stats(self):
        adapter, _ = self._make()
        adapter.execute(query="test")
        s = adapter.stats()
        assert s["calls"] == 1
        assert s["hits"] == 1


# ── SemanticSearchAdapter ────────────────────────────────────────

class TestSemanticSearchAdapter:
    def _make(self):
        from waggledance.adapters.capabilities.semantic_search_adapter import SemanticSearchAdapter
        chroma = MagicMock()
        chroma.search.return_value = [
            {"document": "test doc", "metadata": {}, "score": 0.9}
        ]
        return SemanticSearchAdapter(chromadb_adapter=chroma), chroma

    def test_capability_id(self):
        from waggledance.adapters.capabilities.semantic_search_adapter import SemanticSearchAdapter
        assert SemanticSearchAdapter().CAPABILITY_ID == "retrieve.semantic_search"

    def test_available_with_adapter(self):
        adapter, _ = self._make()
        assert adapter.available is True

    def test_available_without_adapter(self):
        from waggledance.adapters.capabilities.semantic_search_adapter import SemanticSearchAdapter
        assert SemanticSearchAdapter().available is False

    def test_execute_with_embedding(self):
        adapter, _ = self._make()
        result = adapter.execute(embedding=[0.1] * 768, top_k=3)
        assert result["success"] is True
        assert result["count"] == 1
        assert result["quality_path"] == "silver"

    def test_execute_no_embedding(self):
        adapter, _ = self._make()
        result = adapter.execute()
        assert result["success"] is False
        assert "No embedding" in result["error"]

    def test_stats(self):
        adapter, _ = self._make()
        adapter.execute(embedding=[0.1] * 768)
        s = adapter.stats()
        assert s["calls"] == 1
        assert s["successes"] == 1


# ── VectorSearchAdapter ──────────────────────────────────────────

class TestVectorSearchAdapter:
    def _make(self):
        from waggledance.adapters.capabilities.vector_search_adapter import VectorSearchAdapter
        registry = MagicMock()
        col = MagicMock()
        # Mock SearchResult objects
        sr = MagicMock()
        sr.doc_id = "doc1"
        sr.text = "test text"
        sr.score = 0.88
        sr.metadata = {}
        col.search.return_value = [sr]
        registry.get_or_create.return_value = col
        return VectorSearchAdapter(faiss_registry=registry), registry

    def test_capability_id(self):
        from waggledance.adapters.capabilities.vector_search_adapter import VectorSearchAdapter
        assert VectorSearchAdapter(faiss_registry=MagicMock()).CAPABILITY_ID == "retrieve.vector_search"

    def test_available(self):
        adapter, _ = self._make()
        assert adapter.available is True

    def test_execute_with_embedding(self):
        adapter, _ = self._make()
        result = adapter.execute(embedding=[0.1] * 768, collection="axioms")
        assert result["success"] is True
        assert result["count"] == 1
        assert result["quality_path"] == "silver"

    def test_execute_no_embedding(self):
        adapter, _ = self._make()
        result = adapter.execute()
        assert result["success"] is False

    def test_stats(self):
        adapter, _ = self._make()
        adapter.execute(embedding=[0.1] * 768)
        s = adapter.stats()
        assert s["calls"] == 1


# ── HallucinationCheckerAdapter ──────────────────────────────────

class TestHallucinationCheckerAdapter:
    def _make(self):
        from waggledance.adapters.capabilities.hallucination_checker_adapter import HallucinationCheckerAdapter
        checker = MagicMock()
        result = MagicMock()
        result.is_suspicious = False
        result.relevance = 0.85
        result.keyword_overlap = 0.7
        result.reason = ""
        checker.check.return_value = result
        return HallucinationCheckerAdapter(checker=checker), checker

    def test_capability_id(self):
        adapter, _ = self._make()
        assert adapter.CAPABILITY_ID == "verify.hallucination"

    def test_available(self):
        adapter, _ = self._make()
        assert adapter.available is True

    def test_execute_clean(self):
        adapter, _ = self._make()
        result = adapter.execute(question="what is varroa?",
                                  answer="Varroa is a mite parasite.")
        assert result["success"] is True
        assert result["passed"] is True
        assert result["is_suspicious"] is False
        assert result["quality_path"] == "gold"

    def test_execute_suspicious(self):
        adapter, checker = self._make()
        mock_result = MagicMock()
        mock_result.is_suspicious = True
        mock_result.relevance = 0.2
        mock_result.keyword_overlap = 0.0
        mock_result.reason = "low relevance"
        checker.check.return_value = mock_result
        result = adapter.execute(question="what is varroa?",
                                  answer="The weather is nice today.")
        assert result["passed"] is False
        assert result["is_suspicious"] is True

    def test_stats(self):
        adapter, _ = self._make()
        adapter.execute(question="q", answer="a")
        s = adapter.stats()
        assert s["calls"] == 1
        assert s["suspicious_count"] == 0


# ── EnglishValidatorAdapter ──────────────────────────────────────

class TestEnglishValidatorAdapter:
    def _make(self):
        from waggledance.adapters.capabilities.english_validator_adapter import EnglishValidatorAdapter
        validator = MagicMock()
        result = MagicMock()
        result.was_corrected = True
        result.original = "Use remedy for bee sickness"
        result.corrected = "Use treatment for bee disease"
        result.corrections = [("remedy", "treatment"), ("sickness", "disease")]
        result.correction_count = 2
        result.method = "domain"
        validator.validate.return_value = result
        return EnglishValidatorAdapter(validator=validator), validator

    def test_capability_id(self):
        adapter, _ = self._make()
        assert adapter.CAPABILITY_ID == "verify.english_output"

    def test_available(self):
        adapter, _ = self._make()
        assert adapter.available is True

    def test_execute_with_corrections(self):
        adapter, _ = self._make()
        result = adapter.execute(text="Use remedy for bee sickness")
        assert result["success"] is True
        assert result["passed"] is False  # was corrected
        assert result["correction_count"] == 2
        assert result["quality_path"] == "gold"

    def test_execute_no_corrections(self):
        adapter, validator = self._make()
        clean = MagicMock()
        clean.was_corrected = False
        clean.original = "Use treatment for varroa"
        clean.corrected = "Use treatment for varroa"
        clean.corrections = []
        clean.correction_count = 0
        clean.method = "none"
        validator.validate.return_value = clean
        result = adapter.execute(text="Use treatment for varroa")
        assert result["passed"] is True

    def test_stats(self):
        adapter, _ = self._make()
        adapter.execute(text="test")
        s = adapter.stats()
        assert s["calls"] == 1
        assert s["corrections"] == 1


# ── ConsensusAdapter ─────────────────────────────────────────────

class TestConsensusAdapter:
    def test_capability_id(self):
        from waggledance.adapters.capabilities.consensus_adapter import ConsensusAdapter
        assert ConsensusAdapter().CAPABILITY_ID == "verify.consensus"

    def test_available_always(self):
        from waggledance.adapters.capabilities.consensus_adapter import ConsensusAdapter
        # Available if RoundTableController class can be imported
        adapter = ConsensusAdapter()
        assert isinstance(adapter.available, bool)

    def test_execute_deferred(self):
        from waggledance.adapters.capabilities.consensus_adapter import ConsensusAdapter
        adapter = ConsensusAdapter()
        result = adapter.execute(topic="best varroa treatment")
        assert result["success"] is True
        assert result["deferred"] is True
        assert result["topic"] == "best varroa treatment"
        assert result["quality_path"] == "gold"

    def test_execute_with_controller(self):
        from waggledance.adapters.capabilities.consensus_adapter import ConsensusAdapter
        adapter = ConsensusAdapter(round_table=MagicMock())
        result = adapter.execute(topic="test")
        assert result["success"] is True
        assert result["controller_available"] is True

    def test_stats(self):
        from waggledance.adapters.capabilities.consensus_adapter import ConsensusAdapter
        adapter = ConsensusAdapter()
        adapter.execute(topic="test")
        s = adapter.stats()
        assert s["calls"] == 1


# ── FinnishNormalizerAdapter ─────────────────────────────────────

class TestFinnishNormalizerAdapter:
    def test_capability_id(self):
        from waggledance.adapters.capabilities.finnish_normalizer_adapter import FinnishNormalizerAdapter
        assert FinnishNormalizerAdapter().CAPABILITY_ID == "normalize.finnish"

    def test_available(self):
        from waggledance.adapters.capabilities.finnish_normalizer_adapter import FinnishNormalizerAdapter
        adapter = FinnishNormalizerAdapter()
        # Available depends on whether core.normalizer can be imported
        assert isinstance(adapter.available, bool)

    def test_execute_when_available(self):
        from waggledance.adapters.capabilities.finnish_normalizer_adapter import FinnishNormalizerAdapter
        adapter = FinnishNormalizerAdapter()
        if not adapter.available:
            pytest.skip("Finnish normalizer not available")
        result = adapter.execute(text="Miten hoidetaan mehiläisiä?")
        assert result["success"] is True
        assert result["quality_path"] == "gold"
        assert "normalized" in result

    def test_stats(self):
        from waggledance.adapters.capabilities.finnish_normalizer_adapter import FinnishNormalizerAdapter
        adapter = FinnishNormalizerAdapter()
        if adapter.available:
            adapter.execute(text="testi")
        s = adapter.stats()
        assert "capability_id" in s


# ── TranslationAdapter ───────────────────────────────────────────

class TestTranslationAdapter:
    def _make(self):
        from waggledance.adapters.capabilities.translation_adapter import TranslationAdapter
        proxy = MagicMock()
        proxy.fi_to_en.return_value = "How to treat varroa?"
        proxy.en_to_fi.return_value = "Miten hoitaa varroa?"
        return TranslationAdapter(proxy=proxy), proxy

    def test_capability_id(self):
        adapter, _ = self._make()
        assert adapter.CAPABILITY_ID == "normalize.translate_fi_en"

    def test_available(self):
        adapter, _ = self._make()
        assert adapter.available is True

    def test_execute_fi_to_en(self):
        adapter, _ = self._make()
        result = adapter.execute(text="Miten hoitaa varroa?", direction="fi_to_en")
        assert result["success"] is True
        assert result["translated"] == "How to treat varroa?"
        assert result["direction"] == "fi_to_en"
        assert result["quality_path"] == "gold"

    def test_execute_en_to_fi(self):
        adapter, _ = self._make()
        result = adapter.execute(text="How to treat varroa?", direction="en_to_fi")
        assert result["success"] is True
        assert result["translated"] == "Miten hoitaa varroa?"

    def test_stats(self):
        adapter, _ = self._make()
        adapter.execute(text="test")
        s = adapter.stats()
        assert s["calls"] == 1
        assert s["successes"] == 1


# ── IntentClassifierAdapter ──────────────────────────────────────

class TestIntentClassifierAdapter:
    def test_capability_id(self):
        from waggledance.adapters.capabilities.intent_classifier_adapter import IntentClassifierAdapter
        assert IntentClassifierAdapter().CAPABILITY_ID == "sense.intent_classify"

    def test_available(self):
        from waggledance.adapters.capabilities.intent_classifier_adapter import IntentClassifierAdapter
        adapter = IntentClassifierAdapter()
        assert isinstance(adapter.available, bool)

    def test_execute_fallback_to_solver_router(self):
        from waggledance.adapters.capabilities.intent_classifier_adapter import IntentClassifierAdapter
        adapter = IntentClassifierAdapter()
        result = adapter.execute(query="calculate 2+2")
        assert result["success"] is True
        assert result["intent"] == "math"
        assert result["source"] == "solver_router"

    def test_execute_with_smart_router(self):
        from waggledance.adapters.capabilities.intent_classifier_adapter import IntentClassifierAdapter
        router = MagicMock()
        route_result = MagicMock()
        route_result.layer = "math"
        route_result.confidence = 0.95
        route_result.reason = "keyword_match"
        route_result.decision_id = "d001"
        router.route.return_value = route_result
        adapter = IntentClassifierAdapter(smart_router=router)
        result = adapter.execute(query="laske 2+2")
        assert result["success"] is True
        assert result["source"] == "smart_router_v2"
        assert result["quality_path"] == "gold"

    def test_stats(self):
        from waggledance.adapters.capabilities.intent_classifier_adapter import IntentClassifierAdapter
        adapter = IntentClassifierAdapter()
        adapter.execute(query="hello")
        s = adapter.stats()
        assert s["calls"] == 1


# ── SeasonalGuardAdapter ─────────────────────────────────────────

class TestSeasonalGuardAdapter:
    def _make(self):
        from waggledance.adapters.capabilities.seasonal_guard_adapter import SeasonalGuardAdapter
        guard = MagicMock()
        guard.check.return_value = []
        guard.current_month = 3
        guard.rule_count = 8
        return SeasonalGuardAdapter(guard=guard), guard

    def test_capability_id(self):
        adapter, _ = self._make()
        assert adapter.CAPABILITY_ID == "detect.seasonal_rules"

    def test_available(self):
        adapter, _ = self._make()
        assert adapter.available is True

    def test_execute_no_violations(self):
        adapter, _ = self._make()
        result = adapter.execute(text="inspect the hive frames")
        assert result["success"] is True
        assert result["has_violations"] is False
        assert result["violation_count"] == 0
        assert result["quality_path"] == "gold"

    def test_execute_with_violations(self):
        adapter, guard = self._make()
        violation = MagicMock()
        violation.to_dict.return_value = {
            "rule": "honey_extraction",
            "reason_fi": "Hunajaa ei linkota maaliskuussa",
            "reason_en": "No honey extraction in March",
            "matched_keyword": "linkoa",
        }
        guard.check.return_value = [violation]
        result = adapter.execute(text="linkoa hunajaa nyt")
        assert result["has_violations"] is True
        assert result["violation_count"] == 1

    def test_stats(self):
        adapter, _ = self._make()
        adapter.execute(text="test")
        s = adapter.stats()
        assert s["calls"] == 1
        assert s["guard"]["rule_count"] == 8
