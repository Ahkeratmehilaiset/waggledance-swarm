"""SQLite persistence for CaseTrajectory — the primary learning unit.

Stores complete case trajectories with indexed columns for efficient
querying by quality grade, intent, profile, and time range.
Used by Night Learning v2 for case retrieval and by the runtime
for audit trail and restart continuity.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class SQLiteCaseStore:
    """Persistent store for CaseTrajectory objects.

    Stores the full case trajectory as JSON with indexed summary columns
    for efficient querying without deserializing the full payload.
    """

    def __init__(self, db_path: str = "data/case_store.db"):
        self._db_path = db_path
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._create_tables()

    def _create_tables(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS case_trajectories (
                trajectory_id      TEXT PRIMARY KEY,
                intent             TEXT NOT NULL DEFAULT '',
                goal_type          TEXT NOT NULL DEFAULT '',
                quality_grade      TEXT NOT NULL DEFAULT 'bronze',
                profile            TEXT NOT NULL DEFAULT '',
                capability_chain   TEXT NOT NULL DEFAULT '[]',
                verifier_passed    INTEGER NOT NULL DEFAULT 0,
                verifier_confidence REAL NOT NULL DEFAULT 0.0,
                snapshot_before_id TEXT NOT NULL DEFAULT '',
                snapshot_after_id  TEXT NOT NULL DEFAULT '',
                elapsed_ms         REAL NOT NULL DEFAULT 0.0,
                data               TEXT NOT NULL,
                created_at         REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_cases_grade
                ON case_trajectories(quality_grade);
            CREATE INDEX IF NOT EXISTS idx_cases_intent
                ON case_trajectories(intent);
            CREATE INDEX IF NOT EXISTS idx_cases_profile
                ON case_trajectories(profile);
            CREATE INDEX IF NOT EXISTS idx_cases_time
                ON case_trajectories(created_at);

            CREATE TABLE IF NOT EXISTS learning_watermark (
                id          INTEGER PRIMARY KEY CHECK (id = 1),
                processed_before REAL NOT NULL DEFAULT 0.0,
                updated_at  REAL NOT NULL DEFAULT 0.0
            );
        """)
        self._conn.commit()

    def save_case(self, case_dict: Dict[str, Any],
                  intent: str = "",
                  elapsed_ms: float = 0.0) -> str:
        """Persist a case trajectory.

        Args:
            case_dict: CaseTrajectory.to_dict() output
            intent: classified intent (for indexing)
            elapsed_ms: total query latency

        Returns:
            trajectory_id
        """
        tid = case_dict.get("trajectory_id", "")
        goal = case_dict.get("goal") or {}
        quality_grade = case_dict.get("quality_grade", "bronze")
        profile = case_dict.get("profile", "")
        goal_type = goal.get("type", "")

        # Extract capability chain for indexing
        caps = case_dict.get("selected_capabilities") or []
        chain = [c.get("capability_id", "") for c in caps if isinstance(c, dict)]

        # Extract verifier info
        vr = case_dict.get("verifier_result") or {}
        v_passed = 1 if vr.get("passed", False) else 0
        v_conf = vr.get("confidence", 0.0)

        # Snapshot IDs
        sb = case_dict.get("world_snapshot_before") or {}
        sa = case_dict.get("world_snapshot_after") or {}
        sb_id = sb.get("snapshot_id", "")
        sa_id = sa.get("snapshot_id", "")

        now = time.time()
        data_json = json.dumps(case_dict)

        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO case_trajectories "
                "(trajectory_id, intent, goal_type, quality_grade, profile, "
                "capability_chain, verifier_passed, verifier_confidence, "
                "snapshot_before_id, snapshot_after_id, elapsed_ms, data, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (tid, intent, goal_type, quality_grade, profile,
                 json.dumps(chain), v_passed, v_conf,
                 sb_id, sa_id, elapsed_ms, data_json, now)
            )
            self._conn.commit()

        return tid

    def get_case(self, trajectory_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a case trajectory by ID."""
        row = self._conn.execute(
            "SELECT data, created_at FROM case_trajectories WHERE trajectory_id = ?",
            (trajectory_id,)
        ).fetchone()
        if not row:
            return None
        result = json.loads(row[0])
        result["_stored_at"] = row[1]
        return result

    def list_cases(self, limit: int = 50,
                   quality_grade: Optional[str] = None,
                   intent: Optional[str] = None,
                   profile: Optional[str] = None,
                   since: Optional[float] = None) -> List[Dict[str, Any]]:
        """List case trajectories with optional filters.

        Returns summary dicts (without full data payload) for efficiency.
        """
        query = ("SELECT trajectory_id, intent, goal_type, quality_grade, "
                 "profile, capability_chain, verifier_passed, "
                 "verifier_confidence, elapsed_ms, created_at "
                 "FROM case_trajectories WHERE 1=1")
        params: list = []
        if quality_grade:
            query += " AND quality_grade = ?"
            params.append(quality_grade)
        if intent:
            query += " AND intent = ?"
            params.append(intent)
        if profile:
            query += " AND profile = ?"
            params.append(profile)
        if since is not None:
            query += " AND created_at >= ?"
            params.append(since)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        rows = self._conn.execute(query, params).fetchall()
        return [{
            "trajectory_id": r[0],
            "intent": r[1],
            "goal_type": r[2],
            "quality_grade": r[3],
            "profile": r[4],
            "capability_chain": json.loads(r[5]),
            "verifier_passed": bool(r[6]),
            "verifier_confidence": r[7],
            "elapsed_ms": r[8],
            "created_at": r[9],
        } for r in rows]

    def list_full(self, limit: int = 50,
                  quality_grade: Optional[str] = None) -> List[Dict[str, Any]]:
        """List case trajectories with full data payload."""
        query = "SELECT data FROM case_trajectories WHERE 1=1"
        params: list = []
        if quality_grade:
            query += " AND quality_grade = ?"
            params.append(quality_grade)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        rows = self._conn.execute(query, params).fetchall()
        return [json.loads(r[0]) for r in rows]

    def count(self, quality_grade: Optional[str] = None) -> int:
        """Count case trajectories, optionally filtered by grade."""
        if quality_grade:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM case_trajectories WHERE quality_grade = ?",
                (quality_grade,)
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM case_trajectories"
            ).fetchone()
        return row[0]

    def delete_old(self, max_age_days: float = 90) -> int:
        """Delete case trajectories older than max_age_days.

        Returns number of deleted rows.
        """
        cutoff = time.time() - (max_age_days * 86400)
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM case_trajectories WHERE created_at < ?",
                (cutoff,)
            )
            self._conn.commit()
            return cursor.rowcount

    def grade_distribution(self) -> Dict[str, int]:
        """Get count of cases per quality grade."""
        rows = self._conn.execute(
            "SELECT quality_grade, COUNT(*) FROM case_trajectories GROUP BY quality_grade"
        ).fetchall()
        return {r[0]: r[1] for r in rows}

    # ── Learning watermark ────────────────────────────────

    def get_watermark(self) -> float:
        """Get the timestamp up to which cases have been processed."""
        row = self._conn.execute(
            "SELECT processed_before FROM learning_watermark WHERE id = 1"
        ).fetchone()
        return row[0] if row else 0.0

    def set_watermark(self, ts: float) -> None:
        """Update the learning watermark to mark cases as processed."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO learning_watermark (id, processed_before, updated_at) "
                "VALUES (1, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET processed_before = ?, updated_at = ?",
                (ts, time.time(), ts, time.time()),
            )
            self._conn.commit()

    def fetch_pending(self, limit: int = 5000) -> List[Dict[str, Any]]:
        """Fetch cases created after the learning watermark.

        Returns full case dicts (JSON deserialized) for cases that
        have not yet been processed by the night learning pipeline.
        """
        watermark = self.get_watermark()
        rows = self._conn.execute(
            "SELECT data, created_at FROM case_trajectories "
            "WHERE created_at > ? ORDER BY created_at ASC LIMIT ?",
            (watermark, limit),
        ).fetchall()
        results = []
        for r in rows:
            d = json.loads(r[0])
            d["_stored_at"] = r[1]
            results.append(d)
        return results

    def pending_count(self) -> int:
        """Count cases created after the learning watermark."""
        watermark = self.get_watermark()
        row = self._conn.execute(
            "SELECT COUNT(*) FROM case_trajectories WHERE created_at > ?",
            (watermark,),
        ).fetchone()
        return row[0] if row else 0

    def close(self) -> None:
        """Close the database connection."""
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass

    def __del__(self):
        # Guard against partially-initialized instances (e.g. __init__ failed
        # before _lock was set, or attributes cleared during interpreter shutdown)
        if not hasattr(self, '_lock'):
            # Best-effort close without lock
            conn = getattr(self, '_conn', None)
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            return
        self.close()

    def stats(self) -> Dict[str, Any]:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM case_trajectories"
        ).fetchone()
        total = row[0] if row else 0
        grades = self.grade_distribution()
        gold = grades.get("gold", 0)
        return {
            "total": total,
            "grades": grades,
            "gold_rate": gold / total if total > 0 else 0.0,
            "db_path": self._db_path,
        }
