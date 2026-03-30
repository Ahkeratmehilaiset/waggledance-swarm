"""Storage health introspection and maintenance service.

Reports per-database sizes, WAL status, row counts, and growth warnings.
Provides WAL checkpoint helper for periodic maintenance without requiring
a full backup cycle.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

# Default thresholds (MB) — log WARNING when exceeded
_DEFAULT_WARN_MB: Dict[str, float] = {
    "waggle_dance.db": 500.0,
    "case_store.db": 200.0,
    "audit_log.db": 100.0,
    "verifier_store.db": 50.0,
    "world_store.db": 50.0,
    "chroma.sqlite3": 500.0,
}
_DEFAULT_WARN_FALLBACK_MB = 100.0

# WAL size threshold — warn when WAL exceeds this (MB)
_WAL_WARN_MB = 32.0


@dataclass
class DatabaseInfo:
    """Health snapshot for a single SQLite database."""
    path: str
    name: str
    size_mb: float
    wal_size_mb: float
    row_counts: Dict[str, int] = field(default_factory=dict)
    warn: bool = False
    warn_reason: str = ""


@dataclass
class StorageHealthReport:
    """Aggregated storage health report."""
    timestamp: float
    total_size_mb: float
    total_wal_mb: float
    databases: List[DatabaseInfo] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "total_size_mb": round(self.total_size_mb, 1),
            "total_wal_mb": round(self.total_wal_mb, 1),
            "warning_count": len(self.warnings),
            "warnings": self.warnings,
            "databases": [
                {
                    "name": db.name,
                    "size_mb": round(db.size_mb, 1),
                    "wal_size_mb": round(db.wal_size_mb, 2),
                    "row_counts": db.row_counts,
                    "warn": db.warn,
                    "warn_reason": db.warn_reason,
                }
                for db in self.databases
            ],
        }


class StorageHealthService:
    """Introspection and maintenance for SQLite storage layer.

    Scans known database paths, collects size/row metrics,
    and provides WAL checkpoint functionality.
    """

    def __init__(
        self,
        data_dir: str = "data",
        warn_thresholds_mb: Optional[Dict[str, float]] = None,
    ):
        self._data_dir = Path(data_dir)
        self._thresholds = warn_thresholds_mb or _DEFAULT_WARN_MB

    def check_health(self) -> StorageHealthReport:
        """Collect health snapshot for all discovered SQLite databases."""
        report = StorageHealthReport(
            timestamp=time.time(),
            total_size_mb=0.0,
            total_wal_mb=0.0,
        )

        # Discover all .db and .sqlite3 files under data_dir
        db_paths: List[Path] = []
        if self._data_dir.exists():
            db_paths.extend(self._data_dir.glob("*.db"))
            db_paths.extend(self._data_dir.glob("**/*.sqlite3"))

        for db_path in sorted(db_paths):
            info = self._inspect_db(db_path)
            report.databases.append(info)
            report.total_size_mb += info.size_mb
            report.total_wal_mb += info.wal_size_mb

            if info.warn:
                report.warnings.append(f"{info.name}: {info.warn_reason}")

        # Emit log warnings for anything over threshold
        for w in report.warnings:
            log.warning("Storage health: %s", w)

        return report

    def wal_checkpoint(self, mode: str = "TRUNCATE") -> Dict[str, Any]:
        """Run WAL checkpoint on all databases in data_dir.

        Args:
            mode: PASSIVE, FULL, RESTART, or TRUNCATE (default).
                  TRUNCATE resets WAL to zero size after checkpoint.

        Returns:
            Dict mapping db name to checkpoint result or error.
        """
        if mode.upper() not in ("PASSIVE", "FULL", "RESTART", "TRUNCATE"):
            mode = "TRUNCATE"

        results: Dict[str, Any] = {}
        if not self._data_dir.exists():
            return results

        for db_path in sorted(self._data_dir.glob("*.db")):
            name = db_path.name
            try:
                conn = sqlite3.connect(str(db_path), timeout=5)
                cursor = conn.execute(f"PRAGMA wal_checkpoint({mode.upper()})")
                row = cursor.fetchone()
                conn.close()
                # row = (blocked, wal_pages, checkpointed_pages)
                results[name] = {
                    "status": "ok",
                    "blocked": row[0] if row else -1,
                    "wal_pages": row[1] if row else -1,
                    "checkpointed": row[2] if row else -1,
                }
            except Exception as exc:
                results[name] = {"status": "error", "error": str(exc)}
                log.warning("WAL checkpoint failed for %s: %s", name, exc)

        return results

    def _inspect_db(self, db_path: Path) -> DatabaseInfo:
        """Collect size, WAL, and row count info for a single database."""
        name = db_path.name
        size_mb = db_path.stat().st_size / (1024 * 1024) if db_path.exists() else 0.0

        # WAL file
        wal_path = Path(str(db_path) + "-wal")
        wal_mb = wal_path.stat().st_size / (1024 * 1024) if wal_path.exists() else 0.0

        # Row counts for key tables
        row_counts: Dict[str, int] = {}
        try:
            conn = sqlite3.connect(str(db_path), timeout=5)
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'"
            )
            tables = [r[0] for r in cursor.fetchall()]
            for table in tables:
                try:
                    cnt = conn.execute(f"SELECT COUNT(*) FROM [{table}]").fetchone()
                    row_counts[table] = cnt[0] if cnt else 0
                except Exception:
                    row_counts[table] = -1
            conn.close()
        except Exception as exc:
            log.debug("Could not inspect %s: %s", name, exc)

        # Check thresholds
        warn = False
        warn_reason = ""
        threshold = self._thresholds.get(name, _DEFAULT_WARN_FALLBACK_MB)
        if size_mb > threshold:
            warn = True
            warn_reason = f"{size_mb:.1f} MB exceeds {threshold:.0f} MB threshold"
        elif wal_mb > _WAL_WARN_MB:
            warn = True
            warn_reason = f"WAL {wal_mb:.1f} MB exceeds {_WAL_WARN_MB:.0f} MB threshold"

        return DatabaseInfo(
            path=str(db_path),
            name=name,
            size_mb=size_mb,
            wal_size_mb=wal_mb,
            row_counts=row_counts,
            warn=warn,
            warn_reason=warn_reason,
        )
