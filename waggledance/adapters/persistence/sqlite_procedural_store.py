"""SQLite persistence for ProceduralMemory — proven capability chains and anti-patterns."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class SQLiteProceduralStore:
    """Persistent store for procedural memory.

    Stores:
    - Proven procedures (GOLD-grade capability chains that worked)
    - Anti-patterns (QUARANTINE-grade chains to avoid)
    """

    def __init__(self, db_path: str = "data/procedural_store.db"):
        self._db_path = db_path
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._create_tables()

    def _create_tables(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS procedures (
                procedure_id   TEXT PRIMARY KEY,
                intent          TEXT NOT NULL,
                capability_chain TEXT NOT NULL,
                quality_grade   TEXT NOT NULL,
                success_count   INTEGER NOT NULL DEFAULT 1,
                failure_count   INTEGER NOT NULL DEFAULT 0,
                avg_latency_ms  REAL NOT NULL DEFAULT 0.0,
                profile         TEXT NOT NULL DEFAULT '',
                source_case_id  TEXT NOT NULL DEFAULT '',
                created_at      REAL NOT NULL,
                updated_at      REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS anti_patterns (
                pattern_id      TEXT PRIMARY KEY,
                intent          TEXT NOT NULL,
                capability_chain TEXT NOT NULL,
                failure_reason  TEXT NOT NULL DEFAULT '',
                occurrence_count INTEGER NOT NULL DEFAULT 1,
                profile         TEXT NOT NULL DEFAULT '',
                created_at      REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_procedures_intent
                ON procedures(intent);
            CREATE INDEX IF NOT EXISTS idx_anti_patterns_intent
                ON anti_patterns(intent);
        """)
        self._conn.commit()

    def store_procedure(self, procedure_id: str, intent: str,
                        capability_chain: List[str], quality_grade: str,
                        latency_ms: float = 0.0, profile: str = "",
                        source_case_id: str = "") -> None:
        """Store or update a proven procedure."""
        now = time.time()
        chain_json = json.dumps(capability_chain)
        with self._lock:
            existing = self._conn.execute(
                "SELECT success_count, avg_latency_ms FROM procedures WHERE procedure_id = ?",
                (procedure_id,)
            ).fetchone()
            if existing:
                count = existing[0] + 1
                avg_lat = (existing[1] * existing[0] + latency_ms) / count
                self._conn.execute(
                    "UPDATE procedures SET success_count = ?, avg_latency_ms = ?, "
                    "updated_at = ?, quality_grade = ? WHERE procedure_id = ?",
                    (count, avg_lat, now, quality_grade, procedure_id)
                )
            else:
                self._conn.execute(
                    "INSERT INTO procedures (procedure_id, intent, capability_chain, "
                    "quality_grade, avg_latency_ms, profile, source_case_id, "
                    "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (procedure_id, intent, chain_json, quality_grade,
                     latency_ms, profile, source_case_id, now, now)
                )
            self._conn.commit()

    def store_anti_pattern(self, pattern_id: str, intent: str,
                           capability_chain: List[str],
                           failure_reason: str = "",
                           profile: str = "") -> None:
        """Store or update an anti-pattern."""
        chain_json = json.dumps(capability_chain)
        with self._lock:
            existing = self._conn.execute(
                "SELECT occurrence_count FROM anti_patterns WHERE pattern_id = ?",
                (pattern_id,)
            ).fetchone()
            if existing:
                self._conn.execute(
                    "UPDATE anti_patterns SET occurrence_count = ? WHERE pattern_id = ?",
                    (existing[0] + 1, pattern_id)
                )
            else:
                self._conn.execute(
                    "INSERT INTO anti_patterns (pattern_id, intent, capability_chain, "
                    "failure_reason, profile, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (pattern_id, intent, chain_json, failure_reason,
                     profile, time.time())
                )
            self._conn.commit()

    def get_procedure(self, intent: str, profile: str = "") -> Optional[Dict[str, Any]]:
        """Get the best procedure for an intent."""
        query = ("SELECT procedure_id, capability_chain, quality_grade, "
                 "success_count, avg_latency_ms FROM procedures "
                 "WHERE intent = ?")
        params: list = [intent]
        if profile:
            query += " AND (profile = ? OR profile = '')"
            params.append(profile)
        query += " ORDER BY success_count DESC LIMIT 1"
        row = self._conn.execute(query, params).fetchone()
        if not row:
            return None
        return {
            "procedure_id": row[0],
            "capability_chain": json.loads(row[1]),
            "quality_grade": row[2],
            "success_count": row[3],
            "avg_latency_ms": row[4],
        }

    def is_anti_pattern(self, intent: str, capability_chain: List[str]) -> bool:
        """Check if a capability chain is a known anti-pattern."""
        chain_json = json.dumps(capability_chain)
        row = self._conn.execute(
            "SELECT 1 FROM anti_patterns WHERE intent = ? AND capability_chain = ?",
            (intent, chain_json)
        ).fetchone()
        return row is not None

    def list_procedures(self, limit: int = 50) -> List[Dict[str, Any]]:
        """List top procedures by success count."""
        rows = self._conn.execute(
            "SELECT procedure_id, intent, capability_chain, quality_grade, "
            "success_count, avg_latency_ms FROM procedures "
            "ORDER BY success_count DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [{
            "procedure_id": r[0], "intent": r[1],
            "capability_chain": json.loads(r[2]),
            "quality_grade": r[3], "success_count": r[4],
            "avg_latency_ms": r[5],
        } for r in rows]

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
        row = self._conn.execute("SELECT COUNT(*) FROM procedures").fetchone()
        procs = row[0] if row else 0
        row = self._conn.execute("SELECT COUNT(*) FROM anti_patterns").fetchone()
        antis = row[0] if row else 0
        return {"procedures": procs, "anti_patterns": antis, "db_path": self._db_path}
