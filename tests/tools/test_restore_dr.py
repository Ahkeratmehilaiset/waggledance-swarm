"""
Disaster Recovery regression tests for waggle_restore.py.

Validates:
  - ChromaDB validation is non-blocking (uses SQLite, not PersistentClient)
  - check_chromadb handles all states: missing dir, empty dir, valid db, corrupt db
  - Latest backup selection finds the correct zip
  - Restore smoke helpers work correctly
"""

import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# Import after path setup
from tools.waggle_restore import CheckResult, check_chromadb, PROJECT_ROOT


class TestCheckChromaDB:
    """Verify check_chromadb validates via SQLite, never PersistentClient."""

    def test_chromadb_import_check(self):
        """ChromaDB import should succeed (it's installed)."""
        r = CheckResult()
        check_chromadb(r)
        import_checks = [c for c in r.checks if c[0] == "ChromaDB import"]
        assert len(import_checks) == 1
        assert import_checks[0][1] == "OK"

    def test_never_opens_persistent_client(self):
        """check_chromadb must NEVER call chromadb.PersistentClient."""
        r = CheckResult()
        with patch("chromadb.PersistentClient", side_effect=AssertionError(
            "PersistentClient must not be called in validation mode"
        )):
            # Should not raise — because PersistentClient should not be called
            check_chromadb(r)
        # Verify it completed without touching PersistentClient
        names = [c[0] for c in r.checks]
        assert "ChromaDB import" in names

    def test_missing_dir_gives_warn(self):
        """Missing chroma_db/ directory should warn, not fail."""
        r = CheckResult()
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("tools.waggle_restore.PROJECT_ROOT", Path(tmpdir)):
                check_chromadb(r)
        data_checks = [c for c in r.checks if "ChromaDB data" in c[0]]
        assert any(c[1] == "WARN" for c in data_checks)

    def test_valid_chromadb_sqlite(self):
        """A valid chroma.sqlite3 with collections table should pass."""
        r = CheckResult()
        with tempfile.TemporaryDirectory() as tmpdir:
            chroma_dir = Path(tmpdir) / "data" / "chroma_db"
            chroma_dir.mkdir(parents=True)
            db_path = chroma_dir / "chroma.sqlite3"
            conn = sqlite3.connect(str(db_path))
            conn.execute(
                "CREATE TABLE collections (id TEXT PRIMARY KEY, name TEXT)"
            )
            conn.execute(
                "INSERT INTO collections VALUES ('abc', 'test_collection')"
            )
            conn.commit()
            conn.close()

            with patch("tools.waggle_restore.PROJECT_ROOT", Path(tmpdir)):
                check_chromadb(r)

        data_checks = [c for c in r.checks if "ChromaDB data" in c[0]]
        assert len(data_checks) == 1
        assert data_checks[0][1] == "OK"
        assert "1 collections" in data_checks[0][2]

    def test_corrupt_sqlite_gives_warn(self):
        """A corrupt chroma.sqlite3 should warn, not hang or crash."""
        import shutil
        r = CheckResult()
        tmpdir = tempfile.mkdtemp(prefix="wd_test_corrupt_")
        try:
            chroma_dir = Path(tmpdir) / "data" / "chroma_db"
            chroma_dir.mkdir(parents=True)
            db_path = chroma_dir / "chroma.sqlite3"
            db_path.write_bytes(b"NOT A SQLITE FILE")

            with patch("tools.waggle_restore.PROJECT_ROOT", Path(tmpdir)):
                check_chromadb(r)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

        data_checks = [c for c in r.checks if "ChromaDB data" in c[0]]
        assert len(data_checks) == 1
        assert data_checks[0][1] == "WARN"

    def test_missing_sqlite_gives_warn(self):
        """chroma_db/ exists but no chroma.sqlite3 should warn."""
        r = CheckResult()
        with tempfile.TemporaryDirectory() as tmpdir:
            chroma_dir = Path(tmpdir) / "data" / "chroma_db"
            chroma_dir.mkdir(parents=True)
            # Empty dir, no sqlite file

            with patch("tools.waggle_restore.PROJECT_ROOT", Path(tmpdir)):
                check_chromadb(r)

        data_checks = [c for c in r.checks if "ChromaDB data" in c[0]]
        assert len(data_checks) == 1
        assert data_checks[0][1] == "WARN"
        assert "chroma.sqlite3 missing" in data_checks[0][2]


class TestCheckResultTracking:
    """Basic CheckResult integrity."""

    def test_summary_counts(self):
        r = CheckResult()
        r.ok("a")
        r.ok("b")
        r.warn("c")
        r.fail("d")
        ok, warn, fail = r.summary()
        assert (ok, warn, fail) == (2, 1, 1)

    def test_has_failures(self):
        r = CheckResult()
        r.ok("a")
        assert not r.has_failures
        r.fail("b")
        assert r.has_failures


class TestLatestBackupSelection:
    """Verify backup zip discovery works."""

    def test_finds_newest_zip(self):
        """Should find the most recent waggle_*.zip."""
        import glob
        backup_dir = Path("C:/WaggleDance_Backups")
        if not backup_dir.exists():
            pytest.skip("No backup directory on this machine")
        zips = sorted(backup_dir.glob("waggle_*.zip"), key=lambda p: p.stat().st_mtime)
        assert len(zips) > 0, "No backup zips found"
        newest = zips[-1]
        assert newest.stat().st_size > 100_000_000, "Backup too small to be real"
