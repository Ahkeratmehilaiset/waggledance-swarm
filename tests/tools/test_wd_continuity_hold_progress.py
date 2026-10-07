"""Isolated continuity progress-key fixtures; queue and guard are test doubles."""
import hashlib
import json
from pathlib import Path

import pytest

from test_wd_native_tools_wake import THREAD, TOOLS
from test_wd_reboot_bundle import REBOOT, LANE_TEST_SHELLS, _run_powershell
from test_wd_startup_recovery import load, q


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize(
    "frames,expected_calls,expected_errors,distinct_keys",
    [
        ([{}, {}], 1, ["", "stalled after delivered"], 1),
        ([{}, {"work_held": False, "release_held": False}], 2, ["", ""], 2),
        ([{"work_held": False, "release_held": False},
          {"work_held": False, "release_held": True}], 2, ["", ""], 2),
        ([{"work_held": True, "release_held": True},
          {"work_held": False, "release_held": True}], 1, ["work held", ""], 1),
        # Same-pair ABA cannot be recognised without a trusted resume revision.
        ([{"work_held": False, "release_held": True},
          {"work_held": True, "release_held": True},
          {"work_held": False, "release_held": True}],
         1, ["", "work held", "stalled after delivered"], 1),
        ([{"work_held": False}], 0, ["paired exact booleans"], 0),
        ([{"release_held": True}], 0, ["paired exact booleans"], 0),
        ([{"work_held": "false", "release_held": True}],
         0, ["paired exact booleans"], 0),
        ([{"work_held": False, "release_held": None}],
         0, ["paired exact booleans"], 0),
    ],
)
def test_hold_only_progress_and_bounded_resume(
    tmp_path, ps, frames, expected_calls, expected_errors, distinct_keys
):
    journal = tmp_path / ".codex-audit/wd-turn-loop"
    journal.mkdir(parents=True)
    cli = tmp_path / "queue-cli.bin"
    cli.write_bytes(b"non-executable test double")
    cli_hash = hashlib.sha256(cli.read_bytes()).hexdigest().upper()
    script = "$ErrorActionPreference='Stop'\nSet-StrictMode -Version Latest\n"
    for name in ("Assert-WdTurnPath", "Write-WdTurnJson"):
        script += load(REBOOT / "Invoke-WdLaneTurnLoop.ps1", name)
    script += load(TOOLS, "Invoke-WdNativeContinuityStep")
    script += f"""
$script:calls=0
function Test-WdContinuityControlEvents {{ return $false }}
function Start-Process {{ throw 'Native process forbidden by fixture' }}
function Invoke-WdContinuityDecision {{
    param($Snapshot,$NowUtc)
    # Hold/pair malformed validation is intentionally not provided by this fake:
    # malformed dispatch input must also fail at the progress-key boundary.
    $held=$Snapshot.checkpoint['work_held']
    $verdict=if ($held -is [bool] -and $held) {{'hold'}} else {{'dispatch'}}
    return [pscustomobject]@{{schema='wd.continuity-decision.v1';
        agent='codex-lead-1';authority='none';verdict=$verdict;
        target='codex-lead-1';action_key=('a'*64);reasons=@('fixture')}}
}}
function Send-WdNativeToolsQueueMessage {{
    param($CliPath,$ThreadId,$Message,$Worktree)
    if ($Message -notmatch 'not a new assignment or permission') {{throw 'unsafe wake'}}
    $script:calls++
    return 'fake-queue-id'
}}
$frames=ConvertFrom-Json -InputObject {q(json.dumps(frames))}
$observations=@()
$index=0
foreach ($frame in @($frames)) {{
    $checkpoint=[ordered]@{{schema='wd.lane-current.v1';agent='codex-lead-1';
        worktree={q(tmp_path)};status='in_progress';task_id='authorized-task';
        next_action='continue scoped work';next_wakeup_utc=$null;blockers=@();
        updated_at_utc=([DateTimeOffset]'2026-09-28T20:00:00Z').AddMinutes($index).ToString('o')}}
    foreach ($property in $frame.PSObject.Properties) {{
        $checkpoint[$property.Name]=$property.Value
    }}
    [IO.File]::WriteAllText({q(journal.parent / 'wd-current-state.json')},
        ($checkpoint | ConvertTo-Json -Compress))
    $parameters=@{{CliPath={q(cli)};ThreadId='{THREAD}';Worktree={q(tmp_path)};
        Generation='fake-pinned';Agent='codex-lead-1';ExpectedCliHash='{cli_hash}';
        SessionStartedAt='2026-09-28T00:00:00Z';
        Now=([DateTimeOffset]'2026-09-29T05:00:00Z').AddHours($index)}}
    $failure=''
    try {{ $outcome=Invoke-WdNativeContinuityStep @parameters }}
    catch {{ $outcome=$null; $failure=$_.Exception.Message }}
    $observations+=@([ordered]@{{outcome=$outcome;error=$failure}})
    $index++
}}
$entries=@()
$ledger={q(journal / f'continuity-v1-{THREAD}.json')}
if ([IO.File]::Exists($ledger)) {{
    $entries=@((Get-Content -LiteralPath $ledger -Raw | ConvertFrom-Json).entries)
}}
[ordered]@{{calls=$script:calls;observations=@($observations);entries=@($entries)}} |
    ConvertTo-Json -Depth 12 -Compress
"""
    report = json.loads(_run_powershell(script, executable=ps).stdout)
    assert report["calls"] == expected_calls, report
    assert len(report["observations"]) == len(expected_errors)
    for observed, expected in zip(report["observations"], expected_errors):
        if expected:
            assert expected in observed["error"], report
        else:
            assert observed["error"] == "", report
            assert observed["outcome"] == "queued", report
    keys = [entry["key"] for entry in report["entries"]]
    assert len(set(keys)) == distinct_keys, report
    assert all(entry["status"] == "queued" for entry in report["entries"])
    if not frames[0]:
        legacy = dict(task_id="authorized-task", status="in_progress",
                      next_action="continue scoped work", next_wakeup_utc=None)
        expected_hash = hashlib.sha256(
            json.dumps(legacy, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        assert keys[0] == "codex-lead-1:" + "a" * 64 + ":" + expected_hash
