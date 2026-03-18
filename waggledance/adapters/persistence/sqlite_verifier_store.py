"""SQLite persistence for VerifierResult — audit trail for verification outcomes.

Stores verifier results linked to action_id and capability_id for
debugging, analysis, and trust score computation. Supports querying
by pass/fail status, capability, and time range.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class SQLiteVerifierStore:
    """Persistent store for VerifierResult audit trail.

    Each verification outcome is stored with indexed columns for
    efficient querying by action, capability, and pass/fail status.
    """

    def __init__(self, db_path: str = "data/verifier_store.db"):
        self._db_path = db_path
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._create_tables()

    def _create_tables(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS verifier_results (
                result_id           TEXT PRIMARY KEY,
                action_id           TEXT NOT NULL DEFAULT '',
                capability_id       TEXT NOT NULL DEFAULT '',
                trajectory_id       TEXT NOT NULL DEFAULT '',
                passed              INTEGER NOT NULL DEFAULT 0,
                confidence          REAL NOT NULL DEFAULT 0.0,
                residual_improvement REAL NOT NULL DEFAULT 0.0,
                conflict            INTEGER NOT NULL DEFAULT 0,
                hallucination       INTEGER NOT NULL DEFAULT 0,
                data                TEXT NOT NULL,
                created_at          REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_verifier_action
                ON verifier_results(action_id);
            CREATE INDEX IF NOT EXISTS idx_verifier_capability
                ON verifier_results(capability_id);
            CREATE INDEX IF NOT EXISTS idx_verifier_trajectory
                ON verifier_results(trajectory_id);
            CREATE INDEX IF NOT EXISTS idx_verifier_passed
                ON verifier_results(passed);
            CREATE INDEX IF NOT EXISTS idx_verifier_time
                ON verifier_results(created_at);
        """)
        self._conn.commit()

    def save_result(self, result_dict: Dict[str, Any],
                    action_id: str = "",
                    capability_id: str = "",
                    trajectory_id: str = "") -> str:
        """Persist a verifier result.

        Args:
            result_dict: VerifierResult.to_dict() output
            action_id: the action that was verified
            capability_id: the capability that was invoked
            trajectory_id: the case trajectory this belongs to

        Returns:
            result_id (auto-generated UUID)
        """
        result_id = uuid.uuid4().hex[:12]
        passed = 1 if result_dict.get("passed", False) else 0
        confidence = result_dict.get("confidence", 0.0)
        residual = result_dict.get("residual_improvement", 0.0)
        conflict = 1 if result_dict.get("conflict", False) else 0
        hallucination = 1 if result_dict.get("hallucination", False) else 0
        now = time.time()

        with self._lock:
            self._conn.execute(
                "INSERT INTO verifier_results "
                "(result_id, action_id, capability_id, trajectory_id, "
                "passed, confidence, residual_improvement, conflict, "
                "hallucination, data, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (result_id, action_id, capability_id, trajectory_id,
                 passed, confidence, residual, conflict, hallucination,
                 json.dumps(result_dict), now)
            )
            self._conn.commit()

        return result_id

    def get_result(self, result_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a verifier result by ID."""
        row = self._conn.execute(
            "SELECT data, action_id, capability_id, trajectory_id, created_at "
            "FROM verifier_results WHERE result_id = ?",
            (result_id,)
        ).fetchone()
        if not row:
            return None
        result = json.loads(row[0])
        result["result_id"] = result_id
        result["action_id"] = row[1]
        result["capability_id"] = row[2]
        result["trajectory_id"] = row[3]
        result["_stored_at"] = row[4]
        return result

    def get_by_action(self, action_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve verifier result for a specific action."""
        row = self._conn.execute(
            "SELECT result_id, data, capability_id, trajectory_id, created_at "
            "FROM verifier_results WHERE action_id = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (action_id,)
        ).fetchone()
        if not row:
            return None
        result = json.loads(row[1])
        result["result_id"] = row[0]
        result["action_id"] = action_id
        result["capability_id"] = row[2]
        result["trajectory_id"] = row[3]
        result["_stored_at"] = row[4]
        return result

    def get_by_trajectory(self, trajectory_id: str) -> List[Dict[str, Any]]:
        """Retrieve all verifier results for a case trajectory."""
        rows = self._conn.execute(
            "SELECT result_id, data, action_id, capability_id, created_at "
            "FROM verifier_results WHERE trajectory_id = ? "
            "ORDER BY created_at ASC",
            (trajectory_id,)
        ).fetchall()
        results = []
        for r in rows:
            result = json.loads(r[1])
            result["result_id"] = r[0]
            result["action_id"] = r[2]
            result["capability_id"] = r[3]
            result["_stored_at"] = r[4]
            results.append(result)
        return results

    def list_results(self, limit: int = 50,
                     passed: Optional[bool] = None,
                     capability_id: Optional[str] = None,
                     since: Optional[float] = None) -> List[Dict[str, Any]]:
        """List verifier results with optional filters.

        Returns summary dicts for efficiency.
        """
        query = ("SELECT result_id, action_id, capability_id, trajectory_id, "
                 "passed, confidence, residual_improvement, conflict, "
                 "hallucination, created_at "
                 "FROM verifier_results WHERE 1=1")
        params: list = []
        if passed is not None:
            query += " AND passed = ?"
            params.append(1 if passed else 0)
        if capability_id:
            query += " AND capability_id = ?"
            params.append(capability_id)
        if since is not None:
            query += " AND created_at >= ?"
            params.append(since)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        rows = self._conn.execute(query, params).fetchall()
        return [{
            "result_id": r[0],
            "action_id": r[1],
            "capability_id": r[2],
            "trajectory_id": r[3],
            "passed": bool(r[4]),
            "confidence": r[5],
            "residual_improvement": r[6],
            "conflict": bool(r[7]),
            "hallucination": bool(r[8]),
            "created_at": r[9],
        } for r in rows]

    def pass_rate(self, capability_id: Optional[str] = None) -> float:
        """Compute pass rate, optionally per capability."""
        query = "SELECT COUNT(*), SUM(passed) FROM verifier_results"
        params: list = []
        if capability_id:
            query += " WHERE capability_id = ?"
            params.append(capability_id)
        row = self._conn.execute(query, params).fetchone()
        total, passed = row[0], row[1] or 0
        return passed / total if total > 0 else 0.0

    def delete_old(self, max_age_days: float = 90) -> int:
        """Delete verifier results older than max_age_days."""
        cutoff = time.time() - (max_age_days * 86400)
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM verifier_results WHERE created_at < ?",
                (cutoff,)
            )
            self._conn.commit()
            return cursor.rowcount

    def close(self) -> None:
        """Close the database connection."""
        with self._lock:
            self._conn.close()

    def stats(self) -> Dict[str, Any]:
        total = self._conn.execute(
            "SELECT COUNT(*) FROM verifier_results"
        ).fetchone()[0]
        passed = self._conn.execute(
            "SELECT COUNT(*) FROM verifier_results WHERE passed = 1"
        ).fetchone()[0]
        conflicts = self._conn.execute(
            "SELECT COUNT(*) FROM verifier_results WHERE conflict = 1"
        ).fetchone()[0]
        hallucinations = self._conn.execute(
            "SELECT COUNT(*) FROM verifier_results WHERE hallucination = 1"
        ).fetchone()[0]
        return {
            "total": total,
            "passed": passed,
            "failed": total - passed,
            "pass_rate": passed / total if total > 0 else 0.0,
            "conflicts": conflicts,
            "hallucinations": hallucinations,
            "db_path": self._db_path,
        }
