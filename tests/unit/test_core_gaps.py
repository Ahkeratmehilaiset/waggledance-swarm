"""Unit tests for previously untested core modules.

Covers:
  - disk_guard (check_disk_space, get_disk_status, DiskSpaceError)
  - hive_support (PriorityLock, StructuredLogger)
  - settings_validator (WaggleSettings, LLMConfig, validate_settings, resolve_secret)
  - shared_routing_helpers (probe_micromodel, get_route_telemetry, get_learning_ledger)
  - chat_history (ChatHistory — SQLite round-trip)
"""

import asyncio
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


# ══════════════════════════════════════════════════════════════════
# 1. disk_guard
# ══════════════════════════════════════════════════════════════════

class TestDiskGuard(unittest.TestCase):
    """Tests for core/disk_guard.py."""

    def test_check_disk_space_ok(self):
        """Normal disk returns status=ok with positive free_mb."""
        from core.disk_guard import check_disk_space
        result = check_disk_space(".")
        self.assertEqual(result["status"], "ok")
        self.assertGreater(result["free_mb"], 0)
        self.assertGreater(result["free_gb"], 0)
        self.assertGreater(result["total_gb"], 0)

    def test_check_disk_space_with_label(self):
        """Label parameter doesn't break the function."""
        from core.disk_guard import check_disk_space
        result = check_disk_space(".", label="test_label")
        self.assertIn("status", result)

    def test_check_disk_space_invalid_path(self):
        """Invalid path returns status=unknown (no crash)."""
        from core.disk_guard import check_disk_space
        result = check_disk_space("/nonexistent/path/that/doesnt/exist")
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["free_mb"], -1)

    @patch("core.disk_guard.shutil.disk_usage")
    def test_check_disk_space_warning(self, mock_usage):
        """Low disk (200MB free) triggers warning status."""
        from core.disk_guard import check_disk_space
        mock_usage.return_value = MagicMock(
            free=200 * 1024 * 1024,
            total=500 * 1024**3,
        )
        result = check_disk_space(".")
        self.assertEqual(result["status"], "warning")

    @patch("core.disk_guard.shutil.disk_usage")
    def test_check_disk_space_critical_raises(self, mock_usage):
        """Critical disk (<100MB) raises DiskSpaceError."""
        from core.disk_guard import check_disk_space, DiskSpaceError
        mock_usage.return_value = MagicMock(
            free=50 * 1024 * 1024,  # 50 MB
            total=500 * 1024**3,
        )
        with self.assertRaises(DiskSpaceError):
            check_disk_space(".")

    def test_get_disk_status_no_raise(self):
        """get_disk_status never raises, even on critical disk."""
        from core.disk_guard import get_disk_status
        result = get_disk_status(".")
        self.assertIn("status", result)
        self.assertIn("free_mb", result)

    @patch("core.disk_guard.shutil.disk_usage")
    def test_get_disk_status_critical_no_raise(self, mock_usage):
        """get_disk_status returns critical status without raising."""
        from core.disk_guard import get_disk_status
        mock_usage.return_value = MagicMock(
            free=10 * 1024 * 1024,
            total=500 * 1024**3,
        )
        result = get_disk_status(".")
        self.assertEqual(result["status"], "critical")

    def test_disk_space_error_is_oserror(self):
        """DiskSpaceError inherits from OSError."""
        from core.disk_guard import DiskSpaceError
        self.assertTrue(issubclass(DiskSpaceError, OSError))


# ══════════════════════════════════════════════════════════════════
# 2. hive_support — PriorityLock
# ══════════════════════════════════════════════════════════════════

class TestPriorityLock(unittest.TestCase):
    """Tests for core/hive_support.PriorityLock."""

    def setUp(self):
        self._loop = asyncio.new_event_loop()

    def tearDown(self):
        self._loop.close()

    def _run(self, coro):
        return self._loop.run_until_complete(coro)

    def test_initial_state_not_active(self):
        from core.hive_support import PriorityLock
        lock = PriorityLock()
        self.assertFalse(lock.chat_active)
        self.assertFalse(lock.should_skip)

    def test_chat_enters_blocks(self):
        from core.hive_support import PriorityLock
        lock = PriorityLock()
        self._run(lock.chat_enters(cooldown_s=0.01))
        self.assertTrue(lock.chat_active)
        self.assertTrue(lock.should_skip)

    def test_chat_exits_releases(self):
        from core.hive_support import PriorityLock
        lock = PriorityLock()
        self._run(lock.chat_enters(cooldown_s=0.01))
        self._run(lock.chat_exits())
        self.assertFalse(lock.chat_active)

    def test_cooldown_active_after_exit(self):
        from core.hive_support import PriorityLock
        lock = PriorityLock()
        self._run(lock.chat_enters(cooldown_s=10.0))
        self._run(lock.chat_exits())
        # Chat exited but cooldown is long
        self.assertFalse(lock.chat_active)
        self.assertTrue(lock.in_cooldown)
        self.assertTrue(lock.should_skip)

    def test_wait_if_chat_returns_immediately_when_free(self):
        from core.hive_support import PriorityLock
        lock = PriorityLock()
        # Should not block
        self._run(lock.wait_if_chat())


# ══════════════════════════════════════════════════════════════════
# 3. hive_support — StructuredLogger
# ══════════════════════════════════════════════════════════════════

class TestStructuredLogger(unittest.TestCase):
    """Tests for core/hive_support.StructuredLogger."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._log_path = os.path.join(self._tmpdir, "test_metrics.jsonl")

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_log_chat_creates_file(self):
        from core.hive_support import StructuredLogger
        logger = StructuredLogger(path=self._log_path)
        logger.log_chat(query="test", method="llm", agent_id="bee_agent")
        self.assertTrue(os.path.exists(self._log_path))

    def test_log_chat_valid_json(self):
        from core.hive_support import StructuredLogger
        logger = StructuredLogger(path=self._log_path)
        logger.log_chat(query="Hello?", method="hotcache", confidence=0.95)
        with open(self._log_path, "r", encoding="utf-8") as f:
            line = f.readline()
        data = json.loads(line)
        self.assertEqual(data["method"], "hotcache")
        self.assertEqual(data["confidence"], 0.95)
        self.assertIn("ts", data)
        self.assertIn("query_hash", data)

    def test_log_learning_event(self):
        from core.hive_support import StructuredLogger
        logger = StructuredLogger(path=self._log_path)
        logger.log_learning(event="enrichment_cycle", count=5, source="yaml")
        with open(self._log_path, "r", encoding="utf-8") as f:
            data = json.loads(f.readline())
        self.assertEqual(data["event"], "enrichment_cycle")
        self.assertEqual(data["count"], 5)

    def test_log_chat_with_extra(self):
        from core.hive_support import StructuredLogger
        logger = StructuredLogger(path=self._log_path)
        logger.log_chat(query="q", method="m", extra={"custom_key": 42})
        with open(self._log_path, "r", encoding="utf-8") as f:
            data = json.loads(f.readline())
        self.assertEqual(data["custom_key"], 42)

    def test_multiple_log_lines(self):
        from core.hive_support import StructuredLogger
        logger = StructuredLogger(path=self._log_path)
        for i in range(10):
            logger.log_chat(query=f"q{i}", method="llm")
        with open(self._log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        self.assertEqual(len(lines), 10)

    def test_query_hash_deterministic(self):
        from core.hive_support import StructuredLogger
        logger = StructuredLogger(path=self._log_path)
        logger.log_chat(query="same query", method="a")
        logger.log_chat(query="same query", method="b")
        with open(self._log_path, "r", encoding="utf-8") as f:
            d1 = json.loads(f.readline())
            d2 = json.loads(f.readline())
        self.assertEqual(d1["query_hash"], d2["query_hash"])

    def test_unicode_query(self):
        from core.hive_support import StructuredLogger
        logger = StructuredLogger(path=self._log_path)
        logger.log_chat(query="Mikä on mehiläispesän lämpötila? 🐝", method="llm")
        with open(self._log_path, "r", encoding="utf-8") as f:
            data = json.loads(f.readline())
        self.assertIn("query_hash", data)


# ══════════════════════════════════════════════════════════════════
# 4. settings_validator
# ══════════════════════════════════════════════════════════════════

class TestSettingsValidator(unittest.TestCase):
    """Tests for core/settings_validator.py."""

    def test_defaults_valid(self):
        from core.settings_validator import WaggleSettings
        s = WaggleSettings()
        self.assertEqual(s.profile, "cottage")
        self.assertEqual(s.llm.model, "phi4-mini")
        self.assertEqual(s.llm.temperature, 0.4)

    def test_valid_profiles(self):
        from core.settings_validator import WaggleSettings
        for p in ("gadget", "cottage", "home", "factory"):
            s = WaggleSettings(profile=p)
            self.assertEqual(s.profile, p)

    def test_invalid_profile_raises(self):
        from core.settings_validator import WaggleSettings
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            WaggleSettings(profile="invalid_profile")

    def test_temperature_bounds(self):
        from core.settings_validator import LLMConfig
        from pydantic import ValidationError
        LLMConfig(temperature=0.0)  # min ok
        LLMConfig(temperature=2.0)  # max ok
        with self.assertRaises(ValidationError):
            LLMConfig(temperature=-0.1)
        with self.assertRaises(ValidationError):
            LLMConfig(temperature=2.1)

    def test_timeout_minimum(self):
        from core.settings_validator import LLMConfig
        from pydantic import ValidationError
        LLMConfig(timeout=1)  # min ok
        with self.assertRaises(ValidationError):
            LLMConfig(timeout=0)

    def test_learning_config_defaults(self):
        from core.settings_validator import LearningConfig
        lc = LearningConfig()
        self.assertTrue(lc.enrichment_enabled)
        self.assertEqual(lc.enrichment_confidence, 0.80)
        self.assertFalse(lc.distillation_enabled)

    def test_runtime_config_valid_primary(self):
        from core.settings_validator import RuntimeConfig
        for p in ("waggledance", "hivemind", "shadow"):
            rc = RuntimeConfig(primary=p)
            self.assertEqual(rc.primary, p)

    def test_runtime_config_invalid_primary(self):
        from core.settings_validator import RuntimeConfig
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            RuntimeConfig(primary="unknown")

    def test_validate_settings_function(self):
        from core.settings_validator import validate_settings
        raw = {"profile": "cottage", "hivemind": {"heartbeat_interval": 30}}
        result = validate_settings(raw)
        self.assertEqual(result, raw)

    def test_validate_settings_invalid_raises(self):
        from core.settings_validator import validate_settings
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            validate_settings({"profile": "invalid"})

    def test_resolve_secret_env_wins(self):
        from core.settings_validator import resolve_secret
        os.environ["_TEST_SECRET_KEY"] = "from_env"
        try:
            result = resolve_secret("from_yaml", "_TEST_SECRET_KEY")
            self.assertEqual(result, "from_env")
        finally:
            del os.environ["_TEST_SECRET_KEY"]

    def test_resolve_secret_yaml_fallback(self):
        from core.settings_validator import resolve_secret
        result = resolve_secret("yaml_value", "_NONEXISTENT_ENV_KEY_XYZ")
        self.assertEqual(result, "yaml_value")

    def test_resolve_secret_empty(self):
        from core.settings_validator import resolve_secret
        result = resolve_secret("", "_NONEXISTENT_ENV_KEY_XYZ")
        self.assertEqual(result, "")

    def test_max_concurrent_agents_bounds(self):
        from core.settings_validator import HiveMindConfig
        from pydantic import ValidationError
        HiveMindConfig(max_concurrent_agents=1)   # min
        HiveMindConfig(max_concurrent_agents=200)  # max
        with self.assertRaises(ValidationError):
            HiveMindConfig(max_concurrent_agents=0)
        with self.assertRaises(ValidationError):
            HiveMindConfig(max_concurrent_agents=201)


# ══════════════════════════════════════════════════════════════════
# 5. shared_routing_helpers
# ══════════════════════════════════════════════════════════════════

class TestSharedRoutingHelpers(unittest.TestCase):
    """Tests for core/shared_routing_helpers.py."""

    def setUp(self):
        # Reset cached singletons before each test
        import core.shared_routing_helpers as srh
        srh._pattern_engine = None
        srh._pattern_engine_init_attempted = False
        srh._route_telemetry = None
        srh._learning_ledger = None

    def test_probe_micromodel_returns_tuple(self):
        from core.shared_routing_helpers import probe_micromodel
        result = probe_micromodel("test query")
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)
        hit, conf = result
        self.assertIsInstance(hit, bool)
        self.assertIsInstance(conf, float)

    def test_probe_micromodel_no_crash_on_import_error(self):
        """Even if PatternMatchEngine fails to import, returns (False, 0.0)."""
        import core.shared_routing_helpers as srh
        with patch.dict("sys.modules", {"core.micro_model": None}):
            srh._pattern_engine = None
            srh._pattern_engine_init_attempted = False
            result = srh.probe_micromodel("test")
            self.assertEqual(result, (False, 0.0))

    def test_probe_micromodel_caches_engine(self):
        """Second call reuses cached engine (no re-import)."""
        from core.shared_routing_helpers import probe_micromodel
        probe_micromodel("first call")
        probe_micromodel("second call")
        import core.shared_routing_helpers as srh
        self.assertTrue(srh._pattern_engine_init_attempted)

    def test_get_route_telemetry_returns_singleton(self):
        from core.shared_routing_helpers import get_route_telemetry
        t1 = get_route_telemetry()
        t2 = get_route_telemetry()
        self.assertIs(t1, t2)

    def test_get_learning_ledger_returns_singleton(self):
        from core.shared_routing_helpers import get_learning_ledger
        l1 = get_learning_ledger()
        l2 = get_learning_ledger()
        self.assertIs(l1, l2)


# ══════════════════════════════════════════════════════════════════
# 6. chat_history — SQLite round-trip
# ══════════════════════════════════════════════════════════════════

class TestChatHistory(unittest.TestCase):
    """Tests for core/chat_history.ChatHistory."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._db_path = os.path.join(self._tmpdir, "test_chat.db")

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _make(self):
        from core.chat_history import ChatHistory
        return ChatHistory(db_path=self._db_path)

    def test_create_conversation(self):
        ch = self._make()
        cid = ch.create_conversation(profile="cottage")
        self.assertIsInstance(cid, int)
        self.assertGreater(cid, 0)

    def test_add_and_get_messages(self):
        ch = self._make()
        cid = ch.create_conversation()
        mid1 = ch.add_message(cid, "user", "Hei!")
        mid2 = ch.add_message(cid, "assistant", "Moi!", agent_name="Tarhaaja")
        self.assertIsInstance(mid1, int)
        self.assertIsInstance(mid2, int)
        self.assertNotEqual(mid1, mid2)
        conv = ch.get_conversation(cid)
        self.assertIsNotNone(conv)
        self.assertEqual(len(conv["messages"]), 2)

    def test_feedback_round_trip(self):
        ch = self._make()
        cid = ch.create_conversation()
        mid = ch.add_message(cid, "assistant", "response")
        fid = ch.add_feedback(mid, rating=1, correction="better answer")
        self.assertIsInstance(fid, int)

    def test_get_conversations_list(self):
        ch = self._make()
        ch.create_conversation(profile="cottage")
        ch.create_conversation(profile="home")
        convs = ch.get_conversations(limit=10)
        self.assertGreaterEqual(len(convs), 2)

    def test_get_conversations_filter_by_profile(self):
        ch = self._make()
        cid1 = ch.create_conversation(profile="cottage")
        ch.add_message(cid1, "user", "cottage msg")
        cid2 = ch.create_conversation(profile="home")
        ch.add_message(cid2, "user", "home msg")
        cottage = ch.get_conversations(profile="cottage")
        self.assertTrue(all(c["profile"] == "cottage" for c in cottage))

    def test_nonexistent_conversation(self):
        ch = self._make()
        conv = ch.get_conversation(9999)
        self.assertIsNone(conv)

    def test_unicode_content(self):
        ch = self._make()
        cid = ch.create_conversation()
        ch.add_message(cid, "user", "Mikä on mehiläispesän lämpötila? 🐝🍯")
        conv = ch.get_conversation(cid)
        self.assertIn("🐝", conv["messages"][0]["content"])

    def test_message_with_metadata(self):
        ch = self._make()
        cid = ch.create_conversation()
        mid = ch.add_message(
            cid, "assistant", "response",
            agent_name="Tautivahti", language="fi", response_time_ms=150,
        )
        conv = ch.get_conversation(cid)
        msg = conv["messages"][0]
        self.assertEqual(msg["agent_name"], "Tautivahti")
        self.assertEqual(msg["language"], "fi")

    def test_get_or_create_conversation_creates_new(self):
        ch = self._make()
        # Two calls always create at least one conversation
        cid1 = ch.get_or_create_conversation(profile="cottage")
        self.assertIsInstance(cid1, int)
        self.assertGreater(cid1, 0)

    def test_recent_messages(self):
        ch = self._make()
        cid = ch.create_conversation()
        for i in range(5):
            ch.add_message(cid, "user", f"msg {i}")
        msgs = ch.get_recent_messages(limit=3)
        self.assertEqual(len(msgs), 3)


if __name__ == "__main__":
    unittest.main()
