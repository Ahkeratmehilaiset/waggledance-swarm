# SPDX-License-Identifier: Apache-2.0
"""Tests for PredictionErrorLedger."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from waggledance.core.learning.prediction_error_ledger import (
    PredictionErrorLedger,
    PredictionError,
)


@pytest.fixture
def ledger(tmp_path):
    return PredictionErrorLedger(ledger_path=str(tmp_path / "errors.jsonl"))


class TestPredictionErrorLedger:
    def test_record_creates_entry(self, ledger):
        entry = ledger.record(
            query_id="q1",
            solver_used="solve.math",
            verified=True,
            confidence=0.8,
            intent="math",
        )
        assert entry.solver_used == "solve.math"
        assert entry.error_magnitude == 0.0
        assert entry.actual_outcome == "pass"

    def test_record_failure(self, ledger):
        entry = ledger.record(
            query_id="q2",
            solver_used="solve.thermal",
            verified=False,
            confidence=0.3,
            intent="thermal",
        )
        assert entry.error_magnitude == 1.0
        assert entry.actual_outcome == "fail"

    def test_append_only(self, ledger):
        ledger.record("q1", "solve.math", True)
        ledger.record("q2", "solve.math", False)
        ledger.record("q3", "solve.thermal", True)

        # Verify file has 3 lines
        path = Path(ledger._path)
        lines = path.read_text().strip().split("\n")
        assert len(lines) == 3

    def test_recent_from_buffer(self, ledger):
        for i in range(5):
            ledger.record(f"q{i}", "solve.math", i % 2 == 0)
        recent = ledger.recent(3)
        assert len(recent) == 3

    def test_analyze_solver_profiles(self, ledger):
        # 8 successes, 2 failures for math
        for i in range(8):
            ledger.record(f"q{i}", "solve.math", True, intent="math")
        for i in range(2):
            ledger.record(f"qf{i}", "solve.math", False, intent="math")

        # 3 successes, 7 failures for thermal
        for i in range(3):
            ledger.record(f"t{i}", "solve.thermal", True, intent="thermal")
        for i in range(7):
            ledger.record(f"tf{i}", "solve.thermal", False, intent="thermal")

        analysis = ledger.analyze()
        assert analysis.total_entries == 20
        assert analysis.total_errors == 9
        assert "solve.math" in analysis.solver_profiles
        assert "solve.thermal" in analysis.solver_profiles

        math_profile = analysis.solver_profiles["solve.math"]
        assert math_profile.error_rate == pytest.approx(0.2)

        thermal_profile = analysis.solver_profiles["solve.thermal"]
        assert thermal_profile.error_rate == pytest.approx(0.7)

    def test_analyze_intent_error_rates(self, ledger):
        ledger.record("q1", "solve.math", True, intent="math")
        ledger.record("q2", "solve.math", False, intent="math")

        analysis = ledger.analyze()
        assert "math" in analysis.intent_error_rates
        assert analysis.intent_error_rates["math"] == pytest.approx(0.5)

    def test_analyze_trend_detection(self, ledger):
        # First half: all success; second half: all failure → degrading
        for i in range(10):
            ledger.record(f"q{i}", "solve.bad", True, intent="math")
        for i in range(10):
            ledger.record(f"qf{i}", "solve.bad", False, intent="math")

        analysis = ledger.analyze()
        assert "solve.bad" in analysis.degrading_solvers

    def test_analyze_empty_ledger(self, ledger):
        analysis = ledger.analyze()
        assert analysis.total_entries == 0
        assert analysis.overall_error_rate == 0.0

    def test_stats(self, ledger):
        ledger.record("q1", "solve.math", True)
        s = ledger.stats()
        assert s["buffer_size"] == 1
        assert s["file_exists"] is True

    def test_persistence_across_instances(self, tmp_path):
        path = str(tmp_path / "errors.jsonl")
        l1 = PredictionErrorLedger(ledger_path=path)
        l1.record("q1", "solve.math", True, intent="math")
        l1.record("q2", "solve.math", False, intent="math")

        # New instance reads from file
        l2 = PredictionErrorLedger(ledger_path=path)
        analysis = l2.analyze()
        assert analysis.total_entries == 2
