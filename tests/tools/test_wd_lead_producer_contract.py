"""Exact four-worker bootstrap contract; native Lead is a separate producer."""
import json
import os
from pathlib import Path
import pytest
from test_wd_reboot_bundle import REBOOT, LANE_TEST_SHELLS, _run_powershell
from test_wd_startup_recovery import load, q
from test_wd_bridge_limited_bootstrap import observation


def test_shell_matrix_deduplicates_alias_paths():
    """MUTANT_TWO_PS5_ALIASES_AS_TWO_ENGINES: distinct spelling is not PS7 coverage."""
    from test_wd_reboot_bundle import _unique_shell_paths
    paths = [r'C:\WINDOWS\system32\WindowsPowerShell\v1.0\powershell.EXE',
             r'C:\WINDOWS\System32\WindowsPowerShell\v1.0\powershell.exe']
    assert len(_unique_shell_paths(paths)) == (1 if os.name == 'nt' else 2)


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('case', ['four', 'legacy_five', 'duplicate', 'wrong_identity'])
def test_limited_bootstrap_exact_four_supervised_workers(ps, case):
    """MUTANT_BOOTSTRAP_STILL_REQUIRES_FIVE / MUTANT_BOOTSTRAP_COUNT_ONLY."""
    agents = ['codex-tools-1', 'claude-rco-1', 'claude-rco-2', 'fable-5']
    if case == 'legacy_five': agents.append('codex-lead-1')
    if case == 'duplicate': agents[3] = agents[0]
    if case == 'wrong_identity': agents[3] = 'codex-lead-1'
    values = '@(' + ','.join(repr(a) for a in agents) + ')'
    script = "$ErrorActionPreference='Stop'\n" + load(REBOOT / 'WdBridgeLimitedBootstrap.ps1', 'Get-WdBridgeLimitedBootstrapDecision')
    script += f"$o={observation(WatcherAgents=values)}\nGet-WdBridgeLimitedBootstrapDecision @o|ConvertTo-Json -Depth 8"
    result = json.loads(_run_powershell(script, executable=ps).stdout)
    assert result['action'] == ('launch' if case == 'four' else 'refuse'), result


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('apply,conflict', [(False, False), (True, False), (True, True)])
def test_retired_lead_producer_is_stopped_without_relaunch(ps, apply, conflict):
    """MUTANT_LEAD_LEGACY_WATCHER_RELAUNCHED / MUTANT_RETIRE_BEFORE_FLEET_CLEAR."""
    script = "$ErrorActionPreference='Stop'\n" + load(REBOOT / 'wd_supervisor.ps1', 'Invoke-WdWatcherFleetPlan')
    script += f"""
$script:steps=@()
$plan=[pscustomobject]@{{agent='codex-lead-1';action='retire';process=[pscustomobject]@{{ProcessId=123}}}}
$planArguments=@{{Plans=@($plan);Apply=${str(apply).lower()};ConflictMessages=@({repr('opaque peer') if conflict else ''});
 PrepareAction={{param($plans) $script:steps+='prepare'}};
 ReportAction={{param($plan) $script:steps+='report'}};
 StopAction={{param($plan) $script:steps+='stop'}};
 LaunchAction={{param($plan) throw 'Retired Lead must never be relaunched'}}}}
$ok=Invoke-WdWatcherFleetPlan @planArguments
@{{ok=$ok;steps=$script:steps}}|ConvertTo-Json -Depth 5
"""
    result = json.loads(_run_powershell(script, executable=ps).stdout)
    assert result['ok'] is (not conflict)
    assert result['steps'] == ([] if conflict else ['prepare', 'stop'] if apply else ['report'])


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('all_count,exact,stale,marker,expected', [
    (0,0,0,False,'absent'), (1,1,0,False,'retire'), (1,0,1,False,'retire'),
    (2,1,0,False,'conflict'), (1,0,0,False,'conflict'), (1,1,1,False,'conflict'),
    (0,0,0,True,'conflict'), (1,1,0,True,'conflict'), (0,1,0,False,'conflict')])
def test_retirement_requires_one_verified_external_producer(ps, all_count, exact, stale, marker, expected):
    """MUTANT_RETIRE_OPAQUE_PROCESS / MUTANT_RETIRE_PERSISTENT_CONFLICT."""
    script = "$ErrorActionPreference='Stop'\n" + load(REBOOT / 'wd_supervisor.ps1', 'Get-WdRetiredLeadWatcherDisposition')
    script += f"Get-WdRetiredLeadWatcherDisposition -AllCount {all_count} -ExactCount {exact} -ReplaceableCount {stale} -ConflictMarkerExists ${str(marker).lower()}"
    assert _run_powershell(script, executable=ps).stdout.strip() == expected


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('notice_fails,warn', [(False,'Continue'), (True,'Continue'), (True,'Stop')])
def test_lead_notice_failure_preserves_original_refusal(tmp_path, ps, notice_fails, warn):
    """MUTANT_LEAD_NOTICE_MASKS_REFUSAL: notice failure cannot replace queue evidence."""
    script = f"""
$ErrorActionPreference='Stop';$WarningPreference='{warn}'
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile({q(REBOOT / 'start-wd-agent.ps1')},[ref]$tokens,[ref]$errors)
$terminal=$ast.Find({{param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq 'Invoke-WdNativeLeadTerminal'}},$true)
$relayTry=$terminal.Find({{param($n) $n -is [Management.Automation.Language.TryStatementAst] -and $n.Body.Extent.Text.Contains('Invoke-WdNativeToolsWakeRelay -Agent codex-lead-1')}},$true)
$script:notices=@();$script:writes=@()
function Write-WdTurnJson {{param($Path,$Value) $script:writes+=,($Value|ConvertTo-Json -Compress)}}
function ConvertTo-WdToolsNativeArgument {{param($Value) return $Value}}
function Start-WdToolsNativeProcess {{param($CliPath,$ArgumentLine,$Worktree) return $script:privateNative}}
function Invoke-WdNativeToolsWakeRelay {{ throw [InvalidOperationException]::new('private producer unavailable') }}
function Invoke-WdContinuityOperatorNotice {{
 param($Agent,$ThreadId,$Worktree,$ErrorText,$RuntimeRoot,$SessionId)
 $script:notices+=,[ordered]@{{agent=$Agent;thread=$ThreadId;error=$ErrorText;session=$SessionId}}
 if(${str(notice_fails).lower()}){{throw 'private notice unavailable'}}
 return [pscustomobject]@{{status='published'}}
}}
$script:privateNative=[pscustomobject]@{{Id=123;StartTime=[DateTime]::UtcNow;HasExited=$true;ExitCode=0}}
$script:privateNative|Add-Member ScriptMethod Dispose {{}}
$record=[ordered]@{{status='starting';native_pid=0;native_process_start_utc='';bridge_wake_transport='withheld';error=''}}
$native=$null;$readyPath={q(tmp_path / 'ready.json')};$Arguments=@();$CliPath='unused'
$ThreadId='00000000-0000-4000-8000-000000000001';$Worktree={q(tmp_path)};$RuntimeRoot={q(tmp_path)}
$Generation='fixture';$ExpectedCliHash=('A'*64);$SessionId='fixture-session';$caught=$null
try {{. ([scriptblock]::Create($relayTry.Extent.Text))}} catch {{$caught=$_}}
@{{error=$caught.Exception.Message;error_type=$caught.Exception.GetType().FullName;notices=$script:notices;record=$record}}|ConvertTo-Json -Depth 8 -Compress
"""
    result = json.loads(_run_powershell(script, executable=ps).stdout.strip().splitlines()[-1])
    assert (result['error'], result['error_type']) == ('private producer unavailable', 'System.InvalidOperationException')
    assert result['notices'] == [dict(agent='codex-lead-1', thread='00000000-0000-4000-8000-000000000001',
        error='native_wake_relay_blocked: private producer unavailable', session='fixture-session')]
    assert result['record']['status'] == 'bridge_wake_blocked'
    assert result['record']['error'] == 'private producer unavailable'
    assert result['record']['operator_notice_status'] == ('unavailable' if notice_fails else 'published')
