"""Runtime boundary regressions: exact external sessions and cold Tools holds."""
import json
from pathlib import Path

import pytest

from test_wd_reboot_bundle import REBOOT, LANE_TEST_SHELLS, _run_powershell


def q(value):
    return "'" + str(value).replace("'", "''") + "'"


def load(path, name):
    return f"""
if($PSVersionTable.PSEdition -eq 'Desktop'){{$env:PSModulePath=Join-Path $PSHOME 'Modules'}}
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile({q(path)},[ref]$tokens,[ref]$errors)
if($errors.Count){{throw 'parse error'}}
$fn=$ast.Find({{param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq {q(name)}}},$true)
. ([scriptblock]::Create($fn.Extent.Text))
"""


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda value: Path(value).stem)
def test_external_identity_never_adopts_same_lane_or_reused_pid(ps):
    script = load(REBOOT / "start-wd-agent.ps1", "Assert-WdLaneLaunchAvailable") + r"""
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$lane=[pscustomobject]@{agent='codex-lead-1';legacy_process_markers=@()}
$native=[pscustomobject]@{ProcessId=201;ParentProcessId=200;Name='codex.exe';
 CommandLine='C:\test\codex.exe';ExecutablePath='C:\test\codex.exe';CreationDate=[datetime]'2026-09-15T00:00:02Z'}
$approval=[pscustomobject]@{pid=201;name='codex.exe';command_line=$native.CommandLine;
 executable_path=$native.ExecutablePath;process_start_utc='2026-09-15T00:00:02Z'}
$global:rows=@($native)
function Get-CimInstance { $global:rows }
function Check($entries) {
 try { Assert-WdLaneLaunchAvailable -Lane $lane -ExternalSessions $entries -CurrentPid 100; $true }
 catch { $false }
}
$exact=Check @($approval)
$absent=Check @()
$duplicate=Check @($approval,$approval)
$native.CreationDate=$native.CreationDate.AddSeconds(1)
$reused=Check @($approval)
$native.CreationDate=$native.CreationDate.AddSeconds(-1)
$native.CommandLine='C:\test\codex.exe --other'
$command=Check @($approval)
$native.CommandLine=$approval.command_line
$native.ExecutablePath='C:\other\codex.exe'
$path=Check @($approval)
$native.ExecutablePath=$approval.executable_path
$global:rows=@($native,[pscustomobject]@{ProcessId=200;ParentProcessId=1;Name='powershell.exe';
 CommandLine='powershell -File C:\Python\start-wd-agent.ps1 -Agent codex-lead-1';CreationDate=[datetime]'2026-09-15T00:00:00Z'})
$sameLane=Check @($approval)
@{exact=$exact;absent=$absent;duplicate=$duplicate;reused=$reused;command=$command;path=$path;same_lane=$sameLane}|ConvertTo-Json
"""
    assert json.loads(_run_powershell(script, executable=ps).stdout) == {
        "exact": True, "absent": False, "duplicate": False, "reused": False,
        "command": False, "path": False, "same_lane": False,
    }


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda value: Path(value).stem)
@pytest.mark.parametrize("case", ["clean", "blocked", "orphan_pending", "live"])
def test_tools_cold_start_preserves_pending_evidence(tmp_path, ps, case):
    runtime = tmp_path / "bridge"
    worktree = tmp_path / "tools"
    journal = worktree / ".codex-audit" / "wd-turn-loop"
    runtime.mkdir()
    journal.mkdir(parents=True)
    pending = journal / ("turn-" + "a" * 32 + ".pending")
    if case != "clean":
        pending.write_text("original evidence", encoding="utf-8")
    pointer = runtime / ".wd-turn-codex-tools-1.owner.json"
    if case in ("blocked", "live"):
        pointer.write_text(json.dumps({
            "schema": "wd.lane-turn-owner.v1", "agent": "codex-tools-1",
            "pid": 87654321, "process_start_utc": "2026-09-15T00:00:00Z",
            "status": "blocked", "worktree": str(worktree), "journal_root": str(journal),
            "pending_path": str(pending), "session_id": "old", "generation": "a" * 40,
        }), encoding="utf-8")
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    script = load(REBOOT / "start-wd-tools-consumer.ps1", "Assert-WdToolsColdStart")
    script += "$ErrorActionPreference='Stop'; Set-StrictMode -Version Latest\n"
    if case == "blocked":
        # Dotfiles are hidden on Linux; exercise the same file attribute on Windows.
        script += f"[IO.File]::SetAttributes({q(pointer)}, [IO.FileAttributes]::Hidden)\n"
    if case == "live":
        script += "function Get-Process { [pscustomobject]@{ProcessName='powershell';StartTime=[datetime]'2026-09-15T00:00:00Z'} }\n"
    script += fr"""
try {{
 Assert-WdToolsColdStart -BridgeRoot {q(runtime)} -LaneRoot {q(worktree)} -TurnLoopCode ([IO.File]::ReadAllText({q(REBOOT / 'Invoke-WdLaneTurnLoop.ps1')}))
 @{{ok=$true}}|ConvertTo-Json
}} catch {{ @{{ok=$false;error=$_.Exception.Message}}|ConvertTo-Json }}
"""
    result = json.loads(_run_powershell(script, executable=ps).stdout)
    assert result["ok"] is (case in ("clean", "live")), result
    if case == "blocked":
        assert "blocked_previous_unresolved_turn" in result["error"]
    assert {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda value: Path(value).stem)
def test_external_snapshot_hash_expiry_and_duplicates(tmp_path, ps):
    path = tmp_path / "external.json"
    script = "".join(load(REBOOT / "start-wd-agent.ps1", name) for name in (
        "Assert-LanePathWithoutReparse", "Read-Utf8LaneSnapshot", "Read-WdExternalSessions"))
    script += fr"""
$ErrorActionPreference='Stop'; Set-StrictMode -Version Latest
$path={q(path)}
$entry=@{{pid=201;name='codex.exe';command_line='codex.exe';executable_path={q(tmp_path / 'codex.exe')};process_start_utc='2026-09-15T00:00:00Z'}}
$record=@{{schema='wd.external-agent-sessions.v1';expires_at_utc=[DateTimeOffset]::UtcNow.AddHours(1).ToString('o');processes=@($entry)}}
function Save {{ [IO.File]::WriteAllText($path,($record|ConvertTo-Json -Depth 4)); (Get-FileHash -LiteralPath $path).Hash }}
function Check($hash) {{ try {{ $r=@(Read-WdExternalSessions -Path $path -ExpectedHash $hash); $r.Count -eq 1 }} catch {{ $false }} }}
$hash=Save; $valid=Check $hash; $badHash=Check ('F'*64)
$record.expires_at_utc=[DateTimeOffset]::UtcNow.AddSeconds(-1).ToString('o'); $expired=Check (Save)
$record.expires_at_utc=[DateTimeOffset]::UtcNow.AddHours(1).ToString('o'); $record.processes=@($entry,$entry); $duplicate=Check (Save)
@{{valid=$valid;bad_hash=$badHash;expired=$expired;duplicate=$duplicate}}|ConvertTo-Json
"""
    assert json.loads(_run_powershell(script, executable=ps).stdout) == {
        "valid": True, "bad_hash": False, "expired": False, "duplicate": False,
    }
