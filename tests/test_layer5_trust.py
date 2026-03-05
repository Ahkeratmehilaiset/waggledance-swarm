"""
MAGMA Layer 5: Trust & Reputation Engine — Tests
Suite #41: 25 tests
"""

import os
import sys
import sqlite3
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.audit_log import AuditLog
from core.trust_engine import TrustSignal, AgentReputation, TrustEngine


class TestTrustSignal(unittest.TestCase):
    """TrustSignal dataclass basics."""

    def test_defaults(self):
        s = TrustSignal("test", 0.8)
        self.assertEqual(s.name, "test")
        self.assertAlmostEqual(s.value, 0.8)
        self.assertAlmostEqual(s.weight, 1.0)
        self.assertGreater(s.timestamp, 0)

    def test_custom_fields(self):
        s = TrustSignal("x", 0.5, weight=0.3, timestamp=100.0)
        self.assertAlmostEqual(s.weight, 0.3)
        self.assertAlmostEqual(s.timestamp, 100.0)


class TestAgentReputation(unittest.TestCase):
    """AgentReputation container logic."""

    def test_empty_composite(self):
        rep = AgentReputation("agent1")
        self.assertAlmostEqual(rep.composite_score, 0.0)

    def test_composite_with_signals(self):
        rep = AgentReputation("agent1")
        rep.add_signal(TrustSignal("hallucination_rate", 0.9))
        rep.add_signal(TrustSignal("validation_ratio", 0.8))
        score = rep.composite_score
        self.assertGreater(score, 0.0)
        self.assertLessEqual(score, 1.0)

    def test_domain_scores(self):
        rep = AgentReputation("agent1")
        rep.add_signal(TrustSignal("hallucination_rate", 0.9))
        rep.add_signal(TrustSignal("hallucination_rate", 0.7))
        ds = rep.domain_scores
        self.assertIn("hallucination_rate", ds)
        self.assertAlmostEqual(ds["hallucination_rate"], 0.8)

    def test_trend_stable_few_signals(self):
        rep = AgentReputation("agent1")
        rep.add_signal(TrustSignal("x", 0.5))
        self.assertEqual(rep.trend, "stable")

    def test_trend_improving(self):
        rep = AgentReputation("agent1")
        for i in range(30):
            rep.add_signal(TrustSignal("x", 0.3, timestamp=float(i)))
        for i in range(30, 60):
            rep.add_signal(TrustSignal("x", 0.9, timestamp=float(i)))
        self.assertEqual(rep.trend, "improving")

    def test_trend_declining(self):
        rep = AgentReputation("agent1")
        for i in range(30):
            rep.add_signal(TrustSignal("x", 0.9, timestamp=float(i)))
        for i in range(30, 60):
            rep.add_signal(TrustSignal("x", 0.2, timestamp=float(i)))
        self.assertEqual(rep.trend, "declining")

    def test_to_dict(self):
        rep = AgentReputation("agent1")
        rep.add_signal(TrustSignal("hallucination_rate", 0.9))
        d = rep.to_dict()
        self.assertEqual(d["agent_id"], "agent1")
        self.assertIn("composite_score", d)
        self.assertIn("trend", d)
        self.assertIn("signal_counts", d)

    def test_rolling_window_cap(self):
        rep = AgentReputation("agent1")
        for i in range(250):
            rep.add_signal(TrustSignal("x", 0.5, timestamp=float(i)))
        self.assertEqual(len(rep.signals["x"]), 200)


class TestTrustEngine(unittest.TestCase):
    """TrustEngine with real SQLite."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp, "audit.db")
        self.audit = AuditLog(self.db_path)
        self.engine = TrustEngine(self.audit)

    def tearDown(self):
        self.audit.close()

    def test_record_signal(self):
        self.engine.record_signal("a1", "hallucination_rate", 0.9)
        rows = self.engine._conn.execute(
            "SELECT * FROM trust_signals WHERE agent_id='a1'"
        ).fetchall()
        self.assertEqual(len(rows), 1)

    def test_compute_reputation_empty(self):
        rep = self.engine.compute_reputation("unknown")
        self.assertAlmostEqual(rep.composite_score, 0.0)

    def test_compute_reputation_with_signals(self):
        for _ in range(5):
            self.engine.record_signal("a1", "hallucination_rate", 0.85)
            self.engine.record_signal("a1", "validation_ratio", 0.90)
        rep = self.engine.compute_reputation("a1")
        self.assertGreater(rep.composite_score, 0.5)

    def test_get_all_reputations(self):
        self.engine.record_signal("a1", "x", 0.9)
        self.engine.record_signal("a2", "x", 0.5)
        reps = self.engine.get_all_reputations()
        self.assertIn("a1", reps)
        self.assertIn("a2", reps)

    def test_get_ranking(self):
        self.engine.record_signal("a1", "hallucination_rate", 0.9)
        self.engine.record_signal("a2", "hallucination_rate", 0.3)
        ranking = self.engine.get_ranking()
        self.assertEqual(len(ranking), 2)
        self.assertEqual(ranking[0]["agent_id"], "a1")

    def test_get_domain_experts(self):
        self.engine.record_signal("a1", "fact_production", 0.9)
        self.engine.record_signal("a2", "fact_production", 0.4)
        experts = self.engine.get_domain_experts("fact_production")
        self.assertEqual(len(experts), 2)
        self.assertEqual(experts[0]["agent_id"], "a1")

    def test_get_signal_history(self):
        self.engine.record_signal("a1", "x", 0.5)
        self.engine.record_signal("a1", "y", 0.7)
        hist = self.engine.get_signal_history("a1")
        self.assertEqual(len(hist), 2)

    def test_decay_inactive_no_decay_for_recent(self):
        self.engine.record_signal("a1", "x", 0.9)
        decayed = self.engine.decay_inactive(threshold_hours=72)
        self.assertEqual(len(decayed), 0)

    def test_decay_inactive_applies(self):
        # Insert old signal directly
        old_ts = time.time() - 100 * 3600  # 100 hours ago
        self.engine._conn.execute(
            "INSERT INTO trust_signals (agent_id, signal_name, value, timestamp) "
            "VALUES (?, ?, ?, ?)",
            ("old_agent", "x", 0.9, old_ts)
        )
        self.engine._conn.commit()
        decayed = self.engine.decay_inactive(threshold_hours=72)
        self.assertIn("old_agent", decayed)


class TestSignalSources(unittest.TestCase):
    """Test individual signal dimension computation."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp, "audit.db")
        self.audit = AuditLog(self.db_path)
        self.engine = TrustEngine(self.audit)

    def tearDown(self):
        self.audit.close()

    def test_hallucination_signal(self):
        self.engine.record_signal("a1", "hallucination_rate", 0.95)
        rep = self.engine.compute_reputation("a1")
        self.assertIn("hallucination_rate", rep.domain_scores)

    def test_validation_ratio_signal(self):
        self.engine.record_signal("a1", "validation_ratio", 0.80)
        rep = self.engine.compute_reputation("a1")
        self.assertIn("validation_ratio", rep.domain_scores)

    def test_consensus_participation_signal(self):
        self.engine.record_signal("a1", "consensus_participation", 1.0)
        rep = self.engine.compute_reputation("a1")
        self.assertIn("consensus_participation", rep.domain_scores)

    def test_correction_rate_signal(self):
        self.engine.record_signal("a1", "correction_rate", 0.90)
        rep = self.engine.compute_reputation("a1")
        self.assertIn("correction_rate", rep.domain_scores)

    def test_fact_production_signal(self):
        self.engine.record_signal("a1", "fact_production", 1.0)
        rep = self.engine.compute_reputation("a1")
        self.assertIn("fact_production", rep.domain_scores)

    def test_temporal_freshness_signal(self):
        self.engine.record_signal("a1", "temporal_freshness", 0.5)
        rep = self.engine.compute_reputation("a1")
        self.assertIn("temporal_freshness", rep.domain_scores)


class TestIntegration(unittest.TestCase):
    """End-to-end with audit + provenance."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp, "audit.db")
        self.audit = AuditLog(self.db_path)

    def tearDown(self):
        self.audit.close()

    def test_e2e_with_provenance(self):
        from core.provenance import ProvenanceTracker
        prov = ProvenanceTracker(self.audit)
        # Record some audit entries
        self.audit.record("write", "doc1", agent_id="a1")
        prov.record_validation("doc1", "a1", "agree")
        prov.record_validation("doc1", "a1", "agree")

        engine = TrustEngine(self.audit, provenance=prov)
        engine.record_signal("a1", "hallucination_rate", 0.95)
        rep = engine.compute_reputation("a1")
        self.assertGreater(rep.composite_score, 0.0)
        self.assertIn("validation_ratio", rep.domain_scores)

    def test_e2e_multi_agent_ranking(self):
        engine = TrustEngine(self.audit)
        for _ in range(10):
            engine.record_signal("good_agent", "hallucination_rate", 0.95)
            engine.record_signal("bad_agent", "hallucination_rate", 0.2)
        ranking = engine.get_ranking()
        self.assertEqual(ranking[0]["agent_id"], "good_agent")
        self.assertGreater(ranking[0]["composite_score"],
                           ranking[1]["composite_score"])


if __name__ == "__main__":
    unittest.main()
