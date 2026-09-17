"""Real PowerShell reader/monitor regressions, without model calls."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / '.agent-bridge/bin'
SHELLS = list(dict.fromkeys(filter(None, [shutil.which('pwsh'), shutil.which('powershell.exe') ])))


def run(ps, script):
    result = subprocess.run([ps, '-NoProfile', '-NonInteractive', '-Command', script],
                            text=True, capture_output=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize('ps', SHELLS)
@pytest.mark.parametrize('mode', ['complete', 'invalid_second_page', 'row_limit', 'byte_limit', 'partial'])
def test_paged_snapshot_is_complete_or_fails_closed(tmp_path, ps, mode):
    path = tmp_path / 'events.jsonl'
    raw = b''.join(json.dumps({'n': n, 'padding': 'x' * 40}).encode() + b'\n' for n in range(5))
    if mode == 'invalid_second_page':
        raw += b'{invalid}\n'
    elif mode == 'partial':
        raw += b'{"partial":'
    path.write_bytes(raw)
    result = run(ps, f"""
    . '{BIN / 'BridgeIncrementalReader.ps1'}'
    Read-BridgeEventSnapshot -Path '{path}' -PageBytes 130 -MaxBytes {200 if mode == 'byte_limit' else 2000} -MaxRows {3 if mode == 'row_limit' else 100} | ConvertTo-Json -Depth 8 -Compress
    """)
    if mode in ('complete', 'partial'):
        assert result['status'] == 'OK'
        assert [v['n'] for v in result['rows']] == list(range(5))
        expected_offset = len(raw) if mode == 'complete' else len(raw) - len(b'{"partial":')
        assert result['candidate_cursor']['offset'] == expected_offset
    else:
        assert result['status'] == 'BLOCKED'
        assert result['rows'] == []
        assert result['candidate_cursor'] is None


@pytest.mark.parametrize('ps', SHELLS)
@pytest.mark.parametrize('enabled', [False, True])
def test_inbox_monitor_surfaces_only_addressed_wake_requests(tmp_path, ps, enabled):
    shared = tmp_path / 'shared'
    shared.mkdir()
    rows = [
        {'agent': 'codex-lead-1', 'type': 'wake_request', 'to': 'claude-rco-2', 'task_id': 'target'},
        {'agent': 'codex-lead-1', 'type': 'wake_request', 'to': 'claude-rco-1', 'task_id': 'other'},
        {'agent': 'claude-rco-2', 'type': 'wake_request', 'to': 'claude-rco-2', 'task_id': 'self'},
        {'agent': 'codex-lead-1', 'type': 'heartbeat', 'to': 'claude-rco-2', 'task_id': 'noise'},
    ]
    (shared / 'events.jsonl').write_text(''.join(json.dumps(x) + '\n' for x in rows))
    flag = '-IncludeWakeRequests' if enabled else ''
    result = run(ps, f"""
    $rows=@(& '{BIN / 'Monitor-AgentBridge.ps1'}' -Agent claude-rco-2 -RuntimeRoot '{tmp_path}' -TargetedOnly {flag} -ReplayExisting -Json -MaxIterations 1)
    ConvertTo-Json -InputObject @($rows | ForEach-Object {{ $_ | ConvertFrom-Json }}) -Depth 8 -Compress
    """)
    assert [r['task_id'] for r in result] == (['target'] if enabled else [])


@pytest.mark.parametrize('ps', SHELLS)
@pytest.mark.parametrize('change', ['none', 'nonce', 'session_id', 'agent_uuid', 'task_id', 'status', 'token', 'request_stamp', 'sum', 'old', 'future', 'missing_payload'])
def test_release_probe_rejects_uncorrelated_and_stale_answers(ps, change):
    mutations = {
        'none': '', 'nonce': "$e.payload.nonce='wrong'", 'session_id': "$e.session_id='old-session'",
        'agent_uuid': "$e.agent_uuid='foreign'", 'task_id': "$e.task_id='old-task'",
        'status': "$e.status='received'", 'token': "$e.payload.token='wrong'",
        'request_stamp': "$e.payload.request_stamp='wrong'", 'sum': '$e.payload.sum=$true',
        'old': "$e.ts_utc='2026-09-17T08:59:59Z'", 'future': "$e.ts_utc='2026-09-17T09:05:01Z'",
        'missing_payload': '$e.payload=$null',
    }
    result = run(ps, f"""
    . '{ROOT / 'ops/windows/reboot/Test-WdBridgeResponsiveness.ps1'}'
    $r=@{{agent='claude-rco-2';task_id='task';nonce='nonce';token='token';request_stamp='utc:stamp';sum=72;sent_at=[datetime]'2026-09-17T09:00:00Z'}}
    $i=@{{agent_uuid='uuid';session_id='session';run_id='run'}}
    $e=@{{agent='claude-rco-2';agent_uuid='uuid';session_id='session';run_id='run';type='message';status='fleet_probe_pass';to='operator';task_id='task';ts_utc='2026-09-17T09:01:00Z';payload=@{{nonce='nonce';token='token';request_stamp='utc:stamp';sum=72}}}}
    {mutations[change]}
    Test-WdProbeReply -Event $e -Request $r -Identity $i -Deadline ([datetime]'2026-09-17T09:05:00Z') | ConvertTo-Json
    """)
    assert result is (change == 'none')


@pytest.mark.parametrize('ps', SHELLS)
@pytest.mark.parametrize('name,expected', [('claude.exe', True), ('codex.exe', True), ('powershell.exe', False), ('not-codex.exe', False)])
def test_shared_cli_update_is_deferred_for_external_sessions(ps, name, expected):
    source = (ROOT / 'ops/windows/reboot/start-wd-all.ps1').read_text()
    start = source.index('function Test-WdCliUpdateDeferred {')
    end = source.index('\nfunction ', start + 1)
    result = run(ps, source[start:end] + f"\nTest-WdCliUpdateDeferred -Processes @([pscustomobject]@{{Name='{name}'}}) | ConvertTo-Json")
    assert result is expected


@pytest.mark.parametrize('ps', SHELLS)
@pytest.mark.parametrize('exit_code', [0, 7])
def test_native_tools_retains_real_exit_status_after_deferred_wait(tmp_path, ps, exit_code):
    source = (ROOT / 'ops/windows/reboot/start-wd-tools-consumer.ps1').read_text()
    start = source.index('function Start-WdToolsNativeProcess {')
    end = source.index('\nfunction ', start + 1)
    child = tmp_path / 'exit.py'
    child.write_text(f'import sys,time; time.sleep(0.2); sys.exit({exit_code})')
    result = run(ps, source[start:end] + f"""
    $p=Start-WdToolsNativeProcess -CliPath '{sys.executable}' -ArgumentLine '"{child}"' -Worktree '{tmp_path}'
    while(-not $p.WaitForExit(30)) {{ }}
    Start-Sleep -Milliseconds 100
    $p.ExitCode | ConvertTo-Json
    $p.Dispose()
    """)
    assert result == exit_code
