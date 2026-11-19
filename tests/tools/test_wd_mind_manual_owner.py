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
@pytest.mark.parametrize('fault', ['none', 'missing', 'truncated', 'duplicate', 'wrong_manifest',
                                   'wrong_exit', 'stderr', 'missing_eof', 'crlf'])
def test_preowner_refusal_requires_bound_complete_proof_and_eof(fault):
    helper = Path(__file__).resolve().parents[2] / 'ops/windows/reboot/Start-WdMindManualOwner.ps1'
    manifest = 'a' * 64
    proof = json.dumps({'authority':'none', 'manifest_sha256':manifest,
        'schema':'wd.mind-local-owner-admission.v1', 'state':'PRE_OWNER_REFUSED'},
        sort_keys=True, separators=(',', ':')).encode('ascii') + b'\n'
    if fault == 'missing': proof = b''
    if fault == 'truncated': proof = proof[:-1]
    if fault == 'crlf': proof = proof[:-1] + b'\r\n'
    if fault == 'duplicate': proof += proof
    if fault == 'wrong_manifest': proof = proof.replace(b'a' * 64, b'b' * 64)
    path = str(helper).replace("'", "''")
    script = f"""
$ErrorActionPreference='Stop'
. '{path}'
$bytes=[Convert]::FromBase64String('{base64.b64encode(proof).decode()}')
$stream=New-Object System.IO.MemoryStream
$process=[pscustomobject]@{{StandardOutput=[pscustomobject]@{{BaseStream=$stream}};
 HasExited=$true;ExitCode={0 if fault == 'wrong_exit' else 2}}}
$record=@{{OutRead=[System.Threading.Tasks.Task]::FromResult($bytes.Length);
 ErrRead=[System.Threading.Tasks.Task]::FromResult({1 if fault == 'stderr' else 0});
 OutBuffer=$bytes;ErrBuffer=[byte[]]@(65);Line=(New-Object System.IO.MemoryStream);
 Manifest='{manifest}';PreOwnerProofEnabled=$true;PreOwnerProofSeen=$false;
 ReportedState='';Process=$process;Stopping=$false;Invalid=$false;OutDone=$false;ErrDone=$false}}
Update-WdMindManualOwner $record
if ('{fault}' -cne 'missing_eof') {{ Update-WdMindManualOwner $record }}
[pscustomobject]@{{invalid=$record.Invalid;state=$record.State}} | ConvertTo-Json -Compress
"""
    encoded = base64.b64encode(script.encode('utf-16le')).decode()
    shell = str(Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe')
    names = ('SYSTEMROOT', 'WINDIR', 'SYSTEMDRIVE', 'PATH', 'PATHEXT', 'TEMP', 'TMP', 'USERPROFILE', 'LOCALAPPDATA', 'APPDATA')
    env = {name: os.environ[name] for name in names if name in os.environ}
    proc = subprocess.run([shell, '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                          capture_output=True, text=True, timeout=15, check=True, env=env)
    result = json.loads(proc.stdout.lstrip('\ufeff'))
    assert (result['state'] == 'PRE_OWNER_REFUSED' and not result['invalid']) == (fault == 'none')


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
@pytest.mark.parametrize('mode', ['normal', 'normal_scriptblock', 'bad_status', 'bad_scriptblock', 'bad_policy', 'bad_wrapper',
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
    wrapper.write_text('''param([Parameter(Position=0)][string]$Tool,[switch]$VerifyPackage,
      [Parameter(ValueFromRemainingArguments)][string[]]$Rest)
if ($env:WD_SECRET_TEST_MARKER) { exit 9 }
if ($env:WD_BRIDGE_PYTHON_SHA256 -ine ''' + psquote(bridge_python_sha) + ''' -or
    $env:WD_BRIDGE_PYTHON -ine ''' + psquote(bridge_python) + ''') { exit 8 }
if (-not $VerifyPackage -or $Tool -cne 'tools/wd_mind_runtime_child.py' -or
    $Rest.Count -ne 7 -or $Rest[0] -cne '--mind-local-candidate-owner' -or
    $Rest[1] -cne '--policy-path' -or $Rest[2] -cne ''' + psquote(policy_path) + ''' -or
    $Rest[3] -cne '--policy-sha256' -or $Rest[4] -cne ''' + psquote(policy_sha) + ''' -or
    $Rest[5] -cne '--approval-reference-sha256' -or $Rest[6] -cne ''' + psquote(approval_sha) + ''') { exit 7 }
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
        ('' if mode == 'missing_python_path' else ' -BridgePythonPath ' + psquote(bridge_python)) + '''
  $result.accepted=$true
  $deadline=[DateTime]::UtcNow.AddSeconds(7)
  do { $s=Get-WdMindManualOwnerStatus $h; $result.states+=@($s.state); Start-Sleep -Milliseconds 30 }
    while($s.state -notin @('READY','FAILED') -and -not $global:WdMindManualOwnerRecords[$h.Token].Invalid -and [DateTime]::UtcNow -lt $deadline)
  $result.stage=if([IO.File]::Exists(''' + psquote(marker) + ''')){[IO.File]::ReadAllText(''' + psquote(marker) + ''')}else{'not-started'}
  if($s.state -eq 'READY') {
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
    if mode in ('normal', 'normal_scriptblock'):
        detail = dict(result['detail'], synthetic_stage=result['stage'])
        assert result['accepted'] is True and 'READY' in result['states'] and result['final'] == 'STOPPED', json.dumps(detail)
    elif mode == 'bad_status':
        assert result['accepted'] is True and result['final'] == 'FAILED', result
    else:
        assert result['accepted'] is False and result['detail'] == 'refused', result
        assert result['stage'] == 'not-started', result


