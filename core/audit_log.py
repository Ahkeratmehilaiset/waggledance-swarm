"""
Append-only SQLite audit trail for memory operations.
Layer 1 of MAGMA memory architecture.
"""

import hashlib
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("waggledance.audit")


class AuditLog:
    """Immutable audit log — append only, no update/delete."""

    def __init__(self, db_path: str = "data/audit_log.db"):
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
        """)
        self._conn.commit()

    def record(self, action: str, doc_id: str, *,
               collection: str = "waggle_memory",
               layer: str = "working",
               agent_id: str = "",
               session_id: str = "",
               spawn_chain: str = "",
               content_hash: str = "",
               metadata: str = "{}",
               details: str = "") -> int:
        cur = self._conn.execute(
            "INSERT INTO audit (timestamp, action, doc_id, collection, layer, "
            "agent_id, session_id, spawn_chain, content_hash, metadata, details) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (time.time(), action, doc_id, collection, layer,
             agent_id, session_id, spawn_chain, content_hash, metadata, details)
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
        rows = self._conn.execute(
            "SELECT * FROM audit WHERE agent_id=? OR spawn_chain LIKE ? ORDER BY timestamp",
            (root_agent_id, f"%{root_agent_id}%")
        ).fetchall()
        return [dict(r) for r in rows]

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM audit").fetchone()[0]

    def close(self):
        self._conn.close()

    @staticmethod
    def content_hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
