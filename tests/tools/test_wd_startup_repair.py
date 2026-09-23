"""Exact operator-approved external runners must not block a different lane."""
import json
from pathlib import Path

import pytest

from test_wd_reboot_bundle import LANE_TEST_SHELLS, REBOOT, _run_powershell
from test_wd_startup_recovery import load


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('case', ['valid', 'absent', 'wrong_command', 'wrong_exe', 'missing_start', 'duplicate'])
def test_exact_runner_discovery(ps, case):
    script = load(REBOOT / 'start-wd-agent.ps1', 'Get-WdApprovedExternalRunners') + r'''
$ErrorActionPreference='Stop'; Set-StrictMode -Version Latest
$policy=[pscustomobject]@{name='pythonw.exe';executable_path='C:\Python\pythonw.exe';command_line='pythonw.exe C:\external\worker.py --project exact';native_child_name='codex.exe';native_child_executable_path='C:\cli\codex.exe';native_child_command_prefix='C:\cli\codex.exe -C C:\external --sandbox read-only exec '}
$parent=[pscustomobject]@{Name=$policy.name;ExecutablePath=$policy.executable_path;CommandLine=$policy.command_line;ProcessId=31;CreationDate=[DateTime]'2026-09-23T20:00:00Z'}
$rows=@($parent)
'''
    script += {
        'valid': '', 'absent': '$rows=@()',
        'wrong_command': "$parent.CommandLine+=' --other'",
        'wrong_exe': "$parent.ExecutablePath='C:\\wrong.exe'",
        'missing_start': '$parent.CreationDate=$null',
        'duplicate': '$rows=@($parent,$parent)',
    }[case]
    script += r'''
try { $records=@(Get-WdApprovedExternalRunners -Policies @($policy) -Processes $rows); @{denied=$false;count=$records.Count;records=$records}|ConvertTo-Json -Depth 8 -Compress }
catch { @{denied=$true;error=$_.Exception.Message}|ConvertTo-Json -Compress }
'''
    result = json.loads(_run_powershell(script, executable=ps).stdout)
    if case in ('missing_start', 'duplicate'):
        assert result['denied']
    else:
        assert not result['denied']
        assert result['count'] == (1 if case == 'valid' else 0)
        if case == 'valid':
            assert result['records'][0]['kind'] == 'native_parent'
            assert result['records'][0]['pid'] == 31
            assert result['records'][0]['process_start_utc'].startswith('2026-09-23T20:00:00')


def test_manifest_policies_are_wired_into_normal_lane_path():
    source = (REBOOT / 'start-wd-agent.ps1').read_text(encoding='utf-8-sig')
    assert '$manifest.external_native_runners' in source
    assert '$externalSessions += @(Get-WdApprovedExternalRunners' in source
    policies = json.loads((REBOOT / 'wd-fleet.json').read_text(encoding='utf-8'))['external_native_runners']
    assert len(policies) == 1
    assert 'continuous_runner.py run --root S:\\kovaa_migration ' in policies[0]['command_line']
    assert policies[0]['native_child_command_prefix'].endswith(' --search exec --json ')


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_discovered_runner_unblocks_only_exact_child_and_lifetime(ps):
    script = load(REBOOT / 'start-wd-agent.ps1', 'Get-WdApprovedExternalRunners')
    script += load(REBOOT / 'start-wd-agent.ps1', 'Assert-WdLaneLaunchAvailable') + r'''
$ErrorActionPreference='Stop'; Set-StrictMode -Version Latest
$lane=[pscustomobject]@{agent='codex-lead-1';legacy_process_markers=@()}
$policy=[pscustomobject]@{name='python.exe';executable_path='C:\external\python.exe';command_line='C:\external\python.exe worker.py';native_child_name='codex.exe';native_child_executable_path='C:\cli\codex.exe';native_child_command_prefix='C:\cli\codex.exe -s read-only -C C:\external exec '}
$parent=[pscustomobject]@{ProcessId=200;ParentProcessId=1;Name=$policy.name;ExecutablePath=$policy.executable_path;CommandLine=$policy.command_line;CreationDate=[datetime]'2026-09-18T10:00:00Z'}
$native=[pscustomobject]@{ProcessId=201;ParentProcessId=200;Name='codex.exe';ExecutablePath=$policy.native_child_executable_path;CommandLine=($policy.native_child_command_prefix+'task');CreationDate=[datetime]'2026-09-18T10:01:00Z'}
$global:rows=@($parent,$native)
function Get-CimInstance {$global:rows}
function Test-Launch($entries) {
 try {Assert-WdLaneLaunchAvailable -Lane $lane -ExternalSessions $entries -CurrentPid 100; return $true}
 catch {return $false}
}
$before=Test-Launch @()
$entries=@(Get-WdApprovedExternalRunners -Policies @($policy) -Processes $global:rows)
$after=Test-Launch $entries
$native.CommandLine='C:\cli\codex.exe -s workspace-write -C C:\other exec task'
$wrongChild=Test-Launch $entries
$native.CommandLine=$policy.native_child_command_prefix+'task'
$parent.CreationDate=$parent.CreationDate.AddSeconds(1)
$reused=Test-Launch $entries
@{before=$before;after=$after;wrong_child=$wrongChild;reused=$reused}|ConvertTo-Json -Compress
'''
    assert json.loads(_run_powershell(script, executable=ps).stdout) == {
        'before': False, 'after': True, 'wrong_child': False, 'reused': False}


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_cli_busy_is_provider_specific_and_skip_is_not_deferral(ps):
    script = load(REBOOT / 'start-wd-all.ps1', 'Test-WdCliUpdateDeferred')
    script += load(REBOOT / 'start-wd-all.ps1', 'Get-WdCliUpdateStatus') + r'''
$ErrorActionPreference='Stop'
function Get-CimInstance { param($ClassName,$ErrorAction) @([pscustomobject]@{Name='codex.exe'}) }
@{codex=(Get-WdCliUpdateStatus -Provider codex);claude=(Get-WdCliUpdateStatus -Provider claude);skip=(Get-WdCliUpdateStatus -Provider codex -Skip)}|ConvertTo-Json -Compress
'''
    assert json.loads(_run_powershell(script, executable=ps).stdout) == {
        'codex': 'deferred_live_sessions', 'claude': 'pending', 'skip': 'operator_skipped'}


def test_monitor_opens_before_worker_launch_and_is_not_success_only():
    source = (REBOOT / 'start-wd-all.ps1').read_text(encoding='utf-8-sig')
    call = '$conversationProcess = Start-WdBridgeConversationWindow'
    assert source.count(call) == 1
    assert source.index('if ($DryRun) {\n  Assert-WdBridgeSafetyBaseline') < source.index(call)
    assert source.index(call) < source.index("Write-Host 'Applying scheduled-task console containment")


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('case', ['idle', 'codex_busy', 'claude_busy', 'skip', 'failure'])
def test_update_phase_calls_only_idle_provider_and_fails_closed(ps, case):
    source = (REBOOT / 'start-wd-all.ps1').read_text(encoding='utf-8-sig')
    phase = source[source.index('  # Preflight can take minutes.'):source.index('  $codexAfterPath =')]
    script = load(REBOOT / 'start-wd-all.ps1', 'Test-WdCliUpdateDeferred')
    script += load(REBOOT / 'start-wd-all.ps1', 'Get-WdCliUpdateStatus')
    script += f"$case='{case}'\n" + r'''
$ErrorActionPreference='Stop'; Set-StrictMode -Version Latest
$SkipCliUpdate=($case -eq 'skip')
$codexUpdatePath='C:\cli\codex.cmd'; $claudeUpdatePath='C:\cli\claude.cmd'
$codexUpdateHash='same'; $claudeUpdateHash='same'
$codexUpdateStatus='pending'; $claudeUpdateStatus='pending'
$script:calls=[Collections.Generic.List[string]]::new()
function Get-CimInstance { param($ClassName,$ErrorAction)
 if($case -eq 'codex_busy'){[pscustomobject]@{Name='codex.exe'}}
 if($case -eq 'claude_busy'){[pscustomobject]@{Name='claude.exe'}}
}
function Resolve-WdNpmUpdateShim { param($Name) "C:\cli\$Name" }
function Get-FileHash { param($LiteralPath,$Algorithm) [pscustomobject]@{Hash='same'} }
function Invoke-CheckedNative { param($Path,$Arguments,$Label)
 if(($Arguments -join ' ') -cne 'update'){throw 'unexpected arguments'}
 $script:calls.Add($Label)
 if($case -eq 'failure'){throw 'simulated update failure'}
}
function Write-Host { param($Object,$ForegroundColor) }
function Write-Warning { param($Message) }
$failed=$false
try {
''' + phase + r'''
} catch {$failed=$true}
@{calls=@($script:calls.ToArray());failed=$failed;codex=$codexUpdateStatus;claude=$claudeUpdateStatus}|ConvertTo-Json -Compress
'''
    result = json.loads(_run_powershell(script, executable=ps).stdout)
    expected = {
        'idle': (['codex update', 'claude update'], 'updated', 'updated'),
        'codex_busy': (['claude update'], 'deferred_live_sessions', 'updated'),
        'claude_busy': (['codex update'], 'updated', 'deferred_live_sessions'),
        'skip': ([], 'operator_skipped', 'operator_skipped'),
        'failure': (['codex update'], 'failed', 'pending'),
    }[case]
    assert result == dict(calls=expected[0], codex=expected[1], claude=expected[2], failed=case == 'failure')
