#!/usr/bin/env python3
"""
Phase 1 Migration: Backfill canonical_id in all SQLite tables.

Reads alias_registry.yaml and updates canonical_id for every row that has
an agent_id / validator_id matching a known legacy ID.

Usage:
    python tools/migrations/backfill_canonical_ids.py [--dry-run]

Idempotent: safe to run multiple times.
"""

import argparse
import logging
import os
import sqlite3
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from waggledance.core.capabilities.aliasing import AliasRegistry

log = logging.getLogger("migration.backfill_canonical")

# Tables to backfill: (db_path, table, agent_id_column, canonical_column)
TARGETS = [
    ("data/audit_log.db", "audit", "agent_id", "canonical_id"),
    ("data/audit_log.db", "trust_signals", "agent_id", "canonical_id"),
    ("data/audit_log.db", "validations", "validator_id", "canonical_id"),
]

# Hexagonal stack tables (may not exist yet)
OPTIONAL_TARGETS = [
    ("data/trust_store.db", "agent_trust", "agent_id", "canonical_id"),
    ("shared_memory.db", "agents", "id", "canonical_id"),
    ("shared_memory.db", "memories", "agent_id", "canonical_id"),
    ("shared_memory.db", "events", "agent_id", "canonical_id"),
    ("shared_memory.db", "tasks", "agent_id", "canonical_id"),
]


def _ensure_canonical_column(conn: sqlite3.Connection, table: str, col: str = "canonical_id"):
    """Add canonical_id column if it doesn't exist."""
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if col not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
        conn.commit()
        log.info("  Added column %s.%s", table, col)


def backfill_table(
    conn: sqlite3.Connection,
    table: str,
    agent_col: str,
    canonical_col: str,
    lookup: dict,
    dry_run: bool = False,
) -> int:
    """Backfill canonical_id for one table. Returns count of updated rows."""
    # Get distinct agent IDs that have no canonical_id set
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
            log.info("  %s.%s: %s → %s (%d rows)", table, agent_col, agent_id, canonical, count)
        else:
            log.warning("  %s.%s: unknown agent_id %r (no canonical mapping)", table, agent_col, agent_id)

    if not dry_run:
        conn.commit()
    return updated


def run(dry_run: bool = False) -> dict:
    """Run the full backfill. Returns summary stats."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    registry = AliasRegistry.from_yaml("configs/alias_registry.yaml")
    lookup = registry.build_legacy_to_canonical_map()
    log.info("Loaded alias registry: %d agents, %d lookup entries", len(registry), len(lookup))

    stats = {}
    mode = "[DRY RUN] " if dry_run else ""

    for db_path, table, agent_col, canonical_col in TARGETS + OPTIONAL_TARGETS:
        if not Path(db_path).exists():
            log.info("%sSkipping %s (database not found: %s)", mode, table, db_path)
            continue

        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=WAL")

        # Check if table exists
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        if not exists:
            log.info("%sSkipping %s.%s (table not found)", mode, db_path, table)
            conn.close()
            continue

        log.info("%sBackfilling %s.%s ...", mode, db_path, table)
        _ensure_canonical_column(conn, table, canonical_col)
        count = backfill_table(conn, table, agent_col, canonical_col, lookup, dry_run)
        stats[f"{db_path}:{table}"] = count
        conn.close()

    log.info("\n=== Summary ===")
    total = 0
    for key, count in stats.items():
        log.info("  %s: %d rows", key, count)
        total += count
    log.info("  Total: %d rows %s", total, "(would be updated)" if dry_run else "updated")
    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill canonical_id in SQLite tables")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be updated without writing")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
