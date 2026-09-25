# SPDX-License-Identifier: BUSL-1.1
"""Acceptance tests for the dormant append-only accepted-work journal."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import os
import sqlite3
import subprocess
import sys

import pytest

from tools import bridge_work_journal as journal


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "bridge_work_journal.py"


def _receipt(**over):
    result = {
        "contract_id": "contract-a",
        "revision": "1",
        "artifact_id": "artifact-a",
        "evaluation_id": "evaluation-a",
        "state": "accepted",
        "observed_at": "2026-09-25T08:00:00Z",
    }
    result.update(over)
    return result


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_append_is_durable_and_report_consumes_the_pure_ledger(tmp_path):
    database = tmp_path / "journal.sqlite3"
    result = journal.append_receipts(database, [_receipt()])
    assert result == {"appended": 1, "duplicates": 0, "database_state": "available"}
    reopened = _receipt(state="reopened", observed_at="2026-09-25T08:01:00Z")
    assert journal.append_receipts(database, [reopened])["appended"] == 1

    report = journal.journal_report(database)
    assert report["schema"] == journal.REPORT_SCHEMA
    assert report["journal"]["database_state"] == "available"
    assert report["journal"]["receipt_rows"] == 2
    assert report["ledger"]["accepted_work"]["active_accepted_contract_revisions"] == 0
    assert report["ledger"]["accepted_work"]["active_reopened_contract_revisions"] == 1
    assert report["ledger"]["money"]["observed_by_currency"] is None


def test_exact_duplicate_is_idempotent_but_changed_immutable_receipt_is_refused(tmp_path):
    database = tmp_path / "journal.sqlite3"
    receipt = _receipt()
    journal.append_receipts(database, [receipt])
    assert journal.append_receipts(database, [receipt])["duplicates"] == 1
    before = _digest(database)

    with pytest.raises(journal.JournalConflictError, match="conflicting immutable"):
        journal.append_receipts(database, [_receipt(observed_at="2026-09-25T08:02:00Z")])

    assert _digest(database) == before
    assert journal.journal_report(database)["journal"]["receipt_rows"] == 1


def test_reordered_append_cannot_resurrect_a_later_reopened_revision(tmp_path):
    database = tmp_path / "journal.sqlite3"
    later_reopen = _receipt(state="reopened", observed_at="2026-09-25T08:02:00Z")
    earlier_acceptance = _receipt(observed_at="2026-09-25T08:01:00Z")
    journal.append_receipts(database, [later_reopen])
    journal.append_receipts(database, [earlier_acceptance])

    accepted = journal.journal_report(database)["ledger"]["accepted_work"]
    assert accepted["active_accepted_contract_revisions"] == 0
    assert accepted["active_reopened_contract_revisions"] == 1
    assert accepted["active_rows"][0]["state"] == "reopened"


def test_conflict_rolls_back_a_whole_append_batch(tmp_path):
    database = tmp_path / "journal.sqlite3"
    journal.append_receipts(database, [_receipt()])
    new_receipt = _receipt(artifact_id="artifact-new", evaluation_id="evaluation-new")
    conflicting = _receipt(observed_at="2026-09-25T08:03:00Z")

    with pytest.raises(journal.JournalConflictError):
        journal.append_receipts(database, [new_receipt, conflicting])

    report = journal.journal_report(database)
    assert report["journal"]["receipt_rows"] == 1
    assert report["ledger"]["accepted_work"]["active_rows"][0]["artifact_id"] == "artifact-a"


def test_simulated_crash_rolls_back_and_reopen_has_no_partial_rows(tmp_path, monkeypatch):
    database = tmp_path / "journal.sqlite3"
    journal.append_receipts(database, [_receipt()])
    original = journal._append_one

    def interrupted(connection, receipt):
        outcome = original(connection, receipt)
        if receipt["artifact_id"] == "artifact-crash":
            raise RuntimeError("simulated interruption")
        return outcome

    monkeypatch.setattr(journal, "_append_one", interrupted)
    with pytest.raises(RuntimeError, match="simulated interruption"):
        journal.append_receipts(
            database,
            [_receipt(artifact_id="artifact-crash", evaluation_id="evaluation-crash")],
        )

    assert journal.journal_report(database)["journal"]["receipt_rows"] == 1
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM accepted_receipts").fetchone() == (1,)


def test_status_never_creates_database_for_missing_or_read_errors(tmp_path):
    missing = tmp_path / "missing-parent" / "journal.sqlite3"
    report = journal.journal_report(missing)
    assert report["journal"]["database_state"] == "missing"
    assert not missing.parent.exists()

    invalid = tmp_path / "not-a-sqlite-file"
    invalid.write_bytes(b"not a sqlite database")
    before = _digest(invalid)
    with pytest.raises(journal.JournalError, match="journal database cannot be read"):
        journal.journal_report(invalid)
    assert _digest(invalid) == before
    assert not list(tmp_path.glob("not-a-sqlite-file-*"))


def test_status_is_read_only_for_an_existing_database(tmp_path):
    database = tmp_path / "journal.sqlite3"
    journal.append_receipts(database, [_receipt()])
    before = {path.name: _digest(path) for path in tmp_path.iterdir()}
    report = journal.journal_report(database)
    after = {path.name: _digest(path) for path in tmp_path.iterdir()}
    assert report["journal"]["database_state"] == "available"
    assert after == before


def test_status_does_not_create_sidecars_for_closed_wal_database(tmp_path):
    database = tmp_path / "wal.sqlite3"
    journal.append_receipts(database, [_receipt()])
    connection = sqlite3.connect(database)
    assert connection.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
    connection.close()
    before = {path.name: _digest(path) for path in tmp_path.iterdir()}
    journal.journal_report(database)
    after = {path.name: _digest(path) for path in tmp_path.iterdir()}
    assert after == before


def test_empty_append_validates_without_creating_a_database(tmp_path):
    database = tmp_path / "new-parent" / "journal.sqlite3"
    assert journal.append_receipts(database, []) == {
        "appended": 0,
        "duplicates": 0,
        "database_state": "unchanged_empty_append",
    }
    assert not database.parent.exists()


def test_report_explicitly_limits_unknown_coverage_to_persisted_rows(tmp_path):
    report = journal.journal_report(tmp_path / "absent.sqlite3")
    assert report["journal"]["observation_scope"] == "journal_rows_only"
    assert "cannot establish that all accepted work is represented" in report["journal"]["coverage_note"]
    assert report["ledger"]["accepted_work"]["active_accepted_contract_revisions"] == 0


def test_cli_append_then_status(tmp_path):
    database = tmp_path / "journal.sqlite3"
    source = tmp_path / "append.json"
    source.write_text(
        json.dumps({"schema": journal.APPEND_SCHEMA, "accepted_work": [_receipt()]}),
        encoding="utf-8",
    )
    append = subprocess.run(
        [sys.executable, str(SCRIPT), "append", "--database", str(database), "--input", str(source)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert append.returncode == 0, append.stderr
    assert json.loads(append.stdout)["appended"] == 1
    status = subprocess.run(
        [sys.executable, str(SCRIPT), "status", "--database", str(database)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert status.returncode == 0, status.stderr
    assert json.loads(status.stdout)["journal"]["receipt_rows"] == 1


# --- fable-5 regressions for the WAL status fix -------------------------------
# The Lead RED test above is preserved verbatim; everything below is additional.


def _tree(directory):
    """Every file in the directory with its digest, for zero-write assertions."""
    return {path.name: _digest(path) for path in sorted(directory.iterdir())}


def _wal_database(tmp_path, name="wal.sqlite3"):
    database = tmp_path / name
    journal.append_receipts(database, [_receipt()])
    connection = sqlite3.connect(database)
    assert connection.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
    connection.close()
    return database


def test_closed_wal_database_is_read_correctly_not_merely_silently(tmp_path):
    """Reading must still return the rows, not quietly report an empty journal."""
    database = _wal_database(tmp_path)
    report = journal.journal_report(database)
    assert report["journal"]["database_state"] == "available"
    assert report["journal"]["receipt_rows"] == 1


def test_status_never_rewrites_the_on_disk_journal_mode(tmp_path):
    """The rollback relabelling happens in memory only."""
    database = _wal_database(tmp_path)
    before = database.read_bytes()[18:20]
    journal.journal_report(database)
    assert database.read_bytes()[18:20] == before == bytes([2, 2])


def test_unmerged_wal_content_is_refused_not_silently_dropped(tmp_path):
    """The case immutable=1 would get wrong: committed frames still in the -wal.

    A reader that ignored the -wal would return a SHORTER history and look
    successful, which is worse than refusing because the caller cannot tell.
    """
    database = _wal_database(tmp_path)
    holder = sqlite3.connect(database)
    holder.execute("PRAGMA journal_mode=WAL")
    holder.execute("BEGIN IMMEDIATE")
    holder.execute(
        """INSERT INTO accepted_receipts(receipt_identity, canonical_json, contract_id,
           revision, artifact_id, evaluation_id, state, observed_at)
           VALUES ('x','{}','c9','1','a9','e9','accepted','2026-09-25T00:00:00+00:00')""")
    holder.commit()
    try:
        assert (database.parent / (database.name + "-wal")).stat().st_size > 0
        before = _tree(tmp_path)
        with pytest.raises(journal.JournalError, match="write-ahead log is unmerged"):
            journal.journal_report(database)
        assert _tree(tmp_path) == before
    finally:
        holder.close()


def test_hot_rollback_journal_is_refused_with_zero_writes(tmp_path):
    database = tmp_path / "hot.sqlite3"
    journal.append_receipts(database, [_receipt()])
    (tmp_path / "hot.sqlite3-journal").write_bytes(b"\xd9\xd5\x05\xf9 \xa1c\xd7" + bytes(64))
    before = _tree(tmp_path)
    with pytest.raises(journal.JournalError, match="rollback file is hot"):
        journal.journal_report(database)
    assert _tree(tmp_path) == before


def test_an_empty_sidecar_does_not_block_a_readable_database(tmp_path):
    """Only a NON-empty sidecar means unmerged state; a stale empty one must not."""
    database = tmp_path / "empty-sidecar.sqlite3"
    journal.append_receipts(database, [_receipt()])
    (tmp_path / "empty-sidecar.sqlite3-wal").write_bytes(b"")
    before = _tree(tmp_path)
    assert journal.journal_report(database)["journal"]["receipt_rows"] == 1
    assert _tree(tmp_path) == before


@pytest.mark.parametrize("prepare,match", [
    (lambda p: p.write_bytes(b"not a sqlite database"), "cannot be read"),
    (lambda p: p.write_bytes(b"SQLite format 3" + bytes(1) + bytes(40)), "cannot be read"),
    (lambda p: p.write_bytes(b""), "cannot be read"),
])
def test_every_error_path_writes_nothing_at_all(tmp_path, prepare, match):
    """Zero filesystem writes is claimed for errors too, so it is tested there."""
    target = tmp_path / "broken.sqlite3"
    prepare(target)
    before = _tree(tmp_path)
    with pytest.raises(journal.JournalError, match=match):
        journal.journal_report(target)
    assert _tree(tmp_path) == before
    assert not list(tmp_path.glob("broken.sqlite3-*"))


def test_a_damaged_image_fails_the_integrity_check(tmp_path):
    """Damage the SELECT would not notice must still be refused.

    Zeroing a page the query touches raises DatabaseError on its own, so that
    would not prove the integrity check does anything. Zeroing a trailing page
    leaves the query answering "1 row" happily while quick_check reports the
    corruption -- that is the case this test exists for, and removing the
    quick_check makes it fail.
    """
    database = tmp_path / "damaged.sqlite3"
    journal.append_receipts(database, [_receipt()])
    image = bytearray(database.read_bytes())
    image[-64:] = bytes(64)
    database.write_bytes(bytes(image))
    before = _tree(tmp_path)
    with pytest.raises(journal.JournalError, match="integrity check"):
        journal.journal_report(database)
    assert _tree(tmp_path) == before


def test_an_oversized_database_is_refused_before_it_is_loaded(tmp_path, monkeypatch):
    """"Before it is loaded" is asserted, not just asserted-in-the-name.

    The stat-based bound must reject without ever opening the file for its
    contents, so the test watches Path.open and requires it was never used to
    slurp the image.
    """
    database = tmp_path / "big.sqlite3"
    journal.append_receipts(database, [_receipt()])
    monkeypatch.setattr(journal, "MAX_DATABASE_BYTES", 16)
    opened = []
    real_open = Path.open

    def watched(self, *args, **kwargs):
        opened.append(self.name)
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", watched)
    with pytest.raises(journal.JournalError, match="exceeds the readable size bound"):
        journal.journal_report(database)
    # the 100-byte header classify may open it; the image read must not follow
    assert opened.count("big.sqlite3") <= 1, opened


def test_a_database_that_moves_during_the_read_is_refused(tmp_path, monkeypatch):
    database = tmp_path / "moving.sqlite3"
    journal.append_receipts(database, [_receipt()])
    real_fstat = journal.os.fstat

    def shifted(fd):
        info = real_fstat(fd)
        return os.stat_result((info.st_mode, info.st_ino, info.st_dev, info.st_nlink,
                               info.st_uid, info.st_gid, info.st_size,
                               info.st_atime, info.st_mtime + 5, info.st_ctime))

    monkeypatch.setattr(journal.os, "fstat", shifted)
    with pytest.raises(journal.JournalError, match="changed while it was being read"):
        journal.journal_report(database)


def test_sqlite_never_opens_the_journal_file_itself(tmp_path, monkeypatch):
    """Structural: status must not hand the path to sqlite3.connect at all."""
    database = _wal_database(tmp_path)
    real_connect = sqlite3.connect
    seen = []

    def watched(target, *args, **kwargs):
        seen.append(str(target))
        return real_connect(target, *args, **kwargs)

    monkeypatch.setattr(journal.sqlite3, "connect", watched)
    journal.journal_report(database)
    assert seen == [":memory:"], f"status opened something other than memory: {seen}"



def test_a_non_database_is_rejected_without_being_loaded_into_memory(tmp_path, monkeypatch):
    """The header classify earns its place by bounding what we read.

    deserialize would also reject this file, so correctness does not depend on
    the classify. What it buys is that a large non-database is refused after
    100 bytes instead of being copied into memory first, and that is the
    property asserted here.
    """
    target = tmp_path / "huge-not-a-db.bin"
    target.write_bytes(b"definitely not sqlite" * 100_000)
    sizes = []
    real_open = Path.open

    def watched(self, *args, **kwargs):
        handle = real_open(self, *args, **kwargs)
        if self.name == target.name:
            original = handle.read

            def counting(n=-1):
                chunk = original(n)
                sizes.append(len(chunk))
                return chunk

            handle.read = counting
        return handle

    monkeypatch.setattr(Path, "open", watched)
    with pytest.raises(journal.JournalError, match="cannot be read"):
        journal.journal_report(target)
    assert sizes and max(sizes) <= journal.SQLITE_HEADER_BYTES, sizes
