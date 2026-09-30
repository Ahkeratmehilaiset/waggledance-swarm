# SPDX-License-Identifier: BUSL-1.1
"""Read-only v2 queue claims snapshot producer (RCO1 2026-09-30, Lead f8d485a1). Fixture roots only.

The schema is W3's, read from frozen Git (tools/wd_routing_load.py at 15485884, blob 41b53433): SNAPSHOT_KEYS
and ENTRY_KEYS below are copied from it. Negatives first; every refusal has a success twin.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from tools import bridge_v2_queue_snapshot as snap
from tools import bridge_v2_queue_transactions as qt
from tools import bridge_v2_work_queue as wq
from tools.bridge_v2_queue_transactions import QueueTransactionError, QueueTransactions, claim_bytes
from tools.bridge_v2_work_queue import OwnerIdentity

W3_SNAPSHOT_KEYS = {"schema", "observed_utc", "complete", "unreadable", "claims", "pending"}
W3_ENTRY_KEYS = {"source", "agent", "task_id", "owner_session_id"}
NOW = datetime(2026, 9, 30, 21, 20, tzinfo=timezone.utc)
OWNER, OTHER = OwnerIdentity("session-a", "token-a"), OwnerIdentity("session-b", "token-b")


class RecordingMutex:
    def __init__(self) -> None:
        self.held, self.names = False, []

    @contextmanager
    def hold(self, name, timeout_seconds):
        self.names.append(name)
        self.held = True
        try:
            yield
        finally:
            self.held = False


class Lock:
    @contextmanager
    def hold(self, target, timeout_seconds):
        yield


@pytest.fixture
def root(tmp_path):
    (tmp_path / "wt" / "tools").mkdir(parents=True)
    runtime = tmp_path / "runtime"
    (runtime / "work_queue" / "claims").mkdir(parents=True)
    return runtime


def _claim(root: Path, task: str, agent: str = "claude-rco-2", identity=OWNER, scope: str = "tools/a.py",
           now: datetime = NOW) -> dict:
    txns = QueueTransactions(root, mutex=Lock(), claim_lock=Lock(), clock=lambda: now)
    return wq.claim_task(txns, agent=agent, task_id=task, summary="work", mode="write", write_scope=(scope,),
                         identity=identity, cwd=str(root.parent / "wt"), now=now)


def _prepared(root: Path, monkeypatch, task: str, agent: str = "fable-5", identity=OTHER, scope: str = "tools/b.py"):
    real, calls = qt._replace_atomic, {"n": 0}

    def crashing(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise KeyboardInterrupt("crash after the WAL record, before the claim write")
        return real(*args, **kwargs)
    monkeypatch.setattr(qt, "_replace_atomic", crashing)
    with pytest.raises(KeyboardInterrupt):
        _claim(root, task, agent=agent, identity=identity, scope=scope)
    monkeypatch.undo()


def _take(root: Path, mutex=None) -> dict:
    return snap.queue_claims_snapshot(root, mutex=mutex or RecordingMutex(), clock=lambda: NOW)


def _tree(root: Path) -> dict:
    return {str(p.relative_to(root)): (p.read_bytes() if p.is_file() else None) for p in sorted(root.rglob("*"))}


# -- negatives -------------------------------------------------------------------------------------------------

BAD_CLAIMS = {
    "not_json": b"{not json",
    "not_object": b"[]",
    "no_agent": claim_bytes({"task_id": "t/x", "owner_identity": "none"}),
    "agent_not_text": claim_bytes({"agent": 12, "task_id": "t/x", "owner_identity": "none"}),
    "empty_task": claim_bytes({"agent": "fable-5", "task_id": "", "owner_identity": "none"}),
    "session_not_text": claim_bytes({"agent": "fable-5", "task_id": "t/x", "owner_session_id": 12,
                                     "owner_token_sha256": "a" * 64}),
    "session_without_token": claim_bytes({"agent": "fable-5", "task_id": "t/x", "owner_session_id": "s"}),
    "no_session_evidence_pre_b7": claim_bytes({"agent": "fable-5", "task_id": "t/x"}),
    "empty_session": claim_bytes({"agent": "fable-5", "task_id": "t/x", "owner_session_id": "",
                                  "owner_token_sha256": "a" * 64}),
}


@pytest.mark.parametrize("name", sorted(BAD_CLAIMS))
def test_an_unprovable_claim_is_unreadable_beside_a_readable_one(root, name):
    _claim(root, "team/ok")
    (root / "work_queue" / "claims" / ("zz-" + name + ".json")).write_bytes(BAD_CLAIMS[name])
    taken = _take(root)
    assert (taken["complete"], taken["unreadable"], len(taken["claims"])) == (True, 1, 1)


def test_a_corrupt_or_foreign_wal_record_is_unreadable(root, monkeypatch):
    _prepared(root, monkeypatch, "team/p")
    wal = QueueTransactions(root).wal_dir
    (wal / "garbage.json").write_bytes(b"{not json")
    [record] = [p for p in wal.glob("*.json") if p.name != "garbage.json"]
    foreign = record.read_bytes().replace(QueueTransactions(root).root_id.encode(), b"0" * 64)
    (wal / "foreign.json").write_bytes(foreign)
    taken = _take(root)
    assert (taken["unreadable"], len(taken["pending"])) == (2, 1)            # the real record still counts


def test_a_plan_for_one_task_beside_an_active_other_at_the_same_claim_is_incomplete(root, monkeypatch):
    _prepared(root, monkeypatch, "team/x")
    other = _claim(root, "team/y", scope="tools/y.py")
    claim_x = wq._new_claim_path(QueueTransactions(root), "team/x")
    claim_x.write_bytes(claim_bytes(other))                                  # Y now sits at X's claim path
    assert _take(root)["complete"] is False


def test_a_diverged_record_is_final_not_pending(root, monkeypatch):
    # Recovery marks it diverged (the claim matches neither side) and never applies it; the claim file is the fact.
    _prepared(root, monkeypatch, "team/d")
    claim_d = wq._new_claim_path(QueueTransactions(root), "team/d")
    claim_d.write_bytes(claim_bytes(_claim(root, "team/e", scope="tools/e.py")))
    assert [entry["outcome"] for entry in QueueTransactions(root, mutex=Lock(), claim_lock=Lock()).reconcile()
            if "outcome" in entry][0] == "diverged"
    taken = _take(root)
    assert (taken["complete"], taken["pending"]) == (True, [])


@pytest.mark.parametrize("layout", ["no_work_queue", "claims_is_a_file"])
def test_a_missing_or_wrong_claims_directory_is_never_an_empty_queue(tmp_path, layout):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    if layout == "claims_is_a_file":
        (runtime / "work_queue").mkdir()
        (runtime / "work_queue" / "claims").write_text("x", encoding="utf-8")
    taken = _take(runtime)
    assert (taken["complete"], taken["claims"], taken["pending"]) == (False, [], [])


def test_a_listing_error_or_a_vanished_file_makes_the_snapshot_incomplete(root, monkeypatch):
    _claim(root, "team/a")
    monkeypatch.setattr(snap, "read_bytes_or_none", lambda path, limit=None: None)
    assert _take(root)["complete"] is False                                  # listed, then gone
    monkeypatch.undo()

    def refuse(self):
        raise PermissionError("listing refused")
    monkeypatch.setattr(Path, "iterdir", refuse)
    assert _take(root)["complete"] is False


@pytest.mark.skipif(os.name != "nt", reason="a directory junction is the Windows reparse point")
def test_a_junctioned_claims_directory_is_unprovable(tmp_path):
    runtime, real = tmp_path / "runtime", tmp_path / "real-claims"
    (runtime / "work_queue").mkdir(parents=True)
    real.mkdir()
    made = subprocess.run(["cmd", "/c", "mklink", "/J", str(runtime / "work_queue" / "claims"), str(real)],
                          capture_output=True, text=True)
    if made.returncode != 0:
        pytest.skip("a junction could not be created here: " + made.stdout + made.stderr)
    assert _take(runtime)["complete"] is False


def test_over_the_entry_bound_is_unknown_not_truncated(root, monkeypatch):
    _claim(root, "team/a")
    _claim(root, "team/b", scope="tools/b.py")
    monkeypatch.setattr(snap, "MAX_ENTRIES", 1)
    taken = _take(root)
    assert (taken["complete"], taken["claims"]) == (False, [])


def test_every_read_happens_under_the_roots_own_queue_mutex(root, monkeypatch):
    _claim(root, "team/a")
    mutex, real = RecordingMutex(), snap.read_bytes_or_none

    def guarded(path, limit=None):
        assert mutex.held, "a read outside the root mutex"
        return real(path, limit)
    monkeypatch.setattr(snap, "read_bytes_or_none", guarded)
    assert len(_take(root, mutex)["claims"]) == 1
    assert mutex.names == [qt.mutex_name(root)] and not mutex.held         # the writers' own name, released


@pytest.mark.parametrize("error", [KeyboardInterrupt, SystemExit])
def test_cancellation_propagates_and_releases_the_mutex(root, monkeypatch, error):
    _claim(root, "team/a")
    mutex = RecordingMutex()

    def cancelled(path, limit=None):
        raise error()
    monkeypatch.setattr(snap, "read_bytes_or_none", cancelled)
    with pytest.raises(error):
        _take(root, mutex)
    assert not mutex.held


@pytest.mark.parametrize("kwargs", [{"mutex": None}, {"clock": "now"}, {"clock": lambda: NOW.replace(tzinfo=None)},
                                    {"lock_timeout_seconds": 0}, {"lock_timeout_seconds": True},
                                    {"lock_timeout_seconds": 61}], ids=["no_mutex", "clock_not_callable",
                                                                         "naive_clock", "timeout_zero",
                                                                         "timeout_bool", "timeout_over"])
def test_caller_contract_violations_refuse(root, kwargs):
    arguments = {"mutex": RecordingMutex(), "clock": lambda: NOW}
    arguments.update(kwargs)
    with pytest.raises(snap.QueueSnapshotError):
        snap.queue_claims_snapshot(root, **arguments)


def test_an_alias_root_is_refused_before_any_read(tmp_path):
    with pytest.raises(QueueTransactionError):
        _take(tmp_path / "PROGRA~1" / "runtime")


# -- positives -------------------------------------------------------------------------------------------------

def test_a_fully_empty_known_queue(root):
    taken = _take(root)
    assert taken == {"schema": snap.SNAPSHOT_SCHEMA, "observed_utc": "2026-09-30T21:20:00.000000Z", "complete": True,
                     "unreadable": 0, "claims": [], "pending": []}
    assert set(taken) == W3_SNAPSHOT_KEYS


def test_active_expired_identityless_and_pending_claims_are_exact_w3_entries(root, monkeypatch):
    _claim(root, "team/a")
    _claim(root, "team/old", agent="codex-tools-1", scope="tools/o.py", now=NOW - timedelta(hours=13))   # unswept
    _claim(root, "team/c", agent="claude-rco-1", identity=None, scope="tools/c.py")
    _prepared(root, monkeypatch, "team/p")
    taken = _take(root)
    assert (taken["complete"], taken["unreadable"]) == (True, 0)
    assert sorted((e["agent"], e["task_id"], e["owner_session_id"]) for e in taken["claims"]) == [
        ("claude-rco-1", "team/c", None), ("claude-rco-2", "team/a", "session-a"),
        ("codex-tools-1", "team/old", "session-a")]
    assert taken["pending"] == [{"source": "pending", "agent": "fable-5", "task_id": "team/p",
                                 "owner_session_id": "session-b"}]
    assert all(set(e) == W3_ENTRY_KEYS for e in taken["claims"] + taken["pending"])
    assert all(e["source"] == "claim" for e in taken["claims"])


def test_the_snapshot_changes_nothing_on_disk_and_is_never_aliased(root, monkeypatch):
    _claim(root, "team/a")
    _prepared(root, monkeypatch, "team/p")
    before = _tree(root)
    first = _take(root)
    assert _tree(root) == before                                              # no lock file, no directory, no write
    first["claims"][0]["agent"] = "changed"
    first["pending"].clear()
    second = _take(root)
    assert second["claims"][0]["agent"] == "claude-rco-2" and len(second["pending"]) == 1
    assert hashlib.sha256(repr(second).encode()).hexdigest() == hashlib.sha256(repr(_take(root)).encode()).hexdigest()
