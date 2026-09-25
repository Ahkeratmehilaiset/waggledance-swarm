#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Durable append-only storage for explicit accepted-work receipts.

Two APIs open a writable SQLite database: ``append``, and ``publish_pending``,
the recovery path that republishes a committed-but-unpublished state under a
fresh sequence. Only ``append`` can CREATE one. ``status``
never reads the database at all -- not its bytes, not through SQLite. It reads
the newest immutable SNAPSHOT that a writer published, verifies that snapshot's
full digest and embedded sequence, and loads those snapshot bytes into an
in-memory copy. It therefore cannot create a database, parent directory, or
sidecar journal, and cannot be affected by a concurrent writer. It turns stored receipt rows
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


class JournalUnavailable(JournalError):
    """The database could not be reached right now; retrying may succeed.

    A subclass, so every existing caller that catches JournalError keeps
    working, while a caller that can retry -- the CLI does, with its own exit
    code -- can tell "refused, do not retry" from "locked, come back".
    Contention used to leave the module as a raw sqlite3.OperationalError,
    which is neither this module's vocabulary nor something a caller expects.
    """


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
    # Inspect BEFORE CHANGING THE VERSION -- not before all SQL. The schema
    # objects above are created first, and the INSERT OR IGNORE cannot overwrite
    # an existing row, so a database claiming 999 keeps 999 and is refused here.
    # What must never happen unconditionally is WRITING the version: the earlier
    # code did, silently downgrading a database claiming 999 to 2.
    observed = connection.execute(
        "SELECT schema_version FROM journal_metadata WHERE singleton = 1").fetchone()
    if observed is None:
        raise JournalError("journal metadata row is missing")
    version = int(observed[0])
    if version == DATABASE_SCHEMA_VERSION:
        pass
    elif version == 1:
        # The ONLY migration, and only from the one version we understand.
        columns = {row[1] for row in connection.execute(
            "PRAGMA table_info(journal_metadata)").fetchall()}
        if "snapshot_seq" not in columns:
            connection.execute(
                "ALTER TABLE journal_metadata ADD COLUMN snapshot_seq INTEGER NOT NULL DEFAULT 0")
        connection.execute(
            "UPDATE journal_metadata SET schema_version = ? WHERE singleton = 1",
            (DATABASE_SCHEMA_VERSION,))
    else:
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
    # Resolved ONCE, before the transaction. Deriving it again after the commit
    # would put a computation on the one stretch of code that must not fail.
    directory = _snapshot_dir_for(database)
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
        if len(image) > MAX_DATABASE_BYTES:
            # Before the commit, so an unpublishable journal is refused rather
            # than committed into a state the reader can never see.
            raise JournalError("journal image exceeds the publishable size bound")
        _assert_retention_capacity(directory)
        connection.commit()
    except sqlite3.Error as exc:
        # Wrapping connect() alone was not enough: BEGIN IMMEDIATE and the
        # commit are where a concurrent writer's lock actually surfaces, past
        # the default busy timeout, and a raw OperationalError escaped from
        # there into every library caller.
        connection.rollback()
        raise JournalUnavailable(f"journal database is unavailable: {exc}") from exc
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()
    # Only now. A captured image from a rolled-back transaction contains rows
    # that were never committed, which is why publication cannot precede commit.
    # The append is committed. Nothing beyond this point may raise, because an
    # exception here would hide a durable commit behind what looks like a
    # failed append. Every publication problem becomes an observable outcome.
    try:
        publication, reason = _publish_snapshot(directory, sequence, image)
    except JournalError as exc:
        publication, reason = PUBLICATION_PENDING, f"publish_refused:{exc}"
    except OSError as exc:
        publication, reason = PUBLICATION_PENDING, f"publish_failed:{type(exc).__name__}"
    retention_warning = _prune_after_publish(directory, sequence, publication)
    result = {"appended": appended, "duplicates": duplicates,
              "database_state": "available", "snapshot_seq": sequence,
              "publication": publication}
    if reason:
        result["publication_reason"] = reason
    if retention_warning:
        result["retention_warning"] = retention_warning
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
#: How many PUBLISHED SNAPSHOTS may exist before a read is refused. This counts
#: only entries that parse as snapshots, because that is the quantity retention
#: actually governs.
MAX_SNAPSHOT_ENTRIES = 4096
#: How many directory entries of ANY kind may be walked before a read is
#: refused, so a flooded directory cannot turn a read into an unbounded job.
#:
#: These are two bounds on purpose. When one counter served both, any 4097
#: unrelated files dropped into the snapshot directory refused every read AND
#: every write permanently, with no in-module recovery: pruning also has to scan
#: first, and it deliberately never deletes a file it does not own.
#:
#: TRUST BOUNDARY, stated rather than assumed: the snapshot directory is owned
#: by this journal. Anyone able to create arbitrary files inside it can already
#: delete published snapshots, so a flood is a denial of service by a party that
#: already holds write access, not an integrity or confidentiality break. Above
#: this bound recovery is an OPERATOR action -- remove the foreign files --
#: because the alternative, letting the journal delete things it does not own,
#: is the guarantee that makes bounded retention safe in the first place.
MAX_SNAPSHOT_SCAN_ENTRIES = 65536
#: Soft prune target: how many snapshots a healthy journal settles at. It is a
#: GOAL, not a barrier. Only snapshots strictly older than the newest are ever
#: deleted, so reaching this bound cannot destroy the view a reader is on.
MAX_SNAPSHOT_RETAINED = 256
#: Tolerated overshoot above the target. Slack exists so a snapshot that cannot
#: be deleted right now -- a reader holds it, the filesystem refuses -- delays
#: pruning instead of blocking appends. The hard stop is target + slack.
SNAPSHOT_RETENTION_SLACK = 64

PUBLISHED = "published"
PUBLICATION_PENDING = "publication_pending"
ALREADY_PUBLISHED = "already_published"


def _allocate_sequence(connection: sqlite3.Connection) -> int:
    """Next sequence, allocated transactionally so writers cannot collide."""
    current = connection.execute(
        "SELECT snapshot_seq FROM journal_metadata WHERE singleton = 1").fetchone()
    nxt = int(current[0]) + 1 if current else 1
    connection.execute(
        "UPDATE journal_metadata SET snapshot_seq = ? WHERE singleton = 1", (nxt,))
    return nxt


def _journal_key(database: Path) -> str:
    """Stable identity for ONE journal file, derived from its absolute path.

    Snapshots used to live in a directory shared by every database beside them,
    so status for a missing b.sqlite3 happily returned a.sqlite3's receipts.
    Keying the directory binds a snapshot set to the database it describes
    without the reader having to open that database.
    """
    # resolve(), NOT absolute(). absolute() is lexical only: it leaves ../
    # segments and symlinks intact, so ONE physical database reached by two
    # equivalent spellings produced two separate journals, and status reported
    # "unavailable" for data that was genuinely committed and published under
    # the other spelling. Demonstrated empirically in the PR1728 review; the
    # docs claimed an unqualified binding the code did not deliver.
    #
    # resolve() collapses ../, follows links, returns the on-disk casing for a
    # file that EXISTS, and does not raise for one that does not.
    #
    # RESIDUAL, documented rather than implied away: for a database that does
    # not exist YET, resolve() cannot canonicalise the case of a missing
    # component, so on a case-insensitive filesystem two case spellings of the
    # same future file still produce two keys until that file exists. This key
    # hashes the STRING; Path.__eq__ on Windows is case-insensitive and would
    # hide it, which is why the regression tests compare derived keys and never
    # Path objects.
    #
    # THE TRADE ACCEPTED HERE: the key is no longer a pure function of the
    # string. It depends on filesystem state, so a junction or symlink created
    # or removed later can change the key for an unchanged spelling. That is
    # worse for reproducibility and better for truth, and an honest-but-wrong
    # "unavailable" for published data was the worse of the two failures.
    text = str(Path(database).resolve()).replace("\\", "/")
    if len(text) > 1 and text[1] == ":":
        text = text[0].upper() + text[1:]
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def _snapshot_dir_for(database: Path) -> Path:
    return Path(database).parent / SNAPSHOT_DIRNAME / _journal_key(database)


def snapshot_directory(database: Path) -> Path:
    """Where THIS database's snapshots live. Bound to the database, not shared."""
    return _snapshot_dir_for(database)


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
                if seen_count > MAX_SNAPSHOT_SCAN_ENTRIES:
                    raise JournalError(
                        "snapshot directory exceeds the scan bound; it holds "
                        "foreign entries this journal will not delete")
                match = SNAPSHOT_NAME.match(entry.name)
                if not match or not entry.is_file(follow_symlinks=False):
                    continue
                if len(found) >= MAX_SNAPSHOT_ENTRIES:
                    raise JournalError("snapshot directory exceeds the snapshot bound")
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


def _retention_ceiling() -> int:
    """The hard stop, DERIVED from the target so the two cannot drift apart.

    An independent absolute was worse than useless: raising the target above a
    fixed ceiling would have inverted the two levels silently, and lowering the
    target left the stop unreachable. Deriving it also means a caller that
    retunes retention retunes both levels at once.
    """
    return MAX_SNAPSHOT_RETAINED + SNAPSHOT_RETENTION_SLACK


def _prune_superseded(directory: Path, newest_sequence: int,
                      keep: int | None = None) -> int:
    """Delete SUPERSEDED snapshots, oldest first, never the newest.

    The earlier design deleted nothing, which was safe against destroying a
    reader's view but meant the retention bound stopped publication forever:
    appends kept committing while the reader fell permanently behind. Pruning
    only snapshots strictly older than ``newest_sequence`` cannot destroy the
    current view, and a reader holding an older file is protected by the
    filesystem, which refuses the delete (measured on Windows).

    ``keep`` resolves from the module constant HERE rather than in the
    signature. As a default argument it was bound once at import, so the
    constant looked like the live knob while nothing could actually retune it.

    The budget counts DELETIONS THAT SUCCEEDED, not candidates tried, so one
    undeletable file does not consume the whole pass and stall pruning behind
    it forever. Failures are tolerated and leave the file for a later attempt.

    Returns how many snapshots remain.
    """
    if keep is None:
        keep = MAX_SNAPSHOT_RETAINED
    entries = _snapshot_entries(directory)
    budget = len(entries) - keep
    for sequence, _, path in entries:
        if budget <= 0:
            break
        if sequence >= newest_sequence:
            break               # sorted: nothing further is superseded
        try:
            path.unlink()
        except OSError:
            continue            # held or refused; a later pass retries it
        budget -= 1
    return len(_snapshot_entries(directory))


def _prune_after_publish(directory: Path, sequence: int, publication: str) -> str | None:
    """Prune after a successful publish, and NEVER raise while doing it.

    This runs after the commit. Pruning reads the snapshot directory, and a
    directory scan can fail -- unreadable, flooded past MAX_SNAPSHOT_ENTRIES,
    two files claiming one sequence -- so the call that tidies up was able to
    throw over a publication that had already succeeded. The caller then saw an
    exception describing a state that was, in fact, correctly published and
    durable: the same "failure that hides a durable commit" this module refuses
    everywhere else, reintroduced by the cleanup step.

    Retention is housekeeping. Failing to reclaim space never invalidates what
    was published, so it is reported as a warning beside a truthful result and
    never as an exception or a rollback.
    """
    if publication != PUBLISHED:
        return None
    try:
        _prune_superseded(directory, sequence)
    except JournalError as exc:
        return f"prune_refused:{exc}"
    except OSError as exc:
        return f"prune_failed:{type(exc).__name__}"
    return None


def _assert_retention_capacity(directory: Path) -> None:
    """Refuse BEFORE committing only when growth is genuinely out of control.

    Two levels on purpose. The target is a prune goal: going over it is normal
    and is cleaned up after the next successful publish, so an undeletable file
    cannot block appends. The ceiling is the real stop -- reaching it means
    pruning has failed repeatedly, and refusing before the commit is better
    than committing into a state the reader can never see. Neither level ever
    produces indefinite silent lag: one prunes, the other refuses, and both are
    visible to the caller.

    The pre-commit prune passes the CURRENT newest sequence, so the snapshot a
    reader is on now is not a candidate. Nothing is deleted on behalf of an
    append that has not happened yet.
    """
    entries = _snapshot_entries(directory)
    ceiling = _retention_ceiling()
    if len(entries) < ceiling:
        return
    # Prune to the target, but never to a level that still leaves no room for
    # the append being gated. Aiming at the bare target refused an append that
    # pruning could in fact have made room for, whenever slack was zero.
    keep = max(0, min(MAX_SNAPSHOT_RETAINED, ceiling - 1))
    remaining = _prune_superseded(directory, entries[-1][0], keep)
    if remaining >= ceiling:
        raise JournalError(
            "snapshot retention is full and nothing could be pruned; "
            "publication would not be possible for this append")


#: What the published snapshot for a sequence actually IS, as opposed to what
#: the directory listing claims. "corrupt" is kept distinct from "absent" so
#: recovery can repair the journal without hiding the fact that a published,
#: immutable artefact went bad -- that is a failing disk or a tamper, and a
#: caller that only ever sees "published" would never learn of it.
VERIFIED, ABSENT, CORRUPT = "verified", "absent", "corrupt"


def _published_state(directory: Path, sequence: int) -> str:
    """Is sequence ACTUALLY published, verified, and readable?

    Membership in a directory listing is a filename claim. Recovery used to
    accept it and report already_published for a snapshot whose content had
    been corrupted, leaving the journal with no usable published state and no
    indication of it.
    """
    for entry_sequence, digest, path in _snapshot_entries(directory):
        if entry_sequence != sequence:
            continue
        try:
            _load_snapshot(path, digest, sequence)
        except JournalError:
            return CORRUPT
        return VERIFIED
    return ABSENT


def _verify_published(directory: Path, sequence: int) -> bool:
    """Boolean form of _published_state, for callers that only gate on it."""
    return _published_state(directory, sequence) == VERIFIED


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
        for existing_sequence, existing_digest, existing_path in _snapshot_entries(directory):
            if existing_sequence != sequence:
                continue
            if existing_digest != digest:
                raise JournalError("snapshot sequence already published with other content")
            # The NAME agrees with the image we were asked to publish. That is a
            # CLAIM about the file's bytes, and a corrupted file keeps its name:
            # replaying on the strength of the name alone reported published for
            # a sequence whose stored bytes no longer matched anything. Verify
            # the bytes on disk before calling this an idempotent success.
            _load_snapshot(existing_path, existing_digest, sequence)
            return PUBLISHED, None      # idempotent replay, bytes verified
        if len(_snapshot_entries(directory)) >= _retention_ceiling():
            # Only the HARD ceiling blocks publication. Refusing at the soft
            # prune target would defeat the whole redesign: it is the condition
            # that previously left publication stalled forever while appends
            # kept committing. Still an outcome here, never an exception.
            return PUBLICATION_PENDING, "retention_hard_ceiling_reached"
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


def _load_snapshot(path: Path, expected_digest: str, expected_sequence: int) -> list[dict[str, str]]:
    """Load one snapshot, verifying its content AND its claimed identity.

    The filename is an untrusted label. Renaming a valid snapshot from sequence
    1 to 99 preserves the digest, so the digest alone cannot establish which
    state this image is. The sequence embedded in the image is what settles it.
    """
    try:
        info = path.stat()
        if info.st_size > MAX_DATABASE_BYTES:
            raise JournalError("snapshot exceeds the readable size bound")
        with path.open("rb") as handle:
            # Bounded: a stat followed by an unbounded read_bytes() would let a
            # file that grew after the stat be slurped in full.
            image = handle.read(MAX_DATABASE_BYTES + 1)
    except JournalError:
        raise
    except OSError as exc:
        raise JournalError("snapshot cannot be read") from exc
    if len(image) > MAX_DATABASE_BYTES:
        raise JournalError("snapshot exceeds the readable size bound")
    if hashlib.sha256(image).hexdigest() != expected_digest:
        raise JournalError("snapshot content does not match its digest")
    connection = sqlite3.connect(":memory:", isolation_level=None)
    try:
        connection.deserialize(image)
        connection.execute("PRAGMA query_only = ON")
        check = connection.execute("PRAGMA quick_check(16)").fetchone()
        if not check or check[0] != "ok":
            raise JournalError("snapshot failed its integrity check")
        # Version first, on its own, so a snapshot from an older schema that
        # lacks snapshot_seq reports "unsupported" rather than an opaque
        # "cannot be read" from a missing column.
        version = connection.execute(
            "SELECT schema_version FROM journal_metadata WHERE singleton = 1").fetchone()
        if not version or int(version[0]) != DATABASE_SCHEMA_VERSION:
            raise JournalError("unsupported snapshot schema")
        embedded = connection.execute(
            "SELECT snapshot_seq FROM journal_metadata WHERE singleton = 1").fetchone()
        if not embedded or int(embedded[0]) != expected_sequence:
            raise JournalError("snapshot sequence does not match its embedded sequence")
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
    receipts = _load_snapshot(path, digest, sequence)
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
    re-reading under the old one, because a post-commit read could contain
    another writer's rows.

    The append path's protections apply here too, because recovery is a writer:
    the current sequence is only treated as published when the snapshot for it
    actually VERIFIES, the image is size-checked BEFORE the commit, and a
    publication problem after the commit is an outcome rather than an exception.

    A corrupted snapshot is REPAIRED rather than raised on, because this is the
    recovery entry point and raising here would make the one operation whose
    job is to restore a readable journal the one that cannot. The committed
    database is the authority; a fresh sequence republishes it. The repair is
    reported as ``recovered_from`` so corruption never passes as routine.
    """
    database = Path(database)
    if not database.exists():
        return {"publication": PUBLICATION_PENDING, "reason": "database_missing"}
    directory = _snapshot_dir_for(database)
    try:
        connection = sqlite3.connect(str(database), isolation_level=None)
    except sqlite3.Error as exc:
        raise JournalError("journal database cannot be opened for append") from exc
    try:
        connection.execute("BEGIN IMMEDIATE")
        _initialise(connection)
        current = connection.execute(
            "SELECT snapshot_seq FROM journal_metadata WHERE singleton = 1").fetchone()
        # Verified, not merely listed. A corrupted snapshot for this sequence
        # must NOT be reported as already published.
        published_seq = int(current[0]) if current else 0
        state = _published_state(directory, published_seq) if current else ABSENT
        if state == VERIFIED:
            connection.rollback()
            return {"publication": ALREADY_PUBLISHED, "snapshot_seq": published_seq}
        sequence = _allocate_sequence(connection)
        image = connection.serialize()
        if len(image) > MAX_DATABASE_BYTES:
            raise JournalError("journal image exceeds the publishable size bound")
        _assert_retention_capacity(directory)
        connection.commit()
    except sqlite3.Error as exc:
        # Recovery is a writer too and contends for the same lock.
        connection.rollback()
        raise JournalUnavailable(f"journal database is unavailable: {exc}") from exc
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()
    # Committed. Nothing beyond here may raise, for the same reason as append.
    try:
        publication, reason = _publish_snapshot(directory, sequence, image)
    except JournalError as exc:
        publication, reason = PUBLICATION_PENDING, f"publish_refused:{exc}"
    except OSError as exc:
        publication, reason = PUBLICATION_PENDING, f"publish_failed:{type(exc).__name__}"
    retention_warning = _prune_after_publish(directory, sequence, publication)
    result = {"publication": publication, "snapshot_seq": sequence}
    if retention_warning:
        result["retention_warning"] = retention_warning
    if state == CORRUPT:
        # Recovery repairs this, but never silently: an immutable artefact that
        # stopped verifying is evidence of a failing disk or a tamper, and a
        # plain "published" would bury it under an ordinary-looking success.
        result["recovered_from"] = f"corrupt_snapshot:{published_seq}"
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
    except JournalUnavailable as exc:
        # Before JournalError, which it subclasses: contention is an expected
        # operational condition for a CLI, not a refusal and not a crash.
        print(f"journal database unavailable: {exc}", file=sys.stderr)
        return 3
    except JournalError as exc:
        print(f"journal operation refused: {exc}", file=sys.stderr)
        return 2
    except sqlite3.Error as exc:
        # Backstop that should be unreachable now that both writers convert.
        # Kept so any path that ever forgets degrades to an exit code instead
        # of a traceback; if this fires, the conversion above has a hole.
        print(f"journal database unavailable: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 3
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
