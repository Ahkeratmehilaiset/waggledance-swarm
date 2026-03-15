"""Tests for core.learning_ledger — LearningLedger (Phase 4)."""

import os
import tempfile
import time
import unittest

from core.learning_ledger import LearningLedger, LedgerEntry


class TestLedgerEntry(unittest.TestCase):
    def test_round_trip(self):
        entry = LedgerEntry(
            event_type="fact_stored",
            timestamp=1000.0,
            agent_id="bee_health",
            details={"fact": "varroa count=3"},
        )
        d = entry.to_dict()
        restored = LedgerEntry.from_dict(d)
        self.assertEqual(restored.event_type, "fact_stored")
        self.assertEqual(restored.timestamp, 1000.0)
        self.assertEqual(restored.agent_id, "bee_health")
        self.assertEqual(restored.details["fact"], "varroa count=3")

    def test_from_dict_defaults(self):
        entry = LedgerEntry.from_dict({})
        self.assertEqual(entry.event_type, "")
        self.assertEqual(entry.timestamp, 0.0)
        self.assertEqual(entry.agent_id, "")
        self.assertEqual(entry.details, {})


class TestLedgerAppendAndQuery(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.path = os.path.join(self.tmpdir, "test_ledger.jsonl")
        self.ledger = LearningLedger(path=self.path)

    def test_log_creates_file(self):
        self.ledger.log("fact_stored", agent_id="a1", fact="hello")
        self.assertTrue(os.path.isfile(self.path))

    def test_log_returns_entry(self):
        entry = self.ledger.log("correction", agent_id="a2", old="x", new="y")
        self.assertIsInstance(entry, LedgerEntry)
        self.assertEqual(entry.event_type, "correction")
        self.assertEqual(entry.agent_id, "a2")
        self.assertEqual(entry.details["old"], "x")
        self.assertGreater(entry.timestamp, 0.0)

    def test_query_all(self):
        self.ledger.log("fact_stored")
        self.ledger.log("correction")
        self.ledger.log("promotion")
        entries = self.ledger.query()
        self.assertEqual(len(entries), 3)

    def test_query_by_type(self):
        self.ledger.log("fact_stored")
        self.ledger.log("correction")
        self.ledger.log("fact_stored")
        entries = self.ledger.query(event_type="fact_stored")
        self.assertEqual(len(entries), 2)
        for e in entries:
            self.assertEqual(e.event_type, "fact_stored")

    def test_query_by_time(self):
        self.ledger.log("fact_stored")
        cutoff = time.time()
        time.sleep(0.02)  # small sleep to ensure distinct timestamps
        self.ledger.log("correction")
        entries = self.ledger.query(since=cutoff)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].event_type, "correction")

    def test_query_limit(self):
        for _ in range(20):
            self.ledger.log("fact_stored")
        entries = self.ledger.query(limit=5)
        self.assertEqual(len(entries), 5)


class TestLedgerCount(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.path = os.path.join(self.tmpdir, "test_ledger.jsonl")
        self.ledger = LearningLedger(path=self.path)

    def test_count_all(self):
        self.ledger.log("fact_stored")
        self.ledger.log("correction")
        self.assertEqual(self.ledger.count(), 2)

    def test_count_by_type(self):
        self.ledger.log("fact_stored")
        self.ledger.log("correction")
        self.ledger.log("fact_stored")
        self.assertEqual(self.ledger.count(event_type="fact_stored"), 2)
        self.assertEqual(self.ledger.count(event_type="correction"), 1)
        self.assertEqual(self.ledger.count(event_type="rollback"), 0)


class TestLedgerEmptyFile(unittest.TestCase):
    def test_query_nonexistent_file(self):
        ledger = LearningLedger(path="/tmp/does_not_exist_12345.jsonl")
        self.assertEqual(ledger.query(), [])
        self.assertEqual(ledger.count(), 0)

    def test_query_empty_file(self):
        tmpdir = tempfile.mkdtemp()
        path = os.path.join(tmpdir, "empty.jsonl")
        with open(path, "w") as f:
            f.write("")
        ledger = LearningLedger(path=path)
        self.assertEqual(ledger.query(), [])
        self.assertEqual(ledger.count(), 0)


if __name__ == "__main__":
    unittest.main()
