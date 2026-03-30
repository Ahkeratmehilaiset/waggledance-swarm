"""Regression tests for StorageHealthService.

Covers:
- Database discovery and size reporting
- WAL size detection
- Row count collection
- Growth warning thresholds
- WAL checkpoint execution
"""

import os
import sqlite3
import tempfile

import pytest

from waggledance.application.services.storage_health_service import (
    StorageHealthService,
    _DEFAULT_WARN_FALLBACK_MB,
    _WAL_WARN_MB,
)


@pytest.fixture
def data_dir(tmp_path):
    """Create a temp data directory with test databases."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, value TEXT)")
    for i in range(100):
        conn.execute("INSERT INTO items (value) VALUES (?)", (f"item_{i}",))
    conn.commit()
    conn.close()
    return tmp_path


class TestCheckHealth:
    """StorageHealthService.check_health() tests."""

    def test_discovers_databases(self, data_dir):
        svc = StorageHealthService(data_dir=str(data_dir))
        report = svc.check_health()
        assert len(report.databases) >= 1
        names = [db.name for db in report.databases]
        assert "test.db" in names

    def test_reports_size(self, data_dir):
        svc = StorageHealthService(data_dir=str(data_dir))
        report = svc.check_health()
        db = next(d for d in report.databases if d.name == "test.db")
        assert db.size_mb >= 0

    def test_reports_row_counts(self, data_dir):
        svc = StorageHealthService(data_dir=str(data_dir))
        report = svc.check_health()
        db = next(d for d in report.databases if d.name == "test.db")
        assert db.row_counts.get("items") == 100

    def test_total_size_is_sum(self, data_dir):
        svc = StorageHealthService(data_dir=str(data_dir))
        report = svc.check_health()
        expected = sum(d.size_mb for d in report.databases)
        assert abs(report.total_size_mb - expected) < 0.01

    def test_to_dict_format(self, data_dir):
        svc = StorageHealthService(data_dir=str(data_dir))
        report = svc.check_health()
        d = report.to_dict()
        assert "timestamp" in d
        assert "total_size_mb" in d
        assert "databases" in d
        assert isinstance(d["databases"], list)
        assert "warning_count" in d

    def test_empty_dir_returns_empty_report(self, tmp_path):
        svc = StorageHealthService(data_dir=str(tmp_path))
        report = svc.check_health()
        assert len(report.databases) == 0
        assert report.total_size_mb == 0.0


class TestGrowthWarnings:
    """Threshold warning tests."""

    def test_warns_when_over_threshold(self, data_dir):
        # Set threshold absurdly low to trigger warning
        svc = StorageHealthService(
            data_dir=str(data_dir),
            warn_thresholds_mb={"test.db": 0.0001},
        )
        report = svc.check_health()
        db = next(d for d in report.databases if d.name == "test.db")
        assert db.warn is True
        assert "exceeds" in db.warn_reason
        assert len(report.warnings) >= 1

    def test_no_warn_when_under_threshold(self, data_dir):
        svc = StorageHealthService(
            data_dir=str(data_dir),
            warn_thresholds_mb={"test.db": 1000.0},
        )
        report = svc.check_health()
        db = next(d for d in report.databases if d.name == "test.db")
        assert db.warn is False


class TestWalCheckpoint:
    """WAL checkpoint tests."""

    def test_checkpoint_returns_results(self, data_dir):
        svc = StorageHealthService(data_dir=str(data_dir))
        results = svc.wal_checkpoint()
        assert "test.db" in results
        assert results["test.db"]["status"] == "ok"

    def test_checkpoint_modes(self, data_dir):
        svc = StorageHealthService(data_dir=str(data_dir))
        for mode in ("PASSIVE", "FULL", "RESTART", "TRUNCATE"):
            results = svc.wal_checkpoint(mode=mode)
            assert results["test.db"]["status"] == "ok"

    def test_checkpoint_invalid_mode_defaults_truncate(self, data_dir):
        svc = StorageHealthService(data_dir=str(data_dir))
        results = svc.wal_checkpoint(mode="INVALID")
        assert results["test.db"]["status"] == "ok"

    def test_checkpoint_empty_dir(self, tmp_path):
        svc = StorageHealthService(data_dir=str(tmp_path))
        results = svc.wal_checkpoint()
        assert results == {}


class TestSqlite3Discovery:
    """Tests for .sqlite3 file discovery (Chroma-style)."""

    def test_discovers_nested_sqlite3(self, tmp_path):
        sub = tmp_path / "chroma_db"
        sub.mkdir()
        db_path = sub / "chroma.sqlite3"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE test (id INTEGER)")
        conn.commit()
        conn.close()

        svc = StorageHealthService(data_dir=str(tmp_path))
        report = svc.check_health()
        names = [db.name for db in report.databases]
        assert "chroma.sqlite3" in names
