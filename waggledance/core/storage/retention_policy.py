# SPDX-License-Identifier: BUSL-1.1
"""Retention policy for append-only SQLite tables.

Audit H33 + H34: ``audit_log.db`` was growing at ~4.5 MB/day and
``world_store.db`` at ~9.5 MB/day with no DELETE-pruning anywhere in
``waggledance/``. Both tables crossed their ``storage_health_service``
warn thresholds within ~10 operating days.

This module provides a small, explicit retention helper. It deliberately
does NOT auto-run on a timer — operator-facing tools / a future
background scheduler tick call ``run_once()`` to do the actual pruning,
so the operator stays in control of when fsync-heavy DELETE+VACUUM hits
the live DB.

Default rules below match the audit-recommended retention windows:
  - audit_log.audit          -> 30 days (compliance evidence)
  - world_store.world_snapshots -> 7 days (high-volume telemetry)
  - case_store.case_trajectories -> 90 days (learning funnel data)
"""
from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)


def _quote_sqlite_identifier(identifier: str) -> str:
    """Return a safely quoted SQLite identifier."""

    if not identifier:
        raise ValueError("empty SQLite identifier")
    return '"' + identifier.replace('"', '""') + '"'


@dataclass(frozen=True)
class RetentionRule:
    """One pruning rule.

    Attributes:
        db_path:           Path to the SQLite file.
        table:             Table name.
        timestamp_column:  Column holding row creation time (epoch seconds).
        keep_days:         Rows older than now - keep_days*86400 are deleted.
    """

    db_path: Path
    table: str
    timestamp_column: str
    keep_days: int


@dataclass
class PruneReport:
    """Result of one ``RetentionPolicy.run_once()`` call."""

    timestamp: float
    dry_run: bool
    rules_evaluated: int = 0
    rows_deleted: dict = field(default_factory=dict)  # "db:table" -> count
    errors: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "dry_run": self.dry_run,
            "rules_evaluated": self.rules_evaluated,
            "rows_deleted": dict(self.rows_deleted),
            "total_rows_deleted": sum(self.rows_deleted.values()),
            "errors": list(self.errors),
        }


def default_rules(data_dir: Path | str = "data") -> list[RetentionRule]:
    """Operator-tunable default ruleset matching the H33/H34 audit.

    Returns rules only for DBs that exist on disk so this can be safely
    called in test environments without spurious failures.
    """
    base = Path(data_dir)
    candidates = [
        RetentionRule(
            db_path=base / "audit_log.db",
            table="audit",
            timestamp_column="timestamp",
            keep_days=30,
        ),
        RetentionRule(
            db_path=base / "world_store.db",
            table="world_snapshots",
            timestamp_column="timestamp",
            keep_days=7,
        ),
        RetentionRule(
            db_path=base / "case_store.db",
            table="case_trajectories",
            timestamp_column="created_at",
            keep_days=90,
        ),
    ]
    return [r for r in candidates if r.db_path.exists()]


class RetentionPolicy:
    """Apply retention rules to a set of SQLite tables.

    Usage::

        policy = RetentionPolicy(default_rules())
        report = policy.run_once(dry_run=True)   # safe preview
        if report.total_rows_deleted_estimate > 0:
            real = policy.run_once(dry_run=False)
    """

    def __init__(self, rules: list[RetentionRule]) -> None:
        self._rules = list(rules)

    @property
    def rules(self) -> list[RetentionRule]:
        return list(self._rules)

    def run_once(self, *, dry_run: bool = False) -> PruneReport:
        """Execute the configured rules.

        With ``dry_run=True`` the function only counts the rows that
        WOULD be deleted (no writes, no VACUUM). With ``dry_run=False``
        it issues DELETE + VACUUM per rule and commits each rule
        independently so a partial failure doesn't abandon the rest.
        """
        report = PruneReport(timestamp=time.time(), dry_run=dry_run)
        cutoff_by_days: dict[int, float] = {}

        for rule in self._rules:
            report.rules_evaluated += 1
            cutoff = cutoff_by_days.setdefault(
                rule.keep_days, time.time() - rule.keep_days * 86400,
            )
            label = f"{rule.db_path.name}:{rule.table}"
            try:
                count = _apply_rule(rule, cutoff, dry_run=dry_run)
                report.rows_deleted[label] = count
                log.info(
                    "Retention %s on %s: %d rows%s",
                    "preview" if dry_run else "delete",
                    label, count, "" if dry_run else " + VACUUM",
                )
            except Exception as exc:  # noqa: BLE001 — surface as report
                report.errors.append(f"{label}: {exc!r}")
                log.warning("Retention failed on %s: %s", label, exc)

        return report


def _apply_rule(
    rule: RetentionRule, cutoff: float, *, dry_run: bool,
) -> int:
    """Apply one rule. Returns the row count (deleted or would-delete)."""
    if not rule.db_path.exists():
        raise FileNotFoundError(f"DB not found: {rule.db_path}")
    con = sqlite3.connect(str(rule.db_path))
    try:
        quoted_table = _quote_sqlite_identifier(rule.table)
        quoted_timestamp_column = _quote_sqlite_identifier(rule.timestamp_column)
        # Verify table + column exist before issuing the DELETE so we
        # never accidentally drop the wrong column on a schema drift.
        cols = {r[1] for r in con.execute(
            # identifier is quoted.
            f"PRAGMA table_info({quoted_table})"  # nosec B608
        ).fetchall()}
        if not cols:
            raise ValueError(f"Table {rule.table!r} not found in {rule.db_path.name}")
        if rule.timestamp_column not in cols:
            raise ValueError(
                f"Column {rule.timestamp_column!r} not in "
                f"{rule.db_path.name}:{rule.table} (have {sorted(cols)})"
            )

        if dry_run:
            row = con.execute(
                # identifiers are quoted.
                f"SELECT COUNT(*) FROM {quoted_table} "  # nosec B608
                f"WHERE {quoted_timestamp_column} < ?",
                (cutoff,),
            ).fetchone()
            return int(row[0]) if row else 0

        cur = con.execute(
            # identifiers are quoted.
            f"DELETE FROM {quoted_table} "  # nosec B608
            f"WHERE {quoted_timestamp_column} < ?",
            (cutoff,),
        )
        deleted = cur.rowcount or 0
        con.commit()
        # VACUUM cannot run inside a transaction; commit() above ends it.
        # Skip VACUUM if nothing was deleted — it's a costly file rewrite.
        if deleted > 0:
            con.execute("VACUUM")
        return deleted
    finally:
        con.close()
