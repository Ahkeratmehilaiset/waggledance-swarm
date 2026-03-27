"""SQLite persistence for WorkingMemory — short-term context with session management."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class SQLiteWorkingMemory:
    """Persistent backing store for WorkingMemory.

    Stores short-term context items with salience, category, and TTL.
    Enables session recovery and cross-request context sharing.
    """

    def __init__(self, db_path: str = "data/working_memory.db",
                 default_ttl_seconds: float = 3600):
        self._db_path = db_path
        self._default_ttl = default_ttl_seconds
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._create_tables()

    def _create_tables(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS context_items (
                key         TEXT PRIMARY KEY,
                value       TEXT NOT NULL,
                category    TEXT NOT NULL DEFAULT 'general',
                salience    REAL NOT NULL DEFAULT 0.5,
                created_at  REAL NOT NULL,
                expires_at  REAL NOT NULL,
                session_id  TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_context_category
                ON context_items(category);
            CREATE INDEX IF NOT EXISTS idx_context_session
                ON context_items(session_id);
        """)
        self._conn.commit()

    def store(self, key: str, value: Any, category: str = "general",
              salience: float = 0.5, ttl_seconds: float = None,
              session_id: str = "") -> None:
        """Store a context item."""
        now = time.time()
        ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl
        val_json = json.dumps(value) if not isinstance(value, str) else value
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO context_items "
                "(key, value, category, salience, created_at, expires_at, session_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (key, val_json, category, salience, now, now + ttl, session_id)
            )
            self._conn.commit()

    def get(self, key: str) -> Optional[Any]:
        """Get a context item by key (returns None if expired)."""
        now = time.time()
        row = self._conn.execute(
            "SELECT value FROM context_items WHERE key = ? AND expires_at > ?",
            (key, now)
        ).fetchone()
        if not row:
            return None
        try:
            return json.loads(row[0])
        except (json.JSONDecodeError, TypeError):
            return row[0]

    def get_by_category(self, category: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Get all active items in a category."""
        now = time.time()
        rows = self._conn.execute(
            "SELECT key, value, salience, created_at FROM context_items "
            "WHERE category = ? AND expires_at > ? "
            "ORDER BY salience DESC LIMIT ?",
            (category, now, limit)
        ).fetchall()
        results = []
        for key, val, salience, created in rows:
            try:
                parsed = json.loads(val)
            except (json.JSONDecodeError, TypeError):
                parsed = val
            results.append({
                "key": key, "value": parsed,
                "salience": salience, "created_at": created,
            })
        return results

    def clear_category(self, category: str) -> int:
        """Remove all items in a category."""
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM context_items WHERE category = ?", (category,))
            self._conn.commit()
            return cursor.rowcount

    def clear_expired(self) -> int:
        """Remove expired items."""
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM context_items WHERE expires_at < ?", (time.time(),))
            self._conn.commit()
            return cursor.rowcount

    def clear_session(self, session_id: str) -> int:
        """Remove all items from a session."""
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM context_items WHERE session_id = ?", (session_id,))
            self._conn.commit()
            return cursor.rowcount

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass

    def __del__(self):
        if not hasattr(self, '_lock'):
            conn = getattr(self, '_conn', None)
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            return
        self.close()

    def stats(self) -> Dict[str, Any]:
        now = time.time()
        row = self._conn.execute("SELECT COUNT(*) FROM context_items").fetchone()
        total = row[0] if row else 0
        row = self._conn.execute(
            "SELECT COUNT(*) FROM context_items WHERE expires_at > ?", (now,)
        ).fetchone()
        active = row[0] if row else 0
        return {"total_items": total, "active_items": active, "db_path": self._db_path}
