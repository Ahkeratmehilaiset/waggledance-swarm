# SPDX-License-Identifier: BUSL-1.1
"""F8/F8a/F10/F22 work-queue fixtures (authored per operator directive; NOT executed yet).

Every root is a tmp_path with recording lock doubles; identities and clocks are explicit.
Refusals have same-fixture success twins; race schedules are deterministic.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import pytest

from tools import bridge_v2_work_queue as wq
from tools.bridge_v2_queue_transactions import QueueTransactions, Refused, claim_bytes
from tools.bridge_v2_resource_scope import ScopeError, explain_scope, resolve_scopes
from tools.bridge_v2_work_queue import OwnerIdentity, WorkQueueError

NOW = datetime(2026, 9, 29, 22, 0, tzinfo=timezone.utc)
OWNER = OwnerIdentity("session-a", hashlib.sha256(b"token-a").hexdigest())
OTHER = OwnerIdentity("session-b", hashlib.sha256(b"token-b").hexdigest())


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


def test_stale_sweep_archives_only_unowned_claims_and_refuses_owned_ones(env):
    old = NOW - timedelta(hours=13)
    claim(env, task="team/owned", now=old, scope=("tools/o.py",))
    claim(env, task="team/unowned", identity=None, now=old, scope=("tools/u.py",))
    planned = wq.archive_stale_claims(env[0], now=NOW, apply=False)
    outcomes = {r["task_id"]: r["outcome"] for r in planned}
    assert outcomes == {"team/owned": "owned_claim_needs_session_heartbeat_port", "team/unowned": "planned"}
    applied = {r["task_id"]: r for r in wq.archive_stale_claims(env[0], now=NOW, apply=True)}
    assert applied["team/unowned"]["applied"] is True and applied["team/unowned"]["archive"].endswith(".stale_lease.json")
    assert wq.find_claim(env[0], "team/unowned") is None and wq.find_claim(env[0], "team/owned") is not None


@pytest.mark.parametrize("agent,task", [("Bad", "t/1"), ("claude-rco-2", "../x"), ("claude-rco-2", "a//b")])
def test_invalid_names_refuse_before_any_lock(env, agent, task):
    with pytest.raises(WorkQueueError):
        claim(env, agent=agent, task=task)


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
