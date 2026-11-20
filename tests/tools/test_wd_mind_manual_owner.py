"""Synthetic manual owner contract tests; no live bundle or worker."""
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.mark.skipif(os.name != 'nt', reason='PowerShell manual owner guard')
@pytest.mark.parametrize('shell', [
    str(Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'),
    pytest.param(shutil.which('pwsh'), marks=pytest.mark.skipif(shutil.which('pwsh') is None,
                                                               reason='PowerShell 7 unavailable')),
], ids=['powershell51', 'pwsh7'])
def test_capacity_optin_refuses_before_reads_registration_or_child(shell):
    helper = Path(__file__).resolve().parents[2] / 'ops/windows/reboot/Start-WdMindManualOwner.ps1'
    path = str(helper).replace("'", "''")
    script = f"""
$ErrorActionPreference='Stop'
. '{path}'
$global:SyntheticPinReads=0
$global:SyntheticObjects=0
function Read-WdMindManualPinnedBytes {{ $global:SyntheticPinReads++; throw 'synthetic-pin-read' }}
function New-Object {{ $global:SyntheticObjects++; throw 'synthetic-object-create' }}
$results=@()
foreach($registryMode in @('absent','existing')) {{
  if($registryMode -ceq 'absent') {{ Remove-Variable WdMindManualOwnerRecords -Scope Global -ErrorAction SilentlyContinue }}
  else {{ $global:WdMindManualOwnerRecords=@{{sentinel='retained'}} }}
  $original=if($registryMode -ceq 'existing') {{$global:WdMindManualOwnerRecords}} else {{$null}}
  foreach($enabled in @($false,$true)) {{
    foreach($legacy in @('none','startup','worker','both')) {{
      $invokeArgs=@{{Enabled=$enabled;IncludeCapacityDiagnostics=$true;BundleRoot='C:\\synthetic-none';
        ExpectedManifestHash=('a'*64);OperatorPolicyPath='C:\\synthetic-policy';OperatorPolicySha256=('b'*64);
        OperatorApprovalReferenceSha256=('c'*64);BridgePythonPath='C:\\synthetic-python';BridgePythonSha256=('d'*64)}}
      if($legacy -in @('startup','both')){{$invokeArgs.IncludeStartupDiagnostics=$true}}
      if($legacy -in @('worker','both')){{$invokeArgs.IncludeWorkerStageDiagnostics=$true}}
      $message='unexpected-success'
      try {{ Start-WdMindManualOwner @invokeArgs | Out-Null }} catch {{ $message=$_.Exception.Message }}
      $untouched=if($registryMode -ceq 'existing') {{
        [object]::ReferenceEquals($original,$global:WdMindManualOwnerRecords) -and
        $global:WdMindManualOwnerRecords.Count -eq 1 -and $global:WdMindManualOwnerRecords.sentinel -ceq 'retained'
      }} else {{ -not (Test-Path variable:global:WdMindManualOwnerRecords) }}
      $results+=@([pscustomobject]@{{message=$message;untouched=$untouched;reads=$global:SyntheticPinReads;
        objects=$global:SyntheticObjects}})
    }}
  }}
}}
$baseline=@{{Enabled=$false;BundleRoot='C:\\synthetic-none';ExpectedManifestHash=('a'*64);
  OperatorPolicyPath='C:\\synthetic-policy';OperatorPolicySha256=('b'*64);
  OperatorApprovalReferenceSha256=('c'*64);BridgePythonPath='C:\\synthetic-python';BridgePythonSha256=('d'*64)}}
$disabled='unexpected-success'
try {{ Start-WdMindManualOwner @baseline | Out-Null }} catch {{ $disabled=$_.Exception.Message }}
$baseline.Enabled=$true
$enabled='unexpected-success'
try {{ Start-WdMindManualOwner @baseline | Out-Null }} catch {{ $enabled=$_.Exception.Message }}
[pscustomobject]@{{results=$results;disabled=$disabled;enabled=$enabled;baseline_reads=$global:SyntheticPinReads;
  baseline_objects=$global:SyntheticObjects}} | ConvertTo-Json -Depth 5 -Compress
"""
    encoded = base64.b64encode(script.encode('utf-16le')).decode()
    proc = subprocess.run([shell, '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                          capture_output=True, text=True, timeout=20, check=True)
    result = json.loads(proc.stdout.lstrip('\ufeff'))
    assert len(result['results']) == 16
    assert all(row == {'message':'mind_manual_capacity_unavailable', 'untouched':True,
                       'reads':0, 'objects':0} for row in result['results'])
    assert result['disabled'] == 'mind_manual_owner_disabled'
    assert result['enabled'] == 'mind_manual_owner_refused'
    assert result['baseline_reads'] == 1 and result['baseline_objects'] == 0


@pytest.mark.skipif(os.name != 'nt', reason='PowerShell capacity diagnostic parser')
@pytest.mark.parametrize('shell', [
    str(Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'),
    pytest.param(shutil.which('pwsh'), marks=pytest.mark.skipif(shutil.which('pwsh') is None,
                                                               reason='PowerShell 7 unavailable')),
], ids=['powershell51', 'pwsh7'])
def test_capacity_diagnostic_optin_is_strict_and_never_changes_readiness(shell):
    """Synthetic wire only: the emitter's binding is not independently authenticated."""
    manifest = 'a' * 64
    base = {'accepted_chunks': 0, 'authority': 'none', 'capacity_chunks': 500000,
            'freshness': 'UNKNOWN', 'manifest_sha256': manifest,
            'diagnostic_generated_utc': '2020-01-01T00:00:00Z', 'same_user_aba': 'UNKNOWN',
            'schema': 'wd.mind-local-owner-capacity.v1', 'scope': 'PINNED_PREFIX', 'source_authentication': 'UNKNOWN',
            'source_offset': 1, 'source_prefix_sha256': 'b' * 64, 'state': 'OBSERVED'}
    def status(state):
        return {'authority': 'none', 'caller_authentication': 'UNKNOWN',
                'index_refresh': 'NOT_SCHEDULED', 'manifest_sha256': manifest,
                'schema': 'wd.mind-local-owner-status.v1',
                'source_authentication': 'UNKNOWN', 'state': state}
    def wire(value):
        return json.dumps(value, sort_keys=True, separators=(',', ':'))
    starting, ready = wire(status('STARTING')), wire(status('READY'))
    hold = wire(status('HOLD'))
    unknown = dict(base, accepted_chunks=None, capacity_chunks=None, diagnostic_generated_utc=None,
                   source_offset=None, source_prefix_sha256=None, state='UNKNOWN')
    cases = []
    def add(name, candidate, *, enabled=True, before=starting, after=ready, valid=False, state=None):
        line = candidate if isinstance(candidate, str) else wire(candidate)
        text = '\n'.join(x for x in (before, line, after) if x) + '\n'
        cases.append({'name': name, 'wire': text, 'enabled': enabled, 'valid': valid, 'state': state})
    add('observed', base, valid=True, state='READY')
    add('unknown', unknown, valid=True, state='READY')
    add('unknown_then_observed', wire(unknown) + '\n' + wire(base), valid=True, state='READY')
    add('starting_only', base, after='', valid=True, state='STARTING')
    add('hold', dict(base, accepted_chunks=500001), before=hold, after='', valid=True, state='HOLD')
    add('maximum_integer', dict(base, accepted_chunks=2**63-1), valid=True, state='READY')
    add('disabled', base, enabled=False)
    add('before_status', base, before='')
    add('after_stopped', base, before=wire(status('STOPPED')))
    add('duplicate', wire(base) + '\n' + wire(base))
    add('duplicate_unknown', wire(unknown) + '\n' + wire(unknown))
    add('observed_then_unknown', wire(base) + '\n' + wire(unknown))
    add('unknown_then_observed_then_observed', wire(unknown) + '\n' + wire(base) + '\n' + wire(base))
    add('invalid_after_ready', dict(base, accepted_chunks=True), before=ready, after='')
    add('wrong_manifest', dict(base, manifest_sha256='c' * 64))
    add('forged_schema', dict(base, schema='wd.mind-local-owner-capacity.v2'))
    add('extra', dict(base, extra='synthetic-private-path'))
    missing = dict(base); del missing['freshness']; add('missing', missing)
    add('duplicate_key', wire(base).replace('"accepted_chunks":0', '"accepted_chunks":0,"accepted_chunks":0'))
    add('bool_count', dict(base, accepted_chunks=True))
    add('negative_count', dict(base, accepted_chunks=-1))
    add('float_count', dict(base, accepted_chunks=1.0))
    add('huge_count', dict(base, accepted_chunks=2**63))
    add('zero_capacity', dict(base, capacity_chunks=0))
    add('bool_capacity', dict(base, capacity_chunks=False))
    add('zero_offset', dict(base, source_offset=0))
    add('bad_hash', dict(base, source_prefix_sha256='B' * 64))
    add('bad_date', dict(base, diagnostic_generated_utc='2026-02-30T12:00:00Z'))
    add('future_date', dict(base, diagnostic_generated_utc='9999-01-01T00:00:00Z'))
    add('unknown_nonnull', dict(unknown, accepted_chunks=0))
    add('untrusted_field', dict(base, source_authentication='VERIFIED'))
    add('wrong_scope', dict(base, scope='FULL_SOURCE'))
    add('oversized', dict(base, extra='x' * 1100))
    helper = Path(__file__).resolve().parents[2] / 'ops/windows/reboot/Start-WdMindManualOwner.ps1'
    helper_path = str(helper).replace("'", "''")
    script = f"""
$ErrorActionPreference='Stop'
. '{helper_path}'
$cases=[Console]::In.ReadToEnd() | ConvertFrom-Json
$out=@()
foreach($case in $cases) {{
  $bytes=[Text.Encoding]::ASCII.GetBytes([string]$case.wire)
  $stream=New-Object IO.MemoryStream
  $process=[pscustomobject]@{{StandardOutput=[pscustomobject]@{{BaseStream=$stream}};HasExited=$false}}
  $record=@{{OutRead=[Threading.Tasks.Task]::FromResult($bytes.Length);ErrRead=$null;
    OutBuffer=$bytes;Line=(New-Object IO.MemoryStream);Manifest='{manifest}';
    DiagnosticsEnabled=$false;CapacityEnabled=[bool]$case.enabled;CapacityDiagnostic=$null;
    ReportedState='';Process=$process;Stopping=$false;Invalid=$false}}
  Update-WdMindManualOwner $record
  $token='b'*32;$global:WdMindManualOwnerRecords=@{{}};$global:WdMindManualOwnerRecords[$token]=$record
  $diag=$null;$getterError=$null
  try {{$diag=Get-WdMindManualOwnerCapacityDiagnostic ([pscustomobject]@{{Token=$token}})}}
  catch {{$getterError=$_.Exception.Message}}
  $out+= [pscustomobject]@{{name=$case.name;invalid=$record.Invalid;state=$record.State;
      capacity_state=$diag.state;binding_valid=$diag.binding_valid;count=$diag.accepted_chunks;getter_error=$getterError}}
}}
ConvertTo-Json -InputObject @($out) -Depth 8 -Compress
"""
    encoded = base64.b64encode(script.encode('utf-16le')).decode()
    proc = subprocess.run([shell, '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                          input=json.dumps(cases), capture_output=True, text=True, timeout=30, check=True)
    output = json.loads(proc.stdout.lstrip('\ufeff'))
    assert len(output) == len(cases)
    for case, result in zip(cases, output):
        assert result['name'] == case['name']
        assert result['invalid'] is not case['valid'], case['name']
        assert result['state'] == (case['state'] if case['valid'] else 'HOLD'), case['name']
        if case['valid']:
            assert result['getter_error'] is None
            assert result['binding_valid'] == 'UNKNOWN'
            assert result['capacity_state'] == ('UNKNOWN' if case['name'] == 'unknown' else 'OBSERVED')
        else:
            assert result['getter_error'] == ('mind_manual_diagnostics_disabled' if not case['enabled'] else 'mind_manual_status')
            assert result['capacity_state'] is None and result['count'] is None
    assert 'synthetic-private-path' not in proc.stdout + proc.stderr


@pytest.mark.skipif(os.name != 'nt', reason='PowerShell owner preflight')
@pytest.mark.parametrize('stage', ['INPUT', 'MANIFEST', 'POLICY', 'HELPER_OR_PYTHON', 'UNKNOWN', 'REGISTERED'])
@pytest.mark.parametrize('flag', ['', '-IncludeStartupDiagnostics', '-IncludeWorkerStageDiagnostics'])
def test_pre_spawn_refusal_reports_only_fixed_optin_stage(stage, flag):
    helper = Path(__file__).resolve().parents[2] / 'ops/windows/reboot/Start-WdMindManualOwner.ps1'
    path = str(helper).replace("'", "''")
    approval = 'synthetic-private' if stage == 'INPUT' else 'c'*64
    script = f"""
$ErrorActionPreference='Stop'
. '{path}'
function Read-WdMindManualPinnedBytes {{
    param([string]$Path,[string]$Sha256,[int]$MaxBytes)
    $name=[IO.Path]::GetFileName($Path)
    if (('{stage}' -ceq 'MANIFEST' -and $name -ceq 'deployment-manifest.json') -or
        ('{stage}' -ceq 'POLICY' -and $name -ceq 'wd-mind-identifier-policy-approval.json') -or
        ('{stage}' -ceq 'HELPER_OR_PYTHON' -and $name -ceq 'python.exe')) {{ throw 'synthetic-private-path' }}
    if ($name -ceq 'deployment-manifest.json') {{
        return ,[Text.Encoding]::UTF8.GetBytes('{{"schema_version":1,"files":{{"wd-mind-identifier-policy-approval.json":"{'a'*64}","Invoke-WdBridgePython.ps1":"{'a'*64}","Start-WdMindManualOwner.ps1":"{'a'*64}","tools-bootstrap/tools/wd_mind_runtime_child.py":"{'a'*64}"}}}}')
    }}
    if ($name -ceq 'wd-mind-identifier-policy-approval.json') {{
        return ,[Text.Encoding]::UTF8.GetBytes('{{"approval_reference_sha256":"{'c'*64}","identifier_policy_sha256":"{'b'*64}","schema":"wd.mind-identifier-policy-approval.v1"}}')
    }}
    if ($name -ceq 'Start-WdMindManualOwner.ps1') {{ return ,[Text.Encoding]::UTF8.GetBytes($script:WdMindManualOwnerHelperText) }}
    return ,[byte[]](1)
}}
if ('{stage}' -ceq 'UNKNOWN') {{ $global:WdMindManualOwnerRecords='synthetic-invalid-registry' }}
if ('{stage}' -ceq 'REGISTERED') {{
    function New-Object {{
        if ($args[0] -ceq 'Diagnostics.Process') {{
            $fake=[pscustomobject]@{{StartInfo=$null}}
            $fake | Add-Member -MemberType ScriptMethod -Name Start -Value {{ throw 'synthetic-private-start' }}
            return $fake
        }}
        Microsoft.PowerShell.Utility\\New-Object @args
    }}
}}
try {{
    $h=Start-WdMindManualOwner -Enabled -BundleRoot 'C:\\synthetic' -ExpectedManifestHash '{'a'*64}'
        -OperatorPolicyPath 'C:\\synthetic\\policy.json' -OperatorPolicySha256 '{'b'*64}'
        -OperatorApprovalReferenceSha256 '{approval}' -BridgePythonPath 'C:\\synthetic\\python.exe'
        -BridgePythonSha256 '{'d'*64}' {flag}
    $message=if ('{stage}' -ceq 'REGISTERED' -and $null -ne $h -and
        $global:WdMindManualOwnerRecords.ContainsKey([string]$h.Token) -and
        $global:WdMindManualOwnerRecords[[string]$h.Token].State -ceq 'HOLD') {{'registered-hold'}} else {{'unexpected-success'}}
}} catch {{ $message=$_.Exception.Message }}
[pscustomobject]@{{message=$message}} | ConvertTo-Json -Compress
""".replace("'\n        -", "' `\n        -")
    encoded = base64.b64encode(script.encode('utf-16le')).decode()
    shell = str(Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe')
    proc = subprocess.run([shell, '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                          capture_output=True, text=True, timeout=15, check=True)
    result = json.loads(proc.stdout.lstrip('\ufeff'))
    expected = ('registered-hold' if stage == 'REGISTERED' else
                'mind_manual_owner_refused:' + stage if flag else 'mind_manual_owner_refused')
    assert result['message'] == expected


@pytest.mark.skipif(os.name != 'nt', reason='PowerShell owner close diagnostic parser')
@pytest.mark.parametrize(('mode', 'accepted'), [
    ('stopping', True), ('hold', True), ('not_stopping', False),
    ('stopped', False), ('duplicate', False), ('wrong_reason', False),
    ('extra_field', False), ('stderr', False),
])
def test_close_diagnostic_requires_stop_and_is_retained(mode, accepted):
    helper = Path(__file__).resolve().parents[2] / 'ops/windows/reboot/Start-WdMindManualOwner.ps1'
    diagnostic = {'authority': 'none', 'reason': 'BOUND_EXPIRED_WORKER_UNCONFIRMED',
                  'schema': 'wd.mind-local-owner-close-diagnostic.v1'}
    if mode == 'wrong_reason':
        diagnostic['reason'] = 'STOPPED'
    if mode == 'extra_field':
        diagnostic['extra'] = 'invalid'
    line = json.dumps(diagnostic, sort_keys=True, separators=(',', ':')).encode('ascii') + b'\n'
    wire = line * (2 if mode == 'duplicate' else 1)
    path = str(helper).replace("'", "''")
    reported = 'STOPPED' if mode == 'stopped' else 'HOLD' if mode == 'hold' else 'READY'
    script = f"""
$ErrorActionPreference='Stop'
. '{path}'
$bytes=[Convert]::FromBase64String('{base64.b64encode(wire).decode()}')
$stream=New-Object System.IO.MemoryStream
$process=[pscustomobject]@{{StandardOutput=[pscustomobject]@{{BaseStream=$stream}};HasExited=$false}}
$record=@{{OutRead=$(if ('{mode}' -ceq 'stderr') {{$null}} else {{[System.Threading.Tasks.Task]::FromResult($bytes.Length)}});
 ErrRead=$(if ('{mode}' -ceq 'stderr') {{[System.Threading.Tasks.Task]::FromResult($bytes.Length)}} else {{$null}});
 OutBuffer=$bytes;Line=(New-Object System.IO.MemoryStream);Manifest='{'a'*64}';
 DiagnosticsEnabled=$false;DiagnosticCount=0;ReportedState='{reported}';Process=$process;
 Stopping=${str(mode != 'not_stopping').lower()};Invalid=$false;CloseDiagnostic=$null}}
$global:WdMindManualOwnerRecords=@{{'{'a'*32}'=$record}}
$handle=[pscustomobject]@{{Token='{'a'*32}'}}
Update-WdMindManualOwner $record
$result=if ($record.Invalid) {{$null}} else {{Get-WdMindManualOwnerCloseDiagnostic $handle}}
[pscustomobject]@{{invalid=$record.Invalid;state=$record.State;reason=$result.reason;authority=$result.authority}} | ConvertTo-Json -Compress
"""
    encoded = base64.b64encode(script.encode('utf-16le')).decode()
    shell = str(Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe')
    proc = subprocess.run([shell, '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                          capture_output=True, text=True, timeout=15, check=True)
    result = json.loads(proc.stdout.lstrip('\ufeff'))
    assert result == {'invalid': not accepted, 'state': 'HOLD',
                      'reason': 'BOUND_EXPIRED_WORKER_UNCONFIRMED' if accepted else None,
                      'authority': 'none' if accepted else None}


@pytest.mark.skipif(os.name != 'nt', reason='PowerShell owner diagnostic parser')
@pytest.mark.parametrize('shell', [
    str(Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'),
    pytest.param(shutil.which('pwsh'), marks=pytest.mark.skipif(shutil.which('pwsh') is None,
                                                                reason='PowerShell 7 unavailable')),
], ids=['powershell51', 'pwsh7'])
@pytest.mark.parametrize('mode', ['v1', 'v2', 'off', 'extra', 'after_status', 'duplicate', 'split', 'forged_hash'])
def test_preanchor_diagnostic_is_optin_and_never_allows_ready(mode, shell):
    helper = Path(__file__).resolve().parents[2] / 'ops/windows/reboot/Start-WdMindManualOwner.ps1'
    record = {'authority':'none', 'reason':'IMPORT', 'schema':'wd.mind-local-owner-preanchor-diagnostic.v1'}
    if mode == 'forged_hash':
        record['manifest_sha256'] = 'd' * 64
    line = json.dumps(record, sort_keys=True, separators=(',', ':')).encode('ascii') + b'\n'
    status = {'authority':'none', 'caller_authentication':'UNKNOWN', 'index_refresh':'NOT_SCHEDULED',
              'manifest_sha256':'a'*64, 'schema':'wd.mind-local-owner-status.v1',
              'source_authentication':'UNKNOWN', 'state':'READY'}
    ready = json.dumps(status, sort_keys=True, separators=(',', ':')).encode('ascii') + b'\n'
    wire = (ready + line if mode == 'after_status' else line + ready if mode == 'extra'
            else line + line if mode == 'duplicate' else line)
    path = str(helper).replace("'", "''")
    script = f"""
$ErrorActionPreference='Stop'
. '{path}'
$bytes=[Convert]::FromBase64String('{base64.b64encode(wire).decode()}')
$stream=New-Object System.IO.MemoryStream
$process=[pscustomobject]@{{StandardOutput=[pscustomobject]@{{BaseStream=$stream}};HasExited=$false}}
$record=@{{OutRead=[System.Threading.Tasks.Task]::FromResult($bytes.Length);ErrRead=$null;
 OutBuffer=$bytes;Line=(New-Object System.IO.MemoryStream);Manifest='{'a'*64}';
 DiagnosticsEnabled=${str(mode != 'off').lower()};WorkerDiagnosticsEnabled=${str(mode == 'v2').lower()};
 DiagnosticCount=0;ReportedState='';StartDiagnostic=$null;Process=$process;Stopping=$false;Invalid=$false}}
Update-WdMindManualOwner $record
if ('{mode}' -ceq 'split') {{
  $next=[Convert]::FromBase64String('{base64.b64encode(ready).decode()}')
  $record.OutBuffer=$next; $record.OutRead=[System.Threading.Tasks.Task]::FromResult($next.Length)
  Update-WdMindManualOwner $record
}}
[pscustomobject]@{{invalid=$record.Invalid;state=$record.State;reason=$record.StartDiagnostic.reason}} | ConvertTo-Json -Compress
"""
    encoded = base64.b64encode(script.encode('utf-16le')).decode()
    proc = subprocess.run([shell, '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                          capture_output=True, text=True, timeout=15, check=True)
    result = json.loads(proc.stdout.lstrip('\ufeff'))
    accepted = mode in ('v1', 'v2')
    assert result == {'invalid': not accepted, 'state': 'HOLD', 'reason': 'IMPORT' if accepted or mode in ('extra', 'duplicate', 'split') else None}


@pytest.mark.skipif(os.name != 'nt', reason='PowerShell owner diagnostic parser')
@pytest.mark.parametrize(('reason', 'accepted'), [
    ('ADMISSION_MISSING', True), ('ADMISSION_RESERVED', True),
    ('ADMISSION_LOCK_UNAVAILABLE', True), ('ADMISSION_OTHER', False),
])
@pytest.mark.parametrize('version', [1, 2])
def test_manual_owner_accepts_reservation_diagnostic(reason, accepted, version):
    """The wrapper accepts the exact pinned child's reservation refusal, without a live owner."""
    helper = Path(__file__).resolve().parents[2] / 'ops/windows/reboot/Start-WdMindManualOwner.ps1'
    manifest = 'a' * 64
    status = {'authority': 'none', 'caller_authentication': 'UNKNOWN',
              'index_refresh': 'NOT_SCHEDULED', 'manifest_sha256': manifest,
              'schema': 'wd.mind-local-owner-status.v1',
              'source_authentication': 'UNKNOWN', 'state': 'HOLD'}
    diagnostic = {'authority': 'none', 'exception_type': 'LaunchRefused',
                  'reason': reason, 'schema': f'wd.mind-local-owner-start-diagnostic.v{version}'}
    if version == 2:
        diagnostic.update(worker_exit_code=None, worker_exit_stage_hint='UNKNOWN')
    wire = b''.join(json.dumps(record, sort_keys=True, separators=(',', ':')).encode('ascii') + b'\n'
                    for record in (status, diagnostic))
    path = str(helper).replace("'", "''")
    script = f"""
$ErrorActionPreference='Stop'
. '{path}'
$bytes=[Convert]::FromBase64String('{base64.b64encode(wire).decode()}')
$stream=New-Object System.IO.MemoryStream
$process=[pscustomobject]@{{StandardOutput=[pscustomobject]@{{BaseStream=$stream}};HasExited=$false}}
$record=@{{OutRead=[System.Threading.Tasks.Task]::FromResult($bytes.Length);ErrRead=$null;
 OutBuffer=$bytes;Line=(New-Object System.IO.MemoryStream);Manifest='{manifest}';
 DiagnosticsEnabled=$true;WorkerDiagnosticsEnabled=${str(version == 2).lower()};
 DiagnosticCount=0;ReportedState='STARTING';Process=$process;Stopping=$false;Invalid=$false}}
Update-WdMindManualOwner $record
[pscustomobject]@{{invalid=$record.Invalid;state=$record.State;reason=$record.StartDiagnostic.reason}} | ConvertTo-Json -Compress
"""
    encoded = base64.b64encode(script.encode('utf-16le')).decode()
    shell = str(Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe')
    proc = subprocess.run([shell, '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                          capture_output=True, text=True, timeout=15, check=True)
    result = json.loads(proc.stdout.lstrip('\ufeff'))
    assert result == {'invalid': not accepted, 'state': 'HOLD', 'reason': reason if accepted else None}


@pytest.mark.skipif(os.name != 'nt', reason='Windows process and inherited pipe integration')
@pytest.mark.parametrize('shell', [
    str(Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'),
    pytest.param(shutil.which('pwsh'), marks=pytest.mark.skipif(shutil.which('pwsh') is None,
                                                                reason='PowerShell 7 unavailable')),
], ids=['powershell51', 'pwsh7'])
@pytest.mark.parametrize('mode', ['normal', 'normal_scriptblock', 'capacity', 'capacity_v1', 'capacity_v2',
                                  'capacity_real_frame', 'capacity_real_frame_ready_first', 'bad_status', 'bad_scriptblock', 'bad_policy', 'bad_wrapper',
                                  'missing_python_pin', 'wrong_python_pin', 'missing_python_path', 'disabled',
                                  'bad_manifest', 'bad_descriptor', 'bad_child', 'bad_approval'])
def test_manual_owner_synthetic_process_keeps_stdin_and_stops(tmp_path, mode, shell):
    bundle = tmp_path / 'bundle'
    bundle.mkdir()
    helper = Path(__file__).resolve().parents[2] / 'ops/windows/reboot/Start-WdMindManualOwner.ps1'
    helper_copy = bundle / helper.name
    helper_copy.write_bytes(helper.read_bytes())
    child = bundle / 'tools-bootstrap/tools/wd_mind_runtime_child.py'
    child.parent.mkdir(parents=True)
    child.write_bytes(b'# synthetic pinned entrypoint\n')
    policy = b'{"synthetic":"private-test-only"}'
    policy_path = tmp_path / 'policy.json'
    policy_path.write_bytes(policy)
    policy_sha = hashlib.sha256(policy).hexdigest()
    approval_sha = 'c' * 64
    descriptor = json.dumps({'schema':'wd.mind-identifier-policy-approval.v1',
        'identifier_policy_sha256':policy_sha, 'approval_reference_sha256':approval_sha},
        sort_keys=True, separators=(',', ':')).encode()
    descriptor_path = bundle / 'wd-mind-identifier-policy-approval.json'
    descriptor_path.write_bytes(descriptor)
    program = '''import hashlib,json,os,sys\np=open(sys.argv[1],"rb").read()\nif hashlib.sha256(p).hexdigest()!=sys.argv[2]: sys.exit(5)\ndef emit(s):\n print(json.dumps({"schema":"wd.mind-local-owner-status.v1","state":s,"manifest_sha256":os.environ["WD_REBOOT_EXPECTED_MANIFEST_HASH"].lower(),"authority":"none","caller_authentication":"UNKNOWN","source_authentication":"UNKNOWN","index_refresh":"NOT_SCHEDULED"},sort_keys=True,separators=(",",":")),flush=True)\nemit("STARTING");emit("READY")\nif sys.stdin.buffer.read(1)!=b"": sys.exit(6)\nemit("STOPPED")\n'''
    real_frame_mode = mode in ('capacity_real_frame', 'capacity_real_frame_ready_first')
    capacity_mode = mode in ('capacity', 'capacity_v1', 'capacity_v2') or real_frame_mode
    if real_frame_mode:
        # Actual frame factory, synthetic data/transport; no real owner/model/source.
        source_root = str(Path(__file__).resolve().parents[2])
        setup = ('sys.path.insert(0,' + repr(source_root) + ');'
                 'from tools.wd_mind_runtime_child import _capacity_diagnostic_frame;'
                 'm=os.environ["WD_REBOOT_EXPECTED_MANIFEST_HASH"].lower();'
                 'f=lambda v:print(json.dumps(_capacity_diagnostic_frame(m,v),sort_keys=True,separators=(",",":"),ensure_ascii=True),flush=True);')
        ready_before = 'emit("READY");' if mode == 'capacity_real_frame_ready_first' else ''
        ready_after = '' if ready_before else 'emit("READY");'
        sequence = (setup + 'emit("STARTING");' + ready_before + 'f(None);'
                    'assert sys.stdin.buffer.read(1)==b"G";'
                    'f((2,500000,"b"*64,20,"2020-01-01T00:00:00Z"));' + ready_after)
        program = program.replace('emit("STARTING");emit("READY")', sequence)
    elif capacity_mode:
        # The record is generated by an inert synthetic child and has no live source.
        capacity_record = {'accepted_chunks': 2, 'authority': 'none', 'capacity_chunks': 500000,
            'freshness': 'UNKNOWN', 'manifest_sha256': '__MANIFEST__', 'diagnostic_generated_utc': '2020-01-01T00:00:00Z',
            'same_user_aba': 'UNKNOWN', 'schema': 'wd.mind-local-owner-capacity.v1', 'scope': 'PINNED_PREFIX',
            'source_authentication': 'UNKNOWN', 'source_offset': 20, 'source_prefix_sha256': 'b' * 64, 'state': 'OBSERVED'}
        capacity_wire = json.dumps(capacity_record, sort_keys=True, separators=(',', ':'))
        emit_capacity = 'print(' + repr(capacity_wire) + '.replace("__MANIFEST__",os.environ["WD_REBOOT_EXPECTED_MANIFEST_HASH"].lower()),flush=True)'
        program = program.replace('emit("STARTING");emit("READY")', 'emit("STARTING");' + emit_capacity + ';emit("READY")')
    if mode == 'bad_status': program = program.replace('emit("READY")', 'emit("INVALID")')
    marker = tmp_path / '.codex-audit' / 'synthetic-owner-stage.txt'
    marker.parent.mkdir()
    program = program.replace('p=open(sys.argv[1],"rb").read()',
        'open(sys.argv[3],"w").write("started")\np=open(sys.argv[1],"rb").read()\nopen(sys.argv[3],"a").write(" policy-read")')
    child.write_text(program, encoding='utf-8')
    def psquote(value):
        return "'" + str(value).replace("'", "''") + "'"
    bridge_python = marker.parent / 'synthetic-bridge-python.exe'
    bridge_python.write_bytes(b'synthetic-bridge-interpreter')
    bridge_python_sha = hashlib.sha256(bridge_python.read_bytes()).hexdigest()
    wrapper = bundle / 'Invoke-WdBridgePython.ps1'
    suffixes = {'capacity': ['--include-capacity-diagnostics'],
                'capacity_real_frame': ['--include-capacity-diagnostics'],
                'capacity_real_frame_ready_first': ['--include-capacity-diagnostics'],
                'capacity_v1': ['--start-diagnostics', '--include-capacity-diagnostics'],
                'capacity_v2': ['--start-diagnostics-v2', '--include-capacity-diagnostics']}.get(mode, [])
    suffix_checks = ''.join(' -or $Rest[' + str(7 + i) + '] -cne ' + psquote(value)
                            for i, value in enumerate(suffixes))
    wrapper.write_text('''param([Parameter(Position=0)][string]$Tool,[switch]$VerifyPackage,
      [Parameter(ValueFromRemainingArguments)][string[]]$Rest)
if ($env:WD_SECRET_TEST_MARKER) { exit 9 }
if ($env:WD_BRIDGE_PYTHON_SHA256 -ine ''' + psquote(bridge_python_sha) + ''' -or
    $env:WD_BRIDGE_PYTHON -ine ''' + psquote(bridge_python) + ''') { exit 8 }
if (-not $VerifyPackage -or $Tool -cne 'tools/wd_mind_runtime_child.py' -or
    $Rest.Count -ne ''' + str(7 + len(suffixes)) + ''' -or $Rest[0] -cne '--mind-local-candidate-owner' -or
    $Rest[1] -cne '--policy-path' -or $Rest[2] -cne ''' + psquote(policy_path) + ''' -or
    $Rest[3] -cne '--policy-sha256' -or $Rest[4] -cne ''' + psquote(policy_sha) + ''' -or
    $Rest[5] -cne '--approval-reference-sha256' -or $Rest[6] -cne ''' + psquote(approval_sha) + suffix_checks + ''') { exit 7 }
& ''' + psquote(sys.executable) + ' -I -S -B ' + psquote(child) + ' $Rest[2] $Rest[4] ' + psquote(marker) + '''
exit $LASTEXITCODE
''', encoding='utf-8')
    files = {str(path.relative_to(bundle)).replace('\\','/'): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in (helper_copy, child, descriptor_path, wrapper)}
    manifest = json.dumps({'schema_version':1, 'files':files}, separators=(',', ':')).encode()
    (bundle / 'deployment-manifest.json').write_bytes(manifest)
    manifest_sha = hashlib.sha256(manifest).hexdigest()
    if mode == 'bad_wrapper': wrapper.write_text('# modified', encoding='utf-8')
    if mode == 'bad_manifest': manifest_sha = '0' * 64
    if mode == 'bad_descriptor': descriptor_path.write_bytes(b'{}')
    if mode == 'bad_child': child.write_bytes(b'# changed after manifest\n')
    if mode == 'bad_approval': approval_sha = 'd' * 64
    actual_policy_sha = 'd' * 64 if mode == 'bad_policy' else policy_sha
    if mode in ('normal_scriptblock', 'bad_scriptblock'):
        extra = " + [Environment]::NewLine + '# synthetic altered text'" if mode == 'bad_scriptblock' else ''
        loader = '. ([scriptblock]::Create([IO.File]::ReadAllText(' + psquote(helper_copy) + ')' + extra + '))'
    else:
        loader = '. ' + psquote(helper_copy)
    harness = '''$ErrorActionPreference='Stop'
$env:WD_SECRET_TEST_MARKER='synthetic-marker-do-not-inherit'
''' + loader + '''
$result=[ordered]@{accepted=$false; states=@(); final=''; detail=''; stage=''}
try {
  $h=Start-WdMindManualOwner ''' + ('' if mode == 'disabled' else '-Enabled ') + '''-BundleRoot ''' + psquote(bundle) + ' -ExpectedManifestHash ' + psquote(manifest_sha) + \
        ' -OperatorPolicyPath ' + psquote(policy_path) + ' -OperatorPolicySha256 ' + psquote(actual_policy_sha) + \
        ' -OperatorApprovalReferenceSha256 ' + psquote(approval_sha) + \
        ('' if mode == 'missing_python_pin' else ' -BridgePythonSha256 ' + psquote('b'*64 if mode == 'wrong_python_pin' else bridge_python_sha)) + \
        ('' if mode == 'missing_python_path' else ' -BridgePythonPath ' + psquote(bridge_python)) + \
        (' -IncludeCapacityDiagnostics' if capacity_mode else '') + \
        (' -IncludeStartupDiagnostics' if mode == 'capacity_v1' else ' -IncludeWorkerStageDiagnostics' if mode == 'capacity_v2' else '') + '''
  $result.accepted=$true
  $deadline=[DateTime]::UtcNow.AddSeconds(7)
  ''' + ('''do { $u=Get-WdMindManualOwnerCapacityDiagnostic $h; $first=Get-WdMindManualOwnerStatus $h; Start-Sleep -Milliseconds 30 }
    while($u.state -cne 'UNKNOWN' -and -not $global:WdMindManualOwnerRecords[$h.Token].Invalid -and [DateTime]::UtcNow -lt $deadline)
  if($u.state -cne 'UNKNOWN' -or $first.state -cne ''' + psquote('READY' if mode == 'capacity_real_frame_ready_first' else 'STARTING') + ''') {throw 'missing_first_capacity'}
  $result['first_capacity']=$u.state;$result['first_state']=$first.state
  $global:WdMindManualOwnerRecords[$h.Token].Process.StandardInput.Write('G')
  $global:WdMindManualOwnerRecords[$h.Token].Process.StandardInput.Flush()
  ''' if real_frame_mode else '') + '''
  do { $s=Get-WdMindManualOwnerStatus $h; $result.states+=@($s.state); Start-Sleep -Milliseconds 30 }
    while($s.state -notin @('READY','FAILED') -and -not $global:WdMindManualOwnerRecords[$h.Token].Invalid -and [DateTime]::UtcNow -lt $deadline)
  $result.stage=if([IO.File]::Exists(''' + psquote(marker) + ''')){[IO.File]::ReadAllText(''' + psquote(marker) + ''')}else{'not-started'}
  if($s.state -eq 'READY') {
    ''' + ('''do {
      $d=Get-WdMindManualOwnerCapacityDiagnostic $h
      $s=Get-WdMindManualOwnerStatus $h
      if($s.state -cne 'READY' -or $global:WdMindManualOwnerRecords[$h.Token].Invalid){throw 'capacity_not_ready'}
      if($d.state -cne 'OBSERVED'){Start-Sleep -Milliseconds 30}
    } while($d.state -cne 'OBSERVED' -and [DateTime]::UtcNow -lt $deadline)
    if($d.state -cne 'OBSERVED'){throw 'capacity_not_observed'}
    $result['capacity']=[pscustomobject]@{count=$d.accepted_chunks;binding=$d.binding_valid;scope=$d.scope}
    ''' if real_frame_mode else "$d=Get-WdMindManualOwnerCapacityDiagnostic $h; $result['capacity']=[pscustomobject]@{count=$d.accepted_chunks;binding=$d.binding_valid;scope=$d.scope}" if capacity_mode else '') + '''
    for($i=0;$i -lt 5;$i++){ $s=Get-WdMindManualOwnerStatus $h; if($s.state -ne 'READY'){throw 'not_ready'} }
    do { $s=Stop-WdMindManualOwner $h; Start-Sleep -Milliseconds 30 }
      while($s.state -notin @('STOPPED','FAILED') -and [DateTime]::UtcNow -lt $deadline)
  } elseif($global:WdMindManualOwnerRecords[$h.Token].Invalid) {
    do { $s=Stop-WdMindManualOwner $h; Start-Sleep -Milliseconds 30 }
      while($s.state -notin @('STOPPED','FAILED') -and [DateTime]::UtcNow -lt $deadline)
  }
  $result.final=$s.state
  $r=$global:WdMindManualOwnerRecords[$h.Token]
  $result.detail=[pscustomobject]@{reported=$r.ReportedState; invalid=$r.Invalid;
    out_done=$r.OutDone; err_done=$r.ErrDone; exited=$r.Process.HasExited;
    exit_code=$(if($r.Process.HasExited){$r.Process.ExitCode}else{$null});
    synthetic_error=([Text.Encoding]::UTF8.GetString($r.ErrBuffer).Trim([char]0)+$(if($r.Process.HasExited){$r.Process.StandardError.ReadToEnd()}else{''})).Replace('private-test-only','<synthetic>')}
} catch { $result.detail='refused' }
$result.stage=if([IO.File]::Exists(''' + psquote(marker) + ''')){[IO.File]::ReadAllText(''' + psquote(marker) + ''')}else{'not-started'}
$result|ConvertTo-Json -Compress -Depth 5
'''
    encoded = base64.b64encode(harness.encode('utf-16le')).decode()
    proc = subprocess.Popen([shell, '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        stdout, stderr = proc.communicate(timeout=30)
    except subprocess.TimeoutExpired:
        pytest.fail('synthetic process did not exit; no kill attempted')
    assert proc.returncode == 0, stderr
    assert policy.decode() not in stdout + stderr
    assert 'synthetic-marker-do-not-inherit' not in stdout + stderr
    result = json.loads(stdout.lstrip('\ufeff'))
    if mode in ('normal', 'normal_scriptblock', 'capacity', 'capacity_v1', 'capacity_v2',
                'capacity_real_frame', 'capacity_real_frame_ready_first'):
        detail = dict(result['detail'], synthetic_stage=result['stage'])
        assert result['accepted'] is True and 'READY' in result['states'] and result['final'] == 'STOPPED', json.dumps(detail)
        if capacity_mode:
            assert result['capacity'] == {'count': 2, 'binding': 'UNKNOWN', 'scope': 'PINNED_PREFIX'}
        if real_frame_mode:
            assert result['first_capacity'] == 'UNKNOWN'
            assert result['first_state'] == ('READY' if mode == 'capacity_real_frame_ready_first' else 'STARTING')
    elif mode == 'bad_status':
        assert result['accepted'] is True and result['final'] == 'FAILED', result
    else:
        assert result['accepted'] is False and result['detail'] == 'refused', result
        assert result['stage'] == 'not-started', result


