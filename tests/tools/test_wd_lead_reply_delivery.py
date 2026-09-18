"""Late peer replies must wake the same native Lead conversation."""
import json
import os
import subprocess
import hashlib
from pathlib import Path

import pytest

from test_wd_reboot_bundle import REBOOT, LANE_TEST_SHELLS, _run_powershell
from test_wd_startup_recovery import load, q


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_lead_reply_wakes_existing_thread_and_preserves_concurrent_reply(tmp_path, ps):
    wake = tmp_path / 'wake_codex-lead-1'
    state = tmp_path / 'native-bridge-wake.json'
    wake.write_text('Fable answer arrived after final snapshot')
    script = "$ErrorActionPreference='Stop'\nSet-StrictMode -Version Latest\n"
    for name in ['Assert-WdTurnPath', 'Write-WdTurnJson', 'Move-WdWakeSnapshot']:
        script += load(REBOOT / 'Invoke-WdLaneTurnLoop.ps1', name)
    script += load(REBOOT / 'start-wd-tools-consumer.ps1', 'Invoke-WdNativeToolsWakeStep')
    script += f"""
$script:messages=@()
function Send-WdNativeToolsQueueMessage {{
 param($CliPath,$ThreadId,$Message,$Worktree)
 if($ThreadId -cne 'exact-lead-thread'){{throw 'wrong conversation'}}
 $script:messages+=,$Message
 [IO.File]::WriteAllText({q(wake)},'second reply during queue submission')
 return '01a0adff-4558-7e80-8936-6aad0d6df821'
}}
$result=Invoke-WdNativeToolsWakeStep -Agent codex-lead-1 -CliPath unused -ThreadId exact-lead-thread `
 -Worktree {q(tmp_path)} -WakePath {q(wake)} -StatePath {q(state)} -Generation pinned -NativePid 123
@{{result=$result;messages=$script:messages}}|ConvertTo-Json -Depth 8
"""
    result = json.loads(_run_powershell(script, executable=ps).stdout)
    message = result['messages'][0]
    assert 'Automatic bridge wake for codex-lead-1' in message
    assert 'Get-BridgeReplySnapshot.ps1' in message
    assert 'late' in message.lower() and 'pending' in message.lower()
    assert 'Automatic bridge wake for codex-tools-1' not in message
    assert wake.read_text() == 'second reply during queue submission'
    saved = json.loads(state.read_text(encoding='utf-8-sig'))
    assert saved['status'] == 'queued' and saved['agent'] == 'codex-lead-1'
    assert not saved['task_completion_verified']


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('case', ['late', 'wrong_session', 'ack', 'partial', 'invalid', 'conflict'])
def test_fresh_snapshot_never_calls_incomplete_or_wrong_reply_answered(tmp_path, ps, case):
    from test_bridge_request_contract import events
    _, request, reply = events()
    request.update(request_id='exact-v2', request_digest='digest-v2')
    reply.update(in_reply_to_request_id='exact-v2', in_reply_to_request_digest='digest-v2',
                 in_reply_to_requester={k: request[k] for k in ('agent', 'agent_uuid', 'session_id', 'run_id')})
    shared = tmp_path / 'shared'
    shared.mkdir()
    log = shared / 'events.jsonl'
    log.write_text(json.dumps(request) + '\n', encoding='utf-8')
    def read():
        return subprocess.run([ps, '-NoProfile', '-NonInteractive', '-File',
                               str(REBOOT.parents[2] / '.agent-bridge/bin/Get-BridgeReplySnapshot.ps1'),
                               '-RequestId', 'exact-v2'],
                              env=dict(os.environ, AGENT_BRIDGE_RUNTIME_ROOT=str(tmp_path)),
                              capture_output=True, text=True, timeout=40)
    first = read()
    assert first.returncode == 0, first.stderr
    assert json.loads(first.stdout)['results'][0]['state'] == 'pending_at_snapshot'
    if case == 'wrong_session': reply['session_id'] = 'old-session'
    if case == 'ack': reply['status'] = 'received'
    if case == 'conflict': reply = dict(request, message='immutable ID changed')
    with log.open('a', encoding='utf-8') as stream:
        stream.write('{invalid}\n' if case == 'invalid' else json.dumps(reply) + ('' if case == 'partial' else '\n'))
    second = read()
    if case in ('partial', 'invalid', 'conflict'):
        assert second.returncode != 0
    else:
        assert second.returncode == 0, second.stderr
        snapshot = json.loads(second.stdout)
        assert snapshot['results'][0]['state'] == ('answered' if case == 'late' else 'pending_at_snapshot')
        assert snapshot['snapshot_bytes'] > json.loads(first.stdout)['snapshot_bytes']
        if case == 'late': assert snapshot['results'][0]['answers'][0] == reply


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_native_lead_adapter_imports_only_verified_functions_and_relays_in_same_thread(tmp_path, ps):
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    (runtime / 'wake_codex-lead-1').write_text('late answer')
    cli = tmp_path / 'codex.exe'
    cli.write_bytes(b'fixture only, never execute')
    runner = ''
    for name in ['Assert-WdTurnPath', 'Write-WdTurnJson', 'Move-WdWakeSnapshot']:
        runner += load(REBOOT / 'Invoke-WdLaneTurnLoop.ps1', name)
    code = ''
    for name in ['ConvertTo-WdToolsNativeArgument', 'Invoke-WdNativeToolsWakeStep', 'Invoke-WdNativeToolsWakeRelay']:
        code += load(REBOOT / 'start-wd-tools-consumer.ps1', name)
    code += """
function Send-WdNativeToolsQueueMessage {
 param($CliPath,$ThreadId,$Message,$Worktree)
 $global:delivery=@{thread=$ThreadId;message=$Message}
 return '01a0adff-4558-7e80-8936-6aad0d6df821'
}
function Start-WdToolsNativeProcess {
 param($CliPath,$ArgumentLine,$Worktree)
 $global:starts++; $global:arguments=$ArgumentLine
 $p=[pscustomobject]@{Id=123;StartTime=[datetime]::Now;HasExited=$false;ExitCode=0;polls=0}
 $p|Add-Member ScriptMethod WaitForExit {param($milliseconds) $this.polls++;$this.HasExited=($this.polls -gt 1);return $this.HasExited}
 $p|Add-Member ScriptMethod Dispose {}
 return $p
}
"""
    runner_path = tmp_path / 'runner.txt'
    code_path = tmp_path / 'tools.txt'
    runner_path.write_text(runner)
    code_path.write_text(code)
    # The test owns no real terminal. Substitute only the console-presence probe.
    function = load(REBOOT / 'start-wd-agent.ps1', 'Invoke-WdNativeLeadTerminal').replace(
        '$fn.Extent.Text)', "$fn.Extent.Text.Replace('[Console]::IsInputRedirected','$false'))")
    script = "$ErrorActionPreference='Stop'\nSet-StrictMode -Version Latest\n" + function + f"""
$global:starts=0; $env:WD_BRIDGE_BIN=''
. ([scriptblock]::Create([IO.File]::ReadAllText({q(runner_path)})))
. ([scriptblock]::Create([IO.File]::ReadAllText({q(code_path)})))
$verified=@{{}}
$groups=@{{'Invoke-WdLaneTurnLoop.ps1'=@('Assert-WdTurnPath','Write-WdTurnJson','Move-WdWakeSnapshot');
 'start-wd-tools-consumer.ps1'=@('ConvertTo-WdToolsNativeArgument','Send-WdNativeToolsQueueMessage',
 'Invoke-WdNativeToolsWakeStep','Invoke-WdNativeToolsWakeRelay','Start-WdToolsNativeProcess')}}
foreach($file in $groups.Keys){{
 $definitions=@($groups[$file]|ForEach-Object {{'function '+$_+' {{'+(Get-Command $_).ScriptBlock.ToString()+'}}'}})
 $verified[$file]='throw "top-level must not execute"'+"`n"+($definitions -join "`n")
}}
Invoke-WdNativeLeadTerminal -CliPath {q(cli)} -Arguments @('resume','01a0a654-12af-7d81-85fc-d75d515c5b65') `
 -ThreadId 01a0a654-12af-7d81-85fc-d75d515c5b65 -Worktree {q(tmp_path)} -RuntimeRoot {q(runtime)} `
 -Generation fixture -SessionId session -ExpectedCliHash {hashlib.sha256(cli.read_bytes()).hexdigest().upper()} -VerifiedCode $verified
@{{starts=$global:starts;arguments=$global:arguments;delivery=$global:delivery}}|ConvertTo-Json -Depth 6
"""
    result = json.loads(_run_powershell(script, executable=ps).stdout)
    assert result['starts'] == 1 and result['arguments'] == 'resume 01a0a654-12af-7d81-85fc-d75d515c5b65'
    assert result['delivery']['thread'] == '01a0a654-12af-7d81-85fc-d75d515c5b65'
    assert 'Automatic bridge wake for codex-lead-1' in result['delivery']['message']
    journal = tmp_path / '.codex-audit/wd-turn-loop'
    assert json.loads((journal / 'native-terminal.json').read_text(encoding='utf-8-sig'))['status'] == 'stopped'
    assert json.loads((journal / 'native-bridge-wake.json').read_text(encoding='utf-8-sig'))['status'] == 'queued'


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_native_relay_reloads_its_own_receipt_in_non_us_locale(tmp_path, ps):
    wake = tmp_path / 'wake_codex-lead-1'
    state = tmp_path / 'native-bridge-wake.json'
    wake.write_text('first reply')
    script = "$ErrorActionPreference='Stop'\nSet-StrictMode -Version Latest\n"
    for name in ['Assert-WdTurnPath', 'Write-WdTurnJson', 'Move-WdWakeSnapshot']:
        script += load(REBOOT / 'Invoke-WdLaneTurnLoop.ps1', name)
    script += load(REBOOT / 'start-wd-tools-consumer.ps1', 'Invoke-WdNativeToolsWakeStep')
    script += f"""
[Threading.Thread]::CurrentThread.CurrentCulture=[Globalization.CultureInfo]::GetCultureInfo('fi-FI')
function Send-WdNativeToolsQueueMessage {{ return '01a0adff-4558-7e80-8936-6aad0d6df821' }}
$arguments=@{{Agent='codex-lead-1';CliPath='unused';ThreadId='exact-thread';Worktree={q(tmp_path)};
 WakePath={q(wake)};StatePath={q(state)};Generation='fixture';NativePid=123}}
$first=Invoke-WdNativeToolsWakeStep @arguments
# The live failure requires a receipt read on a later poll, not just a first submission.
$record=Get-Content -LiteralPath {q(state)} -Raw | ConvertFrom-Json
$record.updated_at_utc='2026-09-18T14:17:14.0348391+00:00'
Write-WdTurnJson {q(state)} $record
$second=Invoke-WdNativeToolsWakeStep @arguments
@{{first=$first;second=$second}}|ConvertTo-Json
"""
    result = json.loads(_run_powershell(script, executable=ps).stdout)
    assert result['first'] == 'queued' and result['second'] in ('idle', 'debounced')


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('case', ['unchanged', 'append', 'corrupt_cache', 'missing_cache', 'rewrite', 'rotate', 'partial', 'invalid'])
def test_reply_parse_index_is_incremental_and_rebuildable(tmp_path, ps, case):
    from test_bridge_request_contract import events
    _, request, reply = events()
    request.update(request_id='index-v1', request_digest='digest-v1')
    request.setdefault('payload', {})['evidence_time'] = '2026-09-18T00:00:00+05:30'
    reply.update(in_reply_to_request_id='index-v1', in_reply_to_request_digest='digest-v1',
                 in_reply_to_requester={k: request[k] for k in ('agent', 'agent_uuid', 'session_id', 'run_id')})
    shared = tmp_path / 'shared'
    shared.mkdir()
    log = shared / 'events.jsonl'
    log.write_text(json.dumps(request) + '\n')
    def read(*args):
        return subprocess.run([ps, '-NoProfile', '-NonInteractive', '-File',
                               str(REBOOT.parents[2] / '.agent-bridge/bin/Get-BridgeReplySnapshot.ps1'),
                               '-RequestId', 'index-v1', *args],
                              env=dict(os.environ, AGENT_BRIDGE_RUNTIME_ROOT=str(tmp_path)),
                              capture_output=True, text=True, timeout=40)
    first = read()
    assert first.returncode == 0, first.stderr
    assert json.loads(first.stdout)['parsed_rows'] == 1
    assert json.loads(first.stdout)['request']['payload']['evidence_time'] == request['payload']['evidence_time']
    cache = shared / 'cache/reply-index.json'
    assert cache.exists()
    if case == 'corrupt_cache': cache.write_text('{bad cache')
    if case == 'missing_cache': cache.unlink()
    if case == 'rewrite':
        request['message'] = 'Changed canonical request'
        log.write_text(json.dumps(request) + '\n')
    if case == 'rotate':
        log.rename(shared / 'old.jsonl')
        log.write_text(json.dumps(request) + '\n')
    if case in ('append', 'partial', 'invalid'):
        with log.open('a') as stream:
            stream.write('{broken}\n' if case == 'invalid' else json.dumps(reply) + ('' if case == 'partial' else '\n'))
    result = read()
    if case in ('partial', 'invalid'):
        assert result.returncode != 0
        return
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    if case in ('unchanged', 'append'):
        assert value['cache_status'] == 'incremental'
        assert value['parsed_rows'] == (1 if case == 'append' else 0)
    else:
        assert value['cache_status'] == 'rebuilt'
    reference = read('-NoCache')
    assert reference.returncode == 0, reference.stderr
    assert value['results'] == json.loads(reference.stdout)['results']
    assert value['request'] == json.loads(reference.stdout)['request']


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('case', ['processed', 'reported', 'missing_reference', 'wrong_session', 'ack'])
def test_reply_observation_requires_bound_answer_and_honest_report_reference(tmp_path, ps, case):
    from test_bridge_request_contract import events
    _, request, reply = events()
    request.update(request_id='observed-v1', request_digest='digest-v1')
    reply.update(in_reply_to_request_id='observed-v1', in_reply_to_request_digest='digest-v1',
                 in_reply_to_requester={k: request[k] for k in ('agent', 'agent_uuid', 'session_id', 'run_id')})
    if case == 'wrong_session': reply['session_id'] = 'wrong'
    if case == 'ack': reply['status'] = 'received'
    stage = 'user_reported' if case in ('reported', 'missing_reference') else 'lead_processed'
    script = REBOOT.parents[2] / '.agent-bridge/bin/Record-BridgeReplyObservation.ps1'
    command = (f"$env:AGENT_BRIDGE_RUNTIME_ROOT={q(tmp_path)}; & {q(script)} -Agent codex-lead-1 "
               f"-RequestEventJson {q(json.dumps(request))} -ReplyEventJson {q(json.dumps(reply))} -Stage {stage}")
    if case == 'reported': command += " -ReportReference 'operator-summary-42'"
    result = subprocess.run([ps, '-NoProfile', '-NonInteractive', '-Command', command],
                            capture_output=True, text=True, timeout=30)
    records = list((tmp_path / 'shared/telemetry').glob('*.json'))
    if case in ('wrong_session', 'ack', 'missing_reference'):
        assert result.returncode != 0 and not records
    else:
        assert result.returncode == 0, result.stderr
        value = json.loads(records[0].read_text())
        assert value['stage'] == stage and value['observation_source'] == 'agent_reported'
        assert value['request_id'] == request['request_id']
        assert value['reply_ts_utc'] == reply['ts_utc']


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_direct_lane_resume_exports_only_verified_anchor_to_child(tmp_path, ps):
    manifest = tmp_path / 'deployment-manifest.json'
    manifest.write_text('{"fixture":true}')
    digest = hashlib.sha256(manifest.read_bytes()).hexdigest().upper()
    script = "$ErrorActionPreference='Stop'\n" + load(REBOOT / 'start-wd-agent.ps1', 'Set-WdLaneChildManifestAnchor')
    script += f"""
$env:WD_REBOOT_EXPECTED_MANIFEST_HASH='old-wrapper-anchor'
Set-WdLaneChildManifestAnchor -ManifestPath {q(manifest)} -ExpectedHash {q(digest.lower())}
$good=$env:WD_REBOOT_EXPECTED_MANIFEST_HASH
$rejected=$false
try {{ Set-WdLaneChildManifestAnchor -ManifestPath {q(manifest)} -ExpectedHash ('F'*64) }} catch {{ $rejected=$true }}
@{{good=$good;rejected=$rejected;after=$env:WD_REBOOT_EXPECTED_MANIFEST_HASH}}|ConvertTo-Json
"""
    value = json.loads(_run_powershell(script, executable=ps).stdout)
    assert value == dict(good=digest, rejected=True, after=digest)
