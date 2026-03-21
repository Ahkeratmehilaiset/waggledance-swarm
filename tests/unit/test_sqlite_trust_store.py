"""Tests for SQLiteTrustStore — persistent agent reputation (v1.17.0)."""

import asyncio
import os
import tempfile
import unittest

import pytest

try:
    import aiosqlite
    HAS_AIOSQLITE = True
except ImportError:
    HAS_AIOSQLITE = False

from waggledance.core.domain.trust_score import AgentTrust, TrustSignals
from waggledance.adapters.trust.sqlite_trust_store import SQLiteTrustStore


def _make_signals(**overrides) -> TrustSignals:
    defaults = dict(
        hallucination_rate=0.1,
        validation_rate=0.8,
        consensus_agreement=0.7,
        correction_rate=0.05,
        fact_production_rate=0.6,
        freshness_score=0.9,
    )
    defaults.update(overrides)
    return TrustSignals(**defaults)


@pytest.mark.skipif(not HAS_AIOSQLITE, reason="aiosqlite not installed")
class TestSQLiteTrustStore(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._db_path = os.path.join(self._tmpdir, "trust_test.db")
        self._loop = asyncio.new_event_loop()

    def tearDown(self):
        self._loop.close()
        try:
            os.unlink(self._db_path)
        except FileNotFoundError:
            pass
        os.rmdir(self._tmpdir)

    def _run(self, coro):
        return self._loop.run_until_complete(coro)

    def test_get_trust_missing_returns_none(self):
        store = SQLiteTrustStore(self._db_path)
        try:
            result = self._run(store.get_trust("nonexistent"))
            self.assertIsNone(result)
        finally:
            self._run(store.close())

    def test_update_and_get(self):
        store = SQLiteTrustStore(self._db_path)
        try:
            signals = _make_signals()
            trust = self._run(store.update_trust("agent_1", signals))
            self.assertIsInstance(trust, AgentTrust)
            self.assertEqual(trust.agent_id, "agent_1")
            self.assertGreater(trust.composite_score, 0)

            retrieved = self._run(store.get_trust("agent_1"))
            self.assertIsNotNone(retrieved)
            self.assertEqual(retrieved.agent_id, "agent_1")
            self.assertAlmostEqual(retrieved.composite_score, trust.composite_score, places=6)
        finally:
            self._run(store.close())

    def test_update_overwrites(self):
        store = SQLiteTrustStore(self._db_path)
        try:
            self._run(store.update_trust("a1", _make_signals(hallucination_rate=0.1)))
            self._run(store.update_trust("a1", _make_signals(hallucination_rate=0.9)))
            trust = self._run(store.get_trust("a1"))
            # Higher hallucination → lower composite
            self.assertLess(trust.composite_score, 0.7)
        finally:
            self._run(store.close())

    def test_persistence_across_reopen(self):
        """Data survives close + re-open."""
        store = SQLiteTrustStore(self._db_path)
        try:
            self._run(store.update_trust("persist_agent", _make_signals()))
        finally:
            self._run(store.close())

        store2 = SQLiteTrustStore(self._db_path)
        try:
            trust = self._run(store2.get_trust("persist_agent"))
            self.assertIsNotNone(trust)
            self.assertEqual(trust.agent_id, "persist_agent")
        finally:
            self._run(store2.close())

    def test_get_ranking(self):
        store = SQLiteTrustStore(self._db_path)
        try:
            self._run(store.update_trust("low", _make_signals(hallucination_rate=0.9)))
            self._run(store.update_trust("mid", _make_signals(hallucination_rate=0.5)))
            self._run(store.update_trust("high", _make_signals(hallucination_rate=0.0)))

            ranking = self._run(store.get_ranking(limit=3))
            self.assertEqual(len(ranking), 3)
            # Sorted descending by composite
            self.assertEqual(ranking[0].agent_id, "high")
            self.assertEqual(ranking[2].agent_id, "low")
        finally:
            self._run(store.close())

    def test_ranking_limit(self):
        store = SQLiteTrustStore(self._db_path)
        try:
            for i in range(5):
                self._run(store.update_trust(f"a{i}", _make_signals()))
            ranking = self._run(store.get_ranking(limit=2))
            self.assertEqual(len(ranking), 2)
        finally:
            self._run(store.close())

    def test_composite_score_range(self):
        store = SQLiteTrustStore(self._db_path)
        try:
            # Perfect signals
            perfect = _make_signals(
                hallucination_rate=0.0, validation_rate=1.0,
                consensus_agreement=1.0, correction_rate=0.0,
                fact_production_rate=1.0, freshness_score=1.0)
            trust = self._run(store.update_trust("perfect", perfect))
            self.assertAlmostEqual(trust.composite_score, 1.0)

            # Worst signals
            worst = _make_signals(
                hallucination_rate=1.0, validation_rate=0.0,
                consensus_agreement=0.0, correction_rate=1.0,
                fact_production_rate=0.0, freshness_score=0.0)
            trust = self._run(store.update_trust("worst", worst))
            self.assertAlmostEqual(trust.composite_score, 0.0)
        finally:
            self._run(store.close())

    def test_signals_roundtrip(self):
        """All 6 signal fields survive write→read."""
        store = SQLiteTrustStore(self._db_path)
        try:
            signals = TrustSignals(
                hallucination_rate=0.11,
                validation_rate=0.22,
                consensus_agreement=0.33,
                correction_rate=0.44,
                fact_production_rate=0.55,
                freshness_score=0.66,
            )
            self._run(store.update_trust("roundtrip", signals))
            trust = self._run(store.get_trust("roundtrip"))
            s = trust.signals
            self.assertAlmostEqual(s.hallucination_rate, 0.11, places=4)
            self.assertAlmostEqual(s.validation_rate, 0.22, places=4)
            self.assertAlmostEqual(s.consensus_agreement, 0.33, places=4)
            self.assertAlmostEqual(s.correction_rate, 0.44, places=4)
            self.assertAlmostEqual(s.fact_production_rate, 0.55, places=4)
            self.assertAlmostEqual(s.freshness_score, 0.66, places=4)
        finally:
            self._run(store.close())

    def test_default_score_for_new_agent(self):
        """Composite score matches formula, not hardcoded 0.5."""
        store = SQLiteTrustStore(self._db_path)
        try:
            # All-zero signals → composite = 0.25 + 0 + 0 + 0.15 + 0 + 0 = 0.40
            zero_signals = _make_signals(
                hallucination_rate=0.0, validation_rate=0.0,
                consensus_agreement=0.0, correction_rate=0.0,
                fact_production_rate=0.0, freshness_score=0.0)
            trust = self._run(store.update_trust("zero", zero_signals))
            self.assertAlmostEqual(trust.composite_score, 0.40)
        finally:
            self._run(store.close())


if __name__ == "__main__":
    unittest.main()
