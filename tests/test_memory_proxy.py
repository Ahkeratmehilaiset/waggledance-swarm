#!/usr/bin/env python3
"""
Tests for MAGMA Layer 1: AuditLog, ChromaDBAdapter, MemoryWriteProxy, AgentRollback.
Suite #37 — 27 tests.
"""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

# Project root
_here = Path(__file__).resolve().parent
sys.path.insert(0, str(_here.parent))

# Force in-memory chromadb
os.environ.setdefault("CHROMA_IMPL", "ephemeral")


class TestAuditLog(unittest.TestCase):
    """AuditLog: append-only SQLite audit trail."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        from core.audit_log import AuditLog
        self.audit = AuditLog(db_path=self.tmp.name)

    def tearDown(self):
        self.audit.close()
        os.unlink(self.tmp.name)

    def test_create_empty(self):
        self.assertEqual(self.audit.count(), 0)

    def test_record_and_count(self):
        self.audit.record("new", "doc1", agent_id="a1", session_id="s1")
        self.audit.record("new", "doc2", agent_id="a1", session_id="s1")
        self.assertEqual(self.audit.count(), 2)

    def test_query_by_agent(self):
        self.audit.record("new", "doc1", agent_id="a1", session_id="s1")
        self.audit.record("new", "doc2", agent_id="a2", session_id="s1")
        rows = self.audit.query_by_agent("a1")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["doc_id"], "doc1")

    def test_query_by_agent_with_session(self):
        self.audit.record("new", "doc1", agent_id="a1", session_id="s1")
        self.audit.record("new", "doc2", agent_id="a1", session_id="s2")
        rows = self.audit.query_by_agent("a1", session_id="s1")
        self.assertEqual(len(rows), 1)

    def test_query_by_doc(self):
        self.audit.record("new", "doc1", agent_id="a1")
        self.audit.record("correction", "doc1", agent_id="a2")
        rows = self.audit.query_by_doc("doc1")
        self.assertEqual(len(rows), 2)

    def test_query_spawn_tree(self):
        self.audit.record("new", "d1", agent_id="parent", spawn_chain="")
        self.audit.record("new", "d2", agent_id="child1", spawn_chain="parent")
        self.audit.record("new", "d3", agent_id="child2", spawn_chain="parent>child1")
        rows = self.audit.query_spawn_tree("parent")
        self.assertEqual(len(rows), 3)

    def test_content_hash(self):
        from core.audit_log import AuditLog
        h = AuditLog.content_hash("hello world")
        self.assertEqual(len(h), 16)
        self.assertEqual(h, AuditLog.content_hash("hello world"))
        self.assertNotEqual(h, AuditLog.content_hash("hello world!"))


class TestChromaDBAdapter(unittest.TestCase):
    """ChromaDBAdapter: thin wrapper over in-memory ChromaDB."""

    COL = "test_adapter"

    def setUp(self):
        import chromadb
        self.client = chromadb.EphemeralClient()
        # Clean slate
        for c in self.client.list_collections():
            self.client.delete_collection(c.name if hasattr(c, 'name') else c)
        from core.chromadb_adapter import ChromaDBAdapter
        self.adapter = ChromaDBAdapter(client=self.client)

    def test_add_and_get(self):
        emb = [0.1] * 384
        self.adapter.add("d1", "hello", emb, {"tag": "test"})
        doc = self.adapter.get("d1")
        self.assertIsNotNone(doc)
        self.assertEqual(doc["document"], "hello")
        self.assertEqual(doc["metadata"]["tag"], "test")

    def test_get_missing(self):
        self.assertIsNone(self.adapter.get("nonexistent"))

    def test_delete(self):
        emb = [0.1] * 384
        self.adapter.add("d1", "hello", emb, {})
        self.adapter.delete("d1")
        self.assertIsNone(self.adapter.get("d1"))

    def test_update_metadata(self):
        emb = [0.1] * 384
        self.adapter.add("d1", "hello", emb, {"a": "1"})
        self.adapter.update_metadata("d1", {"a": "1", "b": "2"})
        meta = self.adapter.get_metadata("d1")
        self.assertEqual(meta["b"], "2")

    def test_bulk_invalidate(self):
        emb = [0.1] * 384
        self.adapter.add("d1", "hello", emb, {})
        self.adapter.add("d2", "world", emb, {})
        count = self.adapter.bulk_invalidate(["d1", "d2"])
        self.assertEqual(count, 2)
        meta = self.adapter.get_metadata("d1")
        self.assertTrue(meta["_invalidated"])

    def test_search(self):
        emb = [0.1] * 384
        self.adapter.add("d1", "hello bees", emb, {})
        results = self.adapter.search(emb, top_k=5)
        self.assertGreaterEqual(len(results), 1)
        self.assertEqual(results[0]["document"], "hello bees")


class TestMemoryWriteProxy(unittest.TestCase):
    """MemoryWriteProxy: role-based write guard."""

    def setUp(self):
        import chromadb
        self.client = chromadb.EphemeralClient()
        for c in self.client.list_collections():
            self.client.delete_collection(c.name if hasattr(c, 'name') else c)
        from core.chromadb_adapter import ChromaDBAdapter
        from core.audit_log import AuditLog
        from core.memory_proxy import MemoryWriteProxy
        self.adapter = ChromaDBAdapter(client=self.client)
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.audit = AuditLog(db_path=self.tmp.name)
        self.emb = [0.1] * 384
        self.MemoryWriteProxy = MemoryWriteProxy

    def tearDown(self):
        self.audit.close()
        os.unlink(self.tmp.name)

    def _proxy(self, role="worker", agent_id="a1", session_id="s1"):
        return self.MemoryWriteProxy(
            self.adapter, self.audit,
            role=role, agent_id=agent_id, session_id=session_id
        )

    def test_worker_can_write_new(self):
        p = self._proxy("worker")
        doc_id = p.write("d1", "hello", self.emb, mode="new")
        self.assertEqual(doc_id, "d1")
        doc = self.adapter.get("d1")
        self.assertIsNotNone(doc)

    def test_worker_cannot_correct(self):
        p = self._proxy("worker")
        with self.assertRaises(PermissionError):
            p.write("d1", "fixed", self.emb, mode="correction", corrects="d0")

    def test_enricher_can_correct(self):
        p = self._proxy("enricher")
        p.write("d0", "original", self.emb, mode="new")
        corr_id = p.write("d0", "corrected", self.emb,
                          mode="correction", corrects="d0")
        self.assertIn("__corr_", corr_id)

    def test_readonly_cannot_write(self):
        p = self._proxy("readonly")
        with self.assertRaises(PermissionError):
            p.write("d1", "hello", self.emb, mode="new")

    def test_admin_full_access(self):
        p = self._proxy("admin")
        p.write("d1", "hello", self.emb, mode="new")
        p.write("d1", "fixed", self.emb, mode="correction", corrects="d1")
        p.write("d1", "", self.emb, mode="invalidate_range")

    def test_layer_metadata_stamped(self):
        p = self._proxy("worker", agent_id="bot1")
        p.write("d1", "hello", self.emb, mode="new")
        meta = self.adapter.get_metadata("d1")
        self.assertEqual(meta["_layer"], "working")
        self.assertEqual(meta["_agent_id"], "bot1")
        self.assertIn("_content_hash", meta)
        self.assertIn("_created_at", meta)

    def test_correction_links_to_original(self):
        p = self._proxy("enricher")
        p.write("d0", "original", self.emb, mode="new")
        corr_id = p.write("d0", "better", self.emb,
                          mode="correction", corrects="d0")
        meta = self.adapter.get_metadata(corr_id)
        self.assertEqual(meta["_corrects"], "d0")
        self.assertEqual(meta["_layer"], "correction")

    def test_content_hash_consistent(self):
        p = self._proxy("worker")
        p.write("d1", "hello world", self.emb, mode="new")
        meta = self.adapter.get_metadata("d1")
        from core.audit_log import AuditLog
        expected = AuditLog.content_hash("hello world")
        self.assertEqual(meta["_content_hash"], expected)

    def test_invalidate_sets_flag(self):
        p = self._proxy("admin")
        p.write("d1", "hello", self.emb, mode="new")
        p.write("d1", "", self.emb, mode="invalidate_range")
        meta = self.adapter.get_metadata("d1")
        self.assertTrue(meta["_invalidated"])

    def test_audit_recorded(self):
        p = self._proxy("worker", agent_id="bot1", session_id="ses1")
        p.write("d1", "hello", self.emb, mode="new")
        entries = self.audit.query_by_agent("bot1", "ses1")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["action"], "new")

    def test_no_overwrite_mode(self):
        """There is no 'overwrite' mode — only new/correction/invalidate_range."""
        p = self._proxy("admin")
        with self.assertRaises(ValueError):
            p.write("d1", "hello", self.emb, mode="overwrite")

    def test_read_returns_doc(self):
        p = self._proxy("worker")
        p.write("d1", "hello", self.emb, mode="new")
        doc = p.read("d1")
        self.assertIsNotNone(doc)
        self.assertEqual(doc["document"], "hello")

    def test_read_invalidated_returns_none(self):
        p = self._proxy("admin")
        p.write("d1", "hello", self.emb, mode="new")
        p.write("d1", "", self.emb, mode="invalidate_range")
        doc = p.read("d1")
        self.assertIsNone(doc)

    def test_search_filters_invalidated(self):
        p = self._proxy("admin")
        p.write("d1", "hello bees", self.emb, mode="new")
        p.write("d2", "hello wasps", self.emb, mode="new")
        p.write("d1", "", self.emb, mode="invalidate_range")
        results = p.search(self.emb, top_k=10)
        doc_ids = [r.get("metadata", {}).get("_agent_id") for r in results]
        # d1 should be filtered out (invalidated)
        for r in results:
            meta = r.get("metadata", {})
            self.assertFalse(meta.get("_invalidated", False))


class TestAgentRollback(unittest.TestCase):
    """AgentRollback: undo agent writes by session."""

    def setUp(self):
        import chromadb
        self.client = chromadb.EphemeralClient()
        for c in self.client.list_collections():
            self.client.delete_collection(c.name if hasattr(c, 'name') else c)
        from core.chromadb_adapter import ChromaDBAdapter
        from core.audit_log import AuditLog
        from core.memory_proxy import MemoryWriteProxy
        from core.agent_rollback import AgentRollback
        self.adapter = ChromaDBAdapter(client=self.client)
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.audit = AuditLog(db_path=self.tmp.name)
        self.rollback = AgentRollback(self.adapter, self.audit)
        self.emb = [0.1] * 384
        self.MemoryWriteProxy = MemoryWriteProxy

    def tearDown(self):
        self.audit.close()
        os.unlink(self.tmp.name)

    def _proxy(self, role="enricher", agent_id="a1", session_id="s1"):
        return self.MemoryWriteProxy(
            self.adapter, self.audit,
            role=role, agent_id=agent_id, session_id=session_id
        )

    def test_preview(self):
        p = self._proxy()
        p.write("d1", "hello", self.emb, mode="new")
        preview = self.rollback.preview("a1", "s1")
        self.assertEqual(len(preview["working"]), 1)

    def test_rollback_working_deletes(self):
        p = self._proxy()
        p.write("d1", "hello", self.emb, mode="new")
        counts = self.rollback.rollback("a1", "s1")
        self.assertEqual(counts["deleted"], 1)
        self.assertIsNone(self.adapter.get("d1"))

    def test_rollback_correction_invalidates(self):
        p = self._proxy()
        p.write("d0", "original", self.emb, mode="new")
        corr_id = p.write("d0", "fixed", self.emb,
                          mode="correction", corrects="d0")
        # Rollback only corrections by a different agent
        p2 = self._proxy(agent_id="a2", session_id="s2")
        corr_id2 = p2.write("d0", "also fixed", self.emb,
                            mode="correction", corrects="d0")
        counts = self.rollback.rollback("a2", "s2")
        self.assertGreaterEqual(counts["invalidated"], 1)
        meta = self.adapter.get_metadata(corr_id2)
        self.assertTrue(meta["_invalidated"])

    def test_originals_never_touched(self):
        # Record an entry with layer=original in audit
        self.audit.record("new", "orig1", layer="original", agent_id="a1", session_id="s1")
        self.adapter.add("orig1", "sacred data", self.emb, {"_layer": "original"})
        counts = self.rollback.rollback("a1", "s1")
        self.assertEqual(counts["skipped_original"], 1)
        doc = self.adapter.get("orig1")
        self.assertIsNotNone(doc)

    def test_recursive_rollback(self):
        p1 = self._proxy(agent_id="parent", session_id="s1")
        p1.write("d1", "parent data", self.emb, mode="new")
        # Child agent with spawn_chain
        p2 = self.MemoryWriteProxy(
            self.adapter, self.audit,
            role="worker", agent_id="child1",
            session_id="s2", spawn_chain="parent"
        )
        p2.write("d2", "child data", self.emb, mode="new")
        counts = self.rollback.rollback_recursive("parent")
        self.assertGreaterEqual(counts["deleted"], 2)
        self.assertIsNone(self.adapter.get("d1"))
        self.assertIsNone(self.adapter.get("d2"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
