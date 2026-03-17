"""
MAGMA Layer 4: Provenance tracking for fact origin, validation, and consensus.
Extends the audit_log SQLite DB with validation and consensus tables.
"""

import logging
import time
from typing import Dict, List, Optional

log = logging.getLogger("waggledance.provenance")


class ProvenanceTracker:
    """Tracks fact provenance: origin, validations, and consensus records."""

    def __init__(self, audit_log):
        self._audit = audit_log
        self._conn = audit_log._conn
        self._create_tables()

    def _create_tables(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS validations (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                fact_id         TEXT    NOT NULL,
                validator_id    TEXT    NOT NULL,
                verdict         TEXT    NOT NULL,
                timestamp       REAL    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_val_fact ON validations(fact_id);
            CREATE INDEX IF NOT EXISTS idx_val_validator ON validations(validator_id);

            CREATE TABLE IF NOT EXISTS consensus (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                fact_id             TEXT    NOT NULL,
                participating_agents TEXT   NOT NULL,
                synthesis_text      TEXT    NOT NULL,
                timestamp           REAL    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_cons_fact ON consensus(fact_id);
        """)
        # Phase 1 autonomy: add canonical_id columns if missing
        for table, id_col in [("validations", "validator_id"), ("consensus", "participating_agents")]:
            cols = {r[1] for r in self._conn.execute(f"PRAGMA table_info({table})").fetchall()}
            if "canonical_id" not in cols and table == "validations":
                self._conn.execute(
                    "ALTER TABLE validations ADD COLUMN canonical_id TEXT NOT NULL DEFAULT ''"
                )
        self._conn.commit()

    def get_origin(self, fact_id: str) -> Optional[dict]:
        rows = self._audit.query_by_doc(fact_id)
        if not rows:
            return None
        first = rows[0]
        return {
            "fact_id": fact_id,
            "agent_id": first.get("agent_id", ""),
            "source_type": first.get("action", ""),
            "timestamp": first.get("timestamp", 0),
            "content_hash": first.get("content_hash", ""),
        }

    def record_validation(self, fact_id: str, validator_agent_id: str,
                          verdict: str):
        if verdict not in ("agree", "disagree", "neutral"):
            raise ValueError(f"Invalid verdict: {verdict}")
        self._conn.execute(
            "INSERT INTO validations (fact_id, validator_id, verdict, timestamp) "
            "VALUES (?, ?, ?, ?)",
            (fact_id, validator_agent_id, verdict, time.time())
        )
        self._conn.commit()

    def get_validations(self, fact_id: str) -> List[dict]:
        rows = self._conn.execute(
            "SELECT * FROM validations WHERE fact_id=? ORDER BY timestamp",
            (fact_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def record_consensus(self, fact_id: str, participating_agents: List[str],
                         synthesis_text: str):
        agents_str = ",".join(participating_agents)
        self._conn.execute(
            "INSERT INTO consensus (fact_id, participating_agents, "
            "synthesis_text, timestamp) VALUES (?, ?, ?, ?)",
            (fact_id, agents_str, synthesis_text, time.time())
        )
        self._conn.commit()

    def get_consensus(self, fact_id: str) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT * FROM consensus WHERE fact_id=? ORDER BY timestamp DESC LIMIT 1",
            (fact_id,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["participating_agents"] = d["participating_agents"].split(",")
        return d

    def get_agent_contributions(self, agent_id: str) -> dict:
        originated = self._audit.query_by_agent(agent_id)
        validated = self._conn.execute(
            "SELECT * FROM validations WHERE validator_id=? ORDER BY timestamp",
            (agent_id,)
        ).fetchall()
        return {
            "originated": originated,
            "validated": [dict(r) for r in validated],
        }

    def get_provenance_chain(self, fact_id: str) -> dict:
        return {
            "origin": self.get_origin(fact_id),
            "validations": self.get_validations(fact_id),
            "consensus": self.get_consensus(fact_id),
        }

    def get_validated_facts(self, min_validators: int = 2) -> List[dict]:
        rows = self._conn.execute(
            "SELECT fact_id, COUNT(*) as cnt FROM validations "
            "WHERE verdict='agree' GROUP BY fact_id HAVING cnt >= ?",
            (min_validators,)
        ).fetchall()
        return [{"fact_id": r[0], "agree_count": r[1]} for r in rows]
