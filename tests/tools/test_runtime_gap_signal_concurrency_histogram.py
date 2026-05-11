# SPDX-License-Identifier: BUSL-1.1
"""Tests for tools/runtime_gap_signal_concurrency_histogram.py.

The sparse-burst regression test (test_sparse_bursts_do_not_inflate_sla)
covers the bug Codex caught in PR #224 review (2026-05-11 10:05:25Z):
61 one-second bursts spaced over a 3 600 s span must report ~1.7 percent
N_ge_4 windows, NOT 100 percent. The denominator is total observation
windows, not active windows.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tools.runtime_gap_signal_concurrency_histogram import (
    _positive_int,
    build_histogram,
    parse_args,
)


def _make_db(path: Path) -> None:
    """Create a minimal runtime_gap_signals table matching the production schema."""
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            """
            CREATE TABLE runtime_gap_signals(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                family_kind TEXT,
                cell_coord TEXT,
                signal_payload TEXT,
                weight REAL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _insert(
    path: Path, ts: datetime, cell: str, kind: str = "k", family: str = "f"
) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            "INSERT INTO runtime_gap_signals(kind, family_kind, cell_coord, "
            "signal_payload, weight, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (kind, family, cell, "{}", 1.0, ts.isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def test_window_seconds_must_be_positive(tmp_path: Path) -> None:
    db = tmp_path / "x.db"
    _make_db(db)
    with pytest.raises(ValueError):
        build_histogram(db, window_seconds=0)
    with pytest.raises(ValueError):
        build_histogram(db, window_seconds=-1)


def test_empty_db_returns_error_marker(tmp_path: Path) -> None:
    db = tmp_path / "empty.db"
    _make_db(db)
    result = build_histogram(db, window_seconds=1)
    assert result["row_count"] == 0
    assert "error" in result


def test_single_cell_single_window_low_concurrency(tmp_path: Path) -> None:
    db = tmp_path / "low.db"
    _make_db(db)
    base = datetime(2026, 5, 11, 0, 0, 0, tzinfo=timezone.utc)
    for i in range(20):
        _insert(db, base + timedelta(milliseconds=i * 50), "hub")
    result = build_histogram(db, window_seconds=1)
    # 20 writes in <1 s = 1 active window, 1 cell -> N_ge_2 = 0 %
    assert result["row_count"] == 20
    assert result["distinct_cells"] == 1
    assert result["active_window_count"] == 1
    assert result["sla_thresholds"]["N_ge_2"]["windows"] == 0
    assert result["r25_decision_signal"]["verdict"] in {
        "insufficient-data",
        "r25-not-needed",
    }


def test_sparse_bursts_do_not_inflate_sla(tmp_path: Path) -> None:
    """Codex PR #224 review regression — denominator must be total span.

    61 one-second bursts of 4 concurrent cells spaced 60 s apart over a
    ~3 600 s span. The buggy denominator (active windows only) would
    report N_ge_4 = 100 percent; the correct denominator (total
    observation windows, 3601) reports ~1.7 percent and verdict must NOT
    be r25-strongly-recommended.
    """
    db = tmp_path / "sparse.db"
    _make_db(db)
    base = datetime(2026, 5, 11, 0, 0, 0, tzinfo=timezone.utc)
    cells = ["hub", "bee_ops", "environment", "home_comfort"]
    n_bursts = 61
    burst_spacing_s = 60
    for burst in range(n_bursts):
        ts = base + timedelta(seconds=burst * burst_spacing_s)
        for c in cells:
            _insert(db, ts, c)

    result = build_histogram(db, window_seconds=1)
    total_span_s = (n_bursts - 1) * burst_spacing_s  # 60 * 60 = 3600
    expected_total_windows = total_span_s + 1  # inclusive of last second
    assert result["total_observation_windows"] == expected_total_windows
    assert result["active_window_count"] == n_bursts
    n4_pct = result["sla_thresholds"]["N_ge_4"]["pct_of_total_windows"]
    assert 1.0 < n4_pct < 3.0, (
        f"expected sparse-burst N_ge_4 to be about 1.7 percent of total "
        f"windows (61/{expected_total_windows}), got {n4_pct}"
    )
    verdict = result["r25_decision_signal"]["verdict"]
    assert verdict != "r25-strongly-recommended", (
        f"sparse-burst pattern must NOT trigger r25-strongly-recommended; "
        f"got verdict={verdict}"
    )


def test_sustained_high_concurrency_triggers_r25(tmp_path: Path) -> None:
    """If most windows have >=4 concurrent cells, verdict should recommend R25."""
    db = tmp_path / "sustained.db"
    _make_db(db)
    base = datetime(2026, 5, 11, 0, 0, 0, tzinfo=timezone.utc)
    cells = ["hub", "bee_ops", "environment", "home_comfort"]
    # 200 consecutive seconds, every second has 4 cells writing
    for sec in range(200):
        ts = base + timedelta(seconds=sec)
        for c in cells:
            _insert(db, ts, c)
    result = build_histogram(db, window_seconds=1)
    assert result["total_observation_windows"] == 200
    assert result["active_window_count"] == 200
    n4_pct = result["sla_thresholds"]["N_ge_4"]["pct_of_total_windows"]
    assert n4_pct == 100.0
    assert result["r25_decision_signal"]["verdict"] == "r25-strongly-recommended"


def test_cli_rejects_zero_window_seconds() -> None:
    with pytest.raises(SystemExit):
        parse_args(["--db", "x.db", "--window-seconds", "0"])
    with pytest.raises(SystemExit):
        parse_args(["--db", "x.db", "--window-seconds", "-5"])


def test_positive_int_typecheck() -> None:
    assert _positive_int("1") == 1
    assert _positive_int("60") == 60
    import argparse
    with pytest.raises(argparse.ArgumentTypeError):
        _positive_int("0")
    with pytest.raises(argparse.ArgumentTypeError):
        _positive_int("-3")
