"""Tests for SeasonalEngine — Finnish beekeeping calendar."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from waggledance.core.reasoning.seasonal_engine import SeasonalEngine


class TestGetCurrentTasks:
    def setup_method(self):
        self.engine = SeasonalEngine()

    def test_january_tasks_fi(self):
        tasks = self.engine.get_current_tasks(month=1, lang="fi")
        assert isinstance(tasks, list)
        if tasks:
            assert any("paino" in t.lower() or "tarkista" in t.lower() for t in tasks)

    def test_january_tasks_en(self):
        tasks = self.engine.get_current_tasks(month=1, lang="en")
        assert isinstance(tasks, list)
        if tasks:
            assert any("weight" in t.lower() or "check" in t.lower() for t in tasks)

    def test_may_tasks_nonempty(self):
        tasks = self.engine.get_current_tasks(month=5, lang="fi")
        assert isinstance(tasks, list)

    def test_invalid_month_clamped(self):
        tasks = self.engine.get_current_tasks(month=0)
        assert isinstance(tasks, list)
        tasks13 = self.engine.get_current_tasks(month=13)
        assert isinstance(tasks13, list)


class TestGetSeasonalFactor:
    def setup_method(self):
        self.engine = SeasonalEngine()

    def test_winter_low(self):
        assert self.engine.get_seasonal_factor(month=1) < 0.2

    def test_summer_high(self):
        assert self.engine.get_seasonal_factor(month=6) >= 0.9

    def test_range_valid(self):
        for m in range(1, 13):
            f = self.engine.get_seasonal_factor(month=m)
            assert 0.0 <= f <= 1.0


class TestIsCriticalPeriod:
    def setup_method(self):
        self.engine = SeasonalEngine()

    def test_may_critical(self):
        assert self.engine.is_critical_period(month=5) is True

    def test_june_critical(self):
        assert self.engine.is_critical_period(month=6) is True

    def test_september_critical(self):
        assert self.engine.is_critical_period(month=9) is True

    def test_january_not_critical(self):
        assert self.engine.is_critical_period(month=1) is False


class TestGetRecommendations:
    def setup_method(self):
        self.engine = SeasonalEngine()

    def test_recommendations_structure(self):
        rec = self.engine.get_recommendations(month=6)
        assert "month" in rec
        assert "activity_factor" in rec
        assert "is_critical" in rec
        assert "tasks_fi" in rec
        assert "tasks_en" in rec
        assert rec["month"] == 6

    def test_recommendations_critical_month(self):
        rec = self.engine.get_recommendations(month=5)
        assert rec["is_critical"] is True
        assert rec["activity_factor"] >= 0.8


class TestStats:
    def test_stats(self):
        engine = SeasonalEngine()
        engine.get_current_tasks(month=1)
        s = engine.stats()
        assert "query_count" in s
        assert s["query_count"] >= 1
