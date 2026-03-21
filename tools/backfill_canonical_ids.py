"""Backfill canonical_id columns in trust and audit SQLite tables.

Phase 1 migration step 2: Adds canonical_id column to trust_signals
and audit_log tables, then fills it from the alias_registry.

Usage:
    python tools/backfill_canonical_ids.py [--alias-db data/alias_registry.db]
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys


DEFAULT_ALIAS_DB = "data/alias_registry.db"
TRUST_DB = "data/trust_store.db"
AUDIT_DB = "data/audit_log.db"


def load_alias_map(alias_db: str) -> dict:
    """Load legacy_id → canonical mapping from alias registry."""
    if not os.path.exists(alias_db):
        print(f"WARNING: Alias DB not found: {alias_db}")
        print("Run alias_registry_builder.py first.")
        return {}
    conn = sqlite3.connect(alias_db)
    rows = conn.execute("SELECT legacy_id, canonical FROM alias_registry").fetchall()
    conn.close()
    return {row[0]: row[1] for row in rows}


def backfill_table(db_path: str, table: str, id_column: str,
                   alias_map: dict) -> dict:
    """Add canonical_id column and backfill from alias map.

    Returns stats dict.
    """
    if not os.path.exists(db_path):
        return {"db": db_path, "status": "not_found", "updated": 0}

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA busy_timeout=5000")

    # Check if column already exists
    columns = [row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if "canonical_id" not in columns:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN canonical_id TEXT DEFAULT ''")
            conn.commit()
            print(f"  Added canonical_id column to {table}")
        except Exception as exc:
            print(f"  WARNING: Could not add column to {table}: {exc}")
            conn.close()
            return {"db": db_path, "table": table, "status": "column_add_failed",
                    "error": str(exc)}

    # Backfill
    updated = 0
    try:
        rows = conn.execute(
            f"SELECT DISTINCT {id_column} FROM {table} "
            f"WHERE canonical_id IS NULL OR canonical_id = ''"
        ).fetchall()
        for (agent_id,) in rows:
            canonical = alias_map.get(agent_id, "")
            if canonical:
                conn.execute(
                    f"UPDATE {table} SET canonical_id = ? WHERE {id_column} = ?",
                    (canonical, agent_id)
                )
                updated += 1
        conn.commit()
    except Exception as exc:
        print(f"  WARNING: Backfill failed for {table}: {exc}")
        conn.close()
        return {"db": db_path, "table": table, "status": "backfill_failed",
                "error": str(exc)}

    total = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    filled = conn.execute(
        f"SELECT COUNT(*) FROM {table} WHERE canonical_id != ''"
    ).fetchone()[0]
    conn.close()

    return {
        "db": db_path,
        "table": table,
        "status": "ok",
        "total_rows": total,
        "updated": updated,
        "filled": filled,
        "fill_rate": f"{filled / total * 100:.1f}%" if total else "0%",
    }


def main():
    parser = argparse.ArgumentParser(description="Backfill canonical_id columns")
    parser.add_argument("--alias-db", default=DEFAULT_ALIAS_DB)
    args = parser.parse_args()

    print("Loading alias map...")
    alias_map = load_alias_map(args.alias_db)
    print(f"  {len(alias_map)} mappings loaded")

    if not alias_map:
        print("No alias mappings. Run alias_registry_builder.py first.")
        sys.exit(1)

    targets = [
        (TRUST_DB, "trust_signals", "agent_id"),
        (AUDIT_DB, "audit_entries", "agent_id"),
    ]

    for db_path, table, col in targets:
        print(f"\nBackfilling {db_path} → {table}.{col}...")
        result = backfill_table(db_path, table, col, alias_map)
        for k, v in result.items():
            print(f"  {k}: {v}")

    print("\nDone.")


if __name__ == "__main__":
    main()
