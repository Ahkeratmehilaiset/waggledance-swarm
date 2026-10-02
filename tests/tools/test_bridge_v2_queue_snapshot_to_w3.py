# SPDX-License-Identifier: BUSL-1.1
"""The queue claims snapshot feeds W3 end to end (fable-5 on the #1756 composition; claude-rco-2 integration review,
2026-10-01 00:59:20Z, cases E1 to E5).

``queue_claims_snapshot`` reads a fixture root under a mutex port, and ``wd_routing_load.load_blocks`` turns that
one snapshot into the router's load evidence. Both modules are in one tree since this composition, so the contract
between them is pinned by running them together, not by a copied key list. The real Windows mutex is used where it
exists, a recording port everywhere. Fixture roots only; the lane environment is scrubbed. Nothing here wires W3:
its idle dispatch stays a separate, gated decision (tools/bridge_v2_queue_snapshot.py states the residuals).
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path

import pytest

from tools import bridge_v2_queue_snapshot as snap
from tools import bridge_v2_queue_transactions as qt
from tools import bridge_v2_work_queue as wq
from tools import wd_routing_load as w3
from tools import wd_task_router as router
from tools.bridge_v2_queue_transactions import QueueTransactions
from tools.bridge_v2_work_queue import OwnerIdentity
from waggledance.core.work_queue import claim_task as core_claim_task

NOW = datetime(2026, 10, 1, 1, 0, tzinfo=timezone.utc)
WORKERS = [*router.MEMBERS, router.GROK]
OWNER = OwnerIdentity("session-a", "token-a")
POLICY = {"schema": router.POLICY_SCHEMA, "max_evidence_age_seconds": 900, "budget_mode": "steady",
          "class_roles": {name: ["impl"] for name in router.TASK_CLASSES},
          "class_profiles": {name: ["p-impl"] for name in router.TASK_CLASSES}}


def _text(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


class RecordingMutex:
    def __init__(self) -> None:
        self.names = []

    @contextmanager
    def hold(self, name, timeout_seconds):
        self.names.append(name)
        yield


class Lock:
    @contextmanager
    def hold(self, target, timeout_seconds):
        yield


def _windows_port():
    from tools.bridge_v2_queue_ports_windows import NamedMutexPort
    return NamedMutexPort()


@pytest.fixture(params=["recording", "windows"])
def port(request):
    if request.param == "windows" and os.name != "nt":
        pytest.skip("the runtime-root mutex is a Windows named mutex")
    return RecordingMutex() if request.param == "recording" else _windows_port()


@pytest.fixture
def root(tmp_path, monkeypatch):
    for name in [k for k in os.environ if k.upper().startswith(("AGENT_BRIDGE", "WD_"))]:
        monkeypatch.delenv(name)
    (tmp_path / "wt" / "tools").mkdir(parents=True)
    runtime = tmp_path / "runtime"
    (runtime / "work_queue" / "claims").mkdir(parents=True)
    return runtime


def _claim(root: Path, task: str, agent: str, scope: str, identity=OWNER) -> None:
    """A v2 claim written by the v2 writer (tools/bridge_v2_work_queue.py)."""
    txns = QueueTransactions(root, mutex=Lock(), claim_lock=Lock(), clock=lambda: NOW)
    wq.claim_task(txns, agent=agent, task_id=task, summary="work", mode="write", write_scope=(scope,),
                  identity=identity, cwd=str(root.parent / "wt"), now=NOW)


def _pending(root: Path, monkeypatch, task: str, agent: str, scope: str) -> None:
    """A transaction left prepared: the WAL record is written, the claim file is not (a crash in between)."""
    real, calls = qt._replace_atomic, {"n": 0}

    def crashing(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise KeyboardInterrupt("crash after the WAL record, before the claim write")
        return real(*args, **kwargs)
    monkeypatch.setattr(qt, "_replace_atomic", crashing)
    with pytest.raises(KeyboardInterrupt):
        _claim(root, task, agent, scope)
    monkeypatch.setattr(qt, "_replace_atomic", real)      # not undo(): that would also restore the scrubbed env


def _loads(root: Path, port, now: datetime = NOW) -> tuple[dict, dict]:
    snapshot = snap.queue_claims_snapshot(root, mutex=port, clock=lambda: NOW)
    return snapshot, w3.load_blocks(WORKERS, snapshot, _text(now), POLICY)


def _states(blocks: dict) -> dict:
    return {lane: (block["state"], block["claims"]) for lane, block in blocks.items()}


def _idle_but(**busy: int) -> dict:
    return {lane: ("busy", busy[lane]) if lane in busy else ("idle", 0) for lane in router.MEMBERS}


def test_the_producer_and_w3_name_one_schema():
    assert snap.SNAPSHOT_SCHEMA == w3.SNAPSHOT_SCHEMA


def test_e3_an_empty_known_queue_makes_every_member_idle(root, port):
    snapshot, blocks = _loads(root, port)
    assert set(snapshot) == w3.SNAPSHOT_KEYS
    assert _states(blocks) == _idle_but()
    assert router.GROK not in blocks                                   # grok's single_flight is not queue state
    assert all(block == {"schema": w3.LOAD_SCHEMA, "worker": lane, "observed_utc": snapshot["observed_utc"],
                         "state": "idle", "claims": 0} for lane, block in blocks.items())


def test_e1_one_owned_claim_makes_that_lane_busy_and_the_others_idle(root, port):
    _claim(root, "team/e1", "codex-tools-1", "tools/a.py")
    snapshot, blocks = _loads(root, port)
    assert [set(entry) for entry in snapshot["claims"]] == [w3.ENTRY_KEYS]
    assert _states(blocks) == _idle_but(**{"codex-tools-1": 1})


def test_a_pending_transaction_counts_as_busy_like_a_claim(root, port, monkeypatch):
    _claim(root, "team/a", "codex-tools-1", "tools/a.py")
    _pending(root, monkeypatch, "team/p", "fable-5", "tools/p.py")
    snapshot, blocks = _loads(root, port)
    assert [entry["agent"] for entry in snapshot["pending"]] == ["fable-5"]
    assert _states(blocks) == _idle_but(**{"codex-tools-1": 1, "fable-5": 1})


def test_e4_an_identityless_claim_by_the_core_writer_makes_its_lane_busy(root, port):
    core_claim_task(agent="claude-rco-1", task_id="team/core", summary="core", mode="write",
                    write_scope=("tools/c.py",), bridge_root=root, now_utc=NOW)
    snapshot, blocks = _loads(root, port)
    assert [(entry["agent"], entry["owner_session_id"]) for entry in snapshot["claims"]] == [("claude-rco-1", None)]
    assert _states(blocks) == _idle_but(**{"claude-rco-1": 1})


def test_e5_a_non_member_claim_leaves_every_member_idle(root, port):
    _claim(root, "team/scout", "grok-scout-1", "tools/s.py")
    snapshot, blocks = _loads(root, port)
    assert [entry["agent"] for entry in snapshot["claims"]] == ["grok-scout-1"]
    assert _states(blocks) == _idle_but()


@pytest.mark.parametrize("seconds,known", [(900, True), (901, False), (-1, False)], ids=["edge", "stale", "future"])
def test_e2_evidence_older_than_the_policy_age_or_from_the_future_is_unknown(root, port, seconds, known):
    _claim(root, "team/e2", "fable-5", "tools/e.py")
    _, blocks = _loads(root, port, now=NOW + timedelta(seconds=seconds))
    assert _states(blocks) == (_idle_but(**{"fable-5": 1}) if known else {})


def test_an_unreadable_claim_makes_every_lane_unknown_never_idle(root, port):
    _claim(root, "team/a", "codex-tools-1", "tools/a.py")
    (root / "work_queue" / "claims" / "broken.json").write_bytes(b"{not json")
    snapshot, blocks = _loads(root, port)
    assert snapshot["unreadable"] == 1 and blocks == {}
