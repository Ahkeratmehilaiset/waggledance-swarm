#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Durable append-only storage for explicit accepted-work receipts.

Only the explicit ``append`` API opens a writable SQLite database. ``status``
never opens the database with SQLite at all: it reads the file bytes and loads
an in-memory copy, so it cannot create a database, parent directory, or sidecar
journal. It turns stored receipt rows
into the pure report from ``tools.bridge_work_ledger``; it is not runtime
wiring or a collector.
"""
from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
import hashlib
import json
import os
from pathlib import Path
import re
import uuid
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
DATABASE_SCHEMA_VERSION = 2


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
            schema_version INTEGER NOT NULL,
            snapshot_seq INTEGER NOT NULL DEFAULT 0
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
    columns = {row[1] for row in connection.execute(
        "PRAGMA table_info(journal_metadata)").fetchall()}
    if "snapshot_seq" not in columns:
        # Writer-side migration, inside the append transaction, so a v1 database
        # is upgraded exactly once and only by a process entitled to write.
        connection.execute(
            "ALTER TABLE journal_metadata ADD COLUMN snapshot_seq INTEGER NOT NULL DEFAULT 0")
    connection.execute(
        "UPDATE journal_metadata SET schema_version = ? WHERE singleton = 1",
        (DATABASE_SCHEMA_VERSION,))
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
        # Allocate the sequence and capture the image INSIDE this transaction.
        # A fresh post-commit read could contain another writer's rows under a
        # sequence we allocated, so the image must come from here, where it is
        # exactly the state this transaction is about to commit. Verified: an
        # in-transaction serialize() includes the pending rows.
        sequence = _allocate_sequence(connection)
        image = connection.serialize()
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()
    # Only now. A captured image from a rolled-back transaction contains rows
    # that were never committed, which is why publication cannot precede commit.
    publication, reason = _publish_snapshot(_snapshot_dir_for(database), sequence, image)
    result = {"appended": appended, "duplicates": duplicates,
              "database_state": "available", "snapshot_seq": sequence,
              "publication": publication}
    if reason:
        result["publication_reason"] = reason
    return result


# The trailing NUL is written as bytes(1) so no source escape can be mangled.
SQLITE_MAGIC = b"SQLite format 3" + bytes(1)
SQLITE_HEADER_BYTES = 100
#: Header byte 18 is the write version: 1 = rollback journal, 2 = WAL.
_WRITE_VERSION_OFFSET = 18
_JOURNAL_MODES = {1: "rollback", 2: "wal"}
#: A dormant journal is small. Refusing an oversized file keeps the in-memory
#: snapshot bounded instead of trusting the file to be reasonable.
MAX_DATABASE_BYTES = 64 * 1024 * 1024

#: Published snapshots live beside the database. Names carry the FULL sha256 of
#: the image, so a reader verifies content against the name with no side channel.
SNAPSHOT_DIRNAME = "snapshots"
SNAPSHOT_NAME = re.compile(r"^snap-(\d{12})-([0-9a-f]{64})\.journal$")
#: Bounded scan: a directory with more entries than this is refused rather than
#: walked, so a flooded directory cannot turn a read into an unbounded job.
MAX_SNAPSHOT_ENTRIES = 4096
#: Retention bound. Reaching it makes publication report publication_pending.
#: Nothing is ever deleted automatically: silent deletion of an immutable
#: artefact is a worse failure than a visible refusal to publish.
MAX_SNAPSHOT_RETAINED = 256

PUBLISHED = "published"
PUBLICATION_PENDING = "publication_pending"
ALREADY_PUBLISHED = "already_published"


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


SIDECAR_SUFFIXES = ("-wal", "-journal")


def _sidecar_signature(database: Path) -> tuple:
    """Observe both sidecars, or refuse because their state is unknown.

    Only FileNotFoundError means "absent". Every other OSError -- a permission
    denial, an IO error, a path that stopped being a directory -- leaves the
    sidecar state UNKNOWN, and unknown must not be read as "no WAL". The
    previous version caught OSError broadly and continued, so a stat that was
    merely denied looked exactly like a database with no unmerged frames.
    """
    observed = []
    for suffix in SIDECAR_SUFFIXES:
        sidecar = database.with_name(database.name + suffix)
        try:
            info = sidecar.stat()
        except FileNotFoundError:
            observed.append((suffix, None))
        except OSError as exc:
            raise JournalError(
                "journal sidecar state cannot be determined") from exc
        else:
            observed.append(
                (suffix, (info.st_size, info.st_mtime_ns, info.st_ino, info.st_dev)))
    return tuple(observed)


def _refuse_unmerged_sidecars(signature: tuple) -> None:
    """Fail closed when the main file is not the whole committed database.

    A non-empty -wal may hold committed frames that are absent from the main
    file, and a hot -journal means the main file may be mid-transaction.
    Reading either coherently requires a writable open, so this refuses rather
    than silently returning an incomplete history. Ignoring them -- which is
    what immutable=1 alone would do -- would be worse than refusing, because
    the caller could not tell that committed receipts were dropped.
    """
    reasons = {
        "-wal": "journal write-ahead log is unmerged; status cannot read it without writing",
        "-journal": "journal rollback file is hot; status cannot read it without writing",
    }
    for suffix, info in signature:
        if info is not None and info[0] > 0:
            raise JournalError(reasons[suffix])


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

    This is only sound because no unmerged -wal and no hot -journal were
    OBSERVED across the read, so the main image is not known to be missing
    committed frames. That is an observation, not a guarantee: a writer that
    created, committed, checkpointed and removed a -wal between the two
    observations would defeat it. See the concurrency limits in
    docs/BRIDGE_WORK_JOURNAL.md. If the observation were dropped entirely this
    would silently discard committed frames, which is the failure being
    avoided.
    """
    if len(image) < SQLITE_HEADER_BYTES:
        raise JournalError("journal database cannot be read")
    copy = bytearray(image)
    copy[_WRITE_VERSION_OFFSET] = 1
    copy[_WRITE_VERSION_OFFSET + 1] = 1
    return bytes(copy)


def _allocate_sequence(connection: sqlite3.Connection) -> int:
    """Next sequence, allocated transactionally so writers cannot collide."""
    current = connection.execute(
        "SELECT snapshot_seq FROM journal_metadata WHERE singleton = 1").fetchone()
    nxt = int(current[0]) + 1 if current else 1
    connection.execute(
        "UPDATE journal_metadata SET snapshot_seq = ? WHERE singleton = 1", (nxt,))
    return nxt


def _snapshot_dir_for(database: Path) -> Path:
    return Path(database).parent / SNAPSHOT_DIRNAME


def snapshot_directory(database: Path) -> Path:
    return Path(database).parent / SNAPSHOT_DIRNAME


def _snapshot_entries(directory: Path) -> list[tuple[int, str, Path]]:
    """Bounded scan of published snapshots, refusing duplicates and floods."""
    if not directory.exists():
        return []
    if not directory.is_dir():
        raise JournalError("snapshot path is not a directory")
    found: list[tuple[int, str, Path]] = []
    seen: set[int] = set()
    seen_count = 0
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                seen_count += 1
                if seen_count > MAX_SNAPSHOT_ENTRIES:
                    raise JournalError("snapshot directory exceeds the scan bound")
                match = SNAPSHOT_NAME.match(entry.name)
                if not match or not entry.is_file(follow_symlinks=False):
                    continue
                sequence = int(match.group(1))
                if sequence in seen:
                    # Two files claiming the same sequence means the history is
                    # ambiguous. Choosing one would be inventing an answer.
                    raise JournalError("duplicate snapshot sequence")
                seen.add(sequence)
                found.append((sequence, match.group(2), Path(entry.path)))
    except OSError as exc:
        raise JournalError("snapshot directory cannot be read") from exc
    return sorted(found)


def _publish_snapshot(directory: Path, sequence: int, image: bytes) -> tuple[str, str | None]:
    """Write an immutable snapshot, or report why publication did not happen.

    Never replaces an existing file: os.replace onto a live target fails on
    Windows while a reader holds it open (measured). The target name is new by
    construction, and replacing onto a non-existent name is atomic.
    """
    digest = hashlib.sha256(image).hexdigest()
    target = directory / f"snap-{sequence:012d}-{digest}.journal"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        # Check the SEQUENCE, not just this exact name. Comparing only the
        # target name let a second file claim the same sequence with different
        # content, manufacturing precisely the ambiguity the reader refuses.
        for existing_sequence, existing_digest, _ in _snapshot_entries(directory):
            if existing_sequence != sequence:
                continue
            if existing_digest == digest:
                return PUBLISHED, None      # idempotent replay
            raise JournalError("snapshot sequence already published with other content")
        if len(_snapshot_entries(directory)) >= MAX_SNAPSHOT_RETAINED:
            return PUBLICATION_PENDING, "retention_bound_reached"
        temporary = directory / f".publish-{uuid.uuid4().hex}.tmp"
        with temporary.open("wb") as handle:
            handle.write(image)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except JournalError:
        raise
    except OSError as exc:
        return PUBLICATION_PENDING, f"publish_failed:{type(exc).__name__}"
    return PUBLISHED, None


def _read_receipts(database: Path) -> tuple[str, list[dict[str, str]]]:
    """Observe availability ONCE and read receipts, with zero filesystem writes.

    Returns ``(state, rows)``. The caller must not take its own view of whether
    the database exists: two separate observations could disagree, and they did.
    A cached ``exists=True`` in the caller combined with a later absence here
    produced the report "available, 0 receipts" for a database that had been
    deleted -- a confidently wrong answer, which is worse than an error.

    SQLite never opens the file. The image is copied into memory and attached
    to an in-memory database, so no journal, no -shm and no -wal can be created
    even for a WAL-mode database or an error path. A database whose committed
    state is not wholly inside the main file is refused instead.
    """
    if not database.exists():
        return "missing", []
    if not database.is_file():
        raise JournalError("database path is not a regular file")
    # From here the database was observed to exist. If it disappears while we
    # are reading, every step below raises rather than degrading to an empty
    # list, so a vanished database can never be reported as an empty one.
    _classify_database(database)
    before_sidecars = _sidecar_signature(database)
    _refuse_unmerged_sidecars(before_sidecars)
    image = _as_rollback_image(_snapshot_bytes(database))
    # A writer can create or grow a -wal between the check above and the read.
    # Re-observing turns that race from a silently short history into a refusal.
    # This DETECTS the window; it does not close it. See the limitations note in
    # docs/BRIDGE_WORK_JOURNAL.md.
    after_sidecars = _sidecar_signature(database)
    _refuse_unmerged_sidecars(after_sidecars)
    if after_sidecars != before_sidecars:
        raise JournalError("journal sidecar state changed while it was being read")
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
    return "available", [dict(zip(fields, row, strict=True)) for row in rows]


def _load_snapshot(path: Path, expected_digest: str) -> list[dict[str, str]]:
    """Load one immutable snapshot, verifying content against its own name."""
    try:
        info = path.stat()
        if info.st_size > MAX_DATABASE_BYTES:
            raise JournalError("snapshot exceeds the readable size bound")
        image = path.read_bytes()
    except JournalError:
        raise
    except OSError as exc:
        raise JournalError("snapshot cannot be read") from exc
    if hashlib.sha256(image).hexdigest() != expected_digest:
        raise JournalError("snapshot content does not match its digest")
    connection = sqlite3.connect(":memory:", isolation_level=None)
    try:
        connection.deserialize(image)
        connection.execute("PRAGMA query_only = ON")
        check = connection.execute("PRAGMA quick_check(16)").fetchone()
        if not check or check[0] != "ok":
            raise JournalError("snapshot failed its integrity check")
        version = connection.execute(
            "SELECT schema_version FROM journal_metadata WHERE singleton = 1").fetchone()
        if version != (DATABASE_SCHEMA_VERSION,):
            raise JournalError("unsupported snapshot schema")
        rows = connection.execute(
            """SELECT contract_id, revision, artifact_id, evaluation_id, state, observed_at
            FROM accepted_receipts ORDER BY receipt_identity""").fetchall()
    except JournalError:
        raise
    except sqlite3.Error as exc:
        raise JournalError("snapshot cannot be read") from exc
    finally:
        connection.close()
    fields = ("contract_id", "revision", "artifact_id", "evaluation_id", "state", "observed_at")
    return [dict(zip(fields, row, strict=True)) for row in rows]


def journal_report(database: Path) -> dict[str, Any]:
    """Report from the newest PUBLISHED SNAPSHOT only. Never opens the database.

    There is deliberately no live-database fallback. Reading the database would
    reintroduce the non-atomic observation this design exists to remove, and
    reporting an empty journal when no snapshot exists would be a silent wrong
    answer. With no snapshot the state is ``unavailable`` and rows are ``None``.

    ``latest_committed_state`` is always ``"unknown"``: this reader never opens
    the database, so it cannot know whether a commit has happened that is not
    yet published. An old snapshot after a commit-before-publish crash is NOT
    current authority, and this field is how a caller is told so.
    """
    database = Path(database)
    entries = _snapshot_entries(_snapshot_dir_for(database))
    if not entries:
        return {
            "schema": REPORT_SCHEMA,
            "journal": {
                "database_state": "unavailable",
                "receipt_rows": None,
                "snapshot_seq": None,
                "snapshot_as_of": None,
                "latest_committed_state": "unknown",
                "observation_scope": "published_snapshots_only",
                "coverage_note": (
                    "No published snapshot exists. This is not an empty journal: "
                    "the journal state is unknown and no database was read."
                ),
            },
            "ledger": None,
        }
    sequence, digest, path = entries[-1]
    receipts = _load_snapshot(path, digest)
    ledger = build_report(
        {"schema": INPUT_SCHEMA, "usage_attempts": [], "accepted_work": receipts})
    return {
        "schema": REPORT_SCHEMA,
        "journal": {
            "database_state": "available",
            "receipt_rows": len(receipts),
            "snapshot_seq": sequence,
            # Derived from the receipts themselves, never from a clock.
            "snapshot_as_of": max((r["observed_at"] for r in receipts), default=None),
            "latest_committed_state": "unknown",
            "observation_scope": "published_snapshots_only",
            "coverage_note": (
                "The snapshot contains only explicitly appended receipts, and a "
                "newer commit may exist that has not been published. This report "
                "is not evidence of the latest committed state."
            ),
        },
        "ledger": ledger,
    }


def publish_pending(database: Path) -> dict[str, Any]:
    """Recover publication after a commit-before-publish crash. Idempotent.

    Appends nothing, so it cannot duplicate a receipt. It allocates a NEW
    sequence and captures a NEW image in its own transaction rather than
    re-reading under the old sequence, because a post-commit read could contain
    another writer's rows.
    """
    database = Path(database)
    if not database.exists():
        return {"publication": PUBLICATION_PENDING, "reason": "database_missing"}
    directory = _snapshot_dir_for(database)
    published = {sequence for sequence, _, _ in _snapshot_entries(directory)}
    try:
        connection = sqlite3.connect(str(database), isolation_level=None)
    except sqlite3.Error as exc:
        raise JournalError("journal database cannot be opened for append") from exc
    try:
        connection.execute("BEGIN IMMEDIATE")
        _initialise(connection)
        current = connection.execute(
            "SELECT snapshot_seq FROM journal_metadata WHERE singleton = 1").fetchone()
        if current and int(current[0]) in published:
            connection.rollback()
            return {"publication": ALREADY_PUBLISHED, "snapshot_seq": int(current[0])}
        sequence = _allocate_sequence(connection)
        image = connection.serialize()
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()
    publication, reason = _publish_snapshot(directory, sequence, image)
    result = {"publication": publication, "snapshot_seq": sequence}
    if reason:
        result["reason"] = reason
    return result


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
