"""
Fixture-based tests for canonical ID backfill migration.

Tests use in-memory SQLite (no disk I/O) to verify backfill logic:
  - Populates canonical_id from alias registry
  - Idempotent (safe to run twice)
  - Unknown agents skipped gracefully
  - Dry-run mode does not write

Also tests the preflight report structure.
"""

from __future__ import annotations

import sqlite3

import pytest

from waggledance.core.capabilities.aliasing import AgentAlias, AliasRegistry

# ── Fixtures ──────────────────────────────────────────────────

FIXTURE_AGENTS = [
    AgentAlias(
        legacy_id="beekeeper",
        canonical="domain.apiary.beekeeper",
        aliases=("beekeeper", "tarhaaja"),
        profiles=("cottage",),
    ),
    AgentAlias(
        legacy_id="weatherbot",
        canonical="domain.weather.forecaster",
        aliases=("weatherbot", "saabot"),
        profiles=("cottage", "home"),
    ),
    AgentAlias(
        legacy_id="energybot",
        canonical="domain.energy.optimizer",
        aliases=("energybot", "energiabot"),
        profiles=("home", "factory"),
    ),
]


@pytest.fixture
def registry() -> AliasRegistry:
    return AliasRegistry(agents=FIXTURE_AGENTS)


@pytest.fixture
def lookup(registry: AliasRegistry) -> dict:
    return registry.build_legacy_to_canonical_map()


@pytest.fixture
def mem_db() -> sqlite3.Connection:
    """In-memory SQLite with a test table and sample rows."""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE trust_signals ("
        "  id INTEGER PRIMARY KEY,"
        "  agent_id TEXT NOT NULL,"
        "  canonical_id TEXT NOT NULL DEFAULT '',"
        "  score REAL"
        ")"
    )
    # Insert test data — mix of known and unknown agents
    rows = [
        (1, "beekeeper", "", 0.9),
        (2, "beekeeper", "", 0.85),
        (3, "weatherbot", "", 0.7),
        (4, "unknown_agent", "", 0.5),
        (5, "energybot", "", 0.8),
    ]
    conn.executemany(
        "INSERT INTO trust_signals (id, agent_id, canonical_id, score) VALUES (?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    return conn


def _backfill(conn, table, agent_col, canonical_col, lookup, dry_run=False):
    """Minimal backfill implementation matching tools/migrations/backfill_canonical_ids.py."""
    rows = conn.execute(
        f"SELECT DISTINCT {agent_col} FROM {table} "
        f"WHERE {agent_col} IS NOT NULL AND {agent_col} != '' "
        f"AND ({canonical_col} IS NULL OR {canonical_col} = '')"
    ).fetchall()

    updated = 0
    for (agent_id,) in rows:
        canonical = lookup.get(agent_id.lower())
        if canonical:
            if not dry_run:
                conn.execute(
                    f"UPDATE {table} SET {canonical_col} = ? WHERE {agent_col} = ?",
                    (canonical, agent_id),
                )
            count = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {agent_col} = ?",
                (agent_id,),
            ).fetchone()[0]
            updated += count

    if not dry_run:
        conn.commit()
    return updated


# ── Tests ─────────────────────────────────────────────────────

class TestBackfillPopulatesCanonicalId:
    def test_backfill_populates_canonical_id(self, mem_db, lookup):
        """Backfill sets canonical_id for known agents."""
        updated = _backfill(mem_db, "trust_signals", "agent_id", "canonical_id", lookup)

        # beekeeper (2 rows) + weatherbot (1) + energybot (1) = 4
        assert updated == 4

        # Verify specific mappings
        row = mem_db.execute(
            "SELECT canonical_id FROM trust_signals WHERE agent_id = 'beekeeper' LIMIT 1"
        ).fetchone()
        assert row[0] == "domain.apiary.beekeeper"

        row = mem_db.execute(
            "SELECT canonical_id FROM trust_signals WHERE agent_id = 'weatherbot' LIMIT 1"
        ).fetchone()
        assert row[0] == "domain.weather.forecaster"

        row = mem_db.execute(
            "SELECT canonical_id FROM trust_signals WHERE agent_id = 'energybot' LIMIT 1"
        ).fetchone()
        assert row[0] == "domain.energy.optimizer"


class TestBackfillIdempotent:
    def test_backfill_is_idempotent(self, mem_db, lookup):
        """Running backfill twice produces same result (no double-writes)."""
        _backfill(mem_db, "trust_signals", "agent_id", "canonical_id", lookup)
        # Second run — no rows should match (all already filled)
        updated = _backfill(mem_db, "trust_signals", "agent_id", "canonical_id", lookup)
        assert updated == 0

        # Values unchanged
        row = mem_db.execute(
            "SELECT canonical_id FROM trust_signals WHERE agent_id = 'beekeeper' LIMIT 1"
        ).fetchone()
        assert row[0] == "domain.apiary.beekeeper"


class TestBackfillUnknownAgentSkipped:
    def test_backfill_unknown_agent_skipped(self, mem_db, lookup):
        """Unknown agents are left with empty canonical_id."""
        _backfill(mem_db, "trust_signals", "agent_id", "canonical_id", lookup)

        row = mem_db.execute(
            "SELECT canonical_id FROM trust_signals WHERE agent_id = 'unknown_agent'"
        ).fetchone()
        assert row[0] == ""


class TestBackfillDryRunNoWrite:
    def test_backfill_dry_run_no_write(self, mem_db, lookup):
        """Dry-run mode reports counts but does not modify the database."""
        updated = _backfill(
            mem_db, "trust_signals", "agent_id", "canonical_id", lookup,
            dry_run=True,
        )
        # Should report rows that *would* be updated
        assert updated == 4

        # But no actual writes
        row = mem_db.execute(
            "SELECT canonical_id FROM trust_signals WHERE agent_id = 'beekeeper' LIMIT 1"
        ).fetchone()
        assert row[0] == ""


class TestPreflightReportStructure:
    def test_preflight_report_structure(self, registry):
        """Preflight report has expected top-level keys."""
        # Import the preflight module
        import importlib
        import sys
        sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2] / "tools"))

        from migration_preflight import run_preflight

        # Monkey-patch registry loading to use our fixture
        import tools.migration_preflight as mp
        original_from_yaml = AliasRegistry.from_yaml
        AliasRegistry.from_yaml = classmethod(lambda cls, path: registry)
        try:
            report = run_preflight(dry_run=True)
        finally:
            AliasRegistry.from_yaml = original_from_yaml

        assert "agent_count" in report
        assert "lookup_entries" in report
        assert "canonical_ids" in report
        assert "db_coverage" in report
        assert "chroma_coverage" in report
        assert report["mode"] == "dry_run"
        assert report["agent_count"] == len(FIXTURE_AGENTS)
