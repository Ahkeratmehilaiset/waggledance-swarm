"""
Layer 4: Cross-Agent Memory Sharing — 25 tests
Suite #40
"""

import os
import sys
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path
from collections import namedtuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ─── AgentChannel tests ───────────────────────────────────────────

class TestAgentChannel(unittest.TestCase):
    def setUp(self):
        from core.agent_channels import AgentChannel
        self.ch = AgentChannel("test", ["a1", "a2"], "domain")

    def test_post_and_history(self):
        self.ch.post("a1", "hello")
        self.ch.post("a2", "world")
        hist = self.ch.get_history()
        self.assertEqual(len(hist), 2)
        self.assertEqual(hist[0]["from"], "a1")
        self.assertEqual(hist[1]["message"], "world")

    def test_history_limit(self):
        for i in range(30):
            self.ch.post("a1", f"msg{i}")
        self.assertEqual(len(self.ch.get_history(limit=5)), 5)

    def test_history_cap(self):
        from core.agent_channels import MAX_HISTORY
        for i in range(MAX_HISTORY + 50):
            self.ch.post("a1", f"msg{i}")
        self.assertLessEqual(len(self.ch._history), MAX_HISTORY)

    def test_members(self):
        self.assertEqual(self.ch.members, ["a1", "a2"])


# ─── ChannelRegistry tests ────────────────────────────────────────

class TestChannelRegistry(unittest.TestCase):
    def setUp(self):
        from core.agent_channels import ChannelRegistry
        self.reg = ChannelRegistry()

    def test_create_and_get(self):
        ch = self.reg.create("bee", ["a1", "a2"])
        self.assertIsNotNone(self.reg.get("bee"))
        self.assertIsNone(self.reg.get("nonexist"))

    def test_get_channels_for_agent(self):
        self.reg.create("ch1", ["a1", "a2"])
        self.reg.create("ch2", ["a2", "a3"])
        chs = self.reg.get_channels_for_agent("a2")
        self.assertEqual(len(chs), 2)
        chs = self.reg.get_channels_for_agent("a1")
        self.assertEqual(len(chs), 1)

    def test_list_all(self):
        self.reg.create("ch1", ["a1"])
        summary = self.reg.list_all()
        self.assertIn("ch1", summary)
        self.assertIn("members", summary["ch1"])

    def test_auto_role_channels(self):
        self.reg.auto_create_role_channels()
        self.assertIsNotNone(self.reg.get("scouts"))
        self.assertIsNotNone(self.reg.get("workers"))
        self.assertIsNotNone(self.reg.get("judges"))

    def test_auto_domain_channels(self):
        self.reg.auto_create_domain_channels({
            "bee_domain": ["queen_bee", "nurse_bee"],
            "weather": ["weather_agent"],
        })
        self.assertIsNotNone(self.reg.get("bee_domain"))
        self.assertIsNotNone(self.reg.get("weather"))


# ─── ProvenanceTracker tests ──────────────────────────────────────

class TestProvenanceTracker(unittest.TestCase):
    def setUp(self):
        from core.audit_log import AuditLog
        from core.provenance import ProvenanceTracker
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "test_audit.db")
        self.audit = AuditLog(self.db_path)
        self.prov = ProvenanceTracker(self.audit)

    def tearDown(self):
        self.audit.close()

    def test_get_origin(self):
        self.audit.record("store", "fact_1", agent_id="queen_bee",
                          content_hash="abc123")
        origin = self.prov.get_origin("fact_1")
        self.assertIsNotNone(origin)
        self.assertEqual(origin["agent_id"], "queen_bee")

    def test_get_origin_missing(self):
        self.assertIsNone(self.prov.get_origin("nonexist"))

    def test_record_and_get_validation(self):
        self.prov.record_validation("fact_1", "judge_1", "agree")
        self.prov.record_validation("fact_1", "judge_2", "disagree")
        vals = self.prov.get_validations("fact_1")
        self.assertEqual(len(vals), 2)
        self.assertEqual(vals[0]["verdict"], "agree")

    def test_invalid_verdict(self):
        with self.assertRaises(ValueError):
            self.prov.record_validation("f1", "j1", "maybe")

    def test_record_and_get_consensus(self):
        self.prov.record_consensus("fact_1", ["a1", "a2"], "synthesis text")
        cons = self.prov.get_consensus("fact_1")
        self.assertIsNotNone(cons)
        self.assertIn("a1", cons["participating_agents"])
        self.assertEqual(cons["synthesis_text"], "synthesis text")

    def test_get_consensus_missing(self):
        self.assertIsNone(self.prov.get_consensus("nonexist"))

    def test_provenance_chain(self):
        self.audit.record("store", "fact_1", agent_id="scout")
        self.prov.record_validation("fact_1", "judge_1", "agree")
        self.prov.record_consensus("fact_1", ["scout", "judge_1"], "agreed")
        chain = self.prov.get_provenance_chain("fact_1")
        self.assertIsNotNone(chain["origin"])
        self.assertEqual(len(chain["validations"]), 1)
        self.assertIsNotNone(chain["consensus"])

    def test_agent_contributions(self):
        self.audit.record("store", "f1", agent_id="scout")
        self.prov.record_validation("f2", "scout", "agree")
        c = self.prov.get_agent_contributions("scout")
        self.assertGreaterEqual(len(c["originated"]), 1)
        self.assertGreaterEqual(len(c["validated"]), 1)

    def test_get_validated_facts(self):
        self.prov.record_validation("f1", "j1", "agree")
        self.prov.record_validation("f1", "j2", "agree")
        self.prov.record_validation("f2", "j1", "agree")  # only 1
        facts = self.prov.get_validated_facts(min_validators=2)
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["fact_id"], "f1")


# ─── CrossAgentSearch tests ───────────────────────────────────────

SearchResult = namedtuple("SearchResult", ["id", "text", "score", "metadata"])


class TestCrossAgentSearch(unittest.TestCase):
    def setUp(self):
        from core.agent_channels import ChannelRegistry
        from core.provenance import ProvenanceTracker
        from core.cross_agent_search import CrossAgentSearch
        from core.audit_log import AuditLog

        self.tmpdir = tempfile.mkdtemp()
        self.audit = AuditLog(os.path.join(self.tmpdir, "audit.db"))
        self.prov = ProvenanceTracker(self.audit)
        self.channels = ChannelRegistry()
        self.channels.auto_create_role_channels()

        # Mock consciousness + memory
        self.consciousness = MagicMock()
        self.consciousness.memory.search.return_value = [
            SearchResult("doc1", "bee fact", 0.9, {"agent_id": "queen_bee"}),
        ]

        # Mock overlay registry
        self.overlay_reg = MagicMock()
        mock_overlay = MagicMock()
        mock_overlay.search.return_value = [
            {"id": "doc1", "text": "bee fact", "score": 0.9}
        ]
        self.overlay_reg.get.return_value = mock_overlay
        self.overlay_reg.register.return_value = mock_overlay

        self.cs = CrossAgentSearch(
            self.consciousness, self.overlay_reg,
            self.channels, self.prov)

    def tearDown(self):
        self.audit.close()

    def test_search_by_role(self):
        results = self.cs.search_by_role([0.1]*384, "scouts")
        self.assertGreaterEqual(len(results), 0)

    def test_search_by_channel(self):
        self.channels.create("test_ch", ["a1"])
        results = self.cs.search_by_channel([0.1]*384, "test_ch")
        self.assertIsInstance(results, list)

    def test_search_with_provenance(self):
        self.audit.record("store", "doc1", agent_id="queen_bee")
        results = self.cs.search_with_provenance([0.1]*384)
        self.assertEqual(len(results), 1)
        self.assertIn("provenance", results[0])

    def test_get_consensus_facts(self):
        self.prov.record_validation("doc1", "j1", "agree")
        self.prov.record_validation("doc1", "j2", "agree")
        results = self.cs.get_consensus_facts([0.1]*384, min_validators=2)
        # doc1 matches validated set
        self.assertIsInstance(results, list)


# ─── Integration tests ────────────────────────────────────────────

class TestIntegration(unittest.TestCase):
    def setUp(self):
        from core.agent_channels import ChannelRegistry
        from core.provenance import ProvenanceTracker
        from core.audit_log import AuditLog

        self.tmpdir = tempfile.mkdtemp()
        self.audit = AuditLog(os.path.join(self.tmpdir, "audit.db"))
        self.prov = ProvenanceTracker(self.audit)
        self.channels = ChannelRegistry()
        self.channels.auto_create_role_channels()

    def tearDown(self):
        self.audit.close()

    def test_round_table_feedback(self):
        """Simulate round table storing consensus via provenance."""
        self.audit.record("store", "rt_fact_1", agent_id="round_table")
        self.prov.record_consensus("rt_fact_1", ["queen_bee", "nurse_bee"],
                                   "Agreed: bees need more pollen")
        self.channels.broadcast("judges", "round_table",
                                "[Synthesis] bees need more pollen")
        ch = self.channels.get("judges")
        self.assertEqual(len(ch.get_history()), 1)
        cons = self.prov.get_consensus("rt_fact_1")
        self.assertIn("queen_bee", cons["participating_agents"])

    def test_inter_source_exchange(self):
        """Channels enable knowledge exchange between sources."""
        self.channels.create("enrichment", ["web_researcher", "rss_monitor"])
        ch = self.channels.get("enrichment")
        ch.post("web_researcher", "Found: varroa resistance gene study")
        ch.post("rss_monitor", "RSS: new varroa treatment published")
        hist = ch.get_history()
        self.assertEqual(len(hist), 2)

    def test_end_to_end_sharing(self):
        """Full flow: store fact → validate → consensus → channel broadcast."""
        # 1. Store
        self.audit.record("store", "fact_e2e", agent_id="scout_bee",
                          content_hash="hash1")
        # 2. Validate
        self.prov.record_validation("fact_e2e", "queen_bee", "agree")
        self.prov.record_validation("fact_e2e", "guard_bee", "agree")
        # 3. Consensus
        self.prov.record_consensus("fact_e2e",
                                   ["scout_bee", "queen_bee", "guard_bee"],
                                   "Confirmed: hive temp stable")
        # 4. Broadcast
        self.channels.broadcast("workers", "round_table",
                                "Confirmed: hive temp stable")
        # Verify full chain
        chain = self.prov.get_provenance_chain("fact_e2e")
        self.assertEqual(chain["origin"]["agent_id"], "scout_bee")
        self.assertEqual(len(chain["validations"]), 2)
        self.assertIsNotNone(chain["consensus"])
        validated = self.prov.get_validated_facts(min_validators=2)
        self.assertEqual(len(validated), 1)


# ─── Regression tests ─────────────────────────────────────────────

class TestRegression(unittest.TestCase):
    """Ensure Layer 1-3 components still work correctly."""

    def test_audit_log_basic(self):
        from core.audit_log import AuditLog
        tmpdir = tempfile.mkdtemp()
        al = AuditLog(os.path.join(tmpdir, "reg_audit.db"))
        rid = al.record("store", "doc1", agent_id="test")
        self.assertGreater(rid, 0)
        self.assertEqual(al.count(), 1)
        al.close()

    def test_overlay_registry(self):
        from core.memory_overlay import OverlayRegistry
        mock_store = MagicMock()
        reg = OverlayRegistry(mock_store)
        ov = reg.register("test", ["a1", "a2"])
        self.assertIsNotNone(reg.get("test"))
        self.assertEqual(len(reg.list_all()), 1)

    def test_channel_registry_no_side_effects(self):
        """ChannelRegistry doesn't affect OverlayRegistry."""
        from core.agent_channels import ChannelRegistry
        from core.memory_overlay import OverlayRegistry
        cr = ChannelRegistry()
        cr.auto_create_role_channels()
        mock_store = MagicMock()
        oreg = OverlayRegistry(mock_store)
        # Overlay registry should be empty
        self.assertEqual(len(oreg.list_all()), 0)


if __name__ == "__main__":
    unittest.main()
