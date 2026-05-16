# SPDX-License-Identifier: BUSL-1.1
"""Tests for v3.13.0 operator-allowlisted SQLite read transport."""
from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from waggledance.core.v3_13_0.eng06_burn_log_adapter import (
    normalize_burn_log_rows,
)
from waggledance.core.v3_13_0.eng06_fireplace_advisor import (
    OK,
    summarize_burn_log,
)
from waggledance.core.v3_13_0.sqlite_read_transport import (
    DEFAULT_ROW_LIMIT,
    SqliteReadTransportError,
    fetch_sqlite_rows,
)


def _create_burn_log_db(path: Path, *, row_count: int = 3) -> Path:
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE burn_log (
                day_utc TEXT NOT NULL,
                fire_event_count INTEGER NOT NULL,
                peak_chimney_temp_c REAL NOT NULL,
                average_chimney_temp_c REAL NOT NULL
            )
            """
        )
        connection.executemany(
            """
            INSERT INTO burn_log (
                day_utc,
                fire_event_count,
                peak_chimney_temp_c,
                average_chimney_temp_c
            ) VALUES (?, ?, ?, ?)
            """,
            [
                (
                    f"2026-01-{index + 1:02d}T00:00:00Z",
                    1 if index % 2 == 0 else 0,
                    140.0 + index,
                    80.0 + index,
                )
                for index in range(row_count)
            ],
        )
    return path


def test_fetches_rows_from_exact_operator_allowlisted_db_path(
    tmp_path: Path,
) -> None:
    db_path = _create_burn_log_db(tmp_path / "burn-log.db")

    result = fetch_sqlite_rows(
        db_path,
        """
        SELECT day_utc, fire_event_count, peak_chimney_temp_c
        FROM burn_log
        WHERE day_utc >= ?
        ORDER BY day_utc
        """,
        allowed_db_paths=(db_path,),
        parameters=("2026-01-02T00:00:00Z",),
        row_limit=2,
    )

    assert result.source_path == str(db_path.resolve())
    assert result.column_names == (
        "day_utc",
        "fire_event_count",
        "peak_chimney_temp_c",
    )
    assert result.row_count == 2
    assert result.rows == [
        {
            "day_utc": "2026-01-02T00:00:00Z",
            "fire_event_count": 0,
            "peak_chimney_temp_c": 141.0,
        },
        {
            "day_utc": "2026-01-03T00:00:00Z",
            "fire_event_count": 1,
            "peak_chimney_temp_c": 142.0,
        },
    ]
    assert len(result.query_hash) == 64


def test_sqlite_rows_compose_with_eng06_burn_log_adapter(
    tmp_path: Path,
) -> None:
    db_path = _create_burn_log_db(tmp_path / "fireplace.db")
    result = fetch_sqlite_rows(
        db_path,
        """
        SELECT
            day_utc,
            fire_event_count,
            peak_chimney_temp_c,
            average_chimney_temp_c
        FROM burn_log
        ORDER BY day_utc
        """,
        allowed_db_paths=(db_path,),
    )

    burn_log = normalize_burn_log_rows(result.rows)
    summary = summarize_burn_log(
        burn_log,
        horizon_start_utc="2026-01-01T00:00:00Z",
        horizon_end_utc="2026-01-03T00:00:00Z",
    )

    assert summary.result_marker == OK
    assert summary.fire_event_count_30d == 2


def test_refuses_non_allowlisted_db_path(tmp_path: Path) -> None:
    allowed_path = _create_burn_log_db(tmp_path / "allowed.db")
    other_path = _create_burn_log_db(tmp_path / "other.db")

    with pytest.raises(SqliteReadTransportError,
                       match="DB_PATH_NOT_ALLOWLISTED"):
        fetch_sqlite_rows(
            other_path,
            "SELECT day_utc FROM burn_log",
            allowed_db_paths=(allowed_path,),
        )


@pytest.mark.parametrize(
    ("query", "match"),
    [
        ("", "QUERY_EMPTY"),
        ("PRAGMA table_info(burn_log)", "QUERY_SELECT_ONLY_REFUSED"),
        ("UPDATE burn_log SET fire_event_count = 9", "QUERY_SELECT_ONLY_REFUSED"),
        ("DELETE FROM burn_log", "QUERY_SELECT_ONLY_REFUSED"),
        ("SELECT day_utc FROM burn_log; DELETE FROM burn_log",
         "QUERY_SINGLE_STATEMENT_REFUSED"),
    ],
)
def test_refuses_non_select_or_multi_statement_queries(
    tmp_path: Path,
    query: str,
    match: str,
) -> None:
    db_path = _create_burn_log_db(tmp_path / "burn-log.db")

    with pytest.raises(SqliteReadTransportError, match=match):
        fetch_sqlite_rows(db_path, query, allowed_db_paths=(db_path,))


def test_row_limit_is_enforced_before_returning_unbounded_results(
    tmp_path: Path,
) -> None:
    db_path = _create_burn_log_db(tmp_path / "burn-log.db", row_count=4)

    with pytest.raises(SqliteReadTransportError, match="ROW_LIMIT_EXCEEDED"):
        fetch_sqlite_rows(
            db_path,
            "SELECT day_utc FROM burn_log ORDER BY day_utc",
            allowed_db_paths=(db_path,),
            row_limit=3,
        )


def test_duplicate_result_columns_are_refused(tmp_path: Path) -> None:
    db_path = _create_burn_log_db(tmp_path / "burn-log.db")

    with pytest.raises(SqliteReadTransportError,
                       match="DUPLICATE_COLUMN_REFUSED"):
        fetch_sqlite_rows(
            db_path,
            "SELECT day_utc, day_utc FROM burn_log",
            allowed_db_paths=(db_path,),
        )


def test_mapping_parameters_are_supported_without_query_interpolation(
    tmp_path: Path,
) -> None:
    db_path = _create_burn_log_db(tmp_path / "burn-log.db")

    result = fetch_sqlite_rows(
        db_path,
        """
        SELECT day_utc
        FROM burn_log
        WHERE day_utc = :target_day
        """,
        allowed_db_paths=(db_path,),
        parameters={"target_day": "2026-01-02T00:00:00Z"},
    )

    assert result.rows == [{"day_utc": "2026-01-02T00:00:00Z"}]


def test_invalid_parameter_values_are_refused(tmp_path: Path) -> None:
    db_path = _create_burn_log_db(tmp_path / "burn-log.db")

    with pytest.raises(SqliteReadTransportError,
                       match="PARAMETER_VALUE_REFUSED"):
        fetch_sqlite_rows(
            db_path,
            "SELECT day_utc FROM burn_log WHERE fire_event_count = ?",
            allowed_db_paths=(db_path,),
            parameters=(True,),
        )


def test_query_timeout_interrupts_expensive_reads(tmp_path: Path) -> None:
    db_path = _create_burn_log_db(tmp_path / "burn-log.db", row_count=200)

    with pytest.raises(SqliteReadTransportError, match="QUERY_TIMEOUT"):
        fetch_sqlite_rows(
            db_path,
            """
            SELECT count(*) AS row_count
            FROM burn_log AS a, burn_log AS b, burn_log AS c
            """,
            allowed_db_paths=(db_path,),
            timeout_seconds=0.000001,
        )


def test_default_row_limit_constant_is_bounded() -> None:
    assert DEFAULT_ROW_LIMIT < 10_000
