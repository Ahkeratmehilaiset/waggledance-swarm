"""SQLite persistence for WorldModel baselines and entity registry."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class SQLiteWorldStore:
    """Persistent storage for WorldModel baselines and entities.

    Stores baseline values, entity attributes, and world snapshots
    in SQLite with WAL mode for concurrent read access.
    """

    def __init__(self, db_path: str = "data/world_store.db"):
        self._db_path = db_path
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._create_tables()

    def _create_tables(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS baselines (
                entity_id     TEXT NOT NULL,
                metric_name   TEXT NOT NULL,
                baseline_value REAL NOT NULL,
                confidence    REAL NOT NULL DEFAULT 0.5,
                sample_count  INTEGER NOT NULL DEFAULT 1,
                last_updated  REAL NOT NULL,
                source_type   TEXT NOT NULL DEFAULT 'observed',
                PRIMARY KEY (entity_id, metric_name)
            );

            CREATE TABLE IF NOT EXISTS entities (
                entity_id   TEXT PRIMARY KEY,
                entity_type TEXT NOT NULL,
                attributes  TEXT NOT NULL DEFAULT '{}',
                profile     TEXT NOT NULL DEFAULT '',
                created_at  REAL NOT NULL,
                updated_at  REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS world_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                timestamp   REAL NOT NULL,
                profile     TEXT NOT NULL DEFAULT '',
                data        TEXT NOT NULL,
                source_type TEXT NOT NULL DEFAULT 'observed'
            );

            CREATE INDEX IF NOT EXISTS idx_baselines_entity
                ON baselines(entity_id);
            CREATE INDEX IF NOT EXISTS idx_snapshots_time
                ON world_snapshots(timestamp);
        """)
        self._conn.commit()

    def upsert_baseline(self, entity_id: str, metric_name: str,
                        value: float, confidence: float = 0.5,
                        sample_count: int = 1,
                        source_type: str = "observed") -> None:
        """Insert or update a baseline value."""
        with self._lock:
            self._conn.execute("""
                INSERT INTO baselines (entity_id, metric_name, baseline_value,
                                       confidence, sample_count, last_updated, source_type)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(entity_id, metric_name)
                DO UPDATE SET
                    baseline_value = excluded.baseline_value,
                    confidence = excluded.confidence,
                    sample_count = excluded.sample_count,
                    last_updated = excluded.last_updated,
                    source_type = excluded.source_type
            """, (entity_id, metric_name, value, confidence,
                  sample_count, time.time(), source_type))
            self._conn.commit()

    def get_baseline(self, entity_id: str, metric_name: str) -> Optional[Dict[str, Any]]:
        """Get a single baseline value."""
        row = self._conn.execute(
            "SELECT baseline_value, confidence, sample_count, last_updated, source_type "
            "FROM baselines WHERE entity_id = ? AND metric_name = ?",
            (entity_id, metric_name)
        ).fetchone()
        if not row:
            return None
        return {
            "entity_id": entity_id,
            "metric_name": metric_name,
            "baseline_value": row[0],
            "confidence": row[1],
            "sample_count": row[2],
            "last_updated": row[3],
            "source_type": row[4],
        }

    def get_entity_baselines(self, entity_id: str) -> Dict[str, float]:
        """Get all baselines for an entity."""
        rows = self._conn.execute(
            "SELECT metric_name, baseline_value FROM baselines WHERE entity_id = ?",
            (entity_id,)
        ).fetchall()
        return {name: val for name, val in rows}

    def get_all_baselines(self) -> Dict[str, Dict[str, float]]:
        """Get all baselines grouped by entity."""
        rows = self._conn.execute(
            "SELECT entity_id, metric_name, baseline_value FROM baselines"
        ).fetchall()
        result: Dict[str, Dict[str, float]] = {}
        for eid, metric, val in rows:
            if eid not in result:
                result[eid] = {}
            result[eid][metric] = val
        return result

    def upsert_entity(self, entity_id: str, entity_type: str,
                      attributes: Dict = None, profile: str = "") -> None:
        """Insert or update an entity."""
        now = time.time()
        attrs_json = json.dumps(attributes or {})
        with self._lock:
            self._conn.execute("""
                INSERT INTO entities (entity_id, entity_type, attributes, profile,
                                      created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(entity_id)
                DO UPDATE SET
                    entity_type = excluded.entity_type,
                    attributes = excluded.attributes,
                    profile = excluded.profile,
                    updated_at = excluded.updated_at
            """, (entity_id, entity_type, attrs_json, profile, now, now))
            self._conn.commit()

    def get_entity(self, entity_id: str) -> Optional[Dict[str, Any]]:
        """Get an entity by ID."""
        row = self._conn.execute(
            "SELECT entity_type, attributes, profile, created_at, updated_at "
            "FROM entities WHERE entity_id = ?",
            (entity_id,)
        ).fetchone()
        if not row:
            return None
        return {
            "entity_id": entity_id,
            "entity_type": row[0],
            "attributes": json.loads(row[1]),
            "profile": row[2],
            "created_at": row[3],
            "updated_at": row[4],
        }

    def list_entities(self, entity_type: str = None,
                      profile: str = None) -> List[Dict[str, Any]]:
        """List entities with optional filters."""
        query = "SELECT entity_id, entity_type, profile FROM entities WHERE 1=1"
        params: list = []
        if entity_type:
            query += " AND entity_type = ?"
            params.append(entity_type)
        if profile:
            query += " AND profile = ?"
            params.append(profile)
        rows = self._conn.execute(query, params).fetchall()
        return [{"entity_id": r[0], "entity_type": r[1], "profile": r[2]}
                for r in rows]

    def save_snapshot(self, snapshot_id: str, data: Dict[str, Any],
                      profile: str = "", source_type: str = "observed") -> None:
        """Persist a world snapshot."""
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO world_snapshots "
                "(snapshot_id, timestamp, profile, data, source_type) "
                "VALUES (?, ?, ?, ?, ?)",
                (snapshot_id, time.time(), profile, json.dumps(data), source_type)
            )
            self._conn.commit()

    def get_snapshot(self, snapshot_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a world snapshot."""
        row = self._conn.execute(
            "SELECT timestamp, profile, data, source_type "
            "FROM world_snapshots WHERE snapshot_id = ?",
            (snapshot_id,)
        ).fetchone()
        if not row:
            return None
        return {
            "snapshot_id": snapshot_id,
            "timestamp": row[0],
            "profile": row[1],
            "data": json.loads(row[2]),
            "source_type": row[3],
        }

    def list_snapshots(self, limit: int = 50,
                       profile: str = None) -> List[Dict[str, Any]]:
        """List recent snapshots."""
        query = "SELECT snapshot_id, timestamp, profile, source_type FROM world_snapshots"
        params: list = []
        if profile:
            query += " WHERE profile = ?"
            params.append(profile)
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(query, params).fetchall()
        return [{"snapshot_id": r[0], "timestamp": r[1], "profile": r[2],
                 "source_type": r[3]} for r in rows]

    def close(self) -> None:
        """Close the database connection."""
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass

    def __del__(self):
        self.close()

    def stats(self) -> Dict[str, Any]:
        baselines = self._conn.execute("SELECT COUNT(*) FROM baselines").fetchone()[0]
        entities = self._conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        snapshots = self._conn.execute("SELECT COUNT(*) FROM world_snapshots").fetchone()[0]
        return {
            "baselines": baselines,
            "entities": entities,
            "snapshots": snapshots,
            "db_path": self._db_path,
        }
