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


def test_foreign_files_below_the_scan_bound_do_not_break_the_journal(
        tmp_path, monkeypatch):
    """The old single counter let any foreign file brick the journal forever.

    Five unrelated names beside the snapshots used to trip the SNAPSHOT bound,
    refusing every read and every write with no in-module recovery, because
    pruning must scan first and deliberately never deletes what it does not
    own. Foreign entries now count only against the much larger scan bound.
    """
    database = tmp_path / "journal.sqlite3"
    journal.append_receipts(database, [_receipt()])
    directory = journal.snapshot_directory(database)
    for index in range(5):
        (directory / f"filler-{index}").write_bytes(b"")
    monkeypatch.setattr(journal, "MAX_SNAPSHOT_ENTRIES", 3)
    assert journal.journal_report(database)["journal"]["receipt_rows"] == 1
    assert journal.append_receipts(
        database, [_receipt(contract_id="c2")])["appended"] == 1


def test_the_scan_is_bounded_by_total_directory_entries(tmp_path, monkeypatch):
    database = tmp_path / "journal.sqlite3"
    journal.append_receipts(database, [_receipt()])
    directory = journal.snapshot_directory(database)
    for index in range(5):
        (directory / f"filler-{index}").write_bytes(b"")
    monkeypatch.setattr(journal, "MAX_SNAPSHOT_SCAN_ENTRIES", 3)
    with pytest.raises(journal.JournalError, match="exceeds the scan bound"):
        journal.journal_report(database)


def test_the_snapshot_count_is_bounded_separately(tmp_path, monkeypatch):
    """The bound that retention actually governs, counting only snapshots."""
    database = tmp_path / "journal.sqlite3"
    monkeypatch.setattr(journal, "MAX_SNAPSHOT_RETAINED", 64)
    for index in range(4):
        journal.append_receipts(database, [_receipt(contract_id=f"c{index}")])
    assert len(_snapshots(database)) == 4, "setup did not accumulate snapshots"
    monkeypatch.setattr(journal, "MAX_SNAPSHOT_ENTRIES", 3)
    with pytest.raises(journal.JournalError, match="exceeds the snapshot bound"):
        journal.journal_report(database)


def _hold_write_lock(database):
    blocker = sqlite3.connect(str(database), isolation_level=None, timeout=0)
    blocker.execute("BEGIN IMMEDIATE")
    return blocker


def test_contention_is_refused_in_this_modules_vocabulary_not_raw_sqlite3(tmp_path):
    """RCO2 and Tools: a held lock escaped append_receipts as OperationalError.

    Wrapping connect() was not enough. BEGIN IMMEDIATE and the commit are where
    the lock actually surfaces, so every library caller could receive a raw
    sqlite3 exception this module never documents.
    """
    database = tmp_path / "journal.sqlite3"
    journal.append_receipts(database, [_receipt()])
    blocker = _hold_write_lock(database)
    try:
        with pytest.raises(journal.JournalUnavailable, match="unavailable"):
            journal.append_receipts(database, [_receipt(contract_id="c2")])
        with pytest.raises(journal.JournalUnavailable):
            journal.publish_pending(database)
    finally:
        blocker.rollback()
        blocker.close()
    # and the refusal is still a JournalError, so existing callers keep working
    assert issubclass(journal.JournalUnavailable, journal.JournalError)


def test_status_still_answers_under_a_held_write_lock(tmp_path):
    """Non-vacuity pair, and the snapshot-only guarantee under real contention."""
    database = tmp_path / "journal.sqlite3"
    journal.append_receipts(database, [_receipt()])
    blocker = _hold_write_lock(database)
    try:
        assert journal.journal_report(database)["journal"]["receipt_rows"] == 1
    finally:
        blocker.rollback()
        blocker.close()


def test_the_cli_separates_unavailable_from_refused(tmp_path, capsys):
    database = tmp_path / "journal.sqlite3"
    journal.append_receipts(database, [_receipt()])
    document = tmp_path / "in.json"
    document.write_text(json.dumps({
        "schema": journal.APPEND_SCHEMA,
        "accepted_work": [_receipt(contract_id="c2")]}), encoding="utf-8")
    blocker = _hold_write_lock(database)
    try:
        code = journal.main(["append", "--database", str(database),
                             "--input", str(document)])
        assert code == 3, "contention must not share the refusal exit code"
        assert "unavailable" in capsys.readouterr().err
        assert journal.main(["status", "--database", str(database)]) == 0
    finally:
        blocker.rollback()
        blocker.close()


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


# --- recovery path hardening, each finding reproduced before the fix ----------


def test_recovery_does_not_trust_a_corrupted_published_name(tmp_path):
    """Finding 1: membership in a listing is a filename claim, not a snapshot.

    publish_pending reported already_published for a sequence whose snapshot
    content had been corrupted, leaving the journal with no usable published
    state and no sign of it.
    """
    database = tmp_path / "journal.sqlite3"
    journal.append_receipts(database, [_receipt()])
    target = _snapshots(database)[0]
    target.write_bytes(b"corrupted, but the name still claims this sequence")
    result = journal.publish_pending(database)
    assert result["publication"] != journal.ALREADY_PUBLISHED
    assert result["publication"] == journal.PUBLISHED
    assert result["snapshot_seq"] == 2
    assert journal.journal_report(database)["journal"]["receipt_rows"] == 1


def test_recovery_says_when_it_repaired_a_corrupted_snapshot(tmp_path):
    """The Lead's RED test wanted a raise here; this is the divergence, tested.

    Raising would make the recovery entry point the one operation that cannot
    recover. The concern behind it -- corruption must not pass as routine --
    is met by reporting the repair instead of burying it in a plain success.
    """
    database = tmp_path / "journal.sqlite3"
    journal.append_receipts(database, [_receipt()])
    _snapshots(database)[0].write_bytes(b"corrupted")
    result = journal.publish_pending(database)
    assert result["publication"] == journal.PUBLISHED
    assert result["recovered_from"] == "corrupt_snapshot:1"


def test_ordinary_recovery_does_not_claim_corruption(tmp_path, monkeypatch):
    """Non-vacuity pair: recovered_from must mark corruption, not every repair."""
    database = tmp_path / "journal.sqlite3"
    monkeypatch.setattr(journal, "_publish_snapshot",
                        lambda *a: (journal.PUBLICATION_PENDING, "injected"))
    journal.append_receipts(database, [_receipt()])   # commits, never publishes
    monkeypatch.undo()
    result = journal.publish_pending(database)
    assert result["publication"] == journal.PUBLISHED
    assert "recovered_from" not in result, result


def test_recovery_enforces_the_size_bound_before_committing(tmp_path, monkeypatch):
    """Finding 2: append had the pre-commit refusal, recovery did not."""
    database = tmp_path / "journal.sqlite3"
    journal.append_receipts(database, [_receipt()])
    with sqlite3.connect(database) as connection:
        before = connection.execute(
            "SELECT snapshot_seq FROM journal_metadata").fetchone()[0]
    monkeypatch.setattr(journal, "MAX_DATABASE_BYTES", 64)
    with pytest.raises(journal.JournalError, match="publishable size bound"):
        journal.publish_pending(database)
    monkeypatch.undo()
    with sqlite3.connect(database) as connection:
        after = connection.execute(
            "SELECT snapshot_seq FROM journal_metadata").fetchone()[0]
    assert after == before, "the refused recovery advanced the sequence anyway"


def test_recovery_publication_failure_after_commit_is_an_outcome(tmp_path,
                                                                 monkeypatch):
    """Finding 3: recovery must preserve committed-but-unpublished like append."""
    database = tmp_path / "journal.sqlite3"
    journal.append_receipts(database, [_receipt()])
    _snapshots(database)[0].write_bytes(b"corrupted")   # force recovery to act

    def exploding(*args, **kwargs):
        raise OSError(5, "I/O error")

    monkeypatch.setattr(journal, "_publish_snapshot", exploding)
    result = journal.publish_pending(database)
    monkeypatch.undo()
    assert result["publication"] == journal.PUBLICATION_PENDING
    assert result["reason"].startswith("publish_failed:")
    assert "snapshot_seq" in result, "the committed sequence must still be reported"


def test_recovery_survives_a_publish_REFUSAL_after_its_commit(tmp_path):
    """Mutation-driven: the OSError test left `except JournalError` unproven.

    Deleting that clause kept the whole suite green, because the only test of
    the post-commit guard injected an OSError. A refusal raised by
    _publish_snapshot itself is the case that clause exists for, and it must
    not surface as a failed recovery over a commit that really happened.
    """
    database = tmp_path / "journal.sqlite3"
    journal.append_receipts(database, [_receipt()])
    _snapshots(database)[0].write_bytes(b"corrupted")     # force recovery to act
    bogus = b"a different image entirely"
    (journal.snapshot_directory(database) /
     ("snap-000000000002-" + hashlib.sha256(bogus).hexdigest()
      + ".journal")).write_bytes(bogus)
    result = journal.publish_pending(database)
    assert result["publication"] == journal.PUBLICATION_PENDING
    assert result["reason"].startswith("publish_refused:")
    assert result["snapshot_seq"] == 2, "the committed sequence must be reported"


def test_pruning_steps_past_an_undeletable_snapshot_within_one_pass(tmp_path,
                                                                    monkeypatch):
    """Mutation-driven: the budget must count DELETIONS, not attempts.

    Spending the budget on files that refused to go left the journal parked
    above its target with deletable snapshots still sitting there, and the
    always-fails test could not see the difference.
    """
    database = tmp_path / "journal.sqlite3"
    monkeypatch.setattr(journal, "MAX_SNAPSHOT_RETAINED", 2)
    real_unlink = Path.unlink
    monkeypatch.setattr(Path, "unlink",
                        lambda self, *a, **k: (_ for _ in ()).throw(
                            PermissionError(32, "in use")))
    for index in range(5):
        journal.append_receipts(database, [_receipt(contract_id=f"c{index}")])
    assert len(_snapshots(database)) == 5, "setup did not accumulate snapshots"
    stuck = [path for sequence, _, path in
             journal._snapshot_entries(journal.snapshot_directory(database))
             if sequence == 2][0]

    def selective(self, *args, **kwargs):
        if Path(self) == stuck:
            raise PermissionError(32, "in use")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", selective)
    journal.append_receipts(database, [_receipt(contract_id="last")])
    monkeypatch.setattr(Path, "unlink", real_unlink)
    remaining = sorted(sequence for sequence, _, _ in
                       journal._snapshot_entries(journal.snapshot_directory(database)))
    assert stuck.exists(), "the undeletable snapshot was somehow removed"
    assert remaining == [2, 6], f"pruning stalled behind the stuck file: {remaining}"


def test_a_refused_append_never_destroys_the_readers_current_snapshot(tmp_path,
                                                                      monkeypatch):
    """Mutation-driven: the pre-commit prune must exclude the CURRENT newest.

    Treating the not-yet-allocated sequence as newest made every snapshot a
    candidate, so the gate could delete the state the reader is on while
    refusing the append that was supposed to replace it -- leaving nothing.
    """
    database = tmp_path / "journal.sqlite3"
    monkeypatch.setattr(journal, "MAX_SNAPSHOT_RETAINED", 10)
    monkeypatch.setattr(journal, "SNAPSHOT_RETENTION_SLACK", 10)
    for index in range(3):
        journal.append_receipts(database, [_receipt(contract_id=f"c{index}")])
    newest = _snapshots(database)[-1]
    assert newest.name.startswith("snap-000000000003-")
    monkeypatch.setattr(journal, "MAX_SNAPSHOT_RETAINED", 1)
    monkeypatch.setattr(journal, "SNAPSHOT_RETENTION_SLACK", 0)
    with pytest.raises(journal.JournalError, match="retention is full"):
        journal.append_receipts(database, [_receipt(contract_id="refused")])
    monkeypatch.undo()
    assert newest.exists(), "the gate deleted the snapshot the reader is using"
    assert journal.journal_report(database)["journal"]["receipt_rows"] == 3


def test_publish_snapshot_refuses_an_existing_target_whose_content_differs(tmp_path):
    """Direct unit test on _publish_snapshot, as requested."""
    database = tmp_path / "journal.sqlite3"
    journal.append_receipts(database, [_receipt()])
    directory = journal.snapshot_directory(database)
    with pytest.raises(journal.JournalError,
                       match="already published with other content"):
        journal._publish_snapshot(directory, 1, b"a different image entirely")


# --- the replay target's BYTES, not its name ----------------------------------


def test_replay_verifies_the_existing_bytes_not_the_filename(tmp_path):
    """A corrupted file keeps its name, so the name cannot prove the content.

    _publish_snapshot compared the digest parsed out of the FILENAME against
    the image it was handed. Both agreed, so a target whose stored bytes had
    been destroyed was reported as an idempotent success -- publication
    claiming a sequence was safely on disk when nothing readable was.
    """
    database = tmp_path / "journal.sqlite3"
    journal.append_receipts(database, [_receipt()])
    directory = journal.snapshot_directory(database)
    target = _snapshots(database)[0]
    original = target.read_bytes()
    target.write_bytes(b"corrupt")
    with pytest.raises(journal.JournalError):
        journal._publish_snapshot(directory, 1, original)


def test_replay_of_an_intact_snapshot_is_still_an_idempotent_success(tmp_path):
    """Non-vacuity pair: the byte check must not break real replay.

    Without this, _publish_snapshot could refuse every replay outright and the
    test above would still pass.
    """
    database = tmp_path / "journal.sqlite3"
    journal.append_receipts(database, [_receipt()])
    directory = journal.snapshot_directory(database)
    target = _snapshots(database)[0]
    original = target.read_bytes()
    assert journal._publish_snapshot(directory, 1, original) == (journal.PUBLISHED, None)
    assert len(_snapshots(database)) == 1, "replay wrote a second file"
    assert target.read_bytes() == original, "replay rewrote a verified snapshot"


# --- nothing after the commit may raise, INCLUDING the cleanup ----------------


def _boom_journal(*args, **kwargs):
    raise journal.JournalError("injected unreadable snapshot directory")


def _boom_os(*args, **kwargs):
    raise OSError(5, "injected I/O error")


@pytest.mark.parametrize("failure,expected", [(_boom_journal, "prune_refused:"),
                                              (_boom_os, "prune_failed:")])
@pytest.mark.parametrize("recovery", [False, True])
def test_a_prune_failure_after_publish_is_a_warning_not_an_exception(
        tmp_path, monkeypatch, failure, expected, recovery):
    """Retention is housekeeping; failing to reclaim space publishes nothing less.

    _prune_superseded ran after the commit and OUTSIDE the guard, so a scan
    failure -- unreadable directory, flooded past the entry bound, two files
    claiming one sequence -- threw over a publication that had already
    succeeded. That is exactly the failure-hiding-a-durable-commit shape this
    module refuses everywhere else, reintroduced by the cleanup step.
    """
    database = tmp_path / "journal.sqlite3"
    if recovery:
        monkeypatch.setattr(journal, "_publish_snapshot",
                            lambda *a: (journal.PUBLICATION_PENDING, "injected"))
        journal.append_receipts(database, [_receipt()])   # commits, never publishes
        monkeypatch.undo()
    monkeypatch.setattr(journal, "_prune_superseded", failure)
    result = (journal.publish_pending(database) if recovery
              else journal.append_receipts(database, [_receipt()]))
    monkeypatch.undo()
    assert result["publication"] == journal.PUBLISHED
    assert result["snapshot_seq"] > 0
    assert result["retention_warning"].startswith(expected), result
    # The published state must be TRUE, not merely reported: the snapshot the
    # warning was raised beside is on disk and the reader can load it.
    report = journal.journal_report(database)["journal"]
    assert report["snapshot_seq"] == result["snapshot_seq"]
    assert report["receipt_rows"] == 1


def test_a_failed_publication_prunes_nothing_at_all(tmp_path, monkeypatch):
    """Mutation-driven: pruning must not run on behalf of a publish that failed.

    The prune treats everything older than the sequence it is given as
    superseded. Handing it a sequence that was never published makes the
    CURRENT newest snapshot -- the newest thing any reader can load -- a
    deletion candidate, on behalf of a state that does not exist on disk.
    """
    database = tmp_path / "journal.sqlite3"
    journal.append_receipts(database, [_receipt(contract_id="c1")])
    journal.append_receipts(database, [_receipt(contract_id="c2")])
    before = sorted(path.name for path in _snapshots(database))
    assert len(before) == 2, "setup did not accumulate two snapshots"
    monkeypatch.setattr(journal, "MAX_SNAPSHOT_RETAINED", 1)
    monkeypatch.setattr(journal, "_publish_snapshot",
                        lambda *a: (journal.PUBLICATION_PENDING, "injected"))
    result = journal.append_receipts(database, [_receipt(contract_id="c3")])
    monkeypatch.undo()
    assert result["publication"] == journal.PUBLICATION_PENDING
    assert sorted(path.name for path in _snapshots(database)) == before
    # and the reader still has the view it had before the failed publication
    assert journal.journal_report(database)["journal"]["snapshot_seq"] == 2


def test_pruning_only_ever_deletes_journal_owned_cache_snapshots(tmp_path,
                                                                  monkeypatch):
    """The Lead's explicit retention condition, asserted rather than argued.

    Retention may reclaim its own cache and nothing else: not the database, not
    a file that merely happens to sit in the snapshot directory, and nothing
    outside that directory however convincingly it is named. The decoys are
    named to look like snapshots, because a glob would have deleted them.

    SQLite's own -wal/-shm siblings are deliberately NOT asserted here: SQLite
    creates and removes those itself, so a claim about them would be testing
    SQLite rather than this module.
    """
    database = tmp_path / "journal.sqlite3"
    monkeypatch.setattr(journal, "MAX_SNAPSHOT_RETAINED", 1)
    journal.append_receipts(database, [_receipt(contract_id="c0")])
    directory = journal.snapshot_directory(database)
    valid_name = "snap-000000000001-" + "a" * 64 + ".journal"
    bystanders = [
        directory / "notes.txt",
        directory / "snap-000000000009-short.journal",               # bad digest
        directory / ("snap-0000000000010-" + "f" * 64 + ".journal"),  # bad width
        directory / "README",
        # A PERFECTLY valid snapshot name, one directory up beside the database.
        # Only a scan pointed at the wrong directory could ever reach it.
        database.parent / valid_name,
    ]
    for path in bystanders:
        path.write_bytes(b"not a snapshot")
    database_size = database.stat().st_size

    for index in range(4):
        journal.append_receipts(database, [_receipt(contract_id=f"c{index + 1}")])
    monkeypatch.undo()

    # Counted through the parsed scan, not a filename glob.
    owned = journal._snapshot_entries(directory)
    assert len(owned) <= 1, f"retention did not actually prune: {len(owned)}"
    for path in bystanders:
        assert path.exists(), f"pruning deleted a bystander: {path.name}"
        assert path.read_bytes() == b"not a snapshot"
    assert database.exists(), "pruning deleted the authoritative database"
    assert database.stat().st_size >= database_size
    assert journal.journal_report(database)["journal"]["receipt_rows"] == 5


def test_no_snapshot_path_is_derived_after_the_commit(tmp_path):
    """Structural invariant: the post-commit stretch computes nothing.

    Behaviour cannot see this one -- _snapshot_dir_for is pure path arithmetic
    and returns the same value either way -- so it is asserted against the
    source. The rule it protects is that everything after the commit is either
    guarded or incapable of failing, and a derivation sitting there is an
    unguarded call waiting to become one.
    """
    import ast
    import inspect

    source = inspect.getsource(journal)
    tree = ast.parse(source)
    checked = []
    for name in ("append_receipts", "publish_pending"):
        function = next((node for node in ast.walk(tree)
                         if isinstance(node, ast.FunctionDef) and node.name == name), None)
        assert function is not None, f"{name} not found; this test has gone stale"
        commit_index = None
        for index, statement in enumerate(function.body):
            if "connection.commit()" in ast.unparse(statement):
                commit_index = index
                break
        assert commit_index is not None,             f"no commit found in {name}; this test has gone stale"
        after = function.body[commit_index + 1:]
        assert after, f"nothing follows the commit in {name}; this test has gone stale"
        derivations = [node for statement in after for node in ast.walk(statement)
                       if isinstance(node, ast.Call)
                       and isinstance(node.func, ast.Name)
                       and node.func.id == "_snapshot_dir_for"]
        assert not derivations, f"{name} derives a snapshot path after the commit"
        checked.append(name)
    assert checked == ["append_receipts", "publish_pending"]


def test_a_successful_prune_reports_no_retention_warning(tmp_path):
    """Non-vacuity pair: the warning must mark a failure, not every publish."""
    database = tmp_path / "journal.sqlite3"
    result = journal.append_receipts(database, [_receipt()])
    assert result["publication"] == journal.PUBLISHED
    assert "retention_warning" not in result, result


@pytest.mark.parametrize("failure", [_boom_journal, _boom_os])
@pytest.mark.parametrize("collaborator", ["_publish_snapshot", "_prune_superseded"])
@pytest.mark.parametrize("recovery", [False, True])
def test_no_post_commit_collaborator_can_turn_a_commit_into_an_exception(
        tmp_path, monkeypatch, failure, collaborator, recovery):
    """The CLASS, not the two instances of it the review happened to find.

    Every collaborator called after the commit is injected with each failure
    type, on both entry points. A durable commit must always come back as a
    result that reports its sequence, never as an exception.
    """
    database = tmp_path / "journal.sqlite3"
    if recovery:
        monkeypatch.setattr(journal, "_publish_snapshot",
                            lambda *a: (journal.PUBLICATION_PENDING, "injected"))
        journal.append_receipts(database, [_receipt()])
        monkeypatch.undo()
    monkeypatch.setattr(journal, collaborator, failure)
    result = (journal.publish_pending(database) if recovery
              else journal.append_receipts(database, [_receipt()]))
    monkeypatch.undo()
    assert result["snapshot_seq"] > 0
    assert result.get("publication_reason") or result.get("reason")         or result.get("retention_warning"), result
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM accepted_receipts").fetchone()[0] == 1


def test_verify_published_rejects_a_corrupted_snapshot(tmp_path):
    database = tmp_path / "journal.sqlite3"
    journal.append_receipts(database, [_receipt()])
    directory = journal.snapshot_directory(database)
    assert journal._verify_published(directory, 1) is True
    _snapshots(database)[0].write_bytes(b"corrupted")
    assert journal._verify_published(directory, 1) is False
    assert journal._verify_published(directory, 99) is False


# --- the journal key must not collapse distinct names -------------------------


def _case_insensitive_filesystem(root: Path) -> bool:
    probe = root / "CaseProbe.tmp"
    probe.write_bytes(b"")
    try:
        return (root / "caseprobe.tmp").exists()
    finally:
        probe.unlink()


def test_an_aliased_path_to_one_database_yields_one_journal(tmp_path):
    """RCO1's finding: absolute() is lexical, so one database became two.

    A status call through the aliased spelling reported unavailable for
    receipts that were genuinely committed and published through the canonical
    spelling, contradicting the documented binding to one database.
    """
    inner = tmp_path / "sub"
    inner.mkdir()
    canonical = inner / "journal.sqlite3"
    alias = inner / ".." / "sub" / "journal.sqlite3"
    journal.append_receipts(canonical, [_receipt()])
    assert journal._journal_key(alias) == journal._journal_key(canonical)
    assert journal.journal_report(alias)["journal"]["receipt_rows"] == 1


def test_a_case_alias_of_an_existing_database_yields_one_journal(tmp_path):
    if not _case_insensitive_filesystem(tmp_path):
        pytest.skip("case-sensitive filesystem: the two spellings are two files")
    canonical = tmp_path / "journal.sqlite3"
    journal.append_receipts(canonical, [_receipt()])
    shouted = tmp_path / "JOURNAL.SQLITE3"
    assert journal._journal_key(shouted) == journal._journal_key(canonical)
    assert journal.journal_report(shouted)["journal"]["receipt_rows"] == 1


def test_a_symlink_alias_of_an_existing_database_yields_one_journal(tmp_path):
    canonical = tmp_path / "journal.sqlite3"
    journal.append_receipts(canonical, [_receipt()])
    link = tmp_path / "linked.sqlite3"
    try:
        link.symlink_to(canonical)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not creatable here")
    assert journal._journal_key(link) == journal._journal_key(canonical)
    assert journal.journal_report(link)["journal"]["receipt_rows"] == 1


def test_the_residual_case_gap_for_a_database_that_does_not_exist_yet(tmp_path):
    """The documented residual, asserted so it cannot surprise anyone.

    resolve() cannot canonicalise the case of a component that does not exist,
    so two case spellings of the same FUTURE file still key apart until the
    file exists. Compares the derived KEYS, never Path objects: Path.__eq__ is
    case-insensitive on Windows and would report these equal while the key
    strings differ, hiding the gap behind a green test.
    """
    upper = tmp_path / "NotYet.sqlite3"
    lower = tmp_path / "notyet.sqlite3"
    assert not upper.exists() and not lower.exists()
    assert journal._journal_key(upper) != journal._journal_key(lower)
    # ...and once the file exists, both spellings agree again.
    journal.append_receipts(lower, [_receipt()])
    if _case_insensitive_filesystem(tmp_path):
        assert journal._journal_key(upper) == journal._journal_key(lower)


def test_the_key_is_computable_for_a_database_that_does_not_exist(tmp_path):
    """resolve() must not raise on a missing path, or a first append breaks."""
    assert len(journal._journal_key(tmp_path / "deep" / "absent.sqlite3")) == 32


# NOTE: the two tests below pass because their databases are never created.
# With resolve() in place, an EXISTING pair of case spellings would agree on a
# case-insensitive host, which is correct; see the residual test above.
def test_the_journal_key_preserves_case(tmp_path):
    """Finding 5: casefolding made A.sqlite and a.sqlite share a snapshot set.

    On a case-sensitive host those are two different databases, which is the
    borrowing bug the key exists to prevent. Case is preserved; the drive
    letter is normalised because that genuinely is case-insensitive.
    """
    upper = journal._journal_key(tmp_path / "A.sqlite3")
    lower = journal._journal_key(tmp_path / "a.sqlite3")
    assert upper != lower


def test_the_journal_key_normalises_only_the_drive_letter():
    assert journal._journal_key(Path("C:/x/j.sqlite3")) == \
           journal._journal_key(Path("c:/x/j.sqlite3"))


def test_the_module_docstring_names_both_writers(tmp_path):
    """The Lead's finding: the header claimed append was the only writer.

    publish_pending opens the database with BEGIN IMMEDIATE and commits, so
    "only append opens a writable database" was false from the moment recovery
    became a writer. A doc that overstates a safety property is worse than one
    that says nothing.
    """
    flat = " ".join((journal.__doc__ or "").split())
    assert "Only the explicit ``append`` API opens a writable" not in flat
    assert "publish_pending" in flat
    assert "Only ``append`` can CREATE one" in flat


def test_the_module_docstring_describes_snapshot_bytes(tmp_path):
    """Finding 6: the docstring still described reading the database's bytes."""
    doc = journal.__doc__ or ""
    flat = " ".join(doc.split())
    assert "never reads the database at all" in flat
    assert "immutable SNAPSHOT" in flat
    assert "it reads the file bytes and loads an in-memory copy" not in flat


# --- bounded retention, replacing the never-delete design ---------------------


def test_retention_prunes_superseded_snapshots_and_keeps_the_newest(tmp_path,
                                                                    monkeypatch):
    """Replaces the old never-delete contract, which stalled publication forever.

    The previous design reported publication_pending at the bound and deleted
    nothing, so appends kept committing while the reader fell permanently
    behind. Pruning only SUPERSEDED snapshots cannot destroy the current view.
    """
    database = tmp_path / "journal.sqlite3"
    monkeypatch.setattr(journal, "MAX_SNAPSHOT_RETAINED", 3)
    for index in range(6):
        result = journal.append_receipts(database, [_receipt(contract_id=f"c{index}")])
        assert result["publication"] == journal.PUBLISHED, result
    remaining = _snapshots(database)
    assert len(remaining) <= 3
    newest = journal.journal_report(database)["journal"]
    assert newest["snapshot_seq"] == 6
    assert newest["receipt_rows"] == 6


def test_retention_never_deletes_the_snapshot_a_reader_is_using(tmp_path,
                                                                monkeypatch):
    database = tmp_path / "journal.sqlite3"
    monkeypatch.setattr(journal, "MAX_SNAPSHOT_RETAINED", 2)
    journal.append_receipts(database, [_receipt()])
    current = _snapshots(database)[-1]
    held = open(current, "rb")
    try:
        for index in range(4):
            journal.append_receipts(database, [_receipt(contract_id=f"c{index}")])
        # the held file may or may not still exist, but the NEWEST always does
        latest = journal.journal_report(database)["journal"]
        assert latest["snapshot_seq"] == 5
        assert latest["receipt_rows"] == 5
    finally:
        held.close()


def _fill_retention(database, count):
    """Bring the journal to `count` published snapshots under the live bound."""
    for index in range(count - 1):
        journal.append_receipts(database, [_receipt(contract_id=f"fill{index}")])


def test_capacity_is_refused_before_commit_when_nothing_can_be_pruned(tmp_path,
                                                                      monkeypatch):
    """The explicit capacity stop: better than committing into permanent lag."""
    database = tmp_path / "journal.sqlite3"
    journal.append_receipts(database, [_receipt()])
    monkeypatch.setattr(journal, "MAX_SNAPSHOT_RETAINED", 2)
    monkeypatch.setattr(journal, "SNAPSHOT_RETENTION_SLACK", 0)
    _fill_retention(database, 2)
    assert len(_snapshots(database)) == 2, "the ceiling was not actually reached"
    # Nothing is deletable: every prune attempt frees exactly zero.
    monkeypatch.setattr(journal, "_prune_superseded",
                        lambda directory, newest, keep=None:
                            len(journal._snapshot_entries(directory)))
    with sqlite3.connect(database) as connection:
        before = connection.execute(
            "SELECT COUNT(*) FROM accepted_receipts").fetchone()[0]
    with pytest.raises(journal.JournalError, match="retention is full"):
        journal.append_receipts(database, [_receipt(contract_id="c2")])
    monkeypatch.undo()
    with sqlite3.connect(database) as connection:
        after = connection.execute(
            "SELECT COUNT(*) FROM accepted_receipts").fetchone()[0]
    assert after == before, "an unpublishable append was committed anyway"


def test_reaching_the_ceiling_alone_does_not_refuse_when_pruning_frees_room(
        tmp_path, monkeypatch):
    """Non-vacuity pair for the test above: the REFUSAL must need both parts.

    Same ceiling, same reached state, real pruning. If the capacity stop keyed
    on "ceiling reached" instead of "ceiling reached AND nothing was freed",
    this append would be refused too and the test above would prove nothing.
    """
    database = tmp_path / "journal.sqlite3"
    journal.append_receipts(database, [_receipt()])
    monkeypatch.setattr(journal, "MAX_SNAPSHOT_RETAINED", 2)
    monkeypatch.setattr(journal, "SNAPSHOT_RETENTION_SLACK", 0)
    _fill_retention(database, 2)
    assert len(_snapshots(database)) == 2
    result = journal.append_receipts(database, [_receipt(contract_id="c2")])
    assert result["publication"] == journal.PUBLISHED, result
    assert journal.journal_report(database)["journal"]["receipt_rows"] == 3


def test_pruning_tolerates_an_undeletable_snapshot(tmp_path, monkeypatch):
    database = tmp_path / "journal.sqlite3"
    monkeypatch.setattr(journal, "MAX_SNAPSHOT_RETAINED", 2)
    journal.append_receipts(database, [_receipt()])
    real_unlink = Path.unlink

    def refusing(self, *args, **kwargs):
        raise PermissionError(32, "in use")

    monkeypatch.setattr(Path, "unlink", refusing)
    for index in range(3):
        journal.append_receipts(database, [_receipt(contract_id=f"c{index}")])
    monkeypatch.setattr(Path, "unlink", real_unlink)
    # publication kept working even though nothing could be deleted
    assert journal.journal_report(database)["journal"]["snapshot_seq"] == 4
