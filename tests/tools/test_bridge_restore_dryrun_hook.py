# SPDX-License-Identifier: MIT
"""Isolated hook-unit tests, not full Restore/Drain or DryRun purity proof."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import time

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / ".agent-bridge/bin/Restore-BridgeSpool.ps1"
READY_ENV = "AGENT_BRIDGE_TEST_CANONICAL_SCAN_READY"

PS_HOOK = r"""
$ErrorActionPreference = 'Stop'
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $env:WD_HOOK_UNIT_SOURCE, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count -ne 0) { throw 'Restore source parse errors' }
$functions = @($ast.FindAll({ param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
    $node.Name -ceq 'Invoke-BridgeCanonicalScanTestHook'
}, $true))
if ($functions.Count -ne 1) { throw 'Expected exactly one actual hook' }
$function = $functions[0]
$body = $function.Extent.Text
if ($env:WD_HOOK_UNIT_MUTANT -ceq '1') {
    $guards = @($function.Body.FindAll({ param($node)
        $node -is [System.Management.Automation.Language.IfStatementAst] -and
        $node.Clauses.Count -eq 1 -and $null -eq $node.ElseClause -and
        $node.Clauses[0].Item1.Extent.Text.Trim() -ceq '$DryRun' -and
        $node.Clauses[0].Item2.Statements.Count -eq 1 -and
        $node.Clauses[0].Item2.Statements[0] -is
            [System.Management.Automation.Language.ReturnStatementAst]
    }, $true))
    if ($guards.Count -ne 1) { throw 'Expected exactly one DryRun return guard' }
    $guard = $guards[0]
    $body = $body.Remove($guard.Extent.StartOffset - $function.Extent.StartOffset,
        $guard.Extent.EndOffset - $guard.Extent.StartOffset)
}
# Only this AST-extracted function is evaluated; no whole-file dot sourcing.
. ([scriptblock]::Create($body))
$script:DryRun = $env:WD_HOOK_UNIT_DRYRUN -ceq '1'
Invoke-BridgeCanonicalScanTestHook
[Console]::WriteLine('HOOK_UNIT_COMPLETED')
"""


@pytest.fixture(params=["powershell", "pwsh"], ids=["PS5", "PS7"])
def shell(request):
    if os.name != "nt":
        pytest.skip("Windows child PowerShell required")
    pin = "WD_HOOK_UNIT_PS5" if request.param == "powershell" else "WD_HOOK_UNIT_PS7"
    executable = os.environ.get(pin) or shutil.which(request.param)
    if not executable:
        pytest.skip(f"{request.param} unavailable; engine coverage not measured")
    assert Path(executable).is_file(), f"Pinned {pin} executable missing"
    return executable


def _env(ready, dry_run, mutant=False):
    env = os.environ.copy()
    env.pop(READY_ENV, None)
    env.update(WD_HOOK_UNIT_SOURCE=str(SOURCE),
               WD_HOOK_UNIT_DRYRUN="1" if dry_run else "0",
               WD_HOOK_UNIT_MUTANT="1" if mutant else "0")
    if ready is not None:
        env[READY_ENV] = str(ready)
    return env


def _command(shell):
    return [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", PS_HOOK]


def _completed(process):
    assert process.returncode == 0, (process.stdout, process.stderr)
    assert process.stdout.strip() == "HOOK_UNIT_COMPLETED", process.stdout
    assert not process.stderr.strip(), process.stderr


def _run(shell, ready, dry_run, mutant=False, cwd=None):
    process = subprocess.run(_command(shell), env=_env(ready, dry_run, mutant),
                             cwd=cwd, capture_output=True, text=True,
                             timeout=15, check=False)
    _completed(process)


def _assert_inert(ready, before):
    if before is None:
        if ready.exists():
            raise AssertionError("DryRun hook created readiness marker")
    else:
        assert ready.read_bytes() == before, "DryRun hook overwrote sentinel marker"


@pytest.mark.parametrize("before", [None, b"sentinel\x00\xff\r\n"],
                         ids=["absent", "sentinel"])
def test_dryrun_hook_keeps_markers_inert(shell, tmp_path, before):
    ready = tmp_path / "hook.ready"
    release = tmp_path / "hook.ready.release"
    # Pre-existing release makes the mutant fail the intended marker assertion.
    release.write_bytes(b"release-sentinel")
    if before is not None:
        ready.write_bytes(before)
    _run(shell, ready, True)
    _assert_inert(ready, before)
    assert release.read_bytes() == b"release-sentinel"


@pytest.mark.parametrize("dry_run", [False, True])
def test_no_env_hook_is_noop(shell, tmp_path, dry_run):
    _run(shell, None, dry_run, cwd=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_nondryrun_hook_retains_rendezvous(shell, tmp_path):
    ready = tmp_path / "hook.ready"
    release = tmp_path / "hook.ready.release"
    process = subprocess.Popen(_command(shell), env=_env(ready, False),
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True)
    try:
        deadline = time.monotonic() + 5
        while process.poll() is None:
            try:
                if ready.read_bytes() == b"ready":
                    break
            except OSError:
                pass  # Marker creation/write may not yet have completed.
            if time.monotonic() >= deadline:
                pytest.fail("Non-DryRun hook did not signal readiness")
            time.sleep(0.02)
        assert ready.exists(), "Non-DryRun hook exited without readiness"
        assert ready.read_bytes() == b"ready"
        assert process.poll() is None, "Hook returned before release"
        assert not release.exists()
        release.write_bytes(b"release")
        stdout, stderr = process.communicate(timeout=10)
        _completed(subprocess.CompletedProcess(process.args, process.returncode,
                                               stdout, stderr))
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)


def test_guard_removal_mutant_fails_intended_assertion(shell, tmp_path):
    ready = tmp_path / "hook.ready"
    release = tmp_path / "hook.ready.release"
    release.write_bytes(b"release-sentinel")
    _run(shell, ready, True, mutant=True)
    # Successful child completion rules out a setup error masquerading as a kill.
    with pytest.raises(AssertionError, match="^DryRun hook created readiness marker$"):
        _assert_inert(ready, None)
    assert ready.read_bytes() == b"ready"
    assert release.read_bytes() == b"release-sentinel"
