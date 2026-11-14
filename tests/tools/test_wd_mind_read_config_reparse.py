"""Owned Windows junction fixture for the read-only Mind config helper.

The target, fleet and junction are created only under this worktree's
``.codex-audit``. No fixture directory is recursively deleted; failed fixtures
remain there for inspection. No installed bundle, agent or writer is invoked.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import queue
import stat
import subprocess
import threading
import uuid

import pytest


ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "ops/windows/reboot/Get-WdMindReadConfig.ps1"
PS5 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
pytestmark = pytest.mark.skipif(os.name != "nt" or not PS5.is_file(), reason="Windows PowerShell 5.1 required")
REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def _no_reparse_ancestors(path: Path) -> None:
    for part in (path, *path.parents):
        attrs = getattr(os.lstat(part), "st_file_attributes", 0)
        assert not attrs & REPARSE, f"reparse ancestor in owned fixture: {part}"


def _contained(root: Path, path: Path) -> None:
    assert os.path.commonpath([str(root), str(path)]) == str(root)


def _check_owned_link(root: Path, target: Path, junction: Path, sentinel: Path) -> None:
    _no_reparse_ancestors(root)
    _no_reparse_ancestors(target)
    _contained(root, target)
    _contained(root, junction)
    assert getattr(os.lstat(junction), "st_file_attributes", 0) & REPARSE
    assert os.path.normcase(os.path.realpath(junction, strict=True)) == os.path.normcase(str(target.resolve(strict=True)))
    info = os.lstat(sentinel)
    assert stat.S_ISREG(info.st_mode) and not getattr(info, "st_file_attributes", 0) & REPARSE
    assert sentinel.read_bytes() == b"synthetic sentinel survives"


@pytest.mark.parametrize("descendant", [False, True], ids=["junction-parent", "junction-ancestor"])
def test_read_config_refuses_owned_junction_ancestor(descendant):
    # Do not put a reparse point under pytest's disposable --basetemp tree.
    # This retained root is uniquely named; no directory is recursively removed.
    _no_reparse_ancestors(ROOT)
    audit = ROOT / ".codex-audit"
    audit.mkdir(exist_ok=True)
    _no_reparse_ancestors(audit)
    base = audit / "owned-reparse-fixtures"
    try:
        base.mkdir(exist_ok=False)
    except FileExistsError:
        pass
    _no_reparse_ancestors(base)
    assert base.is_dir() and base.resolve(strict=True).is_relative_to(audit.resolve(strict=True))
    root = base / uuid.uuid4().hex
    root.mkdir(exist_ok=False)
    _no_reparse_ancestors(root)
    assert root.resolve(strict=True).is_relative_to(audit.resolve(strict=True))
    target = root / "owned-target"
    target.mkdir()
    _no_reparse_ancestors(target)
    (target / "nested").mkdir()
    _no_reparse_ancestors(target / "nested")
    sentinel = target / "sentinel.bin"
    with sentinel.open("xb") as stream:
        stream.write(b"synthetic sentinel survives")
    junction = root / "owned-junction"
    assert not os.path.lexists(junction)
    _contained(root, target)
    _contained(root, junction)
    live = junction / "nested" / "mind-live" if descendant else junction / "mind-live"
    raw = json.dumps({"mind_read": {"enabled": True, "live_dir": str(live)}}, sort_keys=True).encode("utf-8")
    fleet = root / "synthetic-fleet.json"
    with fleet.open("xb") as stream:
        stream.write(raw)
    control_live = target / "nested" / "mind-live" if descendant else target / "mind-live"
    control_raw = json.dumps({"mind_read": {"enabled": True, "live_dir": str(control_live)}},
                             sort_keys=True).encode("utf-8")
    control_fleet = root / "synthetic-control-fleet.json"
    with control_fleet.open("xb") as stream:
        stream.write(control_raw)
    system_root = os.environ["SystemRoot"]
    env = {"SystemRoot": system_root, "WINDIR": system_root, "PATH": str(Path(system_root) / "System32"),
           "TEMP": str(root), "TMP": str(root), "USERPROFILE": str(root),
           "WD_TEST_HELPER": str(HELPER), "WD_TEST_ROOT": str(root), "WD_TEST_TARGET": str(target),
           "WD_TEST_JUNCTION": str(junction), "WD_TEST_FLEET": str(fleet),
           "WD_TEST_DIGEST": hashlib.sha256(raw).hexdigest(), "WD_TEST_CONTROL_FLEET": str(control_fleet),
           "WD_TEST_CONTROL_DIGEST": hashlib.sha256(control_raw).hexdigest()}
    script = r'''
$ErrorActionPreference='Stop'
$root=[IO.Path]::GetFullPath($env:WD_TEST_ROOT).TrimEnd('\')
$target=[IO.Path]::GetFullPath($env:WD_TEST_TARGET)
$junction=[IO.Path]::GetFullPath($env:WD_TEST_JUNCTION)
if (-not $target.StartsWith($root+'\',[StringComparison]::OrdinalIgnoreCase) -or
    -not $junction.StartsWith($root+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'outside_owned_fixture' }
$targetItem=Get-Item -LiteralPath $target -Force -ErrorAction Stop
if (-not $targetItem.PSIsContainer -or ($targetItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'invalid_target' }
if (Test-Path -LiteralPath $junction) { throw 'junction_already_exists' }
New-Item -ItemType Junction -Path $junction -Target $target | Out-Null
[Console]::Out.WriteLine('JUNCTION_READY'); [Console]::Out.Flush()
if ([Console]::In.ReadLine() -cne 'RUN_AFTER_OWNERSHIP_CHECK') { exit 4 }
. $env:WD_TEST_HELPER
try {
    $value=Get-WdMindReadConfig -FleetPath $env:WD_TEST_FLEET -FleetSha256 $env:WD_TEST_DIGEST
    if ($null -eq $value) { $state='OFF' } else { $state='ENABLED' }
} catch { $state='REFUSED:'+$_.Exception.Message }
[Console]::Out.WriteLine('RESULT:'+$state); [Console]::Out.WriteLine('READY_TO_REMOVE'); [Console]::Out.Flush()
if ([Console]::In.ReadLine() -cne 'REMOVE_AFTER_OWNERSHIP_CHECK') { exit 5 }
$item=Get-Item -LiteralPath $junction -Force -ErrorAction Stop
$targetItem=Get-Item -LiteralPath $target -Force -ErrorAction Stop
if (-not ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
    ($targetItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -or -not $targetItem.PSIsContainer) {
    throw 'junction_or_target_changed'
}
[IO.Directory]::Delete($junction,$false)
[Console]::Out.WriteLine('JUNCTION_REMOVED'); [Console]::Out.Flush()
$control=Get-WdMindReadConfig -FleetPath $env:WD_TEST_CONTROL_FLEET -FleetSha256 $env:WD_TEST_CONTROL_DIGEST
if ($null -eq $control) { [Console]::Out.WriteLine('CONTROL:OFF') } else { [Console]::Out.WriteLine('CONTROL:ENABLED') }
[Console]::Out.Flush()
'''
    process = subprocess.Popen([str(PS5), "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                "-Command", script], cwd=root, env=env, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
    output: queue.Queue[str] = queue.Queue()
    def collect():
        for line in process.stdout:
            output.put(line.strip())
    thread = threading.Thread(target=collect, daemon=True)
    thread.start()
    try:
        assert output.get(timeout=20) == "JUNCTION_READY"
        _check_owned_link(root, target, junction, sentinel)
        process.stdin.write("RUN_AFTER_OWNERSHIP_CHECK\n")
        process.stdin.flush()
        assert output.get(timeout=20) == "RESULT:REFUSED:mind_config_path_refused"
        assert output.get(timeout=20) == "READY_TO_REMOVE"
        # Resolve and inspect this exact owned link/target again immediately
        # before authorizing non-recursive removal of the link only.
        _check_owned_link(root, target, junction, sentinel)
        process.stdin.write("REMOVE_AFTER_OWNERSHIP_CHECK\n")
        process.stdin.flush()
        assert output.get(timeout=20) == "JUNCTION_REMOVED"
        assert output.get(timeout=20) == "CONTROL:ENABLED"
        assert process.wait(timeout=20) == 0
        assert not os.path.lexists(junction)
        _no_reparse_ancestors(target)
        info = os.lstat(sentinel)
        assert target.is_dir() and stat.S_ISREG(info.st_mode) and not getattr(info, "st_file_attributes", 0) & REPARSE
        assert sentinel.read_bytes() == b"synthetic sentinel survives"
        assert fleet.read_bytes() == raw
        assert control_fleet.read_bytes() == control_raw
    finally:
        if process.poll() is None:
            process.kill()  # only the exact Popen child owned by this test
            process.wait(timeout=10)
        thread.join(timeout=10)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()
