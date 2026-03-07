"""
WaggleDance — Database Schema Migration Tool
=============================================
Manages schema versions for all SQLite databases.

Usage:
    python tools/migrate_db.py --check       # Report current versions
    python tools/migrate_db.py --migrate     # Apply pending migrations
    python tools/migrate_db.py --status      # Alias for --check
"""

import argparse
import logging
import sqlite3
import sys
import time
from pathlib import Path

log = logging.getLogger("waggledance.migrate")
logging.basicConfig(level=logging.INFO, format="%(message)s")

# ── Schema version registry ──────────────────────────────────

LATEST_VERSIONS = {
    "audit_log.db": 2,
    "waggle_dance.db": 2,
}

# ── Migration functions ──────────────────────────────────────


def _ensure_schema_version_table(conn: sqlite3.Connection):
    """Create schema_version table if missing (bootstraps v0)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            id          INTEGER PRIMARY KEY CHECK (id = 1),
            version     INTEGER NOT NULL DEFAULT 1,
            updated_at  REAL    NOT NULL
        )
    """)
    conn.commit()


def _get_version(conn: sqlite3.Connection) -> int:
    """Get current schema version (0 if table missing or empty)."""
    try:
        row = conn.execute(
            "SELECT version FROM schema_version WHERE id = 1"
        ).fetchone()
        return row[0] if row else 0
    except sqlite3.OperationalError:
        return 0


def _set_version(conn: sqlite3.Connection, version: int):
    """Upsert schema version."""
    _ensure_schema_version_table(conn)
    conn.execute(
        "INSERT OR REPLACE INTO schema_version (id, version, updated_at) "
        "VALUES (1, ?, ?)",
        (version, time.time())
    )
    conn.commit()


# ── audit_log.db migrations ─────────────────────────────────

def _migrate_audit_log_v1_to_v2(conn: sqlite3.Connection):
    """V1→V2: Add content_preview column for quick audit browsing."""
    # Check if column already exists
    cols = [r[1] for r in conn.execute("PRAGMA table_info(audit)").fetchall()]
    if "content_preview" not in cols:
        conn.execute(
            "ALTER TABLE audit ADD COLUMN content_preview TEXT NOT NULL DEFAULT ''"
        )
        conn.commit()
        log.info("  + Added audit.content_preview column")
    else:
        log.info("  = audit.content_preview already exists")


AUDIT_MIGRATIONS = {
    2: _migrate_audit_log_v1_to_v2,
}


# ── waggle_dance.db migrations ──────────────────────────────

def _migrate_waggle_v1_to_v2(conn: sqlite3.Connection):
    """V1→V2: Add indexes on memories and events for faster queries."""
    conn.executescript("""
        CREATE INDEX IF NOT EXISTS idx_memories_agent
            ON memories(agent_id);
        CREATE INDEX IF NOT EXISTS idx_memories_type
            ON memories(memory_type);
        CREATE INDEX IF NOT EXISTS idx_events_agent
            ON events(agent_id);
        CREATE INDEX IF NOT EXISTS idx_events_type
            ON events(event_type);
        CREATE INDEX IF NOT EXISTS idx_messages_to
            ON messages(to_agent, read);
        CREATE INDEX IF NOT EXISTS idx_tasks_status
            ON tasks(status);
    """)
    conn.commit()
    log.info("  + Added performance indexes on memories/events/messages/tasks")


WAGGLE_MIGRATIONS = {
    2: _migrate_waggle_v1_to_v2,
}


MIGRATION_REGISTRY = {
    "audit_log.db": AUDIT_MIGRATIONS,
    "waggle_dance.db": WAGGLE_MIGRATIONS,
}


# ── Core logic ───────────────────────────────────────────────

def check_db(db_name: str, data_dir: Path) -> dict:
    """Check a single DB and return status."""
    db_path = data_dir / db_name
    latest = LATEST_VERSIONS.get(db_name, 1)

    if not db_path.exists():
        return {"db": db_name, "exists": False, "version": 0,
                "latest": latest, "status": "MISSING"}

    conn = sqlite3.connect(str(db_path))
    try:
        current = _get_version(conn)
        if current == 0:
            # DB exists but no schema_version table — it's v1 (original)
            current = 1
        status = "OK" if current >= latest else "NEEDS_MIGRATION"
        return {"db": db_name, "exists": True, "version": current,
                "latest": latest, "status": status}
    finally:
        conn.close()


def migrate_db(db_name: str, data_dir: Path) -> bool:
    """Apply pending migrations to a single DB. Returns True if changes made."""
    db_path = data_dir / db_name
    latest = LATEST_VERSIONS.get(db_name, 1)
    migrations = MIGRATION_REGISTRY.get(db_name, {})

    if not db_path.exists():
        log.warning(f"  {db_name}: file not found, skipping")
        return False

    try:
        conn = sqlite3.connect(str(db_path), timeout=10)
    except sqlite3.OperationalError as e:
        log.warning(f"  {db_name}: cannot open ({e})")
        return False
    try:
        _ensure_schema_version_table(conn)
        current = _get_version(conn)
        if current == 0:
            current = 1
            _set_version(conn, 1)

        if current >= latest:
            log.info(f"  {db_name}: already at v{current} (latest)")
            return False

        changed = False
        for target_v in range(current + 1, latest + 1):
            fn = migrations.get(target_v)
            if fn:
                log.info(f"  {db_name}: migrating v{target_v - 1} → v{target_v}")
                fn(conn)
                _set_version(conn, target_v)
                changed = True
            else:
                log.warning(f"  {db_name}: no migration function for v{target_v}")
                break

        return changed
    finally:
        conn.close()


def check_all(data_dir: Path) -> list:
    """Check all known DBs."""
    results = []
    for db_name in LATEST_VERSIONS:
        results.append(check_db(db_name, data_dir))
    return results


def migrate_all(data_dir: Path) -> int:
    """Migrate all known DBs. Returns count of DBs changed."""
    changed = 0
    for db_name in LATEST_VERSIONS:
        if migrate_db(db_name, data_dir):
            changed += 1
    return changed


# ── CLI ──────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="WaggleDance database schema migration tool"
    )
    parser.add_argument("--check", "--status", action="store_true",
                        help="Report current schema versions")
    parser.add_argument("--migrate", action="store_true",
                        help="Apply pending migrations")
    parser.add_argument("--data-dir", default="data",
                        help="Data directory (default: data)")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    if not data_dir.exists():
        log.error(f"Data directory not found: {data_dir}")
        sys.exit(1)

    if args.migrate:
        log.info("=== WaggleDance Schema Migration ===\n")
        changed = migrate_all(data_dir)
        log.info(f"\nDone. {changed} database(s) updated.")

        # Verify
        log.info("\n=== Post-migration status ===\n")
        for r in check_all(data_dir):
            _print_status(r)
    else:
        # Default: --check
        log.info("=== WaggleDance Schema Status ===\n")
        all_ok = True
        for r in check_all(data_dir):
            _print_status(r)
            if r["status"] != "OK":
                all_ok = False

        if all_ok:
            log.info("\nAll databases at latest version.")
        else:
            log.info("\nRun with --migrate to apply pending migrations.")
            sys.exit(1)


def _print_status(r: dict):
    icon = {"OK": "✓", "NEEDS_MIGRATION": "⬆", "MISSING": "✗"}
    log.info(f"  {icon.get(r['status'], '?')} {r['db']}: "
             f"v{r['version']}/{r['latest']} [{r['status']}]")


if __name__ == "__main__":
    main()
