#!/usr/bin/env python3
"""
Migration Preflight Report — dry-run alias/canonical migration check.

Reads alias_registry.yaml and reports coverage of canonical IDs across
trust_signals, audit_log, and ChromaDB collections.

Usage:
    python tools/migration_preflight.py [--dry-run]

Always read-only by default. --dry-run is accepted for compatibility but
changes nothing (the tool never writes).
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from waggledance.core.capabilities.aliasing import AliasRegistry

log = logging.getLogger("migration.preflight")


def _db_coverage(db_path: str, table: str, agent_col: str, canonical_col: str) -> dict:
    """Check canonical_id fill rate for one table. Returns stats dict."""
    p = Path(db_path)
    if not p.exists():
        return {"status": "db_not_found", "path": db_path}

    conn = sqlite3.connect(db_path)
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    if not exists:
        conn.close()
        return {"status": "table_not_found", "path": db_path, "table": table}

    # Check if canonical_col exists
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if canonical_col not in cols:
        total = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        conn.close()
        return {
            "status": "column_missing",
            "path": db_path,
            "table": table,
            "total_rows": total,
            "filled": 0,
            "fill_rate": 0.0,
        }

    total = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    filled = conn.execute(
        f"SELECT COUNT(*) FROM {table} WHERE {canonical_col} IS NOT NULL AND {canonical_col} != ''",
    ).fetchone()[0]
    conn.close()

    return {
        "status": "ok",
        "path": db_path,
        "table": table,
        "total_rows": total,
        "filled": filled,
        "fill_rate": round(filled / total, 4) if total > 0 else 1.0,
    }


def _chroma_coverage() -> dict:
    """Check ChromaDB canonical metadata coverage (if available)."""
    try:
        import chromadb

        client = chromadb.PersistentClient(path="data/chromadb")
        collections = client.list_collections()
        results = {}
        for coll in collections:
            name = coll.name if hasattr(coll, "name") else str(coll)
            try:
                data = coll.get(include=["metadatas"])
                total = len(data["ids"])
                filled = sum(
                    1 for m in (data.get("metadatas") or [])
                    if m and m.get("canonical_id")
                )
                results[name] = {
                    "total": total,
                    "filled": filled,
                    "fill_rate": round(filled / total, 4) if total > 0 else 1.0,
                }
            except Exception as e:
                results[name] = {"error": str(e)}
        return {"status": "ok", "collections": results}
    except ImportError:
        return {"status": "chromadb_not_installed"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def run_preflight(dry_run: bool = True) -> dict:
    """Run preflight report. Returns structured report dict."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    # Load registry
    try:
        registry = AliasRegistry.from_yaml("configs/alias_registry.yaml")
    except FileNotFoundError:
        registry = AliasRegistry.from_yaml_default()

    lookup = registry.build_legacy_to_canonical_map()

    report = {
        "mode": "dry_run" if dry_run else "live",
        "agent_count": len(registry),
        "lookup_entries": len(lookup),
        "canonical_ids": len(registry.all_canonical_ids()),
        "db_coverage": {},
        "chroma_coverage": {},
    }

    # DB coverage
    db_targets = [
        ("data/audit_log.db", "trust_signals", "agent_id", "canonical_id"),
        ("data/audit_log.db", "audit", "agent_id", "canonical_id"),
        ("data/audit_log.db", "validations", "validator_id", "canonical_id"),
    ]
    for db_path, table, agent_col, canonical_col in db_targets:
        key = f"{db_path}:{table}"
        report["db_coverage"][key] = _db_coverage(db_path, table, agent_col, canonical_col)

    # Chroma coverage
    report["chroma_coverage"] = _chroma_coverage()

    # Print summary
    print("=" * 60)
    print("Migration Preflight Report")
    print("=" * 60)
    print(f"  Mode:           {'DRY RUN' if dry_run else 'LIVE'}")
    print(f"  Agent count:    {report['agent_count']}")
    print(f"  Lookup entries: {report['lookup_entries']}")
    print(f"  Canonical IDs:  {report['canonical_ids']}")

    print("\n  DB Coverage:")
    for key, info in report["db_coverage"].items():
        status = info["status"]
        if status == "ok":
            print(f"    {key}: {info['filled']}/{info['total_rows']} "
                  f"({info['fill_rate']:.1%})")
        else:
            print(f"    {key}: {status}")

    print("\n  Chroma Coverage:")
    chroma = report["chroma_coverage"]
    if chroma["status"] == "ok":
        for name, info in chroma.get("collections", {}).items():
            if "error" in info:
                print(f"    {name}: error — {info['error']}")
            else:
                print(f"    {name}: {info['filled']}/{info['total']} "
                      f"({info['fill_rate']:.1%})")
    else:
        print(f"    {chroma['status']}")

    print("=" * 60)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migration preflight report")
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="Read-only mode (default, always safe)")
    args = parser.parse_args()
    run_preflight(dry_run=args.dry_run)
