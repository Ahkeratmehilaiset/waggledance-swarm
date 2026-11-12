"""Static contracts for the separately isolated, exact-nodeid Windows job.

These tests inspect source text and fixture metadata only. They never launch
Docker, PowerShell, a model, a bridge writer or any of the 26 target tests.
"""
import hashlib
import json
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / '.github/workflows/mind-fixed-drive-platform-coverage.yml'
MANIFEST = ROOT / 'tests/fixtures/mind_fixed_drive_nodeids.json'
BASELINE = ROOT / '.github/workflows/mind-adopt-platform-coverage.yml'
NATIVE_FILES = {
    'tests/tools/test_wd_q4_production_continuation.py',
    'tests/tools/test_wd_q4_staged_binding_scan_scope.py',
    'tests/tools/test_wd_q4_staged_lane_query.py',
}


def _workflow():
    return yaml.safe_load(WORKFLOW.read_bytes())


def _script():
    job = _workflow()['jobs']['fixed-drive-windows']
    return next(step['run'] for step in job['steps'] if step.get('name') == 'Run isolated fixed-drive cases')


def _assert_safety(script):
    # These are admission checks, not green-by-count claims. The implementation
    # must establish the actual drive before running a single target test.
    required = (
        'GetDriveTypeW', "restype=ctypes.c_uint", 'argtypes=(ctypes.c_wchar_p,)',
        '--network none', '.HostConfig.NetworkMode', '.Mounts',
        'Unexpected container mounts', 'Unexpected credential-like environment key',
        'PYTEST_DISABLE_PLUGIN_AUTOLOAD', 'PYTHONDONTWRITEBYTECODE',
        'tests/fixtures/mind_fixed_drive_nodeids.json',
        'Fixed-drive coverage mismatch', 'Sort-Object -Unique',
        'SelectSingleNode(\'skipped\')', 'SelectSingleNode(\'failure\')', 'SelectSingleNode(\'error\')',
    )
    for item in required:
        assert item in script, item
    inner = script.split("$inner = @'", 1)[1].split("\n'@", 1)[0]
    assert inner.index('GetDriveTypeW') < inner.index('-c $pytestProbe')
    assert '==3' in inner or '== 3' in inner
    assert 'C:\\wdtmp\\.codex-audit\\fixed-drive' in inner
    assert 'subprocess' not in inner
    assert '@fixedDriveTests' in inner
    assert "$case.classname" in inner and "$case.name" in inner
    assert '-cnotin $fixedDriveTests' in inner
    assert '$cases.Count -ne 26' in inner
    assert '$actual.Count -ne 26' in inner
    assert 'docker start -a' in script and '.State.ExitCode' in script


def test_manifest_is_exactly_the_reviewed_twenty_six_nodes():
    value = json.loads(MANIFEST.read_bytes())
    assert value['schema'] == 'wd.fixed-drive-tests.v1'
    nodes = value['nodeids']
    assert len(nodes) == len(set(nodes)) == 26
    assert all(type(node) is str and '::' in node for node in nodes)
    assert {node.split('::', 1)[0] for node in nodes} == NATIVE_FILES
    counts = {name: sum(node.startswith(name + '::') for node in nodes) for name in NATIVE_FILES}
    assert sorted(counts.values()) == [2, 10, 14]
    raw = json.dumps(nodes, separators=(',', ':'), ensure_ascii=True).encode('ascii')
    assert hashlib.sha256(raw).hexdigest() == value['nodeids_sha256']
    assert value['nodeids_sha256'] == '46b5e238d56e576f5d53716732ce6760a6e60059b060e924e3da142560054d50'
    for node in nodes:
        name, test = node.split('::', 1)
        assert ('def ' + test.split('[', 1)[0] + '(') in (ROOT / name).read_text(encoding='utf-8')


def test_job_requires_hosted_windows_and_no_credentials():
    job = _workflow()['jobs']['fixed-drive-windows']
    assert job['permissions'] == {}
    assert job['runs-on'] == 'windows-latest'
    assert 1 <= job['timeout-minutes'] <= 30
    checkout = next(step for step in job['steps'] if 'actions/checkout@' in step.get('uses', ''))
    assert checkout['with']['persist-credentials'] is False
    script = _script()
    assert "$env:RUNNER_ENVIRONMENT -ne 'github-hosted'" in script
    assert 'Expected disposable hosted Windows' in script
    assert 'docker cp' in script
    assert '--mount' not in script and '--volume' not in script and ' -v ' not in script
    assert "-match '(?i)token|secret|password|credential|^GITHUB_'" in script


def test_native_volume_is_proven_before_tests_and_junit_is_exact():
    _assert_safety(_script())


@pytest.mark.parametrize('removed', [
    'GetDriveTypeW', '--network none', '.Mounts',
    'Unexpected credential-like environment key',
    'Sort-Object -Unique', "SelectSingleNode('skipped')",
    '-cnotin $fixedDriveTests',
])
def test_contract_rejects_missing_admission_or_coverage_guard(removed):
    script = _script()
    assert removed in script
    with pytest.raises((AssertionError, IndexError)):
        _assert_safety(script.replace(removed, 'REMOVED_GUARD'))


def test_source_archive_includes_manifest_and_synthetic_import_closure():
    script = _script()
    assert 'git archive --format=tar' in script
    assert "'tests/fixtures/mind_fixed_drive_nodeids.json'" in script
    for filename in NATIVE_FILES:
        assert Path(filename).stem in script
    assert 'ast.parse' in script
    assert 'Fixture source closure incomplete' in script
    assert 'deployment-manifest.json' not in script
    assert '.agent-bridge/shared' not in script


def test_existing_adoption_and_continuation_coverage_is_preserved():
    baseline = BASELINE.read_text(encoding='utf-8')
    assert '$cases.Count -ne 38' in baseline
    assert '$cases.Count -ne 12' in baseline
    assert 'adopt-platform-linux:' in baseline
    assert 'Continuation Windows coverage: all 12 named cases passed' in baseline
    for name in ('ci.yml', 'tests.yml'):
        original = (ROOT / '.github/workflows' / name).read_text(encoding='utf-8')
        assert 'runs-on: ubuntu-latest' in original
        assert 'pytest tests/' in original


# Optimization is part of the admission boundary. All executions below compile
# only extracted probe literals against fake stdlib modules, never real I/O.
def _probe_source(name):
    import re

    matches = re.findall(r"^\s*\$(\w+Probe) = '((?:''|[^'])*)'\s*$", _script(), re.MULTILINE)
    probes = {key: value.replace("''", "'") for key, value in matches}
    return probes[name]


def _execute_probe(name, optimize, *, drive_type=3, os_name='nt', network_result=1,
                   manifest=None, runtime_optimize=0, trace=None):
    import builtins
    import io
    import sys
    from types import SimpleNamespace

    if manifest is None:
        manifest = json.loads(MANIFEST.read_bytes())
    calls = [] if trace is None else trace

    def drive(path):
        calls.append(('drive', path))
        return drive_type

    def connect(address):
        calls.append(('connect', address))
        return network_result

    def run_module(module, **kwargs):
        calls.append(('run_module', module, kwargs))
        return {}

    fake_socket = SimpleNamespace(settimeout=lambda timeout: None,
                                  connect_ex=connect, close=lambda: None)
    modules = {
        'ctypes': SimpleNamespace(WinDLL=lambda *args, **kwargs: SimpleNamespace(GetDriveTypeW=drive),
                                  c_wchar_p=object(), c_uint=object()),
        'os': SimpleNamespace(name=os_name),
        'socket': SimpleNamespace(socket=lambda: fake_socket),
        'sys': SimpleNamespace(flags=SimpleNamespace(optimize=runtime_optimize), exit=sys.exit),
        'runpy': SimpleNamespace(run_module=run_module),
        'json': json,
        'hashlib': hashlib,
    }

    def fake_import(module, *args, **kwargs):
        if module not in modules:
            raise AssertionError('Unexpected probe import: ' + module)
        return modules[module]

    def fake_open(path, *args, **kwargs):
        if path != 'tests/fixtures/mind_fixed_drive_nodeids.json':
            raise AssertionError('Unexpected probe read: ' + str(path))
        return io.StringIO(json.dumps(manifest))

    environment = {'__builtins__': dict(vars(builtins), __import__=fake_import, open=fake_open)}
    exec(compile(_probe_source(name), '<synthetic-' + name + '>', 'exec', optimize=optimize), environment)
    return calls


@pytest.mark.parametrize('optimize', [0, 1, 2])
@pytest.mark.parametrize('drive_type', [0, 2, 4, 5])
def test_drive_probe_refuses_nonfixed_volumes_under_optimization(optimize, drive_type):
    with pytest.raises(SystemExit):
        _execute_probe('driveProbe', optimize, drive_type=drive_type)


@pytest.mark.parametrize('optimize', [0, 1, 2])
@pytest.mark.parametrize('os_name', ['posix', 'unknown'])
def test_drive_probe_refuses_non_windows_under_optimization(optimize, os_name):
    with pytest.raises(SystemExit):
        _execute_probe('driveProbe', optimize, os_name=os_name)


@pytest.mark.parametrize('optimize', [0, 1, 2])
@pytest.mark.parametrize('network_result', [0, None, True])
def test_network_probe_refuses_connected_or_untyped_result_under_optimization(optimize, network_result):
    with pytest.raises(SystemExit):
        _execute_probe('networkProbe', optimize, network_result=network_result)


@pytest.mark.parametrize('optimize', [0, 1, 2])
@pytest.mark.parametrize('fault', ['schema', 'missing_nodes', 'node_type', 'count', 'duplicate', 'digest', 'reordered'])
def test_manifest_probe_refuses_invalid_selector_under_optimization(optimize, fault):
    value = json.loads(MANIFEST.read_bytes())
    if fault == 'schema':
        value['schema'] = 'unknown'
    elif fault == 'missing_nodes':
        del value['nodeids']
    elif fault == 'node_type':
        value['nodeids'][0] = {'not': 'a nodeid'}
    elif fault == 'count':
        value['nodeids'].pop()
    elif fault == 'duplicate':
        value['nodeids'][-1] = value['nodeids'][0]
    elif fault == 'digest':
        value['nodeids_sha256'] = '0' * 64
    elif fault == 'reordered':
        value['nodeids'].reverse()
        value['nodeids_sha256'] = hashlib.sha256(json.dumps(value['nodeids'], separators=(',', ':')).encode()).hexdigest()
    with pytest.raises(SystemExit):
        _execute_probe('manifestProbe', optimize, manifest=value)


@pytest.mark.parametrize('optimize', [0, 1, 2])
@pytest.mark.parametrize('name', ['driveProbe', 'networkProbe', 'manifestProbe'])
def test_valid_admission_probe_retains_behavior_under_optimization(optimize, name):
    _execute_probe(name, optimize)


@pytest.mark.parametrize('optimize', [1, 2])
def test_pytest_guard_refuses_optimized_process_before_running_tests(optimize):
    calls = []
    with pytest.raises(SystemExit):
        _execute_probe('pytestProbe', optimize, runtime_optimize=optimize, trace=calls)
    assert calls == []


def test_pytest_guard_runs_module_in_the_checked_process():
    calls = _execute_probe('pytestProbe', 0)
    assert calls == [('run_module', 'pytest', {'run_name': '__main__', 'alter_sys': True})]


def test_all_admission_probes_parse_and_have_no_optimization_sensitive_asserts():
    import ast

    for name in ['driveProbe', 'networkProbe', 'manifestProbe', 'pytestProbe']:
        tree = ast.parse(_probe_source(name))
        assert not any(isinstance(node, ast.Assert) for node in ast.walk(tree)), name


def test_inner_python_invocations_ignore_environment_and_disable_bytecode():
    script = _script()
    inner = script.split("$inner = @'", 1)[1].split("\n'@", 1)[0]
    assert 'Test-Path Env:PYTHONOPTIMIZE' in inner
    assert inner.index('Test-Path Env:PYTHONOPTIMIZE') < inner.index('& C:\\Python\\python.exe')
    assert 'sys.flags.optimize' in _probe_source('pytestProbe')
    for name in ['driveProbe', 'networkProbe', 'manifestProbe', 'pytestProbe']:
        assert '-E -B -c $' + name in inner
    assert '-m pytest' not in inner
    assert '-c $pytestProbe @fixedDriveTests' in inner
