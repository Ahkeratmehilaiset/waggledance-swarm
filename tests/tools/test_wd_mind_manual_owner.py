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


@pytest.mark.skipif(os.name != 'nt', reason='PowerShell JSON key scanning')
@pytest.mark.parametrize(('document', 'expected'), [
    ('{"a":{"x":1},"b":{"x":2},"text":"brace } colon : and quote \\\""}', 'OK'),
    ('{"a":1,"a":2}', 'REFUSED'),
    ('{"a":1,"\\u0061":2}', 'REFUSED'),
    ('{"a":[{"b":1,"b":2}]}', 'REFUSED'),
    ('{"a":1,"A":2}', 'REFUSED'),
    ('{"\U0001f600":1,"\\ud83d\\ude00":2}', 'REFUSED'),
    ('{"a":', 'REFUSED'),
    ('{"a":true} trailing', 'REFUSED'),
    ('[' * 65 + '0' + ']' * 65, 'REFUSED'),
    ('{"' + ('x' * 8192) + '":0}', 'REFUSED'),
])
def test_manual_json_scanner_preserves_object_scope_and_decodes_keys(document, expected):
    helper = Path(__file__).resolve().parents[2] / 'ops/windows/reboot/Start-WdMindManualOwner.ps1'
    text = helper.read_text(encoding='utf-8')
    marker = 'function Assert-WdManualUniqueJson([string] $Text) {'
    parts = text.split(marker)
    assert len(parts) == 3
    assert parts[1].split('\n}', 1)[0] == parts[2].split('\n}', 1)[0]
    shell = str(Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe')
    payload = base64.b64encode(document.encode()).decode()
    script = f"""
. '{str(helper).replace("'", "''")}'
$s=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{payload}'))
try {{ Assert-WdManualUniqueJson $s; Write-Output 'OK' }} catch {{ Write-Output 'REFUSED' }}
"""
    proc = subprocess.run([shell, '-NoProfile', '-NonInteractive', '-EncodedCommand',
                           base64.b64encode(script.encode('utf-16le')).decode()],
                          capture_output=True, text=True, timeout=15, check=True)
    assert proc.stdout.strip() == expected


@pytest.mark.skipif(os.name != 'nt', reason='PowerShell JSON scanner resource bounds')
@pytest.mark.parametrize('mode', ['document_size', 'key_count'])
def test_manual_json_scanner_has_document_and_key_bounds(tmp_path, mode):
    helper = Path(__file__).resolve().parents[2] / 'ops/windows/reboot/Start-WdMindManualOwner.ps1'
    path = tmp_path / 'synthetic.json'
    document = ('[' + (' ' * 4194305) + ']') if mode == 'document_size' else (
        '{' + ','.join('"k%d":0' % i for i in range(16385)) + '}')
    path.write_text(document)
    shell = str(Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe')
    script = f"""
. '{str(helper).replace("'", "''")}'
$s=[IO.File]::ReadAllText('{str(path).replace("'", "''")}')
try {{ Assert-WdManualUniqueJson $s; Write-Output 'OK' }} catch {{ Write-Output 'REFUSED' }}
"""
    proc = subprocess.run([shell, '-NoProfile', '-NonInteractive', '-EncodedCommand',
                           base64.b64encode(script.encode('utf-16le')).decode()],
                          capture_output=True, text=True, timeout=90, check=True)
    assert proc.stdout.strip() == 'REFUSED'


@pytest.mark.parametrize('mode', ['pinned', 'tampered_dependency', 'unlisted_dependency', 'wrong_manifest', 'real_disabled',
                                  'wrong_commit', 'wrong_pointer_commit', 'wrong_pointer_hash', 'duplicate_pointer', 'different_bundle'])
def test_admin_verified_bytes_closes_package_imports(tmp_path, mode):
    helper = Path(__file__).resolve().parents[2] / 'ops/windows/reboot/Start-WdMindManualOwner.ps1'
    text = helper.read_text(encoding='utf-8')
    trampoline = text.split("$script:WdMindAdminVerifiedBytes = @'\n", 1)[1].split("\n'@", 1)[0]
    commit = 'a' * 40
    store = tmp_path / 'bundles'
    pointer_path = tmp_path / 'state.json'
    root = store / commit
    trampoline = trampoline.replace(r"r'C:\Python\WD_REBOOT_STATE_CURRENT.json'", repr(str(pointer_path)))
    trampoline = trampoline.replace(r"r'C:\Python\wd-reboot-bundles'", repr(str(store)))
    code = root / 'tools-bootstrap/tools'
    code.mkdir(parents=True)
    sources = {
        'tools/__init__.py': b'',
        'tools/dependency.py': b'VALUE = 37\n',
        'tools/wd_mind_owner_reservation_admin.py': (
            b'from tools import dependency\n'
            + (b'from tools import unlisted\n' if mode == 'unlisted_dependency' else b'')
            + b'def main(argv):\n print("SYNTHETIC", dependency.VALUE)\n return 2\n'),
    }
    if mode == 'real_disabled':
        repo = helper.parents[3]
        definition = json.loads((repo / 'ops/windows/reboot/bridge-code-files.json').read_text())
        sources = {p: (repo / p).read_bytes() for p in definition['python_files']
                   if p.endswith('.py') and p.startswith(('tools/', 'waggledance/'))}
    if mode == 'unlisted_dependency':
        (code / 'unlisted.py').write_bytes(b'raise AssertionError("unlisted ran")\n')
    files = {}
    for relative, content in sources.items():
        path = root / 'tools-bootstrap' / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        files['tools-bootstrap/' + relative] = hashlib.sha256(content).hexdigest()
    definition = json.dumps({'python_files': list(sources)}, separators=(',', ':')).encode()
    (root / 'bridge-code-files.json').write_bytes(definition)
    files['bridge-code-files.json'] = hashlib.sha256(definition).hexdigest()
    manifest = json.dumps({'schema_version': 1, 'source_commit': commit, 'files': files}, separators=(',', ':')).encode()
    (root / 'deployment-manifest.json').write_bytes(manifest)
    pin = hashlib.sha256(manifest).hexdigest()
    if mode == 'tampered_dependency':
        (code / 'dependency.py').write_bytes(b'raise AssertionError("tampered ran")\n')
    if mode == 'wrong_manifest':
        pin = '0' * 64
    pointer = {'source_commit': commit, 'final_commit': commit, 'active_bundle': str(root),
               'manifest_sha256': pin, 'final_manifest_sha256': pin}
    if mode == 'wrong_pointer_commit': pointer['final_commit'] = 'b' * 40
    if mode == 'wrong_pointer_hash': pointer['manifest_sha256'] = '0' * 64
    if mode == 'different_bundle': pointer['active_bundle'] = str(store / ('b' * 40))
    raw_pointer = json.dumps(pointer, separators=(',', ':'))
    if mode == 'duplicate_pointer': raw_pointer = raw_pointer[:-1] + ',"source_commit":"' + commit + '"}'
    pointer_path.write_text(raw_pointer)
    argument_commit = 'b' * 40 if mode == 'wrong_commit' else commit
    result = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', trampoline, str(root), pin, 'reset', argument_commit],
                            capture_output=True, text=True, timeout=15, check=False)
    assert result.returncode == 2
    assert result.stdout.strip() == ('SYNTHETIC 37' if mode == 'pinned' else '')
    assert result.stderr.strip() == ('owner_marker_production_route_unavailable' if mode == 'real_disabled' else '')


@pytest.mark.skipif(os.name != 'nt', reason='PowerShell manual admin invocation')
@pytest.mark.parametrize('mode', ['pinned', 'disabled', 'context_tampered', 'helper_tampered', 'pointer_changed_before_child',
                                  'duplicate_pointer', 'escaped_duplicate_pointer', 'duplicate_manifest'])
def test_manual_admin_route_is_explicit_and_pinned(tmp_path, mode):
    commit = 'a' * 40
    store = tmp_path / 'bundles'
    root = store / commit
    root.mkdir(parents=True)
    pointer_path = tmp_path / 'state.json'
    helper = Path(__file__).resolve().parents[2] / 'ops/windows/reboot/Start-WdMindManualOwner.ps1'
    helper_copy = root / helper.name
    helper_text = helper.read_text().replace(r'C:\Python\wd-reboot-bundles', str(store)).replace(
        r'C:\Python\WD_REBOOT_STATE_CURRENT.json', str(pointer_path))
    helper_copy.write_text(helper_text)
    context = root / 'BridgeCodeContext.ps1'
    mutate = ("[IO.File]::WriteAllText('" + str(pointer_path).replace("'", "''") + "','{}')"
              if mode == 'pointer_changed_before_child' else '')
    context.write_text("function Get-WdBridgeCodePackageDefinition { param($Path) @{Definition=@{}} }\n"
                       "function Assert-WdBridgeCodePackageIntegrity { param($BundleRoot,$Deployment,$Definition) " + mutate + " }\n")
    sources = {'tools/__init__.py': b'', 'tools/wd_mind_owner_reservation_admin.py':
               b'def main(argv):\n print("SYNTHETIC_ROUTE")\n return 2\n'}
    files = {}
    for relative, content in sources.items():
        path = root / 'tools-bootstrap' / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        files['tools-bootstrap/' + relative] = hashlib.sha256(content).hexdigest()
    definition = json.dumps({'python_files': list(sources)}, separators=(',', ':')).encode()
    (root / 'bridge-code-files.json').write_bytes(definition)
    for name in (helper.name, context.name, 'bridge-code-files.json'):
        files[name] = hashlib.sha256((root / name).read_bytes()).hexdigest()
    manifest = json.dumps({'schema_version': 1, 'source_commit': commit, 'files': files}, separators=(',', ':')).encode()
    if mode == 'duplicate_manifest':
        manifest = manifest.replace(b'"source_commit":', b'"source_commit":"' + commit.encode() + b'","source_commit":', 1)
    (root / 'deployment-manifest.json').write_bytes(manifest)
    pin = hashlib.sha256(manifest).hexdigest()
    pointer_json = json.dumps({'source_commit': commit, 'final_commit': commit, 'active_bundle': str(root),
                               'manifest_sha256': pin, 'final_manifest_sha256': pin})
    if mode == 'duplicate_pointer': pointer_json = pointer_json[:-1] + ',"source_commit":"' + commit + '"}'
    if mode == 'escaped_duplicate_pointer': pointer_json = pointer_json[:-1] + ',"source_\\u0063ommit":"' + commit + '"}'
    pointer_path.write_text(pointer_json)
    if mode == 'context_tampered':
        context.write_text('throw "synthetic context should not run"')
    elif mode == 'helper_tampered':
        helper_copy.write_bytes(helper_copy.read_bytes() + b'\n# changed\n')
    quote = lambda p: str(p).replace("'", "''")
    enabled = '' if mode == 'disabled' else '-Enabled'
    python = Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs/Python/Python313/python.exe'
    if not python.is_file():
        pytest.skip('trusted test interpreter is not available')
    shell = str(Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe')
    script = f"""
$ErrorActionPreference='Stop'
. '{quote(helper_copy)}'
try {{ $v=Invoke-WdMindManualReservationAdmin {enabled} -Operation reset -BundleRoot '{quote(root)}' `
 -ExpectedCommit '{commit}' -ExpectedManifestHash '{pin}' -BridgePythonPath '{quote(python)}' `
 -BridgePythonSha256 '{hashlib.sha256(python.read_bytes()).hexdigest()}'
 Write-Output ('RESULT='+$v) }} catch {{ Write-Output ('ERROR='+$_.Exception.Message) }}
"""
    proc = subprocess.run([shell, '-NoProfile', '-NonInteractive', '-EncodedCommand',
                           base64.b64encode(script.encode('utf-16le')).decode()],
                          capture_output=True, text=True, timeout=30, check=True)
    if mode == 'pinned':
        assert 'SYNTHETIC_ROUTE' in proc.stdout
        assert '2' in proc.stdout
        assert 'ERROR=' not in proc.stdout
    else:
        assert ('ERROR=mind_manual_admin_disabled' if mode == 'disabled' else
                'ERROR=mind_manual_admin_refused') in proc.stdout
        assert 'SYNTHETIC_ROUTE' not in proc.stdout


@pytest.mark.skipif(os.name != 'nt', reason='PowerShell independent operator bootstrap')
@pytest.mark.parametrize('mode', ['pinned', 'modified_helper', 'changed_after_capture', 'wrong_commit', 'wrong_pointer_hash',
                                  'duplicate_pointer', 'escaped_duplicate_pointer', 'nested_duplicate_pointer', 'duplicate_manifest'])
def test_independent_admin_bootstrap_validates_before_helper_execution(tmp_path, mode):
    commit = 'a' * 40
    store, pointer = tmp_path / 'bundles', tmp_path / 'state.json'
    root = store / commit
    root.mkdir(parents=True)
    helper = Path(__file__).resolve().parents[2] / 'ops/windows/reboot/Start-WdMindManualOwner.ps1'
    original = helper.read_text()
    source = original.replace(r'C:\Python\wd-reboot-bundles', str(store)).replace(
        r'C:\Python\WD_REBOOT_STATE_CURRENT.json', str(pointer))
    source += "\nWrite-Output 'HELPER_EXECUTED'\n"
    target = root / helper.name
    target.write_text(source)
    (root / 'BridgeCodeContext.ps1').write_text(
        "function Get-WdBridgeCodePackageDefinition { param($Path) @{Definition=@{}} }\n"
        "function Assert-WdBridgeCodePackageIntegrity { param($BundleRoot,$Deployment,$Definition) }\n")
    sources = {'tools/__init__.py': b'', 'tools/wd_mind_owner_reservation_admin.py':
               b'def main(argv):\n print("SYNTHETIC_ROUTE")\n return 2\n'}
    files = {}
    for relative, data in sources.items():
        path = root / 'tools-bootstrap' / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        files['tools-bootstrap/' + relative] = hashlib.sha256(data).hexdigest()
    (root / 'bridge-code-files.json').write_text(json.dumps({'python_files': list(sources)}))
    for name in (helper.name, 'BridgeCodeContext.ps1', 'bridge-code-files.json'):
        files[name] = hashlib.sha256((root / name).read_bytes()).hexdigest()
    manifest = json.dumps({'schema_version': 1, 'source_commit': commit, 'files': files}).encode()
    if mode == 'duplicate_manifest':
        manifest = manifest.replace(b'"source_commit":', b'"source_commit":"' + commit.encode() + b'","source_commit":', 1)
    (root / 'deployment-manifest.json').write_bytes(manifest)
    pin = hashlib.sha256(manifest).hexdigest()
    state = {'source_commit': commit, 'final_commit': commit, 'active_bundle': str(root),
             'manifest_sha256': pin, 'final_manifest_sha256': pin}
    if mode == 'wrong_pointer_hash': state['final_manifest_sha256'] = '0' * 64
    pointer_json = json.dumps(state)
    if mode == 'duplicate_pointer': pointer_json = pointer_json[:-1] + ',"source_commit":"' + commit + '"}'
    if mode == 'escaped_duplicate_pointer': pointer_json = pointer_json[:-1] + ',"source_\\u0063ommit":"' + commit + '"}'
    if mode == 'nested_duplicate_pointer': pointer_json = pointer_json[:-1] + ',"probe":{"x":1,"x":2}}'
    pointer.write_text(pointer_json)
    if mode == 'modified_helper': target.write_text('Write-Output "ATTACK_RAN"\n' + source)
    python = Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs/Python/Python313/python.exe'
    if not python.is_file(): pytest.skip('trusted test interpreter is not available')
    bootstrap = original.split('<# BEGIN-WD-MANUAL-EXTERNAL-BOOTSTRAP\n', 1)[1].split('\nEND-WD-MANUAL-EXTERNAL-BOOTSTRAP #>', 1)[0]
    bootstrap = bootstrap.replace(r'C:\Python\wd-reboot-bundles', str(store)).replace(
        r'C:\Python\WD_REBOOT_STATE_CURRENT.json', str(pointer))
    bootstrap = bootstrap.replace('<EXACT_COMMIT>', 'b' * 40 if mode == 'wrong_commit' else commit)
    bootstrap = bootstrap.replace('<EXACT_MANIFEST_SHA256>', pin).replace('<PINNED_PYTHON_PATH>', str(python))
    bootstrap = bootstrap.replace('<PINNED_PYTHON_SHA256>', hashlib.sha256(python.read_bytes()).hexdigest())
    bootstrap = bootstrap.replace('<create_or_reset>', 'reset')
    if mode == 'changed_after_capture':
        replacement = "[IO.File]::WriteAllText('" + str(target).replace("'", "''") + "','Write-Output ATTACK_RAN')\n"
        bootstrap = bootstrap.replace('. ([scriptblock]::Create($utf8.GetString($helperBytes)',
                                      replacement + '. ([scriptblock]::Create($utf8.GetString($helperBytes)', 1)
    shell = str(Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe')
    proc = subprocess.run([shell, '-NoProfile', '-NonInteractive', '-EncodedCommand',
                           base64.b64encode(bootstrap.encode('utf-16le')).decode()],
                          capture_output=True, text=True, timeout=30, check=False)
    assert 'ATTACK_RAN' not in proc.stdout
    if mode == 'pinned':
        assert 'HELPER_EXECUTED' in proc.stdout
        assert 'SYNTHETIC_ROUTE' in proc.stdout
        assert proc.returncode == 0
    else:
        if mode not in ('changed_after_capture',): assert 'HELPER_EXECUTED' not in proc.stdout
        assert 'SYNTHETIC_ROUTE' not in proc.stdout
        assert proc.returncode != 0


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


