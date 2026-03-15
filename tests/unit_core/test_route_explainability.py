"""Tests for core.route_explainability — RouteExplanation and explain_route logic."""

import unittest

from core.route_explainability import RouteExplanation, explain_route


class TestRouteExplanationDataclass(unittest.TestCase):
    def test_default_values(self):
        exp = RouteExplanation(query="hello")
        self.assertEqual(exp.query, "hello")
        self.assertEqual(exp.route_type, "")
        self.assertEqual(exp.confidence, 0.0)
        self.assertFalse(exp.cache_hit)
        self.assertEqual(exp.matched_keywords, [])
        self.assertEqual(exp.fallback_chain, [])
        self.assertEqual(exp.skip_reasons, {})

    def test_to_dict_includes_all_fields(self):
        exp = RouteExplanation(
            query="test", route_type="llm", confidence=0.8,
            cache_hit=True, matched_keywords=["bee"],
            fallback_chain=["llm"], skip_reasons={"memory": "low"},
        )
        d = exp.to_dict()
        self.assertEqual(d["query"], "test")
        self.assertEqual(d["route_type"], "llm")
        self.assertEqual(d["confidence"], 0.8)
        self.assertTrue(d["cache_hit"])
        self.assertEqual(d["matched_keywords"], ["bee"])
        self.assertEqual(d["fallback_chain"], ["llm"])
        self.assertEqual(d["skip_reasons"], {"memory": "low"})


class TestExplainRouteHotcache(unittest.TestCase):
    def test_hotcache_hit(self):
        exp = explain_route("hello", hot_cache_hit=True)
        self.assertEqual(exp.route_type, "hotcache")
        self.assertEqual(exp.confidence, 1.0)
        self.assertTrue(exp.cache_hit)
        # No skip reasons when cache hits
        self.assertNotIn("hotcache", exp.skip_reasons)

    def test_hotcache_miss_records_skip(self):
        exp = explain_route("hello", hot_cache_hit=False)
        self.assertIn("hotcache", exp.skip_reasons)
        self.assertEqual(exp.skip_reasons["hotcache"], "no cache hit")


class TestExplainRouteMicromodel(unittest.TestCase):
    def test_micromodel_enabled_hit_high_confidence(self):
        exp = explain_route(
            "hello", micromodel_enabled=True,
            micromodel_hit=True, micromodel_confidence=0.95,
        )
        self.assertEqual(exp.route_type, "micromodel")
        self.assertEqual(exp.confidence, 0.95)

    def test_micromodel_disabled_skip(self):
        exp = explain_route("hello", micromodel_enabled=False)
        self.assertIn("micromodel", exp.skip_reasons)
        self.assertEqual(exp.skip_reasons["micromodel"], "disabled")

    def test_micromodel_no_hit_skip(self):
        exp = explain_route(
            "hello", micromodel_enabled=True, micromodel_hit=False,
        )
        self.assertIn("micromodel", exp.skip_reasons)
        self.assertEqual(exp.skip_reasons["micromodel"], "no hit")

    def test_micromodel_low_confidence_skip(self):
        exp = explain_route(
            "hello", micromodel_enabled=True,
            micromodel_hit=True, micromodel_confidence=0.50,
        )
        self.assertIn("micromodel", exp.skip_reasons)
        self.assertIn("0.50", exp.skip_reasons["micromodel"])

    def test_micromodel_boundary_confidence_085(self):
        """Confidence exactly 0.85 should NOT trigger micromodel (> not >=)."""
        exp = explain_route(
            "hello", micromodel_enabled=True,
            micromodel_hit=True, micromodel_confidence=0.85,
        )
        self.assertNotEqual(exp.route_type, "micromodel")
        self.assertIn("micromodel", exp.skip_reasons)


class TestExplainRouteMemory(unittest.TestCase):
    def test_memory_high_score(self):
        exp = explain_route("hello", memory_score=0.9)
        self.assertEqual(exp.route_type, "memory")
        self.assertEqual(exp.confidence, 0.9)

    def test_memory_low_score_skip(self):
        exp = explain_route("hello", memory_score=0.3)
        self.assertIn("memory", exp.skip_reasons)
        self.assertIn("0.30", exp.skip_reasons["memory"])

    def test_memory_boundary_070(self):
        """Score exactly 0.7 should NOT trigger memory (> not >=)."""
        exp = explain_route("hello", memory_score=0.7)
        self.assertNotEqual(exp.route_type, "memory")


class TestExplainRouteLLMShortcuts(unittest.TestCase):
    def test_time_query(self):
        exp = explain_route("what time is it", is_time_query=True)
        self.assertEqual(exp.route_type, "llm")
        self.assertEqual(exp.confidence, 0.8)

    def test_system_query(self):
        exp = explain_route("system status", is_system_query=True)
        self.assertEqual(exp.route_type, "llm")
        self.assertEqual(exp.confidence, 0.8)


class TestExplainRouteSwarm(unittest.TestCase):
    def test_swarm_long_query_multiple_keywords(self):
        long_query = "How do I manage varroa mites in the autumn when temperatures are dropping?"
        exp = explain_route(
            long_query, swarm_enabled=True,
            matched_keywords=["varroa", "autumn"],
        )
        self.assertEqual(exp.route_type, "swarm")
        self.assertEqual(exp.confidence, 0.7)

    def test_swarm_disabled_skip(self):
        exp = explain_route("hello world something longer query", swarm_enabled=False)
        self.assertIn("swarm", exp.skip_reasons)
        self.assertEqual(exp.skip_reasons["swarm"], "disabled")

    def test_swarm_short_query_falls_to_default(self):
        """Short query with swarm enabled should NOT use swarm."""
        exp = explain_route("hello", swarm_enabled=True, matched_keywords=["a", "b"])
        self.assertNotEqual(exp.route_type, "swarm")

    def test_swarm_too_few_keywords_falls_to_default(self):
        long_query = "How do I manage varroa mites in the autumn when temperatures drop?"
        exp = explain_route(long_query, swarm_enabled=True, matched_keywords=["varroa"])
        self.assertNotEqual(exp.route_type, "swarm")


class TestExplainRouteDefault(unittest.TestCase):
    def test_default_fallback(self):
        exp = explain_route("generic question")
        self.assertEqual(exp.route_type, "llm")
        self.assertEqual(exp.confidence, 0.6)
        self.assertEqual(exp.fallback_chain, ["memory", "micromodel", "llm", "swarm"])

    def test_default_has_skip_reasons(self):
        exp = explain_route("generic question")
        self.assertIn("hotcache", exp.skip_reasons)
        self.assertIn("micromodel", exp.skip_reasons)
        self.assertIn("memory", exp.skip_reasons)
        self.assertIn("swarm", exp.skip_reasons)


class TestExplainRouteKeywords(unittest.TestCase):
    def test_keywords_propagated(self):
        exp = explain_route("hello", matched_keywords=["bee", "honey"])
        self.assertEqual(exp.matched_keywords, ["bee", "honey"])

    def test_keywords_none_defaults_to_empty(self):
        exp = explain_route("hello", matched_keywords=None)
        self.assertEqual(exp.matched_keywords, [])


if __name__ == "__main__":
    unittest.main()
