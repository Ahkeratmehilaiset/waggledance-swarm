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
