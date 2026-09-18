"""Transport correlation must not masquerade as task-result validation."""
import json
import os
import subprocess
import hashlib
import shutil
from datetime import datetime
from pathlib import Path

import pytest

from test_wd_reboot_bundle import LANE_TEST_SHELLS, REBOOT, _run_powershell
from test_wd_startup_recovery import q

BIN = REBOOT.parents[2] / '.agent-bridge/bin'


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_classifier_keeps_caller_strict_mode_while_validating_its_own_inputs(ps):
    result = _run_powershell(f"""
Set-StrictMode -Off
. {q(BIN / 'BridgeEventClassifier.ps1')}
$empty=[pscustomobject]@{{}}
$caller=$empty.missing
$rejected=$false
try {{ Test-BridgeAckEvent $empty|Out-Null }} catch {{$rejected=$true}}
@{{caller_unchanged=($null -eq $caller);invalid_input_rejected=$rejected}}|ConvertTo-Json
""", executable=ps)
    assert json.loads(result.stdout) == {'caller_unchanged': True, 'invalid_input_rejected': True}


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('case', ['valid', 'wrong_level', 'wrong_type', 'wrong_content', 'extra', 'unknown', 'role_fields', 'empty_checks', 'empty_result', 'independent_review'])
def test_result_contract_separates_schema_content_and_reporting(ps, case):
    contract = dict(schema='wd.task-result-contract.v1', required=['answer'],
                    types={'answer': 'integer'}, equals={'answer': 42}, additional_properties=False)
    request = {'payload': {'result_contract': contract}}
    payload = {'result': {'answer': 42}}
    if case == 'wrong_level': payload = {'answer': 42}
    if case == 'wrong_type': payload['result']['answer'] = '42'
    if case == 'wrong_content': payload['result']['answer'] = 41
    if case == 'extra': payload['result']['extra'] = True
    if case == 'unknown': request['payload'] = {}
    if case == 'role_fields': request['payload'] = {'schema': 'wd.role-request.v1', 'result_fields': ['answer']}
    if case == 'empty_checks': contract.update(types={}, equals={})
    if case == 'empty_result': payload['result'] = {}
    if case == 'independent_review':
        request['to'] = 'claude-rco-1'
        payload['result']['answer'] = 41
    script = f"""
. {q(BIN / 'BridgeTaskResult.ps1')}
Get-BridgeTaskResultValidation -Request ({q(json.dumps(request))}|ConvertFrom-Json) -Payload ({q(json.dumps(payload))}|ConvertFrom-Json)|ConvertTo-Json -Depth 8
"""
    value = json.loads(_run_powershell(script, executable=ps).stdout)
    assert value['reported'] is None
    if case == 'unknown':
        assert value['schema_valid'] is None and value['content_valid'] is None
    elif case in ('role_fields', 'empty_checks', 'independent_review'):
        assert value['schema_valid'] and value['content_valid'] is None
        assert not value['errors']
    elif case in ('wrong_level', 'wrong_type', 'extra', 'empty_result'):
        assert value['schema_valid'] is False and value['content_valid'] is None
    else:
        assert value['schema_valid'] is True
        assert value['content_valid'] is (case == 'valid')


@pytest.mark.skipif(os.name != 'nt', reason='canonical append is Windows only')
@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('case', ['builder', 'wrong_level', 'wrong_content', 'pin_mismatch', 'review_disagreement'])
def test_writer_rejects_bad_structured_results_before_canonical_write(tmp_path, ps, case):
    agent = 'claude-rco-1' if case == 'review_disagreement' else 'codex-tools-1'
    agent_uuid = json.loads((REBOOT.parents[2] / 'configs/bridge_identity_registry.json').read_text())['identities'][agent]
    request = dict(ts_utc='2026-01-01T00:00:00Z', request_id='quality-request', request_digest='digest', agent='operator', session_id='op-session',
                   run_id='op-run', to=agent, type='wake_request', status='request', task_id='fixture/result',
                   expected_responders={agent: dict(agent_uuid=agent_uuid, session_id='tools-session', run_id='tools-run')},
                   payload=dict(nonce='exact-nonce', result_contract=dict(schema='wd.task-result-contract.v1',
                       required=['answer'], types={'answer': 'integer'}, equals={'answer': 42})))
    env = {k: v for k, v in os.environ.items() if not k.startswith(('WD_BRIDGE_', 'WD_REBOOT_', 'AGENT_BRIDGE_'))}
    env.update(AGENT_BRIDGE_RUNTIME_ROOT=str(tmp_path), AGENT_BRIDGE_AGENT=agent,
               AGENT_BRIDGE_AGENT_UUID=agent_uuid, AGENT_BRIDGE_SESSION_ID='tools-session', AGENT_BRIDGE_RUN_ID='tools-run')
    if case == 'pin_mismatch': env['WD_BRIDGE_BIN'] = str(tmp_path / 'foreign')
    answer = 41 if case == 'review_disagreement' else 42
    if case in ('builder', 'pin_mismatch', 'review_disagreement'):
        args = [str(BIN / 'Write-BridgeTaskReply.ps1'), '-Agent', agent, '-RequestEventJson', json.dumps(request),
                '-ResultJson', json.dumps({'answer': answer}), '-ReceiptJson']
    else:
        payload = {'nonce': 'exact-nonce', 'answer': 42} if case == 'wrong_level' else {'nonce': 'exact-nonce', 'result': {'answer': 41}}
        args = [str(BIN / 'Write-AgentEvent.ps1'), '-Agent', agent, '-Type', 'message', '-Status', 'answered',
                '-To', 'operator', '-TaskId', 'fixture/result', '-ReplyToEventJson', json.dumps(request), '-PayloadJson', json.dumps(payload)]
    before = datetime.now().astimezone()
    result = subprocess.run([ps, '-NoProfile', '-NonInteractive', '-File', *args], env=env, text=True, capture_output=True, timeout=40)
    log = tmp_path / 'shared/events.jsonl'
    if case not in ('builder', 'review_disagreement'):
        assert result.returncode != 0 and not log.exists(), result.stdout + result.stderr
        assert ('Execution evidence rejected' if case == 'pin_mismatch' else 'Task result rejected') in result.stderr
        return
    assert result.returncode == 0, result.stderr
    event = json.loads(log.read_text().splitlines()[-1])
    assert event['in_reply_to_request_id'] == request['request_id']
    assert event['payload']['result'] == {'answer': answer}
    assert event['payload']['result_validation']['schema_valid'] is True
    assert event['payload']['result_validation']['content_valid'] is (None if case == 'review_disagreement' else True)
    evidence = event['payload']['execution_evidence']
    assert evidence['helper_directory'] == str(BIN)
    assert before <= datetime.fromisoformat(evidence['observed_at_utc'].replace('Z', '+00:00')) <= datetime.now().astimezone()
    assert evidence['task_completion_verified'] is False
    assert evidence['native_conversation_id'] is None or len(evidence['native_conversation_id']) == 36


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('case', ['informational', 'unknown', 'request', 'late_answer', 'correction', 'ack'])
def test_watcher_preserves_new_requests_and_late_corrections(tmp_path, ps, case):
    event = dict(ts_utc='2026-09-18T18:00:00Z', agent='fable-5', to='codex-lead-1',
                 type='message', status='notice', task_id='already-reported', payload={'notification': 'informational'})
    if case == 'unknown': event['payload'] = {}
    if case == 'request': event.update(type='wake_request', status='request', request_id='new-request')
    if case in ('late_answer', 'correction'):
        event.update(in_reply_to_request_id='old-closed-request', status='corrected' if case == 'correction' else 'answered')
    if case == 'ack': event['status'] = 'received'
    (tmp_path / 'shared').mkdir()
    (tmp_path / 'shared/events.jsonl').write_text(json.dumps(event)+'\n', encoding='utf-8')
    result = subprocess.run([ps, '-NoProfile', '-File', str(BIN / 'Watch-Bridge.ps1'), '-Agent', 'codex-lead-1',
                             '-RuntimeRoot', str(tmp_path), '-StartLineCount', '0', '-MaxIterations', '1',
                             '-PollIntervalMs', '1', '-DebounceMs', '1'], capture_output=True, text=True, timeout=30,
                             env=dict(os.environ, WAGGLE_BRIDGE_WAKE_ENABLED='1'))
    assert result.returncode == 0, result.stderr
    assert (tmp_path / 'wake_codex-lead-1').exists() is (case not in ('informational', 'ack'))


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('case', ['verified', 'repinned', 'unknown_thread'])
def test_execution_evidence_checks_observed_launcher_not_installed_pointer(tmp_path, ps, case):
    generation = 'a' * 40
    bundle = tmp_path / generation
    helpers = bundle / 'tools-bootstrap/.agent-bridge/bin'
    helpers.mkdir(parents=True)
    files = {}
    for name in ('Get-BridgeExecutionEvidence.ps1', 'Write-BridgeTaskReply.ps1', 'Write-AgentEvent.ps1', 'BridgeTaskResult.ps1'):
        shutil.copyfile(BIN / name, helpers / name)
        files['tools-bootstrap/.agent-bridge/bin/' + name] = hashlib.sha256((helpers / name).read_bytes()).hexdigest().upper()
    manifest = bundle / 'deployment-manifest.json'
    manifest.write_text(json.dumps({'files': files}))
    launcher_generation = 'b' * 40 if case == 'repinned' else generation
    thread = '01a0a654-12af-7d81-85fc-d75d515c5b65'
    native_cmd = 'claude.exe --resume ' + thread if case != 'unknown_thread' else 'claude.exe'
    script = f"""
$env:WD_BRIDGE_BIN={q(helpers)}
$env:WD_REBOOT_EXPECTED_MANIFEST_HASH={q(hashlib.sha256(manifest.read_bytes()).hexdigest())}
function Get-CimInstance {{
 param($ClassName,$Filter)
 $id=[int]($Filter -replace 'ProcessId=','')
 $started=[datetime]'2026-09-18T01:00:00Z'
 if($id -eq $PID){{return [pscustomobject]@{{ProcessId=$PID;ParentProcessId=900001;Name='pwsh.exe';CommandLine='helper';CreationDate=$started}}}}
 if($id -eq 900001){{return [pscustomobject]@{{ProcessId=900001;ParentProcessId=900002;Name='claude.exe';CommandLine={q(native_cmd)};CreationDate=$started}}}}
 if($id -eq 900002){{return [pscustomobject]@{{ProcessId=900002;ParentProcessId=0;Name='pwsh.exe';CommandLine='pwsh -File C:\\bundle\\{launcher_generation}\\start-wd-agent.ps1 -Agent claude-rco-1';CreationDate=$started}}}}
}}
& {q(helpers / 'Get-BridgeExecutionEvidence.ps1')}
"""
    completed = _run_powershell(script, executable=ps, check=False)
    assert completed.returncode == 0, completed.stderr
    value = json.loads(completed.stdout)
    assert value['observed_agent'] == 'claude-rco-1'
    assert value['native_conversation_id'] == (None if case == 'unknown_thread' else thread)
    assert value['pin_status'] == ('mismatch' if case == 'repinned' else 'manifest_and_launcher_verified')
    assert value['generation'] == generation


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_watcher_coalesces_identical_events_without_suppressing_revision(tmp_path, ps):
    event = dict(ts_utc='2026-09-18T18:00:00Z', agent='operator', to='codex-lead-1',
                 type='wake_request', status='request', task_id='same-task', request_id='first', session_id='op', payload={})
    revised = dict(event, request_id='second', ts_utc='2026-09-18T18:00:01Z')
    (tmp_path / 'shared').mkdir()
    (tmp_path / 'shared/events.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in (event, event, revised)))
    result = subprocess.run([ps, '-NoProfile', '-File', str(BIN / 'Watch-Bridge.ps1'), '-Agent', 'codex-lead-1',
                             '-RuntimeRoot', str(tmp_path), '-StartLineCount', '0', '-MaxIterations', '1',
                             '-PollIntervalMs', '1', '-DebounceMs', '1'], capture_output=True, text=True, timeout=30,
                             env=dict(os.environ, WAGGLE_BRIDGE_WAKE_ENABLED='1'))
    assert result.returncode == 0, result.stderr
    stages = [json.loads(p.read_text(encoding='utf-8-sig')) for p in (tmp_path / 'shared/telemetry').glob('*.json')]
    assert sorted(e['request_id'] for e in stages) == ['first', 'second']
