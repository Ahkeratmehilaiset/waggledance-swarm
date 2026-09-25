#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Durable append-only storage for explicit accepted-work receipts.

Only the explicit ``append`` API opens a writable SQLite database. ``status``
opens an existing database in SQLite read-only mode and cannot create a
database, parent directory, or sidecar journal. It turns stored receipt rows
into the pure report from ``tools.bridge_work_ledger``; it is not runtime
wiring or a collector.
"""
from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
import json
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.bridge_work_ledger import (  # noqa: E402 - direct-script support above
    DEFAULT_MAX_INPUT_BYTES,
    INPUT_SCHEMA,
    InputError,
    build_report,
    strict_loads,
)


APPEND_SCHEMA = "wd.work-journal-input.v1"
REPORT_SCHEMA = "wd.work-journal-report.v1"
DATABASE_SCHEMA_VERSION = 1


class JournalError(ValueError):
    """A journal operation was refused without silently changing state."""


class JournalConflictError(JournalError):
    """An immutable receipt identity was supplied with different content."""


def _bounded_json(path: Path, *, max_bytes: int) -> Any:
    if max_bytes <= 0:
        raise JournalError("max_bytes must be positive")
    try:
        with path.open("rb") as source:
            payload = source.read(max_bytes + 1)
    except OSError as exc:
        raise JournalError("input cannot be read") from exc
    if len(payload) > max_bytes:
        raise JournalError("input exceeds max_bytes")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise JournalError("input is not UTF-8") from exc
    try:
        return strict_loads(text)
    except InputError as exc:
        raise JournalError(str(exc)) from exc


def load_append_document(path: Path, *, max_bytes: int = DEFAULT_MAX_INPUT_BYTES) -> list[Mapping[str, Any]]:
    """Load one strict append document before any database is opened."""
    document = _bounded_json(path, max_bytes=max_bytes)
    if not isinstance(document, Mapping):
        raise JournalError("journal input must be a JSON object")
    if set(document) != {"schema", "accepted_work"}:
        raise JournalError("journal input has unsupported or missing fields")
    if document.get("schema") != APPEND_SCHEMA:
        raise JournalError("unsupported journal input schema")
    receipts = document.get("accepted_work")
    if not isinstance(receipts, list):
        raise JournalError("accepted_work must be a list")
    return receipts


def _normalise_receipt(raw: Any) -> dict[str, str]:
    """Use the ledger's strict acceptance schema for one stored receipt."""
    try:
        ledger = build_report(
            {"schema": INPUT_SCHEMA, "usage_attempts": [], "accepted_work": [raw]}
        )
    except InputError as exc:
        raise JournalError(f"accepted receipt refused: {exc}") from exc
    row = ledger["accepted_work"]["active_rows"][0]
    return {
        "contract_id": row["contract_id"],
        "revision": row["revision"],
        "artifact_id": row["artifact_id"],
        "evaluation_id": row["evaluation_id"],
        "state": row["state"],
        "observed_at": row["observed_at"],
    }


def _receipt_identity(receipt: Mapping[str, str]) -> str:
    """The stable identity for a state receipt, excluding immutable payload."""
    return json.dumps(
        [
            receipt["contract_id"], receipt["revision"], receipt["artifact_id"],
            receipt["evaluation_id"], receipt["state"],
        ],
        separators=(",", ":"), ensure_ascii=True,
    )


def _canonical_receipt(receipt: Mapping[str, str]) -> str:
    return json.dumps(dict(receipt), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _normalise_receipts(receipts: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    if isinstance(receipts, (str, bytes)) or not isinstance(receipts, Sequence):
        raise JournalError("accepted receipts must be a sequence")
    return [_normalise_receipt(raw) for raw in receipts]


def _initialise(connection: sqlite3.Connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS journal_metadata (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
            schema_version INTEGER NOT NULL
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS accepted_receipts (
            receipt_identity TEXT PRIMARY KEY,
            canonical_json TEXT NOT NULL,
            contract_id TEXT NOT NULL,
            revision TEXT NOT NULL,
            artifact_id TEXT NOT NULL,
            evaluation_id TEXT NOT NULL,
            state TEXT NOT NULL CHECK (state IN ('accepted', 'reopened')),
            observed_at TEXT NOT NULL
        )"""
    )
    connection.execute(
        "INSERT OR IGNORE INTO journal_metadata(singleton, schema_version) VALUES (1, ?)",
        (DATABASE_SCHEMA_VERSION,),
    )
    version = connection.execute(
        "SELECT schema_version FROM journal_metadata WHERE singleton = 1"
    ).fetchone()
    if version != (DATABASE_SCHEMA_VERSION,):
        raise JournalError("unsupported journal database schema")


def _append_one(connection: sqlite3.Connection, receipt: Mapping[str, str]) -> str:
    identity = _receipt_identity(receipt)
    canonical = _canonical_receipt(receipt)
    existing = connection.execute(
        "SELECT canonical_json FROM accepted_receipts WHERE receipt_identity = ?", (identity,)
    ).fetchone()
    if existing is not None:
        if existing[0] == canonical:
            return "duplicate"
        raise JournalConflictError("conflicting immutable accepted-work receipt")
    connection.execute(
        """INSERT INTO accepted_receipts(
            receipt_identity, canonical_json, contract_id, revision, artifact_id,
            evaluation_id, state, observed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            identity, canonical, receipt["contract_id"], receipt["revision"],
            receipt["artifact_id"], receipt["evaluation_id"], receipt["state"],
            receipt["observed_at"],
        ),
    )
    return "appended"


def append_receipts(database: Path, receipts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Append validated receipts atomically; exact replays are no-ops."""
    normalised = _normalise_receipts(receipts)
    database = Path(database)
    if database.exists() and not database.is_file():
        raise JournalError("database path is not a regular file")
    if not normalised:
        return {"appended": 0, "duplicates": 0, "database_state": "unchanged_empty_append"}
    try:
        database.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(database), isolation_level=None)
    except sqlite3.Error as exc:
        raise JournalError("journal database cannot be opened for append") from exc
    try:
        connection.execute("BEGIN IMMEDIATE")
        _initialise(connection)
        appended = 0
        duplicates = 0
        for receipt in normalised:
            outcome = _append_one(connection, receipt)
            if outcome == "appended":
                appended += 1
            else:
                duplicates += 1
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()
    return {"appended": appended, "duplicates": duplicates, "database_state": "available"}


# The trailing NUL is written as bytes(1) so no source escape can be mangled.
SQLITE_MAGIC = b"SQLite format 3" + bytes(1)
SQLITE_HEADER_BYTES = 100
#: Header byte 18 is the write version: 1 = rollback journal, 2 = WAL.
_WRITE_VERSION_OFFSET = 18
_JOURNAL_MODES = {1: "rollback", 2: "wal"}
#: A dormant journal is small. Refusing an oversized file keeps the in-memory
#: snapshot bounded instead of trusting the file to be reasonable.
MAX_DATABASE_BYTES = 64 * 1024 * 1024


def _classify_database(path: Path) -> str:
    """Name the journal mode from the file header alone.

    Deliberately not ``PRAGMA journal_mode``: by the time a pragma can answer,
    SQLite has already opened the database and, for WAL, already created the
    -shm and -wal sidecars this function exists to avoid.
    """
    try:
        with path.open("rb") as source:
            header = source.read(SQLITE_HEADER_BYTES)
    except OSError as exc:
        raise JournalError("journal database cannot be read") from exc
    if len(header) < SQLITE_HEADER_BYTES or not header.startswith(SQLITE_MAGIC):
        raise JournalError("journal database cannot be read")
    mode = _JOURNAL_MODES.get(header[_WRITE_VERSION_OFFSET])
    if mode is None:
        raise JournalError("journal database cannot be read")
    return mode


def _refuse_unmerged_sidecars(database: Path) -> None:
    """Fail closed when the main file is not the whole committed database.

    A non-empty -wal may hold committed frames that are absent from the main
    file, and a hot -journal means the main file may be mid-transaction.
    Reading either coherently requires a writable open, so this refuses rather
    than silently returning an incomplete history. Ignoring them -- which is
    what immutable=1 alone would do -- would be worse than refusing, because
    the caller could not tell that committed receipts were dropped.
    """
    for suffix, reason in (
        ("-wal", "journal write-ahead log is unmerged; status cannot read it without writing"),
        ("-journal", "journal rollback file is hot; status cannot read it without writing"),
    ):
        sidecar = database.with_name(database.name + suffix)
        try:
            size = sidecar.stat().st_size
        except OSError:
            continue
        if size > 0:
            raise JournalError(reason)


def _identity(info: Any) -> tuple:
    return (info.st_size, info.st_mtime_ns, info.st_ino, info.st_dev)


def _snapshot_bytes(database: Path) -> bytes:
    """Copy the database image into memory, refusing if it moved mid-read."""
    try:
        before = database.stat()
    except OSError as exc:
        raise JournalError("journal database cannot be read") from exc
    if before.st_size > MAX_DATABASE_BYTES:
        raise JournalError("journal database exceeds the readable size bound")
    try:
        with database.open("rb") as source:
            image = source.read(MAX_DATABASE_BYTES + 1)
            after = os.fstat(source.fileno())
    except OSError as exc:
        raise JournalError("journal database cannot be read") from exc
    if len(image) > MAX_DATABASE_BYTES:
        raise JournalError("journal database exceeds the readable size bound")
    if _identity(after) != _identity(before) or len(image) != before.st_size:
        raise JournalError("journal database changed while it was being read")
    return image


def _as_rollback_image(image: bytes) -> bytes:
    """Label OUR IN-MEMORY COPY as rollback mode. The file is never touched.

    SQLite refuses to deserialize a WAL-mode image, because an in-memory
    database cannot run WAL. Rewriting header bytes 18 and 19 in the copy makes
    it loadable.

    This is only sound because ``_refuse_unmerged_sidecars`` has already
    established that no -wal and no hot -journal hold committed state, so the
    main image IS the complete committed database. Under that precondition the
    journal-mode label describes how a writer would behave, and there is no
    writer here. If the precondition were dropped this would silently discard
    committed frames, which is exactly the failure mode we are avoiding.
    """
    if len(image) < SQLITE_HEADER_BYTES:
        raise JournalError("journal database cannot be read")
    copy = bytearray(image)
    copy[_WRITE_VERSION_OFFSET] = 1
    copy[_WRITE_VERSION_OFFSET + 1] = 1
    return bytes(copy)


def _read_receipts(database: Path) -> list[dict[str, str]]:
    """Read receipts with ZERO filesystem writes, on every path.

    SQLite never opens the file. The image is copied into memory and attached
    to an in-memory database, so no journal, no -shm and no -wal can be created
    even for a WAL-mode database or an error path. A database whose committed
    state is not wholly inside the main file is refused instead.
    """
    if not database.exists():
        return []
    if not database.is_file():
        raise JournalError("database path is not a regular file")
    _classify_database(database)
    _refuse_unmerged_sidecars(database)
    image = _as_rollback_image(_snapshot_bytes(database))
    connection = sqlite3.connect(":memory:", isolation_level=None)
    try:
        connection.deserialize(image)
        connection.execute("PRAGMA query_only = ON")
        # Coherence check on the snapshot itself: a torn or damaged image must
        # not be reported as a short but valid history.
        check = connection.execute("PRAGMA quick_check(16)").fetchone()
        if not check or check[0] != "ok":
            raise JournalError("journal database failed its integrity check")
        version = connection.execute(
            "SELECT schema_version FROM journal_metadata WHERE singleton = 1"
        ).fetchone()
        if version != (DATABASE_SCHEMA_VERSION,):
            raise JournalError("unsupported journal database schema")
        rows = connection.execute(
            """SELECT contract_id, revision, artifact_id, evaluation_id, state, observed_at
            FROM accepted_receipts ORDER BY receipt_identity"""
        ).fetchall()
    except sqlite3.Error as exc:
        raise JournalError("journal database cannot be read") from exc
    finally:
        connection.close()
    fields = ("contract_id", "revision", "artifact_id", "evaluation_id", "state", "observed_at")
    return [dict(zip(fields, row, strict=True)) for row in rows]


def journal_report(database: Path) -> dict[str, Any]:
    """Build a deterministic ledger report without creating or modifying a DB."""
    database = Path(database)
    exists = database.exists()
    receipts = _read_receipts(database)
    ledger = build_report(
        {"schema": INPUT_SCHEMA, "usage_attempts": [], "accepted_work": receipts}
    )
    return {
        "schema": REPORT_SCHEMA,
        "journal": {
            "database_state": "available" if exists else "missing",
            "receipt_rows": len(receipts),
            "observation_scope": "journal_rows_only",
            "coverage_note": (
                "The journal contains only explicitly appended receipts and cannot "
                "establish that all accepted work is represented."
            ),
        },
        "ledger": ledger,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Append or read the dormant accepted-work SQLite journal.")
    subcommands = parser.add_subparsers(dest="command", required=True)
    append = subcommands.add_parser("append", help="atomically append explicit accepted-work receipts")
    append.add_argument("--database", required=True, type=Path)
    append.add_argument("--input", required=True, type=Path, help="strict journal input JSON")
    append.add_argument("--max-input-bytes", type=int, default=DEFAULT_MAX_INPUT_BYTES)
    status = subcommands.add_parser("status", help="read the journal without creating or modifying it")
    status.add_argument("--database", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "append":
            result = append_receipts(
                args.database,
                load_append_document(args.input, max_bytes=args.max_input_bytes),
            )
        else:
            result = journal_report(args.database)
    except JournalError as exc:
        print(f"journal operation refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
