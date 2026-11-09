"""Synthetic bounded planner CLI; the child runs no session or model."""
import base64
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('q4_cli_tuple_fixture', Path(__file__).with_name('test_wd_q4_descriptor_host_tuple.py'))
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)


@pytest.fixture
def plan_request(tmp_path):
    item = fixtures.candidate.__wrapped__(tmp_path)
    item.update(fixtures._with_manifest(item))
    for name in ('manifest_bytes', 'fleet_bytes', 'binding_bytes'):
        item[name + '_b64'] = base64.b64encode(item.pop(name)).decode('ascii')
    return item


def run(raw, *args, root=ROOT):
    source = ('import sys;sys.path.insert(0,sys.argv.pop(1));'
              'from tools.wd_q4_bound_session_plan_cli import main;raise SystemExit(main())')
    return subprocess.run([sys.executable, '-I', '-S', '-B', '-c', source, str(root), *args],
                          input=raw, capture_output=True, timeout=30)


def encoded(value):
    return json.dumps(value, separators=(',', ':')).encode('ascii')


def test_off_does_not_read_input_and_has_no_environment_output():
    result = run(b'synthetic-private-marker')
    assert result.returncode == 0
    assert result.stdout == b'{"status":"OFF"}\n' and result.stderr == b''


def test_enabled_produces_closed_plan(plan_request):
    result = run(encoded(plan_request), '--enabled')
    assert result.returncode == 0, result.stderr
    plan = json.loads(result.stdout)
    assert plan['status'] == 'PLAN_ONLY' and plan['manifest_binding'] == 'MANIFEST_PINS_ONLY'
    assert plan['command_sha256'] == '4' * 64
    assert 'synthetic-private-marker' not in result.stdout.decode()


@pytest.mark.parametrize('fault', ['query', 'missing', 'bad64', 'manifest_digest', 'duplicate', 'too_large', 'bad_option'])
def test_refuses_untrusted_or_oversized_input_without_echo(plan_request, fault):
    if fault == 'query': plan_request['query'] = 'synthetic-private-marker'
    if fault == 'missing': del plan_request['manifest_bytes_b64']
    if fault == 'bad64': plan_request['manifest_bytes_b64'] = 'not base64!'
    if fault == 'manifest_digest': plan_request['expected_manifest_sha256'] = '9' * 64
    raw = encoded(plan_request)
    if fault == 'duplicate': raw = raw[:-1] + b',"lane":"duplicate"}'
    if fault == 'too_large': raw = b' ' * (16 * 1024 * 1024 + 1)
    result = run(raw, '--bad' if fault == 'bad_option' else '--enabled')
    assert result.returncode == 2
    assert result.stdout == b''
    assert result.stderr == b'{"error":"bound_session_plan_cli_refused"}\n'


def test_cli_and_library_in_package_without_new_entrypoint():
    definition = json.loads((ROOT / 'ops/windows/reboot/bridge-code-files.json').read_bytes())
    for name in ('tools/wd_q4_bound_session_plan.py', 'tools/wd_q4_bound_session_plan_cli.py'):
        assert definition['python_files'].count(name) == 1
        assert name not in definition['python_entrypoints'].values()


def test_synthetic_manifest_listed_code_stage_runs_without_worktree_imports(plan_request, tmp_path):
    definition = json.loads((ROOT / 'ops/windows/reboot/bridge-code-files.json').read_bytes())
    stage = tmp_path / 'synthetic-stage'
    for name in definition['python_files']:
        if name.endswith('.py'):
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
    result = run(encoded(plan_request), '--enabled', root=stage)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['status'] == 'PLAN_ONLY'
