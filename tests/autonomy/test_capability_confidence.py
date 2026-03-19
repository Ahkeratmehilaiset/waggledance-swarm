# SPDX-License-Identifier: Apache-2.0
"""Tests for CapabilityConfidenceTracker."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from waggledance.core.learning.capability_confidence import (
    CapabilityConfidenceTracker,
    DEFAULT_ALPHA,
    DEFAULT_INITIAL_CONFIDENCE,
)


@pytest.fixture
def tracker(tmp_path):
    return CapabilityConfidenceTracker(
        persist_path=str(tmp_path / "confidence.json"),
    )


class TestEMAUpdate:
    def test_first_success(self, tracker):
        conf = tracker.update("solve.math", verified=True)
        expected = DEFAULT_ALPHA * DEFAULT_INITIAL_CONFIDENCE + (1 - DEFAULT_ALPHA) * 1.0
        assert conf == pytest.approx(expected)

    def test_first_failure(self, tracker):
        conf = tracker.update("solve.math", verified=False)
        expected = DEFAULT_ALPHA * DEFAULT_INITIAL_CONFIDENCE + (1 - DEFAULT_ALPHA) * 0.0
        assert conf == pytest.approx(expected)

    def test_repeated_success_approaches_one(self, tracker):
        for _ in range(200):
            conf = tracker.update("solve.math", verified=True)
        assert conf > 0.99

    def test_repeated_failure_approaches_zero(self, tracker):
        for _ in range(200):
            conf = tracker.update("solve.math", verified=False)
        assert conf < 0.01

    def test_mixed_converges(self, tracker):
        # 80% success rate
        for i in range(100):
            tracker.update("solve.math", verified=(i % 5 != 0))
        conf = tracker.get_confidence("solve.math")
        # Should be roughly around 0.8
        assert 0.6 < conf < 0.95


class TestQueries:
    def test_get_all(self, tracker):
        tracker.update("solve.math", True)
        tracker.update("solve.thermal", False)
        all_conf = tracker.get_all()
        assert "solve.math" in all_conf
        assert "solve.thermal" in all_conf

    def test_get_lowest(self, tracker):
        tracker.update("solve.math", True)
        tracker.update("solve.bad", False)
        tracker.update("solve.ok", True)
        lowest = tracker.get_lowest(2)
        assert len(lowest) == 2
        assert lowest[0][0] == "solve.bad"  # lowest confidence first

    def test_get_confidence_unknown(self, tracker):
        assert tracker.get_confidence("unknown") == DEFAULT_INITIAL_CONFIDENCE

    def test_get_trends(self, tracker):
        # Improving solver: starts bad, ends good → EMA > raw
        for _ in range(10):
            tracker.update("solve.improving", False)
        for _ in range(10):
            tracker.update("solve.improving", True)
        # Degrading solver: starts good, ends bad → EMA < raw
        for _ in range(10):
            tracker.update("solve.degrading", True)
        for _ in range(10):
            tracker.update("solve.degrading", False)

        improving, degrading = tracker.get_trends(1)
        # Improving: EMA boosted by recent successes > raw 50%
        assert len(improving) >= 1
        assert improving[0][0] == "solve.improving"
        # Degrading: EMA dragged down by recent failures < raw 50%
        assert len(degrading) >= 1
        assert degrading[0][0] == "solve.degrading"


class TestPersistence:
    def test_save_and_load(self, tmp_path):
        path = str(tmp_path / "confidence.json")
        t1 = CapabilityConfidenceTracker(persist_path=path)
        t1.update("solve.math", True)
        t1.update("solve.math", True)
        conf1 = t1.get_confidence("solve.math")

        t2 = CapabilityConfidenceTracker(persist_path=path)
        conf2 = t2.get_confidence("solve.math")
        assert conf1 == pytest.approx(conf2)

    def test_file_format(self, tmp_path):
        path = tmp_path / "confidence.json"
        t = CapabilityConfidenceTracker(persist_path=str(path))
        t.update("solve.math", True)
        data = json.loads(path.read_text())
        assert "solve.math" in data
        assert "confidence" in data["solve.math"]
        assert "total_observations" in data["solve.math"]


class TestSelfEntitySync:
    def test_sync_to_self_entity(self, tracker):
        tracker.update("solve.math", True)
        tracker.update("solve.thermal", False)

        from waggledance.core.world.world_model import WorldModel
        wm = WorldModel(cognitive_graph=None, profile="TEST")
        # With None graph, sync returns False
        assert tracker.sync_to_self_entity(wm) is False

    def test_stats(self, tracker):
        tracker.update("solve.math", True)
        s = tracker.stats()
        assert s["tracked_capabilities"] == 1
        assert s["alpha"] == DEFAULT_ALPHA


class TestRuntimeIntegration:
    def test_runtime_has_ledger_and_confidence(self):
        from waggledance.core.autonomy.runtime import AutonomyRuntime
        rt = AutonomyRuntime(profile="TEST")
        assert rt.prediction_ledger is not None
        assert rt.capability_confidence is not None

    def test_handle_query_records_prediction(self):
        from waggledance.core.autonomy.runtime import AutonomyRuntime
        rt = AutonomyRuntime(profile="TEST")
        rt.start()

        initial_buffer = len(rt.prediction_ledger.recent(1000))
        rt.handle_query("What is 2+2?")
        after_buffer = len(rt.prediction_ledger.recent(1000))
        assert after_buffer > initial_buffer

        rt.stop()

    def test_handle_query_updates_confidence(self):
        from waggledance.core.autonomy.runtime import AutonomyRuntime
        rt = AutonomyRuntime(profile="TEST")
        rt.start()

        rt.handle_query("What is 2+2?")
        all_conf = rt.capability_confidence.get_all()
        # At least one capability should have been tracked
        assert len(all_conf) >= 1

        rt.stop()


class TestAPIEndpoint:
    def test_autonomy_service_capability_confidence(self):
        from waggledance.application.services.autonomy_service import AutonomyService
        svc = AutonomyService(profile="TEST")
        svc.start()
        result = svc.get_capability_confidence()
        assert result["available"] is True
        assert "scores" in result
        svc.stop()
