# SPDX-License-Identifier: BUSL-1.1
"""S2 off Windows: the legacy PowerShell claim writers take no runtime-root mutex there, just as the Python CLI writers
take none (tools/work_queue.py _root_mutex returns a null context off Windows).

The v2 runtime-root mutex is a Windows named mutex, named from a drive-letter root. Under pwsh on Linux the S2
PowerShell half (1764254b) still canonicalized every root, so a POSIX root refused every claim mutation with "a root
must be a local drive-letter path" (CI on #1756 at db52a697: test_bridge_stale_routing.py, pwsh). Off Windows,
Enter-BridgeQueueRootMutex now returns no mutex and excludes nothing, and Exit-BridgeQueueRootMutex accepts that.

The host check is one function, Test-BridgeQueueRootMutexHost, so the first test also runs on Windows by standing in
for another host after dot-sourcing; its twin shows that with the real check a POSIX root is still refused there. The
writers' round trip on a POSIX root runs where the host really is not Windows (pwsh on Linux in CI). The bin runs from
a copy whose Global mutex names are made Local and whose event writer is a stub.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

REPO = Path(__file__).resolve().parents[2]
HELPER = REPO / ".agent-bridge" / "bin" / "ClaimLeaseHeartbeat.ps1"
SHELLS = [shell for shell in (("powershell.exe", "pwsh.exe") if os.name == "nt" else ("pwsh",)) if shutil.which(shell)]
pytestmark = pytest.mark.skipif(not SHELLS, reason="no PowerShell on this host")
POSIX_ROOT = "/tmp/wd-legacy-mutex-off-windows/runtime"
STAND_IN = "function Test-BridgeQueueRootMutexHost { $false }; "
AGENT = "claude-rco-2"
SESSION = "off-windows-session"
TOKEN = "e" * 64


def _env(runtime: Path | None = None) -> dict:
    env = {key: value for key, value in os.environ.items() if not key.startswith(("AGENT_BRIDGE_", "WD_"))}
    if runtime is not None:
        env.update(AGENT_BRIDGE_RUNTIME_ROOT=str(runtime), AGENT_BRIDGE_OWNER_SESSION_ID=SESSION,
                   AGENT_BRIDGE_OWNER_TOKEN=TOKEN)
    return env


def _shell(shell: str, arguments: list, env: dict, cwd: Path | None = None) -> subprocess.CompletedProcess:
    prefix = [shutil.which(shell), "-NoProfile", "-NonInteractive"]
    if os.name == "nt":
        prefix += ["-ExecutionPolicy", "Bypass"]
    return subprocess.run(prefix + arguments, cwd=cwd, env=env, capture_output=True, text=True, timeout=180)


@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: s.split(".")[0])
def test_off_windows_enter_returns_no_mutex_and_exit_accepts_it(shell):
    command = (f". '{HELPER}'; {STAND_IN}$held = Enter-BridgeQueueRootMutex -Root '{POSIX_ROOT}'; "
               "'NULL:' + ($null -eq $held); Exit-BridgeQueueRootMutex -Mutex $held -Completed; 'EXITED'")
    done = _shell(shell, ["-Command", command], _env())
    assert done.returncode == 0, done.stdout + done.stderr
    assert done.stdout.split() == ["NULL:True", "EXITED"]


@pytest.mark.skipif(os.name != "nt", reason="the twin: on Windows the real host check keeps the mutex")
@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: s.split(".")[0])
def test_on_windows_the_real_host_check_still_refuses_a_posix_root(shell):
    command = (f". '{HELPER}'; 'HOST:' + (Test-BridgeQueueRootMutexHost); "
               f"$held = Enter-BridgeQueueRootMutex -Root '{POSIX_ROOT}'")
    done = _shell(shell, ["-Command", command], _env())
    assert done.returncode != 0 and "HOST:True" in done.stdout, done.stdout + done.stderr
    assert "a root must be a local drive-letter path" in done.stdout + done.stderr


@pytest.fixture
def bridge(tmp_path):
    code = tmp_path / "fixture" / ".agent-bridge" / "bin"
    shutil.copytree(REPO / ".agent-bridge" / "bin", code)
    (tmp_path / "fixture" / "configs").mkdir()
    shutil.copy2(REPO / "configs" / "bridge_identity_registry.json", tmp_path / "fixture" / "configs")
    local = "Local\\WdOffWindowsFixture-" + os.urandom(8).hex() + "-"
    for script in code.glob("*.ps1"):
        source = script.read_text(encoding="utf-8-sig")
        if "Global\\WaggleDanceBridge" in source:
            script.write_text(source.replace("Global\\WaggleDanceBridge", local), encoding="utf-8-sig")
    (code / "Write-AgentEvent.ps1").write_text("$null = $args\n'fixture: no event written'\n", encoding="utf-8")
    worktree = tmp_path / "wt"
    for child in ("objects", "refs"):                 # RS7: a claim cwd must be a git top level
        (worktree / ".git" / child).mkdir(parents=True)
    (worktree / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    runtime = tmp_path / "runtime"
    (runtime / "work_queue" / "claims").mkdir(parents=True)
    return code, worktree, runtime


@pytest.mark.skipif(os.name == "nt", reason="the writers' round trip on a POSIX root needs a host that is not Windows")
def test_off_windows_the_legacy_writers_claim_bump_sweep_and_release_on_a_posix_root(bridge):
    code, worktree, runtime = bridge
    env = _env(runtime)
    claims = runtime / "work_queue" / "claims"
    claim = _shell("pwsh", ["-File", str(code / "Claim-AgentTask.ps1"), "-Agent", AGENT, "-TaskId", "team/one",
                            "-Summary", "off windows", "-Mode", "write", "-WriteScope", "tools/one.py",
                            "-LeaseSeconds", "600"], env, worktree)
    assert claim.returncode == 0, claim.stdout + claim.stderr
    [path] = sorted(claims.glob("*.json"))
    assert json.loads(path.read_text(encoding="utf-8-sig"))["owner_session_id"] == SESSION
    before = path.read_bytes()
    bumped = _shell("pwsh", ["-Command", f". '{code / 'ClaimLeaseHeartbeat.ps1'}'; "
                                         f"'BUMPED:' + (Update-BridgeClaimLease -Root '{runtime}' -AgentName '{AGENT}')"],
                    env, worktree)
    assert "BUMPED:1" in bumped.stdout, bumped.stdout + bumped.stderr
    assert path.read_bytes() != before
    old = datetime.now(timezone.utc) - timedelta(hours=2)
    stale = claims / "team_stale.json"
    stale.write_text(json.dumps({"claimed_at_utc": old.isoformat(), "last_heartbeat_utc": old.isoformat(),
                                 "agent": AGENT, "task_id": "team/stale", "summary": "stale", "mode": "write",
                                 "write_scope": ["tools/stale.py"], "resources": [], "run_id": "", "lease_seconds": 1,
                                 "claim_lease_expires_utc": (old + timedelta(seconds=1)).isoformat(), "pid": 1,
                                 "cwd": str(worktree), "git_branch": "", "owner_identity": "none"}), encoding="utf-8")
    swept = _shell("pwsh", ["-File", str(code / "Invoke-StaleClaimSweep.ps1"), "-Quiet"], env, worktree)
    assert swept.returncode == 0 and not stale.exists(), swept.stdout + swept.stderr
    released = _shell("pwsh", ["-File", str(code / "Release-AgentTask.ps1"), "-Agent", AGENT, "-TaskId", "team/one",
                               "-Status", "done", "-Message", "released"], env, worktree)
    assert released.returncode == 0, released.stdout + released.stderr
    assert sorted(claims.glob("*.json")) == [] and len(list((runtime / "work_queue" / "done").glob("*.json"))) == 2
