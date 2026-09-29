"""The supervisor Git resolver names exactly one application (source rehearsal, 2026-09-29).

A Git Bash parent puts mingw64\\bin ahead of cmd on PATH, so an unconfigured
Get-Command lookup returns two git.exe entries. Joining their sources is not a
path, and source-mode start-wd-all -DryRun stopped with "The given path's format
is not supported" instead of a clear refusal.
"""
import json
from pathlib import Path

import pytest

from test_wd_reboot_bundle import REBOOT, LANE_TEST_SHELLS, _run_powershell
from test_wd_startup_recovery import load, q

SUPERVISOR = REBOOT / "wd_supervisor.ps1"


def fake_git(root: Path) -> Path:
    root.mkdir(parents=True)
    path = root / "git.exe"
    path.write_bytes(b"not a real git")
    return path


def resolve(ps: str, path_dirs: list[Path], configured: str = "") -> dict:
    script = (load(SUPERVISOR, "Assert-WdSupervisorPathWithoutReparse")
              + load(SUPERVISOR, "Resolve-WdSupervisorGitApplication") + f"""
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$env:Path = {q(';'.join(str(d) for d in path_dirs))}
try {{
  $r = Resolve-WdSupervisorGitApplication -ConfiguredPath {q(configured)}
  @{{ ok = $true; value = [string]$r }} | ConvertTo-Json -Compress
}} catch {{
  @{{ ok = $false; value = $_.Exception.Message }} | ConvertTo-Json -Compress
}}
""")
    return json.loads(_run_powershell(script, executable=ps).stdout)


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_two_unconfigured_path_hits_are_refused_as_ambiguous(ps, tmp_path):
    first = fake_git(tmp_path / "mingw64" / "bin")
    fake_git(tmp_path / "cmd")
    result = resolve(ps, [first.parent, tmp_path / "cmd"])
    assert result == {"ok": False,
                      "value": "supervisor Git lookup is ambiguous; configure watchers.git_executable"}


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_one_unconfigured_path_hit_resolves_to_that_application(ps, tmp_path):
    only = fake_git(tmp_path / "cmd")
    assert resolve(ps, [only.parent]) == {"ok": True, "value": str(only)}


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_a_configured_git_wins_even_when_path_is_ambiguous(ps, tmp_path):
    first = fake_git(tmp_path / "mingw64" / "bin")
    configured = fake_git(tmp_path / "cmd")
    result = resolve(ps, [first.parent, configured.parent], configured=str(configured))
    assert result == {"ok": True, "value": str(configured)}


def test_every_generation_lookup_passes_the_configured_git():
    text = SUPERVISOR.read_text(encoding="utf-8-sig")
    calls = [line for line in text.splitlines() if "Resolve-OwnBundleGeneration" in line
             and "function" not in line]
    assert len(calls) == 2
    tools = text.index("$toolsGeneration = Resolve-OwnBundleGeneration")
    assert "-GitExecutable (Get-RequiredText $configuration.watchers 'git_executable')" in text[tools:tools + 240]
    watcher = text.index("$watcherTargetGeneration = Resolve-OwnBundleGeneration")
    assert "-GitExecutable $watcherGitExecutable" in text[watcher:watcher + 200]
