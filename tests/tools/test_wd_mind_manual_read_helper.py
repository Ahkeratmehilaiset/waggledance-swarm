"""Synthetic manual-read helper contracts; no real lane, owner, bridge, or policy."""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import time
from datetime import datetime, timedelta, timezone

import pytest

from wd_mind_owned_compat import run_owned


ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / 'ops/windows/reboot/Invoke-WdMindManualRead.ps1'
SHELL = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
WINDOWS = pytest.mark.skipif(os.name != 'nt', reason='synthetic Windows PowerShell helper')
TEXT = 'SYNTHETIC_MANUAL_CONTENT_ONLY'


def _literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _bad_scalar(value, variant):
    if variant == 'object':
        return value[0]
    if variant == 'nested':
        return [value]
    return {'empty': [], 'singleton': [value], 'null': None,
            'string': '1', 'bool': True, 'float': 1.0, 'integer': 1}[variant]


def _bundle(tmp_path, fault='none', *, lane='codex-lead-1', source_agent='codex-lead-1', text=TEXT,
            shape_component=None, shape_field=None, shape_variant=None):
    audit = tmp_path / '.codex-audit'
    audit.mkdir()
    bundle = audit / 'synthetic-bundle'
    bundle.mkdir()
    policy = audit / 'synthetic-policy.json'
    policy.write_bytes(b'{"identifiers":[],"schema":"wd.mind-identifier-policy.v2"}')
    python = audit / 'synthetic-python.exe'
    python.write_bytes(b'synthetic inert executable pin; never run')
    fleet = b'{"synthetic_fleet":true}'
    fleet_pin = _sha(fleet)
    approval = 'c' * 64
    event_time = (datetime.now(timezone.utc) - timedelta(seconds=1200)).replace(microsecond=0)
    frame = {'schema': 'wd.mind-flat-verified-session.v1', 'authority': 'none', 'lane': lane,
             'fleet_sha256': fleet_pin, 'sender_authentication': 'UNKNOWN', 'index_match': 'UNKNOWN',
             'same_user_aba': 'UNKNOWN', 'current_status_freshness': 'UNKNOWN',
             'results': [{'text': text, 'source_event_sha256': 'b'*64,
                          'event_time_utc_floor': event_time.isoformat().replace('+00:00', 'Z'),
                          'source_agent': source_agent, 'event_age_seconds_upper_bound': 1200}]}
    if fault == 'lane': frame['lane'] = 'codex-tools-1'
    if fault == 'fleet_result': frame['fleet_sha256'] = 'e' * 64
    if fault == 'authority': frame['authority'] = 'trusted'
    if fault == 'future':
        frame['results'][0]['event_time_utc_floor'] = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat().replace('+00:00', 'Z')
    if fault == 'rco_source': frame['results'][0]['source_agent'] = 'claude-rco-1'
    if fault == 'ancient': frame['results'][0]['event_time_utc_floor'] = '1900-01-01T00:00:00Z'
    if fault == 'bad_date': frame['results'][0]['event_time_utc_floor'] = '2026-02-30T12:00:00Z'
    if fault == 'age_negative': frame['results'][0]['event_age_seconds_upper_bound'] = -1
    if fault == 'age_float': frame['results'][0]['event_age_seconds_upper_bound'] = 1.5
    if fault == 'age_overflow': frame['results'][0]['event_age_seconds_upper_bound'] = 2**63
    if fault == 'age_int64_max': frame['results'][0]['event_age_seconds_upper_bound'] = 2**63 - 1
    if shape_component in ('output', 'hit'):
        target = frame if shape_component == 'output' else frame['results'][0]
        target[shape_field] = _bad_scalar(target[shape_field], shape_variant)
    output = json.dumps(frame, ensure_ascii=True, separators=(',', ':'))
    if fault == 'invalid_json': output = '{"schema":'
    if fault == 'duplicate_authority': output = output.replace('"authority":"none"', '"authority":"trusted","authority":"none"', 1)
    if fault == 'exit2_partial': output = output[:len(output)//2]
    if fault == 'stdout_overflow': output = 'X' * 16385
    stderr = ('E' * 4097 if fault == 'stderr_overflow' else 'synthetic error' if fault in ('stderr', 'late_stderr') else '')
    code = 2 if fault in ('exit2_full', 'exit2_partial') else 0
    started, finished = audit / 'synthetic-started.marker', audit / 'synthetic-finished.marker'
    descendant_finished = audit / 'synthetic-descendant-finished.marker'
    descendant_script = ("Start-Sleep -Milliseconds 1700; [IO.File]::WriteAllText("
                         + _literal(descendant_finished) + ",'finished')")
    descendant_encoded = base64.b64encode(descendant_script.encode('utf-16-le')).decode('ascii')
    descendant_launch = (
        "$i=New-Object Diagnostics.ProcessStartInfo; "
        "$i.FileName=Join-Path $PSHOME 'powershell.exe'; "
        "$i.Arguments='-NoProfile -NonInteractive -EncodedCommand " + descendant_encoded + "'; "
        "$i.UseShellExecute=$false; $i.CreateNoWindow=$true; "
        "$p=New-Object Diagnostics.Process; $p.StartInfo=$i; [void]$p.Start(); "
    ) if fault == 'descendant_eof' else ''
    wrapper = (
        "if ($args -cnotcontains 'tools/wd_bridge_mind_session.py' -or "
        "$args -cnotcontains '--mind-manual-candidate-read') { "
        "[Console]::Error.Write('synthetic_bad_route'); $global:LASTEXITCODE=2; return }; "
        + '[IO.File]::WriteAllText(' + _literal(started) + ",'started'); "
        + descendant_launch
        + ('Start-Sleep -Milliseconds 2200; ' if fault == 'timeout' else '')
        + '[Console]::Out.Write(' + _literal(output) + '); '
        + ('Start-Sleep -Milliseconds 3000; ' if fault == 'late_exit' else
           'Start-Sleep -Milliseconds 700; ' if fault == 'late_stderr' else '')
        + ('[Console]::Error.Write(' + _literal(stderr) + '); ' if stderr else '')
        + '[IO.File]::WriteAllText(' + _literal(finished) + ",'finished'); "
        + '$global:LASTEXITCODE=' + str(code)
    ).encode('utf-8')
    descriptor_value = {'schema': 'wd.mind-identifier-policy-approval.v1',
                        'identifier_policy_sha256': _sha(policy.read_bytes()),
                        'approval_reference_sha256': approval}
    if shape_component == 'descriptor':
        descriptor_value[shape_field] = _bad_scalar(descriptor_value[shape_field], shape_variant)
    descriptor = json.dumps(descriptor_value, sort_keys=True, separators=(',', ':')).encode()
    helper_bytes = HELPER.read_bytes()
    cleanup_marker = audit / 'synthetic-success-before-cleanup.marker'
    if fault == 'cleanup_delay':
        needle = b'        if($null -ne $process){'
        assert helper_bytes.count(needle) == 1
        hook = ("        if($result.state -ceq 'SUCCESS'){[IO.File]::WriteAllText("
                + _literal(cleanup_marker) + ",'success');Start-Sleep -Milliseconds 3500}\n").encode()
        helper_bytes = helper_bytes.replace(needle, hook + needle)
    if fault == 'age_cleanup_delay':
        needle = b'        if($null -ne $process){'
        assert helper_bytes.count(needle) == 1
        helper_bytes = helper_bytes.replace(needle, b'        Start-Sleep -Milliseconds 2500\n' + needle)
    if fault == 'age_pin_delay':
        needle = b'        foreach($name in $pins.Keys){'
        assert helper_bytes.count(needle) == 1
        helper_bytes = helper_bytes.replace(needle, b'        Start-Sleep -Milliseconds 2500\n' + needle)
    if fault in ('clock_forward', 'clock_backward'):
        initial = event_time + timedelta(seconds=1200)
        final = initial + timedelta(seconds=60 if fault == 'clock_forward' else -1300)
        needle = b'function Invoke-WdMindManualRead {'
        assert helper_bytes.count(needle) == 1 and b'[DateTimeOffset]::UtcNow' in helper_bytes
        clock = ("$script:WdSyntheticClockCalls=0\nfunction Get-WdSyntheticClock {\n"
                 "$script:WdSyntheticClockCalls++\n"
                 "if($script:WdSyntheticClockCalls -eq 1){return [DateTimeOffset]::Parse("
                 + _literal(initial.isoformat()) + ")}\nreturn [DateTimeOffset]::Parse("
                 + _literal(final.isoformat()) + ")\n}\n").encode()
        helper_bytes = helper_bytes.replace(needle, clock + needle)
        helper_bytes = helper_bytes.replace(b'[DateTimeOffset]::UtcNow', b'$(Get-WdSyntheticClock)')
    files = {'Invoke-WdMindManualRead.ps1': helper_bytes,
             'Invoke-WdBridgePython.ps1': wrapper,
             'tools-bootstrap/tools/wd_bridge_mind_session.py': b'# synthetic pin target; never executed\n',
             'wd-fleet.json': fleet,
             'wd-mind-identifier-policy-approval.json': descriptor}
    pins = {}
    for relative, data in files.items():
        path = bundle.joinpath(*relative.split('/'))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        pins[relative] = _sha(data)
    manifest = bundle / 'deployment-manifest.json'
    manifest_value = {'schema_version': 1, 'files': pins}
    if shape_component == 'manifest':
        manifest_value[shape_field] = _bad_scalar(manifest_value[shape_field], shape_variant)
    manifest.write_bytes(json.dumps(manifest_value, separators=(',', ':')).encode())
    values = {'bundle': bundle, 'manifest_pin': _sha(manifest.read_bytes()), 'policy': policy,
              'policy_pin': _sha(policy.read_bytes()), 'approval': approval, 'python': python,
              'python_pin': _sha(python.read_bytes()), 'fleet_pin': fleet_pin,
              'started': started, 'finished': finished, 'cleanup_marker': cleanup_marker,
              'descendant_finished': descendant_finished}
    if fault == 'manifest_pin': values['manifest_pin'] = 'd'*64
    elif fault == 'policy_pin': values['policy_pin'] = 'd'*64
    elif fault == 'approval_pin': values['approval'] = 'd'*64
    elif fault == 'fleet_pin': values['fleet_pin'] = 'd'*64
    elif fault == 'python_pin': values['python_pin'] = 'd'*64
    elif fault in ('tampered_wrapper', 'tampered_entrypoint', 'tampered_fleet', 'tampered_descriptor'):
        target = {'tampered_wrapper': 'Invoke-WdBridgePython.ps1',
                  'tampered_entrypoint': 'tools-bootstrap/tools/wd_bridge_mind_session.py',
                  'tampered_fleet': 'wd-fleet.json',
                  'tampered_descriptor': 'wd-mind-identifier-policy-approval.json'}[fault]
        with bundle.joinpath(*target.split('/')).open('ab') as stream:
            stream.write(b'\n# synthetic tamper')
    return values


def _run(values, *, enabled=True, timeout=5, bundle_override=None, lane='codex-lead-1', strict=False, busy=False,
         observe_now=False):
    helper = values['bundle'] / 'Invoke-WdMindManualRead.ps1'
    bundle = values['bundle'] if bundle_override is None else bundle_override
    switch = '-Enabled' if enabled else ''
    script = f"""
$ErrorActionPreference='Stop'
. {_literal(helper)}
{'Set-StrictMode -Version Latest' if strict else ''}
{"$global:WdMindManualReadRecords=@{'synthetic-held'=@{State='HOLD'}}" if busy else ''}
$result=Invoke-WdMindManualRead {switch} -BundleRoot {_literal(bundle)} `
 -ExpectedManifestHash {_literal(values['manifest_pin'])} -OperatorPolicyPath {_literal(values['policy'])} `
 -OperatorPolicySha256 {_literal(values['policy_pin'])} -OperatorApprovalReferenceSha256 {_literal(values['approval'])} `
 -BridgePythonPath {_literal(values['python'])} -BridgePythonSha256 {_literal(values['python_pin'])} `
 -Lane {_literal(lane)} -ExpectedFleetSha256 {_literal(values['fleet_pin'])} -TimeoutSeconds {timeout}
if ($null -eq $result -or $result -is [array]) {{ throw 'invalid_result_shape' }}
{'$result | Add-Member NoteProperty test_observed_utc ([DateTimeOffset]::UtcNow.ToString(\'o\'))' if observe_now else ''}
[Console]::Out.Write((ConvertTo-Json -InputObject $result -Depth 20 -Compress))
"""
    argv = [str(SHELL), '-NoProfile', '-NonInteractive', '-EncodedCommand',
            base64.b64encode(script.encode('utf-16-le')).decode('ascii')]
    completed = run_owned(argv, cwd=ROOT, input='', capture_output=True, text=True,
                          encoding='utf-8', timeout=30,
                          creationflags=getattr(__import__('subprocess'), 'CREATE_NO_WINDOW', 0))
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout.lstrip('\ufeff'))
    result = {key.lower(): value for key, value in result.items()}
    return result, completed


@WINDOWS
def test_manual_read_default_off_before_input_io(tmp_path):
    values = _bundle(tmp_path)
    result, proc = _run(values, enabled=False, bundle_override=tmp_path / 'synthetic-absent')
    assert result['state'] == 'DISABLED' and result['authority'] == 'none' and result['data'] is None
    assert not values['started'].exists() and TEXT not in proc.stdout + proc.stderr


@WINDOWS
def test_manual_read_success_accepts_only_bound_complete_json(tmp_path):
    values = _bundle(tmp_path)
    result, _ = _run(values)
    assert result['state'] == 'SUCCESS' and result['authority'] == 'none'
    assert result['data']['lane'] == 'codex-lead-1' and result['data']['fleet_sha256'] == values['fleet_pin']
    assert result['data']['results'][0]['text'] == TEXT
    assert result['data']['results'][0]['source_event_sha256'] == 'b'*64
    assert values['started'].read_text() == 'started' and values['finished'].read_text() == 'finished'


@WINDOWS
def test_manual_read_rco_consumer_can_receive_build_lane_content(tmp_path):
    values = _bundle(tmp_path, lane='claude-rco-1', source_agent='codex-lead-1')
    result, _ = _run(values, lane='claude-rco-1')
    assert result['state'] == 'SUCCESS'
    assert result['data']['lane'] == 'claude-rco-1'
    assert result['data']['results'][0]['source_agent'] == 'codex-lead-1'


@WINDOWS
@pytest.mark.parametrize('fault', ['age_pin_delay', 'age_cleanup_delay'])
def test_manual_read_age_upper_covers_late_pin_and_cleanup(tmp_path, fault):
    values = _bundle(tmp_path, fault=fault)
    result, _ = _run(values, timeout=10, observe_now=True)
    assert result['state'] == 'SUCCESS'
    hit = result['data']['results'][0]
    observed = datetime.fromisoformat(result['test_observed_utc'].replace('Z', '+00:00'))
    event = datetime.fromisoformat(hit['event_time_utc_floor'].replace('Z', '+00:00'))
    assert hit['event_age_seconds_upper_bound'] >= (observed - event).total_seconds()


@WINDOWS
@pytest.mark.parametrize(('fault', 'expected'), [('clock_forward', 'SUCCESS'), ('clock_backward', 'REFUSED')])
def test_manual_read_rechecks_wall_clock_before_return(tmp_path, fault, expected):
    values = _bundle(tmp_path, fault=fault)
    result, _ = _run(values, timeout=5)
    assert result['state'] == expected
    if expected == 'SUCCESS':
        assert result['data']['results'][0]['event_age_seconds_upper_bound'] >= 1260
    else:
        assert result['data'] is None


@WINDOWS
@pytest.mark.parametrize('fault', ['bad_date', 'age_negative', 'age_float', 'age_overflow'])
def test_manual_read_refuses_invalid_date_or_age(tmp_path, fault):
    values = _bundle(tmp_path, fault=fault)
    result, proc = _run(values)
    assert result['state'] == 'REFUSED' and result['data'] is None
    assert TEXT not in proc.stdout + proc.stderr


@WINDOWS
def test_manual_read_preserves_historical_timestamp(tmp_path):
    values = _bundle(tmp_path, fault='ancient')
    result, _ = _run(values, timeout=5)
    assert result['state'] == 'SUCCESS'
    assert result['data']['results'][0]['event_time_utc_floor'] == '1900-01-01T00:00:00Z'
    assert result['data']['results'][0]['event_age_seconds_upper_bound'] > 1_000_000


@WINDOWS
def test_manual_read_preserves_int64_max_age_without_overflow(tmp_path):
    values = _bundle(tmp_path, fault='age_int64_max')
    result, _ = _run(values, timeout=5)
    assert result['state'] == 'SUCCESS'
    assert result['data']['results'][0]['event_age_seconds_upper_bound'] == 2**63 - 1


@WINDOWS
def test_manual_read_invalid_pin_under_strict_mode_fails_closed(tmp_path):
    values = _bundle(tmp_path, fault='manifest_pin')
    result, _ = _run(values, strict=True)
    assert result['state'] == 'REFUSED' and result['data'] is None
    assert not values['started'].exists()


@WINDOWS
@pytest.mark.parametrize('variant', ['empty', 'singleton'])
@pytest.mark.parametrize(('component', 'field'), [
    ('manifest', 'schema_version'), ('manifest', 'files'),
    ('descriptor', 'schema'), ('descriptor', 'identifier_policy_sha256'),
    ('descriptor', 'approval_reference_sha256'),
    ('output', 'schema'), ('output', 'authority'), ('output', 'lane'), ('output', 'fleet_sha256'),
    ('output', 'sender_authentication'), ('output', 'index_match'), ('output', 'same_user_aba'),
    ('output', 'current_status_freshness'),
    ('hit', 'text'), ('hit', 'source_event_sha256'), ('hit', 'event_time_utc_floor'), ('hit', 'source_agent'),
])
def test_manual_read_rejects_array_in_scalar_field(tmp_path, component, field, variant):
    values = _bundle(tmp_path, shape_component=component, shape_field=field, shape_variant=variant)
    result, proc = _run(values)
    assert result['state'] == 'REFUSED' and result['data'] is None
    assert TEXT not in proc.stdout + proc.stderr
    if component in ('manifest', 'descriptor'):
        assert not values['started'].exists()


@WINDOWS
@pytest.mark.parametrize(('field', 'variant'), [
    ('schema_version', 'null'), ('schema_version', 'string'), ('schema_version', 'bool'), ('schema_version', 'float'),
    ('files', 'null'), ('files', 'string'), ('files', 'integer'),
])
def test_manual_read_rejects_non_integer_schema_or_non_object_files(tmp_path, field, variant):
    values = _bundle(tmp_path, shape_component='manifest', shape_field=field, shape_variant=variant)
    result, proc = _run(values)
    assert result['state'] == 'REFUSED' and result['data'] is None
    assert not values['started'].exists() and TEXT not in proc.stdout + proc.stderr


@WINDOWS
def test_manual_read_rejects_singleton_array_age(tmp_path):
    values = _bundle(tmp_path, shape_component='hit', shape_field='event_age_seconds_upper_bound',
                     shape_variant='singleton')
    result, proc = _run(values)
    assert result['state'] == 'REFUSED' and result['data'] is None
    assert TEXT not in proc.stdout + proc.stderr


@WINDOWS
@pytest.mark.parametrize('variant', ['object', 'empty', 'nested'])
def test_manual_read_rejects_invalid_results_container(tmp_path, variant):
    values = _bundle(tmp_path, shape_component='output', shape_field='results', shape_variant=variant)
    result, proc = _run(values)
    assert result['state'] == 'REFUSED' and result['data'] is None
    assert TEXT not in proc.stdout + proc.stderr


@WINDOWS
def test_parallel_synthetic_calls_keep_results_separate(tmp_path):
    left, right = tmp_path / 'left', tmp_path / 'right'
    left.mkdir(); right.mkdir()
    a = _bundle(left, text='SYNTHETIC_CALL_A')
    b = _bundle(right, text='SYNTHETIC_CALL_B')
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(_run, a)
        second = pool.submit(_run, b)
        a_result, _ = first.result()
        b_result, _ = second.result()
    assert a_result['state'] == b_result['state'] == 'SUCCESS'
    assert a_result['data']['results'][0]['text'] == 'SYNTHETIC_CALL_A'
    assert b_result['data']['results'][0]['text'] == 'SYNTHETIC_CALL_B'


@WINDOWS
def test_manual_read_existing_session_hold_refuses_new_child(tmp_path):
    values = _bundle(tmp_path)
    result, _ = _run(values, busy=True)
    assert result['state'] == 'REFUSED' and result['data'] is None
    assert not values['started'].exists()


@WINDOWS
def test_manual_read_cleanup_over_deadline_discards_prepared_success(tmp_path):
    values = _bundle(tmp_path, fault='cleanup_delay')
    result, proc = _run(values, timeout=3)
    assert values['cleanup_marker'].read_text() == 'success'
    assert result['state'] == 'REFUSED' and result['data'] is None
    assert TEXT not in proc.stdout + proc.stderr


@WINDOWS
@pytest.mark.parametrize('fault', ['exit2_full', 'exit2_partial', 'invalid_json', 'duplicate_authority',
                                   'lane', 'fleet_result', 'authority', 'future', 'rco_source', 'stderr',
                                   'stdout_overflow', 'stderr_overflow'])
def test_manual_read_refuses_bad_child_without_exposing_content(tmp_path, fault):
    values = _bundle(tmp_path, fault)
    result, proc = _run(values)
    assert values['started'].read_text() == 'started' and values['finished'].read_text() == 'finished'
    assert result['state'] == 'REFUSED' and result['authority'] == 'none' and result['data'] is None
    assert TEXT not in proc.stdout + proc.stderr


@WINDOWS
@pytest.mark.parametrize('fault', ['manifest_pin', 'policy_pin', 'approval_pin', 'fleet_pin', 'python_pin',
                                   'tampered_wrapper', 'tampered_entrypoint', 'tampered_fleet', 'tampered_descriptor'])
def test_manual_read_refuses_changed_independent_pins_before_child(tmp_path, fault):
    values = _bundle(tmp_path, fault)
    result, proc = _run(values)
    assert result['state'] == 'REFUSED' and result['authority'] == 'none' and result['data'] is None
    assert not values['started'].exists() and TEXT not in proc.stdout + proc.stderr


@WINDOWS
@pytest.mark.parametrize('timeout', [0, 301])
def test_manual_read_refuses_deadlines_outside_1_to_300_before_child(tmp_path, timeout):
    values = _bundle(tmp_path)
    result, _ = _run(values, timeout=timeout)
    assert result['state'] == 'REFUSED' and result['authority'] == 'none' and result['data'] is None
    assert not values['started'].exists()


@WINDOWS
def test_manual_read_timeout_discards_and_retains_child_until_exit_and_eof(tmp_path):
    values = _bundle(tmp_path, 'timeout')
    start = time.monotonic()
    result, proc = _run(values, timeout=1)
    elapsed = time.monotonic() - start
    assert result['state'] == 'REFUSED' and result['authority'] == 'none' and result['data'] is None
    assert values['started'].read_text() == 'started' and values['finished'].read_text() == 'finished'
    assert elapsed >= 2.0
    assert TEXT not in proc.stdout + proc.stderr


@WINDOWS
@pytest.mark.parametrize(('fault', 'timeout'), [('late_exit', 2), ('late_stderr', 5)])
def test_complete_json_is_withheld_until_exit_eof_and_stderr(tmp_path, fault, timeout):
    values = _bundle(tmp_path, fault)
    result, proc = _run(values, timeout=timeout)
    assert values['started'].read_text() == 'started' and values['finished'].read_text() == 'finished'
    assert result['state'] == 'REFUSED' and result['data'] is None
    assert TEXT not in proc.stdout + proc.stderr


@WINDOWS
def test_manual_read_waits_for_descendant_pipe_eof(tmp_path):
    values = _bundle(tmp_path, 'descendant_eof')
    result, _ = _run(values, timeout=5)
    assert values['finished'].read_text() == 'finished'
    assert values['descendant_finished'].read_text() == 'finished'
    assert values['finished'].stat().st_mtime_ns < values['descendant_finished'].stat().st_mtime_ns
    assert result['state'] == 'SUCCESS' and result['data']['results'][0]['text'] == TEXT
