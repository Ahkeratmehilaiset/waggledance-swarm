# SPDX-License-Identifier: BUSL-1.1
"""S2 (Lead 2026-09-30): the legacy PowerShell claim writers take the v2 queue's runtime-root mutex.

The v2 queue and its read-only claims snapshot hold ``mutex_name(root)`` (``Global\\WaggleDanceBridgeV2Queue-`` plus 32
hex) while they list and change claims. A legacy writer that never takes it can create, refresh, release, bump or sweep
a claim in the middle of that listing, so a snapshot can report complete and IDLE while a legacy claim lands. Every
legacy claim/done mutation now runs inside that same kernel object: the root mutex first, then the per-claim lock, as
the Python queue orders them. A busy or abandoned root refuses the mutation, with nothing read or changed; a refused
mutation still releases the root it took; each blocked case has a success twin once the root is free.

The bin runs from a copy whose other Global mutex names are made Local and whose event writer is a stub, so no fleet
mutex or event log is touched. The root mutex keeps its real name: it is derived from the temporary root.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tools.bridge_v2_queue_transactions import mutex_name

REPO = Path(__file__).resolve().parents[2]
SHELLS = [shell for shell in ("powershell.exe", "pwsh.exe") if os.name == "nt" and shutil.which(shell)]
pytestmark = pytest.mark.skipif(not SHELLS, reason="the legacy claim writers are Windows PowerShell")
AGENT = "claude-rco-2"
SESSION = "legacy-mutex-session"
TOKEN = "f" * 64
ROOT_PREFIX = "Global\\WaggleDanceBridgeV2Queue-"


@pytest.fixture
def bridge(tmp_path):
    code = tmp_path / "fixture" / ".agent-bridge" / "bin"
    shutil.copytree(REPO / ".agent-bridge" / "bin", code)
    (tmp_path / "fixture" / "configs").mkdir()
    shutil.copy2(REPO / "configs" / "bridge_identity_registry.json", tmp_path / "fixture" / "configs")
    local = "Local\\WdLegacyMutexFixture-" + os.urandom(8).hex() + "-"
    for script in code.glob("*.ps1"):
        source = script.read_text(encoding="utf-8-sig")
        if "Global\\WaggleDanceBridge" in source:
            kept = source.replace(ROOT_PREFIX, "\x00ROOT\x00").replace("Global\\WaggleDanceBridge", local)
            script.write_text(kept.replace("\x00ROOT\x00", ROOT_PREFIX), encoding="utf-8-sig")
    (code / "Write-AgentEvent.ps1").write_text("$null = $args\n'fixture: no event written'\n", encoding="utf-8")
    worktree = tmp_path / "wt"
    for child in ("objects", "refs"):                 # RS7: a claim cwd must be a git top level
        (worktree / ".git" / child).mkdir(parents=True)
    (worktree / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    runtime = tmp_path / "runtime"
    (runtime / "work_queue" / "claims").mkdir(parents=True)
    return code, worktree, runtime


def _run(shell, bridge, arguments, root_text=None):
    code, worktree, runtime = bridge
    env = {key: value for key, value in os.environ.items() if not key.startswith(("AGENT_BRIDGE_", "WD_"))}
    env.update(AGENT_BRIDGE_RUNTIME_ROOT=str(runtime) if root_text is None else root_text,
               AGENT_BRIDGE_OWNER_SESSION_ID=SESSION, AGENT_BRIDGE_OWNER_TOKEN=TOKEN)
    return subprocess.run([shutil.which(shell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                           *arguments], cwd=worktree, env=env, capture_output=True, text=True, timeout=180)


def _claim(shell, bridge, task="team/one", scope="tools/one.py", force=False, root_text=None):
    arguments = ["-File", str(bridge[0] / "Claim-AgentTask.ps1"), "-Agent", AGENT, "-TaskId", task,
                 "-Summary", "legacy claim " + task, "-Mode", "write", "-WriteScope", scope, "-LeaseSeconds", "600"]
    return _run(shell, bridge, arguments + (["-Force"] if force else []), root_text)


def _release(shell, bridge, task="team/one"):
    return _run(shell, bridge, ["-File", str(bridge[0] / "Release-AgentTask.ps1"), "-Agent", AGENT, "-TaskId", task,
                                "-Status", "done", "-Message", "released"])


def _bump(shell, bridge):
    command = (f". '{bridge[0] / 'ClaimLeaseHeartbeat.ps1'}'; "
               f"'BUMPED:' + (Update-BridgeClaimLease -Root '{bridge[2]}' -AgentName '{AGENT}')")
    return _run(shell, bridge, ["-Command", command])


def _sweep(shell, bridge):
    return _run(shell, bridge, ["-File", str(bridge[0] / "Invoke-StaleClaimSweep.ps1"), "-Quiet"])


def _claims(bridge):
    return sorted(path.name for path in (bridge[2] / "work_queue" / "claims").glob("*.json"))


def _done(bridge):
    done = bridge[2] / "work_queue" / "done"
    return sorted(path.name for path in done.glob("*.json")) if done.is_dir() else []


def _stale_claim(bridge, task="team/stale"):
    old = datetime.now(timezone.utc) - timedelta(hours=2)
    claim = {"claimed_at_utc": old.isoformat(), "last_heartbeat_utc": old.isoformat(), "agent": AGENT,
             "task_id": task, "summary": "stale", "mode": "write", "write_scope": ["tools/stale.py"], "resources": [],
             "run_id": "", "lease_seconds": 1, "claim_lease_expires_utc": (old + timedelta(seconds=1)).isoformat(),
             "pid": 1, "cwd": str(bridge[1]), "git_branch": "", "owner_identity": "none"}
    path = bridge[2] / "work_queue" / "claims" / "team_stale.json"
    path.write_text(json.dumps(claim), encoding="utf-8")
    return path


def _held(runtime):
    from tools.bridge_v2_queue_ports_windows import NamedMutexPort
    return NamedMutexPort().hold(mutex_name(runtime), 1)


def _busy(result):
    return result.returncode != 0 and "queue mutex busy" in result.stdout + result.stderr


@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: s.split(".")[0])
def test_a_legacy_claim_waits_for_the_v2_root_mutex_and_lands_once_it_is_free(bridge, shell):
    with _held(bridge[2]):
        blocked = _claim(shell, bridge)
    assert _busy(blocked), blocked.stdout + blocked.stderr
    assert _claims(bridge) == []                                    # nothing created while a v2 holder listed
    landed = _claim(shell, bridge)                                  # success twin
    assert landed.returncode == 0, landed.stdout + landed.stderr
    [name] = _claims(bridge)
    stored = json.loads((bridge[2] / "work_queue" / "claims" / name).read_text(encoding="utf-8-sig"))
    assert (stored["task_id"], stored["owner_session_id"]) == ("team/one", SESSION)


@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: s.split(".")[0])
def test_refresh_release_lease_bump_and_sweep_wait_for_the_root_mutex_then_apply(bridge, shell):
    assert _claim(shell, bridge).returncode == 0
    [name] = _claims(bridge)
    path = bridge[2] / "work_queue" / "claims" / name
    before = path.read_bytes()
    stale = _stale_claim(bridge)
    with _held(bridge[2]):
        refreshed = _claim(shell, bridge, force=True)
        released = _release(shell, bridge)
        bumped = _bump(shell, bridge)
        swept = _sweep(shell, bridge)
    assert _busy(refreshed) and _busy(released), refreshed.stderr + released.stderr
    assert "BUMPED:0" in bumped.stdout and "queue mutex busy" in bumped.stdout + bumped.stderr, bumped.stdout
    assert swept.returncode != 0 and "queue mutex busy" in swept.stderr, swept.stdout + swept.stderr
    assert path.read_bytes() == before and stale.exists() and _done(bridge) == []   # nothing changed while held
    assert "BUMPED:1" in _bump(shell, bridge).stdout                                # success twins, root free
    assert path.read_bytes() != before
    assert _sweep(shell, bridge).returncode == 0 and not stale.exists()
    assert _release(shell, bridge).returncode == 0 and _claims(bridge) == []
    assert len(_done(bridge)) == 2


@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: s.split(".")[0])
def test_every_alias_of_the_root_takes_the_one_python_mutex(bridge, shell):
    runtime = bridge[2]
    for alias in (str(runtime).upper() + "\\", str(runtime).replace("\\", "/") + "/./"):
        with _held(runtime):
            blocked = _claim(shell, bridge, root_text=alias)
        assert _busy(blocked), (alias, blocked.stdout + blocked.stderr)
    assert _claims(bridge) == []


@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: s.split(".")[0])
def test_a_refused_legacy_claim_releases_the_root_mutex_it_took(bridge, shell):
    from tools import bridge_v2_queue_ports_windows as ports

    kernel32 = ports._kernel32()
    witness = ports._create(mutex_name(bridge[2]), kernel32)        # keeps the object alive: abandonment would show
    try:
        assert _claim(shell, bridge, task="team/first", scope="tools/shared.py").returncode == 0
        conflict = _claim(shell, bridge, task="team/second", scope="tools/shared.py")
        assert conflict.returncode == 3 and "write-scope conflict" in conflict.stderr, conflict.stderr
        with _held(bridge[2]):                                      # released, not abandoned: the port enters
            pass
    finally:
        kernel32.CloseHandle(witness)


@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: s.split(".")[0])
def test_a_release_failure_never_replaces_the_original_error_but_surfaces_after_success(bridge, shell):
    command = (f". '{bridge[0] / 'ClaimLeaseHeartbeat.ps1'}'; "
               "$gone = New-Object System.Threading.Mutex($false); $gone.Dispose(); "
               "try { try { throw 'primary failure' } finally { Exit-BridgeQueueRootMutex -Mutex $gone } } "
               "catch { 'CAUGHT:' + $_.Exception.Message }; "
               "try { Exit-BridgeQueueRootMutex -Mutex $gone -Completed } "
               "catch { 'SURFACED:' + $_.Exception.GetBaseException().GetType().Name }")
    result = _run(shell, bridge, ["-Command", command])
    assert "CAUGHT:primary failure" in result.stdout, result.stdout + result.stderr          # the original stands
    assert "release failed after an earlier failure" in result.stdout + result.stderr
    assert "SURFACED:ObjectDisposedException" in result.stdout, result.stdout + result.stderr  # no silent success


@pytest.mark.parametrize("shell", SHELLS[:1], ids=lambda s: s.split(".")[0])
def test_a_missing_root_mutex_helper_refuses_the_mutation_and_leaves_readers_working(bridge, shell):
    (bridge[0] / "BridgeV2QueueMutex.ps1").unlink()
    refused = _claim(shell, bridge)
    assert refused.returncode != 0 and _claims(bridge) == [], refused.stdout + refused.stderr
    command = (f". '{bridge[0] / 'ClaimLeaseHeartbeat.ps1'}'; "
               "'LIVENESS:' + (Get-BridgeSessionHeartbeatLiveness -Root '" + str(bridge[2]) + "' "
               "-Claim ([pscustomobject]@{agent='x'}) -NowUtc ([DateTime]::UtcNow))")
    assert "LIVENESS:not_live" in _run(shell, bridge, ["-Command", command]).stdout


def _abandon(shell, bridge):
    command = (f". '{bridge[0] / 'BridgeV2QueueMutex.ps1'}'; "
               f"$held = Enter-BridgeV2QueueMutex -RuntimeRoot '{bridge[2]}' -TimeoutMs 5000; 'ENTERED'")
    assert "ENTERED" in _run(shell, bridge, ["-Command", command]).stdout   # the process ends holding it


@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: s.split(".")[0])
def test_an_abandoned_root_mutex_is_refused_once_and_never_adopted(bridge, shell):
    # Abandonment is only observable while the kernel object lives, so a witness handle stays open.
    from tools import bridge_v2_queue_ports_windows as ports

    kernel32 = ports._kernel32()
    witness = ports._create(mutex_name(bridge[2]), kernel32)
    try:
        assert _claim(shell, bridge).returncode == 0
        _abandon(shell, bridge)
        refused = _release(shell, bridge)
        assert refused.returncode != 0 and "abandoned" in refused.stdout + refused.stderr, refused.stderr
        assert len(_claims(bridge)) == 1 and _done(bridge) == []      # nothing changed under the refusal
        assert _release(shell, bridge).returncode == 0                # normal again after the one refusal
        _abandon(shell, bridge)
        swept = _sweep(shell, bridge)
        assert swept.returncode != 0 and "abandoned" in swept.stdout + swept.stderr, swept.stderr
        _abandon(shell, bridge)
        # The claim's own pre-claim sweep meets the abandonment first and refuses it (warned, nothing changed);
        # the claim then takes a fresh, normal ownership, as the next attempt at the Python port does.
        claimed = _claim(shell, bridge, task="team/two", scope="tools/two.py")
        assert claimed.returncode == 0 and "abandoned" in claimed.stdout + claimed.stderr, claimed.stderr
    finally:
        kernel32.CloseHandle(witness)


# -- Tools BFE7F4A1 (PS-PENDING-WAL-ADMISSION): the PS writer compares unfinished v2 transactions --------------------

def _wal(bridge, name, state="prepared", task="team/pending", scope="tools/one.py", mode="write", raw=None):
    """A v2 WAL record as recovery would apply it: only the fields this writer reads (state, after) are real."""
    wal = bridge[2] / "work_queue" / "v2" / "wal"
    wal.mkdir(parents=True, exist_ok=True)
    after = {"agent": "codex-lead-1", "task_id": task, "mode": mode, "cwd": str(bridge[1]), "write_scope": [scope]}
    record = {"state": state, "claim_rel": "work_queue/claims/x.json", "txid": "0" * 32, "after": after}
    (wal / name).write_bytes(raw if raw is not None else json.dumps(record).encode("utf-8"))


def _read_claim(shell, bridge, task="team/reader", scope="tools/one.py"):
    return _run(shell, bridge, ["-File", str(bridge[0] / "Claim-AgentTask.ps1"), "-Agent", AGENT, "-TaskId", task,
                                "-Summary", "legacy read " + task, "-Mode", "read-only", "-WriteScope", scope,
                                "-LeaseSeconds", "600"])


@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: s.split(".")[0])
@pytest.mark.parametrize("case", ["pending_overlap", "applied_overlap", "capitalised_mode_overlap", "unreadable", "unknown_state"])
def test_an_unfinished_or_unreadable_wal_record_refuses_an_overlapping_write_claim(bridge, shell, case):
    raw = b"{not json" if case == "unreadable" else None
    _wal(bridge, "a.json", state={"applied_overlap": "applied", "unknown_state": "half-done"}.get(case, "prepared"),
         mode="Write" if case == "capitalised_mode_overlap" else "write", raw=raw)
    before = (bridge[2] / "work_queue" / "v2" / "wal" / "a.json").read_bytes()
    refused = _claim(shell, bridge)
    assert refused.returncode == 3, refused.stdout + refused.stderr
    expected = "unfinished claim of team/pending" if case.endswith("overlap") else "unreadable; overlap unknown"
    assert expected in refused.stderr, refused.stderr
    assert _claims(bridge) == [] and (bridge[2] / "work_queue" / "v2" / "wal" / "a.json").read_bytes() == before
    assert _read_claim(shell, bridge).returncode == 0                       # twin: a read claim is not compared


@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: s.split(".")[0])
@pytest.mark.parametrize("case", ["disjoint", "finished", "same_task", "read_plan", "release_plan"])
def test_a_disjoint_finished_own_or_non_write_wal_record_admits_the_write_claim(bridge, shell, case):
    if case == "release_plan":
        _wal(bridge, "a.json", raw=json.dumps({"state": "prepared", "after": None}).encode("utf-8"))
    else:
        _wal(bridge, "a.json", state="aborted" if case == "finished" else "prepared",
             task="team/one" if case == "same_task" else "team/pending",
             scope="tools/other.py" if case == "disjoint" else "tools/one.py",
             mode="read-only" if case == "read_plan" else "write")
    claimed = _claim(shell, bridge)
    assert claimed.returncode == 0, claimed.stdout + claimed.stderr
    assert len(_claims(bridge)) == 1



# -- RS7F-L1 (RCO1 B4026D50): a REAL digest-valid prepared WAL left by the Python writer, not a hand-made record ------

@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: s.split(".")[0])
def test_a_real_prepared_wal_from_the_python_writer_refuses_the_overlapping_ps_claim_until_recovery(bridge, shell,
                                                                                                    monkeypatch):
    from tools import bridge_v2_queue_transactions as qt
    from tools import bridge_v2_work_queue as wq
    from tools.bridge_v2_queue_ports_windows import windows_queue_transactions

    _, worktree, runtime = bridge
    now = datetime.now(timezone.utc)
    txns = windows_queue_transactions(runtime, clock=lambda: now)   # the real root mutex the PS writer also takes
    assert txns.ports_on and type(txns.mutex).__name__ == "NamedMutexPort" and txns.root_id   # mutex_name(runtime)
    real, calls = qt._replace_atomic, {"n": 0}

    def crashing(*args, **kwargs):                       # model: test_bridge_v2_queue_snapshot_to_w3.py _pending
        calls["n"] += 1
        if calls["n"] == 1:
            raise KeyboardInterrupt("fixture crash after the WAL record, before the claim write")
        return real(*args, **kwargs)

    monkeypatch.setattr(qt, "_replace_atomic", crashing)
    with pytest.raises(KeyboardInterrupt):
        wq.claim_task(txns, agent="codex-lead-1", task_id="team/x", summary="x", mode="write",
                      write_scope=("tools/one.py",), identity=wq.OwnerIdentity("session-x", "token-x"),
                      cwd=str(worktree), now=now)
    monkeypatch.setattr(qt, "_replace_atomic", real)      # not undo(): keep the fixture's environment
    [wal] = sorted(txns.wal_dir.glob("*.json"))
    record = txns._load(wal)
    assert record is not None and record["state"] == "prepared"   # digest-valid and bound to this root
    assert record["after"]["task_id"] == "team/x" and _claims(bridge) == []
    before = wal.read_bytes()

    refused = _claim(shell, bridge, task="team/y", scope="tools/one.py")
    assert refused.returncode == 3, refused.stdout + refused.stderr
    assert "write-scope conflict with an unfinished claim of team/x" in refused.stderr, refused.stderr
    assert _claims(bridge) == [] and wal.read_bytes() == before       # nothing written, the WAL untouched
    with pytest.raises(wq.Refused, match="unfinished claim of team/x"):  # the Python writer agrees
        wq.claim_task(txns, agent="claude-rco-1", task_id="team/y", summary="y", mode="write",
                      write_scope=("tools/one.py",), identity=wq.OwnerIdentity("session-y", "token-y"),
                      cwd=str(worktree), now=now)

    assert _claim(shell, bridge, task="team/z", scope="tools/other.py").returncode == 0   # twin: disjoint
    assert _read_claim(shell, bridge, task="team/reader", scope="tools/one.py").returncode == 0   # twin: read-only

    outcomes = txns.reconcile()                           # the real recovery rolls X forward
    assert [o["outcome"] for o in outcomes] == ["rolled_forward"], outcomes
    assert list(txns.wal_dir.glob("*.json")) == []        # the record is filed, nothing left unfinished
    writes = [json.loads(path.read_text(encoding="utf-8")) for path in (runtime / "work_queue" / "claims").glob("*.json")]
    writes = {c["task_id"]: c["write_scope"] for c in writes if c.get("mode") == "write"}
    assert writes == {"team/x": ["tools/one.py"], "team/z": ["tools/other.py"]}   # no overlapping write claims
