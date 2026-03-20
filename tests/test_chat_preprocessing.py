"""Tests for chat preprocessing module."""
import pytest
from unittest.mock import MagicMock, AsyncMock
from core.chat_preprocessing import ChatPreprocessor, PreprocessResult


class TestLanguageDetection:
    def test_auto_detection_returns_string(self):
        hive = MagicMock()
        pp = ChatPreprocessor(hive)
        result = pp.detect_lang("hei", "auto")
        assert isinstance(result, str)
        assert len(result) >= 2

    def test_explicit_language_passed_through(self):
        hive = MagicMock()
        pp = ChatPreprocessor(hive)
        assert pp.detect_lang("hello", "en") == "en"
        assert pp.detect_lang("hei", "fi") == "fi"


class TestDatetimeDetection:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_time_query(self):
        hive = MagicMock()
        hive.metrics = MagicMock()
        hive.monitor = None
        hive._notify_ws = AsyncMock()
        pp = ChatPreprocessor(hive)
        result = await pp.detect_datetime(
            "paljonko kello on", "fi", "paljonko kello on", 0.0)
        assert result is not None
        assert "Kello on" in result

    @pytest.mark.asyncio(loop_scope="function")
    async def test_date_query(self):
        hive = MagicMock()
        hive.metrics = MagicMock()
        hive.monitor = None
        hive._notify_ws = AsyncMock()
        pp = ChatPreprocessor(hive)
        result = await pp.detect_datetime(
            "mikä päivä tänään on", "fi", "mikä päivä tänään on", 0.0)
        assert result is not None
        assert "Tänään on" in result

    @pytest.mark.asyncio(loop_scope="function")
    async def test_non_datetime_returns_none(self):
        hive = MagicMock()
        pp = ChatPreprocessor(hive)
        result = await pp.detect_datetime(
            "kerro mehiläisistä", "fi", "kerro mehiläisistä", 0.0)
        assert result is None


class TestCorrectionDetection:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_strong_correction(self):
        hive = MagicMock()
        hive._last_chat_message = "Mikä on pesän lämpötila?"
        hive._last_chat_response = "Se on 50 astetta."
        hive._last_chat_agent_id = "beekeeper"
        hive.consciousness = MagicMock()
        hive.agent_levels = None
        hive.monitor = None
        hive._notify_ws = AsyncMock()
        hive.metrics = MagicMock()
        pp = ChatPreprocessor(hive)
        result = await pp.detect_correction(
            "Väärin, se on 35 astetta", "fi", "Väärin, se on 35 astetta", 0.0)
        assert result is not None
        assert "Kiitos korjauksesta" in result

    @pytest.mark.asyncio(loop_scope="function")
    async def test_no_correction_normal_message(self):
        hive = MagicMock()
        hive._last_chat_message = "test"
        hive._last_chat_response = "test"
        hive.consciousness = MagicMock()
        pp = ChatPreprocessor(hive)
        result = await pp.detect_correction(
            "Kerro lisää mehiläisistä", "fi", "Kerro lisää mehiläisistä", 0.0)
        assert result is None
