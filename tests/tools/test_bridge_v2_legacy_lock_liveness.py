"""Legacy PowerShell stale sweep liveness under the v2 runtime-root mutex (RCO2 5416a7ab F1; Lead 792d42b6).

Invoke-StaleClaimSweep decides and archives under the root, claim and beat locks, and only then, with every lock
released, reads the dispatcher tail and writes the release event, in archive order. A slow or failing event writer
therefore never makes a concurrent claim wait for the root. Runs on the participation fixture (private bin copy,
Local legacy mutex names, temp runtime root, stub event writer); nothing live is touched.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import time

import pytest

from test_bridge_v2_legacy_mutex_participation import (SHELLS, SESSION, TOKEN, _busy, _claim, _claims, _done,  # noqa: F401
                                                       _stale_claim, bridge)

pytestmark = pytest.mark.skipif(not SHELLS, reason="Windows PowerShell hosts only")

# The stub event writer logs its -TaskId and -PayloadJson, optionally after a sleep or instead of a throw.
WRITER = r"""
$task = ''; $payload = ''
for ($i = 0; $i -lt $args.Count; $i++) {
    if ($args[$i] -ceq '-TaskId') { $task = [string]$args[$i + 1] }
    if ($args[$i] -ceq '-PayloadJson') { $payload = [string]$args[$i + 1] }
}
if ($task -cnotlike 'team/stale*') { 'fixture: claim event'; return }
Start-Sleep -Seconds __SLEEP__
if ('__THROW__' -ceq 'yes') { throw 'fixture: event writer down' }
Add-Content -LiteralPath '__LOG__' -Value ($task + "`t" + $payload) -Encoding utf8
"""


def _writer(bridge, tmp_path, sleep=0, throw=False) -> Path:
    log = tmp_path / "events.log"
    text = WRITER.replace("__SLEEP__", str(sleep)).replace("__THROW__", "yes" if throw else "no")
    (bridge[0] / "Write-AgentEvent.ps1").write_text(text.replace("__LOG__", str(log)), encoding="utf-8")
    return log


def _env(bridge):
    env = {key: value for key, value in os.environ.items() if not key.startswith(("AGENT_BRIDGE_", "WD_"))}
    env.update(AGENT_BRIDGE_RUNTIME_ROOT=str(bridge[2]), AGENT_BRIDGE_OWNER_SESSION_ID=SESSION,
               AGENT_BRIDGE_OWNER_TOKEN=TOKEN)
    return env


def _sweep_command(shell, bridge):
    command = f"& '{bridge[0] / 'Invoke-StaleClaimSweep.ps1'}' -Quiet | ForEach-Object {{ 'REC:' + $_.task_id }}"
    return [shutil.which(shell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command]


def _sweep(shell, bridge):
    return subprocess.run(_sweep_command(shell, bridge), cwd=bridge[1], env=_env(bridge), capture_output=True,
                          text=True, timeout=180)


def _stale(bridge, task):
    path = _stale_claim(bridge, task)
    target = path.with_name(task.replace("/", "_") + ".json")
    path.rename(target)
    return target


@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: s.split(".")[0])
def test_a_claim_lands_while_a_sweep_writes_a_slow_release_event(bridge, shell, tmp_path):
    log = _writer(bridge, tmp_path, sleep=12)
    stale = _stale(bridge, "team/stale")
    sweeper = subprocess.Popen(_sweep_command(shell, bridge), cwd=bridge[1], env=_env(bridge),
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 30
        while stale.exists() and time.monotonic() < deadline:   # wait until it is archived (lock work done)
            time.sleep(0.1)
        assert not stale.exists() and len(_done(bridge)) == 1
        started = time.monotonic()
        claimed = _claim(shell, bridge)
        elapsed = time.monotonic() - started
        assert claimed.returncode == 0, claimed.stdout + claimed.stderr
        assert not _busy(claimed) and _claims(bridge) == ["team_one.json"]
        assert sweeper.poll() is None and not log.exists()   # the claim landed while the event was still pending
        assert elapsed < 8, elapsed
    finally:
        out, err = sweeper.communicate(timeout=120)
    assert sweeper.returncode == 0, err
    assert out.split() == ["REC:team/stale"]
    task, payload = log.read_text(encoding="utf-8-sig").strip().split("\t")
    assert task == "team/stale" and json.loads(payload)["archived_path"].endswith(_done(bridge)[0])


@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: s.split(".")[0])
def test_events_and_records_follow_archive_order_with_the_decided_values(bridge, shell, tmp_path):
    log = _writer(bridge, tmp_path)
    _stale(bridge, "team/stale-a")
    _stale(bridge, "team/stale-b")
    result = _sweep(shell, bridge)
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["REC:team/stale-a", "REC:team/stale-b"]
    rows = [line.split("\t") for line in log.read_text(encoding="utf-8-sig").strip().splitlines()]
    assert [task for task, _ in rows] == ["team/stale-a", "team/stale-b"]
    done = _done(bridge)
    for (task, payload), name in zip(rows, done):
        fields = json.loads(payload)
        assert fields["task_id"] == task and fields["claim_agent"] == "claude-rco-2"
        assert fields["archived_path"].endswith(name) and fields["claim_lease_seconds"] == 1
        archived = json.loads((bridge[2] / "work_queue" / "done" / name).read_text(encoding="utf-8-sig"))
        assert archived["release_status"] == "stale_lease" and archived["task_id"] == task
    assert _claims(bridge) == []


@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: s.split(".")[0])
def test_a_failed_release_event_is_warned_and_the_archive_stands(bridge, shell, tmp_path):
    _writer(bridge, tmp_path, throw=True)
    _stale(bridge, "team/stale")
    result = _sweep(shell, bridge)
    assert result.returncode == 0, result.stderr
    assert "stale-lease release event emit failed: fixture: event writer down" in result.stdout + result.stderr
    assert result.stdout.split()[-1] == "REC:team/stale"
    assert _claims(bridge) == [] and len(_done(bridge)) == 1


@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: s.split(".")[0])
def test_the_dispatcher_lookup_and_event_run_after_every_lock_is_released(bridge, shell):
    text = (bridge[0] / "Invoke-StaleClaimSweep.ps1").read_text(encoding="utf-8-sig")
    root_exit = text.index("Exit-BridgeQueueRootMutex -Mutex $rootMutex")
    assert text.index("foreach ($archived in $archivedReleases)") > root_exit
    assert text.index("Get-StaleClaimDispatcher -Claim $claim -BridgeRoot $bridgeRoot") > root_exit
    assert text.index("& $writeEvent") > root_exit
    assert text.index("[System.IO.File]::Move($file.FullName, $donePath)") < root_exit
