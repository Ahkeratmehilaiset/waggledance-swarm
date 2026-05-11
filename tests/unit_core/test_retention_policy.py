# SPDX-License-Identifier: BUSL-1.1
"""Audit H33+H34 regression — retention policy prunes old rows.

Tests pin the contract: dry_run counts but doesn't delete; apply
deletes only rows older than the cutoff; schema-drift surfaces as an
error (never silent column rename); empty DB / missing column / missing
table do not crash the policy.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from waggledance.core.storage.retention_policy import (
    PruneReport,
    RetentionPolicy,
    RetentionRule,
    default_rules,
)


# ── Helpers ───────────────────────────────────────────────────────


def _make_table(db_path: Path, table: str, ts_col: str = "timestamp") -> None:
    """Create a single-column table for pruning tests."""
    con = sqlite3.connect(str(db_path))
    try:
        con.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, {ts_col} REAL NOT NULL)")
        con.commit()
    finally:
        con.close()


def _insert_rows(db_path: Path, table: str, timestamps: list[float], ts_col: str = "timestamp") -> None:
    con = sqlite3.connect(str(db_path))
    try:
        con.executemany(
            f"INSERT INTO {table}({ts_col}) VALUES (?)",
            [(t,) for t in timestamps],
        )
        con.commit()
    finally:
        con.close()


def _row_count(db_path: Path, table: str) -> int:
    con = sqlite3.connect(str(db_path))
    try:
        return int(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    finally:
        con.close()


# ── Dry-run / apply contract ──────────────────────────────────────


class TestRetentionPolicyDryRun:
    def test_dry_run_counts_without_deleting(self, tmp_path):
        db = tmp_path / "test.db"
        _make_table(db, "events")
        # 3 rows older than 1 day; 2 rows from now.
        old = time.time() - 2 * 86400
        new = time.time() - 1
        _insert_rows(db, "events", [old, old, old, new, new])

        rule = RetentionRule(db, "events", "timestamp", keep_days=1)
        policy = RetentionPolicy([rule])
        report = policy.run_once(dry_run=True)

        assert report.dry_run is True
        assert report.rules_evaluated == 1
        assert report.rows_deleted["test.db:events"] == 3
        assert _row_count(db, "events") == 5  # nothing actually deleted

    def test_apply_deletes_and_keeps_recent(self, tmp_path):
        db = tmp_path / "test.db"
        _make_table(db, "events")
        old = time.time() - 5 * 86400
        new = time.time() - 1
        _insert_rows(db, "events", [old, old, new])

        rule = RetentionRule(db, "events", "timestamp", keep_days=1)
        report = RetentionPolicy([rule]).run_once(dry_run=False)

        assert report.dry_run is False
        assert report.rows_deleted["test.db:events"] == 2
        assert _row_count(db, "events") == 1

    def test_empty_table_no_op(self, tmp_path):
        db = tmp_path / "test.db"
        _make_table(db, "events")
        rule = RetentionRule(db, "events", "timestamp", keep_days=30)
        report = RetentionPolicy([rule]).run_once(dry_run=False)
        assert report.rows_deleted["test.db:events"] == 0
        assert report.errors == []

    def test_nothing_old_enough_no_op(self, tmp_path):
        db = tmp_path / "test.db"
        _make_table(db, "events")
        _insert_rows(db, "events", [time.time() - 1, time.time() - 2])
        rule = RetentionRule(db, "events", "timestamp", keep_days=30)
        report = RetentionPolicy([rule]).run_once(dry_run=False)
        assert report.rows_deleted["test.db:events"] == 0
        assert _row_count(db, "events") == 2


# ── Schema-drift safety ───────────────────────────────────────────


class TestRetentionPolicySafety:
    def test_missing_db_surfaces_as_error(self, tmp_path):
        rule = RetentionRule(
            tmp_path / "does_not_exist.db", "events", "timestamp", keep_days=30,
        )
        report = RetentionPolicy([rule]).run_once(dry_run=False)
        assert report.rules_evaluated == 1
        assert len(report.errors) == 1
        assert "does_not_exist.db" in report.errors[0]

    def test_missing_table_surfaces_as_error(self, tmp_path):
        db = tmp_path / "test.db"
        # create a DB with a different table than the rule targets
        _make_table(db, "other_table")
        rule = RetentionRule(db, "events", "timestamp", keep_days=30)
        report = RetentionPolicy([rule]).run_once(dry_run=False)
        assert len(report.errors) == 1
        assert "events" in report.errors[0]

    def test_missing_timestamp_column_surfaces_as_error(self, tmp_path):
        db = tmp_path / "test.db"
        _make_table(db, "events", ts_col="created_at")  # column != rule
        rule = RetentionRule(db, "events", "timestamp", keep_days=30)
        report = RetentionPolicy([rule]).run_once(dry_run=False)
        assert len(report.errors) == 1
        assert "timestamp" in report.errors[0]

    def test_one_rule_failure_does_not_abort_others(self, tmp_path):
        good_db = tmp_path / "good.db"
        _make_table(good_db, "events")
        _insert_rows(good_db, "events", [time.time() - 100 * 86400])
        bad_rule = RetentionRule(
            tmp_path / "missing.db", "events", "timestamp", keep_days=30,
        )
        good_rule = RetentionRule(good_db, "events", "timestamp", keep_days=30)
        report = RetentionPolicy([bad_rule, good_rule]).run_once(dry_run=False)

        # Both rules attempted; bad rule errored; good rule still pruned.
        assert report.rules_evaluated == 2
        assert len(report.errors) == 1
        assert report.rows_deleted.get("good.db:events") == 1


# ── default_rules() filter ────────────────────────────────────────


class TestDefaultRules:
    def test_default_rules_filters_missing_dbs(self, tmp_path):
        """Only DBs that physically exist appear in the default ruleset."""
        rules = default_rules(tmp_path)
        assert rules == [], (
            "default_rules() should return [] when no DBs are present"
        )

    def test_default_rules_picks_up_present_db(self, tmp_path):
        db = tmp_path / "audit_log.db"
        _make_table(db, "audit")
        rules = default_rules(tmp_path)
        # Only the audit_log rule should resolve (the world_store + case_store DBs aren't there).
        labels = [(r.db_path.name, r.table) for r in rules]
        assert ("audit_log.db", "audit") in labels
        assert len(rules) == 1


# ── PruneReport serialization ────────────────────────────────────


class TestPruneReportSerialization:
    def test_to_dict_includes_total(self, tmp_path):
        db = tmp_path / "test.db"
        _make_table(db, "events")
        old = time.time() - 100 * 86400
        _insert_rows(db, "events", [old, old, old])
        rule = RetentionRule(db, "events", "timestamp", keep_days=30)
        report = RetentionPolicy([rule]).run_once(dry_run=True)
        d = report.to_dict()
        assert d["dry_run"] is True
        assert d["total_rows_deleted"] == 3
        assert d["rows_deleted"]["test.db:events"] == 3
        assert d["errors"] == []
