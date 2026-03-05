"""
MAGMA: Cognitive Graph — Tests
Suite #42: ~20 tests
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.cognitive_graph import CognitiveGraph, EDGE_TYPES


class TestNodeOperations(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "graph.json")
        self.g = CognitiveGraph(self.path)

    def test_add_node(self):
        self.g.add_node("n1", agent_id="a1")
        self.assertTrue(self.g.has_node("n1"))

    def test_get_node(self):
        self.g.add_node("n1", agent_id="a1")
        node = self.g.get_node("n1")
        self.assertEqual(node["id"], "n1")
        self.assertEqual(node["agent_id"], "a1")

    def test_get_missing_node(self):
        self.assertIsNone(self.g.get_node("missing"))

    def test_remove_node(self):
        self.g.add_node("n1")
        self.assertTrue(self.g.remove_node("n1"))
        self.assertFalse(self.g.has_node("n1"))

    def test_remove_missing_node(self):
        self.assertFalse(self.g.remove_node("missing"))


class TestEdgeOperations(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "graph.json")
        self.g = CognitiveGraph(self.path)

    def test_add_edge(self):
        self.g.add_edge("a", "b", link_type="causal")
        self.assertTrue(self.g.graph.has_edge("a", "b"))
        self.assertTrue(self.g.has_node("a"))
        self.assertTrue(self.g.has_node("b"))

    def test_invalid_edge_type(self):
        with self.assertRaises(ValueError):
            self.g.add_edge("a", "b", link_type="invalid")

    def test_get_edges(self):
        self.g.add_edge("a", "b", link_type="causal")
        self.g.add_edge("c", "a", link_type="semantic")
        edges = self.g.get_edges("a")
        self.assertEqual(len(edges), 2)

    def test_remove_edge(self):
        self.g.add_edge("a", "b", link_type="causal")
        self.assertTrue(self.g.remove_edge("a", "b"))
        self.assertFalse(self.g.graph.has_edge("a", "b"))

    def test_remove_missing_edge(self):
        self.assertFalse(self.g.remove_edge("x", "y"))


class TestQueryOperations(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "graph.json")
        self.g = CognitiveGraph(self.path)
        # Build chain: a -> b -> c -> d
        self.g.add_edge("a", "b", link_type="causal")
        self.g.add_edge("b", "c", link_type="derived_from")
        self.g.add_edge("c", "d", link_type="causal")
        self.g.add_edge("a", "x", link_type="semantic")  # not causal

    def test_neighbors_all(self):
        n = self.g.neighbors("a")
        self.assertIn("b", n)
        self.assertIn("x", n)

    def test_neighbors_filtered(self):
        n = self.g.neighbors("a", link_type="causal")
        self.assertIn("b", n)
        self.assertNotIn("x", n)

    def test_neighbors_direction_out(self):
        n = self.g.neighbors("b", direction="out")
        self.assertIn("c", n)
        self.assertNotIn("a", n)

    def test_neighbors_missing_node(self):
        self.assertEqual(self.g.neighbors("missing"), [])

    def test_find_dependents(self):
        deps = self.g.find_dependents("a")
        dep_ids = [d[0] for d in deps]
        self.assertIn("b", dep_ids)
        self.assertIn("c", dep_ids)
        self.assertIn("d", dep_ids)
        self.assertNotIn("x", dep_ids)  # semantic edge, not causal

    def test_find_dependents_depth(self):
        deps = self.g.find_dependents("a", max_depth=1)
        dep_ids = [d[0] for d in deps]
        self.assertIn("b", dep_ids)
        self.assertNotIn("d", dep_ids)

    def test_find_ancestors(self):
        anc = self.g.find_ancestors("d")
        anc_ids = [a[0] for a in anc]
        self.assertIn("c", anc_ids)
        self.assertIn("b", anc_ids)
        self.assertIn("a", anc_ids)

    def test_shortest_path(self):
        path = self.g.shortest_path("a", "d")
        self.assertEqual(path, ["a", "b", "c", "d"])

    def test_shortest_path_unreachable(self):
        self.g.add_node("isolated")
        self.assertIsNone(self.g.shortest_path("a", "isolated"))

    def test_stats(self):
        s = self.g.stats()
        self.assertEqual(s["nodes"], 5)  # a, b, c, d, x
        self.assertEqual(s["edges"], 4)
        self.assertIn("causal", s["edge_types"])


class TestPersistence(unittest.TestCase):
    def test_save_and_load(self):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "graph.json")
        g1 = CognitiveGraph(path)
        g1.add_edge("a", "b", link_type="causal")
        g1.add_node("a", agent_id="test")
        g1.save()

        g2 = CognitiveGraph(path)
        self.assertTrue(g2.has_node("a"))
        self.assertTrue(g2.graph.has_edge("a", "b"))
        self.assertEqual(g2.stats()["nodes"], 2)

    def test_load_missing_file(self):
        g = CognitiveGraph("/tmp/nonexistent_graph_test.json")
        self.assertEqual(g.graph.number_of_nodes(), 0)


class TestEdgeTypes(unittest.TestCase):
    def test_all_edge_types_valid(self):
        tmp = tempfile.mkdtemp()
        g = CognitiveGraph(os.path.join(tmp, "g.json"))
        for et in EDGE_TYPES:
            g.add_edge(f"src_{et}", f"tgt_{et}", link_type=et)
        self.assertEqual(g.graph.number_of_edges(), len(EDGE_TYPES))


if __name__ == "__main__":
    unittest.main()
