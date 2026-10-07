"""Continuity checkpoint and ledger timestamps under a loader without -DateKind (RCO2 78aaf7b0 C2).

pwsh before 7.5 has no ConvertFrom-Json -DateKind: an ISO timestamp becomes a Local DateTime, and [string] of it is
the invariant MM/dd/yyyy text, which a host-culture Parse reads day/month swapped. This fixture SIMULATES that loader
with a ConvertFrom-Json proxy (no -DateKind; ISO strings become Local DateTime) under fi-FI. It does not claim to run
an old host. The current loader (with -DateKind) is the native twin. Queue and guard are test doubles.
"""
import hashlib
import json
from pathlib import Path

import pytest

from test_wd_native_tools_wake import THREAD, TOOLS
from test_wd_reboot_bundle import REBOOT, LANE_TEST_SHELLS, _run_powershell
from test_wd_startup_recovery import load, q

OLD_LOADER = r"""
$script:RealConvertFromJson = Get-Command ConvertFrom-Json -CommandType Cmdlet
function ConvertFrom-Json {
    [CmdletBinding()] param([Parameter(ValueFromPipeline)] $InputObject)
    process {
        $value = $InputObject | & $script:RealConvertFromJson
        $convert = {
            param($Node)
            foreach ($property in @($Node.PSObject.Properties)) {
                if ($property.Value -is [string] -and $property.Value -cmatch '^[0-9]{4}-[0-9]{2}-[0-9]{2}T') {
                    $property.Value = [datetime]::Parse($property.Value, [Globalization.CultureInfo]::InvariantCulture)
                } elseif ($property.Value -is [Management.Automation.PSCustomObject]) { & $convert $property.Value }
                elseif ($property.Value -is [Array]) { foreach ($item in $property.Value) {
                    if ($item -is [Management.Automation.PSCustomObject]) { & $convert $item } } }
            }
        }
        & $convert $value
        $value
    }
}
"""


def _steps(tmp_path, ps, old_loader, checkpoint_at, session_at, nows):
    journal = tmp_path / ".codex-audit/wd-turn-loop"
    journal.mkdir(parents=True)
    cli = tmp_path / "queue-cli.bin"
    cli.write_bytes(b"non-executable test double")
    cli_hash = hashlib.sha256(cli.read_bytes()).hexdigest().upper()
    script = "$ErrorActionPreference='Stop'\nSet-StrictMode -Version Latest\n"
    script += "[Threading.Thread]::CurrentThread.CurrentCulture=[Globalization.CultureInfo]::GetCultureInfo('fi-FI')\n"
    for name in ("Assert-WdTurnPath", "Write-WdTurnJson"):
        script += load(REBOOT / "Invoke-WdLaneTurnLoop.ps1", name)
    script += load(TOOLS, "Invoke-WdNativeContinuityStep")
    if old_loader:
        script += OLD_LOADER
    script += f"""
$script:calls=0
function Test-WdContinuityControlEvents {{ return $false }}
function Start-Process {{ throw 'Native process forbidden by fixture' }}
function Invoke-WdContinuityDecision {{
    param($Snapshot,$NowUtc)
    return [pscustomobject]@{{schema='wd.continuity-decision.v1';agent='codex-lead-1';authority='none';
        verdict='dispatch';target='codex-lead-1';action_key=('a'*64);reasons=@('fixture')}}
}}
function Send-WdNativeToolsQueueMessage {{ param($CliPath,$ThreadId,$Message,$Worktree) $script:calls++; return 'fake-queue-id' }}
$checkpoint=[ordered]@{{schema='wd.lane-current.v1';agent='codex-lead-1';worktree={q(tmp_path)};status='in_progress';
    task_id='authorized-task';next_action='continue scoped work';next_wakeup_utc=$null;blockers=@();
    updated_at_utc='{checkpoint_at}'}}
[IO.File]::WriteAllText({q(journal.parent / 'wd-current-state.json')}, ($checkpoint | ConvertTo-Json -Compress))
$observations=@()
foreach ($now in @({', '.join(repr(n) for n in nows)})) {{
    $parameters=@{{CliPath={q(cli)};ThreadId='{THREAD}';Worktree={q(tmp_path)};Generation='fake-pinned';
        Agent='codex-lead-1';ExpectedCliHash='{cli_hash}';SessionStartedAt=[DateTimeOffset]'{session_at}';
        Now=[DateTimeOffset]$now}}
    try {{ $observations+=@('OK:' + (Invoke-WdNativeContinuityStep @parameters)) }}
    catch {{ $observations+=@('ERR:' + $_.Exception.Message) }}
}}
[ordered]@{{calls=$script:calls;observations=@($observations)}} | ConvertTo-Json -Compress
"""
    return json.loads(_run_powershell(script, executable=ps).stdout)


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize("old_loader", [True, False], ids=["no_datekind_loader", "native_loader"])
def test_a_fresh_checkpoint_is_not_read_as_predating_the_session(tmp_path, ps, old_loader):
    # 1 Oct read day/month swapped is 10 Jan: before the fix the fresh checkpoint "predated" the session.
    report = _steps(tmp_path, ps, old_loader, "2026-10-01T05:00:00+00:00", "2026-10-01T04:00:00Z",
                    ["2026-10-01T09:00:00Z"])
    assert report == {"calls": 1, "observations": ["OK:queued"]}, report


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize("old_loader", [True, False], ids=["no_datekind_loader", "native_loader"])
def test_a_predating_checkpoint_never_reads_as_fresh(tmp_path, ps, old_loader):
    # 12 Mar read swapped is 3 Dec: before the fix a checkpoint OLDER than the session passed the floor (fail-open).
    report = _steps(tmp_path, ps, old_loader, "2026-03-12T05:00:00+00:00", "2026-03-12T06:00:00Z",
                    ["2026-03-12T09:00:00Z"])
    assert report["calls"] == 0, report
    assert report["observations"][0].startswith("ERR:Continuity checkpoint predates this native session"), report


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize("old_loader", [True, False], ids=["no_datekind_loader", "native_loader"])
def test_the_ledger_rate_limit_reads_its_own_timestamp_exactly(tmp_path, ps, old_loader):
    # Second step 30 min later must be rate_limited (< 3600 s), never "stalled" from a misread entry time.
    report = _steps(tmp_path, ps, old_loader, "2026-10-01T05:00:00+00:00", "2026-10-01T04:00:00Z",
                    ["2026-10-01T09:00:00Z", "2026-10-01T09:30:00Z"])
    assert report == {"calls": 1, "observations": ["OK:queued", "OK:rate_limited"]}, report
