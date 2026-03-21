"""Tests for core.causal_replay_api — CausalReplayService and ReplayResult."""

import unittest
from unittest.mock import MagicMock

from core.causal_replay_api import CausalReplayService, ReplayResult


class TestReplayResult(unittest.TestCase):
    def test_default_values(self):
        r = ReplayResult(node_id="n1")
        self.assertEqual(r.node_id, "n1")
        self.assertEqual(r.ancestors, [])
        self.assertEqual(r.dependents, [])
        self.assertEqual(r.shortest_path, [])

    def test_to_dict(self):
        r = ReplayResult(
            node_id="n1",
            ancestors=["a1", "a2"],
            dependents=["d1"],
            shortest_path=["a1", "n1"],
        )
        d = r.to_dict()
        self.assertEqual(d["node_id"], "n1")
        self.assertEqual(d["ancestors"], ["a1", "a2"])
        self.assertEqual(d["dependents"], ["d1"])
        self.assertEqual(d["shortest_path"], ["a1", "n1"])


class TestCausalReplayServiceNoGraph(unittest.TestCase):
    def test_replay_without_graph_returns_empty(self):
        svc = CausalReplayService(graph=None)
        result = svc.replay("node-x")
        self.assertEqual(result.node_id, "node-x")
        self.assertEqual(result.ancestors, [])
        self.assertEqual(result.dependents, [])
        self.assertEqual(result.shortest_path, [])


class TestCausalReplayServiceWithMockGraph(unittest.TestCase):
    def setUp(self):
        self.graph = MagicMock()
        self.svc = CausalReplayService(graph=self.graph)

    def test_ancestors_called(self):
        self.graph.find_ancestors.return_value = [("root", 1), ("mid", 2)]
        self.graph.find_dependents.return_value = []
        result = self.svc.replay("leaf")
        self.graph.find_ancestors.assert_called_once_with("leaf")
        self.assertEqual(result.ancestors, [("root", 1), ("mid", 2)])

    def test_dependents_called(self):
        self.graph.find_ancestors.return_value = []
        self.graph.find_dependents.return_value = [("child1", 1)]
        result = self.svc.replay("parent")
        self.graph.find_dependents.assert_called_once_with("parent")
        self.assertEqual(result.dependents, [("child1", 1)])

    def test_shortest_path_called_when_ancestors_exist(self):
        self.graph.find_ancestors.return_value = [("root", 1)]
        self.graph.find_dependents.return_value = []
        self.graph.shortest_path.return_value = ["root", "mid", "leaf"]
        result = self.svc.replay("leaf")
        self.graph.shortest_path.assert_called_once_with(("root", 1), "leaf")
        self.assertEqual(result.shortest_path, ["root", "mid", "leaf"])

    def test_shortest_path_skipped_when_no_ancestors(self):
        self.graph.find_ancestors.return_value = []
        self.graph.find_dependents.return_value = []
        result = self.svc.replay("isolated")
        self.graph.shortest_path.assert_not_called()
        self.assertEqual(result.shortest_path, [])

    def test_ancestors_exception_handled(self):
        self.graph.find_ancestors.side_effect = RuntimeError("graph error")
        self.graph.find_dependents.return_value = []
        result = self.svc.replay("bad")
        self.assertEqual(result.ancestors, [])

    def test_dependents_exception_handled(self):
        self.graph.find_ancestors.return_value = []
        self.graph.find_dependents.side_effect = RuntimeError("graph error")
        result = self.svc.replay("bad")
        self.assertEqual(result.dependents, [])

    def test_shortest_path_exception_handled(self):
        self.graph.find_ancestors.return_value = [("root", 1)]
        self.graph.find_dependents.return_value = []
        self.graph.shortest_path.side_effect = RuntimeError("no path")
        result = self.svc.replay("node")
        self.assertEqual(result.shortest_path, [])


class TestCausalReplayServiceNoMethods(unittest.TestCase):
    """Graph object that doesn't have the expected methods."""

    def test_graph_without_find_ancestors(self):
        graph = MagicMock(spec=[])  # No attributes
        svc = CausalReplayService(graph=graph)
        result = svc.replay("node")
        self.assertEqual(result.ancestors, [])
        self.assertEqual(result.dependents, [])


if __name__ == "__main__":
    unittest.main()
