# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
"""Read-only SQLite transport for operator-allowlisted local databases.

This module performs one explicit SELECT against one operator-allowlisted
SQLite database path. It opens SQLite in read-only mode, refuses non-SELECT
statements before execution, caps returned rows, and installs a progress
handler so long-running reads can be interrupted.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import os
from pathlib import Path
from time import monotonic
from typing import Any, Mapping, Sequence
from urllib.parse import quote
import sqlite3


DEFAULT_TIMEOUT_SECONDS = 5.0
DEFAULT_ROW_LIMIT = 500
MAX_TIMEOUT_SECONDS = 30.0
MAX_ROW_LIMIT = 10_000
_PROGRESS_HANDLER_OPS = 1_000
_READ_ONLY_URI_OPTIONS = "mode=ro"

SqliteScalar = str | int | float | bytes | None
SqliteParameters = Sequence[SqliteScalar] | Mapping[str, SqliteScalar]


@dataclass(frozen=True)
class SqliteReadResult:
    """Rows returned from an operator-allowlisted SQLite SELECT."""

    rows: list[dict[str, SqliteScalar]]
    column_names: tuple[str, ...]
    row_count: int
    source_path: str
    query_hash: str


class SqliteReadTransportError(ValueError):
    """Invalid SQLite read input or failed local read."""


def fetch_sqlite_rows(
    db_path: str | os.PathLike[str],
    query: str,
    *,
    allowed_db_paths: Sequence[str | os.PathLike[str]],
    parameters: SqliteParameters | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    row_limit: int = DEFAULT_ROW_LIMIT,
) -> SqliteReadResult:
    """Execute one read-only SELECT against an allowlisted SQLite database."""
    allowed_paths = _normalize_allowed_paths(allowed_db_paths)
    source_path = _validate_db_path(db_path, allowed_paths=allowed_paths)
    normalized_query = _validate_select_query(query)
    normalized_parameters = _validate_parameters(parameters)
    normalized_timeout = _validate_timeout(timeout_seconds)
    normalized_row_limit = _validate_row_limit(row_limit)
    return _execute_select(
        source_path,
        normalized_query,
        parameters=normalized_parameters,
        timeout_seconds=normalized_timeout,
        row_limit=normalized_row_limit,
    )


def _execute_select(
    source_path: Path,
    query: str,
    *,
    parameters: SqliteParameters,
    timeout_seconds: float,
    row_limit: int,
) -> SqliteReadResult:
    deadline = monotonic() + timeout_seconds
    uri = _read_only_uri(source_path)
    try:
        with sqlite3.connect(
            uri,
            timeout=timeout_seconds,
            uri=True,
        ) as connection:
            connection.row_factory = sqlite3.Row
            connection.set_progress_handler(
                lambda: 1 if monotonic() > deadline else 0,
                _PROGRESS_HANDLER_OPS,
            )
            cursor = connection.execute(query, parameters)
            rows = cursor.fetchmany(row_limit + 1)
            if len(rows) > row_limit:
                raise SqliteReadTransportError("ROW_LIMIT_EXCEEDED")
            column_names = _column_names(cursor)
    except SqliteReadTransportError:
        raise
    except sqlite3.OperationalError as exc:
        if "interrupted" in str(exc).lower():
            raise SqliteReadTransportError("QUERY_TIMEOUT") from exc
        if "readonly" in str(exc).lower():
            raise SqliteReadTransportError("READ_ONLY_REFUSED") from exc
        raise SqliteReadTransportError("QUERY_EXECUTION_FAILED") from exc
    except sqlite3.DatabaseError as exc:
        raise SqliteReadTransportError("DATABASE_READ_FAILED") from exc

    return SqliteReadResult(
        rows=[dict(row) for row in rows],
        column_names=column_names,
        row_count=len(rows),
        source_path=str(source_path),
        query_hash=sha256(query.encode("utf-8")).hexdigest(),
    )


def _validate_db_path(
    db_path: str | os.PathLike[str],
    *,
    allowed_paths: frozenset[str],
) -> Path:
    if not isinstance(db_path, (str, os.PathLike)):
        raise SqliteReadTransportError("DB_PATH_REFUSED")
    try:
        path = Path(db_path).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise SqliteReadTransportError("DB_PATH_REFUSED") from exc
    if not path.is_file():
        raise SqliteReadTransportError("DB_PATH_REFUSED")
    if _path_key(path) not in allowed_paths:
        raise SqliteReadTransportError("DB_PATH_NOT_ALLOWLISTED")
    return path


def _normalize_allowed_paths(
    raw_paths: Sequence[str | os.PathLike[str]],
) -> frozenset[str]:
    if not isinstance(raw_paths, Sequence) or isinstance(raw_paths, (str, bytes)):
        raise SqliteReadTransportError("ALLOWLIST_PATHS_REFUSED")
    normalized: set[str] = set()
    for index, raw_path in enumerate(raw_paths):
        if not isinstance(raw_path, (str, os.PathLike)):
            raise SqliteReadTransportError(f"ALLOWLIST_PATHS_REFUSED_{index}")
        try:
            path = Path(raw_path).expanduser().resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise SqliteReadTransportError(
                f"ALLOWLIST_PATHS_REFUSED_{index}"
            ) from exc
        if not path.is_file():
            raise SqliteReadTransportError(f"ALLOWLIST_PATHS_REFUSED_{index}")
        normalized.add(_path_key(path))
    return frozenset(normalized)


def _path_key(path: Path) -> str:
    return os.path.normcase(str(path))


def _validate_select_query(query: str) -> str:
    if not isinstance(query, str) or not query.strip():
        raise SqliteReadTransportError("QUERY_EMPTY")
    normalized = query.strip()
    first_token = normalized.split(None, 1)[0].lower()
    if first_token != "select":
        raise SqliteReadTransportError("QUERY_SELECT_ONLY_REFUSED")
    if ";" in normalized.rstrip(";"):
        raise SqliteReadTransportError("QUERY_SINGLE_STATEMENT_REFUSED")
    return normalized.rstrip(";").strip()


def _validate_parameters(
    parameters: SqliteParameters | None,
) -> SqliteParameters:
    if parameters is None:
        return ()
    if isinstance(parameters, Mapping):
        return {
            _validate_parameter_name(key): _validate_parameter_value(value)
            for key, value in parameters.items()
        }
    if isinstance(parameters, Sequence) and not isinstance(
        parameters,
        (str, bytes, bytearray),
    ):
        return tuple(_validate_parameter_value(value) for value in parameters)
    raise SqliteReadTransportError("PARAMETERS_REFUSED")


def _validate_parameter_name(key: Any) -> str:
    if not isinstance(key, str) or not key.strip():
        raise SqliteReadTransportError("PARAMETER_NAME_REFUSED")
    return key.strip()


def _validate_parameter_value(value: Any) -> SqliteScalar:
    if isinstance(value, bool) or not isinstance(
        value,
        (str, int, float, bytes, type(None)),
    ):
        raise SqliteReadTransportError("PARAMETER_VALUE_REFUSED")
    return value


def _validate_timeout(timeout_seconds: float) -> float:
    if isinstance(timeout_seconds, bool) or not isinstance(
        timeout_seconds,
        (int, float),
    ):
        raise SqliteReadTransportError("TIMEOUT_OUT_OF_RANGE")
    normalized = float(timeout_seconds)
    if normalized <= 0 or normalized > MAX_TIMEOUT_SECONDS:
        raise SqliteReadTransportError("TIMEOUT_OUT_OF_RANGE")
    return normalized


def _validate_row_limit(row_limit: int) -> int:
    if isinstance(row_limit, bool) or not isinstance(row_limit, int):
        raise SqliteReadTransportError("ROW_LIMIT_OUT_OF_RANGE")
    if row_limit <= 0 or row_limit > MAX_ROW_LIMIT:
        raise SqliteReadTransportError("ROW_LIMIT_OUT_OF_RANGE")
    return row_limit


def _column_names(cursor: sqlite3.Cursor) -> tuple[str, ...]:
    if cursor.description is None:
        raise SqliteReadTransportError("QUERY_RESULT_SHAPE_REFUSED")
    names = tuple(column[0] for column in cursor.description)
    if not names or any(not isinstance(name, str) or not name for name in names):
        raise SqliteReadTransportError("QUERY_RESULT_SHAPE_REFUSED")
    if len(set(names)) != len(names):
        raise SqliteReadTransportError("DUPLICATE_COLUMN_REFUSED")
    return names


def _read_only_uri(path: Path) -> str:
    return f"file:{quote(str(path), safe=':/')}?{_READ_ONLY_URI_OPTIONS}"


__all__ = [
    "DEFAULT_ROW_LIMIT",
    "DEFAULT_TIMEOUT_SECONDS",
    "MAX_ROW_LIMIT",
    "MAX_TIMEOUT_SECONDS",
    "SqliteParameters",
    "SqliteReadResult",
    "SqliteReadTransportError",
    "SqliteScalar",
    "fetch_sqlite_rows",
]
