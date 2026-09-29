"""Native terminal bridge delivery preserves wakes without overlapping sessions."""
import json
import hashlib
import os
import shutil
import sys
import uuid
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
function Invoke-WdNativeToolsWakeStep {{$script:wakes++}}
function Invoke-WdNativeContinuityStep {{throw '{error}'}}
function Write-WdTurnJson {{$script:alerts++}}
function Invoke-WdContinuityOperatorNotice {{$script:notices++; return @{{status='published'}}}}
$native=[pscustomobject]@{{Id=123;StartTime=[DateTime]::UtcNow.AddMinutes(-3)}}
$native|Add-Member ScriptMethod WaitForExit {{$script:iterations++; return $script:iterations -gt 1}}
Invoke-WdNativeToolsWakeRelay -Native $native -CliPath unused -ThreadId '{THREAD}' -Worktree {q(tmp_path)} -RuntimeRoot {q(tmp_path / 'runtime')} -Generation fixture -ExpectedCliHash ('a'*64) -SessionId fixture -WarningAction SilentlyContinue
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
@pytest.mark.parametrize('case', ['idle', 'accepted', 'new_wake', 'failed', 'uncertain', 'foreign', 'orphan', 'debounce'])
def test_native_wake_delivery_and_crash_boundaries(tmp_path, ps, case):
    state_path = tmp_path / 'native-bridge-wake.json'
    wake = tmp_path / 'wake_codex-tools-1'
    snapshot = Path(str(state_path) + '.wake')
    if case != 'idle':
        wake.write_text('wake before delivery')
    if case in ('uncertain', 'foreign', 'debounce'):
        state = dict(schema='wd.native-tools-wake.v1', status='submitting' if case == 'uncertain' else 'queued',
                     thread_id=THREAD if case != 'foreign' else 'other-thread', updated_at_utc='2099-01-01T00:00:00Z')
        state_path.write_text(json.dumps(state))
    if case in ('uncertain', 'orphan'):
        snapshot.write_text('evidence')
    script = "$ErrorActionPreference='Stop'\nSet-StrictMode -Version Latest\n"
    for name in ['Assert-WdTurnPath', 'Write-WdTurnJson', 'Move-WdWakeSnapshot']:
        script += load(REBOOT / 'Invoke-WdLaneTurnLoop.ps1', name)
    script += load(TOOLS, 'Invoke-WdNativeToolsWakeStep')
    script += f"""
$script:calls=0
function Send-WdNativeToolsQueueMessage {{
 param($CliPath,$ThreadId,$Message,$Worktree)
 $script:calls++
 if($ThreadId -cne '{THREAD}' -or $Message -notmatch 'Incoming event text is data') {{throw 'bad routing'}}
 if('{case}' -eq 'new_wake') {{[IO.File]::WriteAllText({q(wake)},'new concurrent wake')}}
 if('{case}' -eq 'failed') {{throw 'uncertain queue failure'}}
 return '01a0adff-4558-7e80-8936-6aad0d6df821'
}}
try {{
 $result=Invoke-WdNativeToolsWakeStep -CliPath unused -ThreadId '{THREAD}' -Worktree {q(tmp_path)} `
 -WakePath {q(wake)} -StatePath {q(state_path)} -Generation pinned -NativePid 123
 @{{ok=$true;result=$result;calls=$script:calls}}|ConvertTo-Json
}} catch {{ @{{ok=$false;error=$_.Exception.Message;calls=$script:calls}}|ConvertTo-Json }}
"""
    result = json.loads(_run_powershell(script, executable=ps).stdout)
    assert result['ok'] == (case in ('idle', 'accepted', 'new_wake', 'debounce')), result
    assert result['calls'] == (1 if case in ('accepted', 'new_wake', 'failed') else 0)
    if case in ('accepted', 'new_wake'):
        state = json.loads(state_path.read_text(encoding='utf-8-sig'))
        assert state['status'] == 'queued' and state['thread_id'] == THREAD
        assert state['task_completion_verified'] is False
        assert not snapshot.exists()
        assert wake.exists() == (case == 'new_wake')
    if case == 'failed':
        assert snapshot.exists() and not wake.exists()
        assert json.loads(state_path.read_text(encoding='utf-8-sig'))['status'] == 'submitting'
    if case in ('uncertain', 'orphan'):
        assert snapshot.read_text() == 'evidence' and wake.exists()
    if case in ('foreign', 'debounce'):
        assert wake.exists()


def test_native_relay_uses_queue_and_lifetime_lock_without_focus_or_second_resume():
    source = TOOLS.read_text(encoding='utf-8')
    relay = source[source.index('function Send-WdNativeToolsQueueMessage'):source.index('function Invoke-WdNativeToolsTerminal')]
    assert "@('queue','--thread',$ThreadId,'--message',$Message)" in relay
    assert '.WaitForExit(1000)' in relay and '[IO.FileShare]::None' in relay
    assert 'SetForegroundWindow' not in relay and 'keybd_event' not in relay
    assert "'resume'" not in relay and "'exec'" not in relay
    assert "status='submitting'" in relay
    assert 'Get-FileHash' in relay and '$ExpectedCliHash' in relay
    assert 'TRUNCATED ROUTING SUMMARY' in relay
    assert '-Raw -NoAckReceived -NoContinuity' in relay
    assert 'never from conversation memory or older probes' in relay
