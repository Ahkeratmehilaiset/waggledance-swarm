"""Ordinary durable-checkpoint recovery and isolated Tail0 reader checks."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
WRITER = ROOT / "ops/windows/reboot/Write-WdLaneCurrentState.ps1"
READER = ROOT / ".agent-bridge/bin/Read-AgentBridge.ps1"
SHELLS = sorted({p for p in (shutil.which("powershell.exe"), shutil.which("pwsh")) if p})
pytestmark = pytest.mark.skipif(os.name != "nt" or not SHELLS or not shutil.which("git"),
                                reason="Windows C: checkpoint fixture requires PowerShell and Git")


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def shell_run(shell, command, *, env=None):
    return subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command", command],
                          cwd=ROOT, env=env, capture_output=True, text=True, timeout=45)


@pytest.fixture(params=SHELLS or [None])
def recovery(request):
    audit = ROOT / ".codex-audit"
    audit.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="checkpoint-recovery-", dir=audit) as directory:
        worktree = Path(directory)
        assert worktree.resolve().drive.upper() == "C:"
        # This is a new disposable test repository, never a recovered snapshot.
        subprocess.run(["git", "init", "-q", str(worktree)], check=True, capture_output=True)
        (worktree / "seed.txt").write_text("ordinary recovery fixture\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(worktree), "add", "seed.txt"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(worktree), "-c", "user.name=WD recovery test",
                        "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
                        "commit", "-q", "-m", "fixture seed"], check=True, capture_output=True)
        head = subprocess.check_output(["git", "-C", str(worktree), "rev-parse", "HEAD"], text=True).strip()
        yield {"shell": request.param, "worktree": worktree, "head": head}


def write_checkpoint(recovery, task="before-restart", extra=""):
    return shell_run(recovery["shell"],
                     f"& {quote(WRITER)} -Agent codex-tools-1 -Worktree {quote(recovery['worktree'])} "
                     f"-TaskId {quote(task)} -Status working -NextAction 'Read current bridge evidence' {extra} | Out-Null")


def reload_checkpoint(recovery):
    path = recovery["worktree"] / ".codex-audit/wd-current-state.json"
    result = shell_run(recovery["shell"],
                       f"Get-Content -LiteralPath {quote(path)} -Raw | ConvertFrom-Json -ErrorAction Stop | ConvertTo-Json -Depth 6")
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_checkpoint_reloads_in_new_process_despite_orphan_partial_temp(recovery):
    result = write_checkpoint(recovery)
    assert result.returncode == 0, result.stderr
    directory = recovery["worktree"] / ".codex-audit"
    canonical = directory / "wd-current-state.json"
    saved = canonical.read_bytes()
    orphan = directory / ".wd-current-state.99999.interrupted.tmp"
    orphan.write_bytes(b'{"schema":"wd.lane-current.v1","task_id":')
    state = reload_checkpoint(recovery)
    assert state["schema"] == "wd.lane-current.v1"
    assert state["task_id"] == "before-restart"
    assert state["head"] == recovery["head"]
    assert canonical.read_bytes() == saved
    assert orphan.read_bytes().endswith(b'"task_id":')
    result = write_checkpoint(recovery, task="after-restart")
    assert result.returncode == 0, result.stderr
    assert reload_checkpoint(recovery)["task_id"] == "after-restart"
    assert list(directory.glob("*.tmp")) == [orphan]
    assert orphan.read_bytes().endswith(b'"task_id":')


@pytest.mark.parametrize("invalid", ["-NextWakeupUtc not-a-timestamp", "-WriteScope ('x' * 501)",
                                     "-WorkHeld $true", "-ReleaseHeld $true", "-WorkHeld 1 -ReleaseHeld $false",
                                     "-WorkHeld 'false' -ReleaseHeld $true", "-WorkHeld $null -ReleaseHeld $true"])
def test_rejected_next_checkpoint_preserves_last_durable_record(recovery, invalid):
    result = write_checkpoint(recovery)
    assert result.returncode == 0, result.stderr
    directory = recovery["worktree"] / ".codex-audit"
    orphan = directory / ".wd-current-state.99999.interrupted.tmp"
    orphan.write_bytes(b'{"partial":')
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in directory.iterdir()}
    rejected = write_checkpoint(recovery, task="must-not-replace", extra=invalid)
    assert rejected.returncode != 0
    assert reload_checkpoint(recovery)["task_id"] == "before-restart"
    assert {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in directory.iterdir()} == before


def test_current_legacy_tail_zero_reads_normal_isolated_jsonl(recovery):
    runtime = recovery["worktree"] / ".codex-audit/runtime"
    (runtime / "shared").mkdir(parents=True)
    events = runtime / "shared/events.jsonl"
    rows = [dict(ts_utc="2026-09-13T12:00:00Z", agent="codex-tools-1",
                 session_id="recovery-fixture", task_id=f"task-{index}",
                 type="status", status="working", message=f"ordinary fixture row {index}")
            for index in range(3)]
    events.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    before = events.read_bytes()
    env = dict(os.environ)
    env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(runtime)
    result = shell_run(recovery["shell"],
                       f"& {quote(READER)} -NoAckReceived -NoContinuity -ShowClaims -Tail 0", env=env)
    assert result.returncode == 0, result.stderr
    assert "stable event snapshot unavailable" not in result.stdout + result.stderr
    for index in range(3):
        assert f"ordinary fixture row {index}" in result.stdout
    assert events.read_bytes() == before
    assert not list(runtime.rglob("*.wal"))


def state_file(recovery):
    return json.loads((recovery["worktree"] / ".codex-audit/wd-current-state.json").read_text(encoding="utf-8"))


def test_structured_holds_are_paired_booleans_never_defaulted_and_never_lost_by_omission(recovery):
    assert write_checkpoint(recovery).returncode == 0
    assert not {"work_held", "release_held"} & set(state_file(recovery))
    declared = write_checkpoint(recovery, task="prepare", extra="-WorkHeld $false -ReleaseHeld $true")
    assert declared.returncode == 0, declared.stderr
    assert (state_file(recovery)["work_held"], state_file(recovery)["release_held"]) == (False, True)
    # An omitted declaration keeps the HOLD, also across a task change.
    carried = write_checkpoint(recovery, task="next-step")
    assert carried.returncode == 0, carried.stderr
    state = state_file(recovery)
    assert (state["task_id"], state["work_held"], state["release_held"]) == ("next-step", False, True)
    # Only an explicit declaration changes or clears it.
    assert write_checkpoint(recovery, task="paused-work", extra="-WorkHeld $true -ReleaseHeld $true").returncode == 0
    assert write_checkpoint(recovery, task="cleared", extra="-WorkHeld $false -ReleaseHeld $false").returncode == 0
    state = state_file(recovery)
    assert (state["task_id"], state["work_held"], state["release_held"]) == ("cleared", False, False)


@pytest.mark.parametrize("fields,refused", [
    (dict(work_held=True), True), (dict(release_held=True), True),
    (dict(work_held="true", release_held=True), True), (dict(work_held=None, release_held=False), True),
    (dict(agent="codex-lead-1", work_held=False, release_held=True), True),
    (None, True), (dict(Work_Held=True, Release_Held=True), False),
])
def test_undeterminable_previous_hold_declaration_refuses_an_omitted_write(recovery, fields, refused):
    assert write_checkpoint(recovery).returncode == 0
    path = recovery["worktree"] / ".codex-audit/wd-current-state.json"
    if fields is None:
        path.write_text('{"schema": "wd.lane-current.v1", "work_held":', encoding="utf-8")
    else:
        path.write_text(json.dumps(dict(json.loads(path.read_text(encoding="utf-8")), **fields)), encoding="utf-8")
    before = path.read_bytes()
    omitted = write_checkpoint(recovery, task="omitted")
    assert (omitted.returncode != 0) is refused, omitted.stderr
    if refused:
        assert path.read_bytes() == before
        explicit = write_checkpoint(recovery, task="explicit", extra="-WorkHeld $true -ReleaseHeld $true")
        assert explicit.returncode == 0, explicit.stderr
        assert (state_file(recovery)["work_held"], state_file(recovery)["release_held"]) == (True, True)
    else:
        assert not {"work_held", "release_held"} & set(state_file(recovery))
