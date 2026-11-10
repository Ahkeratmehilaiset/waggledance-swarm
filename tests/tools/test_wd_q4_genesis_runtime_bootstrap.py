"""Synthetic genesis runtime launch checks, never a live bundle or source."""
import importlib
import hashlib
import json
import os
import ctypes
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
LAUNCH = ROOT / 'ops/windows/reboot/Invoke-WdQ4Genesis.ps1'
_PORTABLE_OFF_TESTS = frozenset((
    'test_runtime_bootstrap_default_off_does_no_io',
    'test_powershell_launcher_default_off_without_reading_inputs',
))


@pytest.fixture(autouse=True)
def _require_supported_genesis_host(request):
    if os.name == 'nt':
        return
    name = request.node.originalname
    if name not in _PORTABLE_OFF_TESTS:
        pytest.skip('genesis runtime requires Windows C: paths, PowerShell and kernel32')
    if name == 'test_powershell_launcher_default_off_without_reading_inputs' and shutil.which('pwsh') is None:
        pytest.skip('portable default-off check requires PowerShell 7 (pwsh)')


def test_runtime_bootstrap_default_off_does_no_io(monkeypatch, capsys):
    mod = importlib.import_module('tools.wd_q4_genesis_runtime_bootstrap')
    monkeypatch.setattr(mod, '_read', lambda *_args, **_kw: pytest.fail('OFF read'))
    assert mod.main([]) == 0
    assert json.loads(capsys.readouterr().out) == {'status': 'OFF'}


def test_powershell_launcher_default_off_without_reading_inputs():
    host = 'powershell' if os.name == 'nt' else 'pwsh'
    run = subprocess.run([host, '-NoProfile', '-NonInteractive', '-File', str(LAUNCH)],
                         text=True, capture_output=True, timeout=20)
    assert run.returncode == 0
    assert json.loads(run.stdout) == {'status': 'OFF'}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode('ascii')


@pytest.fixture
def candidate(tmp_path):
    from sandbox.mind.step2 import runtime_builder
    bundle = tmp_path / 'bundle'
    code = bundle / 'tools-bootstrap'
    (code / 'tools').mkdir(parents=True)
    (code / 'sandbox/mind/step2').mkdir(parents=True)
    for name in ('wd_q4_genesis_runtime_bootstrap.py', 'wd_q4_live_update_bootstrap.py'):
        shutil.copyfile(ROOT / 'tools' / name, code / 'tools' / name)
    for name in ('WdQ4ProcessJob.ps1', 'Invoke-WdQ4GenesisWorker.ps1'):
        shutil.copyfile(ROOT / 'ops/windows/reboot' / name, bundle / name)
    (code / 'tools/__init__.py').write_text('', encoding='ascii')
    cli = code / 'tools/wd_q4_genesis_cli.py'
    cli.write_text("def main(args):\n import synthetic_runtime_dependency\n assert synthetic_runtime_dependency.VALUE==42\n print('{\"status\":\"PUBLISHED_GENESIS\",\"pointer_sha256\":\"'+'e'*64+'\"}')\n return 0\n", encoding='ascii')
    verifier = code / 'sandbox/mind/step2/runtime_manifest.py'
    shutil.copyfile(ROOT / 'sandbox/mind/step2/runtime_manifest.py', verifier)
    runtime = tmp_path / 'runtime'
    (runtime / 'assets/code').mkdir(parents=True)
    (runtime / 'site').mkdir()
    (runtime / 'model').mkdir()
    (runtime / 'assets/code/stub.txt').write_text('synthetic code', encoding='ascii')
    (runtime / 'site/synthetic_runtime_dependency.py').write_text('VALUE=42\n', encoding='ascii')
    (runtime / 'model/model.txt').write_text('synthetic model', encoding='ascii')
    runtime_manifest = tmp_path / 'runtime-manifest.json'
    built = runtime_builder.build_runtime_manifest(runtime, runtime_manifest, generation='a' * 40, revision='b' * 40)
    files = {'tools-bootstrap/' + p.relative_to(code).as_posix(): sha(p.read_bytes()).upper()
             for p in code.rglob('*') if p.is_file()}
    files.update({name: sha((bundle / name).read_bytes()).upper()
                  for name in ('WdQ4ProcessJob.ps1', 'Invoke-WdQ4GenesisWorker.ps1')})
    manifest = bundle / 'deployment-manifest.json'
    manifest.write_bytes(encode({'schema_version': 1, 'source_commit': 'c' * 40, 'files': files}))
    manifest_pin = sha(manifest.read_bytes())
    inputs = tmp_path / 'inputs'
    inputs.mkdir()
    for name in ('source', 'sidecar', 'checkpoint', 'private'):
        (tmp_path / name).mkdir()
    config = inputs / 'config.json'
    config.write_bytes(encode({'schema': 'wd.q4-genesis-cli.v1', 'manifest_sha256': manifest_pin,
        'bundle_root': str(bundle), 'trusted_model_root': str(runtime / 'model'),
        'source_path': str(tmp_path / 'source/events.jsonl'), 'generation_path': str(tmp_path / 'sidecar/gen.json'),
        'checkpoint_root': str(tmp_path / 'checkpoint'), 'private_state_root': str(tmp_path / 'private')}))
    spec = inputs / 'runtime.json'
    spec.write_bytes(encode({'schema': 'wd.q4-genesis-runtime.v1', 'bundle_root': str(bundle),
        'manifest_sha256': manifest_pin, 'interpreter_path': sys.executable,
        'interpreter_sha256': sha(Path(sys.executable).read_bytes()), 'runtime_root': str(runtime),
        'runtime_manifest_path': str(runtime_manifest), 'runtime_manifest_sha256': built['manifest_sha256'],
        'runtime_generation': 'a' * 40, 'runtime_revision': 'b' * 40, 'verifier_sha256': sha(verifier.read_bytes())}))
    return {'bundle': bundle, 'bootstrap': code / 'tools/wd_q4_genesis_runtime_bootstrap.py', 'cli': cli,
        'runtime': runtime, 'manifest': manifest, 'manifest_pin': manifest_pin, 'config': config,
        'config_pin': sha(config.read_bytes()), 'spec': spec, 'spec_pin': sha(spec.read_bytes())}


def execute(candidate):
    env = dict(os.environ, WD_REBOOT_EXPECTED_MANIFEST_HASH=candidate['manifest_pin'])
    return subprocess.run([sys.executable, '-I', '-S', '-B', str(candidate['bootstrap']),
        '--enable', '--config', str(candidate['config']), '--sha256', candidate['config_pin'],
        '--manifest-sha256', candidate['manifest_pin'], '--runtime', str(candidate['spec']),
        '--runtime-sha256', candidate['spec_pin']], capture_output=True, text=True, timeout=30, env=env)


def repin_bundle(candidate):
    code = candidate['bundle'] / 'tools-bootstrap'
    manifest = json.loads(candidate['manifest'].read_bytes())
    manifest['files'] = {'tools-bootstrap/' + p.relative_to(code).as_posix(): sha(p.read_bytes()).upper()
                         for p in code.rglob('*') if p.is_file()}
    manifest['files'].update({name: sha((candidate['bundle'] / name).read_bytes()).upper()
        for name in ('WdQ4ProcessJob.ps1', 'Invoke-WdQ4GenesisWorker.ps1')})
    candidate['manifest'].write_bytes(encode(manifest))
    candidate['manifest_pin'] = sha(candidate['manifest'].read_bytes())
    for name, pin_name in (('config', 'config_pin'), ('spec', 'spec_pin')):
        value = json.loads(candidate[name].read_bytes())
        value['manifest_sha256'] = candidate['manifest_pin']
        candidate[name].write_bytes(encode(value))
        candidate[pin_name] = sha(candidate[name].read_bytes())


def test_isolated_bootstrap_uses_verified_runtime_site(candidate):
    result = execute(candidate)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'status': 'PUBLISHED_GENESIS', 'pointer_sha256': 'e' * 64}


@pytest.mark.parametrize('fault', ['site', 'model', 'config', 'bootstrap', 'unlisted', 'interpreter', 'model_root'])
def test_refuses_modified_or_unbound_inputs_without_output_leak(candidate, fault):
    if fault == 'site': (candidate['runtime'] / 'site/synthetic_runtime_dependency.py').write_text('VALUE=1')
    if fault == 'model': (candidate['runtime'] / 'model/model.txt').write_text('tampered')
    if fault == 'config': candidate['config'].write_bytes(b'{}')
    if fault == 'bootstrap': candidate['bootstrap'].write_bytes(candidate['bootstrap'].read_bytes() + b'\n')
    if fault == 'unlisted': (candidate['bundle'] / 'tools-bootstrap/tools/extra.py').write_text('pass')
    if fault in ('interpreter', 'model_root'):
        spec = json.loads(candidate['spec'].read_bytes())
        if fault == 'interpreter': spec['interpreter_sha256'] = '0' * 64
        else: spec['runtime_root'] = str(candidate['bundle'])
        candidate['spec'].write_bytes(encode(spec))
        candidate['spec_pin'] = sha(candidate['spec'].read_bytes())
    result = execute(candidate)
    assert result.returncode == 2 and json.loads(result.stdout) == {'status': 'REFUSED'}
    assert 'synthetic' not in result.stdout + result.stderr


@pytest.mark.parametrize('mode', ['bad_output', 'exception', 'interrupt'])
def test_started_cli_uncertainty_is_sanitized_and_unknown(candidate, mode):
    expressions = {'bad_output': "print('synthetic-private-canary'); return 0",
                   'exception': "raise ValueError('synthetic-private-canary')",
                   'interrupt': "raise KeyboardInterrupt('synthetic-private-canary')"}
    candidate['cli'].write_text('def main(args):\n ' + expressions[mode] + '\n', encoding='ascii')
    repin_bundle(candidate)
    result = execute(candidate)
    assert result.returncode == 3 and json.loads(result.stdout) == {'status': 'UNKNOWN'}
    assert 'synthetic-private-canary' not in result.stdout + result.stderr



SKIP = 'd' * 64
SKIP_LINE = '{"status":"GENESIS_SKIP_PROGRESS","skip_receipt_sha256":"' + SKIP + '"}'


def fake_cli(candidate, line, code):
    candidate['cli'].write_text('def main(args):\n print(' + repr(line) + ')\n return ' + str(code) + '\n',
                                encoding='ascii')
    repin_bundle(candidate)


def test_cli_skip_progress_is_propagated_with_exit_4(candidate):
    fake_cli(candidate, SKIP_LINE, 4)
    result = execute(candidate)
    assert result.returncode == 4, result.stderr
    assert json.loads(result.stdout) == {'status': 'GENESIS_SKIP_PROGRESS', 'skip_receipt_sha256': SKIP}
    assert result.stdout.count('\n') == 1 and 'pointer' not in result.stdout


# Every malformed or inconsistent skip progress answer is an uncertain outcome, never progress or publication.
MALFORMED_SKIP = {
    'skip_exit_0': (SKIP_LINE, 0),
    'skip_exit_2': (SKIP_LINE, 2),
    'skip_exit_3': (SKIP_LINE, 3),
    'skip_without_sha': ('{"status":"GENESIS_SKIP_PROGRESS"}', 4),
    'skip_upper_sha': ('{"status":"GENESIS_SKIP_PROGRESS","skip_receipt_sha256":"' + 'D' * 64 + '"}', 4),
    'skip_short_sha': ('{"status":"GENESIS_SKIP_PROGRESS","skip_receipt_sha256":"' + 'd' * 63 + '"}', 4),
    'skip_int_sha': ('{"status":"GENESIS_SKIP_PROGRESS","skip_receipt_sha256":1}', 4),
    'skip_with_pointer': ('{"status":"GENESIS_SKIP_PROGRESS","skip_receipt_sha256":"' + SKIP +
                          '","pointer_sha256":"' + 'e' * 64 + '"}', 4),
    'skip_with_extra_key': ('{"offset":5,"status":"GENESIS_SKIP_PROGRESS","skip_receipt_sha256":"' + SKIP + '"}', 4),
    'refused_exit_4': ('{"status":"REFUSED"}', 4),
    'unknown_exit_4': ('{"status":"UNKNOWN"}', 4),
    'published_exit_4': ('{"status":"PUBLISHED_GENESIS","pointer_sha256":"' + 'e' * 64 + '"}', 4),
    'published_with_skip_sha': ('{"status":"PUBLISHED_GENESIS","pointer_sha256":"' + 'e' * 64 +
                                '","skip_receipt_sha256":"' + SKIP + '"}', 0),
    'refused_with_skip_sha': ('{"status":"REFUSED","skip_receipt_sha256":"' + SKIP + '"}', 2),
    'lower_case_status': ('{"status":"genesis_skip_progress","skip_receipt_sha256":"' + SKIP + '"}', 4),
}


@pytest.mark.parametrize('case', sorted(MALFORMED_SKIP))
def test_malformed_or_inconsistent_skip_progress_is_unknown(candidate, case):
    fake_cli(candidate, *MALFORMED_SKIP[case])
    result = execute(candidate)
    assert result.returncode == 3 and json.loads(result.stdout) == {'status': 'UNKNOWN'}


@pytest.mark.parametrize('line,code', [('{"status":"REFUSED"}', 2), ('{"status":"UNKNOWN"}', 3),
    ('{"status":"PUBLISHED_GENESIS_PARTIAL","pointer_sha256":"' + 'e' * 64 + '","runner_receipt_sha256":"' + 'f' * 64 + '"}', 0)])
def test_v1_statuses_are_unchanged(candidate, line, code):
    fake_cli(candidate, line, code)
    result = execute(candidate)
    assert result.returncode == code and json.loads(result.stdout) == json.loads(line)


def launcher_command(candidate):
    scratch = candidate['bundle'].parent / 'scratch'
    scratch.mkdir(exist_ok=True)
    host = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    return ['powershell', '-NoProfile', '-NonInteractive', '-File', str(LAUNCH), '-Run',
        '-BundleRoot', str(candidate['bundle']), '-ExpectedCommit', 'c' * 40,
        '-ExpectedManifestSha256', candidate['manifest_pin'], '-ConfigPath', str(candidate['config']),
        '-ExpectedConfigSha256', candidate['config_pin'], '-RuntimePath', str(candidate['spec']),
        '-ExpectedRuntimeSha256', candidate['spec_pin'], '-ScratchRoot', str(scratch),
        '-ExpectedHostSha256', sha(host.read_bytes()), '-TimeoutSeconds', '30']


def test_powershell_launcher_propagates_skip_progress_through_real_bootstrap(candidate):
    fake_cli(candidate, SKIP_LINE, 4)
    result = subprocess.run(launcher_command(candidate), text=True, capture_output=True, timeout=40)
    assert result.returncode == 4, result.stderr
    assert json.loads(result.stdout) == {'status': 'GENESIS_SKIP_PROGRESS', 'skip_receipt_sha256': SKIP}


# The launcher checks the worker answer itself: a fake bootstrap prints exactly this line and exit code.
LAUNCHER_CASES = {
    'skip_exit_4': (SKIP_LINE, 4, 4),
    'skip_exit_0': (SKIP_LINE, 0, 3),
    'skip_exit_2': (SKIP_LINE, 2, 3),
    'skip_without_sha': ('{"status":"GENESIS_SKIP_PROGRESS"}', 4, 3),
    'skip_upper_sha': ('{"status":"GENESIS_SKIP_PROGRESS","skip_receipt_sha256":"' + 'D' * 64 + '"}', 4, 3),
    'skip_with_pointer': ('{"status":"GENESIS_SKIP_PROGRESS","skip_receipt_sha256":"' + SKIP +
                          '","pointer_sha256":"' + 'e' * 64 + '"}', 4, 3),
    'refused_exit_4': ('{"status":"REFUSED"}', 4, 3),
    'published_with_skip_sha': ('{"status":"PUBLISHED_GENESIS","pointer_sha256":"' + 'e' * 64 +
                                '","skip_receipt_sha256":"' + SKIP + '"}', 0, 3),
    'lower_case_status': ('{"status":"genesis_skip_progress","skip_receipt_sha256":"' + SKIP + '"}', 4, 3),
    'skip_sha_newline': ('{"status":"GENESIS_SKIP_PROGRESS","skip_receipt_sha256":"' + SKIP + '\\n"}', 4, 3),
    'refused_exit_2': ('{"status":"REFUSED"}', 2, 2),
}


@pytest.mark.parametrize('case', sorted(LAUNCHER_CASES))
def test_powershell_launcher_checks_skip_progress_answer(candidate, case):
    line, code, expected = LAUNCHER_CASES[case]
    candidate['bootstrap'].write_text('import sys\nprint(' + repr(line) + ')\nsys.exit(' + str(code) + ')\n',
                                      encoding='ascii')
    repin_bundle(candidate)
    result = subprocess.run(launcher_command(candidate), text=True, capture_output=True, timeout=40)
    assert result.returncode == expected, result.stderr
    if expected == 3:
        assert json.loads(result.stdout) == {'status': 'UNKNOWN'}
    else:
        assert json.loads(result.stdout) == json.loads(line)

def test_powershell_fixed_launcher_checks_pins_then_runs_only_bootstrap(candidate):
    scratch = candidate['bundle'].parent / 'scratch'
    scratch.mkdir()
    host = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    command = ['powershell', '-NoProfile', '-NonInteractive', '-File', str(LAUNCH), '-Run',
        '-BundleRoot', str(candidate['bundle']), '-ExpectedCommit', 'c' * 40,
        '-ExpectedManifestSha256', candidate['manifest_pin'], '-ConfigPath', str(candidate['config']),
        '-ExpectedConfigSha256', candidate['config_pin'], '-RuntimePath', str(candidate['spec']),
        '-ExpectedRuntimeSha256', candidate['spec_pin'], '-ScratchRoot', str(scratch),
        '-ExpectedHostSha256', sha(host.read_bytes()), '-TimeoutSeconds', '30']
    result = subprocess.run(command, text=True, capture_output=True, timeout=40)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'status': 'PUBLISHED_GENESIS', 'pointer_sha256': 'e' * 64}
    command[command.index('-ExpectedConfigSha256') + 1] = '0' * 64
    result = subprocess.run(command, text=True, capture_output=True, timeout=40)
    assert result.returncode == 2 and json.loads(result.stdout) == {'status': 'REFUSED'}


def test_powershell_interpreter_mismatch_never_starts_bootstrap(candidate):
    marker = candidate['config'].parent / 'synthetic-started'
    candidate['bootstrap'].write_text('from pathlib import Path\nPath(' + repr(str(marker)) + ').write_text("x")\n')
    repin_bundle(candidate)
    spec = json.loads(candidate['spec'].read_bytes())
    spec['interpreter_sha256'] = '0' * 64
    candidate['spec'].write_bytes(encode(spec))
    candidate['spec_pin'] = sha(candidate['spec'].read_bytes())
    scratch = candidate['bundle'].parent / 'scratch'
    scratch.mkdir()
    host = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    command = ['powershell', '-NoProfile', '-NonInteractive', '-File', str(LAUNCH), '-Run',
        '-BundleRoot', str(candidate['bundle']), '-ExpectedCommit', 'c' * 40,
        '-ExpectedManifestSha256', candidate['manifest_pin'], '-ConfigPath', str(candidate['config']),
        '-ExpectedConfigSha256', candidate['config_pin'], '-RuntimePath', str(candidate['spec']),
        '-ExpectedRuntimeSha256', candidate['spec_pin'], '-ScratchRoot', str(scratch),
        '-ExpectedHostSha256', sha(host.read_bytes()), '-TimeoutSeconds', '30']
    result = subprocess.run(command, text=True, capture_output=True, timeout=40)
    assert result.returncode == 2 and json.loads(result.stdout) == {'status': 'REFUSED'}
    assert not marker.exists()


@pytest.mark.parametrize('fault', ['scratch_overlap', 'worker_tamper'])
def test_powershell_preflight_rejects_unsafe_scratch_and_worker(candidate, fault):
    scratch = candidate['bundle'].parent / 'scratch'
    scratch.mkdir()
    if fault == 'scratch_overlap':
        scratch = candidate['bundle']
    else:
        worker = candidate['bundle'] / 'Invoke-WdQ4GenesisWorker.ps1'
        worker.write_bytes(worker.read_bytes() + b'\n# tampered\n')
    host = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    command = ['powershell', '-NoProfile', '-NonInteractive', '-File', str(LAUNCH), '-Run',
        '-BundleRoot', str(candidate['bundle']), '-ExpectedCommit', 'c' * 40,
        '-ExpectedManifestSha256', candidate['manifest_pin'], '-ConfigPath', str(candidate['config']),
        '-ExpectedConfigSha256', candidate['config_pin'], '-RuntimePath', str(candidate['spec']),
        '-ExpectedRuntimeSha256', candidate['spec_pin'], '-ScratchRoot', str(scratch),
        '-ExpectedHostSha256', sha(host.read_bytes()), '-TimeoutSeconds', '30']
    result = subprocess.run(command, text=True, capture_output=True, timeout=40)
    assert result.returncode == 2 and json.loads(result.stdout) == {'status': 'REFUSED'}
    assert not list(scratch.glob('wd-q4-job-*'))


def test_timed_out_real_synthetic_python_child_is_gone(candidate):
    from ctypes import wintypes
    marker = candidate['bundle'].parent / 'synthetic-child.json'
    candidate['bootstrap'].write_text('''import ctypes,json,os,time
from ctypes import wintypes
k=ctypes.WinDLL('kernel32',use_last_error=True)
k.GetCurrentProcess.restype=wintypes.HANDLE
k.GetProcessTimes.argtypes=[wintypes.HANDLE]+[ctypes.POINTER(wintypes.FILETIME)]*4
creation=wintypes.FILETIME(); exit_time=wintypes.FILETIME(); kernel=wintypes.FILETIME(); user=wintypes.FILETIME()
assert k.GetProcessTimes(k.GetCurrentProcess(),ctypes.byref(creation),ctypes.byref(exit_time),ctypes.byref(kernel),ctypes.byref(user))
identity=(creation.dwHighDateTime<<32)|creation.dwLowDateTime
open(%r,'w').write(json.dumps({'pid':os.getpid(),'creation':identity}))
time.sleep(15)
''' % str(marker), encoding='ascii')
    repin_bundle(candidate)
    scratch = candidate['bundle'].parent / 'scratch'
    scratch.mkdir()
    host = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    command = ['powershell', '-NoProfile', '-NonInteractive', '-File', str(LAUNCH), '-Run',
        '-BundleRoot', str(candidate['bundle']), '-ExpectedCommit', 'c' * 40,
        '-ExpectedManifestSha256', candidate['manifest_pin'], '-ConfigPath', str(candidate['config']),
        '-ExpectedConfigSha256', candidate['config_pin'], '-RuntimePath', str(candidate['spec']),
        '-ExpectedRuntimeSha256', candidate['spec_pin'], '-ScratchRoot', str(scratch),
        '-ExpectedHostSha256', sha(host.read_bytes()), '-TimeoutSeconds', '4']
    result = subprocess.run(command, text=True, capture_output=True, timeout=25)
    assert result.returncode == 3 and json.loads(result.stdout) == {'status': 'UNKNOWN'}
    assert marker.exists(), 'synthetic child did not start; cleanup is unproven'
    child = json.loads(marker.read_text())
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    handle = kernel.OpenProcess(0x1000 | 0x100000, False, child['pid'])
    if handle:
        try:
            creation=wintypes.FILETIME(); exit_time=wintypes.FILETIME(); kernel_time=wintypes.FILETIME(); user=wintypes.FILETIME()
            kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
            assert kernel.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exit_time),
                                          ctypes.byref(kernel_time), ctypes.byref(user))
            observed=(creation.dwHighDateTime<<32)|creation.dwLowDateTime
            kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
            assert observed != child['creation'] or kernel.WaitForSingleObject(handle,0) == 0
        finally:
            kernel.CloseHandle.argtypes=[wintypes.HANDLE]
            kernel.CloseHandle(handle)
