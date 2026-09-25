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
    # The append contract is unchanged; publication outcome is additive, because
    # a caller must be able to distinguish committed from committed-and-published.
    assert result == {"appended": 1, "duplicates": 0, "database_state": "available",
                      "snapshot_seq": 1, "publication": journal.PUBLISHED}
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


# =============================================================================
# Snapshot-era status tests.
#
# status no longer opens the database, so every test that asserted a property
# of reading a LIVE database (WAL sidecars, hot journals, a database deleted
# mid-read) has been RETIRED rather than adjusted: its premise no longer
# exists. The intent of each is carried over here against snapshots, which is
# where those hazards now live or provably cannot.
# =============================================================================


def _tree(directory):
    return {path.name: _digest(path) for path in sorted(directory.iterdir())
            if path.is_file()}


def _snapshots(database):
    return sorted(journal.snapshot_directory(database).iterdir())


# --- publication is coupled to commit, not to a later read --------------------


def test_sequence_and_image_are_captured_in_the_committing_transaction(tmp_path):
    """The correction that matters: the image must be the state being committed.

    A fresh post-commit read could contain another writer's rows under a
    sequence we allocated, so the image is taken inside the transaction.
    """
    database = tmp_path / "journal.sqlite3"
    result = journal.append_receipts(database, [_receipt()])
    assert (result["snapshot_seq"], result["publication"]) == (1, journal.PUBLISHED)
    report = journal.journal_report(database)["journal"]
    assert (report["snapshot_seq"], report["receipt_rows"]) == (1, 1)


def test_a_rolled_back_append_publishes_nothing(tmp_path):
    """A captured image from a rolled-back transaction holds phantom rows.

    Measured during design: serialize() inside a transaction that is later
    rolled back still contains the discarded rows, so publication must never
    precede a successful commit.
    """
    database = tmp_path / "journal.sqlite3"
    journal.append_receipts(database, [_receipt()])
    published_before = _snapshots(database)
    conflicting = _receipt(observed_at="2026-09-26T00:00:00+00:00")
    with pytest.raises(journal.JournalConflictError):
        journal.append_receipts(database, [_receipt(contract_id="other"), conflicting])
    assert _snapshots(database) == published_before


def test_crash_before_commit_publishes_nothing(tmp_path, monkeypatch):
    database = tmp_path / "journal.sqlite3"
    journal.append_receipts(database, [_receipt()])
    before = _snapshots(database)

    def exploding(connection):
        # sqlite3.Connection is an immutable type, so the crash is injected at
        # the allocation step instead: inside the transaction, before commit.
        raise RuntimeError("crash before commit")

    monkeypatch.setattr(journal, "_allocate_sequence", exploding)
    with pytest.raises(RuntimeError):
        journal.append_receipts(database, [_receipt(contract_id="c2")])
    monkeypatch.undo()
    assert _snapshots(database) == before
    assert journal.journal_report(database)["journal"]["receipt_rows"] == 1


def test_crash_after_commit_before_publish_is_visible_and_recoverable(tmp_path,
                                                                      monkeypatch):
    """The honest gap: the reader lags, and the report says so."""
    database = tmp_path / "journal.sqlite3"
    journal.append_receipts(database, [_receipt()])
    monkeypatch.setattr(journal, "_publish_snapshot",
                        lambda *a, **k: (journal.PUBLICATION_PENDING, "simulated_crash"))
    result = journal.append_receipts(database, [_receipt(contract_id="c2")])
    assert result["publication"] == journal.PUBLICATION_PENDING
    assert result["appended"] == 1
    monkeypatch.undo()

    stale = journal.journal_report(database)["journal"]
    assert stale["snapshot_seq"] == 1
    assert stale["receipt_rows"] == 1
    assert stale["latest_committed_state"] == "unknown"

    recovered = journal.publish_pending(database)
    assert recovered["publication"] == journal.PUBLISHED
    assert journal.journal_report(database)["journal"]["receipt_rows"] == 2


def test_recovery_is_idempotent_and_appends_no_receipt(tmp_path):
    database = tmp_path / "journal.sqlite3"
    journal.append_receipts(database, [_receipt()])
    rows_before = journal.journal_report(database)["journal"]["receipt_rows"]
    first = journal.publish_pending(database)
    second = journal.publish_pending(database)
    assert first["publication"] == journal.ALREADY_PUBLISHED
    assert second["publication"] == journal.ALREADY_PUBLISHED
    assert journal.journal_report(database)["journal"]["receipt_rows"] == rows_before


# --- no snapshot means unavailable, never an empty journal --------------------


def test_no_snapshot_is_unavailable_with_no_live_database_fallback(tmp_path,
                                                                   monkeypatch):
    """Reporting zero rows here would be a silent wrong answer."""
    database = tmp_path / "journal.sqlite3"
    monkeypatch.setattr(journal, "_publish_snapshot",
                        lambda *a, **k: (journal.PUBLICATION_PENDING, "suppressed"))
    journal.append_receipts(database, [_receipt()])
    monkeypatch.undo()
    report = journal.journal_report(database)["journal"]
    assert report["database_state"] == "unavailable"
    assert report["receipt_rows"] is None
    assert report["latest_committed_state"] == "unknown"


def test_a_completely_absent_journal_is_also_unavailable(tmp_path):
    report = journal.journal_report(tmp_path / "nothing" / "j.sqlite3")["journal"]
    assert report["database_state"] == "unavailable"
    assert report["receipt_rows"] is None


def test_status_never_opens_the_database(tmp_path, monkeypatch):
    database = tmp_path / "journal.sqlite3"
    journal.append_receipts(database, [_receipt()])
    seen = []
    real_connect = sqlite3.connect

    def watched(target, *args, **kwargs):
        seen.append(str(target))
        return real_connect(target, *args, **kwargs)

    monkeypatch.setattr(journal.sqlite3, "connect", watched)
    journal.journal_report(database)
    assert seen == [":memory:"], f"status opened something else: {seen}"


# --- snapshots are verified, and refusals are refusals ------------------------


def test_a_tampered_snapshot_is_refused_by_full_digest(tmp_path):
    database = tmp_path / "journal.sqlite3"
    journal.append_receipts(database, [_receipt()])
    target = _snapshots(database)[0]
    image = bytearray(target.read_bytes())
    image[-64:] = bytes(64)
    target.write_bytes(bytes(image))
    with pytest.raises(journal.JournalError, match="does not match its digest"):
        journal.journal_report(database)


def test_a_truncated_snapshot_is_refused(tmp_path):
    database = tmp_path / "journal.sqlite3"
    journal.append_receipts(database, [_receipt()])
    target = _snapshots(database)[0]
    with open(target, "r+b") as handle:
        handle.truncate(200)
    with pytest.raises(journal.JournalError, match="does not match its digest"):
        journal.journal_report(database)


def test_a_duplicate_sequence_is_ambiguous_and_refused(tmp_path):
    database = tmp_path / "journal.sqlite3"
    journal.append_receipts(database, [_receipt()])
    original = _snapshots(database)[0]
    twin = original.parent / ("snap-000000000001-" + ("b" * 64) + ".journal")
    twin.write_bytes(original.read_bytes())
    with pytest.raises(journal.JournalError, match="duplicate snapshot sequence"):
        journal.journal_report(database)


def test_an_unsupported_snapshot_schema_is_refused(tmp_path):
    database = tmp_path / "journal.sqlite3"
    journal.append_receipts(database, [_receipt()])
    memory = sqlite3.connect(":memory:")
    memory.execute(
        "CREATE TABLE journal_metadata(singleton INTEGER, schema_version INTEGER)")
    memory.execute("INSERT INTO journal_metadata VALUES (1, 99)")
    image = memory.serialize()
    memory.close()
    digest = hashlib.sha256(image).hexdigest()
    target = journal.snapshot_directory(database) / f"snap-000000000009-{digest}.journal"
    target.write_bytes(image)
    with pytest.raises(journal.JournalError, match="unsupported snapshot schema"):
        journal.journal_report(database)


def test_a_name_that_is_not_a_snapshot_is_ignored_not_read(tmp_path):
    database = tmp_path / "journal.sqlite3"
    journal.append_receipts(database, [_receipt()])
    directory = journal.snapshot_directory(database)
    (directory / "snap-bad-name.journal").write_bytes(b"garbage")
    (directory / ".publish-orphan.tmp").write_bytes(b"garbage")
    assert journal.journal_report(database)["journal"]["receipt_rows"] == 1


def test_status_writes_nothing_on_a_snapshot_error_path(tmp_path):
    database = tmp_path / "journal.sqlite3"
    journal.append_receipts(database, [_receipt()])
    directory = journal.snapshot_directory(database)
    bogus = b"not sqlite at all"
    (directory / ("snap-000000000002-" + hashlib.sha256(bogus).hexdigest()
                  + ".journal")).write_bytes(bogus)
    before = _tree(directory)
    with pytest.raises(journal.JournalError):
        journal.journal_report(database)
    assert _tree(directory) == before


def test_status_is_byte_identical_read_only_on_success(tmp_path):
    database = tmp_path / "journal.sqlite3"
    journal.append_receipts(database, [_receipt()])
    snaps = journal.snapshot_directory(database)
    before = (_tree(tmp_path), _tree(snaps))
    journal.journal_report(database)
    assert (_tree(tmp_path), _tree(snaps)) == before


# --- bounds, concurrency and retention ----------------------------------------


def test_the_scan_is_bounded(tmp_path, monkeypatch):
    database = tmp_path / "journal.sqlite3"
    journal.append_receipts(database, [_receipt()])
    directory = journal.snapshot_directory(database)
    for index in range(5):
        (directory / f"filler-{index}").write_bytes(b"")
    monkeypatch.setattr(journal, "MAX_SNAPSHOT_ENTRIES", 3)
    with pytest.raises(journal.JournalError, match="exceeds the scan bound"):
        journal.journal_report(database)


def test_retention_reports_pending_rather_than_deleting(tmp_path, monkeypatch):
    """Silent deletion of an immutable artefact is worse than a visible refusal."""
    database = tmp_path / "journal.sqlite3"
    journal.append_receipts(database, [_receipt()])
    monkeypatch.setattr(journal, "MAX_SNAPSHOT_RETAINED", 1)
    before = _tree(journal.snapshot_directory(database))
    result = journal.append_receipts(database, [_receipt(contract_id="c2")])
    assert result["publication"] == journal.PUBLICATION_PENDING
    assert result["publication_reason"] == "retention_bound_reached"
    assert result["appended"] == 1
    assert _tree(journal.snapshot_directory(database)) == before


def test_concurrent_readers_see_a_stable_image_while_a_writer_publishes(tmp_path):
    """The property the previous design could not provide."""
    database = tmp_path / "journal.sqlite3"
    journal.append_receipts(database, [_receipt()])
    first = _snapshots(database)[0]
    expected = first.name.split("-")[2].removesuffix(".journal")
    held = open(first, "rb")
    try:
        journal.append_receipts(database, [_receipt(contract_id="c2")])
        journal.append_receipts(database, [_receipt(contract_id="c3")])
        assert hashlib.sha256(held.read()).hexdigest() == expected
        assert journal.journal_report(database)["journal"]["snapshot_seq"] == 3
    finally:
        held.close()


def test_writers_receive_distinct_monotonic_sequences(tmp_path):
    database = tmp_path / "journal.sqlite3"
    seqs = [journal.append_receipts(
        database, [_receipt(contract_id=f"c{i}")])["snapshot_seq"] for i in range(4)]
    assert seqs == [1, 2, 3, 4]
    assert len(_snapshots(database)) == 4


def test_republishing_the_same_sequence_is_a_no_op(tmp_path):
    database = tmp_path / "journal.sqlite3"
    journal.append_receipts(database, [_receipt()])
    target = _snapshots(database)[0]
    before = (target.stat().st_mtime_ns, _digest(target))
    outcome, reason = journal._publish_snapshot(
        journal.snapshot_directory(database), 1, target.read_bytes())
    assert (outcome, reason) == (journal.PUBLISHED, None)
    assert (target.stat().st_mtime_ns, _digest(target)) == before


def test_a_sequence_republished_with_other_content_is_refused(tmp_path):
    database = tmp_path / "journal.sqlite3"
    journal.append_receipts(database, [_receipt()])
    with pytest.raises(journal.JournalError,
                       match="already published with other content"):
        journal._publish_snapshot(
            journal.snapshot_directory(database), 1, b"different image")


# --- the two ordering properties, which need a commit hook to observe ---------
# Found by mutation: without these, moving the capture to AFTER commit and
# moving publication to BEFORE commit both left the suite green. They are the
# two properties the design turns on, so they get tests that can see them.


class _CommitHook:
    """Proxy a sqlite3 connection so commit() can be intercepted.

    sqlite3.Connection is an immutable type and cannot be monkeypatched, so the
    hook is installed by wrapping the object the module receives from connect().
    """

    def __init__(self, inner, on_commit=None, fail=False):
        self._inner = inner
        self._on_commit = on_commit
        self._fail = fail

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def commit(self):
        if self._fail:
            raise RuntimeError("commit failed")
        self._inner.commit()
        if self._on_commit:
            self._on_commit()


def _install_hook(monkeypatch, database, **kwargs):
    real_connect = sqlite3.connect

    def connecting(target, *args, **kw):
        inner = real_connect(target, *args, **kw)
        if str(target) == str(database):
            return _CommitHook(inner, **kwargs)
        return inner

    monkeypatch.setattr(journal.sqlite3, "connect", connecting)


def test_the_published_image_excludes_rows_committed_by_another_writer(
        tmp_path, monkeypatch):
    """The capture must be the state THIS transaction commits, not a later read.

    An interloper commits immediately after our commit. If the image were taken
    after the commit, the snapshot published under OUR sequence would contain
    the interloper's receipt. It must not.
    """
    database = tmp_path / "journal.sqlite3"
    journal.append_receipts(database, [_receipt()])
    real_sqlite_connect = sqlite3.connect

    fired = []

    def interloper():
        # One shot, and via the REAL connect: the hook also wraps this
        # connection, so without the latch the interloper re-enters itself.
        if fired:
            return
        fired.append(True)
        side = real_sqlite_connect(database, isolation_level=None)
        side.execute("BEGIN IMMEDIATE")
        side.execute(
            """INSERT INTO accepted_receipts VALUES
               ('intruder','{}','zzz','1','az','ez','accepted',
                '2026-09-25T00:00:00+00:00')""")
        side.commit()
        side.close()

    _install_hook(monkeypatch, database, on_commit=interloper)
    result = journal.append_receipts(database, [_receipt(contract_id="mine")])
    monkeypatch.undo()

    assert result["publication"] == journal.PUBLISHED
    rows = journal.journal_report(database)["journal"]["receipt_rows"]
    newest = _snapshots(database)[-1]
    parts = newest.name.removesuffix(".journal").split("-")
    contracts = {row["contract_id"] for row in
                 journal._load_snapshot(newest, parts[2], int(parts[1]))}
    assert "zzz" not in contracts, "the snapshot contains another writer's committed row"
    assert "mine" in contracts
    assert rows == 2


def test_nothing_is_published_when_the_commit_fails(tmp_path, monkeypatch):
    """Publication must follow a successful commit, never precede it."""
    database = tmp_path / "journal.sqlite3"
    journal.append_receipts(database, [_receipt()])
    before = _snapshots(database)

    _install_hook(monkeypatch, database, fail=True)
    with pytest.raises(RuntimeError, match="commit failed"):
        journal.append_receipts(database, [_receipt(contract_id="c2")])
    monkeypatch.undo()

    assert _snapshots(database) == before, "a snapshot was published for an uncommitted append"
    assert journal.journal_report(database)["journal"]["receipt_rows"] == 1


# --- the six review findings, each reproduced before being fixed --------------


def test_a_neighbour_database_cannot_borrow_another_journals_snapshots(tmp_path):
    """Finding 1: snapshots were keyed by DIRECTORY, not by database.

    Appending to a.sqlite3 and then asking about a missing b.sqlite3 in the same
    directory returned a.sqlite3's receipts as available/1.
    """
    first = tmp_path / "a.sqlite3"
    second = tmp_path / "b.sqlite3"
    journal.append_receipts(first, [_receipt()])
    report = journal.journal_report(second)["journal"]
    assert report["database_state"] == "unavailable"
    assert report["receipt_rows"] is None
    # and the two journals do not share a snapshot directory at all
    assert journal.snapshot_directory(first) != journal.snapshot_directory(second)


def test_each_journal_keeps_its_own_snapshots_side_by_side(tmp_path):
    first = tmp_path / "a.sqlite3"
    second = tmp_path / "b.sqlite3"
    journal.append_receipts(first, [_receipt(contract_id="in-a")])
    journal.append_receipts(second, [_receipt(contract_id="in-b")])
    journal.append_receipts(second, [_receipt(contract_id="in-b-2")])
    assert journal.journal_report(first)["journal"]["receipt_rows"] == 1
    assert journal.journal_report(second)["journal"]["receipt_rows"] == 2


def test_an_unknown_schema_version_is_refused_without_being_rewritten(tmp_path):
    """Finding 2: _initialise wrote the current version unconditionally.

    A database claiming version 999 was silently downgraded to 2 instead of
    being refused, so the mutation is asserted absent as well as the refusal.
    """
    database = tmp_path / "journal.sqlite3"
    journal.append_receipts(database, [_receipt()])
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE journal_metadata SET schema_version = 999")
    with pytest.raises(journal.JournalError, match="unsupported journal database schema"):
        journal.append_receipts(database, [_receipt(contract_id="c2")])
    with sqlite3.connect(database) as connection:
        still = connection.execute(
            "SELECT schema_version FROM journal_metadata").fetchone()[0]
    assert still == 999, "the unknown version was rewritten instead of refused"


def test_a_version_one_database_is_migrated_exactly_once(tmp_path):
    """The only migration we understand must still work."""
    database = tmp_path / "journal.sqlite3"
    journal.append_receipts(database, [_receipt()])
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE journal_metadata SET schema_version = 1")
    assert journal.append_receipts(
        database, [_receipt(contract_id="c2")])["appended"] == 1
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT schema_version FROM journal_metadata").fetchone()[0] == 2


def test_a_renamed_snapshot_is_refused_by_its_embedded_sequence(tmp_path):
    """Finding 3: the filename is an untrusted label.

    Renaming a valid snapshot from sequence 1 to 99 preserves the digest, so
    the digest alone cannot say which state the image is.
    """
    database = tmp_path / "journal.sqlite3"
    journal.append_receipts(database, [_receipt()])
    original = _snapshots(database)[0]
    digest = original.name.removesuffix(".journal").split("-")[2]
    original.rename(original.parent / f"snap-000000000099-{digest}.journal")
    with pytest.raises(journal.JournalError, match="embedded sequence"):
        journal.journal_report(database)


def test_the_snapshot_read_is_bounded_even_if_the_file_grows_after_the_stat(
        tmp_path, monkeypatch):
    """Finding 4: a stat-then-read_bytes pair is unbounded in between.

    The stat must UNDER-report, otherwise the size check trips first and the
    read bound is never exercised. An earlier version of this test just lowered
    the limit, so the stat check caught everything and the test proved nothing
    about the read.
    """
    database = tmp_path / "journal.sqlite3"
    journal.append_receipts(database, [_receipt()])
    target = _snapshots(database)[0]
    real_stat = Path.stat

    def understating(self, *args, **kwargs):
        info = real_stat(self, *args, **kwargs)
        if self.name == target.name:
            return os.stat_result((info.st_mode, info.st_ino, info.st_dev,
                                   info.st_nlink, info.st_uid, info.st_gid, 1,
                                   info.st_atime, info.st_mtime, info.st_ctime))
        return info

    monkeypatch.setattr(Path, "stat", understating)
    monkeypatch.setattr(journal, "MAX_DATABASE_BYTES", 64)
    with pytest.raises(journal.JournalError, match="exceeds the readable size bound"):
        journal.journal_report(database)


def test_an_unpublishable_image_is_refused_before_the_commit(tmp_path, monkeypatch):
    """Finding 5: an image too large to publish must not be committed first."""
    database = tmp_path / "journal.sqlite3"
    journal.append_receipts(database, [_receipt()])
    rows_before = journal.journal_report(database)["journal"]["receipt_rows"]
    monkeypatch.setattr(journal, "MAX_DATABASE_BYTES", 64)
    with pytest.raises(journal.JournalError, match="publishable size bound"):
        journal.append_receipts(database, [_receipt(contract_id="c2")])
    monkeypatch.undo()
    with sqlite3.connect(database) as connection:
        committed = connection.execute(
            "SELECT COUNT(*) FROM accepted_receipts").fetchone()[0]
    assert committed == rows_before, "an unpublishable append was committed anyway"


def test_a_publication_problem_after_commit_is_an_outcome_not_an_exception(tmp_path):
    """Finding 6: a post-commit JournalError hid a durable commit.

    The caller saw what looked like a failed append while the receipts were in
    fact committed, which is the worst combination available.
    """
    database = tmp_path / "journal.sqlite3"
    journal.append_receipts(database, [_receipt()])
    bogus = b"different image"
    (journal.snapshot_directory(database) /
     ("snap-000000000002-" + hashlib.sha256(bogus).hexdigest()
      + ".journal")).write_bytes(bogus)
    result = journal.append_receipts(database, [_receipt(contract_id="c2")])
    assert result["publication"] == journal.PUBLICATION_PENDING
    assert result["publication_reason"].startswith("publish_refused:")
    assert result["appended"] == 1
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM accepted_receipts").fetchone()[0] == 2


def test_the_live_database_reading_path_is_gone_not_merely_unused(tmp_path):
    """The retirement is asserted, not just claimed in a commit message.

    My previous report implied these were gone while they were still defined.
    """
    for name in ("_read_receipts", "_sidecar_signature", "_refuse_unmerged_sidecars",
                 "_classify_database", "_snapshot_bytes", "_as_rollback_image"):
        assert not hasattr(journal, name), f"{name} still exists"


def test_status_opens_only_memory_for_every_outcome(tmp_path, monkeypatch):
    """The explicit replacement for the retired live-database tests."""
    database = tmp_path / "journal.sqlite3"
    journal.append_receipts(database, [_receipt()])
    seen = []
    real_connect = sqlite3.connect

    def watched(target, *args, **kwargs):
        seen.append(str(target))
        return real_connect(target, *args, **kwargs)

    monkeypatch.setattr(journal.sqlite3, "connect", watched)
    journal.journal_report(database)                      # success
    journal.journal_report(tmp_path / "absent.sqlite3")   # unavailable
    original = _snapshots(database)[0]
    original.write_bytes(b"corrupted")
    with pytest.raises(journal.JournalError):
        journal.journal_report(database)                  # error path
    assert set(seen) == {":memory:"}, f"status opened a database: {seen}"
