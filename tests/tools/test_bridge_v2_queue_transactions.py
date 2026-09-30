# SPDX-License-Identifier: BUSL-1.1
"""F8 queue transaction fixtures (authored per operator directive; NOT executed yet).

Deterministic: ports are recording doubles, cut points are injected by monkeypatching the
module's own write helpers, and every runtime root is a tmp_path (never a live root).
"""
from __future__ import annotations

import ast
from contextlib import contextmanager
from datetime import datetime, timezone
import errno
import hashlib
import json
import os
from pathlib import Path

import pytest

from tools import bridge_v2_queue_transactions as qt
from tools.bridge_v2_queue_transactions import (Blocked, LockTimeout, Plan, QueueTransactionError, QueueTransactions,
                                                RecordConflict, Refused, claim_bytes, claim_key, claim_lock_path,
                                                mutex_name)

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 9, 29, 22, 0, tzinfo=timezone.utc)
CLAIM = {"agent": "claude-rco-2", "task_id": "t/1"}


class Recorder:
    """Records lock order; can refuse one lock kind to model a timeout."""

    def __init__(self, fail=None):
        self.events, self.fail = [], fail

    def port(self, kind):
        recorder = self

        class Port:
            @contextmanager
            def hold(self, target, timeout_seconds):
                if recorder.fail == kind:
                    raise LockTimeout(kind + " busy")
                recorder.events.append(("enter", kind, str(target)))
                try:
                    yield
                finally:
                    recorder.events.append(("exit", kind, str(target)))
        return Port()


def make(tmp_path, recorder=None, **kwargs):
    recorder = recorder or Recorder()
    counter = iter(range(1000))
    txns = QueueTransactions(tmp_path, mutex=recorder.port("mutex"), claim_lock=recorder.port("claim"),
                             clock=lambda: NOW, new_id=lambda: f"{next(counter):032x}", **kwargs)
    return txns, recorder


def claim_path(txns, name="task.json"):
    path = txns.root / "work_queue" / "claims" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def create(txns, path, obj=None, event=True, key="claim:t/1"):
    obj = obj or dict(CLAIM)
    return txns.transact("claim", path, key, lambda before: Plan(
        after=obj, expect_absent=True, result=obj, event={"type": "claim"} if event else None))


def beat(n):
    return dict(CLAIM, beat=n)


def wal_records(txns):
    paths = sorted(txns.wal_dir.glob("*.json")) + sorted(txns.final_dir.glob("*.json"))
    return [json.loads(p.read_text()) for p in paths]


def wal_states(txns):
    return sorted(record["state"] for record in wal_records(txns))


def outbox(txns):
    return sorted(txns.outbox_dir.glob("*.json")) if txns.outbox_dir.is_dir() else []


def test_ports_off_refuses_before_any_read_or_write(tmp_path):
    txns = QueueTransactions(tmp_path)
    with pytest.raises(QueueTransactionError, match="ports are off"):
        create(txns, claim_path(txns))
    assert not (tmp_path / "work_queue" / "v2").exists()


def test_root_mutex_is_taken_before_the_legacy_sibling_claim_lock(tmp_path):
    txns, recorder = make(tmp_path)
    path = claim_path(txns)
    create(txns, path)
    assert [e[:2] for e in recorder.events] == [("enter", "mutex"), ("enter", "claim"),
                                                ("exit", "claim"), ("exit", "mutex")]
    assert recorder.events[0][2] == mutex_name(tmp_path)
    assert recorder.events[1][2] == str(path) + ".lock"          # "$ClaimPath.lock" spelling
    assert claim_lock_path(path).name == "task.json.lock"


@pytest.mark.parametrize("kind", ["mutex", "claim"])
def test_a_lock_timeout_mutates_nothing(tmp_path, kind):
    txns, _ = make(tmp_path, Recorder(fail=kind))
    path = claim_path(txns)
    with pytest.raises(LockTimeout):
        create(txns, path)
    assert not path.exists() and not txns.wal_dir.exists()


def test_the_mutex_name_is_derived_from_the_normalized_root(tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    assert mutex_name(tmp_path) != mutex_name(other)                # never one global constant
    assert mutex_name(str(tmp_path) + "/") == mutex_name(tmp_path)   # trailing separator ignored
    assert mutex_name(tmp_path).startswith("Global\\WaggleDanceBridgeV2Queue-")
    with pytest.raises(QueueTransactionError):
        mutex_name("relative/root")


@pytest.mark.parametrize("target", ["elsewhere.json", "work_queue/claims/sub/task.json", "work_queue/done/t.json"])
def test_a_claim_path_outside_the_claims_directory_is_refused_before_any_lock(tmp_path, target):
    txns, recorder = make(tmp_path)
    with pytest.raises(QueueTransactionError, match="outside the runtime root|legal work_queue record"):
        create(txns, tmp_path / target)
    assert recorder.events == [] and not txns.wal_dir.exists()   # N4: checked before any lock
    create(txns, claim_path(txns))                                # success twin
    assert recorder.events[0][:2] == ("enter", "mutex")


def test_a_create_replace_and_delete_leave_a_complete_filed_wal_and_one_outbox_record_each(tmp_path):
    txns, _ = make(tmp_path)
    path = claim_path(txns)
    create(txns, path)
    assert path.read_bytes() == claim_bytes(CLAIM)
    txns.transact("heartbeat", path, "hb:1", lambda before: Plan(after=beat(1)))
    archive = txns.root / "work_queue" / "done" / "t_1-x.json"
    txns.transact("release", path, "rel:1", lambda before: Plan(after=None, archive=(archive, {"released": True}),
                                                                event={"type": "release"}))
    assert not path.exists() and json.loads(archive.read_text()) == {"released": True}
    assert wal_states(txns) == ["outboxed", "outboxed", "outboxed"]
    assert list(txns.wal_dir.glob("*.json")) == []                 # every finished record is filed
    assert len(outbox(txns)) == 2                                  # heartbeat publishes nothing
    for record in wal_records(txns):
        assert record["root_identity"] == qt.root_identity(tmp_path)
        assert (txns.final_dir / f"{claim_key(record['claim_rel'])}.{record['txid']}.json").exists()


def test_a_missing_claim_is_never_recreated(tmp_path):
    txns, _ = make(tmp_path)
    path = claim_path(txns)
    with pytest.raises(Refused, match="never recreated"):
        txns.transact("heartbeat", path, "hb", lambda before: Plan(after={"x": 1}))
    assert not path.exists() and not txns.wal_dir.exists()


def test_a_refusing_plan_mutates_nothing(tmp_path):
    txns, _ = make(tmp_path)
    path = claim_path(txns)
    create(txns, path)
    before = path.read_bytes()

    def refuse(_before):
        raise Refused("owner mismatch")
    with pytest.raises(Refused):
        txns.transact("release", path, "rel", refuse)
    assert path.read_bytes() == before and wal_states(txns) == ["outboxed"]


def test_compare_and_swap_refuses_a_change_made_outside_the_lock(tmp_path):
    txns, _ = make(tmp_path)
    path = claim_path(txns)
    create(txns, path)

    def racing_plan(before):
        path.write_bytes(b'{"written": "by a writer that ignores the lock"}\n')   # legacy unfenced writer
        return Plan(after=beat(2))
    with pytest.raises(Refused, match="changed outside the lock"):
        txns.transact("heartbeat", path, "hb", racing_plan)
    assert json.loads(path.read_text()) == {"written": "by a writer that ignores the lock"}
    assert wal_states(txns) == ["aborted", "outboxed"]


def _crash_on(monkeypatch, name, nth=1):
    real = getattr(qt, name)
    calls = {"n": 0}

    def crashing(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == nth:
            raise KeyboardInterrupt("simulated crash")
        return real(*args, **kwargs)
    monkeypatch.setattr(qt, name, crashing)


def test_a_crash_before_the_claim_write_is_redone_not_falsely_aborted(tmp_path, monkeypatch):
    txns, _ = make(tmp_path)
    path = claim_path(txns)
    create(txns, path)
    _crash_on(monkeypatch, "_replace_atomic")        # the claim replace crashes
    with pytest.raises(KeyboardInterrupt):
        txns.transact("heartbeat", path, "hb:9", lambda b: Plan(after=beat(9)))
    monkeypatch.undo()
    assert path.read_bytes() == claim_bytes(CLAIM) and wal_states(txns) == ["outboxed", "prepared"]
    assert [o["outcome"] for o in txns.reconcile()] == ["rolled_forward"]   # decided under the locks: redone
    assert path.read_bytes() == claim_bytes(beat(9)) and wal_states(txns) == ["outboxed", "outboxed"]


def test_the_next_transaction_recovers_its_claim_before_its_plan_reads_it(tmp_path, monkeypatch):
    txns, _ = make(tmp_path)
    path = claim_path(txns)
    create(txns, path)
    _crash_on(monkeypatch, "_replace_atomic")
    with pytest.raises(KeyboardInterrupt):
        txns.transact("heartbeat", path, "hb:9", lambda b: Plan(after=beat(9)))
    monkeypatch.undo()
    seen = []
    txns.transact("heartbeat", path, "hb:10", lambda b: seen.append(b) or Plan(after=beat(10)))
    assert seen == [claim_bytes(beat(9))]                          # the retried attempt's outcome, not the old bytes
    assert path.read_bytes() == claim_bytes(beat(10)) and wal_states(txns) == ["outboxed"] * 3


def test_crash_after_apply_rolls_the_bookkeeping_forward(tmp_path, monkeypatch):
    txns, _ = make(tmp_path)
    path = claim_path(txns)
    real_set_state = QueueTransactions._set_state

    def crash_on_applied(self, wal_path, txn, state, reason=None):
        if state == "applied":
            raise KeyboardInterrupt("simulated crash after apply")
        return real_set_state(self, wal_path, txn, state, reason)
    monkeypatch.setattr(QueueTransactions, "_set_state", crash_on_applied)
    with pytest.raises(KeyboardInterrupt):
        create(txns, path)
    monkeypatch.undo()
    assert path.exists() and wal_states(txns) == ["prepared"]
    assert [o["outcome"] for o in txns.reconcile()] == ["rolled_forward_bookkeeping"]
    assert wal_states(txns) == ["outboxed"] and len(outbox(txns)) == 1


def test_s1_a_create_applied_before_its_bookkeeping_is_published_before_the_owner_releases(tmp_path, monkeypatch):
    # RCO1 S1 (ii): the old reconcile called this create "aborted" after the release; its event was lost.
    txns, _ = make(tmp_path)
    path = claim_path(txns)
    real_set_state = QueueTransactions._set_state
    monkeypatch.setattr(QueueTransactions, "_set_state", lambda self, w, t, state, reason=None: (
        (_ for _ in ()).throw(KeyboardInterrupt()) if state == "applied" else real_set_state(self, w, t, state, reason)))
    with pytest.raises(KeyboardInterrupt):
        create(txns, path)
    monkeypatch.undo()
    archive = txns.root / "work_queue" / "done" / "t_1-r.json"
    txns.transact("release", path, "rel:1", lambda b: Plan(after=None, archive=(archive, {"r": 1}),
                                                           event={"type": "release"}))
    events = sorted(json.loads(p.read_text())["event"]["type"] for p in outbox(txns))
    assert events == ["claim", "release"] and "aborted" not in wal_states(txns)
    assert wal_states(txns) == ["outboxed", "outboxed"] and txns.reconcile() == []


def test_s1_a_retried_release_after_a_failed_unlink_archives_once_and_publishes_once(tmp_path, monkeypatch):
    # RCO1 S1 (i): T1 wrote its archive, then the claim unlink failed; the retry used to archive
    # a second time and reconcile then published a second release under another key.
    txns, _ = make(tmp_path)
    path = claim_path(txns)
    create(txns, path)
    done = txns.root / "work_queue" / "done"
    real_unlink = Path.unlink

    def unlink(self, *args, **kwargs):
        if self == path:
            raise PermissionError(13, "a reader holds the claim open")
        return real_unlink(self, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", unlink)
    with pytest.raises(PermissionError):
        txns.transact("release", path, "rel:1", lambda b: Plan(after=None, archive=(done / "t_1-a1.json", {"r": 1}),
                                                               event={"type": "release"}))
    monkeypatch.undo()
    assert path.exists() and (done / "t_1-a1.json").exists()

    def retry(before):
        if before is None:
            raise Refused("no active claim for task (archived or replaced meanwhile)")
        return Plan(after=None, archive=(done / "t_1-a2.json", {"r": 2}), event={"type": "release"})
    with pytest.raises(Refused, match="no active claim") as refused:
        txns.transact("release", path, "rel:2", retry)
    # Q-OUTCOME-TRUTH: the refusal carries the earlier work this call DID complete first.
    assert [o["outcome"] for o in refused.value.recovered] == ["rolled_forward"]
    assert not path.exists() and sorted(p.name for p in done.glob("*.json")) == ["t_1-a1.json"]   # one archive
    releases = [json.loads(p.read_text()) for p in outbox(txns) if json.loads(p.read_text())["op"] == "release"]
    assert [r["idempotency_key"] for r in releases] == ["rel:1"]                                  # one event
    assert wal_states(txns) == ["outboxed", "outboxed"]


def test_a_claim_changed_after_a_crash_is_diverged_blocks_and_is_left_alone(tmp_path, monkeypatch):
    txns, _ = make(tmp_path)
    path = claim_path(txns)
    create(txns, path)
    _crash_on(monkeypatch, "_replace_atomic")
    with pytest.raises(KeyboardInterrupt):
        txns.transact("heartbeat", path, "hb", lambda b: Plan(after=beat(9)))
    monkeypatch.undo()
    path.write_bytes(b'{"someone": "else"}\n')
    assert [o["outcome"] for o in txns.reconcile()] == ["diverged", "blocked"]
    assert path.read_bytes() == b'{"someone": "else"}\n'
    with pytest.raises(Blocked, match="diverged"):
        txns.transact("heartbeat", path, "hb:2", lambda b: Plan(after=beat(2)))
    assert path.read_bytes() == b'{"someone": "else"}\n'


def test_a_corrupt_wal_record_is_reported_never_acted_on_and_blocks_its_claim(tmp_path):
    txns, _ = make(tmp_path)
    txns.wal_dir.mkdir(parents=True)
    (txns.wal_dir / "bad.json").write_text("{not json", encoding="utf-8")
    assert txns.reconcile() == [{"wal": "bad.json", "outcome": "corrupt"}]
    path = claim_path(txns)
    create(txns, path)                                             # success twin: "bad.json" names no claim
    own = txns.wal_dir / f"{claim_key('work_queue/claims/task.json')}.{'e' * 32}.json"
    own.write_text("{not json", encoding="utf-8")
    with pytest.raises(Blocked, match="corrupt"):
        txns.transact("heartbeat", path, "hb", lambda b: Plan(after=beat(1)))
    assert path.read_bytes() == claim_bytes(CLAIM)


def test_publication_is_outside_the_locks_at_least_once_and_never_lost(tmp_path):
    txns, recorder = make(tmp_path)
    create(txns, claim_path(txns))
    assert txns.publish_pending(None) == {"published": 0, "pending": 1, "failed": 0, "rejected": 0,
                                          "rejected_records": []}
    published = []

    class Flaky:
        def __init__(self):
            self.calls = 0

        def publish(self, record):
            self.calls += 1
            if self.calls == 1:
                raise OSError("writer unavailable")
            published.append(record["idempotency_key"])
    flaky = Flaky()
    locks_before = len(recorder.events)
    assert txns.publish_pending(flaky)["failed"] == 1
    assert txns.publish_pending(flaky)["published"] == 1
    assert txns.publish_pending(flaky)["published"] == 0           # marked: not republished
    assert published == ["claim:t/1"]
    assert recorder.events[locks_before:] == []                    # publication took no lock of any kind


def test_s4_a_lost_marker_republishes_the_same_key_so_the_publisher_must_be_idempotent(tmp_path, monkeypatch):
    txns, _ = make(tmp_path)
    create(txns, claim_path(txns))
    real_create = qt._create_atomic
    monkeypatch.setattr(qt, "_create_atomic", lambda path, data: (_ for _ in ()).throw(OSError("crash"))
                        if path.name.endswith(".published") else real_create(path, data))
    keys = []

    class Publisher:
        def publish(self, record):
            keys.append(record["idempotency_key"])
    assert txns.publish_pending(Publisher())["published"] == 1
    assert txns.publish_pending(Publisher())["published"] == 1     # at least once, never exactly once
    assert keys == ["claim:t/1", "claim:t/1"]


def test_s2_a_differing_or_torn_outbox_record_is_never_swallowed(tmp_path):
    txns, _ = make(tmp_path)
    path = claim_path(txns)
    txns.outbox_dir.mkdir(parents=True)
    squatter = txns.outbox_dir / (hashlib.sha256(b"claim:t/1").hexdigest() + ".json")
    squatter.write_bytes(b'{"torn": ')
    with pytest.raises(QueueTransactionError, match="WAS applied"):
        create(txns, path)
    assert path.read_bytes() == claim_bytes(CLAIM)                 # the claim change did happen, truthfully
    [record] = wal_records(txns)
    assert record["state"] == "diverged" and record["reason"] == "outbox_record_conflict"
    assert squatter.read_bytes() == b'{"torn": '                   # never overwritten
    report = txns.publish_pending(None)
    assert report["rejected"] == 1 and report["rejected_records"] == [squatter.name] and report["pending"] == 0
    with pytest.raises(Blocked, match="outbox_record_conflict"):
        txns.transact("heartbeat", path, "hb", lambda b: Plan(after=beat(1)))


def test_s2_a_crash_while_writing_a_record_leaves_no_torn_record_under_its_final_name(tmp_path, monkeypatch):
    txns, _ = make(tmp_path)
    path = claim_path(txns)
    real_write = qt._write_exclusive

    def torn(target, data):
        if target.parent == txns.outbox_dir:
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "wb") as stream:
                stream.write(data[: len(data) // 2])
            raise KeyboardInterrupt("crash mid-write")
        return real_write(target, data)
    monkeypatch.setattr(qt, "_write_exclusive", torn)
    with pytest.raises(KeyboardInterrupt):
        create(txns, path)
    monkeypatch.undo()
    assert outbox(txns) == [] and wal_states(txns) == ["applied"]    # only the temporary file was torn
    assert [o["outcome"] for o in txns.reconcile()] == ["outboxed"]
    assert len(outbox(txns)) == 1 and txns.publish_pending(None)["pending"] == 1


def test_s2_the_same_idempotency_key_from_another_transaction_is_a_visible_conflict(tmp_path):
    txns, _ = make(tmp_path)
    path = claim_path(txns)
    create(txns, path)
    with pytest.raises(QueueTransactionError, match="WAS applied"):
        txns.transact("claim", path, "claim:t/1", lambda b: Plan(after=dict(CLAIM, v=2), event={"type": "claim"}))
    assert len(outbox(txns)) == 1 and wal_states(txns) == ["diverged", "outboxed"]


def bound_record(txns, state="applied", **changes):
    """A WAL record exactly as transact binds it, with optional changes."""
    record = {"schema": qt.TXN_SCHEMA, "root_identity": txns.root_id, "txid": "a" * 32, "idempotency_key": "k",
              "op": "claim", "claim_rel": "work_queue/claims/task.json", "before_sha256": None,
              "after": dict(CLAIM), "after_sha256": qt._digest(CLAIM), "archive_rel": None, "archive": None,
              "archive_sha256": None, "event": {"type": "claim"}, "event_sha256": qt._digest({"type": "claim"}),
              "state": state, "created_utc": NOW.isoformat()}
    record.update(changes)
    return record


def write_record(txns, record, txid=None):
    txns.wal_dir.mkdir(parents=True, exist_ok=True)
    rel = record["claim_rel"] if isinstance(record.get("claim_rel"), str) else "x"
    name = f"{claim_key(rel)}.{txid or record['txid']}.json"
    (txns.wal_dir / name).write_bytes(claim_bytes(record))
    return name


@pytest.mark.parametrize("change", [
    {"claim_rel": "../../outside.json"}, {"archive_rel": "work_queue/claims/x.json"}, {"op": "force_takeover"},
    {"txid": "not-hex"}, {"event": ["not", "a", "dict"]}, {"before_sha256": "zz"},
    {"root_identity": "0" * 64},                                   # copied from another runtime root
    {"after_sha256": "0" * 64},                                    # after-state digest does not bind
    {"event": {"type": "release"}, "event_sha256": qt._digest({"type": "release"})},   # event type is not the op
    {"event": {"type": "claim", "generation_before": "1" * 64},
     "event_sha256": qt._digest({"type": "claim", "generation_before": "1" * 64})},    # another generation
    {"archive_rel": "work_queue/done/x.json"},                     # archive path without an archive record
    {"injected": True}])                                           # a key transact never writes
def test_s3_a_forged_or_unbound_wal_record_is_corrupt_and_never_outboxed(tmp_path, change):
    txns, _ = make(tmp_path)
    name = write_record(txns, bound_record(txns, **change))
    assert txns.reconcile() == [{"wal": name, "outcome": "corrupt"}]
    assert outbox(txns) == []


def test_s3_a_record_under_another_txid_name_is_corrupt(tmp_path):
    txns, _ = make(tmp_path)
    name = write_record(txns, bound_record(txns), txid="b" * 32)
    assert txns.reconcile() == [{"wal": name, "outcome": "corrupt"}] and outbox(txns) == []


def test_s3_a_bound_applied_record_is_outboxed_only_when_its_mutation_is_on_disk(tmp_path):
    txns, _ = make(tmp_path)
    write_record(txns, bound_record(txns))
    assert [o["outcome"] for o in txns.reconcile()] == ["diverged", "blocked"]   # nothing on disk: no event
    assert outbox(txns) == []
    other, _ = make(tmp_path / "twin")
    path = claim_path(other)
    path.write_bytes(claim_bytes(CLAIM))                            # success twin: the after-state IS on disk
    write_record(other, bound_record(other))
    assert [o["outcome"] for o in other.reconcile()] == ["outboxed"] and len(outbox(other)) == 1


def test_s3_publication_rejects_records_that_do_not_bind_to_an_outboxed_wal_record(tmp_path):
    txns, _ = make(tmp_path)
    create(txns, claim_path(txns))
    [genuine] = outbox(txns)
    record = json.loads(genuine.read_text())
    forged = dict(record, idempotency_key="forged", txid="c" * 32)   # no WAL record behind it
    (txns.outbox_dir / (hashlib.sha256(b"forged").hexdigest() + ".json")).write_bytes(claim_bytes(forged))
    foreign = dict(record, idempotency_key="foreign", root_identity="0" * 64)
    (txns.outbox_dir / (hashlib.sha256(b"foreign").hexdigest() + ".json")).write_bytes(claim_bytes(foreign))
    (txns.outbox_dir / ("d" * 64 + ".json")).write_bytes(b'{"torn"')
    published = []

    class Publisher:
        def publish(self, item):
            published.append(item["idempotency_key"])
    report = txns.publish_pending(Publisher())
    assert report["published"] == 1 and report["rejected"] == 3 and published == ["claim:t/1"]


def test_s9_a_record_over_the_read_bound_or_malformed_is_refused_before_anything_is_written(tmp_path):
    txns, _ = make(tmp_path)
    path = claim_path(txns)
    with pytest.raises(QueueTransactionError, match="256 KiB"):
        create(txns, path, obj=dict(CLAIM, pad="x" * (300 * 1024)))
    with pytest.raises(QueueTransactionError, match="malformed"):
        txns.transact("claim", path, "k", lambda b: Plan(after=dict(CLAIM), expect_absent=True,
                                                         event={"type": "release"}))
    with pytest.raises(QueueTransactionError, match="unknown queue operation"):
        txns.transact("takeover", path, "k", lambda b: Plan(after=dict(CLAIM), expect_absent=True))
    with pytest.raises(QueueTransactionError, match="idempotency key"):
        txns.transact("claim", path, "k" * 513, lambda b: Plan(after=dict(CLAIM), expect_absent=True))
    assert not path.exists() and not txns.wal_dir.exists()
    create(txns, path)                                              # success twin
    assert path.exists()


def test_an_existing_archive_record_is_a_conflict_refused_before_anything_is_written(tmp_path):
    txns, _ = make(tmp_path)
    path = claim_path(txns)
    create(txns, path)
    archive = txns.root / "work_queue" / "done" / "t_1-x.json"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b'{"someone": "else"}\n')
    with pytest.raises(RecordConflict, match="already exists"):
        txns.transact("release", path, "rel", lambda b: Plan(after=None, archive=(archive, {"r": 1}),
                                                             event={"type": "release"}))
    assert path.exists() and archive.read_bytes() == b'{"someone": "else"}\n' and wal_states(txns) == ["outboxed"]


def _link_or_skip(link: Path, target: Path, directory: bool = False) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(target, link, target_is_directory=directory)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are not available to this account")


def test_q_path_a_linked_claim_leaf_is_refused_before_any_lock_read_or_write(tmp_path):
    txns, recorder = make(tmp_path / "root")
    outside = tmp_path / "outside.json"
    outside.write_bytes(b'{"secret": 1}\n')
    path = txns.root / "work_queue" / "claims" / "task.json"
    _link_or_skip(path, outside)
    with pytest.raises(QueueTransactionError, match="link"):
        txns.transact("heartbeat", path, "hb", lambda b: Plan(after=beat(1)))
    assert recorder.events == [] and outside.read_bytes() == b'{"secret": 1}\n' and not txns.wal_dir.exists()


@pytest.mark.parametrize("linked", ["work_queue/done", "work_queue/v2", "work_queue/v2/wal"])
def test_q_path_a_linked_state_directory_is_refused_before_any_effect(tmp_path, linked):
    txns, recorder = make(tmp_path / "root")
    path = claim_path(txns)
    path.write_bytes(claim_bytes(CLAIM))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    _link_or_skip(txns.root / linked, elsewhere, directory=True)
    archive = txns.root / "work_queue" / "done" / "t_1-x.json"
    with pytest.raises(QueueTransactionError, match="link"):
        txns.transact("release", path, "rel", lambda b: Plan(after=None, archive=(archive, {"r": 1}),
                                                             event={"type": "release"}))
    assert recorder.events == [] and path.read_bytes() == claim_bytes(CLAIM) and list(elsewhere.iterdir()) == []


def test_q_path_recovery_rechecks_a_claim_that_became_a_link(tmp_path, monkeypatch):
    txns, _ = make(tmp_path / "root")
    path = claim_path(txns)
    create(txns, path)
    _crash_on(monkeypatch, "_replace_atomic")
    with pytest.raises(KeyboardInterrupt):
        txns.transact("heartbeat", path, "hb:9", lambda b: Plan(after=beat(9)))
    monkeypatch.undo()
    outside = tmp_path / "outside.json"
    outside.write_bytes(b'{"secret": 1}\n')
    path.unlink()
    _link_or_skip(path, outside)
    outcomes = txns.reconcile()
    assert [o["outcome"] for o in outcomes] == ["blocked"] and "link" in outcomes[0]["reason"]
    assert outside.read_bytes() == b'{"secret": 1}\n' and wal_states(txns) == ["outboxed", "prepared"]


def test_q_wal_every_later_state_and_reason_is_reserved_before_any_mutation(tmp_path, monkeypatch):
    probe, _ = make(tmp_path / "probe")          # the same deterministic record on another root
    create(probe, claim_path(probe))
    [record] = wal_records(probe)
    worst = len(claim_bytes(dict(record, state=qt.LONGEST_STATE, reason=qt.LONGEST_REASON)))
    txns, _ = make(tmp_path / "real")
    path = claim_path(txns)
    monkeypatch.setattr(qt, "MAX_RECORD_BYTES", worst - 1)
    with pytest.raises(QueueTransactionError, match="every later state and reason"):
        create(txns, path)
    assert not path.exists() and not txns.wal_dir.exists()             # refused before any mutation
    monkeypatch.setattr(qt, "MAX_RECORD_BYTES", worst)                 # success twin: it exactly fits
    real_set_state = QueueTransactions._set_state

    def crash_on_applied(self, wal_path, txn, state, reason=None):
        if state == "applied" and reason is None:
            raise KeyboardInterrupt("crash after the mutation")
        return real_set_state(self, wal_path, txn, state, reason)
    monkeypatch.setattr(QueueTransactions, "_set_state", crash_on_applied)
    with pytest.raises(KeyboardInterrupt):
        create(txns, path)
    monkeypatch.setattr(QueueTransactions, "_set_state", real_set_state)
    assert [o["outcome"] for o in txns.reconcile()] == ["rolled_forward_bookkeeping"]   # a reason is recorded
    assert [len(p.read_bytes()) <= worst for p in txns.final_dir.glob("*.json")] == [True]


def test_q_durability_a_zero_progress_write_refuses_and_a_cleanup_failure_never_masks_it(tmp_path, monkeypatch):
    txns, _ = make(tmp_path)
    path = claim_path(txns)
    monkeypatch.setattr(qt, "_os_write", lambda fd, data: 0)
    real_unlink = Path.unlink

    def failing_cleanup(self, *args, **kwargs):
        if ".v2tmp." in self.name:
            raise PermissionError(13, "cleanup refused")
        return real_unlink(self, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", failing_cleanup)
    with pytest.raises(QueueTransactionError, match="zero-progress"):   # the primary error, not the cleanup one
        create(txns, path)
    assert not path.exists() and wal_states(txns) == []


def test_q_durability_short_writes_still_complete_the_record(tmp_path, monkeypatch):
    txns, _ = make(tmp_path)
    path = claim_path(txns)
    real_write = os.write
    monkeypatch.setattr(qt, "_os_write", lambda fd, data: real_write(fd, bytes(data[:7])))   # 7 bytes at a time
    create(txns, path)
    assert path.read_bytes() == claim_bytes(CLAIM) and wal_states(txns) == ["outboxed"]


ENV_NAMES = {"environ", "environb", "getenv", "getenvb", "putenv", "unsetenv"}


def test_modules_never_import_waggledance_and_read_the_environment_in_one_place_only():
    for name in ("bridge_v2_queue_transactions.py", "bridge_v2_work_queue.py", "bridge_v2_resource_scope.py"):
        tree = ast.parse((ROOT / "tools" / name).read_text(encoding="utf-8"))
        modules = {getattr(n, "module", None) or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        modules |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        assert not any(m.split(".")[0] == "waggledance" for m in modules), name
        # No evasion: no `from os import environ`, no bare environ/getenv names, no getattr(os, "environ").
        imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
        assert not imported & ENV_NAMES, name
        assert not any(isinstance(n, ast.Name) and n.id in ENV_NAMES for n in ast.walk(tree)), name
        assert not any(isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value in ENV_NAMES
                       for n in ast.walk(tree)), name
        readers = {fn.name for fn in ast.walk(tree) if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
                   for n in ast.walk(fn) if isinstance(n, ast.Attribute) and n.attr in ENV_NAMES}
        module_level = [n for n in tree.body if not isinstance(n, (ast.FunctionDef, ast.ClassDef))
                        for m in ast.walk(n) if isinstance(m, ast.Attribute) and m.attr in ENV_NAMES]
        # The consumer-facing facade's resolve_bridge_root is the single documented env reader.
        assert readers <= ({"resolve_bridge_root"} if name == "bridge_v2_work_queue.py" else set()), (name, readers)
        assert not module_level, name


# -- W-LOCK-PATH: FileClaimLock re-walks the sibling lock leaf before EACH open attempt --

def test_file_claim_lock_refuses_a_linked_leaf_before_any_open(tmp_path, monkeypatch):
    outside = tmp_path / "outside.lock"
    outside.write_bytes(b"")
    lock = tmp_path / "root" / "work_queue" / "claims" / "task.json.lock"
    _link_or_skip(lock, outside)
    opened = []
    monkeypatch.setattr(qt, "_open_lock", lambda path: opened.append(path))
    with pytest.raises(QueueTransactionError, match="link"):
        with qt.FileClaimLock().hold(lock, 2.0):
            pytest.fail("the body must not run")
    assert opened == [] and outside.read_bytes() == b""       # refused at once: no open, no wait


def test_file_claim_lock_refuses_a_directory_leaf_before_any_open(tmp_path, monkeypatch):
    lock = tmp_path / "work_queue" / "claims" / "task.json.lock"
    lock.mkdir(parents=True)
    opened = []
    monkeypatch.setattr(qt, "_open_lock", lambda path: opened.append(path))
    with pytest.raises(QueueTransactionError, match="not a regular file"):
        with qt.FileClaimLock().hold(lock, 2.0):
            pytest.fail("the body must not run")
    assert opened == []


def test_file_claim_lock_rechecks_the_leaf_before_each_attempt_and_survives_short_contention(tmp_path, monkeypatch):
    lock = tmp_path / "work_queue" / "claims" / "task.json.lock"
    lock.parent.mkdir(parents=True)
    guarded, attempts = [], []
    real_guard, real_open = qt._guard, qt._open_lock
    monkeypatch.setattr(qt, "_guard", lambda path, kind, leaf="file": guarded.append(kind) or real_guard(path, kind, leaf))

    def contended_once(path):
        attempts.append(path)
        if len(attempts) == 1:
            raise BlockingIOError(errno.EAGAIN, "held by another process")   # contention: retried
        return real_open(path)
    monkeypatch.setattr(qt, "_open_lock", contended_once)
    ran = []
    with qt.FileClaimLock().hold(lock, 2.0):
        ran.append(True)
    assert ran == [True] and len(attempts) == 2 and guarded == ["claim lock", "claim lock"]   # a guard per open


def test_file_claim_lock_refuses_a_link_swapped_in_after_the_guard(tmp_path, monkeypatch):
    lock = tmp_path / "work_queue" / "claims" / "task.json.lock"
    lock.parent.mkdir(parents=True)

    def swapped(path):
        raise OSError(errno.ELOOP, "O_NOFOLLOW refused a symbolic link")   # the race, as POSIX reports it
    monkeypatch.setattr(qt, "_open_lock", swapped)
    with pytest.raises(QueueTransactionError, match="link or directory at open time"):
        with qt.FileClaimLock().hold(lock, 2.0):
            pytest.fail("the body must not run")
