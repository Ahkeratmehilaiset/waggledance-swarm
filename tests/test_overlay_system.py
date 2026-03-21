"""
MAGMA: Overlay System Expansion — Tests
Suite #43: ~20 tests
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.memory_overlay import (
    OverlayBranch, BranchManager, MoodPreset,
)


class TestOverlayBranch(unittest.TestCase):

    def test_create_branch(self):
        b = OverlayBranch("test", "A test branch")
        self.assertEqual(b.name, "test")
        self.assertFalse(b.active)
        self.assertEqual(b.replacement_count, 0)

    def test_add_replacement(self):
        b = OverlayBranch("test")
        b.add_replacement("doc1", "new content")
        self.assertEqual(b.replacement_count, 1)
        self.assertIn("doc1", b.replaced_ids)

    def test_remove_replacement(self):
        b = OverlayBranch("test")
        b.add_replacement("doc1", "new")
        self.assertTrue(b.remove_replacement("doc1"))
        self.assertEqual(b.replacement_count, 0)

    def test_remove_missing(self):
        b = OverlayBranch("test")
        self.assertFalse(b.remove_replacement("missing"))

    def test_apply_to_results(self):
        b = OverlayBranch("test")
        b.add_replacement("doc1", "replaced content")
        results = [
            {"id": "doc1", "content": "original"},
            {"id": "doc2", "content": "untouched"},
        ]
        applied = b.apply_to_results(results)
        self.assertEqual(applied[0]["content"], "replaced content")
        self.assertEqual(applied[1]["content"], "untouched")

    def test_apply_empty_branch(self):
        b = OverlayBranch("test")
        results = [{"id": "doc1", "content": "x"}]
        self.assertEqual(b.apply_to_results(results), results)

    def test_to_dict(self):
        b = OverlayBranch("test", "desc")
        b.add_replacement("d1", "x")
        d = b.to_dict()
        self.assertEqual(d["name"], "test")
        self.assertEqual(d["replacement_count"], 1)
        self.assertIn("d1", d["replaced_ids"])


class TestBranchManager(unittest.TestCase):

    def setUp(self):
        self.mgr = BranchManager()

    def test_create_and_get(self):
        b = self.mgr.create("br1", "test branch")
        self.assertIsNotNone(self.mgr.get("br1"))
        self.assertEqual(b.name, "br1")

    def test_get_missing(self):
        self.assertIsNone(self.mgr.get("missing"))

    def test_delete(self):
        self.mgr.create("br1")
        self.assertTrue(self.mgr.delete("br1"))
        self.assertIsNone(self.mgr.get("br1"))

    def test_delete_active_resets(self):
        self.mgr.create("br1")
        self.mgr.activate("br1")
        self.mgr.delete("br1")
        self.assertIsNone(self.mgr.active_branch)

    def test_activate(self):
        self.mgr.create("br1")
        self.assertTrue(self.mgr.activate("br1"))
        self.assertEqual(self.mgr.active_branch.name, "br1")
        self.assertTrue(self.mgr.get("br1").active)

    def test_activate_switches(self):
        self.mgr.create("br1")
        self.mgr.create("br2")
        self.mgr.activate("br1")
        self.mgr.activate("br2")
        self.assertFalse(self.mgr.get("br1").active)
        self.assertTrue(self.mgr.get("br2").active)

    def test_activate_missing(self):
        self.assertFalse(self.mgr.activate("missing"))

    def test_deactivate(self):
        self.mgr.create("br1")
        self.mgr.activate("br1")
        prev = self.mgr.deactivate()
        self.assertEqual(prev, "br1")
        self.assertIsNone(self.mgr.active_branch)

    def test_apply_active(self):
        br = self.mgr.create("br1")
        br.add_replacement("d1", "new")
        self.mgr.activate("br1")
        results = [{"id": "d1", "content": "old"}]
        applied = self.mgr.apply_active(results)
        self.assertEqual(applied[0]["content"], "new")

    def test_apply_no_active(self):
        results = [{"id": "d1", "content": "old"}]
        self.assertEqual(self.mgr.apply_active(results), results)

    def test_compare(self):
        ba = self.mgr.create("a")
        bb = self.mgr.create("b")
        ba.add_replacement("d1", "version_a")
        bb.add_replacement("d1", "version_b")
        results = [{"id": "d1", "content": "base"}]
        cmp = self.mgr.compare(results, "a", "b")
        self.assertEqual(cmp["base"][0]["content"], "base")
        self.assertEqual(cmp["branch_a"]["results"][0]["content"], "version_a")
        self.assertEqual(cmp["branch_b"]["results"][0]["content"], "version_b")

    def test_list_all(self):
        self.mgr.create("a")
        self.mgr.create("b")
        listing = self.mgr.list_all()
        self.assertIn("a", listing)
        self.assertIn("b", listing)

    def test_create_from_agent_data(self):
        entries = [
            {"doc_id": "d1", "content": "fact 1"},
            {"doc_id": "d2", "content": "fact 2"},
        ]
        br = self.mgr.create_from_agent_data(
            "fix", "agent1", entries,
            transform=lambda c: c.upper())
        self.assertEqual(br.replacement_count, 2)
        results = [{"id": "d1", "content": "fact 1"}]
        applied = br.apply_to_results(results)
        self.assertEqual(applied[0]["content"], "FACT 1")

    def test_create_from_agent_data_no_transform(self):
        entries = [{"doc_id": "d1", "content": "x"}]
        br = self.mgr.create_from_agent_data("br", "a1", entries)
        self.assertIn("PENDING REPLACEMENT", br._replacements["d1"]["content"])


class TestMoodPreset(unittest.TestCase):

    def test_cautious(self):
        result = MoodPreset.cautious("Bees need sugar syrup")
        self.assertIn("UNCERTAIN", result)
        self.assertIn("verification", result)

    def test_verified_only_with_source(self):
        result = MoodPreset.verified_only("Varroa treatment. Source: Smith 2024")
        self.assertNotIn("HIDDEN", result)

    def test_verified_only_without_source(self):
        result = MoodPreset.verified_only("Bees like flowers")
        self.assertIn("HIDDEN", result)

    def test_concise(self):
        result = MoodPreset.concise("First sentence. Second sentence. Third.")
        self.assertEqual(result, "First sentence.")


if __name__ == "__main__":
    unittest.main()
