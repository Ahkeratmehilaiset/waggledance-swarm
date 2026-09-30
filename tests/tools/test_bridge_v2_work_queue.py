# SPDX-License-Identifier: BUSL-1.1
"""F8/F8a/F10/F22 work-queue fixtures (authored per operator directive; run 2026-09-30 by RCO1 on Windows).

Every root is a tmp_path with recording lock doubles; identities and clocks are explicit.
Refusals have same-fixture success twins; race schedules are deterministic.
"""
from __future__ import annotations

from contextlib import contextmanager
import dataclasses
from datetime import datetime, timedelta, timezone
import hashlib
import inspect
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading

import pytest

from tools import bridge_v2_work_queue as wq
from tools.bridge_v2_queue_transactions import FileClaimLock, LockTimeout, QueueTransactions, Refused, claim_bytes
from tools.bridge_v2_resource_scope import ScopeError, explain_scope, resolve_scopes
from tools.bridge_v2_work_queue import OwnerIdentity, WorkQueueError

NOW = datetime(2026, 9, 29, 22, 0, tzinfo=timezone.utc)
OWNER = OwnerIdentity("session-a", "token-a")      # the RAW tokens the sessions hold (S8)
OTHER = OwnerIdentity("session-b", "token-b")


class Lock:
    @contextmanager
    def hold(self, target, timeout_seconds):
        yield


@pytest.fixture
def env(tmp_path):
    worktree = tmp_path / "wt"
    (worktree / "tools").mkdir(parents=True)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    return QueueTransactions(runtime, mutex=Lock(), claim_lock=Lock(), clock=lambda: NOW), str(worktree)


def claim(env, task="team/task-1", agent="claude-rco-2", identity=OWNER, scope=("tools/a.py",), mode="write",
          now=NOW, **kw):
    txns, cwd = env
    return wq.claim_task(txns, agent=agent, task_id=task, summary="work", mode=mode, write_scope=scope,
                         identity=identity, cwd=cwd, now=now, **kw)


def claim_file(env, task="team/task-1"):
    return wq.find_claim(env[0], task)


def test_a_new_claim_uses_the_legacy_wire_format(env):
    record = claim(env)
    path = claim_file(env)
    assert path.name == "team_task-1-" + hashlib.sha256(b"team/task-1").hexdigest()[:12] + ".json"
    assert path.read_bytes() == claim_bytes(record)               # indent 2, sorted keys, newline
    assert record["owner_session_id"] == "session-a" and "owner_identity" not in record
    assert record["claim_lease_expires_utc"] == "2026-09-29T22:15:00Z"
    identityless = claim(env, task="team/task-2", identity=None, scope=("tools/b.py",))
    assert identityless["owner_identity"] == "none" and "owner_session_id" not in identityless


def test_only_the_owning_session_refreshes_and_other_agents_are_refused(env):
    claim(env)
    refreshed = claim(env, now=NOW + timedelta(minutes=1))       # success twin: same owner
    assert refreshed["last_heartbeat_utc"] == "2026-09-29T22:01:00Z"
    with pytest.raises(Refused, match="another session"):
        claim(env, identity=OTHER)
    with pytest.raises(Refused, match="another agent"):
        claim(env, agent="fable-5")


def test_release_archives_with_the_legacy_name_and_deletes_the_claim(env):
    claim(env)
    record = wq.release_task(env[0], agent="claude-rco-2", task_id="team/task-1", identity=OWNER, now=NOW)
    done = env[0].root / "work_queue" / "done" / ("team_task-1-" + hashlib.sha256(b"team/task-1").hexdigest()[:12]
                                                  + "-" + wq.safe_name("2026-09-29T22:00:00Z") + ".json")
    assert json.loads(done.read_text()) == record and claim_file(env) is None


def test_release_by_another_session_or_of_a_legacy_claim_is_refused(env):
    claim(env)
    with pytest.raises(Refused, match="another session"):
        wq.release_task(env[0], agent="claude-rco-2", task_id="team/task-1", identity=OTHER, now=NOW)
    legacy = env[0].root / "work_queue" / "claims" / "legacy.json"
    legacy.write_bytes(claim_bytes({"agent": "claude-rco-2", "task_id": "legacy", "last_heartbeat_utc": iso(NOW)}))
    with pytest.raises(Refused, match="pre-B7"):
        wq.release_task(env[0], agent="claude-rco-2", task_id="legacy", identity=OWNER, now=NOW)
    wq.release_task(env[0], agent="claude-rco-2", task_id="legacy", identity=OWNER, now=NOW,
                    allow_legacy_unowned_claim=True)                  # success twin
    assert wq.find_claim(env[0], "legacy") is None


def iso(value):
    return wq.iso(value)


def test_heartbeat_is_owner_only_and_keeps_every_other_field(env):
    claim(env)
    path = claim_file(env)
    raw = json.loads(path.read_text())
    raw["owner_pid"] = 4242                                         # PowerShell-only field survives
    path.write_bytes(claim_bytes(raw))
    beat = wq.heartbeat(env[0], agent="claude-rco-2", task_id="team/task-1", identity=OWNER,
                        now=NOW + timedelta(minutes=5))
    assert beat["owner_pid"] == 4242 and beat["claim_lease_expires_utc"] == "2026-09-29T22:20:00Z"
    with pytest.raises(Refused, match="owning session"):
        wq.heartbeat(env[0], agent="claude-rco-2", task_id="team/task-1", identity=OTHER, now=NOW)


def test_c2_release_first_then_heartbeat_never_resurrects_the_claim(env, monkeypatch):
    claim(env)
    path = claim_file(env)
    real_find = wq.find_claim
    # Deterministic schedule: the heartbeat looks the claim up, then the release wins the lock.
    monkeypatch.setattr(wq, "find_claim", lambda txns, task: path)
    wq.release_task(env[0], agent="claude-rco-2", task_id="team/task-1", identity=OWNER, now=NOW)
    with pytest.raises(Refused, match="released or replaced|never recreated"):
        wq.heartbeat(env[0], agent="claude-rco-2", task_id="team/task-1", identity=OWNER, now=NOW)
    monkeypatch.setattr(wq, "find_claim", real_find)
    assert not path.exists() and wq.find_claim(env[0], "team/task-1") is None


def test_c2_heartbeat_first_then_release_both_succeed(env):
    claim(env)
    wq.heartbeat(env[0], agent="claude-rco-2", task_id="team/task-1", identity=OWNER, now=NOW + timedelta(minutes=1))
    record = wq.release_task(env[0], agent="claude-rco-2", task_id="team/task-1", identity=OWNER,
                             now=NOW + timedelta(minutes=2))
    assert record["released_at_utc"] == "2026-09-29T22:02:00Z" and claim_file(env) is None


def test_write_scope_overlap_is_refused_and_disjoint_scopes_coexist(env):
    claim(env, scope=("tools",))
    with pytest.raises(Refused, match="write-scope conflict"):
        claim(env, task="team/task-2", agent="fable-5", identity=OTHER, scope=("tools/a.py",))
    claim(env, task="team/task-3", agent="fable-5", identity=OTHER, scope=("docs/x.md",))   # success twin


def _live_heartbeat(txns, identity, at):
    digest = hashlib.sha256(f"{identity.owner_session_id}\n{identity.owner_token_sha256}".encode()).hexdigest()
    path = txns.root / "work_queue" / "heartbeats" / f"{digest}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"owner_session_id": identity.owner_session_id,
                                "owner_token_sha256": identity.owner_token_sha256,
                                "last_beat_utc": iso(at)}), encoding="utf-8")


def test_the_facade_sweep_keeps_the_core_selection_rules(env):
    txns = env[0]
    old = NOW - timedelta(hours=13)
    claim(env, task="team/owned-dead", now=old, scope=("tools/o.py",))                     # lease expired, no beat
    claim(env, task="team/owned-live", now=old, scope=("tools/l.py",), identity=OTHER)     # lease expired, live beat
    claim(env, task="team/unowned", identity=None, now=old, scope=("tools/u.py",))
    claim(env, task="team/fresh", identity=None, scope=("tools/f.py",))
    (txns.root / "work_queue" / "claims" / "op-task.json").write_bytes(claim_bytes(
        {"agent": "operator", "task_id": "op-task", "last_heartbeat_utc": iso(old), "lease_seconds": 900}))
    _live_heartbeat(txns, OTHER, NOW - timedelta(seconds=30))
    planned = wq.archive_stale_claims(bridge_root=txns.root, now_utc=NOW)
    assert {a.claim.task_id for a in planned} == {"team/owned-dead", "team/unowned"}   # operator never swept
    for entry in planned:
        assert entry.applied is False and entry.age_seconds == 46800
        assert entry.release_reason == "last_heartbeat_utc was 46800s old; lease threshold 43200s"
        assert entry.archived_path.name.endswith(".20260929T220000Z.stale_lease.json")
    assert wq.find_claim(txns, "team/unowned") is not None                             # a dry run writes nothing


def test_the_facade_sweep_applies_only_through_injected_transactions(env, tmp_path):
    txns = env[0]
    claim(env, task="team/unowned", identity=None, now=NOW - timedelta(hours=13), scope=("tools/u.py",))
    with pytest.raises(WorkQueueError, match="no injected ports") as refused:
        wq.archive_stale_claims(bridge_root=txns.root, now_utc=NOW, apply=True)            # no default port
    assert not str(refused.value).startswith("sweep refused")      # the consumer adds that prefix (N2)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    with pytest.raises(WorkQueueError, match="another runtime root"):
        wq.archive_stale_claims(bridge_root=txns.root, now_utc=NOW, apply=True,
                                transactions=QueueTransactions(elsewhere, mutex=Lock(), claim_lock=Lock()))
    [entry] = wq.archive_stale_claims(bridge_root=txns.root, now_utc=NOW, apply=True, transactions=txns)
    payload = json.loads(entry.archived_path.read_text())
    assert entry.applied is True and payload["release_status"] == "stale_lease"
    assert payload["release_reason"] == entry.release_reason and wq.find_claim(txns, "team/unowned") is None


def test_the_facade_exports_the_core_names_with_the_core_shapes():
    assert isinstance(wq.AGENT_ID_PATTERN, re.Pattern) and wq.AGENT_ID_PATTERN.pattern == r"^[a-z][a-z0-9_-]{1,32}$"
    assert isinstance(wq.DEFAULT_BRIDGE_ROOT, Path) and wq.DEFAULT_BRIDGE_ROOT.name == ".agent-bridge"
    assert issubclass(wq.WorkQueueError, ValueError)
    assert [f.name for f in dataclasses.fields(wq.Claim)] == [
        "agent", "task_id", "summary", "mode", "write_scope", "run_id", "claimed_at_utc", "last_heartbeat_utc",
        "lease_seconds", "claim_lease_expires_utc", "role", "agent_uuid", "capabilities", "cwd", "owner_session_id",
        "owner_token_sha256", "owner_identity"]
    assert [f.name for f in dataclasses.fields(wq.ArchivedClaim)] == [
        "claim", "archived_path", "age_seconds", "release_reason", "applied"]
    assert wq.Claim.__dataclass_params__.frozen and wq.ArchivedClaim.__dataclass_params__.frozen
    sweep = inspect.signature(wq.archive_stale_claims).parameters
    assert list(sweep) == ["bridge_root", "now_utc", "max_age_seconds", "apply", "transactions"]
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in sweep.values())
    assert sweep["apply"].default is False and sweep["transactions"].default is None
    assert sweep["max_age_seconds"].default == 12 * 60 * 60
    assert list(inspect.signature(wq.list_claims).parameters) == ["bridge_root"]
    assert list(inspect.signature(wq.resolve_bridge_root).parameters) == ["bridge_root"]


def test_resolve_bridge_root_is_core_equal(monkeypatch, tmp_path):
    for name in wq.BRIDGE_ROOT_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    assert wq.resolve_bridge_root() == wq.DEFAULT_BRIDGE_ROOT
    monkeypatch.setenv("AGENT_BRIDGE_ROOT", str(tmp_path / "b"))
    assert wq.resolve_bridge_root() == tmp_path / "b"
    monkeypatch.setenv("AGENT_BRIDGE_RUNTIME_ROOT", str(tmp_path / "a"))
    assert wq.resolve_bridge_root() == tmp_path / "a"                      # the runtime root wins
    assert wq.resolve_bridge_root(tmp_path / "x") == tmp_path / "x"        # an explicit root wins
    monkeypatch.setenv("AGENT_BRIDGE_RUNTIME_ROOT", "   ")
    assert wq.resolve_bridge_root() == tmp_path / "b"                      # a blank value is ignored


def test_list_claims_returns_core_claim_objects(env):
    claim(env)
    [record] = wq.list_claims(bridge_root=env[0].root)
    assert isinstance(record, wq.Claim) and record.task_id == "team/task-1"
    assert record.write_scope == ("tools/a.py",) and record.lease_seconds == 900
    assert record.owner_session_id == "session-a" and record.claim_lease_expires_utc == "2026-09-29T22:15:00Z"

class RecordingLock:
    def __init__(self):
        self.entered = []

    @contextmanager
    def hold(self, target, timeout_seconds):
        self.entered.append(str(target))
        yield


@pytest.mark.parametrize("agent,task", [("Bad", "t/1"), ("claude-rco-2", "../x"), ("claude-rco-2", "a//b")])
def test_invalid_names_refuse_before_any_lock(tmp_path, agent, task):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (tmp_path / "wt" / "tools").mkdir(parents=True)
    lock = RecordingLock()
    txns = QueueTransactions(runtime, mutex=lock, claim_lock=lock, clock=lambda: NOW)
    with pytest.raises(WorkQueueError):
        claim((txns, str(tmp_path / "wt")), agent=agent, task=task)
    assert lock.entered == [] and not (runtime / "work_queue").exists()   # before any lock or write
    claim((txns, str(tmp_path / "wt")))                                    # success twin takes both locks
    assert len(lock.entered) == 2


def test_s8_the_readable_owner_hash_is_never_a_credential(env):
    claim(env)
    stored = json.loads(claim_file(env).read_text())["owner_token_sha256"]
    assert stored == hashlib.sha256(b"token-a").hexdigest()                 # core's derivation of the raw token
    forged = OwnerIdentity("session-a", stored)                              # the readable hash, as a "token"
    with pytest.raises(Refused, match="owning session"):
        wq.heartbeat(env[0], agent="claude-rco-2", task_id="team/task-1", identity=forged, now=NOW)
    with pytest.raises(Refused, match="another session"):
        wq.release_task(env[0], agent="claude-rco-2", task_id="team/task-1", identity=forged, now=NOW)
    with pytest.raises(Refused, match="another session"):
        claim(env, identity=forged)
    with pytest.raises(WorkQueueError, match="raw owner token"):
        wq.heartbeat(env[0], agent="claude-rco-2", task_id="team/task-1", identity=OwnerIdentity("session-a", ""),
                     now=NOW)
    wq.heartbeat(env[0], agent="claude-rco-2", task_id="team/task-1", identity=OWNER, now=NOW)   # success twin
    assert "token-a" not in repr(OWNER)


def test_s5_scope_dedupe_is_set_based_and_keeps_the_first_order():
    many = ",".join(f"tools/f{i}.py" for i in range(20000)) + ",tools/f0.py, tools/f1.py"
    result = wq._scope_entries(many)
    assert len(result) == 20000 and result[:2] == ("tools/f0.py", "tools/f1.py")
    assert wq._scope_entries(["a, b", "b", " a ", ""]) == ("a", "b")


def _raw_claim(env, name, **fields):
    txns, cwd = env
    directory = txns.root / "work_queue" / "claims"
    directory.mkdir(parents=True, exist_ok=True)
    record = {"agent": "fable-5", "task_id": name, "mode": "write", "cwd": cwd, "last_heartbeat_utc": iso(NOW)}
    record.update(fields)
    (directory / f"{name}.json").write_bytes(claim_bytes(record))
    return directory / f"{name}.json"


def test_s7_scopes_types_and_modes_are_normalized_like_core(env):
    stringy = _raw_claim(env, "string-scope", write_scope="tools/a.py")        # ONE entry, never its characters
    with pytest.raises(Refused, match="write-scope conflict with active claim string-scope"):
        claim(env, scope=("tools/a.py",))
    stringy.unlink()
    _raw_claim(env, "null-scope", write_scope=None)                            # empty, never a TypeError
    _raw_claim(env, "reader", mode="read-only", write_scope=["tools/a.py"])    # only WRITE claims conflict
    assert claim(env, scope=("tools/a.py",))["write_scope"] == ["tools/a.py"]  # success twin
    assert claim(env, task="team/task-8", scope="tools/y.py")["write_scope"] == ["tools/y.py"]   # caller string
    with pytest.raises(WorkQueueError, match="list of strings"):
        claim(env, task="team/task-7", scope=[b"tools/x.py"])
    _raw_claim(env, "odd-scope", write_scope=[{"path": "tools/b.py"}])
    with pytest.raises(Refused, match="unresolvable write scope"):
        claim(env, task="team/task-9", scope=("tools/z.py",))


def test_s9_oversize_is_refused_before_mutation_and_an_unreadable_claim_blocks_write_claims(env):
    txns, cwd = env
    with pytest.raises(WorkQueueError, match="16384"):
        wq.claim_task(txns, agent="claude-rco-2", task_id="team/big", summary="x" * 20000, mode="write",
                      write_scope=("tools/big.py",), identity=OWNER, cwd=cwd, now=NOW)
    assert not (txns.root / "work_queue").exists()
    huge = txns.root / "work_queue" / "claims" / "huge.json"
    huge.parent.mkdir(parents=True)
    huge.write_bytes(b'{"pad": "' + b"x" * (300 * 1024) + b'"}')
    with pytest.raises(Refused, match="unreadable or over the size bound"):
        claim(env, scope=("tools/a.py",))                                      # never skipped: overlap unknown
    assert claim(env, task="team/reader", mode="read-only", scope=())["mode"] == "read-only"   # twin
    huge.unlink()
    assert claim(env, scope=("tools/a.py",))["mode"] == "write"                 # success twin


def test_n3_ports_off_creates_nothing(tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (tmp_path / "wt" / "tools").mkdir(parents=True)
    with pytest.raises(WorkQueueError, match="ports are off"):
        claim((QueueTransactions(runtime), str(tmp_path / "wt")))
    assert not (runtime / "work_queue").exists()


class TimeoutLock:
    @contextmanager
    def hold(self, target, timeout_seconds):
        raise LockTimeout("claim lock busy: " + Path(target).name)
        yield   # pragma: no cover


def test_s6_the_facade_maps_every_other_transaction_refusal_to_work_queue_error(env):
    txns = env[0]
    claim(env, task="team/unowned", identity=None, now=NOW - timedelta(hours=13), scope=("tools/u.py",))
    busy = QueueTransactions(txns.root, mutex=RecordingLock(), claim_lock=TimeoutLock())
    with pytest.raises(WorkQueueError, match="busy") as timed_out:
        wq.archive_stale_claims(bridge_root=txns.root, now_utc=NOW, apply=True, transactions=busy)
    assert str(timed_out.value).startswith("team/unowned: ")
    archive = txns.root / "work_queue" / "done" / f"{wq.safe_name('team/unowned')}.20260929T220000Z.stale_lease.json"
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_bytes(b'{"someone": "else"}\n')
    with pytest.raises(WorkQueueError, match="already exists"):
        wq.archive_stale_claims(bridge_root=txns.root, now_utc=NOW, apply=True, transactions=txns)
    assert wq.find_claim(txns, "team/unowned") is not None                   # nothing archived
    archive.unlink()
    [entry] = wq.archive_stale_claims(bridge_root=txns.root, now_utc=NOW, apply=True, transactions=txns)
    assert entry.applied is True and wq.find_claim(txns, "team/unowned") is None   # success twin


class BeatingTransactions(QueueTransactions):
    """The owner's session beats AFTER the sweep listed the claim and before its apply plan runs."""

    beat_text = None

    def transact(self, op, claim_path, idempotency_key, plan_fn, **fenced):
        digest = hashlib.sha256(f"{OWNER.owner_session_id}\n{OWNER.owner_token_sha256}".encode()).hexdigest()
        beat = self.root / "work_queue" / "heartbeats" / f"{digest}.json"
        beat.parent.mkdir(parents=True, exist_ok=True)
        beat.write_text(self.beat_text, encoding="utf-8")
        return super().transact(op, claim_path, idempotency_key, plan_fn, **fenced)


@pytest.mark.parametrize("beat_text", [
    json.dumps({"owner_session_id": "session-a", "owner_token_sha256": hashlib.sha256(b"token-a").hexdigest(),
                "last_beat_utc": "2026-09-29T21:59:30Z"}),                  # live at apply time
    "{not json",                                                           # unknown at apply time
], ids=["live", "unknown"])
def test_q_sweep_the_owner_session_is_rechecked_at_apply_under_the_locks(env, beat_text):
    txns = env[0]
    claim(env, task="team/owned", now=NOW - timedelta(hours=13), scope=("tools/o.py",))   # lease long expired
    beating = BeatingTransactions(txns.root, mutex=Lock(), claim_lock=Lock(), clock=lambda: NOW)
    beating.beat_text = beat_text
    assert wq.archive_stale_claims(bridge_root=txns.root, now_utc=NOW, apply=True, transactions=beating) == []
    assert wq.find_claim(txns, "team/owned") is not None                   # skipped, never deleted


def test_q_sweep_a_session_still_dead_at_apply_is_archived(env):
    txns = env[0]
    claim(env, task="team/owned", now=NOW - timedelta(hours=13), scope=("tools/o.py",))
    [entry] = wq.archive_stale_claims(bridge_root=txns.root, now_utc=NOW, apply=True, transactions=txns)
    assert entry.applied is True and wq.find_claim(txns, "team/owned") is None   # success twin


def test_n7_a_deep_or_oversized_session_heartbeat_is_unknown_never_a_crash(env):
    txns = env[0]
    claim(env, task="team/owned", now=NOW - timedelta(hours=13), scope=("tools/o.py",))
    digest = hashlib.sha256(f"{OWNER.owner_session_id}\n{OWNER.owner_token_sha256}".encode()).hexdigest()
    beat = txns.root / "work_queue" / "heartbeats" / f"{digest}.json"
    beat.parent.mkdir(parents=True, exist_ok=True)
    beat.write_text("[" * 100000 + "]" * 100000, encoding="utf-8")
    assert wq.archive_stale_claims(bridge_root=txns.root, now_utc=NOW) == []   # unknown is never swept
    beat.write_text('{"x": "' + "y" * (300 * 1024) + '"}', encoding="utf-8")
    assert wq.archive_stale_claims(bridge_root=txns.root, now_utc=NOW) == []
    beat.unlink()
    assert [a.claim.task_id for a in wq.archive_stale_claims(bridge_root=txns.root, now_utc=NOW)] == ["team/owned"]


def test_resource_scopes_explain_and_refuse_ambiguity(env):
    txns, cwd = env
    ok = explain_scope("tools/bridge_v2_work_queue.py", worktree=cwd, bridge_root=str(txns.root))
    assert ok["accepted"] and ok["kind"] == "repo" and ok["examples"]
    for entry, fragment in (("tools/../x", "traversal"), ("tools/café.py", "ASCII"), ("tools/a.", "alias"),
                            ("worktree:tools/x", ".codex-audit"), ("bogus:x", "unknown resource kind"),
                            ("tools/*.py", "whole repository")):
        refused = explain_scope(entry, worktree=cwd, bridge_root=str(txns.root))
        assert refused["accepted"] is False and fragment in refused["reason"], entry
    assert [s.path for s in resolve_scopes(["tools/a.py, tools/B.py"], worktree=cwd,
                                           bridge_root=str(txns.root))] == ["tools/a.py", "tools/b.py"]
    with pytest.raises(ScopeError):
        resolve_scopes(["/outside/the/roots"], worktree=cwd, bridge_root=str(txns.root))


# -- F8 session-heartbeat fence (RCO1 2026-09-30, Lead request d79f933d) -----------------------------------
# The PowerShell session-heartbeat writer (Write-BridgeSessionHeartbeat, ClaimLeaseHeartbeat.ps1) takes only the
# sibling lock of the BEAT file (Enter-BridgeClaimLock -ClaimPath <beat>: "<beat>.json.lock", FileShare.None),
# never the claim lock or the runtime mutex. So the sweep holds that same sibling lock from its apply-time re-read
# of the beat through the delete: no beat can land in between. Each test below failed at 7779e9a2 (no fence).

REPO = Path(__file__).resolve().parents[2]


def _beat_path(txns, identity=OWNER):
    digest = hashlib.sha256(f"{identity.owner_session_id}\n{identity.owner_token_sha256}".encode()).hexdigest()
    return txns.root / "work_queue" / "heartbeats" / f"{digest}.json"


def _beat_json(at, identity=OWNER):
    return json.dumps({"owner_session_id": identity.owner_session_id, "owner_token_sha256": identity.owner_token_sha256,
                       "last_beat_utc": iso(at), "ttl_seconds": 180})


def _writer_beat(path, at, timeout_seconds=0.2):
    """The PowerShell writer's protocol in Python: the beat's sibling lock, then the beat. False: the lock was
    busy and the beat was skipped, as Write-BridgeSessionHeartbeat returns $false."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with FileClaimLock().hold(Path(str(path) + ".lock"), timeout_seconds):
            path.write_text(_beat_json(at), encoding="utf-8")
    except LockTimeout:
        return False
    return True


@pytest.fixture
def fenced(tmp_path):
    """Real sibling file locks (the PowerShell-compatible claim-lock port); the mutex stays a no-op double."""
    worktree = tmp_path / "wt"
    (worktree / "tools").mkdir(parents=True)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    return QueueTransactions(runtime, mutex=Lock(), claim_lock=FileClaimLock(), clock=lambda: NOW,
                             lock_timeout_seconds=0.5), str(worktree)


class LateBeatTransactions(QueueTransactions):
    """Cut point: the owner's writer tries to beat AFTER the sweep plan's apply-time re-read and BEFORE its
    delete (``_record`` runs between the two, under the locks)."""

    attempts = None

    def _record(self, op, claim_rel, key, before, plan):
        if op == "stale_archive":
            self.attempts.append(_writer_beat(_beat_path(self), NOW))
        return super()._record(op, claim_rel, key, before, plan)


def test_f8_fence_no_session_beat_lands_between_the_apply_time_recheck_and_the_delete(fenced):
    txns = fenced[0]
    claim(fenced, task="team/owned", now=NOW - timedelta(hours=13), scope=("tools/o.py",))   # lease long expired
    late = LateBeatTransactions(txns.root, mutex=Lock(), claim_lock=FileClaimLock(), clock=lambda: NOW,
                                lock_timeout_seconds=0.5)
    late.attempts = []
    [entry] = wq.archive_stale_claims(bridge_root=txns.root, now_utc=NOW, apply=True, transactions=late)
    assert entry.applied is True and wq.find_claim(txns, "team/owned") is None
    # The writer found the beat's lock held, so the decision and the delete were one fenced step: no beat that
    # the sweep did not see exists beside the archived claim (at 7779 the beat landed and the claim still went).
    assert late.attempts == [False] and not _beat_path(txns).exists()
    assert _writer_beat(_beat_path(txns), NOW) is True                          # success twin: fence released


def test_f8_fence_a_beat_in_progress_when_the_sweep_arrives_is_seen_and_keeps_the_claim(fenced):
    txns = fenced[0]
    claim(fenced, task="team/owned", now=NOW - timedelta(hours=13), scope=("tools/o.py",))
    beat = _beat_path(txns)
    beat.parent.mkdir(parents=True, exist_ok=True)
    holding, go = threading.Event(), threading.Event()

    def writer():
        with FileClaimLock().hold(Path(str(beat) + ".lock"), 5):
            holding.set()
            go.wait(5)
            beat.write_text(_beat_json(NOW - timedelta(seconds=5)), encoding="utf-8")   # live at apply time

    thread = threading.Thread(target=writer)
    thread.start()
    release = threading.Timer(0.3, go.set)
    try:
        assert holding.wait(5)
        release.start()
        patient = QueueTransactions(txns.root, mutex=Lock(), claim_lock=FileClaimLock(), clock=lambda: NOW,
                                    lock_timeout_seconds=5)
        # Listed while the beat was absent (dead); the apply waits for the writer and re-reads a live beat.
        assert wq.archive_stale_claims(bridge_root=txns.root, now_utc=NOW, apply=True, transactions=patient) == []
    finally:
        go.set()
        release.cancel()
        thread.join(5)
    assert wq.find_claim(txns, "team/owned") is not None


def test_f8_fence_a_writer_holding_the_beat_lock_past_the_timeout_fails_the_sweep_closed(fenced):
    txns = fenced[0]
    claim(fenced, task="team/owned", now=NOW - timedelta(hours=13), scope=("tools/o.py",))
    beat = _beat_path(txns)
    beat.parent.mkdir(parents=True, exist_ok=True)
    with FileClaimLock().hold(Path(str(beat) + ".lock"), 1):
        with pytest.raises(WorkQueueError, match="busy"):
            wq.archive_stale_claims(bridge_root=txns.root, now_utc=NOW, apply=True, transactions=txns)
    assert wq.find_claim(txns, "team/owned") is not None                       # unknown never deletes
    [entry] = wq.archive_stale_claims(bridge_root=txns.root, now_utc=NOW, apply=True, transactions=txns)
    assert entry.applied is True                                               # success twin once released


class OrderedPorts:
    """One event list for a mutex double and the REAL sibling locks, to prove the one lock order."""

    def __init__(self):
        self.events, self.files = [], FileClaimLock()
        ports = self

        class Mutex:
            @contextmanager
            def hold(self, name, timeout_seconds):
                ports.events.append("mutex")
                try:
                    yield
                finally:
                    ports.events.append("/mutex")

        class Files:
            @contextmanager
            def hold(self, lock_path, timeout_seconds):
                kind = Path(lock_path).parent.name
                with ports.files.hold(lock_path, timeout_seconds):
                    ports.events.append(kind)
                    try:
                        yield
                    finally:
                        ports.events.append("/" + kind)

        self.mutex, self.claim_lock = Mutex(), Files()


def test_f8_fence_order_is_mutex_then_claim_lock_then_beat_lock_and_only_owned_claims_take_it(fenced):
    txns = fenced[0]
    claim(fenced, task="team/owned", now=NOW - timedelta(hours=13), scope=("tools/o.py",))
    claim(fenced, task="team/unowned", identity=None, now=NOW - timedelta(hours=13), scope=("tools/u.py",))
    ports = OrderedPorts()
    ordered = QueueTransactions(txns.root, mutex=ports.mutex, claim_lock=ports.claim_lock, clock=lambda: NOW,
                                lock_timeout_seconds=0.5)
    archived = wq.archive_stale_claims(bridge_root=txns.root, now_utc=NOW, apply=True, transactions=ordered)
    assert sorted(a.claim.task_id for a in archived) == ["team/owned", "team/unowned"]
    chunks, current = [], []
    for event in ports.events:
        current.append(event)
        if event == "/mutex":
            chunks.append(current)
            current = []
    assert current == [] and sorted(chunks) == sorted([
        ["mutex", "claims", "heartbeats", "/heartbeats", "/claims", "/mutex"],   # owned: fenced, innermost
        ["mutex", "claims", "/claims", "/mutex"]])                               # identity-less: no beat, no fence


def test_f8_fence_an_absent_heartbeats_directory_is_created_so_the_fence_is_real(fenced):
    txns = fenced[0]
    claim(fenced, task="team/owned", now=NOW - timedelta(hours=13), scope=("tools/o.py",))
    heartbeats = txns.root / "work_queue" / "heartbeats"
    assert not heartbeats.exists()
    [entry] = wq.archive_stale_claims(bridge_root=txns.root, now_utc=NOW, apply=True, transactions=txns)
    # A writer that creates the directory later still meets the lock file the sweep held; no beat was invented.
    assert entry.applied is True and heartbeats.is_dir() and not _beat_path(txns).exists()


@pytest.mark.skipif(os.name != "nt", reason="the PowerShell writer and FileShare.None are Windows-only")
@pytest.mark.parametrize("shell", ["powershell.exe", "pwsh.exe"], ids=["ps51", "pwsh7"])
def test_f8_fence_the_real_powershell_session_writer_skips_its_beat_while_the_fence_is_held(fenced, shell):
    executable = shutil.which(shell)
    if executable is None:
        pytest.skip(shell + " is not on PATH")
    txns = fenced[0]
    beat = _beat_path(txns)
    beat.parent.mkdir(parents=True, exist_ok=True)
    script = REPO / ".agent-bridge" / "bin" / "ClaimLeaseHeartbeat.ps1"
    command = ("$ErrorActionPreference = 'Stop'; . '" + str(script) + "'; $script:BridgeClaimLockTimeoutMs = 300; "
               "$id = [pscustomobject]@{ owner_session_id = '" + OWNER.owner_session_id + "'; owner_token_sha256 = '"
               + OWNER.owner_token_sha256 + "' }; 'RESULT=' + (Write-BridgeSessionHeartbeat -Root '" + str(txns.root)
               + "' -AgentName 'claude-rco-2' -Identity $id -TtlSeconds 180)")

    def run():
        return subprocess.run([executable, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                               "-Command", command], capture_output=True, text=True, timeout=120).stdout

    with FileClaimLock().hold(Path(str(beat) + ".lock"), 1):                   # the sweep's fence, held
        assert "RESULT=False" in run()
        assert not beat.exists()
    assert "RESULT=True" in run()                                              # success twin: fence released
    written = json.loads(beat.read_text(encoding="utf-8"))
    assert (written["owner_session_id"], written["owner_token_sha256"]) == (OWNER.owner_session_id,
                                                                           OWNER.owner_token_sha256)
