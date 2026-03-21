#!/usr/bin/env python3
"""
Tests for MAGMA Layer 2: ReplayStore, ReplayEngine.
Suite #38 — 23 tests.
"""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

_here = Path(__file__).resolve().parent
sys.path.insert(0, str(_here.parent))

os.environ.setdefault("CHROMA_IMPL", "ephemeral")


# ── Minimal adapter stub ──────────────────────────────────────────

class StubAdapter:
    """Minimal adapter for testing (in-memory dict)."""

    def __init__(self):
        self.docs = {}

    def add(self, doc_id, text, embedding, metadata, collection="waggle_memory"):
        self.docs[doc_id] = {"id": doc_id, "document": text, "metadata": metadata}

    def get(self, doc_id, collection="waggle_memory"):
        return self.docs.get(doc_id)

    def get_metadata(self, doc_id, collection="waggle_memory"):
        doc = self.docs.get(doc_id)
        return doc["metadata"] if doc else None

    def update_metadata(self, doc_id, metadata, collection="waggle_memory"):
        if doc_id in self.docs:
            self.docs[doc_id]["metadata"] = metadata

    def search(self, embedding, top_k=5, collection="waggle_memory"):
        return list(self.docs.values())[:top_k]


# ── ReplayStore tests ─────────────────────────────────────────────

class TestReplayStore(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False, mode="w")
        self.tmp.close()
        os.unlink(self.tmp.name)  # start fresh
        from core.replay_store import ReplayStore
        self.store = ReplayStore(path=self.tmp.name)

    def tearDown(self):
        if os.path.exists(self.tmp.name):
            os.unlink(self.tmp.name)

    def test_store_and_get_by_doc(self):
        self.store.store("d1", "hello world", "abc123", {"k": "v"})
        rec = self.store.get_by_doc("d1")
        self.assertIsNotNone(rec)
        self.assertEqual(rec["text"], "hello world")
        self.assertEqual(rec["content_hash"], "abc123")

    def test_get_by_hash(self):
        self.store.store("d1", "text1", "hash1")
        self.store.store("d2", "text2", "hash2")
        rec = self.store.get_by_hash("hash2")
        self.assertEqual(rec["doc_id"], "d2")

    def test_get_by_doc_returns_latest(self):
        self.store.store("d1", "v1", "h1")
        self.store.store("d1", "v2", "h2")
        rec = self.store.get_by_doc("d1")
        self.assertEqual(rec["text"], "v2")

    def test_iter_range(self):
        now = time.time()
        self.store.store("d1", "t1", "h1")
        entries = list(self.store.iter_range(now - 1, now + 10))
        self.assertEqual(len(entries), 1)

    def test_append_only(self):
        self.store.store("d1", "a", "h1")
        self.store.store("d2", "b", "h2")
        # Both entries present
        self.assertIsNotNone(self.store.get_by_doc("d1"))
        self.assertIsNotNone(self.store.get_by_doc("d2"))


# ── AuditLog new methods ──────────────────────────────────────────

class TestAuditLogLayer2(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        from core.audit_log import AuditLog
        self.audit = AuditLog(db_path=self.tmp.name)

    def tearDown(self):
        self.audit.close()
        os.unlink(self.tmp.name)

    def test_query_by_time_range(self):
        t1 = time.time()
        self.audit.record("new", "d1", agent_id="a1")
        self.audit.record("new", "d2", agent_id="a2")
        t2 = time.time()
        results = self.audit.query_by_time_range(t1 - 1, t2 + 1)
        self.assertEqual(len(results), 2)

    def test_query_by_time_range_agent_filter(self):
        self.audit.record("new", "d1", agent_id="a1")
        self.audit.record("new", "d2", agent_id="a2")
        results = self.audit.query_by_time_range(0, float("inf"), agent_id="a1")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["agent_id"], "a1")

    def test_query_by_time_range_layer_filter(self):
        self.audit.record("new", "d1", layer="working")
        self.audit.record("correction", "d2", layer="correction")
        results = self.audit.query_by_time_range(0, float("inf"), layer="correction")
        self.assertEqual(len(results), 1)

    def test_query_by_hash(self):
        self.audit.record("new", "d1", content_hash="aaa")
        self.audit.record("new", "d2", content_hash="bbb")
        results = self.audit.query_by_hash("aaa")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["doc_id"], "d1")


# ── ReplayEngine manifest ─────────────────────────────────────────

class TestReplayEngineManifest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        from core.audit_log import AuditLog
        from core.replay_engine import ReplayEngine
        self.audit = AuditLog(db_path=self.tmp.name)
        self.adapter = StubAdapter()
        self.engine = ReplayEngine(self.adapter, self.audit)

    def tearDown(self):
        self.audit.close()
        os.unlink(self.tmp.name)

    def test_manifest_empty(self):
        m = self.engine.get_manifest()
        self.assertEqual(m["total"], 0)

    def test_manifest_by_session(self):
        self.audit.record("new", "d1", session_id="s1", agent_id="a1")
        self.audit.record("new", "d2", session_id="s2", agent_id="a1")
        m = self.engine.get_manifest(session_id="s1", agent_id="a1")
        self.assertEqual(m["total"], 1)

    def test_manifest_by_agent(self):
        self.audit.record("new", "d1", agent_id="agent_x")
        self.audit.record("new", "d2", agent_id="agent_y")
        m = self.engine.get_manifest(agent_id="agent_x")
        self.assertEqual(m["total"], 1)

    def test_manifest_by_time(self):
        t1 = time.time()
        self.audit.record("new", "d1")
        t2 = time.time()
        m = self.engine.get_manifest(start_ts=t1 - 1, end_ts=t2 + 1)
        self.assertEqual(m["total"], 1)


# ── ReplayEngine dry_run ──────────────────────────────────────────

class TestReplayEngineDryRun(unittest.TestCase):

    def setUp(self):
        self.tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp_db.close()
        self.tmp_jsonl = tempfile.mktemp(suffix=".jsonl")
        from core.audit_log import AuditLog
        from core.replay_store import ReplayStore
        from core.replay_engine import ReplayEngine
        self.audit = AuditLog(db_path=self.tmp_db.name)
        self.replay_store = ReplayStore(path=self.tmp_jsonl)
        self.adapter = StubAdapter()
        self.engine = ReplayEngine(self.adapter, self.audit, replay_store=self.replay_store)

    def tearDown(self):
        self.audit.close()
        os.unlink(self.tmp_db.name)
        if os.path.exists(self.tmp_jsonl):
            os.unlink(self.tmp_jsonl)

    def _make_proxy(self, role="admin", agent_id="test", session_id="s1"):
        from core.memory_proxy import MemoryWriteProxy
        return MemoryWriteProxy(self.adapter, self.audit, role=role,
                                agent_id=agent_id, session_id=session_id,
                                replay_store=self.replay_store)

    def test_session_preview(self):
        proxy = self._make_proxy(session_id="sess1")
        proxy.write("d1", "hello", [0.0]*8)
        results = self.engine.replay_session("sess1", proxy=proxy, dry_run=True)
        self.assertTrue(any(r["status"] == "would_replay" for r in results))

    def test_time_range_preview(self):
        proxy = self._make_proxy()
        t1 = time.time()
        proxy.write("d1", "data", [0.0]*8)
        t2 = time.time()
        results = self.engine.replay_time_range(t1 - 1, t2 + 1, proxy=proxy, dry_run=True)
        self.assertTrue(len(results) >= 1)

    def test_corrections_preview(self):
        proxy = self._make_proxy()
        proxy.write("d1", "original", [0.0]*8)
        proxy.write("d1", "fixed", [0.0]*8, mode="correction", corrects="d1")
        results = self.engine.replay_corrections("d1", proxy=proxy, dry_run=True)
        self.assertTrue(len(results) >= 1)


# ── ReplayEngine execute ──────────────────────────────────────────

class TestReplayEngineExecute(unittest.TestCase):

    def setUp(self):
        self.tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp_db.close()
        self.tmp_jsonl = tempfile.mktemp(suffix=".jsonl")
        self.tmp_db2 = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp_db2.close()
        from core.audit_log import AuditLog
        from core.replay_store import ReplayStore
        from core.replay_engine import ReplayEngine
        self.audit = AuditLog(db_path=self.tmp_db.name)
        self.replay_store = ReplayStore(path=self.tmp_jsonl)
        self.adapter = StubAdapter()
        self.engine = ReplayEngine(self.adapter, self.audit, replay_store=self.replay_store)
        # Target for replay
        self.target_audit = AuditLog(db_path=self.tmp_db2.name)
        self.target_adapter = StubAdapter()

    def tearDown(self):
        self.audit.close()
        self.target_audit.close()
        os.unlink(self.tmp_db.name)
        os.unlink(self.tmp_db2.name)
        if os.path.exists(self.tmp_jsonl):
            os.unlink(self.tmp_jsonl)

    def _make_proxy(self, adapter=None, audit=None, **kw):
        from core.memory_proxy import MemoryWriteProxy
        return MemoryWriteProxy(
            adapter or self.adapter, audit or self.audit,
            role="admin", agent_id="test", session_id="s1",
            replay_store=self.replay_store, **kw
        )

    def test_session_replay_execute(self):
        proxy = self._make_proxy()
        proxy.write("d1", "hello world", [0.0]*8)
        # Replay into fresh target adapter — doc doesn't exist there
        from core.memory_proxy import MemoryWriteProxy
        target_proxy = MemoryWriteProxy(self.target_adapter, self.target_audit,
                                        role="admin", agent_id="replay", session_id="s_replay",
                                        replay_store=self.replay_store)
        results = self.engine.replay_session("s1", proxy=target_proxy, dry_run=False)
        replayed = [r for r in results if r["status"] == "replayed"]
        self.assertTrue(len(replayed) >= 1)

    def test_skips_dupes(self):
        proxy = self._make_proxy()
        proxy.write("d1", "same text", [0.0]*8)
        # Replay same session — content_hash exists, should dedup_skip
        results = self.engine.replay_session("s1", proxy=proxy, dry_run=False)
        skipped = [r for r in results if r["status"] == "dedup_skip"]
        self.assertTrue(len(skipped) >= 1)

    def test_time_range_filtered(self):
        proxy = self._make_proxy()
        t1 = time.time()
        proxy.write("d1", "data1", [0.0]*8)
        t2 = time.time()
        results = self.engine.replay_time_range(
            t1 - 1, t2 + 1, proxy=proxy, dry_run=False, layer="working"
        )
        self.assertTrue(len(results) >= 1)


# ── Corrections ───────────────────────────────────────────────────

class TestReplayCorrections(unittest.TestCase):

    def setUp(self):
        self.tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp_db.close()
        self.tmp_jsonl = tempfile.mktemp(suffix=".jsonl")
        from core.audit_log import AuditLog
        from core.replay_store import ReplayStore
        from core.replay_engine import ReplayEngine
        self.audit = AuditLog(db_path=self.tmp_db.name)
        self.replay_store = ReplayStore(path=self.tmp_jsonl)
        self.adapter = StubAdapter()
        self.engine = ReplayEngine(self.adapter, self.audit, replay_store=self.replay_store)

    def tearDown(self):
        self.audit.close()
        os.unlink(self.tmp_db.name)
        if os.path.exists(self.tmp_jsonl):
            os.unlink(self.tmp_jsonl)

    def _make_proxy(self):
        from core.memory_proxy import MemoryWriteProxy
        return MemoryWriteProxy(self.adapter, self.audit, role="admin",
                                agent_id="test", session_id="s1",
                                replay_store=self.replay_store)

    def test_rebuild_chain(self):
        proxy = self._make_proxy()
        proxy.write("d1", "original text", [0.0]*8)
        proxy.write("d1", "corrected text", [0.0]*8, mode="correction", corrects="d1")
        results = self.engine.replay_corrections("d1", proxy=proxy, dry_run=True)
        self.assertTrue(len(results) >= 2)

    def test_invalidated_skipped(self):
        proxy = self._make_proxy()
        proxy.write("d1", "text", [0.0]*8)
        proxy.write("d1", "text", [0.0]*8, mode="invalidate_range")
        results = self.engine.replay_corrections("d1", proxy=proxy, dry_run=True)
        skipped = [r for r in results if r["status"] == "skipped"]
        self.assertTrue(len(skipped) >= 1)


# ── Dedup ─────────────────────────────────────────────────────────

class TestReplayDedup(unittest.TestCase):

    def setUp(self):
        self.tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp_db.close()
        from core.audit_log import AuditLog
        from core.replay_engine import ReplayEngine
        self.audit = AuditLog(db_path=self.tmp_db.name)
        self.adapter = StubAdapter()
        self.engine = ReplayEngine(self.adapter, self.audit)

    def tearDown(self):
        self.audit.close()
        os.unlink(self.tmp_db.name)

    def test_finds_dupes(self):
        self.audit.record("new", "d1", content_hash="same")
        self.audit.record("new", "d2", content_hash="same")
        dupes = self.engine.deduplicate()
        self.assertEqual(len(dupes), 1)
        self.assertEqual(dupes[0]["count"], 2)

    def test_no_false_positives(self):
        self.audit.record("new", "d1", content_hash="aaa")
        self.audit.record("new", "d2", content_hash="bbb")
        dupes = self.engine.deduplicate()
        self.assertEqual(len(dupes), 0)


# ── Integration ───────────────────────────────────────────────────

class TestReplayIntegration(unittest.TestCase):

    def setUp(self):
        self.tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp_db.close()
        self.tmp_jsonl = tempfile.mktemp(suffix=".jsonl")
        from core.audit_log import AuditLog
        from core.replay_store import ReplayStore
        self.audit = AuditLog(db_path=self.tmp_db.name)
        self.replay_store = ReplayStore(path=self.tmp_jsonl)
        self.adapter = StubAdapter()

    def tearDown(self):
        self.audit.close()
        os.unlink(self.tmp_db.name)
        if os.path.exists(self.tmp_jsonl):
            os.unlink(self.tmp_jsonl)

    def test_proxy_writes_to_replay_store(self):
        from core.memory_proxy import MemoryWriteProxy
        proxy = MemoryWriteProxy(self.adapter, self.audit, role="admin",
                                 agent_id="a1", session_id="s1",
                                 replay_store=self.replay_store)
        proxy.write("d1", "important fact", [0.0]*8)
        rec = self.replay_store.get_by_doc("d1")
        self.assertIsNotNone(rec)
        self.assertEqual(rec["text"], "important fact")

    def test_round_trip_recovery(self):
        from core.memory_proxy import MemoryWriteProxy
        from core.replay_engine import ReplayEngine
        proxy = MemoryWriteProxy(self.adapter, self.audit, role="admin",
                                 agent_id="a1", session_id="s1",
                                 replay_store=self.replay_store)
        proxy.write("d1", "recover me", [0.0]*8)
        # Simulate data loss
        target = StubAdapter()
        engine = ReplayEngine(target, self.audit, replay_store=self.replay_store)
        target_proxy = MemoryWriteProxy(target, self.audit, role="admin",
                                        agent_id="replay", session_id="s_replay",
                                        replay_store=self.replay_store)
        results = engine.replay_session("s1", proxy=target_proxy, dry_run=False)
        replayed = [r for r in results if r["status"] == "replayed"]
        self.assertTrue(len(replayed) >= 1)


# ── Regression: Layer 1 still works ──────────────────────────────

class TestLayer1Regression(unittest.TestCase):

    def setUp(self):
        self.tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp_db.close()
        from core.audit_log import AuditLog
        self.audit = AuditLog(db_path=self.tmp_db.name)
        self.adapter = StubAdapter()

    def tearDown(self):
        self.audit.close()
        os.unlink(self.tmp_db.name)

    def test_proxy_without_replay_store(self):
        from core.memory_proxy import MemoryWriteProxy
        proxy = MemoryWriteProxy(self.adapter, self.audit, role="worker",
                                 agent_id="a1", session_id="s1")
        doc_id = proxy.write("d1", "text", [0.0]*8)
        self.assertEqual(doc_id, "d1")
        self.assertEqual(self.audit.count(), 1)

    def test_audit_log_backward_compat(self):
        self.audit.record("new", "d1", agent_id="a1", content_hash="h1")
        self.assertEqual(self.audit.count(), 1)
        results = self.audit.query_by_agent("a1")
        self.assertEqual(len(results), 1)


# ── Causal replay (CognitiveGraph) ──────────────────────────────

class TestCausalReplay(unittest.TestCase):

    def setUp(self):
        self.tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp_db.close()
        self.tmp_jsonl = tempfile.mktemp(suffix=".jsonl")
        self.tmp_graph = tempfile.mktemp(suffix=".json")
        from core.audit_log import AuditLog
        from core.replay_store import ReplayStore
        from core.replay_engine import ReplayEngine
        from core.cognitive_graph import CognitiveGraph
        self.audit = AuditLog(db_path=self.tmp_db.name)
        self.replay_store = ReplayStore(path=self.tmp_jsonl)
        self.adapter = StubAdapter()
        self.graph = CognitiveGraph(self.tmp_graph)
        self.engine = ReplayEngine(self.adapter, self.audit,
                                   replay_store=self.replay_store,
                                   cognitive_graph=self.graph)

    def tearDown(self):
        self.audit.close()
        os.unlink(self.tmp_db.name)
        for p in (self.tmp_jsonl, self.tmp_graph):
            if os.path.exists(p):
                os.unlink(p)

    def test_preview_causal_no_graph(self):
        from core.replay_engine import ReplayEngine
        engine = ReplayEngine(self.adapter, self.audit)
        result = engine.preview_causal("x")
        self.assertIn("error", result)

    def test_preview_causal_with_deps(self):
        self.graph.add_edge("d1", "d2", link_type="causal")
        self.graph.add_edge("d2", "d3", link_type="derived_from")
        result = self.engine.preview_causal("d1")
        self.assertEqual(result["would_replay"], 2)
        dep_ids = [d["id"] for d in result["dependents"]]
        self.assertIn("d2", dep_ids)
        self.assertIn("d3", dep_ids)

    def test_replay_causal_dry_run(self):
        # Set up graph: d1 -> d2
        self.graph.add_edge("d1", "d2", link_type="causal")
        # Record audit entry for d2
        self.audit.record("store", "d2", agent_id="a1", details="downstream fact")
        result = self.engine.replay_causal("d1", proxy=None, dry_run=True)
        self.assertEqual(result["replayed"], 1)
        self.assertEqual(result["results"][0]["status"], "would_replay")

    def test_replay_causal_no_audit(self):
        self.graph.add_edge("d1", "d2", link_type="causal")
        result = self.engine.replay_causal("d1", proxy=None, dry_run=True)
        self.assertEqual(result["results"][0]["status"], "no_audit_entry")

    def test_replay_causal_no_graph(self):
        from core.replay_engine import ReplayEngine
        engine = ReplayEngine(self.adapter, self.audit)
        result = engine.replay_causal("x", proxy=None, dry_run=True)
        self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
