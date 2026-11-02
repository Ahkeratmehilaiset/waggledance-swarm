"""RED: isolated bound-session caller boundary, no live data or runtime."""
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


WRAPPER = Path(__file__).resolve().parents[2] / 'ops/windows/reboot/Invoke-WdQ4BoundSession.ps1'
JOB = Path(__file__).resolve().parents[2] / 'ops/windows/reboot/WdQ4ProcessJob.ps1'
PWSH = shutil.which('pwsh')
pytestmark = pytest.mark.skipif(os.name != 'nt' or not PWSH, reason='Windows PowerShell 7 required')
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def synthetic_bound_bundle(tmp_path):
    spec = importlib.util.spec_from_file_location('q4_bound_fixture', Path(__file__).with_name('test_wd_q4_descriptor_host_tuple.py'))
    fixtures = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixtures)
    candidate = fixtures.candidate.__wrapped__(tmp_path)
    venv_path = tmp_path / 'synthetic-venv'
    subprocess.run([sys.executable, '-m', 'venv', '--without-pip', str(venv_path)],
                   check=True, capture_output=True, timeout=30)
    interpreter = str(venv_path / 'Scripts' / 'python.exe')
    bundle = tmp_path / 'bundle'
    bundle.mkdir()
    candidate['descriptor']['bundle_root'] = str(bundle)
    candidate['descriptor']['worker']['bundle-root'] = str(bundle)
    candidate['descriptor']['interpreter'] = interpreter
    candidate['descriptor']['worker']['interpreter'] = interpreter
    exe_hash = hashlib.sha256(Path(interpreter).read_bytes()).hexdigest()
    candidate['descriptor']['interpreter_sha256'] = exe_hash
    candidate['descriptor']['worker']['interpreter-sha256'] = exe_hash
    candidate['host']['Interpreter'] = interpreter
    candidate['host']['InterpreterSha256'] = exe_hash
    candidate['host']['WorkingDirectory'] = str(tmp_path / 'work')
    candidate['descriptor']['cwd'] = candidate['host']['WorkingDirectory']
    candidate['descriptor']['worker']['cwd'] = candidate['host']['WorkingDirectory']
    (tmp_path / 'work').mkdir()
    scratch = tmp_path / 'scratch'
    scratch.mkdir()
    files = {}
    def put(name, data):
        path = bundle / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        files[name] = hashlib.sha256(data).hexdigest()
    host = ("function Invoke-WdQ4SessionHost { param([switch]$Enabled,$JobHelper,$JobHelperSha256,"
            "$Interpreter,$InterpreterSha256,$Bootstrap,$BootstrapSha256,$BootstrapArguments,"
            "$WorkingDirectory,$ScratchRoot,[byte[]]$InputBytes,$TimeoutSeconds,$MaxOutputBytes)\n"
            "if ([Text.Encoding]::UTF8.GetString($InputBytes) -cne 'synthetic-private-marker') { throw 'input' }; "
            "[pscustomobject]@{Status='OK';Output=[Text.Encoding]::UTF8.GetBytes('{\"synthetic\":true}' + [char]10)} }\n")
    for name in ('BridgeCodeContext.ps1', 'WdQ4ProcessJob.ps1', 'Invoke-WdQ4SessionCommand.ps1'):
        put(name, (ROOT / 'ops/windows/reboot' / name).read_bytes())
    put('Invoke-WdQ4SessionHost.ps1', host.encode())
    put('Invoke-WdBridgePython.ps1', b'# synthetic unused wrapper')
    original = json.loads((ROOT / 'ops/windows/reboot/bridge-code-files.json').read_bytes())
    python_files = [name for name in original['python_files'] if name.endswith('.py')]
    for name in python_files:
        put('tools-bootstrap/' + name, (ROOT / name).read_bytes())
    wheel = b'synthetic wheel'
    put('tools-bootstrap/wheels/fake-1.0-py3-none-any.whl', wheel)
    put('tools-bootstrap/site/fake/__init__.py', b'# synthetic')
    definition = {'schema': 'wd.bridge-code-package.v1', 'package_relative_root': 'tools-bootstrap',
        'invocation_wrapper_relative': 'Invoke-WdBridgePython.ps1', 'python_files': python_files,
        'python_entrypoints': {'query': 'tools/wd_q4_agent_query.py'},
        'python_requirements': [{'name':'fake','version':'1.0','wheel':'fake-1.0-py3-none-any.whl',
            'sha256': hashlib.sha256(wheel).hexdigest(), 'import_path':'fake/__init__.py'}],
        'wheel_store_relative':'wheels','python_site_relative':'site',
        'import_smoke': {'third_party_module':'fake','package_modules':['tools.wd_q4_agent_query']},
        'isolation_environment': {'PYTHONDONTWRITEBYTECODE':'1','PYTHONNOUSERSITE':'1','PYTHONSAFEPATH':'1'}}
    put('bridge-code-files.json', json.dumps(definition).encode())
    candidate['host'].update({'HostScript':str(bundle / 'Invoke-WdQ4SessionHost.ps1'),
        'HostSha256':files['Invoke-WdQ4SessionHost.ps1'], 'JobHelper':str(bundle / 'WdQ4ProcessJob.ps1'),
        'JobHelperSha256':files['WdQ4ProcessJob.ps1'],
        'Bootstrap':str(bundle / 'tools-bootstrap/tools/wd_q4_session_outer_entry.py'),
        'BootstrapSha256':files['tools-bootstrap/tools/wd_q4_session_outer_entry.py']})
    manifest = json.dumps({'schema_version':1,'source_commit':'a'*40,'files':files,
        'bridge_code_package':{'python_executable':interpreter,'python_executable_sha256':exe_hash}}).encode()
    (bundle / 'deployment-manifest.json').write_bytes(manifest)
    candidate.update(fixtures._rebind_manifest(candidate, manifest))
    for key in ('manifest_bytes','fleet_bytes','binding_bytes'):
        candidate[key+'_b64']=base64.b64encode(candidate.pop(key)).decode()
    metadata = tmp_path / 'metadata.json'
    metadata.write_text(json.dumps(candidate),encoding='utf-8')
    return {'bundle':bundle,'scratch':scratch,'metadata':metadata,'manifest_sha':hashlib.sha256(manifest).hexdigest()}


def test_anchored_plan_to_command_with_synthetic_bundle(synthetic_bound_bundle):
    item=synthetic_bound_bundle
    quote=lambda value: "'" + str(value).replace("'", "''") + "'"
    script=("$ErrorActionPreference='Stop'; . " + quote(WRAPPER)
        + '; $r=Invoke-WdQ4BoundSession -Enabled -BundleRoot ' + quote(item['bundle'])
        + " -ExpectedCommit '" + 'a'*40 + "' -ExpectedManifestSha256 " + quote(item['manifest_sha'])
        + ' -PlanMetadata ([IO.File]::ReadAllText(' + quote(item['metadata']) + ')) -ScratchRoot ' + quote(item['scratch'])
        + "; @{status=$r.Status;output=$(if($null -eq $r.Output){$null}else{[Text.Encoding]::UTF8.GetString($r.Output)})}|ConvertTo-Json -Compress")
    encoded=base64.b64encode(script.encode('utf-16-le')).decode()
    result=subprocess.run([PWSH,'-NoProfile','-NonInteractive','-EncodedCommand',encoded],
        input=b'synthetic-private-marker',capture_output=True,timeout=180)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'status':'UNVALIDATED','output':'{"synthetic":true}\n'}
    assert b'synthetic-private-marker' not in result.stdout+result.stderr


@pytest.mark.parametrize('fault', ['manifest', 'command', 'duplicate_metadata', 'job', 'bootstrap', 'shadow_hashlib', 'shadow_json', 'scratch_mismatch', 'scratch_overlap_runtime'])
def test_preflight_fault_refuses_before_planner_child(synthetic_bound_bundle, tmp_path, fault):
    item=synthetic_bound_bundle
    metadata=item['metadata'].read_text(encoding='utf-8')
    if fault == 'manifest':
        (item['bundle'] / 'deployment-manifest.json').write_bytes(b'{"synthetic":"tampered"}')
    if fault == 'command':
        (item['bundle'] / 'Invoke-WdQ4SessionCommand.ps1').write_bytes(b'# synthetic tamper')
    if fault == 'job':
        (item['bundle'] / 'WdQ4ProcessJob.ps1').write_bytes(b'# synthetic tamper')
    if fault == 'bootstrap':
        (item['bundle'] / 'tools-bootstrap/tools/wd_q4_session_outer_entry.py').write_bytes(b'# synthetic tamper')
    if fault in ('shadow_hashlib', 'shadow_json'):
        rogue = item['bundle'] / 'tools-bootstrap' / ('hashlib.py' if fault == 'shadow_hashlib' else 'json/__init__.py')
        rogue.parent.mkdir(parents=True, exist_ok=True)
        rogue.write_text('# synthetic shadow', encoding='utf-8')
    if fault == 'duplicate_metadata':
        metadata=metadata[:-1] + ',"lane":"duplicate"}'
        item['metadata'].write_text(metadata,encoding='utf-8')
    if fault == 'scratch_mismatch':
        doc=json.loads(metadata)
        doc['host']['ScratchRoot']=str(tmp_path / 'other-scratch')
        item['metadata'].write_text(json.dumps(doc),encoding='utf-8')
    if fault == 'scratch_overlap_runtime':
        doc=json.loads(metadata)
        doc['descriptor']['outer']['runtime-root']=str(item['scratch'] / 'protected-runtime')
        item['metadata'].write_text(json.dumps(doc),encoding='utf-8')
    marker=tmp_path / 'planner-would-start'
    quote=lambda value: "'" + str(value).replace("'", "''") + "'"
    script=("$ErrorActionPreference='Stop'; . " + quote(WRAPPER)
        + '; function Invoke-WdQ4BoundMetadataChild { [IO.File]::WriteAllText(' + quote(marker) + ",'entered'); throw 'unexpected' }; "
        + '$r=Invoke-WdQ4BoundSession -Enabled -BundleRoot ' + quote(item['bundle'])
        + " -ExpectedCommit '" + 'a'*40 + "' -ExpectedManifestSha256 " + quote(item['manifest_sha'])
        + ' -PlanMetadata ([IO.File]::ReadAllText(' + quote(item['metadata']) + ')) -ScratchRoot ' + quote(item['scratch'])
        + '; @{status=$r.Status}|ConvertTo-Json -Compress')
    encoded=base64.b64encode(script.encode('utf-16-le')).decode()
    result=subprocess.run([PWSH,'-NoProfile','-NonInteractive','-EncodedCommand',encoded],
        input=b'synthetic-private-marker',capture_output=True,timeout=180)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'status':'REFUSED'}
    assert not marker.exists()
    assert b'synthetic-private-marker' not in result.stdout+result.stderr


def test_uncertain_planner_cleanup_quarantines_without_retry(synthetic_bound_bundle, tmp_path):
    item=synthetic_bound_bundle
    marker=tmp_path / 'planner-calls'
    quote=lambda value: "'" + str(value).replace("'", "''") + "'"
    invocation=('$r=Invoke-WdQ4BoundSession -Enabled -BundleRoot ' + quote(item['bundle'])
        + " -ExpectedCommit '" + 'a'*40 + "' -ExpectedManifestSha256 " + quote(item['manifest_sha'])
        + ' -PlanMetadata ([IO.File]::ReadAllText(' + quote(item['metadata']) + ')) -ScratchRoot ' + quote(item['scratch']))
    script=("$ErrorActionPreference='Stop'; . " + quote(WRAPPER)
        + '; function Invoke-WdQ4BoundMetadataChild { [IO.File]::AppendAllText(' + quote(marker)
        + ",'x'); [pscustomobject]@{Outcome='unknown';Output=$null} }; "
        + invocation + '; $one=$r.Status; ' + invocation + "; @{first=$one;second=$r.Status}|ConvertTo-Json -Compress")
    encoded=base64.b64encode(script.encode('utf-16-le')).decode()
    result=subprocess.run([PWSH,'-NoProfile','-NonInteractive','-EncodedCommand',encoded],
        input=b'synthetic-private-marker',capture_output=True,timeout=180)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'first':'UNKNOWN','second':'UNKNOWN'}
    assert marker.read_text() == 'x'
    assert b'synthetic-private-marker' not in result.stdout+result.stderr


def test_off_does_not_load_inputs_or_read_stdin():
    assert WRAPPER.is_file(), 'RED: manifest-bound session caller not present'
    quote = "'" + str(WRAPPER).replace("'", "''") + "'"
    script = ("$ErrorActionPreference='Stop'; . " + quote + "; "
              "Invoke-WdQ4BoundSession | ConvertTo-Json -Compress")
    encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
    result = subprocess.run([PWSH, '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                            input=b'synthetic-private-marker', capture_output=True, timeout=30)
    assert result.returncode == 0
    assert b'synthetic-private-marker' not in result.stdout + result.stderr
    assert json.loads(result.stdout) == {'Status': 'OFF', 'Output': None}


def test_bad_manifest_refuses_before_session_or_child(tmp_path):
    assert WRAPPER.is_file(), 'RED: manifest-bound session caller not present'
    bundle = tmp_path / 'bundle'
    bundle.mkdir()
    (bundle / 'deployment-manifest.json').write_bytes(b'{"schema_version":1}')
    marker = tmp_path / 'synthetic-child-marker'
    quote = lambda value: "'" + str(value).replace("'", "''") + "'"
    script = ("$ErrorActionPreference='Stop'; . " + quote(WRAPPER)
              + '; $r=Invoke-WdQ4BoundSession -Enabled -BundleRoot ' + quote(bundle)
              + " -ExpectedCommit '" + 'a' * 40 + "' -ExpectedManifestSha256 '" + '0' * 64 + "'"
              + " -PlanMetadata '{}' -ScratchRoot " + quote(tmp_path)
              + '; $r|ConvertTo-Json -Compress')
    encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
    result = subprocess.run([PWSH, '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                            input=b'synthetic-private-marker', capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'Status': 'REFUSED', 'Output': None}
    assert b'synthetic-private-marker' not in result.stdout + result.stderr
    assert not marker.exists()


def test_nonlocal_bundle_is_refused_before_any_snapshot(tmp_path):
    marker=tmp_path / 'snapshot-called'
    quote=lambda value: "'" + str(value).replace("'", "''") + "'"
    script=("$ErrorActionPreference='Stop'; . " + quote(WRAPPER)
        + '; function Read-WdQ4BoundSnapshot { [IO.File]::WriteAllText(' + quote(marker) + ",'called'); throw 'unexpected' }; "
        + r"$r=Invoke-WdQ4BoundSession -Enabled -BundleRoot '\\synthetic.invalid\bundle'"
        + " -ExpectedCommit '" + 'a'*40 + "' -ExpectedManifestSha256 '" + '0'*64
        + "' -PlanMetadata '{}' -ScratchRoot " + quote(tmp_path)
        + '; @{status=$r.Status}|ConvertTo-Json -Compress')
    encoded=base64.b64encode(script.encode('utf-16-le')).decode()
    result=subprocess.run([PWSH,'-NoProfile','-NonInteractive','-EncodedCommand',encoded],
        input=b'synthetic-private-marker',capture_output=True,timeout=15)
    assert result.returncode == 0
    assert json.loads(result.stdout) == {'status':'REFUSED'}
    assert not marker.exists()


def test_job_nonzero_exit_retains_stdout_so_caller_must_reject_it(tmp_path):
    scratch = tmp_path / 'scratch'
    scratch.mkdir()
    work = tmp_path / 'work'
    work.mkdir()
    quote = lambda value: "'" + str(value).replace("'", "''") + "'"
    script = ("$ErrorActionPreference='Stop'; . " + quote(JOB) + '; '
              "$r=Invoke-WdQ4ProcessJob -DetailedOutcome -Executable " + quote(sys.executable)
              + " -Arguments @('-I','-S','-B','-c','import sys; print(42); sys.exit(7)')"
              + ' -WorkingDirectory ' + quote(work) + ' -ScratchRoot ' + quote(scratch)
              + " -InputBytes ([Text.Encoding]::UTF8.GetBytes('synthetic-metadata'))"
              + ' -TimeoutSeconds 10 -MaxOutputBytes 4096; '
              "@{outcome=$r.Outcome;cleanup=$r.Cleanup;exit=$r.ExitCode;"
              "output=[Text.Encoding]::UTF8.GetString($r.Stdout).Trim()}|ConvertTo-Json -Compress")
    encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
    result = subprocess.run([PWSH, '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                            capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'outcome': 'completed', 'cleanup': 'confirmed', 'exit': 7, 'output': '42'}


@pytest.mark.parametrize('shadow', ['hashlib.py', 'json/__init__.py'])
def test_front_inserted_code_root_can_shadow_stdlib_in_isolated_python(tmp_path, shadow):
    """Demonstrates why package-root inventory must precede a planner child."""
    code_root = tmp_path / 'synthetic-code-root'
    target = code_root / shadow
    target.parent.mkdir(parents=True)
    target.write_text("raise RuntimeError('synthetic-shadow-observed')\n", encoding='utf-8')
    module = 'hashlib' if shadow == 'hashlib.py' else 'json'
    source = "import sys;sys.path.insert(0,sys.argv[1]);import " + module
    result = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', source, str(code_root)],
                            capture_output=True, timeout=30)
    assert result.returncode != 0
    assert b'synthetic-shadow-observed' in result.stderr


@pytest.mark.parametrize('shadow', ['hashlib.py', 'json/__init__.py'])
def test_appended_code_root_keeps_stdlib_first_and_imports_manifest_listed_plan(tmp_path, shadow):
    root = Path(__file__).resolve().parents[2]
    definition = json.loads((root / 'ops/windows/reboot/bridge-code-files.json').read_bytes())
    code_root = tmp_path / 'synthetic-code-root'
    for name in definition['python_files']:
        if name.endswith('.py'):
            dest = code_root / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / name, dest)
    target = code_root / shadow
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("raise RuntimeError('synthetic-shadow-observed')\n", encoding='utf-8')
    source = ("import sys;sys.path.append(sys.argv[1]);import hashlib,json;"
              "import tools.wd_q4_bound_session_plan_cli as plan;"
              "print(json.dumps({'hashlib':hashlib.__file__,'json':json.__file__,'plan':plan.__file__}))")
    result = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', source, str(code_root)],
                            capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    actual = json.loads(result.stdout)
    assert not Path(actual['hashlib']).is_relative_to(code_root)
    assert not Path(actual['json']).is_relative_to(code_root)
    assert Path(actual['plan']).is_relative_to(code_root / 'tools')


@pytest.mark.parametrize('program,expected', [
    ('import sys;sys.stdout.buffer.write(sys.stdin.buffer.read())', 'completed'),
    ('import sys;print(42);sys.exit(7)', 'unknown'),
    ('import time;time.sleep(5)', 'unknown'),
    ('import sys;sys.stdout.buffer.write(b"x"*200000)', 'unknown'),
])
def test_owned_metadata_child_is_bounded_and_discards_untrusted_output(tmp_path, program, expected):
    assert WRAPPER.is_file(), 'RED: bounded metadata-child helper not present'
    quote = lambda value: "'" + str(value).replace("'", "''") + "'"
    script = ("$ErrorActionPreference='Stop'; . " + quote(WRAPPER)
              + '; $r=Invoke-WdQ4BoundMetadataChild -Executable ' + quote(sys.executable)
              + " -Arguments @('-I','-S','-B','-c'," + quote(program) + ')'
              + ' -WorkingDirectory ' + quote(tmp_path)
              + " -InputBytes ([Text.Encoding]::UTF8.GetBytes('synthetic-metadata'))"
              + ' -TimeoutSeconds 1 -MaxOutputBytes 4096; '
              "@{count=@($r).Count;outcome=$r.Outcome;output=$(if($null -eq $r.Output){$null}else{[Text.Encoding]::UTF8.GetString($r.Output)})}"
              '|ConvertTo-Json -Compress')
    encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
    result = subprocess.run([PWSH, '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                            capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
    actual = json.loads(result.stdout)
    assert actual['count'] == 1
    assert actual['outcome'] == expected
    assert actual['output'] == ('synthetic-metadata' if expected == 'completed' else None)


@pytest.mark.parametrize('fault', ['valid', 'shadow_hashlib', 'shadow_json', 'missing', 'mismatch', 'uppercase_bytecode'])
def test_exact_code_root_inventory_against_anchored_entries(tmp_path, fault):
    bundle = tmp_path / 'bundle'
    root = bundle / 'tools-bootstrap'
    good = root / 'tools' / 'synthetic.py'
    good.parent.mkdir(parents=True)
    good.write_bytes(b'synthetic safe code')
    manifest = {'files': {'tools-bootstrap/tools/synthetic.py': hashlib.sha256(good.read_bytes()).hexdigest()}}
    if fault in ('shadow_hashlib', 'shadow_json'):
        rogue = root / ('hashlib.py' if fault == 'shadow_hashlib' else 'json/__init__.py')
        rogue.parent.mkdir(parents=True, exist_ok=True)
        rogue.write_text('synthetic shadow', encoding='utf-8')
    if fault == 'missing': manifest['files']['tools-bootstrap/missing.py'] = 'a' * 64
    if fault == 'mismatch': manifest['files']['tools-bootstrap/tools/synthetic.py'] = 'a' * 64
    if fault == 'uppercase_bytecode':
        cached = root / 'tools' / '__PYCACHE__' / 'cached.PYC'
        cached.parent.mkdir(parents=True)
        cached.write_bytes(b'synthetic bytecode')
        manifest['files']['tools-bootstrap/tools/__PYCACHE__/cached.PYC'] = hashlib.sha256(cached.read_bytes()).hexdigest()
    quote = lambda value: "'" + str(value).replace("'", "''") + "'"
    manifest_json = json.dumps(manifest, separators=(',', ':'))
    script = ("$ErrorActionPreference='Stop'; . " + quote(WRAPPER)
              + '; $d=' + quote(manifest_json) + ' | ConvertFrom-Json; '
              + 'try { Assert-WdQ4BoundCodeRoot -BundleRoot ' + quote(bundle)
              + " -Deployment $d; $result='pass' } catch { $result='refused' }; "
              + '@{result=$result}|ConvertTo-Json -Compress')
    encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
    result = subprocess.run([PWSH, '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                            capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'result': 'pass' if fault == 'valid' else 'refused'}
