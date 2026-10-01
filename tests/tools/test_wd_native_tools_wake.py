"""Native terminal bridge delivery preserves wakes without overlapping sessions."""
import base64
import json
import hashlib
import os
import shutil
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from test_wd_reboot_bundle import REBOOT, LANE_TEST_SHELLS, _run_powershell
from test_wd_startup_recovery import load, q
from test_wd_bridge_code_context import HAS_BRIDGE_PYTHON

TOOLS = REBOOT / 'start-wd-tools-consumer.ps1'
THREAD = '01a0a07b-ca98-71e1-90cb-d588435a2d8d'


def notice_registry(bundle):
    path = bundle / 'tools-bootstrap/configs/bridge_identity_registry.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'identities': {'codex-lead-1': 'd3c9d1d1-96a9-4eb8-a8e2-6f05f9d1a101',
                                               'codex-tools-1': '7a8af68d-20bc-4598-9953-23c5dd98b102'}}))
    return {path.relative_to(bundle).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest().upper()}


# Same guard as the _stage_fake_bundle tests in test_wd_bridge_code_context:
# Install-WdBridgePythonSite is Windows-shaped (backslash site prefix) and
# needs the pinned fleet interpreter, so the staged bundle cannot be built on
# Linux with any interpreter (CI 36542115843 at d25ce2ae).
@pytest.mark.skipif(not HAS_BRIDGE_PYTHON,
                    reason='bundle staging is Windows-only and needs the pinned bridge interpreter')
@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_guard_runs_through_real_hash_pinned_bundle_wrapper(tmp_path, ps):
    from test_wd_bridge_code_context import _stage_fake_bundle

    # Real deployment-shaped wrapper/context; unrelated dependency wheel is a
    # fixture. The continuity implementation itself is the real packaged code.
    bundle = _stage_fake_bundle(tmp_path)
    relative = 'tools/bridge_continuity_guard.py'
    guard = bundle / 'tools-bootstrap' / relative
    shutil.copyfile(REBOOT.parents[2] / relative, guard)
    definition_path = bundle / 'bridge-code-files.json'
    definition = json.loads(definition_path.read_text())
    definition['python_files'].append(relative)
    definition['python_entrypoints']['continuity_guard'] = relative
    definition_path.write_text(json.dumps(definition))
    manifest_path = bundle / 'deployment-manifest.json'
    manifest = json.loads(manifest_path.read_text())
    for key, path in [('bridge-code-files.json', definition_path), ('tools-bootstrap/' + relative, guard)]:
        manifest['files'][key] = hashlib.sha256(path.read_bytes()).hexdigest().upper()
    manifest_path.write_text(json.dumps(manifest))
    anchor = hashlib.sha256(manifest_path.read_bytes()).hexdigest().upper()
    script = "$ErrorActionPreference='Stop'\n"
    script += load(TOOLS, 'Invoke-WdContinuityDecision')
    script += f"""
$env:WD_BRIDGE_PYTHON_WRAPPER={q(bundle / 'Invoke-WdBridgePython.ps1')}
$env:WD_REBOOT_EXPECTED_MANIFEST_HASH='{anchor}'
$snapshot=@{{schema='wd.continuity-snapshot.v1';agent='codex-lead-1';
 checkpoint=@{{task_id='work';status='in_progress';next_action='Continue scoped work';next_wakeup_utc=$null;updated_at_utc='2026-09-28T20:00:00Z'}};
 evidence=@{{scope='checkpoint_only';complete=$true;collected_at_utc='2026-09-29T05:00:00Z';source_digest=('a'*64);errors=@()}};
 claims=@();inbound_requests=@();waits=@();events=@();processing=@();cancellations=@();holds=@()}}
Invoke-WdContinuityDecision -Snapshot $snapshot -NowUtc '2026-09-29T05:00:00Z' | ConvertTo-Json -Depth 12 -Compress
"""
    result = json.loads(_run_powershell(script, executable=ps).stdout)
    assert result['verdict'] == 'dispatch', result
    assert result['authority'] == 'none', result


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('case', ['valid', 'missing', 'foreign', 'tampered', 'bad_receipt', 'queued', 'no_hash_module'])
def test_operator_notice_caller_anchors_code_and_bounds_checkpoint_payload(tmp_path, ps, case):
    bundle = tmp_path / 'bundle'
    bundle.mkdir()
    publisher = bundle / 'Send-WdContinuityAlert.ps1'
    capture = tmp_path / 'call.json'
    publisher.write_text(
        'param($Agent,$TaskId,$ThreadId,$Worktree,$Reason,$CheckpointDigest,$ProgressKey)\n'
        f'$PSBoundParameters | ConvertTo-Json -Compress | Set-Content -LiteralPath {q(capture)}\n'
        + ('\'{}\'\n' if case == 'bad_receipt' else
           '\'{"schema":"wd.continuity-alert-result.v1","status":"queued"}\'\n' if case == 'queued' else
           '\'{"schema":"wd.continuity-alert-result.v1","status":"published"}\'\n'),
        encoding='utf-8')
    manifest = bundle / 'deployment-manifest.json'
    manifest.write_text(json.dumps({'files': {publisher.name: hashlib.sha256(publisher.read_bytes()).hexdigest().upper(), **notice_registry(bundle)}}))
    anchor = hashlib.sha256(manifest.read_bytes()).hexdigest().upper()
    if case == 'tampered':
        publisher.write_text('throw "untrusted"')
    audit = tmp_path / '.codex-audit'
    audit.mkdir()
    checkpoint = audit / 'wd-current-state.json'
    if case != 'missing':
        checkpoint.write_text(json.dumps(dict(agent='other' if case == 'foreign' else 'codex-lead-1',
                                             task_id='authorized-task', history='private history must not be sent')))
    script = "$ErrorActionPreference='Stop'\nSet-StrictMode -Version Latest\n"
    script += load(REBOOT / 'Invoke-WdLaneTurnLoop.ps1', 'Assert-WdTurnPath')
    script += load(TOOLS, 'Invoke-WdContinuityOperatorNotice')
    if case == 'no_hash_module':
        script += "function Get-FileHash { throw 'PS5 module unavailable' }\n"
    script += f"""
$env:WD_BRIDGE_PYTHON_WRAPPER={q(bundle / 'Invoke-WdBridgePython.ps1')}
$env:WD_REBOOT_EXPECTED_MANIFEST_HASH='{anchor}'
try {{
 $r=Invoke-WdContinuityOperatorNotice -Agent codex-lead-1 -ThreadId '{THREAD}' -Worktree {q(tmp_path)} -RuntimeRoot {q(tmp_path / 'runtime')} -SessionId fixture-session -ErrorText 'held'
 @{{ok=$true;receipt=$r}} | ConvertTo-Json -Compress
}} catch {{@{{ok=$false;error=$_.Exception.Message}} | ConvertTo-Json -Compress}}
"""
    result = json.loads(_run_powershell(script, executable=ps).stdout)
    assert result['ok'] == (case not in ('tampered', 'bad_receipt')), result
    if case == 'tampered':
        assert not capture.exists()
    else:
        call = json.loads(capture.read_text(encoding='utf-8-sig'))
        assert call['Agent'] == 'codex-lead-1' and call['ThreadId'] == THREAD
        assert 'private history' not in json.dumps(call)
        if case in ('missing', 'foreign'):
            assert call['Reason'] == 'checkpoint_unavailable'
            assert call['CheckpointDigest'] == '0' * 64
            assert call['TaskId'] == 'codex-lead-1/continuity-recovery'
        else:
            assert call['Reason'] == 'hold_possible'
            assert call['CheckpointDigest'] == hashlib.sha256(checkpoint.read_bytes()).hexdigest()


def canonical_progress(value, allow_list=False):
    """Reference for the caller's shell-independent ProgressKey encoding."""
    if value is None:
        return 'null'
    if isinstance(value, str):
        units = value.encode('utf-16-le')
        out = []
        for i in range(0, len(units), 2):
            n = units[i] | (units[i + 1] << 8)
            out.append(chr(n) if 0x20 <= n <= 0x7E and n not in (0x22, 0x5C) else '\\u%04x' % n)
        return '"' + ''.join(out) + '"'
    if allow_list and isinstance(value, list):
        return '[' + ','.join(canonical_progress(item) for item in value) + ']'
    raise TypeError(type(value))


def expected_progress_key(record, fields=('task_id', 'status', 'next_action', 'next_wakeup_utc', 'blockers')):
    try:
        text = '{' + ','.join(canonical_progress(f) + ':' + canonical_progress(record[f], f == 'blockers')
                              for f in fields if f in record) + '}'
    except TypeError:
        return None
    return hashlib.sha256(text.encode('ascii')).hexdigest()


PROGRESS_BASE = dict(agent='codex-tools-1', task_id='codex-lead-1/probe-task', status='in_progress',
                     next_action='Preserve held/cancelled work', next_wakeup_utc=None, blockers=[],
                     updated_at_utc='2026-09-29T05:00:00Z')
PROGRESS_CASES = {
    'base': {},
    'heartbeat_only': dict(updated_at_utc='2026-09-29T06:00:00Z', history='later heartbeat'),
    'blockers_one': dict(blockers=['x']),
    'blockers_scalar': dict(blockers='x'),
    'blockers_null': dict(blockers=None),
    'blockers_missing': dict(blockers=...),
    'blockers_two': dict(blockers=['a', 'b']),
    'wakeup_set': dict(next_wakeup_utc='2026-09-29T07:00:00Z'),
    'wakeup_missing': dict(next_wakeup_utc=...),
    'status_changed': dict(status='waiting'),
    'escapes': dict(next_action='wait <RCO> & don\'t "quote" \\ tab\there'),
    'unicode': dict(next_action='äö — ok \U0001f41d'),
    # JSON numbers/booleans/objects deserialize differently per shell: fail closed, never rounded.
    'reject_float': dict(next_action=0.84551240822557006),
    'reject_int64_overflow': dict(blockers=[9223372036854775808]),
    'reject_uint64_overflow': dict(next_wakeup_utc=18446744073709551616),
    'reject_small_int': dict(status=1),
    'reject_bool': dict(status=True),
    'reject_object': dict(blockers=[{'a': 'b'}]),
    'reject_nested_list': dict(blockers=[['x']]),
    'reject_list_outside_blockers': dict(next_action=['x']),
}
REJECTED = {case for case in PROGRESS_CASES if case.startswith('reject_')}


def progress_record(case):
    record = dict(PROGRESS_BASE)
    for key, value in PROGRESS_CASES[case].items():
        if value is ...:
            record.pop(key)
        else:
            record[key] = value
    return record


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_operator_notice_progress_key_is_canonical_across_shells(tmp_path, ps):
    bundle = tmp_path / 'bundle'
    bundle.mkdir()
    publisher = bundle / 'Send-WdContinuityAlert.ps1'
    publisher.write_text(
        'param($Agent,$TaskId,$ThreadId,$Worktree,$Reason,$CheckpointDigest,$ProgressKey)\n'
        "[IO.File]::WriteAllText((Join-Path $Worktree 'key.txt'), $ProgressKey + ' ' + $Reason + ' ' + $CheckpointDigest)\n"
        '\'{"schema":"wd.continuity-alert-result.v1","status":"published"}\'\n', encoding='utf-8')
    manifest = bundle / 'deployment-manifest.json'
    manifest.write_text(json.dumps({'files': {publisher.name: hashlib.sha256(publisher.read_bytes()).hexdigest().upper(),
                                              **notice_registry(bundle)}}))
    anchor = hashlib.sha256(manifest.read_bytes()).hexdigest().upper()
    script = "$ErrorActionPreference='Stop'\nSet-StrictMode -Version Latest\n"
    script += load(REBOOT / 'Invoke-WdLaneTurnLoop.ps1', 'Assert-WdTurnPath')
    script += load(TOOLS, 'Invoke-WdContinuityOperatorNotice')
    script += f"$env:WD_BRIDGE_PYTHON_WRAPPER={q(bundle / 'Invoke-WdBridgePython.ps1')}\n"
    script += f"$env:WD_REBOOT_EXPECTED_MANIFEST_HASH='{anchor}'\n"
    for case in PROGRESS_CASES:
        worktree = tmp_path / case
        (worktree / '.codex-audit').mkdir(parents=True)
        # BOM-less UTF-8 with raw non-ASCII: Windows PowerShell must not decode it as ANSI.
        (worktree / '.codex-audit/wd-current-state.json').write_text(
            json.dumps(progress_record(case), ensure_ascii=False), encoding='utf-8')
        script += (f"Invoke-WdContinuityOperatorNotice -Agent codex-tools-1 -ThreadId '{THREAD}' "
                   f"-Worktree {q(worktree)} -RuntimeRoot {q(tmp_path / 'runtime')} -SessionId fixture-session "
                   "-ErrorText 'held' | Out-Null\n")
    _run_powershell(script, executable=ps)
    calls = {case: (tmp_path / case / 'key.txt').read_text(encoding='utf-8').split(' ') for case in PROGRESS_CASES}
    keys = {case: call[0] for case, call in calls.items()}
    # Exact reference digests make PowerShell 5.1 and 7 agree by construction.
    assert keys == {case: expected_progress_key(progress_record(case)) or '0' * 64 for case in PROGRESS_CASES}
    for case, (_, reason, digest) in calls.items():
        assert (reason, digest == '0' * 64) == (('checkpoint_unavailable', True) if case in REJECTED
                                               else ('hold_possible', False)), (case, reason)
    assert keys['heartbeat_only'] == keys['base']
    distinct = [case for case in PROGRESS_CASES if case != 'heartbeat_only' and case not in REJECTED]
    assert len({keys[case] for case in distinct}) == len(distinct), keys


def test_lead_imports_continuity_dependencies_from_verified_code():
    source = (REBOOT / 'start-wd-agent.ps1').read_text(encoding='utf-8')
    imports = source.split('$imports = @{', 1)[1].split('foreach ($file', 1)[0]
    for name in ('Invoke-WdContinuityDecision', 'Invoke-WdNativeContinuityStep', 'Test-WdContinuityControlEvents',
                 'Invoke-WdContinuityOperatorNotice', 'Get-WdContinuityRetryDelay'):
        assert f"'{name}'" in imports


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_guard_errors_back_off_but_history_catchup_is_bounded_between_wakes(ps):
    script = load(TOOLS, 'Get-WdContinuityRetryDelay')
    script += "@(Get-WdContinuityRetryDelay 'held'; Get-WdContinuityRetryDelay 'unknown'; Get-WdContinuityRetryDelay 'Continuity canonical scan catching up; recovery withheld') | ConvertTo-Json -Compress"
    assert json.loads(_run_powershell(script, executable=ps).stdout) == [300, 300, 1]
    source = TOOLS.read_text()
    assert 'Get-WdContinuityRetryDelay -ErrorText $_.Exception.Message' in source


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('catching_up', [True, False])
def test_relay_history_catchup_is_not_an_operator_incident(tmp_path, ps, catching_up):
    (tmp_path / '.codex-audit/wd-turn-loop').mkdir(parents=True)
    error = 'Continuity canonical scan catching up; recovery withheld' if catching_up else 'Continuity work held'
    script = "$ErrorActionPreference='Stop'\n$WarningPreference='SilentlyContinue'\n"
    script += load(REBOOT / 'Invoke-WdLaneTurnLoop.ps1', 'Assert-WdTurnPath')
    script += load(TOOLS, 'Get-WdContinuityRetryDelay')
    script += load(TOOLS, 'Invoke-WdNativeToolsWakeRelay')
    script += f"""
$script:iterations=0; $script:wakes=0; $script:notices=0; $script:alerts=0
function Invoke-WdNativeToolsWakeStep {{param($SessionId) if ($SessionId -cne 'launcher-session') {{throw 'Wake step lost launcher session'}}; $script:wakes++}}
function Invoke-WdNativeContinuityStep {{throw '{error}'}}
function Write-WdTurnJson {{$script:alerts++}}
function Invoke-WdContinuityOperatorNotice {{$script:notices++; return @{{status='published'}}}}
$native=[pscustomobject]@{{Id=123;StartTime=[DateTime]::UtcNow.AddMinutes(-3)}}
$native|Add-Member ScriptMethod WaitForExit {{$script:iterations++; return $script:iterations -gt 1}}
Invoke-WdNativeToolsWakeRelay -Native $native -CliPath unused -ThreadId '{THREAD}' -Worktree {q(tmp_path)} -RuntimeRoot {q(tmp_path / 'runtime')} -Generation fixture -ExpectedCliHash ('a'*64) -SessionId launcher-session -WarningAction SilentlyContinue
@{{wakes=$script:wakes;notices=$script:notices;alerts=$script:alerts}}|ConvertTo-Json -Compress
"""
    report = json.loads(_run_powershell(script, executable=ps).stdout)
    assert report == dict(wakes=1, notices=0 if catching_up else 1, alerts=0 if catching_up else 1), report


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('checkpoint_mode', ['missing', 'heartbeat'])
def test_real_operator_publisher_and_caller_publish_once_without_ending_host(tmp_path, ps, checkpoint_mode):
    from test_wd_continuity_alert import MOCK_WRITER

    (tmp_path / 'runtime').mkdir()
    bundle = tmp_path / 'bundle'
    bin_dir = bundle / 'tools-bootstrap/.agent-bridge/bin'
    bin_dir.mkdir(parents=True)
    writer = bin_dir / 'Write-AgentEvent.ps1'
    writer.write_text(MOCK_WRITER, encoding='utf-8')
    publisher = bundle / 'Send-WdContinuityAlert.ps1'
    shutil.copyfile(REBOOT / publisher.name, publisher)
    manifest = bundle / 'deployment-manifest.json'
    manifest.write_text(json.dumps({'files': {**notice_registry(bundle), **{
        path.relative_to(bundle).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest().upper()
        for path in (writer, publisher)}}}))
    anchor = hashlib.sha256(manifest.read_bytes()).hexdigest().upper()
    capture = tmp_path / 'events.jsonl'
    script = "$ErrorActionPreference='Stop'\nSet-StrictMode -Version Latest\n"
    script += load(REBOOT / 'Invoke-WdLaneTurnLoop.ps1', 'Assert-WdTurnPath')
    script += load(TOOLS, 'Invoke-WdContinuityOperatorNotice')
    script += f"""
$env:WD_BRIDGE_PYTHON_WRAPPER={q(bundle / 'Invoke-WdBridgePython.ps1')}
$env:WD_REBOOT_EXPECTED_MANIFEST_HASH='{anchor}'
$env:WD_TEST_ALERT_CAPTURE={q(capture)}
$env:WD_TEST_ALERT_MODE='canonical'
$results=@(1..2 | ForEach-Object {{
 if ('{checkpoint_mode}' -eq 'heartbeat') {{
  [void][IO.Directory]::CreateDirectory({q(tmp_path / '.codex-audit')})
  @{{agent='codex-lead-1';task_id='work';status='in_progress';next_action='same action';next_wakeup_utc='2026-09-29T10:00:00Z';blockers=@();updated_at_utc=[string]$_;history=@($_)}} | ConvertTo-Json | Set-Content {q(tmp_path / '.codex-audit/wd-current-state.json')}
 }}
 Invoke-WdContinuityOperatorNotice -Agent codex-lead-1 -ThreadId '{THREAD}' -Worktree {q(tmp_path)} -RuntimeRoot {q(tmp_path / 'runtime')} -SessionId fixture-session -ErrorText 'checkpoint missing'
}})
@{{alive=$true;results=$results}} | ConvertTo-Json -Depth 10 -Compress
"""
    report = json.loads(_run_powershell(script, executable=ps).stdout)
    assert report['alive'] is True
    assert [r['status'] for r in report['results']] == ['published', 'already_reported'], report
    events = [json.loads(line) for line in capture.read_text(encoding='utf-8-sig').splitlines()]
    assert len(events) == 1
    assert events[0]['To'] == 'operator' and events[0]['Type'] == 'message'
    assert json.loads(events[0]['PayloadJson'])['authority'] == 'none'


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('fails', [False, True])
def test_tools_notice_binds_scrubbed_identity_and_restores_parent_environment(tmp_path, ps, fails):
    bundle = tmp_path / 'bundle'
    bundle.mkdir()
    capture = tmp_path / 'identity.json'
    publisher = bundle / 'Send-WdContinuityAlert.ps1'
    publisher.write_text('param($Agent,$TaskId,$ThreadId,$Worktree,$Reason,$CheckpointDigest,$ProgressKey)\n'
                        f'@{{root=$env:AGENT_BRIDGE_RUNTIME_ROOT;uuid=$env:AGENT_BRIDGE_AGENT_UUID;session=$env:AGENT_BRIDGE_SESSION_ID;run=$env:AGENT_BRIDGE_RUN_ID}}|ConvertTo-Json|Set-Content {q(capture)}\n'
                        + ('throw "fixture failure"' if fails else '\'{"schema":"wd.continuity-alert-result.v1","status":"published"}\''))
    manifest = bundle / 'deployment-manifest.json'
    manifest.write_text(json.dumps({'files': {**notice_registry(bundle), publisher.name: hashlib.sha256(publisher.read_bytes()).hexdigest().upper()}}))
    anchor = hashlib.sha256(manifest.read_bytes()).hexdigest().upper()
    script = "$ErrorActionPreference='Stop'\n"
    script += load(REBOOT / 'Invoke-WdLaneTurnLoop.ps1', 'Assert-WdTurnPath')
    script += load(TOOLS, 'Invoke-WdContinuityOperatorNotice')
    script += f"""
$env:WD_BRIDGE_PYTHON_WRAPPER={q(bundle / 'Invoke-WdBridgePython.ps1')}
$env:WD_REBOOT_EXPECTED_MANIFEST_HASH='{anchor}'
foreach($key in @('AGENT_BRIDGE_RUNTIME_ROOT','AGENT_BRIDGE_AGENT_UUID','AGENT_BRIDGE_SESSION_ID','AGENT_BRIDGE_RUN_ID')) {{Remove-Item -LiteralPath ('Env:'+$key) -ErrorAction SilentlyContinue}}
$env:AGENT_BRIDGE_RUN_ID='parent-run'
try {{Invoke-WdContinuityOperatorNotice -Agent codex-tools-1 -ThreadId '{THREAD}' -Worktree {q(tmp_path)} -RuntimeRoot {q(tmp_path / 'runtime')} -SessionId 'tools-session' -ErrorText 'checkpoint missing' | Out-Null}} catch {{}}
@{{uuid=(Test-Path Env:AGENT_BRIDGE_AGENT_UUID);root=(Test-Path Env:AGENT_BRIDGE_RUNTIME_ROOT);session=(Test-Path Env:AGENT_BRIDGE_SESSION_ID);run=$env:AGENT_BRIDGE_RUN_ID}}|ConvertTo-Json -Compress
"""
    restored = json.loads(_run_powershell(script, executable=ps).stdout)
    observed = json.loads(capture.read_text(encoding='utf-8-sig'))
    assert observed == dict(root=str(tmp_path / 'runtime'), uuid='7a8af68d-20bc-4598-9953-23c5dd98b102', session='tools-session', run='tools-session')
    assert restored == dict(uuid=False, root=False, session=False, run='parent-run')


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('refuse_platform', [False, True])
def test_tools_notice_real_writer_reaches_only_explicit_runtime(tmp_path, ps, refuse_platform):
    bundle = tmp_path / 'bundle'
    bin_dir = bundle / 'tools-bootstrap/.agent-bridge/bin'
    bin_dir.mkdir(parents=True)
    # Isolated files alone do not isolate the Windows production kernel locks.
    # Rewrite literals only in this fixture, never add a runtime bypass.
    mutex_prefix = 'Local\\ContinuityFixture-' + uuid.uuid4().hex + '-'
    for helper in (REBOOT.parents[2] / '.agent-bridge/bin').glob('*.ps1'):
        source = helper.read_text(encoding='utf-8-sig').replace(
            'Global\\WaggleDanceBridge', mutex_prefix)
        if refuse_platform and helper.name == 'Write-AgentEvent.ps1':
            source = source.replace(
                '[Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT', '$true')
        (bin_dir / helper.name).write_text(source, encoding='utf-8-sig')
    registry = notice_registry(bundle)
    publisher = bundle / 'Send-WdContinuityAlert.ps1'
    shutil.copyfile(REBOOT / publisher.name, publisher)
    files = {path.relative_to(bundle).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest().upper()
             for path in [publisher, *bin_dir.glob('*.ps1')]}
    manifest = bundle / 'deployment-manifest.json'
    manifest.write_text(json.dumps({'files': {**files, **registry}}))
    anchor = hashlib.sha256(manifest.read_bytes()).hexdigest().upper()
    # AppendV1's Win32 write-through WAL paths must fit the native path limit.
    # A deeply nested pytest --basetemp can exceed it despite a short event path.
    runtime = REBOOT.parents[2] / '.codex-audit' / ('nr-' + uuid.uuid4().hex[:16])
    runtime.mkdir(parents=True)
    script = "$ErrorActionPreference='Stop'\n"
    script += "Get-ChildItem Env: | Where-Object Name -Match '^(AGENT_BRIDGE_|WD_|CLAUDE_CODE_|GIT_)' | ForEach-Object { Remove-Item -LiteralPath ('Env:'+$_.Name) }\n"
    script += load(REBOOT / 'Invoke-WdLaneTurnLoop.ps1', 'Assert-WdTurnPath')
    script += load(TOOLS, 'Invoke-WdContinuityOperatorNotice')
    script += f"""
$env:WD_BRIDGE_PYTHON_WRAPPER={q(bundle / 'Invoke-WdBridgePython.ps1')}
$env:WD_REBOOT_EXPECTED_MANIFEST_HASH='{anchor}'
Invoke-WdContinuityOperatorNotice -Agent codex-tools-1 -ThreadId '{THREAD}' -Worktree {q(tmp_path)} -RuntimeRoot {q(runtime)} -SessionId tools-fixture -ErrorText 'checkpoint missing' | ConvertTo-Json -Depth 8 -Compress
"""
    report = json.loads(_run_powershell(script, executable=ps).stdout)
    if refuse_platform or os.name != 'nt':
        # AppendV1 intentionally refuses unsupported platforms. Keep that
        # safety fence and prove the caller never upgrades refusal to success.
        assert report['status'] == 'unknown', report
        assert report['reason_code'] == 'delivery_uncertain', report
        assert not (runtime / 'shared/events.jsonl').exists()
        assert not (runtime / 'spool').exists()
        ledger_path = tmp_path / '.codex-audit/wd-turn-loop' / f'continuity-alert-v1-{THREAD}.json'
        before = ledger_path.read_bytes()
        ledger = json.loads(before)
        assert len(ledger['entries']) == 1
        assert ledger['entries'][0]['status'] == 'uncertain'
        retry = json.loads(_run_powershell(script, executable=ps).stdout)
        assert retry['status'] == 'unknown'
        assert retry['reason_code'] == 'delivery_uncertain'
        assert ledger_path.read_bytes() == before
        assert not (runtime / 'shared/events.jsonl').exists()
        return
    assert report['status'] == 'published', report
    events = [json.loads(line) for line in (runtime / 'shared/events.jsonl').read_text(encoding='utf-8-sig').splitlines()]
    assert len(events) == 1
    assert events[0]['agent'] == 'codex-tools-1'
    assert events[0]['agent_uuid'] == '7a8af68d-20bc-4598-9953-23c5dd98b102'
    assert events[0]['session_id'] == 'tools-fixture'
    assert events[0]['to'] == 'operator'
    assert not (bundle / 'tools-bootstrap/.agent-bridge/shared/events.jsonl').exists()


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('case', ['result', 'finding', 'masked_hold', 'global_hold', 'peer_hold', 'foreign_task', 'operator_failure', 'ordinary_wait', 'foreign_broadcast', 'operator_retracted', 'camel_control', 'rewrite_prefix', 'corrupt_log', 'tampered_helper'])
def test_continuity_control_gate_uses_anchored_canonical_reader(tmp_path, ps, case):
    bundle = tmp_path / 'bundle'
    helper_dir = bundle / 'tools-bootstrap/.agent-bridge/bin'
    helper_dir.mkdir(parents=True)
    files = {}
    for leaf in ('BridgeLogReader.ps1', 'BridgeIncrementalReader.ps1'):
        target = helper_dir / leaf
        shutil.copyfile(REBOOT.parents[2] / '.agent-bridge/bin' / leaf, target)
        files[target.relative_to(bundle).as_posix()] = hashlib.sha256(target.read_bytes()).hexdigest().upper()
    manifest = bundle / 'deployment-manifest.json'
    manifest.write_text(json.dumps({'files': files}), encoding='utf-8')
    anchor = hashlib.sha256(manifest.read_bytes()).hexdigest().upper()
    if case == 'tampered_helper':
        (helper_dir / 'BridgeIncrementalReader.ps1').write_text('throw "tampered"')
    runtime = tmp_path / 'runtime'
    (runtime / 'shared').mkdir(parents=True)
    event = dict(agent='claude-rco-2', task_id='work' if case != 'foreign_task' else 'other',
                 type='message' if case == 'result' else 'finding', status='full_suite_result',
                 ts_utc='2026-09-28T22:59:00Z', payload={'notification': 'informational'})
    if case == 'global_hold':
        event.update(agent='operator', task_id='global', status='hold', to='all')
    if case == 'peer_hold':
        event.update(agent='fable-5', status='unsafe')
    if case == 'operator_failure':
        event.update(agent='operator', task_id='other', type='message', status='wake_send_failed', to='')
    if case == 'ordinary_wait':
        event.update(type='message', status='awaiting_review')
    if case == 'foreign_broadcast':
        event.update(agent='codex-lead-1', task_id='other', to='all', type='decision', status='changes_requested')
    if case == 'operator_retracted':
        event.update(agent='operator', task_id='other', to='codex-lead-1', type='decision', status='operator_signed_head_exact_retracted')
    if case == 'camel_control':
        event.update(type='message', status='changesRequested')
    if case == 'rewrite_prefix':
        event.update(type='message', status='notice')
    if case == 'masked_hold':
        event.update(status='changes_requested')
    content = json.dumps(event) + '\n'
    if case == 'masked_hold':
        content += json.dumps(dict(event, type='message', status='in_progress')) + '\n'
    (runtime / 'shared/events.jsonl').write_text('BROKEN\n' if case == 'corrupt_log' else content)
    script = "$ErrorActionPreference='Stop'\nSet-StrictMode -Version Latest\n"
    script += load(REBOOT / 'Invoke-WdLaneTurnLoop.ps1', 'Assert-WdTurnPath')
    script += load(TOOLS, 'Test-WdContinuityControlEvents')
    script += f"""
$env:WD_BRIDGE_PYTHON_WRAPPER={q(bundle / 'Invoke-WdBridgePython.ps1')}
$env:WD_REBOOT_EXPECTED_MANIFEST_HASH='{anchor}'
try {{
 $held=Test-WdContinuityControlEvents -RuntimeRoot {q(runtime)} -TaskId work -Agent codex-lead-1 -CheckpointAt '2026-09-28T22:00:00Z'
 if ('{case}' -eq 'rewrite_prefix') {{
  $log={q(runtime / 'shared/events.jsonl')}
  [IO.File]::WriteAllText($log, [IO.File]::ReadAllText($log).Replace('notice','hold  '))
  $held=Test-WdContinuityControlEvents -RuntimeRoot {q(runtime)} -TaskId work -Agent codex-lead-1
 }}
 @{{ok=$true;held=$held}}|ConvertTo-Json -Compress
}} catch {{@{{ok=$false;error=$_.Exception.Message}}|ConvertTo-Json -Compress}}
"""
    report = json.loads(_run_powershell(script, executable=ps).stdout)
    assert report['ok'] == (case not in ('corrupt_log', 'tampered_helper', 'rewrite_prefix')), report
    if report['ok']:
        assert report['held'] == (case in ('finding', 'masked_hold', 'global_hold', 'peer_hold', 'camel_control')), report


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_control_history_is_incremental_and_cannot_unlatch_a_hold(tmp_path, ps):
    bundle = tmp_path / 'bundle'
    helper_dir = bundle / 'tools-bootstrap/.agent-bridge/bin'
    helper_dir.mkdir(parents=True)
    files = {}
    for leaf in ('BridgeLogReader.ps1', 'BridgeIncrementalReader.ps1'):
        target = helper_dir / leaf
        shutil.copyfile(REBOOT.parents[2] / '.agent-bridge/bin' / leaf, target)
        files[target.relative_to(bundle).as_posix()] = hashlib.sha256(target.read_bytes()).hexdigest().upper()
    manifest = bundle / 'deployment-manifest.json'
    manifest.write_text(json.dumps({'files': files}))
    anchor = hashlib.sha256(manifest.read_bytes()).hexdigest().upper()
    runtime = tmp_path / 'runtime'
    (runtime / 'shared').mkdir(parents=True)
    log = runtime / 'shared/events.jsonl'
    row = json.dumps(dict(agent='peer', task_id='other', to='codex-lead-1', type='finding', status='changes_requested', message='x' * 4000)) + '\n'
    log.write_text(row * 800)
    script = "$ErrorActionPreference='Stop'\nSet-StrictMode -Version Latest\n"
    script += load(REBOOT / 'Invoke-WdLaneTurnLoop.ps1', 'Assert-WdTurnPath')
    script += load(TOOLS, 'Test-WdContinuityControlEvents')
    script += f"""
$env:WD_BRIDGE_PYTHON_WRAPPER={q(bundle / 'Invoke-WdBridgePython.ps1')}
$env:WD_REBOOT_EXPECTED_MANIFEST_HASH='{anchor}'
function Check {{
 try {{@{{ok=$true;held=(Test-WdContinuityControlEvents -RuntimeRoot {q(runtime)} -TaskId work -Agent codex-lead-1)}}}}
 catch {{@{{ok=$false;error=$_.Exception.Message}}}}
}}
$checks=@(1..5 | ForEach-Object {{Check}})
[IO.File]::AppendAllText({q(log)}, '{{"agent":"operator","task_id":"work","type":"decision","status":"hold"}}'+[char]10)
$held=Check
[IO.File]::WriteAllText({q(log)}, '{{"agent":"peer","task_id":"work","type":"message","status":"notice"}}'+[char]10)
$afterTruncate=Check
@{{checks=$checks;held=$held;afterTruncate=$afterTruncate}} | ConvertTo-Json -Depth 8 -Compress
"""
    report = json.loads(_run_powershell(script, executable=ps).stdout)
    assert report['checks'][0]['ok'] is False, report
    assert 'catching up' in report['checks'][0]['error'], report
    assert report['checks'][-1] == {'ok': True, 'held': False}, report
    assert report['held'] == {'ok': True, 'held': True}, report
    assert report['afterTruncate'] == {'ok': True, 'held': True}, report


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('status', ['in_progress', 'operator_signature_pending', 'sentinel_idle', 'handoff'])
def test_real_guard_cli_drives_native_recovery_without_accepting_results(tmp_path, ps, status):
    journal = tmp_path / '.codex-audit/wd-turn-loop'
    journal.mkdir(parents=True)
    checkpoint = dict(schema='wd.lane-current.v1', agent='codex-lead-1', worktree=str(tmp_path),
                      status=status, task_id='legacy-awaiting-suite', next_action='Read RCO2 full-suite result',
                      blockers=[], next_wakeup_utc=None, updated_at_utc='2026-09-28T21:56:00Z')
    (journal.parent / 'wd-current-state.json').write_text(json.dumps(checkpoint))
    guard = REBOOT.parents[2] / 'tools/bridge_continuity_guard.py'
    assert guard.is_file(), 'Fable implementation must be integrated, not stubbed'
    wrapper = tmp_path / 'Invoke-WdBridgePython.ps1'
    wrapper.write_text(
        "[CmdletBinding(PositionalBinding=$false)]\nparam([Parameter(Position=0)][string]$Tool,"
        "[Parameter(ValueFromRemainingArguments)][string[]]$ToolArguments)\n"
        f"& {q(sys.executable)} {q(guard)} @ToolArguments\n"
        "$global:LASTEXITCODE=$LASTEXITCODE\n", encoding='utf-8')
    cli = tmp_path / 'queue-cli.bin'
    cli.write_bytes(b'test fixture, not executable')
    cli_hash = hashlib.sha256(cli.read_bytes()).hexdigest().upper()
    script = "$ErrorActionPreference='Stop'\nSet-StrictMode -Version Latest\n"
    for name in ('Assert-WdTurnPath', 'Write-WdTurnJson'):
        script += load(REBOOT / 'Invoke-WdLaneTurnLoop.ps1', name)
    for name in ('Invoke-WdContinuityDecision', 'Invoke-WdNativeContinuityStep'):
        script += load(TOOLS, name)
    script += f"""
$env:WD_BRIDGE_PYTHON_WRAPPER={q(wrapper)}
$script:calls=0
function Test-WdContinuityControlEvents {{param($RuntimeRoot,$TaskId) return $false}}
function Send-WdNativeToolsQueueMessage {{
 param($CliPath,$ThreadId,$Message,$Worktree)
 if ($Message -notmatch 'Nothing here says a dependency completed; nothing is accepted') {{throw 'unsafe wake'}}
 $script:calls++; return 'queue-id'
}}
$errors=@()
1..2 | ForEach-Object {{
 try {{Invoke-WdNativeContinuityStep -CliPath {q(cli)} -ThreadId '{THREAD}' -Worktree {q(tmp_path)} `
 -Generation pinned -Agent codex-lead-1 -ExpectedCliHash '{cli_hash}' -Now '2026-09-29T05:00:00Z' | Out-Null}}
 catch {{$errors += $_.Exception.Message}}
}}
@{{calls=$script:calls;errors=$errors}} | ConvertTo-Json -Compress
"""
    report = json.loads(_run_powershell(script, executable=ps).stdout)
    assert report['calls'] == (1 if status == 'in_progress' else 0), report
    if status == 'in_progress':
        assert not report['errors'], report


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('case', ['dispatch', 'rollover', 'stale_session', 'startup_grace', 'retry', 'wait', 'hold', 'idle_ok', 'unknown', 'uncertain', 'bad_identity'])
def test_work_bound_guard_queues_once_or_fails_closed(tmp_path, ps, case):
    journal = tmp_path / '.codex-audit/wd-turn-loop'
    journal.mkdir(parents=True)
    checkpoint = dict(schema='wd.lane-current.v1', agent='codex-lead-1',
                      worktree=str(tmp_path), status='in_progress', task_id='authorized-task',
                      next_action='continue scoped work', next_wakeup_utc=None,
                      updated_at_utc='2026-09-28T20:00:00Z')
    if case == 'bad_identity':
        checkpoint['agent'] = 'codex-tools-1'
    (journal.parent / 'wd-current-state.json').write_text(json.dumps(checkpoint))
    ledger = journal / f'continuity-v1-{THREAD}.json'
    cli = tmp_path / 'queue-cli.bin'
    cli.write_bytes(b'non-executable test double')
    cli_hash = hashlib.sha256(cli.read_bytes()).hexdigest().upper()
    script = "$ErrorActionPreference='Stop'\nSet-StrictMode -Version Latest\n"
    for name in ['Assert-WdTurnPath', 'Write-WdTurnJson']:
        script += load(REBOOT / 'Invoke-WdLaneTurnLoop.ps1', name)
    script += load(TOOLS, 'Invoke-WdNativeContinuityStep')
    verdict = case if case in ('wait', 'hold', 'idle_ok', 'unknown') else 'dispatch'
    script += f"""
$script:calls=0
function Test-WdContinuityControlEvents {{param($RuntimeRoot,$TaskId) return $false}}
function Invoke-WdContinuityDecision {{
 param($Snapshot,$NowUtc)
 if($Snapshot.evidence.scope -cne 'checkpoint_only') {{throw 'false evidence completeness'}}
 return [pscustomobject]@{{schema='wd.continuity-decision.v1';agent='codex-lead-1';
 authority='none';verdict='{verdict}';target='codex-lead-1';action_key=('a'*64);reasons=@('fixture')}}
}}
function Send-WdNativeToolsQueueMessage {{
 param($CliPath,$ThreadId,$Message,$Worktree)
 $script:calls++
 if(($ThreadId -cne '{THREAD}' -and '{case}' -ne 'rollover') -or $Message -notmatch 'not a new assignment') {{throw 'unsafe routing'}}
 if('{case}' -eq 'uncertain') {{throw 'ambiguous queue timeout'}}
 return 'queue-id'
}}
$errors=@()
$sessionStart=if ('{case}' -eq 'stale_session') {{'2026-09-29T04:57:00Z'}} elseif ('{case}' -eq 'startup_grace') {{'2026-09-29T04:59:00Z'}} else {{'2026-09-28T00:00:00Z'}}
1..2 | ForEach-Object {{
 try {{Invoke-WdNativeContinuityStep -CliPath {q(cli)} -ThreadId '{THREAD}' -Worktree {q(tmp_path)} `
 -Generation pinned -Agent codex-lead-1 -ExpectedCliHash '{cli_hash}' -SessionStartedAt $sessionStart -Now '2026-09-29T05:00:00Z' | Out-Null}}
 catch {{$errors += $_.Exception.Message}}
}}
if ('{case}' -eq 'retry') {{
 foreach ($time in @('2026-09-29T06:00:00Z','2026-09-29T07:00:00Z')) {{
  try {{Invoke-WdNativeContinuityStep -CliPath {q(cli)} -ThreadId '{THREAD}' -Worktree {q(tmp_path)} `
   -Generation pinned -Agent codex-lead-1 -ExpectedCliHash '{cli_hash}' -Now $time | Out-Null}}
  catch {{$errors += $_.Exception.Message}}
 }}
}}
if ('{case}' -eq 'rollover') {{
 try {{Invoke-WdNativeContinuityStep -CliPath {q(cli)} -ThreadId '11a0a07b-ca98-71e1-90cb-d588435a2d8d' -Worktree {q(tmp_path)} `
 -Generation next -Agent codex-lead-1 -ExpectedCliHash '{cli_hash}' -Now '2026-09-29T05:01:00Z' | Out-Null}}
 catch {{$errors += $_.Exception.Message}}
}}
@{{calls=$script:calls;errors=$errors}} | ConvertTo-Json -Compress
"""
    report = json.loads(_run_powershell(script, executable=ps).stdout)
    assert report['calls'] == (2 if case == 'rollover' else 1 if case in ('dispatch', 'retry', 'uncertain') else 0), report
    assert len(report['errors']) == (2 if case in ('retry', 'uncertain', 'unknown', 'hold', 'bad_identity', 'stale_session') else 0), report
    if case in ('dispatch', 'rollover', 'retry', 'uncertain'):
        state = json.loads(ledger.read_text(encoding='utf-8-sig'))
        assert len(state['entries']) == 1
        assert state['entries'][0]['status'] == ('submitting' if case == 'uncertain' else 'queued')
    else:
        assert not ledger.exists()


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('fields', [{}, {'work_held': False, 'release_held': True}, {'work_held': True},
                                    {'work_held': 'true', 'release_held': None},
                                    {'Work_Held': True, 'Release_Held': True}])
def test_checkpoint_collector_passes_hold_fields_verbatim_by_exact_name(tmp_path, ps, fields):
    (tmp_path / '.codex-audit/wd-turn-loop').mkdir(parents=True)
    checkpoint = dict(schema='wd.lane-current.v1', agent='codex-lead-1', worktree=str(tmp_path),
                      status='in_progress', task_id='authorized-task', next_action='continue scoped work',
                      next_wakeup_utc=None, updated_at_utc='2026-09-28T20:00:00Z', **fields)
    (tmp_path / '.codex-audit/wd-current-state.json').write_text(json.dumps(checkpoint))
    cli = tmp_path / 'queue-cli.bin'
    cli.write_bytes(b'non-executable test double')
    cli_hash = hashlib.sha256(cli.read_bytes()).hexdigest().upper()
    script = "$ErrorActionPreference='Stop'\nSet-StrictMode -Version Latest\n"
    for name in ['Assert-WdTurnPath', 'Write-WdTurnJson']:
        script += load(REBOOT / 'Invoke-WdLaneTurnLoop.ps1', name)
    script += load(TOOLS, 'Invoke-WdNativeContinuityStep')
    script += f"""
$script:seen=$null
function Test-WdContinuityControlEvents {{param($RuntimeRoot,$TaskId) return $false}}
function Invoke-WdContinuityDecision {{
 param($Snapshot,$NowUtc)
 $script:seen=$Snapshot.checkpoint
 return [pscustomobject]@{{schema='wd.continuity-decision.v1';agent='codex-lead-1';
 authority='none';verdict='wait';target='codex-lead-1';action_key=('a'*64);reasons=@('fixture')}}
}}
function Send-WdNativeToolsQueueMessage {{throw 'no queue call expected'}}
$null=Invoke-WdNativeContinuityStep -CliPath {q(cli)} -ThreadId '{THREAD}' -Worktree {q(tmp_path)} `
 -Generation pinned -Agent codex-lead-1 -ExpectedCliHash '{cli_hash}' -SessionStartedAt '2026-09-28T00:00:00Z' -Now '2026-09-29T05:00:00Z'
$script:seen | ConvertTo-Json -Compress
"""
    seen = json.loads(_run_powershell(script, executable=ps).stdout)
    # Exact names pass verbatim (the guard judges the values); mis-cased names never pass.
    expected = {key: value for key, value in fields.items() if key in ('work_held', 'release_held')}
    assert {key: value for key, value in seen.items() if key.lower() in ('work_held', 'release_held')} == expected


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_native_recovery_progress_hash_is_canonical_across_shells(tmp_path, ps):
    cli = tmp_path / 'queue-cli.bin'
    cli.write_bytes(b'non-executable test double')
    cli_hash = hashlib.sha256(cli.read_bytes()).hexdigest().upper()
    # The native step reads next_wakeup_utc directly (the state writer always emits it).
    cases = ('base', 'heartbeat_only', 'status_changed', 'wakeup_set', 'escapes', 'unicode')
    script = "$ErrorActionPreference='Stop'\nSet-StrictMode -Version Latest\n"
    for name in ['Assert-WdTurnPath', 'Write-WdTurnJson']:
        script += load(REBOOT / 'Invoke-WdLaneTurnLoop.ps1', name)
    script += load(TOOLS, 'Invoke-WdNativeContinuityStep')
    script += """
function Test-WdContinuityControlEvents {param($RuntimeRoot,$TaskId) return $false}
function Invoke-WdContinuityDecision {
 param($Snapshot,$NowUtc)
 return [pscustomobject]@{schema='wd.continuity-decision.v1';agent='codex-lead-1';
 authority='none';verdict='dispatch';target='codex-lead-1';action_key=('a'*64);reasons=@('fixture')}
}
function Send-WdNativeToolsQueueMessage {param($CliPath,$ThreadId,$Message,$Worktree) return 'queue-id'}
"""
    for case in cases:
        worktree = tmp_path / case
        (worktree / '.codex-audit/wd-turn-loop').mkdir(parents=True)
        record = progress_record(case)
        record.update(schema='wd.lane-current.v1', agent='codex-lead-1', worktree=str(worktree))
        # BOM-less UTF-8 with raw non-ASCII, as the state writer produces it.
        (worktree / '.codex-audit/wd-current-state.json').write_text(json.dumps(record, ensure_ascii=False),
                                                                     encoding='utf-8')
        script += (f"Invoke-WdNativeContinuityStep -CliPath {q(cli)} -ThreadId '{THREAD}' -Worktree {q(worktree)} "
                   f"-Generation pinned -Agent codex-lead-1 -ExpectedCliHash '{cli_hash}' "
                   "-SessionStartedAt '2026-09-28T00:00:00Z' -Now '2026-09-29T05:00:00Z' | Out-Null\n")
    rejected = ('reject_float', 'reject_bool', 'reject_list_outside_blockers')
    for case in rejected:
        worktree = tmp_path / case
        (worktree / '.codex-audit/wd-turn-loop').mkdir(parents=True)
        record = progress_record(case)
        record.update(schema='wd.lane-current.v1', agent='codex-lead-1', worktree=str(worktree))
        (worktree / '.codex-audit/wd-current-state.json').write_text(json.dumps(record), encoding='utf-8')
        script += (f"$outcome = try {{ Invoke-WdNativeContinuityStep -CliPath {q(cli)} -ThreadId '{THREAD}' "
                   f"-Worktree {q(worktree)} -Generation pinned -Agent codex-lead-1 -ExpectedCliHash '{cli_hash}' "
                   "-SessionStartedAt '2026-09-28T00:00:00Z' -Now '2026-09-29T05:00:00Z'; 'accepted' } "
                   "catch { $_.Exception.Message }\n"
                   f"[IO.File]::WriteAllText({q(worktree / 'outcome.txt')}, [string]$outcome)\n")
    _run_powershell(script, executable=ps)
    for case in rejected:
        assert (tmp_path / case / 'outcome.txt').read_text() == 'Continuity progress value type unsupported'
        assert not (tmp_path / case / f'.codex-audit/wd-turn-loop/continuity-v1-{THREAD}.json').exists()
    hashes = {}
    for case in cases:
        ledger = tmp_path / case / f'.codex-audit/wd-turn-loop/continuity-v1-{THREAD}.json'
        entries = json.loads(ledger.read_text(encoding='utf-8-sig'))['entries']
        assert len(entries) == 1 and entries[0]['status'] == 'queued', entries
        prefix = 'codex-lead-1:' + 'a' * 64 + ':'
        assert entries[0]['key'].startswith(prefix)
        hashes[case] = entries[0]['key'][len(prefix):]
    fields = ('task_id', 'status', 'next_action', 'next_wakeup_utc')
    assert hashes == {case: expected_progress_key(progress_record(case), fields) for case in cases}
    assert hashes['heartbeat_only'] == hashes['base']
    assert len({hashes[case] for case in cases if case != 'heartbeat_only'}) == len(cases) - 1, hashes


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('case', ['idle', 'accepted', 'new_wake', 'failed', 'uncertain', 'foreign', 'orphan', 'debounce',
                                  'outstanding', 'outstanding_old', 'consumed', 'foreign_receipt', 'legacy',
                                  'rejected', 'rejected_retry', 'rejected_backoff', 'miscased_receipt',
                                  'linked_receipt', 'oversized_receipt', 'stale_receipt', 'cached_then_consumed'])
def test_native_wake_delivery_and_crash_boundaries(tmp_path, ps, case):
    state_path = tmp_path / 'native-bridge-wake.json'
    wake = tmp_path / 'wake_codex-tools-1'
    snapshot = Path(str(state_path) + '.wake')
    owned = '5' * 32  # an attempt-bound snapshot id (RCO1 c97)
    named = Path(str(state_path) + '.wake.' + owned)
    if case != 'idle':
        wake.write_text('wake before delivery')
    ago = dict(outstanding=60, outstanding_old=600, consumed=60, foreign_receipt=60, legacy=60,
               rejected_retry=60, rejected_backoff=5, miscased_receipt=60, linked_receipt=60, oversized_receipt=60,
               stale_receipt=60, cached_then_consumed=60)
    delivery = '0123456789abcdef' * 2
    if case in ('uncertain', 'foreign', 'debounce') or case in ago:
        stamp = ((datetime.now(timezone.utc) - timedelta(seconds=ago[case])).isoformat() if case in ago
                 else '2099-01-01T00:00:00Z')
        status = 'submitting' if case == 'uncertain' else 'rejected' if case.startswith('rejected_') else 'queued'
        state = dict(schema='wd.native-tools-wake.v1', status=status, rejections=1, delivery_id=delivery,
                     thread_id=THREAD if case != 'foreign' else 'other-thread', updated_at_utc=stamp)
        if case != 'legacy':
            state['receipt'] = 'model_turn_started'
        if case.startswith('rejected_'):
            state['snapshot_id'] = owned
        state_path.write_text(json.dumps(state))
    if case in ('uncertain', 'orphan'):  # a fixed-name snapshot: submitting or no record, both blocked
        snapshot.write_text('evidence')
    if case in ('rejected_retry', 'rejected_backoff'):
        named.write_text('evidence')
    # Only the woken conversation's own receipt for the outstanding delivery releases the hold.
    receipts = dict(
        consumed=[('model_turn_started', 'codex-tools-1', delivery, 'agent_reported')],
        foreign_receipt=[('relay_enqueued', 'codex-tools-1', delivery, 'runtime_observed'),
                         ('model_turn_started', 'codex-tools-1', 'f' * 32, 'agent_reported'),
                         ('model_turn_started', 'codex-lead-1', delivery, 'agent_reported')],
        outstanding_old=[('model_turn_started', 'codex-tools-1', delivery, 'runtime_observed')],
        # Links, mis-cased keys, oversized or stale files never count; a cached miss never hides a later receipt.
        **{name: [('model_turn_started', 'codex-tools-1', delivery, 'agent_reported')]
           for name in ('miscased_receipt', 'linked_receipt', 'oversized_receipt', 'stale_receipt')},
        cached_then_consumed=[('model_turn_started', 'codex-lead-1', delivery, 'agent_reported')])
    telemetry = tmp_path / 'shared' / 'telemetry'
    for index, (stage, target, delivery_id, source) in enumerate(receipts.get(case, [])):
        telemetry.mkdir(parents=True, exist_ok=True)
        (telemetry / f'stage-{index:032x}.json').write_text(json.dumps(dict(
            schema='wd.bridge-stage.v1', stage=stage, target=target, delivery_id=delivery_id,
            observation_source=source)))
    first_receipt = telemetry / ('stage-' + '0' * 32 + '.json')
    late_receipt = json.dumps(dict(schema='wd.bridge-stage.v1', stage='model_turn_started', target='codex-tools-1',
                                   delivery_id=delivery, observation_source='agent_reported'))
    if case == 'miscased_receipt':
        first_receipt.write_text(first_receipt.read_text().replace('"delivery_id"', '"Delivery_Id"'))
    if case == 'oversized_receipt':
        first_receipt.write_text(json.dumps(dict(json.loads(first_receipt.read_text()), pad='x' * 65536)))
    if case == 'stale_receipt':
        old = (datetime.now(timezone.utc) - timedelta(seconds=600)).timestamp()
        os.utime(first_receipt, (old, old))
    if case == 'linked_receipt':
        target = first_receipt.replace(tmp_path / 'receipt-target.json')
        try:
            os.symlink(target, first_receipt)
        except OSError:
            pytest.skip('creating a symbolic link needs a privilege this host lacks')
    if case == 'outstanding_old':
        # A lane checkpoint write is not consumption.
        (tmp_path / '.codex-audit').mkdir(exist_ok=True)
        (tmp_path / '.codex-audit' / 'wd-current-state.json').write_text('{}')
    script = "$ErrorActionPreference='Stop'\nSet-StrictMode -Version Latest\n"
    for name in ['Assert-WdTurnPath', 'Write-WdTurnJson', 'Move-WdWakeSnapshot']:
        script += load(REBOOT / 'Invoke-WdLaneTurnLoop.ps1', name)
    script += load(TOOLS, 'Invoke-WdNativeToolsWakeStep')
    script += load(TOOLS, 'Get-WdVerifiedNativeWakeMessage')
    script += load(TOOLS, 'Get-WdInlineNativeWakeMessage')
    from test_wd_native_wake_prompt import relay_bundle_setup
    script += relay_bundle_setup(tmp_path)
    script += f"""
$script:calls=0
function Send-WdNativeToolsQueueMessage {{
 param($CliPath,$ThreadId,$Message,$Worktree)
 $script:calls++
 if($ThreadId -cne '{THREAD}' -or $Message -notmatch 'WAKE_PROCEDURE_TOOLS.md') {{throw 'bad routing'}}
 if('{case}' -eq 'new_wake') {{[IO.File]::WriteAllText({q(wake)},'new concurrent wake')}}
 if('{case}' -eq 'failed') {{throw 'uncertain queue failure'}}
 if('{case}' -eq 'rejected') {{throw 'Codex queue rejected the submission; nothing was queued: Error: failed to queue session message: thread/queue/add failed: queue cannot contain more than 100 submissions (code -32600)'}}
 return '01a0adff-4558-7e80-8936-6aad0d6df821'
}}
try {{
 if('{case}' -eq 'cached_then_consumed') {{
  $first=Invoke-WdNativeToolsWakeStep -CliPath unused -ThreadId '{THREAD}' -Worktree {q(tmp_path)} `
  -WakePath {q(wake)} -StatePath {q(state_path)} -Generation pinned -NativePid 123
  if($first -cne 'outstanding' -or $script:calls) {{throw ('first poll ' + $first)}}
  [IO.File]::WriteAllText({q(telemetry / ('stage-' + 'f' * 32 + '.json'))},{q(late_receipt)})
 }}
 $result=Invoke-WdNativeToolsWakeStep -CliPath unused -ThreadId '{THREAD}' -Worktree {q(tmp_path)} `
 -WakePath {q(wake)} -StatePath {q(state_path)} -Generation pinned -NativePid 123
 @{{ok=$true;result=$result;calls=$script:calls}}|ConvertTo-Json
}} catch {{ @{{ok=$false;error=$_.Exception.Message;calls=$script:calls}}|ConvertTo-Json }}
"""
    result = json.loads(_run_powershell(script, executable=ps).stdout)
    delivered = ('accepted', 'new_wake', 'consumed', 'legacy', 'rejected_retry', 'cached_then_consumed')
    held = ('outstanding', 'outstanding_old', 'foreign_receipt', 'miscased_receipt', 'linked_receipt',
            'oversized_receipt', 'stale_receipt')
    assert result['ok'] == (case not in ('failed', 'uncertain', 'foreign', 'orphan', 'rejected')), result
    assert result['calls'] == (1 if case in delivered + ('failed', 'rejected') else 0)
    if case in delivered:
        state = json.loads(state_path.read_text(encoding='utf-8-sig'))
        assert state['status'] == 'queued' and state['thread_id'] == THREAD
        assert state['task_completion_verified'] is False and state['receipt'] == 'model_turn_started'
        assert len(state['delivery_id']) == 32 and state['delivery_id'] != delivery
        assert not snapshot.exists() and not list(tmp_path.glob('native-bridge-wake.json.wake.*'))
        assert wake.exists() == (case in ('new_wake', 'rejected_retry'))
    if case in held:
        state = json.loads(state_path.read_text(encoding='utf-8-sig'))
        assert result['result'] == 'outstanding' and wake.read_text() == 'wake before delivery'
        assert state['status'] == 'queued' and state['delivery_id'] == delivery and not snapshot.exists()
    if case == 'failed':
        assert len(list(tmp_path.glob('native-bridge-wake.json.wake.*'))) == 1 and not wake.exists()
        assert json.loads(state_path.read_text(encoding='utf-8-sig'))['status'] == 'submitting'
    if case == 'rejected':  # a refusal message without the completed call's evidence stays unresolved
        state = json.loads(state_path.read_text(encoding='utf-8-sig'))
        (moved,) = tmp_path.glob('native-bridge-wake.json.wake.*')
        assert state['status'] == 'submitting' and moved.name.endswith('.' + state['snapshot_id'])
        assert moved.read_text() == 'wake before delivery' and not wake.exists()
    if case == 'rejected_backoff':
        assert result['result'] == 'rejected_backoff' and wake.exists() and named.read_text() == 'evidence'
    if case in ('uncertain', 'orphan'):
        assert snapshot.read_text() == 'evidence' and wake.exists()
    if case in ('foreign', 'debounce'):
        assert wake.exists()


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('case', ['receipt_then_state', 'crash_after_receipt', 'no_evidence', 'ambiguous_evidence', 'ambiguous'])
def test_only_a_reclassified_completed_refusal_writes_a_receipt_and_rejected(tmp_path, ps, case):
    state_path = tmp_path / 'native-bridge-wake.json'
    wake = tmp_path / 'wake_codex-tools-1'
    wake.write_text('wake before delivery')
    refusal = ('Error: failed to queue session message: thread/queue/add failed: '
               'queue cannot contain more than 100 submissions (code -32600)')
    source = TOOLS.read_text(encoding='utf-8')
    start = source.index('    function Get-WdNativeQueueOutcome {')
    classifier = source[start:source.index('\n    }\n', start) + 6]  # the real nested classifier the Step re-reads
    script = "$ErrorActionPreference='Stop'\nSet-StrictMode -Version Latest\n"
    for name in ['Assert-WdTurnPath', 'Write-WdTurnJson', 'Move-WdWakeSnapshot']:
        script += load(REBOOT / 'Invoke-WdLaneTurnLoop.ps1', name)
    script += load(TOOLS, 'Invoke-WdNativeToolsWakeStep')
    script += load(TOOLS, 'Get-WdVerifiedNativeWakeMessage')
    script += load(TOOLS, 'Get-WdInlineNativeWakeMessage')
    from test_wd_native_wake_prompt import relay_bundle_setup
    script += relay_bundle_setup(tmp_path)
    stdout = 'noise' if case == 'ambiguous_evidence' else ''
    script += f"""
$real=${{function:Write-WdTurnJson}}
$script:atWrite=-1
function Write-WdTurnJson {{
 if($args[1].status -ceq 'rejected') {{
  $script:atWrite=@([IO.Directory]::GetFiles({q(tmp_path)},'native-bridge-wake.json.refusal-*')).Count
  if('{case}' -eq 'crash_after_receipt') {{throw 'simulated crash before the rejected state'}}
 }}
 & $real @args
}}
function Send-WdNativeToolsQueueMessage {{
 param($CliPath,$ThreadId,$Message,$Worktree)
{classifier}
 if('{case}' -eq 'ambiguous') {{throw 'Codex queue did not confirm exact-thread delivery: {refusal}'}}
 $refusal=[InvalidOperationException]::new('Codex queue rejected the submission; nothing was queued: {refusal}')
 if('{case}' -ne 'no_evidence') {{$refusal.Data['wd_exit_code']=1;$refusal.Data['wd_stdout']='{stdout}';$refusal.Data['wd_stderr']='{refusal}'+[char]10}}
 throw $refusal
}}
try {{
 $result=Invoke-WdNativeToolsWakeStep -CliPath unused -ThreadId '{THREAD}' -Worktree {q(tmp_path)} `
 -WakePath {q(wake)} -StatePath {q(state_path)} -Generation pinned -NativePid 123
 @{{ok=$true;result=$result;at_write=$script:atWrite}}|ConvertTo-Json
}} catch {{ @{{ok=$false;error=$_.Exception.Message;at_write=$script:atWrite}}|ConvertTo-Json }}
"""
    result = json.loads(_run_powershell(script, executable=ps).stdout)
    state = json.loads(state_path.read_text(encoding='utf-8-sig'))
    receipts = sorted(tmp_path.glob('native-bridge-wake.json.refusal-*'))
    (named,) = tmp_path.glob('native-bridge-wake.json.wake.*')
    assert named.name == 'native-bridge-wake.json.wake.' + state['snapshot_id'] and not wake.exists()
    assert named.read_text() == 'wake before delivery'  # the claimed wake is kept byte-exact in every case
    if case in ('ambiguous', 'no_evidence', 'ambiguous_evidence'):  # never a refusal: submitting, no receipt, no retry
        assert not result['ok'] and state['status'] == 'submitting' and not receipts and result['at_write'] == -1
    elif case == 'crash_after_receipt':
        assert not result['ok'] and state['status'] == 'submitting' and len(receipts) == 1
        assert receipts[0].name == 'native-bridge-wake.json.refusal-' + state['delivery_id']
        receipt = json.loads(receipts[0].read_text(encoding='utf-8'))
        assert {key: receipt[key] for key in ('schema', 'agent', 'thread_id', 'generation', 'native_pid', 'delivery_id',
                                              'snapshot_id', 'outcome', 'exit_code', 'stdout', 'stderr', 'code', 'cap')} == dict(
            schema='wd.native-queue-refusal.v1', agent='codex-tools-1', thread_id=THREAD, generation='pinned',
            native_pid=123, delivery_id=state['delivery_id'], snapshot_id=state['snapshot_id'], outcome='rejected',
            exit_code=1, stdout='', stderr=refusal + '\n', code=-32600, cap=100)
        assert receipt['relay_pid'] == state['relay_pid'] and receipt['completed_at_utc']
        assert receipt['stderr_sha256'] == hashlib.sha256((refusal + '\n').encode()).hexdigest().upper()
        assert receipt['stdout_sha256'] == hashlib.sha256(b'').hexdigest().upper()
    else:  # the rejected state now holds the refusal and keeps the named snapshot for the relay retry
        assert result['ok'] and result['result'] == 'rejected' and state['status'] == 'rejected'
        assert result['at_write'] == 1 and not receipts and state['rejections'] == 1


C97 = {'crash_after_claim': (True, 'queued', 1), 'crash_after_move': (True, 'queued', 1),
       'crash_after_move_new_wake': (True, 'queued', 1), 'claiming_nothing': (True, 'idle', 0),
       'crash_after_submitting': (False, 'unresolved', 0), 'confirmed_cleared': (True, 'queued', 1),
       'identical_bytes_unowned': (False, 'Unowned native bridge wake snapshot', 0), 'legacy_queued_fixed': (True, 'queued', 1),
       'legacy_fixed_other': (False, 'Legacy native bridge wake snapshot', 0),
       'orphan_named': (False, 'Unowned native bridge wake snapshot', 0),
       'claiming_without_id': (False, 'claim has no snapshot id', 0), 'rejected_owned_retry': (True, 'queued', 1),
       'move_blocked': (True, 'retry_snapshot', 0), 'watching_owned': (False, 'unexpected status', 0)}
# Only Windows refuses the move while another handle holds the wake file without delete sharing; POSIX renames an
# open file (pwsh on Linux CI, 2026-10-01), so off Windows move_blocked has no blocked move to observe.
WINDOWS_SHARING = pytest.mark.skipif(os.name != 'nt', reason='a move blocked by an open handle is Windows file sharing')


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('case', [pytest.param(case, marks=WINDOWS_SHARING) if case == 'move_blocked' else case
                                  for case in sorted(C97)])
def test_named_snapshot_crash_twins(tmp_path, ps, case):
    # RCO1 c97 twins: each writes the on-disk state a crash leaves, then runs ONE step.
    state_path = tmp_path / 'native-bridge-wake.json'
    wake = tmp_path / 'wake_codex-tools-1'
    w, x = '5' * 32, '6' * 32
    owned = Path(str(state_path) + '.wake.' + w)
    old_delivery = '0123456789abcdef' * 2
    stamp = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()

    def record(status, **extra):
        fields = dict(schema='wd.native-tools-wake.v1', status=status, thread_id=THREAD,
                      agent='codex-tools-1', delivery_id=old_delivery, queue_id='', rejections=0,
                      updated_at_utc=stamp)
        fields.update(extra)
        state_path.write_text(json.dumps(fields))

    if case in ('crash_after_claim', 'move_blocked'):
        record('claiming', snapshot_id=w)
        wake.write_text('claimed wake')
    elif case in ('crash_after_move', 'crash_after_move_new_wake'):
        record('claiming', snapshot_id=w)
        owned.write_text('claimed wake')
        if case.endswith('new_wake'):
            wake.write_text('newer wake')
    elif case == 'claiming_nothing':
        record('claiming', snapshot_id=w)
    elif case == 'crash_after_submitting':
        record('submitting', snapshot_id=w)
        owned.write_text('claimed wake')
    elif case == 'confirmed_cleared':
        record('queued', snapshot_id=w, receipt='model_turn_started')
        owned.write_text('delivered wake')
        wake.write_text('next wake')
        telemetry = tmp_path / 'shared' / 'telemetry'
        telemetry.mkdir(parents=True)
        (telemetry / ('stage-' + '0' * 32 + '.json')).write_text(json.dumps(dict(
            schema='wd.bridge-stage.v1', stage='model_turn_started', target='codex-tools-1', delivery_id=old_delivery,
            observation_source='agent_reported')))
    elif case == 'identical_bytes_unowned':
        record('queued', snapshot_id=w, receipt='model_turn_started')
        Path(str(state_path) + '.wake.' + x).write_text('delivered wake')
    elif case == 'legacy_queued_fixed':
        record('queued')
        Path(str(state_path) + '.wake').write_text('legacy wake')
        wake.write_text('next wake')
    elif case == 'legacy_fixed_other':
        record('rejected', rejections=1)
        Path(str(state_path) + '.wake').write_text('legacy wake')
    elif case == 'orphan_named':
        owned.write_text('orphan wake')
    elif case == 'claiming_without_id':
        record('claiming')
        wake.write_text('wake')
    elif case == 'rejected_owned_retry':
        record('rejected', snapshot_id=w, rejections=1)
        owned.write_text('refused wake')
        wake.write_text('newer wake')
    else:  # watching_owned: a new claim would strand the owned file (RCO1 preflight rule)
        record('watching', snapshot_id=w)
        owned.write_text('claimed wake')
        wake.write_text('newer wake')
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir() if path.is_file()}
    script = "$ErrorActionPreference='Stop'\nSet-StrictMode -Version Latest\n"
    for name in ['Assert-WdTurnPath', 'Write-WdTurnJson', 'Move-WdWakeSnapshot']:
        script += load(REBOOT / 'Invoke-WdLaneTurnLoop.ps1', name)
    script += load(TOOLS, 'Invoke-WdNativeToolsWakeStep')
    script += load(TOOLS, 'Get-WdVerifiedNativeWakeMessage')
    script += load(TOOLS, 'Get-WdInlineNativeWakeMessage')
    from test_wd_native_wake_prompt import relay_bundle_setup
    script += relay_bundle_setup(tmp_path)
    script += f"""
$script:calls=0
function Send-WdNativeToolsQueueMessage {{
 param($CliPath,$ThreadId,$Message,$Worktree)
 $script:calls++
 return '01a0adff-4558-7e80-8936-6aad0d6df821'
}}
try {{
 $result=Invoke-WdNativeToolsWakeStep -CliPath unused -ThreadId '{THREAD}' -Worktree {q(tmp_path)} `
 -WakePath {q(wake)} -StatePath {q(state_path)} -Generation pinned -NativePid 123
 @{{ok=$true;result=$result;calls=$script:calls}}|ConvertTo-Json
}} catch {{ @{{ok=$false;result=$_.Exception.Message;calls=$script:calls}}|ConvertTo-Json }}
"""
    if case == 'move_blocked':
        with wake.open('rb'):  # a watcher holding the wake file without delete sharing
            result = json.loads(_run_powershell(script, executable=ps).stdout)
    else:
        result = json.loads(_run_powershell(script, executable=ps).stdout)
    ok, outcome, calls = C97[case]
    assert (result['ok'], result['calls']) == (ok, calls) and outcome in result['result'], result
    after = {path.name: path.read_bytes() for path in tmp_path.iterdir() if path.is_file()}
    if not ok or case in ('claiming_nothing', 'move_blocked'):
        assert after == before  # every refusal, idle step and retryable move leaves the bytes exactly as found
        return
    state = json.loads(state_path.read_text(encoding='utf-8-sig'))
    named = [path.name for path in tmp_path.glob('native-bridge-wake.json.wake.*') if '.wake.legacy-' not in path.name]
    assert state['status'] == 'queued' and state['delivery_id'] != old_delivery and not named
    if case in ('crash_after_claim', 'crash_after_move', 'crash_after_move_new_wake', 'rejected_owned_retry'):
        assert state['snapshot_id'] == w  # the claimed or refused wake itself was sent, never a new claim
    else:
        assert state['snapshot_id'] not in ('', w)
    if case in ('crash_after_move_new_wake', 'rejected_owned_retry'):
        assert wake.read_text() == 'newer wake'  # a newer wake is not consumed by this send
    if case == 'legacy_queued_fixed':
        (kept,) = tmp_path.glob('native-bridge-wake.json.wake.legacy-*')
        assert kept.read_bytes() == before['native-bridge-wake.json.wake']  # set aside byte-exact, never sent


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_queue_outcome_classifier_is_exact(ps):
    # Pure fixtures for the nested classifier: no fake executable, process or queue call.
    refusal = ('Error: failed to queue session message: thread/queue/add failed: queue cannot contain more than'
               ' 100 submissions (code -32600)')
    queued = f'Queued message 01a0adff-4558-7e80-8936-6aad0d6df821 for thread {THREAD}.\n'
    cases = dict(
        queued=(0, queued, '', 'queued'),
        queued_other_thread=(0, queued.replace(THREAD, 'other-thread'), '', 'ambiguous'),
        queued_nonzero_exit=(1, queued, '', 'ambiguous'),
        refusal_lf=(1, ' \n', refusal + '\n', 'rejected'),
        refusal_crlf=(1, '', refusal + '\r\n', 'rejected'),
        near_miss_code=(1, '', refusal.replace('-32600', '-32601') + '\n', 'ambiguous'),
        embedded=(1, '', 'warning: retrying\n' + refusal + '\n', 'ambiguous'),
        quoted=(1, '', "'" + refusal + "'\n", 'ambiguous'),
        trailing_text=(1, '', refusal + ' (retry later)\n', 'ambiguous'),
        stdout_noise=(1, 'partial output\n', refusal + '\n', 'ambiguous'),
        exit_zero=(0, '', refusal + '\n', 'ambiguous'),
    )
    script = "$ErrorActionPreference='Stop'\nSet-StrictMode -Version Latest\n" + load(TOOLS, 'Get-WdNativeQueueOutcome')
    script += ("function Get-FixtureText($Value) { [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($Value)) }\n"
               "$outcomes=[ordered]@{}\n")
    for name, (code, out, err, _) in cases.items():
        stdout, stderr = (base64.b64encode(text.encode('utf-8')).decode('ascii') for text in (out, err))
        script += (f"$r=Get-WdNativeQueueOutcome -ExitCode {code} -Stdout (Get-FixtureText '{stdout}')"
                   f" -Stderr (Get-FixtureText '{stderr}') -ThreadId '{THREAD}'\n"
                   f"$outcomes['{name}']=$r.outcome + '|' + $r.queue_id\n")
    script += "$outcomes | ConvertTo-Json\n"
    result = json.loads(_run_powershell(script, executable=ps).stdout)
    for name, (_, _, _, expected) in cases.items():
        queue_id = '01a0adff-4558-7e80-8936-6aad0d6df821' if expected == 'queued' else ''
        assert result[name] == expected + '|' + queue_id, (name, result)


def test_wake_procedures_and_fallback_require_the_exact_consumption_receipt():
    source = TOOLS.read_text(encoding='utf-8')
    for agent, procedure in (('codex-tools-1', 'WAKE_PROCEDURE_TOOLS.md'), ('codex-lead-1', 'WAKE_PROCEDURE_LEAD.md')):
        command = ('Write-BridgeStageObservation -BridgeRoot $env:AGENT_BRIDGE_RUNTIME_ROOT -Stage model_turn_started'
                   f' -Target {agent} -DeliveryId')
        assert command + ' <current delivery_id>.' in (REBOOT / procedure).read_text(encoding='utf-8')
        assert command + " ' + $deliveryId + '. ' +" in source
    step = source[source.index('function Invoke-WdNativeToolsWakeStep'):]
    assert "(& $value 'observation_source') -ceq 'agent_reported'" in step
    assert "return 'outstanding'" in step


def test_native_relay_uses_queue_and_lifetime_lock_without_focus_or_second_resume():
    source = TOOLS.read_text(encoding='utf-8')
    relay = source[source.index('function Send-WdNativeToolsQueueMessage'):source.index('function Invoke-WdNativeToolsTerminal')]
    assert "@('queue','--thread',$ThreadId,'--message',$Message)" in relay
    assert '.WaitForExit(1000)' in relay and '[IO.FileShare]::None' in relay
    assert 'SetForegroundWindow' not in relay and 'keybd_event' not in relay
    assert "'resume'" not in relay and "'exec'" not in relay
    assert "status='submitting'" in relay
    assert 'Get-FileHash' in relay and '$ExpectedCliHash' in relay
    procedure = (REBOOT / 'WAKE_PROCEDURE_TOOLS.md').read_text(encoding='utf-8')
    assert 'Get-WdVerifiedNativeWakeMessage' in relay
    assert 'TRUNCATED ROUTING SUMMARY' in procedure
    assert '-Raw -NoAckReceived -NoContinuity' in procedure
    assert 'never from conversation memory or older probes' in procedure


AMBIGUOUS = {  # completed calls that are neither queued nor the exact refusal, and one that never completed
    'reworded': (1, '', 'Error: thread/queue/add failed: the queue is full (code -32600)'),
    'trailing_period': (1, '', 'Error: failed to queue session message: thread/queue/add failed: '
                                'queue cannot contain more than 100 submissions (code -32600).'),
    'stdout_noise': (1, 'partial output', 'Error: failed to queue session message: thread/queue/add failed: '
                                          'queue cannot contain more than 100 submissions (code -32600)'),
    'long_stderr': (2, '', 'E' * 6000),
    'not_completed': None,
}


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('case', sorted(AMBIGUOUS))
def test_an_ambiguous_completed_call_keeps_its_evidence_and_stays_unresolved(tmp_path, ps, case):
    """Fable candidate (wording-pinned refusal): a completed call the exact classifier cannot resolve stays
    submitting (UNKNOWN) and is never retried, but its exact evidence is kept durably for the operator."""
    state_path = tmp_path / 'native-bridge-wake.json'
    wake = tmp_path / 'wake_codex-tools-1'
    wake.write_text('wake before delivery')
    source = TOOLS.read_text(encoding='utf-8')
    start = source.index('    function Get-WdNativeQueueOutcome {')
    classifier = source[start:source.index('\n    }\n', start) + 6]
    script = "$ErrorActionPreference='Stop'\nSet-StrictMode -Version Latest\n"
    for name in ['Assert-WdTurnPath', 'Write-WdTurnJson', 'Move-WdWakeSnapshot']:
        script += load(REBOOT / 'Invoke-WdLaneTurnLoop.ps1', name)
    script += load(TOOLS, 'Invoke-WdNativeToolsWakeStep')
    script += load(TOOLS, 'Get-WdVerifiedNativeWakeMessage')
    script += load(TOOLS, 'Get-WdInlineNativeWakeMessage')
    from test_wd_native_wake_prompt import relay_bundle_setup
    script += relay_bundle_setup(tmp_path)
    evidence = AMBIGUOUS[case]
    if evidence is None:
        raise_line = " throw 'Codex queue call timed out; its outcome is unknown'\n"
    else:
        code, out, err = evidence
        raise_line = (f" $e=[InvalidOperationException]::new('Codex queue did not confirm exact-thread delivery: ' + {q(err)})\n"
                      f" $e.Data['wd_exit_code']={code};$e.Data['wd_stdout']={q(out)};$e.Data['wd_stderr']={q(err)}\n"
                      " throw $e\n")
    script += f"""
$script:calls=0
function Send-WdNativeToolsQueueMessage {{
 param($CliPath,$ThreadId,$Message,$Worktree)
{classifier}
 $script:calls++
{raise_line}}}
try {{
 $result=Invoke-WdNativeToolsWakeStep -CliPath unused -ThreadId '{THREAD}' -Worktree {q(tmp_path)} `
 -WakePath {q(wake)} -StatePath {q(state_path)} -Generation pinned -NativePid 123
 @{{ok=$true;result=$result;calls=$script:calls}}|ConvertTo-Json
}} catch {{ @{{ok=$false;error=$_.Exception.Message;calls=$script:calls}}|ConvertTo-Json }}
"""
    result = json.loads(_run_powershell(script, executable=ps).stdout)
    state = json.loads(state_path.read_text(encoding='utf-8-sig'))
    assert not result['ok'] and result['calls'] == 1 and state['status'] == 'submitting'   # UNKNOWN, no retry
    assert not list(tmp_path.glob('native-bridge-wake.json.refusal-*'))
    receipts = sorted(tmp_path.glob('native-bridge-wake.json.ambiguous-*'))
    if evidence is None:
        assert receipts == []   # a call that never completed has no evidence to keep
        return
    code, out, err = evidence
    (receipt_path,) = receipts
    assert receipt_path.name == 'native-bridge-wake.json.ambiguous-' + state['delivery_id']
    receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
    assert {key: receipt[key] for key in ('schema', 'agent', 'thread_id', 'delivery_id', 'snapshot_id', 'outcome',
                                          'exit_code', 'resolution', 'retry')} == dict(
        schema='wd.native-queue-ambiguous.v1', agent='codex-tools-1', thread_id=THREAD,
        delivery_id=state['delivery_id'], snapshot_id=state['snapshot_id'], outcome='ambiguous', exit_code=code,
        resolution='operator_reconciliation_required', retry='never')
    assert receipt['stderr'] == err[:4096] and receipt['stderr_truncated'] is (len(err) > 4096)
    assert receipt['stdout'] == out and receipt['stdout_truncated'] is False
    assert receipt['stderr_sha256'] == hashlib.sha256(err.encode()).hexdigest().upper()
    assert receipt['stdout_sha256'] == hashlib.sha256(out.encode()).hexdigest().upper()


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('age, stale', [(3600, True), (60, False)])
def test_a_long_outstanding_wake_becomes_observable_but_is_never_resubmitted(tmp_path, ps, age, stale):
    """Fable candidate (outstanding hold with no timeout or alert): past the bound the Step reports
    outstanding_stale and keeps ONE durable observation per delivery; it never submits another wake."""
    state_path = tmp_path / 'native-bridge-wake.json'
    wake = tmp_path / 'wake_codex-tools-1'
    wake.write_text('wake before delivery')
    delivery = '0123456789abcdef' * 2
    stamp = (datetime.now(timezone.utc) - timedelta(seconds=age)).isoformat()
    state_path.write_text(json.dumps(dict(schema='wd.native-tools-wake.v1', status='queued', delivery_id=delivery,
                                          thread_id=THREAD, updated_at_utc=stamp, receipt='model_turn_started',
                                          agent='codex-tools-1')))
    script = "$ErrorActionPreference='Stop'\nSet-StrictMode -Version Latest\n"
    for name in ['Assert-WdTurnPath', 'Write-WdTurnJson', 'Move-WdWakeSnapshot']:
        script += load(REBOOT / 'Invoke-WdLaneTurnLoop.ps1', name)
    script += load(TOOLS, 'Invoke-WdNativeToolsWakeStep')
    script += f"""
$script:calls=0
function Send-WdNativeToolsQueueMessage {{ $script:calls++; throw 'must not be called' }}
$first=Invoke-WdNativeToolsWakeStep -CliPath unused -ThreadId '{THREAD}' -Worktree {q(tmp_path)} `
 -WakePath {q(wake)} -StatePath {q(state_path)} -Generation pinned -NativePid 123
$second=Invoke-WdNativeToolsWakeStep -CliPath unused -ThreadId '{THREAD}' -Worktree {q(tmp_path)} `
 -WakePath {q(wake)} -StatePath {q(state_path)} -Generation pinned -NativePid 123
@{{first=$first;second=$second;calls=$script:calls}}|ConvertTo-Json
"""
    before_state = state_path.read_bytes()
    result = json.loads(_run_powershell(script, executable=ps).stdout)
    expected = 'outstanding_stale' if stale else 'outstanding'
    assert (result['first'], result['second'], result['calls']) == (expected, expected, 0)
    assert state_path.read_bytes() == before_state and wake.read_text() == 'wake before delivery'
    observations = sorted(tmp_path.glob('native-bridge-wake.json.outstanding-*'))
    if not stale:
        assert observations == []
        return
    (observation,) = observations
    assert observation.name == 'native-bridge-wake.json.outstanding-' + delivery
    record = json.loads(observation.read_text(encoding='utf-8'))
    assert {key: record[key] for key in ('schema', 'agent', 'thread_id', 'delivery_id', 'status', 'retry')} == dict(
        schema='wd.native-wake-outstanding.v1', agent='codex-tools-1', thread_id=THREAD, delivery_id=delivery,
        status='outstanding', retry='never')
    assert record['age_seconds'] >= 3600 and record['queued_at_utc'] and record['observed_at_utc']


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('warning_preference', ['Continue', 'Stop'])
@pytest.mark.parametrize('observation_fails', [True, False], ids=['write_refused', 'success_twin'])
def test_outstanding_observation_failure_never_terminates_or_resubmits(tmp_path, ps, warning_preference,
                                                                    observation_fails):
    state_path = tmp_path / 'native-bridge-wake.json'
    wake = tmp_path / 'wake_codex-tools-1'
    wake.write_text('pending wake')
    delivery = '0123456789abcdef' * 2
    state_path.write_text(json.dumps(dict(schema='wd.native-tools-wake.v1', status='queued', delivery_id=delivery,
        thread_id=THREAD, agent='codex-tools-1', receipt='model_turn_started',
        updated_at_utc=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat())))
    before = state_path.read_bytes()
    script = f"$ErrorActionPreference='Stop'\n$WarningPreference='{warning_preference}'\nSet-StrictMode -Version Latest\n"
    for name in ['Assert-WdTurnPath', 'Write-WdTurnJson', 'Move-WdWakeSnapshot']:
        script += load(REBOOT / 'Invoke-WdLaneTurnLoop.ps1', name)
    script += load(TOOLS, 'Invoke-WdNativeToolsWakeStep')
    script += f"""
$realAssert=${{function:Assert-WdTurnPath}}
function Assert-WdTurnPath {{
 param($Path)
 if ({'$true' if observation_fails else '$false'} -and $Path -like '*.outstanding-*') {{throw 'fixture observation refused'}}
 & $realAssert $Path
}}
$script:calls=0
function Send-WdNativeToolsQueueMessage {{$script:calls++; throw 'must not submit'}}
$results=@(1..2 | ForEach-Object {{
 Invoke-WdNativeToolsWakeStep -CliPath unused -ThreadId '{THREAD}' -Worktree {q(tmp_path)} `
 -WakePath {q(wake)} -StatePath {q(state_path)} -Generation fixture -NativePid 123 3>$null
}})
@{{results=$results;calls=$script:calls}}|ConvertTo-Json -Compress
"""
    result = json.loads(_run_powershell(script, executable=ps).stdout)
    assert result == dict(results=['outstanding_stale', 'outstanding_stale'], calls=0)
    assert state_path.read_bytes() == before and wake.read_text() == 'pending wake'
    observations = list(tmp_path.glob('native-bridge-wake.json.outstanding-*'))
    assert len(observations) == (0 if observation_fails else 1)


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('status', ['queued', 'rejected'])
@pytest.mark.parametrize('future', [True, False], ids=['future_refusal', 'ordinary_twin'])
def test_future_relay_timestamp_is_unknown_not_debounce_or_retry(tmp_path, ps, status, future):
    state_path = tmp_path / 'native-bridge-wake.json'
    wake = tmp_path / 'wake_codex-tools-1'
    wake.write_text('pending wake')
    stamp = datetime.now(timezone.utc) + timedelta(hours=1) if future else datetime.now(timezone.utc) - timedelta(seconds=1)
    record = dict(schema='wd.native-tools-wake.v1', status=status, delivery_id='1' * 32,
                  thread_id=THREAD, updated_at_utc=stamp.isoformat(), receipt='model_turn_started', rejections=1)
    if status == 'rejected':
        record['snapshot_id'] = '2' * 32
        Path(str(state_path) + '.wake.' + '2' * 32).write_text('refused wake')
    state_path.write_text(json.dumps(record))
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    script = "$ErrorActionPreference='Stop'\nSet-StrictMode -Version Latest\n"
    for name in ['Assert-WdTurnPath', 'Write-WdTurnJson', 'Move-WdWakeSnapshot']:
        script += load(REBOOT / 'Invoke-WdLaneTurnLoop.ps1', name)
    script += load(TOOLS, 'Invoke-WdNativeToolsWakeStep')
    script += f"""
$script:calls=0
function Send-WdNativeToolsQueueMessage {{$script:calls++;throw 'must not submit'}}
$results=@(1..2 | ForEach-Object {{
 Invoke-WdNativeToolsWakeStep -CliPath unused -ThreadId '{THREAD}' -Worktree {q(tmp_path)} `
 -WakePath {q(wake)} -StatePath {q(state_path)} -Generation fixture -NativePid 123
}})
@{{results=$results;calls=$script:calls}}|ConvertTo-Json -Compress
"""
    result = json.loads(_run_powershell(script, executable=ps).stdout)
    expected = 'unknown_future_timestamp' if future else ('debounced' if status == 'queued' else 'rejected_backoff')
    assert result == dict(results=[expected, expected], calls=0)
    after = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    assert {name: after.get(name) for name in before} == before          # nothing existing is touched
    added = sorted(set(after) - set(before))
    if not future:
        assert added == []
        return
    # O2: one durable UNKNOWN observation, created once over both polls; it grants and completes nothing.
    assert added == ['native-bridge-wake.json.unknown-future-' + '1' * 32]
    observation = json.loads(after[added[0]].decode('utf-8'))
    assert {key: observation[key] for key in ('schema', 'agent', 'thread_id', 'delivery_id', 'status', 'retry',
                                              'resolution', 'record_updated_at_utc')} == dict(
        schema='wd.native-wake-unknown-clock.v1', agent='codex-tools-1', thread_id=THREAD, delivery_id='1' * 32,
        status='unknown_future_timestamp', retry='never', resolution='operator_clock_reconciliation',
        record_updated_at_utc=record['updated_at_utc'])
    assert observation['ahead_seconds'] >= 3500


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('warning_preference', ['Continue', 'Stop'])
def test_a_refused_future_clock_observation_is_a_warning_never_a_stop_or_a_send(tmp_path, ps, warning_preference):
    state_path = tmp_path / 'native-bridge-wake.json'
    wake = tmp_path / 'wake_codex-tools-1'
    wake.write_text('pending wake')
    state_path.write_text(json.dumps(dict(schema='wd.native-tools-wake.v1', status='queued', delivery_id='1' * 32,
        thread_id=THREAD, updated_at_utc=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        receipt='model_turn_started')))
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    script = f"$ErrorActionPreference='Stop'\n$WarningPreference='{warning_preference}'\nSet-StrictMode -Version Latest\n"
    for name in ['Assert-WdTurnPath', 'Write-WdTurnJson', 'Move-WdWakeSnapshot']:
        script += load(REBOOT / 'Invoke-WdLaneTurnLoop.ps1', name)
    script += load(TOOLS, 'Invoke-WdNativeToolsWakeStep')
    script += f"""
$realAssert=${{function:Assert-WdTurnPath}}
function Assert-WdTurnPath {{
 param($Path)
 if ($Path -like '*.unknown-future-*') {{throw 'fixture observation refused'}}
 & $realAssert $Path
}}
$script:calls=0
function Send-WdNativeToolsQueueMessage {{$script:calls++;throw 'must not submit'}}
$out=@(Invoke-WdNativeToolsWakeStep -CliPath unused -ThreadId '{THREAD}' -Worktree {q(tmp_path)} `
 -WakePath {q(wake)} -StatePath {q(state_path)} -Generation fixture -NativePid 123 3>&1)
$warnings=@($out | Where-Object {{ $_ -is [Management.Automation.WarningRecord] }} | ForEach-Object {{ [string]$_.Message }})
$results=@($out | Where-Object {{ $_ -isnot [Management.Automation.WarningRecord] }})
@{{results=$results;calls=$script:calls;warnings=$warnings}}|ConvertTo-Json -Compress
"""
    result = json.loads(_run_powershell(script, executable=ps).stdout)
    assert result['results'] == ['unknown_future_timestamp'] and result['calls'] == 0
    assert [w for w in result['warnings'] if w.startswith('Native wake future-clock observation could not be written: ')]
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()} == before


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('warning_preference', ['Stop', 'Continue'])
@pytest.mark.parametrize('session', ['', 'sess-fixture-1'], ids=['alert_skipped', 'alert_refused'])
def test_post_send_warnings_stay_visible_and_never_unwind_a_queued_delivery(tmp_path, ps, warning_preference, session):
    """O1: after the queue call succeeded, the degraded-prompt, alert and latency warnings are visible warnings,
    never a terminating error, under WarningPreference Stop too: the delivery completes once (queued, snapshot
    removed) and nothing is resubmitted."""
    state_path = tmp_path / 'native-bridge-wake.json'
    wake = tmp_path / 'wake_codex-tools-1'
    wake.write_text('wake before delivery')
    telemetry = tmp_path / 'telemetry-bin'
    telemetry.mkdir()
    (telemetry / 'BridgeTelemetry.ps1').write_text("throw 'fixture telemetry unavailable'\n")
    script = f"$ErrorActionPreference='Stop'\n$WarningPreference='{warning_preference}'\nSet-StrictMode -Version Latest\n"
    for name in ['Assert-WdTurnPath', 'Write-WdTurnJson', 'Move-WdWakeSnapshot']:
        script += load(REBOOT / 'Invoke-WdLaneTurnLoop.ps1', name)
    script += load(TOOLS, 'Invoke-WdNativeToolsWakeStep')
    script += load(TOOLS, 'Get-WdInlineNativeWakeMessage')
    from test_wd_native_wake_prompt import relay_bundle_setup
    script += relay_bundle_setup(tmp_path)
    script += f"""
$env:WD_BRIDGE_BIN={q(telemetry)}
$script:calls=0
function Get-WdVerifiedNativeWakeMessage {{ param($Agent,$DeliveryId) throw 'fixture compact procedure unavailable' }}
function Invoke-WdContinuityOperatorNotice {{ throw 'fixture notice refused' }}
function Send-WdNativeToolsQueueMessage {{ param($CliPath,$ThreadId,$Message,$Worktree) $script:calls++; 'queue-fixture' }}
$out=@(Invoke-WdNativeToolsWakeStep -CliPath unused -ThreadId '{THREAD}' -Worktree {q(tmp_path)} `
 -WakePath {q(wake)} -StatePath {q(state_path)} -Generation pinned -NativePid 123 -SessionId '{session}' 3>&1)
$warnings=@($out | Where-Object {{ $_ -is [Management.Automation.WarningRecord] }} | ForEach-Object {{ [string]$_.Message }})
$results=@($out | Where-Object {{ $_ -isnot [Management.Automation.WarningRecord] }})
@{{results=$results;calls=$script:calls;warnings=$warnings}}|ConvertTo-Json -Compress
"""
    run = _run_powershell(script, executable=ps)
    result = json.loads(run.stdout)
    state = json.loads(state_path.read_text(encoding='utf-8-sig'))
    assert result['results'] == ['queued'] and result['calls'] == 1, run.stdout + run.stderr
    assert state['status'] == 'queued' and state['prompt_mode'] == 'inline_degraded'
    assert not wake.exists() and not list(tmp_path.glob('native-bridge-wake.json.wake*'))
    warnings = result['warnings']
    assert 'Native wake compact procedure unavailable: delivered verified inline fallback; package repair required' in warnings
    alert = ('Native wake prompt alert skipped: launcher session unavailable; degradation retained in relay state' if not session
             else 'Native wake prompt alert unavailable; degradation retained in relay state')
    assert alert in warnings
    assert 'Native relay latency observation unavailable: fixture telemetry unavailable' in warnings


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('classifier_fails', [True, False], ids=['classifier_throws', 'success_twin'])
@pytest.mark.parametrize('warning_preference', ['Continue', 'Stop'])
def test_a_failing_ambiguity_classifier_never_replaces_the_original_queue_error(tmp_path, ps, classifier_fails,
                                                                                warning_preference):
    """RCO1 b814 (frozen 487995d3): the evidence extraction ran outside the receipt try, so a classifier that
    throws replaced the ORIGINAL queue error. The original error object must reach the caller unchanged and the
    attempt must stay submitting with no retry; the success twin still keeps the exact ambiguous receipt.
    Lead 9373acdc (b17ff105): under $WarningPreference='Stop' the diagnostic warning itself must not become
    the error either."""
    state_path = tmp_path / 'native-bridge-wake.json'
    wake = tmp_path / 'wake_codex-tools-1'
    wake.write_text('wake before delivery')
    source = TOOLS.read_text(encoding='utf-8')
    start = source.index('    function Get-WdNativeQueueOutcome {')
    classifier = source[start:source.index('\n    }\n', start) + 6]
    if classifier_fails:
        classifier = "    function Get-WdNativeQueueOutcome { throw 'classifier exploded' }\n"
    script = f"$WarningPreference='{warning_preference}'\n$ErrorActionPreference='Stop'\nSet-StrictMode -Version Latest\n"
    for name in ['Assert-WdTurnPath', 'Write-WdTurnJson', 'Move-WdWakeSnapshot']:
        script += load(REBOOT / 'Invoke-WdLaneTurnLoop.ps1', name)
    script += load(TOOLS, 'Invoke-WdNativeToolsWakeStep')
    script += load(TOOLS, 'Get-WdVerifiedNativeWakeMessage')
    script += load(TOOLS, 'Get-WdInlineNativeWakeMessage')
    from test_wd_native_wake_prompt import relay_bundle_setup
    script += relay_bundle_setup(tmp_path)
    code, out, err = AMBIGUOUS['reworded']
    script += f"""
$script:calls=0;$script:primary=$null
function Send-WdNativeToolsQueueMessage {{
 param($CliPath,$ThreadId,$Message,$Worktree)
{classifier}
 $script:calls++
 $e=[InvalidOperationException]::new('Codex queue did not confirm exact-thread delivery: ' + {q(err)})
 $e.Data['wd_exit_code']={code};$e.Data['wd_stdout']={q(out)};$e.Data['wd_stderr']={q(err)}
 $script:primary=$e
 throw $e
}}
try {{
 $result=Invoke-WdNativeToolsWakeStep -CliPath unused -ThreadId '{THREAD}' -Worktree {q(tmp_path)} `
 -WakePath {q(wake)} -StatePath {q(state_path)} -Generation pinned -NativePid 123 3>$null
 @{{ok=$true;result=$result;calls=$script:calls}}|ConvertTo-Json
}} catch {{
 @{{ok=$false;error=$_.Exception.Message;original=[object]::ReferenceEquals($_.Exception,$script:primary);calls=$script:calls}}|ConvertTo-Json
}}
"""
    result = json.loads(_run_powershell(script, executable=ps).stdout)
    state = json.loads(state_path.read_text(encoding='utf-8-sig'))
    assert (result['ok'], result['original'], result['calls']) == (False, True, 1)
    assert result['error'] == 'Codex queue did not confirm exact-thread delivery: ' + err
    assert state['status'] == 'submitting'   # UNKNOWN: never retried, never guessed complete
    assert not list(tmp_path.glob('native-bridge-wake.json.refusal-*'))
    receipts = sorted(tmp_path.glob('native-bridge-wake.json.ambiguous-*'))
    if classifier_fails:
        assert receipts == []   # nothing could be classified, so nothing is kept
        return
    (receipt_path,) = receipts
    assert receipt_path.name == 'native-bridge-wake.json.ambiguous-' + state['delivery_id']
    receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
    assert (receipt['outcome'], receipt['exit_code'], receipt['stderr'], receipt['delivery_id']) == (
        'ambiguous', code, err, state['delivery_id'])
