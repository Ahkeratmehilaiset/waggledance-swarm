"""Tests for AudioAdapter — audio sensor adapter."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from waggledance.adapters.sensors.audio_adapter import AudioAdapter


class TestAudioAdapterNoMonitor:
    """Tests when no AudioMonitor is available."""

    def test_not_available(self):
        adapter = AudioAdapter()
        assert adapter.available is False

    def test_capability_id(self):
        adapter = AudioAdapter()
        assert adapter.CAPABILITY_ID == "sense.audio"

    def test_get_status_unavailable(self):
        adapter = AudioAdapter()
        status = adapter.get_status()
        assert status["available"] is False
        assert status["events_processed"] == 0

    def test_bee_analysis_unavailable(self):
        adapter = AudioAdapter()
        result = adapter.get_bee_analysis()
        assert result["available"] is False

    def test_bird_detections_unavailable(self):
        adapter = AudioAdapter()
        result = adapter.get_bird_detections()
        assert result["available"] is False


class TestAudioAdapterWithMock:
    """Tests with a mock AudioMonitor."""

    def _make_mock(self):
        class MockAudioMonitor:
            def status(self):
                return {"recording": True}
            def get_bee_analysis(self):
                return {"frequency_hz": 250, "queen_present": True}
            def get_bird_detections(self):
                return {"species": ["tit", "robin"], "count": 2}
        return MockAudioMonitor()

    def test_available_with_monitor(self):
        adapter = AudioAdapter(audio_monitor=self._make_mock())
        assert adapter.available is True

    def test_ingest_audio_event(self):
        adapter = AudioAdapter(audio_monitor=self._make_mock())
        result = adapter.ingest_audio_event("audio/bee", {"level": 0.8})
        assert result["success"] is True
        assert result["topic"] == "audio/bee"
        assert result["capability_id"] == "sense.audio"

    def test_stats_after_events(self):
        adapter = AudioAdapter(audio_monitor=self._make_mock())
        adapter.ingest_audio_event("a", {})
        adapter.ingest_audio_event("b", {})
        s = adapter.stats()
        assert s["events_processed"] == 2
