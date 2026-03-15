"""SQLite-backed trust store — persistent agent reputation tracking.

Implements TrustStorePort using aiosqlite with WAL mode.
New agents get default composite_score=0.5.
"""

import time
from pathlib import Path

try:
    import aiosqlite
except ImportError:
    aiosqlite = None  # type: ignore[assignment]

from waggledance.core.domain.trust_score import AgentTrust, TrustSignals


_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_trust (
    agent_id          TEXT PRIMARY KEY,
    composite_score   REAL NOT NULL DEFAULT 0.5,
    hallucination_rate REAL NOT NULL DEFAULT 0.0,
    validation_rate   REAL NOT NULL DEFAULT 0.0,
    consensus_agreement REAL NOT NULL DEFAULT 0.0,
    correction_rate   REAL NOT NULL DEFAULT 0.0,
    fact_production_rate REAL NOT NULL DEFAULT 0.0,
    freshness_score   REAL NOT NULL DEFAULT 0.0,
    updated_at        REAL NOT NULL
);
"""

_UPSERT = """
INSERT INTO agent_trust
    (agent_id, composite_score,
     hallucination_rate, validation_rate, consensus_agreement,
     correction_rate, fact_production_rate, freshness_score, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(agent_id) DO UPDATE SET
    composite_score   = excluded.composite_score,
    hallucination_rate = excluded.hallucination_rate,
    validation_rate   = excluded.validation_rate,
    consensus_agreement = excluded.consensus_agreement,
    correction_rate   = excluded.correction_rate,
    fact_production_rate = excluded.fact_production_rate,
    freshness_score   = excluded.freshness_score,
    updated_at        = excluded.updated_at;
"""


class SQLiteTrustStore:
    """Persistent trust store backed by SQLite (WAL mode)."""

    def __init__(self, db_path: str = "data/trust_store.db") -> None:
        self._db_path = db_path
        self._db: "aiosqlite.Connection | None" = None

    async def _ensure_db(self) -> "aiosqlite.Connection":
        if self._db is not None:
            return self._db
        if aiosqlite is None:
            raise RuntimeError("aiosqlite is required: pip install aiosqlite")
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._db_path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.executescript(_SCHEMA)
        await self._db.commit()
        return self._db

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def get_trust(self, agent_id: str) -> AgentTrust | None:
        db = await self._ensure_db()
        async with db.execute(
            "SELECT * FROM agent_trust WHERE agent_id = ?", (agent_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_trust(row)

    async def update_trust(
        self, agent_id: str, signals: TrustSignals
    ) -> AgentTrust:
        db = await self._ensure_db()
        composite = self._compute_composite(signals)
        now = time.time()
        await db.execute(
            _UPSERT,
            (
                agent_id,
                composite,
                signals.hallucination_rate,
                signals.validation_rate,
                signals.consensus_agreement,
                signals.correction_rate,
                signals.fact_production_rate,
                signals.freshness_score,
                now,
            ),
        )
        await db.commit()
        return AgentTrust(
            agent_id=agent_id,
            composite_score=composite,
            signals=signals,
            updated_at=now,
        )

    async def get_ranking(self, limit: int = 20) -> list[AgentTrust]:
        db = await self._ensure_db()
        async with db.execute(
            "SELECT * FROM agent_trust ORDER BY composite_score DESC LIMIT ?",
            (limit,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [self._row_to_trust(r) for r in rows]

    @staticmethod
    def _compute_composite(s: TrustSignals) -> float:
        """Same weights as InMemoryTrustStore."""
        composite = (
            (1.0 - s.hallucination_rate) * 0.25
            + s.validation_rate * 0.20
            + s.consensus_agreement * 0.20
            + (1.0 - s.correction_rate) * 0.15
            + s.fact_production_rate * 0.10
            + s.freshness_score * 0.10
        )
        return max(0.0, min(1.0, composite))

    @staticmethod
    def _row_to_trust(row: tuple) -> AgentTrust:
        return AgentTrust(
            agent_id=row[0],
            composite_score=row[1],
            signals=TrustSignals(
                hallucination_rate=row[2],
                validation_rate=row[3],
                consensus_agreement=row[4],
                correction_rate=row[5],
                fact_production_rate=row[6],
                freshness_score=row[7],
            ),
            updated_at=row[8],
        )
