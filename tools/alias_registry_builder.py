"""Build alias registry SQLite table from configs/alias_registry.yaml.

Phase 1 migration: loads the YAML alias registry into a SQLite table
for fast canonical_id lookups at runtime.

Usage:
    python tools/alias_registry_builder.py [--db data/alias_registry.db]
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time

import yaml


DEFAULT_YAML = "configs/alias_registry.yaml"
DEFAULT_DB = "data/alias_registry.db"


def load_registry(yaml_path: str) -> dict:
    """Load alias registry from YAML."""
    with open(yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data or {}


def build_db(registry: dict, db_path: str) -> dict:
    """Build SQLite alias registry from parsed YAML.

    Returns stats dict with counts.
    """
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")

    conn.executescript("""
        DROP TABLE IF EXISTS alias_registry;
        CREATE TABLE alias_registry (
            legacy_id   TEXT NOT NULL,
            canonical   TEXT NOT NULL,
            profile     TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (legacy_id)
        );
        CREATE INDEX idx_alias_canonical ON alias_registry(canonical);
    """)

    total = 0
    aliases_total = 0

    for legacy_id, entry in registry.items():
        if not isinstance(entry, dict):
            continue
        canonical = entry.get("canonical", "")
        profiles = entry.get("profiles", [])
        profile_str = ",".join(profiles) if isinstance(profiles, list) else str(profiles)

        # Insert the primary legacy_id
        conn.execute(
            "INSERT OR REPLACE INTO alias_registry (legacy_id, canonical, profile) "
            "VALUES (?, ?, ?)",
            (legacy_id, canonical, profile_str)
        )
        total += 1

        # Insert each alias
        for alias in entry.get("aliases", []):
            if alias != legacy_id:
                conn.execute(
                    "INSERT OR REPLACE INTO alias_registry (legacy_id, canonical, profile) "
                    "VALUES (?, ?, ?)",
                    (alias, canonical, profile_str)
                )
                aliases_total += 1

    conn.commit()

    # Verify
    count = conn.execute("SELECT COUNT(*) FROM alias_registry").fetchone()[0]
    canonical_count = conn.execute(
        "SELECT COUNT(DISTINCT canonical) FROM alias_registry"
    ).fetchone()[0]
    conn.close()

    return {
        "agents": total,
        "aliases": aliases_total,
        "total_rows": count,
        "canonical_ids": canonical_count,
        "db_path": db_path,
    }


def main():
    parser = argparse.ArgumentParser(description="Build alias registry SQLite DB")
    parser.add_argument("--yaml", default=DEFAULT_YAML, help="Path to alias_registry.yaml")
    parser.add_argument("--db", default=DEFAULT_DB, help="Output SQLite DB path")
    args = parser.parse_args()

    if not os.path.exists(args.yaml):
        print(f"ERROR: {args.yaml} not found")
        sys.exit(1)

    print(f"Loading registry from {args.yaml}...")
    registry = load_registry(args.yaml)

    print(f"Building SQLite DB at {args.db}...")
    stats = build_db(registry, args.db)

    print(f"Done: {stats['agents']} agents, {stats['aliases']} aliases, "
          f"{stats['total_rows']} total rows, {stats['canonical_ids']} canonical IDs")


if __name__ == "__main__":
    main()
