"""Synthetic opt-in correlation through the fleet/Tools launch boundaries."""
import json
import os
from pathlib import Path

import pytest

from test_wd_reboot_bundle import REBOOT, LANE_TEST_SHELLS, _run_powershell
from test_wd_startup_recovery import load
from test_wd_bridge_limited_bootstrap import run, READY, CONSUMER, NATIVE, THREAD, GEN, observation, APPLY, VERIFY
from test_wd_q4_shared_tools_startup import startup_read_fixture, startup_read_call, STARTUP_READ_FUNCTIONS

FLEET = 'wd-reboot-1234567890abcdef1234567890abcdef'
BOOT = REBOOT / 'WdBridgeLimitedBootstrap.ps1'
TOOLS = REBOOT / 'start-wd-tools-consumer.ps1'
DEPLOY = REBOOT / 'Deploy-WdRebootBundle.ps1'
SUPERVISOR = REBOOT / 'wd_supervisor.ps1'
FLEET_SOURCE = REBOOT / 'start-wd-all.ps1'


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_supervisor_arguments_forward_opt_in_id_and_refuse_invalid(ps):
    script = load(BOOT, 'Get-WdBridgeSupervisorArguments') + f"""
$ErrorActionPreference='Stop'; Set-StrictMode -Version Latest
$off=@(Get-WdBridgeSupervisorArguments -SupervisorScript 'C:\\a.ps1' -ConfigPath 'C:\\x.json' -Apply $true)
$on=@(Get-WdBridgeSupervisorArguments -SupervisorScript 'C:\\a.ps1' -ConfigPath 'C:\\x.json' -Apply $true -FleetStartupRunId '{FLEET}')
$bad=$false; try {{ [void](Get-WdBridgeSupervisorArguments -SupervisorScript 'C:\\a.ps1' -ConfigPath 'C:\\x.json' -Apply $true -FleetStartupRunId '../bad') }} catch {{ $bad=$true }}
@{{off=$off;on=$on;bad=$bad}} | ConvertTo-Json -Compress
"""
    out = json.loads(_run_powershell(script, executable=ps).stdout)
    assert '-FleetStartupRunId' not in out['off']
    i = out['on'].index('-FleetStartupRunId')
    assert out['on'][i+1] == FLEET
    assert out['bad'] is True


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('case', ['matching', 'missing', 'mismatch', 'default_off'])
def test_pinned_tools_readiness_requires_requested_fleet_id(ps, case):
    record = READY
    if case in ('matching', 'mismatch'):
        value = FLEET if case == 'matching' else 'other-fleet'
        record = record[:-1] + ";fleet_startup_run_id='" + value + "'}"
    expected = '' if case == 'default_off' else FLEET
    body = f"""
$procs={{ param($ProcessId) if([int]$ProcessId -eq 41){{ return {CONSUMER} }}; if([int]$ProcessId -eq 42){{ return {NATIVE} }}; return $null }}
$ok=Test-WdBridgeToolsReadinessPinned -Record ({record}) -Generation '{GEN}' -ThreadId '{THREAD}' `
 -ExpectedFleetStartupRunId '{expected}' -GetProcess $procs
@{{ok=$ok}} | ConvertTo-Json -Compress
"""
    assert run(ps, body)['ok'] is (case in ('matching', 'default_off'))


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('case', ['matching', 'missing', 'mismatch'])
def test_existing_tools_requires_exact_fleet_id_without_stopping_it(ps, case):
    record = READY
    if case != 'missing':
        record = record[:-1] + ";fleet_startup_run_id='" + (FLEET if case == 'matching' else 'old-fleet') + "'}"
    body = f"""
$script:calls=New-Object System.Collections.Generic.List[string]
$observe={{ return {observation()} }}
$reconcile={{ param($Apply) $script:calls.Add([string]$Apply); $line=if($Apply){{'{APPLY}'}}else{{'{VERIFY}'}}; return [pscustomobject]@{{exit_code=0;lines=@($line)}} }}
$procs={{ param($ProcessId) if([int]$ProcessId -eq 41){{ return {CONSUMER} }}; if([int]$ProcessId -eq 42){{ return {NATIVE} }}; return $null }}
$result=Invoke-WdBridgeLimitedBootstrap -Observe $observe -RunSupervisorOnce $reconcile -ReadToolsReadiness {{ return {record} }} `
 -GetProcess $procs -Sleep {{ param($Seconds) }} -ToolsWaitSeconds 1 -ExpectedFleetStartupRunId '{FLEET}'
@{{result=$result;calls=@($script:calls)}} | ConvertTo-Json -Depth 6 -Compress
"""
    out = run(ps, body)
    assert out['result']['ok'] is (case == 'matching')
    if case != 'matching':
        assert out['result']['stage'] == 'tools'
        assert 'fleet_startup_unattested' in out['result']['reasons']
        assert out['calls'] == ['True']
    else:
        assert out['result']['tools_binding']['fleet_startup_run_id'] == FLEET
        assert out['calls'] == ['True', 'False']


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_invalid_fleet_id_is_refused_before_any_observation_or_reconcile(ps):
    body = """
$script:calls=New-Object System.Collections.Generic.List[string]
$observe={ $script:calls.Add('observe'); return @{} }
$reconcile={ param($Apply) $script:calls.Add('reconcile'); return $null }
$result=Invoke-WdBridgeLimitedBootstrap -Observe $observe -RunSupervisorOnce $reconcile -ReadToolsReadiness { $null } `
 -GetProcess { param($ProcessId) $null } -Sleep { param($Seconds) } -ToolsWaitSeconds 1 -ExpectedFleetStartupRunId '../invalid'
@{result=$result;calls=@($script:calls)} | ConvertTo-Json -Depth 6 -Compress
"""
    out = run(ps, body)
    assert out['result']['ok'] is False
    assert 'fleet_startup_run_id_refused' in out['result']['reasons']
    assert out['calls'] == []


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_generated_fleet_wrapper_anchors_id_before_uac_and_forwards_only_when_opted_in(ps):
    script = load(DEPLOY, 'New-ForwardingWrapper') + """
$hash='a'*64
$wrapper=New-ForwardingWrapper -Target 'fake-target.ps1' -ExpectedHash $hash -ExpectedManifestHash $hash `
 -WrapperKind 'fleet' -ExpectedFinalCommit ('f'*40) -ExpectedFinalManifestHash $hash
$tokens=$null;$errors=$null
[void][Management.Automation.Language.Parser]::ParseInput($wrapper,[ref]$tokens,[ref]$errors)
@{parsed=($errors.Count -eq 0); text=$wrapper} | ConvertTo-Json -Compress
"""
    out = json.loads(_run_powershell(script, executable=ps).stdout)
    assert out['parsed'] is True
    text = out['text']
    assert text.index("if ($BindFleetStartup)") < text.index('$elevated = Start-Process')
    assert "$targetParameters['RunId'] = $RunId" in text
    assert '[switch] $RequireFreshLanes' in text
    assert "if ($BindFleetStartup -or [bool]$targetParameters['RequireFreshLanes']) { [void]$commandParts.Add('-RequireFreshLanes') }" in text
    assert "'RunId'" in text[text.index('foreach ($name in @('):text.index('$restoreCommand =')]
    assert "-FleetStartupRunId $(if ($BindFleetStartup)" in text


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('kind', ['supervisor', 'tools'])
def test_generated_machine_wrapper_exposes_and_forwards_correlation_id(ps, kind):
    script = load(DEPLOY, 'New-ForwardingWrapper') + f"""
$hash='a'*64
$wrapper=New-ForwardingWrapper -Target 'fake-target.ps1' -ExpectedHash $hash -ExpectedManifestHash $hash `
 -WrapperKind '{kind}' -ExpectedFinalCommit ('f'*40) -ExpectedFinalManifestHash $hash
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseInput($wrapper,[ref]$tokens,[ref]$errors)
if($errors.Count){{throw 'wrapper parse'}}
$params=$ast.ParamBlock.Extent.Text
$target={{ param([string]$FleetStartupRunId) return $FleetStartupRunId }}
$fixture=[scriptblock]::Create($params + "`n& `$target @PSBoundParameters")
$output=& $fixture -FleetStartupRunId '{FLEET}' -Generation ('a'*40)
$bad=$false;try {{ & $fixture -FleetStartupRunId '../invalid' -Generation ('a'*40) -ErrorAction Stop }} catch {{ $bad=$true }}
@{{param=$params;output=[string]$output;invalid_refused=$bad}} | ConvertTo-Json -Compress
"""
    # Supervisor wrapper does not accept the Tools-only mandatory Generation.
    if kind == 'supervisor':
        script = script.replace(" -Generation ('a'*40)", '')
    out = json.loads(_run_powershell(script, executable=ps).stdout)
    assert '$FleetStartupRunId' in out['param']
    assert out['output'] == FLEET
    assert out['invalid_refused'] is True


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('case', ['all_cold', 'one_live', 'two_live', 'missing_lane', 'duplicate_lane'])
def test_fresh_fleet_requirement_never_accepts_mixed_old_and_new_processes(ps, case):
    script = load(FLEET_SOURCE, 'Assert-WdFreshFleetLanes') + f"""
$ErrorActionPreference='Stop'; Set-StrictMode -Version Latest
if (-not (Get-Command Assert-WdFreshFleetLanes -ErrorAction SilentlyContinue)) {{ throw 'function unavailable' }}
$states=@(
 [pscustomobject]@{{lane=[pscustomobject]@{{agent='codex-lead-1'}};live=@()}},
 [pscustomobject]@{{lane=[pscustomobject]@{{agent='fable-5'}};live=@()}},
 [pscustomobject]@{{lane=[pscustomobject]@{{agent='claude-rco-1'}};live=@()}},
 [pscustomobject]@{{lane=[pscustomobject]@{{agent='claude-rco-2'}};live=@()}}
)
if ('{case}' -in @('one_live','two_live')) {{ $states[0].live=@([pscustomobject]@{{ProcessId=99}}) }}
if ('{case}' -eq 'two_live') {{ $states[1].live=@([pscustomobject]@{{ProcessId=98}}) }}
if ('{case}' -eq 'missing_lane') {{ $states=@($states | Select-Object -First 3) }}
if ('{case}' -eq 'duplicate_lane') {{ $states[1].lane.agent=$states[0].lane.agent }}
$ok=$true; try {{ Assert-WdFreshFleetLanes -LaneStates $states }} catch {{ $ok=$false }}
@{{ok=$ok}} | ConvertTo-Json -Compress
"""
    assert json.loads(_run_powershell(script, executable=ps).stdout)['ok'] is (case == 'all_cold')


def test_opt_in_requires_fresh_lanes_and_reports_unknown_authentication():
    deploy = DEPLOY.read_text(encoding='utf-8-sig')
    fleet = FLEET_SOURCE.read_text(encoding='utf-8-sig')
    assert "if ($BindFleetStartup -or [bool]$targetParameters['RequireFreshLanes']) { [void]$commandParts.Add('-RequireFreshLanes') }" in deploy
    assert "correlation_authentication" in deploy and "UNKNOWN" in deploy
    assert "[switch] $RequireFreshLanes" in fleet
    assert "if ($RequireFreshLanes) { Assert-WdFreshFleetLanes -LaneStates $laneStates }" in fleet
    assert "if ($RequireFreshLanes) { Assert-WdFreshToolsState -ToolsState $toolsProcessState }" in fleet
    assert "if ($RequireFreshLanes) { Assert-WdFreshToolsState -ToolsState $lockedToolsState }" in fleet
    assert 'Assert-WdToolsProcessVisibility' in fleet
    assert "atomic_startup_verified" in deploy
    assert "-NotePropertyName tools_cold_visibility -NotePropertyValue 'UNKNOWN'" in deploy
    assert '-NotePropertyName full_fleet_startup_verified -NotePropertyValue $false' in deploy
    assert '-NotePropertyName production_decision_supported -NotePropertyValue $false' in deploy


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('case', ['all_cold', 'current', 'starting', 'stale', 'legacy', 'missing'])
def test_known_existing_tools_process_stops_fresh_fleet_before_launch(ps, case):
    script = load(FLEET_SOURCE, 'Assert-WdFreshToolsState') + f"""
$ErrorActionPreference='Stop'; Set-StrictMode -Version Latest
if (-not (Get-Command Assert-WdFreshToolsState -ErrorAction SilentlyContinue)) {{ throw 'function unavailable' }}
$state=[pscustomobject]@{{current=@();starting=@();stale=@();legacy=@()}}
if ('{case}' -in @('current','starting','stale','legacy')) {{ $state.('{case}')=@([pscustomobject]@{{ProcessId=100}}) }}
if ('{case}' -eq 'missing') {{ $state.PSObject.Properties.Remove('stale') }}
$ok=$true;try {{ Assert-WdFreshToolsState -ToolsState $state }} catch {{ $ok=$false }}
@{{ok=$ok}} | ConvertTo-Json -Compress
"""
    assert json.loads(_run_powershell(script, executable=ps).stdout)['ok'] is (case == 'all_cold')


@pytest.mark.skipif(os.name != 'nt', reason='Windows PowerShell paths are not POSIX paths')
@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('case', ['other_process', 'known', 'foreign', 'empty', 'missing', 'unparseable', 'empty_snapshot', 'unrelated_script'])
def test_unresolved_powershell_and_foreign_tools_launcher_fail_closed(ps, case):
    script = load(FLEET_SOURCE, 'Assert-WdToolsProcessVisibility') + f"""
$ErrorActionPreference='Stop'; Set-StrictMode -Version Latest
if (-not (Get-Command Assert-WdToolsProcessVisibility -ErrorAction SilentlyContinue)) {{ throw 'function unavailable' }}
function Test-WdPowerShellHostOptionToken {{ param($Token,$HostKind,$Name) return $false }}
$p=[pscustomobject]@{{Name='powershell.exe';CommandLine='known'}}
if ('{case}' -eq 'empty') {{ $p.CommandLine='' }}
if ('{case}' -eq 'missing') {{ $p.CommandLine=$null }}
if ('{case}' -eq 'other_process') {{ $p.Name='notepad.exe' }}
$parse={{ param($CommandLine,$HostKind)
 if ('{case}' -eq 'unparseable') {{ return $null }}
 if ('{case}' -eq 'unrelated_script') {{ return [pscustomobject]@{{script_path='C:\\other\\unrelated.ps1';arguments=@('powershell.exe','-File','C:\\other\\unrelated.ps1');file_index=1}} }}
 if ('{case}' -eq 'foreign') {{ return [pscustomobject]@{{script_path='C:\\foreign\\start-wd-tools-consumer.ps1';arguments=@('powershell.exe','-File','C:\\foreign\\start-wd-tools-consumer.ps1');file_index=1}} }}
 return [pscustomobject]@{{script_path='C:\\Python\\start-wd-tools-consumer.ps1';arguments=@('powershell.exe','-File','C:\\Python\\start-wd-tools-consumer.ps1');file_index=1}} }}
$snapshot=if ('{case}' -eq 'empty_snapshot') {{ @() }} else {{ @($p) }}
$ok=$true; try {{ Assert-WdToolsProcessVisibility -Processes $snapshot `
 -AllowedToolsLaunchers @('C:\\Python\\start-wd-tools-consumer.ps1') -ParseInvocation $parse }} catch {{ $ok=$false }}
@{{ok=$ok}} | ConvertTo-Json -Compress
"""
    assert json.loads(_run_powershell(script, executable=ps).stdout)['ok'] is (case in ('other_process', 'known'))


@pytest.mark.skipif(os.name != 'nt', reason='native Windows argv parser')
@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('case', ['known', 'foreign', 'missing', 'encoded', 'encoded_file_other', 'encoded_file_known', 'unrelated_file'])
def test_visibility_uses_real_supervisor_parser_on_synthetic_command_lines(ps, case):
    parser_path = str(SUPERVISOR).replace("'", "''")
    script = load(FLEET_SOURCE, 'Assert-WdToolsProcessVisibility') + f"""
$ErrorActionPreference='Stop'; Set-StrictMode -Version Latest
$known='C:\\Python\\start-wd-tools-consumer.ps1'
$path=if ('{case}' -eq 'foreign') {{ 'C:\\foreign\\start-wd-tools-consumer.ps1' }} else {{ $known }}
$command='"C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" -NoProfile -File "'+$path+'"'
if ('{case}' -eq 'missing') {{ $command=$null }}
if ('{case}' -eq 'encoded') {{ $command='powershell.exe -EncodedCommand ZQ==' }}
if ('{case}' -eq 'encoded_file_other') {{ $command='powershell.exe -EncodedCommand ZQ== -File "C:\\Python\\unrelated.ps1"' }}
if ('{case}' -eq 'encoded_file_known') {{ $command='powershell.exe -EncodedCommand ZQ== -File "'+$known+'"' }}
if ('{case}' -eq 'unrelated_file') {{ $command='powershell.exe -File "C:\\other\\unrelated.ps1"' }}
$p=[pscustomobject]@{{Name='powershell.exe';CommandLine=$command;ProcessId=11111}}
$ok=$true;try {{ Assert-WdToolsProcessVisibility -Processes @($p) -AllowedToolsLaunchers @($known) `
 -ParserSourcePath '{parser_path}' -AllowUnpinnedParser }} catch {{ $ok=$false }}
@{{ok=$ok}} | ConvertTo-Json -Compress
"""
    assert json.loads(_run_powershell(script, executable=ps).stdout)['ok'] is (case == 'known')


@pytest.mark.skipif(os.name != 'nt', reason='native Windows argv parser')
@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('parent_kind', ['interactive', 'wrapper_file'])
def test_synthetic_wrapper_uac_process_tree_holds_when_parent_provenance_is_unknown(ps, parent_kind):
    parser_path = str(SUPERVISOR).replace("'", "''")
    script = load(FLEET_SOURCE, 'Assert-WdToolsProcessVisibility') + f"""
$ErrorActionPreference='Stop'; Set-StrictMode -Version Latest
$own=[pscustomobject]@{{Name='powershell.exe';ProcessId=$PID;CommandLine='powershell.exe -EncodedCommand ZQ=='}}
$parent=[pscustomobject]@{{Name='powershell.exe';ProcessId=11111;CommandLine='powershell.exe -NoExit'}}
if ('{parent_kind}' -eq 'wrapper_file') {{ $parent.CommandLine='powershell.exe -File C:\\Python\\start-wd-all.ps1 -Auto' }}
$ok=$true;try {{ Assert-WdToolsProcessVisibility -Processes @($parent,$own) `
 -AllowedToolsLaunchers @('C:\\Python\\start-wd-tools-consumer.ps1') -ParserSourcePath '{parser_path}' `
 -AllowUnpinnedParser }} catch {{ $ok=$false }}
@{{ok=$ok}} | ConvertTo-Json -Compress
"""
    assert json.loads(_run_powershell(script, executable=ps).stdout)['ok'] is False


@pytest.mark.skipif(os.name != 'nt', reason='native Windows argv parser')
@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_current_unverified_encoded_host_is_not_whitelisted_by_pid(ps):
    parser_path = str(SUPERVISOR).replace("'", "''")
    script = load(FLEET_SOURCE, 'Assert-WdToolsProcessVisibility') + f"""
$ErrorActionPreference='Stop'; Set-StrictMode -Version Latest
$own=[pscustomobject]@{{Name='powershell.exe';ProcessId=$PID;CommandLine='powershell.exe -EncodedCommand ZQ=='}}
$ok=$true;try {{ Assert-WdToolsProcessVisibility -Processes @($own) `
 -AllowedToolsLaunchers @('C:\\Python\\start-wd-tools-consumer.ps1') -ParserSourcePath '{parser_path}' `
 -AllowUnpinnedParser }} catch {{ $ok=$false }}
@{{ok=$ok}} | ConvertTo-Json -Compress
"""
    assert json.loads(_run_powershell(script, executable=ps).stdout)['ok'] is False


def test_agent_and_tools_producers_keep_correlation_and_tools_native_run_distinct():
    fleet = FLEET_SOURCE.read_text(encoding='utf-8-sig')
    agent = (REBOOT / 'start-wd-agent.ps1').read_text(encoding='utf-8-sig')
    tool = TOOLS.read_text(encoding='utf-8-sig')
    assert "'-RunId', $RunId" in fleet
    assert '-StartupRunId $RunId' in agent
    assert 'run_id = $StartupRunId' in agent
    assert 'run_id = $RunId' in agent and 'session_id = $RunId' in agent
    assert 'run_id = $StartupRunId' in tool
    assert '-StartupRunId $runId -FleetStartupRunId $FleetStartupRunId' in tool
    assert "if ($FleetStartupRunId) { $record['fleet_startup_run_id'] = $FleetStartupRunId }" in tool
    assert "if ($FleetStartupRunId) { $conversationReadinessBase['fleet_startup_run_id'] = $FleetStartupRunId }" in tool


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('bind,requested,expected', [(False, False, False), (False, True, True),
                                                      (True, False, True), (True, True, True)])
def test_generated_uac_command_preserves_direct_or_implicit_fresh_requirement(ps, bind, requested, expected):
    bool_text = lambda v: '$true' if v else '$false'
    script = load(DEPLOY, 'New-ForwardingWrapper') + f"""
$hash='a'*64
$wrapper=New-ForwardingWrapper -Target 'fake-target.ps1' -ExpectedHash $hash -ExpectedManifestHash $hash `
 -WrapperKind 'fleet' -ExpectedFinalCommit ('f'*40) -ExpectedFinalManifestHash $hash
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseInput($wrapper,[ref]$tokens,[ref]$errors)
$ifs=@($ast.FindAll({{param($n) $n -is [Management.Automation.Language.IfStatementAst] -and
 $n.Extent.Text.Contains("`$commandParts.Add('-RequireFreshLanes')")}},$true) | Sort-Object {{ $_.Extent.Text.Length }})
if ($ifs.Count -lt 1) {{ throw 'fresh forward missing' }}
$BindFleetStartup={bool_text(bind)}
$targetParameters=@{{RequireFreshLanes={bool_text(requested)}}}
$commandParts=New-Object 'System.Collections.Generic.List[string]'
. ([scriptblock]::Create($ifs[0].Extent.Text))
@{{forward=($commandParts -ccontains '-RequireFreshLanes');count=$commandParts.Count}} | ConvertTo-Json -Compress
"""
    out = json.loads(_run_powershell(script, executable=ps).stdout)
    assert out['forward'] is expected and out['count'] == int(expected)


@pytest.mark.skipif(os.name != 'nt', reason='isolated synthetic helper starts Windows PowerShell')
@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_tools_startup_receipt_binds_fleet_id_separately_from_native_run(tmp_path, ps):
    fx = startup_read_fixture(tmp_path, 'codex-tools-1', mode='refuse')
    call = startup_read_call(fx, 'codex-tools-1').replace(' -TimeoutSeconds',
        f" -StartupRunId 'tools-native-run' -FleetStartupRunId '{FLEET}' -TimeoutSeconds", 1)
    script = '\n'.join(load(TOOLS, function) for function in STARTUP_READ_FUNCTIONS) + '\n' + call
    result = json.loads(_run_powershell(script, executable=ps).stdout)
    assert result['run_id'] == 'tools-native-run'
    assert result['fleet_startup_run_id'] == FLEET
    persisted = json.loads((fx['worktree'] / '.codex-audit' / 'wd-mind-startup-read.json').read_bytes())
    assert persisted['fleet_startup_run_id'] == FLEET and persisted['run_id'] == 'tools-native-run'


def test_real_source_has_opt_in_fleet_forwarding_without_default_change():
    deploy = DEPLOY.read_text(encoding='utf-8-sig')
    supervisor = SUPERVISOR.read_text(encoding='utf-8-sig')
    tools = TOOLS.read_text(encoding='utf-8-sig')
    assert '[switch] $BindFleetStartup' in deploy
    assert "if ($BindFleetStartup)" in deploy and "-FleetStartupRunId" in deploy
    assert "if ($FleetStartupRunId)" in supervisor and "-FleetStartupRunId" in supervisor
    assert "fleet_startup_run_id" in tools
    assert '-StartupRunId $runId -FleetStartupRunId $FleetStartupRunId' in tools
