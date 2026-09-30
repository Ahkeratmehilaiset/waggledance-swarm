# SPDX-License-Identifier: BUSL-1.1
"""The PowerShell twin of the v2 queue root-mutex name, pinned against Python (RCO1 2026-09-30).

A different name in PowerShell would silently exclude nothing, so every accepted and every refused root
must agree between tools.bridge_v2_queue_transactions.mutex_name and Get-BridgeV2QueueMutexName
(.agent-bridge/bin/BridgeV2QueueMutex.ps1). Windows only (drive-letter roots); roots live under tmp_path.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from tools.bridge_v2_queue_transactions import QueueTransactionError, mutex_name

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / ".agent-bridge" / "bin" / "BridgeV2QueueMutex.ps1"
SHELLS = [shell for shell in (shutil.which("powershell.exe"), shutil.which("pwsh")) if shell] if os.name == "nt" else []
pytestmark = pytest.mark.skipif(not SHELLS, reason="drive-letter roots and PowerShell are Windows-only")


def _ps_names(shell: str, tmp_path: Path, roots: list[str]) -> list[str]:
    """One PowerShell run: 'NAME:<name>' or 'REFUSED:<message>' per root, in order."""
    listing = tmp_path / "roots.json"
    listing.write_text(json.dumps(roots), encoding="utf-8")
    command = (f". '{HELPER}'; $roots = Get-Content -Raw -Encoding UTF8 -LiteralPath '{listing}' | ConvertFrom-Json; "
               "foreach ($root in $roots) { try { 'NAME:' + (Get-BridgeV2QueueMutexName -RuntimeRoot $root) } "
               "catch { 'REFUSED:' + $_.Exception.Message } }")
    env = {key: value for key, value in os.environ.items() if not key.startswith(("AGENT_BRIDGE_", "WD_"))}
    done = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command],
                          capture_output=True, text=True, timeout=120, env=env)
    lines = [line for line in done.stdout.splitlines() if line.startswith(("NAME:", "REFUSED:"))]
    assert len(lines) == len(roots), done.stdout + done.stderr
    return lines


def _accepted(tmp_path: Path) -> list[str]:
    base = str(tmp_path)
    (tmp_path / "Exists").mkdir()
    return [base, base + "\\", base.upper(), str(tmp_path / "Exists" / "Missing" / "deeper"),
            base.replace("\\", "/") + "/./sub//x/", base[:3]]                     # base[:3] is the drive root


def _refused(tmp_path: Path) -> list[str]:
    base = str(tmp_path)
    return [base + "\\a\\..\\b", "\\\\server\\share\\x", "relative\\x", base + "\\trailing.\\x",
            base + "\\space \\x", "C:\\PROGRA~1\\x", base + "\\x:stream", base + "\\caf\u00e9"]


@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: Path(s).stem)
def test_accepted_roots_give_the_same_mutex_name_in_both_runtimes(tmp_path, shell):
    roots = _accepted(tmp_path)
    assert _ps_names(shell, tmp_path, roots) == ["NAME:" + mutex_name(root) for root in roots]
    assert len({mutex_name(root) for root in roots[:3]}) == 1              # case and a trailing separator never split it


@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: Path(s).stem)
def test_refused_roots_are_refused_by_both_runtimes(tmp_path, shell):
    roots = _refused(tmp_path)
    for root in roots:
        with pytest.raises(QueueTransactionError):
            mutex_name(root)
    lines = _ps_names(shell, tmp_path, roots)
    assert all(line.startswith("REFUSED:") for line in lines), list(zip(roots, lines))


@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: Path(s).stem)
def test_a_junction_in_the_root_is_refused_by_both_runtimes(tmp_path, shell):
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    made = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True)
    if made.returncode != 0:
        pytest.skip("a junction could not be created here: " + made.stdout + made.stderr)
    root = str(link / "runtime")
    with pytest.raises(QueueTransactionError, match="link/reparse"):
        mutex_name(root)
    [line] = _ps_names(shell, tmp_path, [root])
    assert line.startswith("REFUSED:") and "reparse" in line
    assert _ps_names(shell, tmp_path, [str(target / "runtime")]) == ["NAME:" + mutex_name(str(target / "runtime"))]


# -- acquisition: the SAME kernel object as the Python NamedMutexPort (RCO1 2026-09-30) -------------------

def _ps_hold(shell: str, tmp_path: Path, root: Path, release_file: Path) -> subprocess.Popen:
    """A PowerShell process that enters the root mutex, prints HELD, waits for release_file, then exits it."""
    command = (f". '{HELPER}'; $held = Enter-BridgeV2QueueMutex -RuntimeRoot '{root}' -TimeoutMs 5000; 'HELD'; "
               f"$deadline = (Get-Date).AddSeconds(30); while (-not (Test-Path -LiteralPath '{release_file}') -and "
               "(Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 50 }; Exit-BridgeV2QueueMutex -Mutex $held; "
               "'RELEASED'")
    env = {key: value for key, value in os.environ.items() if not key.startswith(("AGENT_BRIDGE_", "WD_"))}
    return subprocess.Popen([shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)


def _ps_try(shell: str, root: Path, timeout_ms: int, *, exit_mutex: bool = True) -> str:
    """One PowerShell attempt: ENTERED (and exited unless exit_mutex is False) or REFUSED:<message>."""
    tail = "Exit-BridgeV2QueueMutex -Mutex $held; " if exit_mutex else ""
    command = (f". '{HELPER}'; try {{ $held = Enter-BridgeV2QueueMutex -RuntimeRoot '{root}' -TimeoutMs {timeout_ms}; "
               f"{tail}'ENTERED' }} catch {{ 'REFUSED:' + $_.Exception.Message }}")
    env = {key: value for key, value in os.environ.items() if not key.startswith(("AGENT_BRIDGE_", "WD_"))}
    done = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command],
                          capture_output=True, text=True, timeout=120, env=env)
    lines = [line for line in done.stdout.splitlines() if line.startswith(("ENTERED", "REFUSED:"))]
    assert len(lines) == 1, done.stdout + done.stderr
    return lines[0]


@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: Path(s).stem)
def test_a_powershell_holder_excludes_the_python_port_until_it_exits(tmp_path, shell):
    from tools.bridge_v2_queue_ports_windows import NamedMutexPort
    from tools.bridge_v2_queue_transactions import LockTimeout

    root, release_file = tmp_path / "runtime", tmp_path / "release"
    root.mkdir()
    holder = _ps_hold(shell, tmp_path, root, release_file)
    try:
        assert holder.stdout.readline().strip() == "HELD", holder.stderr.read()
        with pytest.raises(LockTimeout, match="busy"):
            with NamedMutexPort().hold(mutex_name(root), 0.3):
                pytest.fail("the Python port must not enter while PowerShell holds the root mutex")
        release_file.write_text("go", encoding="utf-8")
        assert holder.stdout.readline().strip() == "RELEASED"
    finally:
        release_file.write_text("go", encoding="utf-8")
        holder.wait(60)
    with NamedMutexPort().hold(mutex_name(root), 1):                      # success twin: released
        pass


@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: Path(s).stem)
def test_a_python_holder_makes_powershell_refuse_busy_and_its_twin_enters(tmp_path, shell):
    from tools.bridge_v2_queue_ports_windows import NamedMutexPort

    root = tmp_path / "runtime"
    root.mkdir()
    with NamedMutexPort().hold(mutex_name(root), 1):
        refused = _ps_try(shell, root, 300)
    assert refused.startswith("REFUSED:") and "busy" in refused
    assert _ps_try(shell, root, 1000) == "ENTERED"                         # success twin once released


@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: Path(s).stem)
def test_an_abandoned_root_mutex_is_refused_once_then_entered(tmp_path, shell):
    # Abandonment is only observable while the kernel object lives: with no other open handle, a holder
    # that exits destroys it and the next caller creates a fresh one (WAL recovery, not this signal, is the
    # safety net). So this test keeps one non-owning handle open, as a concurrent waiter would.
    from tools import bridge_v2_queue_ports_windows as ports

    root = tmp_path / "runtime"
    root.mkdir()
    kernel32 = ports._kernel32()
    witness = ports._create(mutex_name(root), kernel32)
    try:
        assert _ps_try(shell, root, 1000, exit_mutex=False) == "ENTERED"  # the process ends holding it
        abandoned = _ps_try(shell, root, 1000)
        assert abandoned.startswith("REFUSED:") and "abandoned" in abandoned
        assert _ps_try(shell, root, 1000) == "ENTERED"                     # normal again after the refusal
    finally:
        kernel32.CloseHandle(witness)
