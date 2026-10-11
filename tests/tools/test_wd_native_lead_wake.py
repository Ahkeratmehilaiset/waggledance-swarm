"""Lead reuses Tools' exact-thread relay without taking over its native UI.

The queue transport and terminal are private doubles: these tests prove the
launcher-to-relay contract, not real Codex model-turn execution or live uptime.
"""
import json
from pathlib import Path

import pytest

from test_wd_reboot_bundle import REBOOT, LANE_TEST_SHELLS, _run_powershell
from test_wd_startup_recovery import load, q

THREAD = '01a0a654-12af-7d81-85fc-d75d515c5b65'


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('case', ['idle', 'active', 'uncertain', 'foreign_thread',
                                'producer_missing', 'producer_dead', 'producer_dies',
                                'producer_wrong', 'producer_disabled', 'producer_bad_id',
                                'producer_empty_id', 'producer_zero_id', 'producer_not_started',
                                'idle_after_receipt', 'active_receipt_later'])
def test_lead_terminal_runs_shared_relay_and_preserves_ui(tmp_path, ps, case):
    """MUTANT_LEAD_RELAY_OMITTED / MUTANT_LEAD_WRONG_THREAD / MUTANT_LEAD_REPLAY."""
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    journal = tmp_path / '.codex-audit' / 'wd-turn-loop'
    journal.mkdir(parents=True)
    wake = runtime / 'wake_codex-lead-1'
    shared = runtime / 'shared'
    shared.mkdir()
    event = dict(ts_utc='2026-10-11T00:00:00Z', type='message', agent='claude-rco-1',
                 task_id='fixture/lead-wake', to='codex-lead-1', status='answered',
                 message='bridge message addressed to codex-lead-1', payload={})
    second_event = dict(event, task_id='fixture/lead-wake-second',
                        message='second real bridge message addressed to Lead')
    (shared / 'events.jsonl').write_text(json.dumps(event) + '\n', encoding='utf-8')
    watcher = REBOOT.parents[2] / '.agent-bridge/bin/Watch-Bridge.ps1'
    # Real producer and incremental reader, exclusively on private fixture data.
    _run_powershell(f"$env:WAGGLE_BRIDGE_WAKE_ENABLED='1'; & {q(watcher)} -Agent codex-lead-1 "
                    f"-RuntimeRoot {q(runtime)} -StartLineCount 0 -MaxIterations 1 "
                    "-PollIntervalMs 1 -DebounceMs 1", executable=ps)
    initial_wake = wake.read_bytes()
    cli = tmp_path / 'fake-cli'
    cli.write_text('queue transport is replaced; this file is never executed')
    tools = (REBOOT / 'start-wd-tools-consumer.ps1').read_text(encoding='utf-8')
    runner = (REBOOT / 'Invoke-WdLaneTurnLoop.ps1').read_text(encoding='utf-8')
    # Bypass only the console precondition in the private fixture. A live native
    # terminal still requires interactive stdin; tests never start a real CLI.
    entry = load(REBOOT / 'start-wd-agent.ps1', 'Invoke-WdNativeLeadTerminal')
    guard = "if ([Console]::IsInputRedirected) { throw 'Native Lead requires an interactive terminal' }"
    (tmp_path / 'tools.txt').write_text(tools, encoding='utf-8')
    (tmp_path / 'runner.txt').write_text(runner, encoding='utf-8')
    script = load(REBOOT / 'Invoke-WdLaneTurnLoop.ps1', 'Assert-WdTurnPath') + entry
    script += f"""
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$entry=(Get-Command Invoke-WdNativeLeadTerminal).ScriptBlock.ToString()
$guard={q(guard)}
if(-not $entry.Contains($guard)){{throw 'fixture console precondition changed'}}
$entry=$entry.Replace($guard,'# Private test harness supplies the terminal double.')
. ([scriptblock]::Create('function Invoke-WdNativeLeadTerminal {{'+$entry+'}}'))
$env:WD_BRIDGE_BIN=''
$env:AGENT_BRIDGE_WAKE_JOB=$(switch('{case}'){{
 'producer_bad_id'{{'abc'}} 'producer_empty_id'{{''}} 'producer_zero_id'{{'0'}} default{{'42'}}
}})
$env:WAGGLE_BRIDGE_WAKE_ENABLED=$(if('{case}' -ceq 'producer_disabled'){{'0'}}else{{'1'}})
function Get-Job {{
 param($Id)
 if($Id -ne 42){{throw 'wrong producer job'}}
 if('{case}' -ceq 'producer_missing'){{return $null}}
 return [pscustomobject]@{{Name=$(if('{case}' -ceq 'producer_wrong'){{'agent-bridge-watcher-codex-tools-1'}}else{{'agent-bridge-watcher-codex-lead-1'}});
  State=$(if('{case}' -ceq 'producer_not_started'){{'NotStarted'}}elseif('{case}' -ceq 'producer_dead' -or ('{case}' -ceq 'producer_dies' -and $script:polls -ge 2)){{'Failed'}}else{{'Running'}})}}
}}
$script:calls=0; $script:polls=0; $script:disposed=$false
$script:waitedAfterFailure=$false; $script:sent=@(); $script:terminalCalls=0
$script:native=[pscustomobject]@{{Id=123;StartTime=[DateTime]::UtcNow;ExitCode=0;HasExited=$false}}
$script:native | Add-Member ScriptMethod WaitForExit {{
 param($Milliseconds)
 if($null -eq $Milliseconds){{$script:waitedAfterFailure=$true; $this.HasExited=$true;return}}
 $script:polls++
 if($script:polls -eq 2 -and '{case}' -in @('idle','active','producer_dies','idle_after_receipt','active_receipt_later')){{
   [IO.File]::AppendAllText({q(shared / 'events.jsonl')},{q(json.dumps(second_event))}+[Environment]::NewLine,(New-Object Text.UTF8Encoding($false)))
   & {q(watcher)} -Agent codex-lead-1 -RuntimeRoot {q(runtime)} -StartLineCount 1 -MaxIterations 1 -PollIntervalMs 1 -DebounceMs 1
   $p={q(journal / 'native-bridge-wake.json')}
   $s=Get-Content -LiteralPath $p -Raw | ConvertFrom-Json
   $s.updated_at_utc=[DateTimeOffset]::UtcNow.AddSeconds(-10).ToString('o')
   Write-WdTurnJson $p $s
 }}
 if(($script:polls -eq 2 -and '{case}' -ceq 'idle_after_receipt') -or ($script:polls -eq 3 -and '{case}' -ceq 'active_receipt_later')){{
   $s=Get-Content -LiteralPath {q(journal / 'native-bridge-wake.json')} -Raw | ConvertFrom-Json
   [void][IO.Directory]::CreateDirectory({q(shared / 'telemetry')})
   # Private simulated consumption, not a real model-turn observation.
   Write-WdTurnJson {q(shared / 'telemetry/stage-private-consumption.json')} @{{schema='wd.bridge-stage.v1';
    stage='model_turn_started';target='codex-lead-1';delivery_id=$s.delivery_id;observation_source='agent_reported'}}
 }}
 if($script:polls -ge 4){{$this.HasExited=$true;return $true}}
 return $false
}}
$script:native | Add-Member ScriptMethod Dispose {{$script:disposed=$true}}
function Replace-FixtureDefinition {{
 param([string]$Text,[string]$Name,[string]$Replacement)
 $tokens=$null;$errors=$null
 $ast=[Management.Automation.Language.Parser]::ParseInput($Text,[ref]$tokens,[ref]$errors)
 if($errors.Count){{throw 'fixture source parse failure'}}
 $defs=@($ast.EndBlock.Statements|Where-Object {{
  $_ -is [Management.Automation.Language.FunctionDefinitionAst] -and $_.Name -ceq $Name}})
 if($defs.Count -ne 1){{throw ('fixture function missing: '+$Name)}}
 $def=$defs[0]
 return $Text.Substring(0,$def.Extent.StartOffset)+$Replacement+$Text.Substring($def.Extent.EndOffset)
}}
$tools=[IO.File]::ReadAllText({q(tmp_path / 'tools.txt')})
$tools=Replace-FixtureDefinition $tools Start-WdToolsNativeProcess @'
function Start-WdToolsNativeProcess {{
 param($CliPath,$ArgumentLine,$Worktree)
 $script:terminalCalls++
 $script:terminalLine=$ArgumentLine
 return $script:native
}}
'@
$tools=Replace-FixtureDefinition $tools Send-WdNativeToolsQueueMessage @'
function Send-WdNativeToolsQueueMessage {{
 param($CliPath,$ThreadId,$Message,$Worktree)
 $script:calls++
 $script:sent+=@{{thread=$ThreadId;message=$Message;worktree=$Worktree}}
 if('{case}' -ceq 'uncertain'){{throw 'private uncertain queue result'}}
 return '01a0adff-4558-7e80-8936-6aad0d6df821'
}}
'@
$tools=Replace-FixtureDefinition $tools Get-WdVerifiedNativeWakeMessage @'
function Get-WdVerifiedNativeWakeMessage {{
 param($Agent,$DeliveryId)
 return Get-WdInlineNativeWakeMessage -Agent $Agent -DeliveryId $DeliveryId
}}
'@
$tools=Replace-FixtureDefinition $tools Invoke-WdNativeContinuityStep @'
function Invoke-WdNativeContinuityStep {{ param($CliPath,$ThreadId,$Worktree,$Generation,$Agent,$ExpectedCliHash,$RuntimeRoot,$SessionStartedAt);return 'fixture_no_recovery' }}
'@
$verified=@{{'start-wd-tools-consumer.ps1'=$tools;'Invoke-WdLaneTurnLoop.ps1'=[IO.File]::ReadAllText({q(tmp_path / 'runner.txt')})}}
$statePath={q(journal / 'native-bridge-wake.json')}
if('{case}' -ceq 'foreign_thread'){{
 [IO.File]::WriteAllText($statePath,(@{{schema='wd.native-tools-wake.v1';status='watching';
  agent='codex-lead-1';thread_id='02b1b765-12af-7d81-85fc-d75d515c5b66'}}|ConvertTo-Json))
}}
$ok=$false;$fixtureError=''
try {{
 Invoke-WdNativeLeadTerminal -CliPath {q(cli)} -Arguments @('resume','{THREAD}','--cd',{q(tmp_path)}) `
  -ThreadId '{THREAD}' -Worktree {q(tmp_path)} -RuntimeRoot {q(runtime)} -Generation fixture `
  -ExpectedCliHash (Get-FileHash -LiteralPath {q(cli)}).Hash -SessionId fixture-session -VerifiedCode $verified
 $ok=$true
}} catch {{$fixtureError=$_.Exception.Message}}
@{{ok=$ok;error=$fixtureError;calls=$script:calls;sent=$script:sent;terminal_calls=$script:terminalCalls;
 terminal_line=$script:terminalLine;disposed=$script:disposed;waited_after_failure=$script:waitedAfterFailure}}|ConvertTo-Json -Depth 8 -Compress
"""
    result = _run_powershell(script, executable=ps)
    # Non-terminating relay failure warnings precede the single JSON record.
    record = json.loads(next(line for line in reversed(result.stdout.splitlines()) if line.startswith('{')))
    assert record['terminal_calls'] == 1 and record['disposed'] is True, record
    assert 'resume' in record['terminal_line'] and THREAD in record['terminal_line'], record
    success = case in ('idle', 'active', 'idle_after_receipt', 'active_receipt_later')
    expected_calls = 2 if case in ('idle_after_receipt', 'active_receipt_later') else (1 if case in ('idle', 'active', 'uncertain', 'producer_dies') else 0)
    assert record['ok'] is success, record
    assert record['calls'] == expected_calls, record
    state_path = journal / 'native-bridge-wake.json'
    state = json.loads(state_path.read_text(encoding='utf-8-sig')) if state_path.exists() else None
    if case in ('idle', 'active'):
        assert state['status'] == 'queued' and state['agent'] == 'codex-lead-1', state
        assert state['thread_id'] == THREAD and state['task_completion_verified'] is False
        assert state['receipt'] == 'model_turn_started'
        assert json.loads(wake.read_text())['schema'] == 'wd.bridge-wake-observation.v1'
        assert json.loads((shared / 'events.jsonl').read_text().splitlines()[1]) == second_event
        assert not list(journal.glob('native-bridge-wake.json.wake.*'))
        sent, = record['sent']
        assert sent['thread'] == THREAD and sent['worktree'] == str(tmp_path)
        assert 'codex-lead-1' in sent['message'] and 'delivery_id=' in sent['message']
        assert 'codex-tools-1' not in sent['message']
        assert record['waited_after_failure'] is False
    elif case == 'uncertain':
        assert state['status'] == 'submitting', state
        snapshot, = journal.glob('native-bridge-wake.json.wake.*')
        assert snapshot.name.endswith('.' + state['snapshot_id'])
        assert snapshot.read_bytes() == initial_wake
        assert record['waited_after_failure'] is True
        assert record['error'] == 'private uncertain queue result'
    elif case == 'foreign_thread':
        assert state['thread_id'] != THREAD and wake.exists()
        assert record['error'] == 'Native bridge relay conversation changed'
        assert record['waited_after_failure'] is True
    elif case == 'producer_dies':
        assert state['status'] == 'queued' and state['thread_id'] == THREAD
        assert json.loads(wake.read_text())['schema'] == 'wd.bridge-wake-observation.v1'
        assert 'Native Lead wake producer unavailable; automatic delivery withheld' in record['error']
        assert record['waited_after_failure'] is True
    elif case in ('idle_after_receipt', 'active_receipt_later'):
        assert state['status'] == 'queued' and state['thread_id'] == THREAD
        assert not wake.exists() and not list(journal.glob('native-bridge-wake.json.wake.*'))
        first, second = record['sent']
        assert first['thread'] == second['thread'] == THREAD
        assert first['message'] != second['message']
        consumed = json.loads((shared / 'telemetry/stage-private-consumption.json').read_text(encoding='utf-8-sig'))
        assert consumed['delivery_id'] != state['delivery_id']
        assert json.loads((shared / 'events.jsonl').read_text().splitlines()[1]) == second_event
    else:
        assert state is None and wake.exists()
        assert 'Native Lead wake producer unavailable; automatic delivery withheld' in record['error']
        assert record['waited_after_failure'] is True
    # A refused queue path keeps the operator's terminal alive until it exits.
    # The private double exposes no Kill, input, foreground, steer or restart API.
    ready = json.loads((journal / 'native-terminal.json').read_text(encoding='utf-8-sig'))
    # MUTANT_LEAD_BLOCKED_EVIDENCE_OVERWRITTEN: UI exit must not erase refusal.
    assert ready['status'] == ('stopped' if success else 'bridge_wake_blocked')
    assert ready['thread_id'] == THREAD
    assert ready['native_pid'] == 123 and ready['bridge_wake_transport'] == 'codex_queue'
    assert ready['bridge_wake_producer_status'] == ('unavailable' if case.startswith('producer_') else 'running')
    assert ready['bridge_wake_producer_observed_at_utc']


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('native_lead', [True, False])
def test_only_native_lead_owns_its_pinned_session_wake_producer(ps, native_lead):
    """MUTANT_NATIVE_LEAD_WITHOUT_PRODUCER; other lane startup is unchanged."""
    source = (REBOOT / 'start-wd-agent.ps1').read_text(encoding='utf-8')
    start = source.index('$sessionArgs = @{')
    end = source.index('\nif ([bool]$lane.require_dedicated_worktree)', start)
    script = f"""
$lane=@{{agent='fixture';role='fixture';agent_uuid='fixture';capabilities=@()}}
$runtimeRoot='runtime';$worktree='worktree';$RunId='fixture';$primaryRepo='repo'
$nativeLead=${str(native_lead).lower()}
{source[start:end]}
$sessionArgs.SkipWakeWatcher | ConvertTo-Json -Compress
"""
    skipped = json.loads(_run_powershell(script, executable=ps).stdout)
    assert skipped is (not native_lead)


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_native_lead_producer_is_not_also_supervisor_owned(ps):
    """MUTANT_LEAD_DOUBLE_PRODUCER: one source for the dirty bit."""
    config = json.loads((REBOOT / 'wd_supervisor_loop.json').read_text(encoding='utf-8'))
    assert 'codex-lead-1' not in config['watchers']['agents']
    assert set(config['watchers']['agents']) == {'codex-tools-1', 'claude-rco-1', 'claude-rco-2', 'fable-5'}
    # Configuration must be accepted by the real validator, not only by a
    # Python assertion. Keep malformed/missing/duplicate identities refused.
    script = load(REBOOT / 'wd_supervisor.ps1', 'Assert-WdExactWatcherAgentSet')
    script += f"""
$cfg=Get-Content -LiteralPath {q(REBOOT / 'wd_supervisor_loop.json')} -Raw | ConvertFrom-Json
$agents=@($cfg.watchers.agents)
Assert-WdExactWatcherAgentSet -Agents $agents
$refused=0
foreach($bad in @(@($agents+'codex-lead-1'),@($agents[0],$agents[0],$agents[2],$agents[3]),@($agents[0..2]))){{
 try{{Assert-WdExactWatcherAgentSet -Agents $bad}}catch{{$refused++}}
}}
$refused|ConvertTo-Json -Compress
"""
    assert json.loads(_run_powershell(script, executable=ps).stdout) == 3
