# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
# See LICENSE-BUSL.txt and LICENSE-CORE.md
"""
Append-only SQLite audit trail for memory operations.
Layer 1 of MAGMA memory architecture.
"""

import hashlib
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import List, Optional

log = logging.getLogger("waggledance.audit")


class AuditLog:
    """Immutable audit log — append only, no update/delete."""

    def __init__(self, db_path: str = "data/audit_log.db"):
        self._write_lock = threading.Lock()
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._create_tables()

    def _create_tables(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS audit (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp   REAL    NOT NULL,
                action      TEXT    NOT NULL,
                doc_id      TEXT    NOT NULL,
                collection  TEXT    NOT NULL DEFAULT 'waggle_memory',
                layer       TEXT    NOT NULL DEFAULT 'working',
                agent_id    TEXT    NOT NULL DEFAULT '',
                session_id  TEXT    NOT NULL DEFAULT '',
                spawn_chain TEXT    NOT NULL DEFAULT '',
                content_hash TEXT   NOT NULL DEFAULT '',
                metadata    TEXT    NOT NULL DEFAULT '{}',
                details     TEXT    NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_audit_agent    ON audit(agent_id);
            CREATE INDEX IF NOT EXISTS idx_audit_session  ON audit(session_id);
            CREATE INDEX IF NOT EXISTS idx_audit_ts       ON audit(timestamp);
            CREATE INDEX IF NOT EXISTS idx_audit_doc      ON audit(doc_id);
            CREATE INDEX IF NOT EXISTS idx_audit_hash     ON audit(content_hash);
        """)
        # Phase 1 autonomy: add canonical_id column if missing
        self._maybe_add_canonical_id()
        self._conn.commit()

    def _maybe_add_canonical_id(self):
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(audit)").fetchall()}
        if "canonical_id" not in cols:
            self._conn.execute(
                "ALTER TABLE audit ADD COLUMN canonical_id TEXT NOT NULL DEFAULT ''"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_canonical ON audit(canonical_id)"
            )

    def record(self, action: str, doc_id: str, *,
               collection: str = "waggle_memory",
               layer: str = "working",
               agent_id: str = "",
               canonical_id: str = "",
               session_id: str = "",
               spawn_chain: str = "",
               content_hash: str = "",
               metadata: str = "{}",
               details: str = "") -> int:
        try:
            from core.disk_guard import check_disk_space
            check_disk_space(str(Path(self.db_path).parent), label="AuditLog")
        except (ImportError, OSError):
            pass
        with self._write_lock:
            cur = self._conn.execute(
                "INSERT INTO audit (timestamp, action, doc_id, collection, layer, "
                "agent_id, canonical_id, session_id, spawn_chain, content_hash, metadata, details) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (time.time(), action, doc_id, collection, layer,
                 agent_id, canonical_id, session_id, spawn_chain, content_hash, metadata, details)
            )
            self._conn.commit()
            return cur.lastrowid

    def query_by_agent(self, agent_id: str, session_id: Optional[str] = None) -> List[dict]:
        if session_id:
            rows = self._conn.execute(
                "SELECT * FROM audit WHERE agent_id=? AND session_id=? ORDER BY timestamp",
                (agent_id, session_id)
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM audit WHERE agent_id=? ORDER BY timestamp",
                (agent_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def query_by_doc(self, doc_id: str) -> List[dict]:
        rows = self._conn.execute(
            "SELECT * FROM audit WHERE doc_id=? ORDER BY timestamp",
            (doc_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def query_spawn_tree(self, root_agent_id: str) -> List[dict]:
        # Escape LIKE wildcards in user input
        escaped = root_agent_id.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        rows = self._conn.execute(
            "SELECT * FROM audit WHERE agent_id=? OR spawn_chain LIKE ? ESCAPE '\\' ORDER BY timestamp",
            (root_agent_id, f"%{escaped}%")
        ).fetchall()
        return [dict(r) for r in rows]

    def query_by_time_range(self, start_ts: float, end_ts: float, *,
                            agent_id: Optional[str] = None,
                            layer: Optional[str] = None) -> List[dict]:
        sql = "SELECT * FROM audit WHERE timestamp >= ? AND timestamp <= ?"
        params: list = [start_ts, end_ts]
        if agent_id is not None:
            sql += " AND agent_id = ?"
            params.append(agent_id)
        if layer is not None:
            sql += " AND layer = ?"
            params.append(layer)
        sql += " ORDER BY timestamp"
        return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def query_by_hash(self, content_hash: str) -> List[dict]:
        rows = self._conn.execute(
            "SELECT * FROM audit WHERE content_hash=? ORDER BY timestamp",
            (content_hash,)
        ).fetchall()
        return [dict(r) for r in rows]

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM audit").fetchone()[0]

    def close(self):
        self._conn.close()

    @staticmethod
    def content_hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

    # ── v2.0: Autonomy black box ─────────────────────────────────

    def record_autonomy_event(self, event_type: str, goal_id: str = "",
                               action_id: str = "", capability: str = "",
                               quality_path: str = "", details: str = "") -> None:
        """Record an autonomy runtime event for black-box audit trail.

        Extends the audit log to capture goal/mission/action/policy events
        from the autonomy runtime.
        """
        self.record(
            action=f"autonomy:{event_type}",
            doc_id=goal_id or action_id,
            agent_id=capability or "autonomy_runtime",
            content_hash=self.content_hash(details) if details else "",
            details=f"[{quality_path}] {details[:500]}" if details else "",
        )
