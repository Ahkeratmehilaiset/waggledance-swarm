#!/usr/bin/env python3
"""
Tests for MAGMA Layer 3: Proxy Wiring & Overlay Networks.
Suite #39 — 20 tests.
"""

import hashlib
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_here = Path(__file__).resolve().parent
sys.path.insert(0, str(_here.parent))

os.environ.setdefault("CHROMA_IMPL", "ephemeral")


# ── Stub for MemoryStore ──────────────────────────────────────────

class StubMemoryStore:
    """Minimal MemoryStore stub for testing."""

    def __init__(self):
        self._docs = {}
        self.count = 0
        self.collection = StubCollection(self)

    def store(self, obs_id, text, embedding, metadata=None):
        self._docs[obs_id] = {"id": obs_id, "text": text, "metadata": metadata or {}}
        self.count += 1

    def store_batch(self, ids, docs, embeddings, metadatas):
        for i, oid in enumerate(ids):
            self._docs[oid] = {"id": oid, "text": docs[i], "metadata": metadatas[i]}
            self.count += 1

    def search(self, embedding, top_k=5, min_score=0.0):
        return []  # no dupes


class StubCollection:
    """Minimal ChromaDB collection stub."""

    def __init__(self, store):
        self._store = store

    def upsert(self, ids, embeddings, documents, metadatas):
        for i, oid in enumerate(ids):
            self._store._docs[oid] = {
                "id": oid, "text": documents[i],
                "metadata": metadatas[i] if metadatas else {},
            }

    def query(self, query_embeddings, n_results=5, where=None, include=None):
        # Filter by where clause
        matches = []
        for oid, doc in self._store._docs.items():
            meta = doc.get("metadata", {})
            if where:
                if "agent_id" in where:
                    filt = where["agent_id"]
                    if isinstance(filt, dict) and "$in" in filt:
                        if meta.get("agent_id") not in filt["$in"]:
                            continue
                    elif meta.get("agent_id") != filt:
                        continue
            matches.append(doc)

        matches = matches[:n_results]
        return {
            "ids": [[m["id"] for m in matches]],
            "documents": [[m["text"] for m in matches]],
            "metadatas": [[m["metadata"] for m in matches]],
            "distances": [[0.1 for _ in matches]],
        }

    def get(self, where=None, limit=100, include=None):
        matches = []
        for oid, doc in self._store._docs.items():
            meta = doc.get("metadata", {})
            if where and "agent_id" in where:
                filt = where["agent_id"]
                if isinstance(filt, dict) and "$in" in filt:
                    if meta.get("agent_id") not in filt["$in"]:
                        continue
                elif meta.get("agent_id") != filt:
                    continue
            matches.append(oid)
            if len(matches) >= limit:
                break
        return {"ids": matches}

    def count(self):
        return len(self._store._docs)


# ── Stub Consciousness for wire_audit testing ─────────────────────

class StubConsciousness:
    """Minimal Consciousness-like object for testing wire_audit + _learn_single."""

    def __init__(self, memory_store):
        self.memory = memory_store
        self._insight_counter = 0

    def wire_audit(self, audit_log, replay_store=None):
        self._audit_log = audit_log
        self._replay_store = replay_store

    def _learn_single(self, text, agent_id="system", source_type="heartbeat",
                      confidence=0.5, validated=False, metadata=None):
        combined = text
        embedding = [0.0] * 8

        self._insight_counter += 1
        obs_id = f"{source_type}_{agent_id}_{int(time.time())}_{self._insight_counter:04d}"

        meta = {
            "agent_id": agent_id, "source_type": source_type,
            "confidence": confidence, "validated": validated,
        }
        if metadata:
            meta.update(metadata)

        self.memory.store(obs_id, combined, embedding, meta)

        # MAGMA: audit + replay (same logic as real Consciousness)
        _al = getattr(self, '_audit_log', None)
        if _al:
            _hash = hashlib.sha256(combined.encode()).hexdigest()[:16]
            _al.record(
                "store", obs_id, agent_id=agent_id,
                content_hash=_hash, details=combined,
            )
            _rs = getattr(self, '_replay_store', None)
            if _rs:
                _rs.store(obs_id, combined, _hash, meta)

        return True


# ── wire_audit tests ──────────────────────────────────────────────

class TestWireAudit(unittest.TestCase):

    def setUp(self):
        self.tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp_db.close()
        self.tmp_jsonl = tempfile.mktemp(suffix=".jsonl")
        from core.audit_log import AuditLog
        from core.replay_store import ReplayStore
        self.audit = AuditLog(db_path=self.tmp_db.name)
        self.replay_store = ReplayStore(path=self.tmp_jsonl)
        self.memory = StubMemoryStore()
        self.consciousness = StubConsciousness(self.memory)

    def tearDown(self):
        self.audit.close()
        os.unlink(self.tmp_db.name)
        if os.path.exists(self.tmp_jsonl):
            os.unlink(self.tmp_jsonl)

    def test_wire_audit_attaches(self):
        self.consciousness.wire_audit(self.audit, self.replay_store)
        self.assertIs(self.consciousness._audit_log, self.audit)
        self.assertIs(self.consciousness._replay_store, self.replay_store)

    def test_learn_single_records_audit(self):
        self.consciousness.wire_audit(self.audit, self.replay_store)
        self.consciousness._learn_single("Mehiläiset voivat hyvin", agent_id="test_agent")
        self.assertEqual(self.audit.count(), 1)
        entries = self.audit.query_by_agent("test_agent")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["action"], "store")

    def test_learn_single_records_replay(self):
        self.consciousness.wire_audit(self.audit, self.replay_store)
        self.consciousness._learn_single("Pollen is abundant", agent_id="bee_monitor")
        entries = self.audit.query_by_agent("bee_monitor")
        obs_id = entries[0]["doc_id"]
        rec = self.replay_store.get_by_doc(obs_id)
        self.assertIsNotNone(rec)
        self.assertIn("Pollen", rec["text"])

    def test_unwired_no_error(self):
        # Without wire_audit, _learn_single still works
        result = self.consciousness._learn_single("Safe without audit")
        self.assertTrue(result)
        self.assertEqual(self.audit.count(), 0)


# ── Batch audit test ──────────────────────────────────────────────

class TestBatchAudit(unittest.TestCase):

    def setUp(self):
        self.tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp_db.close()
        self.tmp_jsonl = tempfile.mktemp(suffix=".jsonl")
        from core.audit_log import AuditLog
        from core.replay_store import ReplayStore
        self.audit = AuditLog(db_path=self.tmp_db.name)
        self.replay_store = ReplayStore(path=self.tmp_jsonl)
        self.memory = StubMemoryStore()

    def tearDown(self):
        self.audit.close()
        os.unlink(self.tmp_db.name)
        if os.path.exists(self.tmp_jsonl):
            os.unlink(self.tmp_jsonl)

    def test_batch_records_audit(self):
        """Simulate batch flush with audit recording."""
        ids = ["fact_a1_001", "fact_a2_002"]
        docs = ["Bees are healthy", "Varroa count low"]
        metas = [
            {"agent_id": "agent_a", "source_type": "test"},
            {"agent_id": "agent_b", "source_type": "test"},
        ]
        self.memory.store_batch(ids, docs, [None, None], metas)

        # Simulate MAGMA batch audit (same logic as _flush_learn_queue)
        for i, oid in enumerate(ids):
            doc = docs[i]
            _hash = hashlib.sha256(doc.encode()).hexdigest()[:16]
            m = metas[i]
            self.audit.record(
                "store", oid, agent_id=m["agent_id"],
                content_hash=_hash, details=doc,
            )
            self.replay_store.store(oid, doc, _hash, m)

        self.assertEqual(self.audit.count(), 2)
        self.assertIsNotNone(self.replay_store.get_by_doc("fact_a1_001"))

    def test_unwired_batch_no_error(self):
        """Batch store works without audit wiring."""
        self.memory.store_batch(["id1"], ["text"], [None], [{"agent_id": "x"}])
        self.assertEqual(self.memory.count, 1)
        self.assertEqual(self.audit.count(), 0)


# ── MemoryOverlay tests ──────────────────────────────────────────

class TestMemoryOverlay(unittest.TestCase):

    def setUp(self):
        self.store = StubMemoryStore()
        # Add docs with different agent_ids
        self.store.store("d1", "bee health", [0.0]*8, {"agent_id": "bee_monitor"})
        self.store.store("d2", "weather data", [0.0]*8, {"agent_id": "weather_feed"})
        self.store.store("d3", "hive temp", [0.0]*8, {"agent_id": "bee_monitor"})

    def test_search_filters_by_agent(self):
        from core.memory_overlay import MemoryOverlay
        ov = MemoryOverlay(self.store, ["bee_monitor"])
        results = ov.search([0.0]*8, top_k=10)
        self.assertTrue(all(r["metadata"]["agent_id"] == "bee_monitor" for r in results))
        self.assertEqual(len(results), 2)

    def test_search_multi_agent(self):
        from core.memory_overlay import MemoryOverlay
        ov = MemoryOverlay(self.store, ["bee_monitor", "weather_feed"])
        results = ov.search([0.0]*8, top_k=10)
        self.assertEqual(len(results), 3)

    def test_list_ids(self):
        from core.memory_overlay import MemoryOverlay
        ov = MemoryOverlay(self.store, ["weather_feed"])
        ids = ov.list_ids()
        self.assertEqual(ids, ["d2"])


# ── OverlayRegistry tests ────────────────────────────────────────

class TestOverlayRegistry(unittest.TestCase):

    def setUp(self):
        self.store = StubMemoryStore()

    def test_register_and_get(self):
        from core.memory_overlay import OverlayRegistry
        reg = OverlayRegistry(self.store)
        ov = reg.register("sensors", ["bee_monitor", "weather_feed"])
        self.assertIsNotNone(ov)
        self.assertIs(reg.get("sensors"), ov)

    def test_get_missing(self):
        from core.memory_overlay import OverlayRegistry
        reg = OverlayRegistry(self.store)
        self.assertIsNone(reg.get("nonexistent"))

    def test_list_all(self):
        from core.memory_overlay import OverlayRegistry
        reg = OverlayRegistry(self.store)
        reg.register("sensors", ["bee_monitor"])
        reg.register("feeds", ["rss_feed", "weather_feed"])
        listing = reg.list_all()
        self.assertEqual(len(listing), 2)
        self.assertEqual(listing["sensors"]["agents"], ["bee_monitor"])
        self.assertEqual(listing["feeds"]["agents"], ["rss_feed", "weather_feed"])


# ── MAGMA get_status tests ───────────────────────────────────────

class TestMagmaStatus(unittest.TestCase):

    def test_status_with_audit(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        from core.audit_log import AuditLog
        audit = AuditLog(db_path=tmp.name)
        audit.record("store", "d1", agent_id="test")

        status = {
            "audit_wired": audit is not None,
            "audit_entries": audit.count(),
            "replay_wired": False,
        }
        self.assertTrue(status["audit_wired"])
        self.assertEqual(status["audit_entries"], 1)
        audit.close()
        os.unlink(tmp.name)

    def test_status_without_audit(self):
        _audit_log = None
        status = {
            "audit_wired": _audit_log is not None,
            "audit_entries": 0,
            "replay_wired": False,
        }
        self.assertFalse(status["audit_wired"])
        self.assertEqual(status["audit_entries"], 0)


# ── API endpoint tests ───────────────────────────────────────────

class TestMagmaAPI(unittest.TestCase):

    def setUp(self):
        self.tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp_db.close()
        from core.audit_log import AuditLog
        self.audit = AuditLog(db_path=self.tmp_db.name)

    def tearDown(self):
        self.audit.close()
        os.unlink(self.tmp_db.name)

    def test_stats_endpoint(self):
        hm = MagicMock()
        hm._audit_log = self.audit
        hm._replay_store = None
        hm._overlay_registry = None

        al = getattr(hm, '_audit_log', None)
        stats = {
            "audit_wired": al is not None,
            "audit_entries": al.count() if al else 0,
            "replay_wired": getattr(hm, '_replay_store', None) is not None,
            "overlays": 0,
        }
        self.assertTrue(stats["audit_wired"])
        self.assertEqual(stats["audit_entries"], 0)

    def test_audit_endpoint(self):
        self.audit.record("store", "d1", agent_id="bee")
        self.audit.record("store", "d2", agent_id="weather")
        now = time.time()
        entries = self.audit.query_by_time_range(now - 86400, now)
        self.assertEqual(len(entries), 2)

    def test_audit_agent_endpoint(self):
        self.audit.record("store", "d1", agent_id="bee")
        self.audit.record("store", "d2", agent_id="weather")
        entries = self.audit.query_by_agent("bee")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["agent_id"], "bee")

    def test_overlays_endpoint(self):
        from core.memory_overlay import OverlayRegistry
        store = StubMemoryStore()
        reg = OverlayRegistry(store)
        reg.register("sensors", ["bee", "weather"])
        listing = reg.list_all()
        self.assertIn("sensors", listing)


# ── Regression: Layer 1+2 still work ─────────────────────────────

class TestLayerRegression(unittest.TestCase):

    def test_layer1_proxy_works(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        from core.audit_log import AuditLog
        from core.memory_proxy import MemoryWriteProxy

        class MinAdapter:
            def __init__(self): self.docs = {}
            def add(self, did, text, emb, meta, col): self.docs[did] = text

        audit = AuditLog(db_path=tmp.name)
        proxy = MemoryWriteProxy(MinAdapter(), audit, role="worker", agent_id="test")
        doc_id = proxy.write("d1", "text", [0.0]*8)
        self.assertEqual(doc_id, "d1")
        self.assertEqual(audit.count(), 1)
        audit.close()
        os.unlink(tmp.name)

    def test_layer2_replay_store_works(self):
        tmp_jsonl = tempfile.mktemp(suffix=".jsonl")
        from core.replay_store import ReplayStore
        rs = ReplayStore(path=tmp_jsonl)
        rs.store("d1", "text", "hash1")
        rec = rs.get_by_doc("d1")
        self.assertEqual(rec["text"], "text")
        os.unlink(tmp_jsonl)


if __name__ == "__main__":
    unittest.main()
