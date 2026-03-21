"""Tests for chat telemetry module."""
import pytest
from unittest.mock import MagicMock
from core.chat_telemetry import ChatTelemetry


class TestTelemetry:
    def test_record_request_telemetry_no_crash(self):
        hive = MagicMock()
        hive._last_chat_method = "test"
        hive._last_chat_agent_id = "test"
        t = ChatTelemetry(hive)
        t.record_request_telemetry("test", 0.8, 100.0, True, "hello")

    def test_populate_hot_cache_skips_low_score(self):
        hive = MagicMock()
        hive.consciousness = MagicMock()
        hive.consciousness.hot_cache = MagicMock()
        t = ChatTelemetry(hive)
        t.populate_hot_cache("query", "response", score=0.3, detected_lang="fi")
        hive.consciousness.hot_cache.put.assert_not_called()

    def test_populate_hot_cache_skips_non_fi(self):
        hive = MagicMock()
        hive.consciousness = MagicMock()
        hive.consciousness.hot_cache = MagicMock()
        t = ChatTelemetry(hive)
        t.populate_hot_cache("query", "response", score=0.8, detected_lang="en")
        hive.consciousness.hot_cache.put.assert_not_called()

    def test_populate_hot_cache_stores_valid(self):
        hive = MagicMock()
        hive.consciousness = MagicMock()
        hive.consciousness.hot_cache = MagicMock()
        t = ChatTelemetry(hive)
        t.populate_hot_cache("query", "This is a valid response.", score=0.8, detected_lang="fi")
        hive.consciousness.hot_cache.put.assert_called_once()
