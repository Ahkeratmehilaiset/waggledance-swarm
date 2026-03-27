"""Tests for baseline_store.count() and stats() safe-fetch patterns (PHASE 2 fix).

Verifies that count/stats methods return 0 instead of crashing when
fetchone() returns None (e.g., closed connection, corrupt db).
"""

import sqlite3
import pytest
from unittest.mock import MagicMock, patch

from waggledance.core.world.baseline_store import BaselineStore


class TestBaselineStoreCount:
    """Verify baseline_store.count() handles None fetchone."""

    def test_count_returns_int_on_normal_db(self, tmp_path):
        db = tmp_path / "baselines.db"
        store = BaselineStore(db_path=str(db))
        assert store.count() == 0
        store.close()

    def test_count_handles_none_fetchone(self, tmp_path):
        """When fetchone() returns None, count() should return 0."""
        db = tmp_path / "baselines.db"
        store = BaselineStore(db_path=str(db))
        # Replace _conn with a mock whose execute().fetchone() returns None
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None
        mock_conn.execute.return_value = mock_cursor
        store._conn = mock_conn
        assert store.count() == 0


class TestSQLiteStoreStats:
    """Verify stats() methods across persistence stores handle None fetchone."""

    def test_world_store_stats_safe(self, tmp_path):
        from waggledance.adapters.persistence.sqlite_world_store import SQLiteWorldStore
        store = SQLiteWorldStore(db_path=str(tmp_path / "world.db"))
        result = store.stats()
        assert result["baselines"] == 0
        assert result["entities"] == 0
        assert result["snapshots"] == 0
        store.close()

    def test_case_store_stats_safe(self, tmp_path):
        from waggledance.adapters.persistence.sqlite_case_store import SQLiteCaseStore
        store = SQLiteCaseStore(db_path=str(tmp_path / "cases.db"))
        result = store.stats()
        assert result["total"] == 0
        assert result["gold_rate"] == 0.0
        store.close()

    def test_verifier_store_stats_safe(self, tmp_path):
        from waggledance.adapters.persistence.sqlite_verifier_store import SQLiteVerifierStore
        store = SQLiteVerifierStore(db_path=str(tmp_path / "verifier.db"))
        result = store.stats()
        assert result["total"] == 0
        assert result["passed"] == 0
        assert result["pass_rate"] == 0.0
        store.close()

    def test_procedural_store_stats_safe(self, tmp_path):
        from waggledance.adapters.persistence.sqlite_procedural_store import SQLiteProceduralStore
        store = SQLiteProceduralStore(db_path=str(tmp_path / "procs.db"))
        result = store.stats()
        assert result["procedures"] == 0
        assert result["anti_patterns"] == 0
        store.close()

    def test_working_memory_stats_safe(self, tmp_path):
        from waggledance.adapters.persistence.sqlite_working_memory import SQLiteWorkingMemory
        store = SQLiteWorkingMemory(db_path=str(tmp_path / "wm.db"))
        result = store.stats()
        assert result["total_items"] == 0
        assert result["active_items"] == 0
        store.close()
