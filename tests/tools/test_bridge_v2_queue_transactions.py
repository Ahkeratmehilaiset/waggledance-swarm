# SPDX-License-Identifier: BUSL-1.1
"""F8 queue transaction fixtures (authored per operator directive; NOT executed yet).

Deterministic: ports are recording doubles, cut points are injected by monkeypatching the
module's own write helpers, and every runtime root is a tmp_path (never a live root).
"""
from __future__ import annotations

import ast
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from tools import bridge_v2_queue_transactions as qt
from tools.bridge_v2_queue_transactions import (LockTimeout, Plan, QueueTransactionError, QueueTransactions,
                                                Refused, claim_bytes, claim_lock_path, mutex_name)

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 9, 29, 22, 0, tzinfo=timezone.utc)


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


def create(txns, path, obj=None, event=True):
    obj = obj or {"agent": "claude-rco-2", "task_id": "t/1"}
    return txns.transact("claim", path, "claim:t/1", lambda before: Plan(
        after=obj, expect_absent=True, result=obj, event={"type": "claim"} if event else None))


def wal_states(txns):
    return sorted(json.loads(p.read_text())["state"] for p in txns.wal_dir.glob("*.json"))


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


def test_a_create_replace_and_delete_leave_a_complete_wal_and_one_outbox_record(tmp_path):
    txns, _ = make(tmp_path)
    path = claim_path(txns)
    create(txns, path)
    assert path.read_bytes() == claim_bytes({"agent": "claude-rco-2", "task_id": "t/1"})
    txns.transact("heartbeat", path, "hb:1", lambda before: Plan(after={"agent": "claude-rco-2", "task_id": "t/1",
                                                                        "beat": 1}))
    archive = txns.root / "work_queue" / "done" / "t_1-x.json"
    txns.transact("release", path, "rel:1", lambda before: Plan(after=None, archive=(archive, {"released": True}),
                                                                event={"type": "release"}))
    assert not path.exists() and json.loads(archive.read_text()) == {"released": True}
    assert wal_states(txns) == ["outboxed", "outboxed", "outboxed"]
    assert len(list(txns.outbox_dir.glob("*.json"))) == 2          # heartbeat publishes nothing


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
        return Plan(after={"agent": "claude-rco-2", "task_id": "t/1", "beat": 2})
    with pytest.raises(Refused, match="changed outside the lock"):
        txns.transact("heartbeat", path, "hb", racing_plan)
    assert json.loads(path.read_text()) == {"written": "by a writer that ignores the lock"}
    assert "aborted" in wal_states(txns)


def _crash_on(monkeypatch, name, nth=1):
    real = getattr(qt, name)
    calls = {"n": 0}

    def crashing(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == nth:
            raise KeyboardInterrupt("simulated crash")
        return real(*args, **kwargs)
    monkeypatch.setattr(qt, name, crashing)


def test_crash_before_apply_reconciles_to_aborted(tmp_path, monkeypatch):
    txns, _ = make(tmp_path)
    path = claim_path(txns)
    create(txns, path)
    before = path.read_bytes()
    _crash_on(monkeypatch, "_replace_atomic")        # the claim replace crashes
    with pytest.raises(KeyboardInterrupt):
        txns.transact("heartbeat", path, "hb", lambda b: Plan(after={"agent": "claude-rco-2", "task_id": "t/1", "beat": 9}))
    monkeypatch.undo()
    outcomes = txns.reconcile()
    assert [o["outcome"] for o in outcomes] == ["aborted"] and path.read_bytes() == before


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
    assert wal_states(txns) == ["outboxed"] and len(list(txns.outbox_dir.glob("*.json"))) == 1


def test_crash_after_archive_before_delete_is_an_aborted_release_with_an_orphan(tmp_path, monkeypatch):
    txns, _ = make(tmp_path)
    path = claim_path(txns)
    create(txns, path)
    archive = txns.root / "work_queue" / "done" / "t_1-y.json"
    monkeypatch.setattr(Path, "unlink", lambda self, *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        txns.transact("release", path, "rel", lambda b: Plan(after=None, archive=(archive, {"r": 1}),
                                                             event={"type": "release"}))
    monkeypatch.undo()
    assert [o["outcome"] for o in txns.reconcile()] == ["aborted_orphan_archive"]
    assert path.exists() and archive.exists()                      # truthful: reported, never deleted


def test_a_claim_changed_after_a_crash_is_diverged_and_left_alone(tmp_path, monkeypatch):
    txns, _ = make(tmp_path)
    path = claim_path(txns)
    create(txns, path)
    _crash_on(monkeypatch, "_replace_atomic")
    with pytest.raises(KeyboardInterrupt):
        txns.transact("heartbeat", path, "hb", lambda b: Plan(after={"agent": "claude-rco-2", "task_id": "t/1", "beat": 9}))
    monkeypatch.undo()
    path.write_bytes(b'{"someone": "else"}\n')
    assert [o["outcome"] for o in txns.reconcile()] == ["diverged"]
    assert path.read_bytes() == b'{"someone": "else"}\n'


def test_a_corrupt_wal_record_is_reported_and_never_acted_on(tmp_path):
    txns, _ = make(tmp_path)
    txns.wal_dir.mkdir(parents=True)
    (txns.wal_dir / "bad.json").write_text("{not json", encoding="utf-8")
    assert txns.reconcile() == [{"wal": "bad.json", "outcome": "corrupt"}]


def test_publication_is_outside_the_locks_idempotent_and_never_lost(tmp_path):
    txns, recorder = make(tmp_path)
    create(txns, claim_path(txns))
    assert txns.publish_pending(None) == {"published": 0, "pending": 1, "failed": 0}
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
    assert txns.publish_pending(flaky) == {"published": 0, "pending": 0, "failed": 1}
    assert txns.publish_pending(flaky) == {"published": 1, "pending": 0, "failed": 0}
    assert txns.publish_pending(flaky) == {"published": 0, "pending": 0, "failed": 0}   # never twice
    assert published == ["claim:t/1"]
    assert all(e[1] != "mutex" for e in recorder.events[4:])       # publication took no lock


def test_the_same_idempotency_key_is_one_outbox_record(tmp_path):
    txns, _ = make(tmp_path)
    path = claim_path(txns)
    create(txns, path)
    txns.transact("claim", path, "claim:t/1", lambda b: Plan(after={"agent": "claude-rco-2", "task_id": "t/1", "v": 2},
                                                             event={"type": "claim"}))
    assert len(list(txns.outbox_dir.glob("*.json"))) == 1


def test_module_never_imports_waggledance_or_reads_the_environment():
    for name in ("bridge_v2_queue_transactions.py", "bridge_v2_work_queue.py", "bridge_v2_resource_scope.py"):
        tree = ast.parse((ROOT / "tools" / name).read_text(encoding="utf-8"))
        modules = {getattr(n, "module", None) or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        modules |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        assert not any(m.split(".")[0] == "waggledance" for m in modules), name
        assert not any(isinstance(n, ast.Attribute) and n.attr in ("environ", "getenv")
                       for n in ast.walk(tree)), name

@pytest.mark.parametrize("change", [{"claim_rel": "../../outside.json"}, {"archive_rel": "work_queue/claims/x.json"},
                                    {"op": "force_takeover"}, {"txid": "not-hex"}, {"event": ["not", "a", "dict"]},
                                    {"before_sha256": "zz"}])
def test_a_forged_wal_record_is_corrupt_and_never_outboxed(tmp_path, change):
    txns, _ = make(tmp_path)
    txns.wal_dir.mkdir(parents=True)
    record = {"schema": qt.TXN_SCHEMA, "txid": "a" * 32, "idempotency_key": "k", "op": "claim",
              "claim_rel": "work_queue/claims/task.json", "before_sha256": None, "after": {}, "after_sha256": None,
              "archive_rel": None, "archive": None, "event": {"type": "claim"}, "state": "applied",
              "created_utc": NOW.isoformat()}
    record.update(change)
    (txns.wal_dir / "forged.json").write_bytes(claim_bytes(record))
    assert txns.reconcile() == [{"wal": "forged.json", "outcome": "corrupt"}]
    assert not txns.outbox_dir.exists()
    record.update({"claim_rel": "work_queue/claims/task.json", "archive_rel": None, "op": "claim", "txid": "a" * 32,
                   "event": {"type": "claim"}, "before_sha256": None})
    (txns.wal_dir / "forged.json").write_bytes(claim_bytes(record))
    assert [o["outcome"] for o in txns.reconcile()] == ["outboxed"]   # success twin: a well-formed record
