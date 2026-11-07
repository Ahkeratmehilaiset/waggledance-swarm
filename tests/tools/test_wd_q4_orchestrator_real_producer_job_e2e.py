"""Isolated real Windows Job -> real continuation producer, never live bridge.

Opt in with WD_Q4_REAL_E2E=1 and provide an externally pinned fixture descriptor
through WD_Q4_REAL_E2E_FIXTURE and WD_Q4_REAL_E2E_FIXTURE_SHA256. The descriptor
must name an isolated, separately built E5 runtime and an approved fleet file.
The initial synthetic genesis uses an in-process synthetic encoder; the two Job
calls use the real pinned subprocess encoder and producer. No service, task,
installation, lane session or real bridge source is opened or modified.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest
from tests.tools.test_wd_q4_genesis_bootstrap import synthetic as genesis_synthetic
from tests.tools import test_wd_q4_genesis_bootstrap as base
from tools import wd_q4_status_prefix_runner as _runner

ROOT = Path(__file__).resolve().parents[2]
_FLEET_ANCHORS = frozenset(_runner._FLEET_SHA)
_FIXTURE_KEYS = frozenset({'schema', 'runtime_root', 'runtime_manifest_path', 'runtime_manifest_sha256',
                           'runtime_revision', 'model_revision', 'model_weights_sha256', 'fleet_path', 'fleet_sha256'})
_HEX64 = re.compile(r'[0-9a-f]{64}\Z')
_RUNTIME_REVISION = re.compile(r'(?:[0-9a-f]{40}|[0-9a-f]{64})\Z')
_MODEL_REVISION = re.compile(r'[0-9a-f]{40}\Z')


def fixture_descriptor():
    from tools.wd_q4_descriptor_cli import _read_pinned
    from tools.wd_q4_genesis_cli import _c_path, _doc
    path = os.environ.get('WD_Q4_REAL_E2E_FIXTURE')
    pin = os.environ.get('WD_Q4_REAL_E2E_FIXTURE_SHA256')
    if not path or not pin or _HEX64.fullmatch(pin) is None:
        raise ValueError('explicit fixture descriptor and sha256 required')
    descriptor = _doc(_read_pinned(_c_path(path), 65536, pin, 'fixture_refused'))
    if set(descriptor) != _FIXTURE_KEYS or descriptor['schema'] != 'wd.q4-real-producer-e2e-fixture.v1':
        raise ValueError('fixture descriptor invalid')
    for key in ('runtime_manifest_sha256', 'model_weights_sha256', 'fleet_sha256'):
        if type(descriptor[key]) is not str or _HEX64.fullmatch(descriptor[key]) is None:
            raise ValueError('fixture pin invalid')
    for key, pattern in (('runtime_revision', _RUNTIME_REVISION), ('model_revision', _MODEL_REVISION)):
        if type(descriptor[key]) is not str or pattern.fullmatch(descriptor[key]) is None:
            raise ValueError('fixture revision invalid')
    if descriptor['fleet_sha256'] not in _FLEET_ANCHORS:
        raise ValueError('fleet pin is not approved by the tested source')
    for key in ('runtime_root', 'runtime_manifest_path', 'fleet_path'):
        descriptor[key] = _c_path(descriptor[key])
    return descriptor


def test_fixture_descriptor_requires_explicit_hash(monkeypatch):
    monkeypatch.delenv('WD_Q4_REAL_E2E_FIXTURE', raising=False)
    monkeypatch.delenv('WD_Q4_REAL_E2E_FIXTURE_SHA256', raising=False)
    with pytest.raises(ValueError, match='explicit fixture descriptor'):
        fixture_descriptor()


def test_fixture_descriptor_refuses_mismatched_hash(tmp_path, monkeypatch):
    path = tmp_path / 'descriptor.json'
    path.write_bytes(b'{}')
    monkeypatch.setenv('WD_Q4_REAL_E2E_FIXTURE', str(path))
    monkeypatch.setenv('WD_Q4_REAL_E2E_FIXTURE_SHA256', 'a' * 64)
    with pytest.raises(Exception):
        fixture_descriptor()


@pytest.mark.skipif(os.name != 'nt', reason='fixture path contract is Windows-only')
def test_fixture_descriptor_accepts_only_approved_fleet_pin(tmp_path, monkeypatch):
    descriptor = {'schema': 'wd.q4-real-producer-e2e-fixture.v1', 'runtime_root': str(tmp_path / 'runtime'),
        'runtime_manifest_path': str(tmp_path / 'runtime-manifest.json'), 'runtime_manifest_sha256': 'a' * 64,
        'runtime_revision': 'b' * 40, 'model_revision': 'c' * 40, 'model_weights_sha256': 'd' * 64,
        'fleet_path': str(tmp_path / 'fleet.json'), 'fleet_sha256': next(iter(_FLEET_ANCHORS))}
    path = tmp_path / 'descriptor.json'
    data = json.dumps(descriptor).encode('utf-8')
    path.write_bytes(data)
    monkeypatch.setenv('WD_Q4_REAL_E2E_FIXTURE', str(path))
    monkeypatch.setenv('WD_Q4_REAL_E2E_FIXTURE_SHA256', hashlib.sha256(data).hexdigest())
    assert fixture_descriptor() == descriptor
    descriptor['fleet_sha256'] = '0' * 64
    data = json.dumps(descriptor).encode('utf-8')
    path.write_bytes(data)
    monkeypatch.setenv('WD_Q4_REAL_E2E_FIXTURE_SHA256', hashlib.sha256(data).hexdigest())
    with pytest.raises(ValueError, match='fleet pin'):
        fixture_descriptor()


@pytest.fixture
def pinned_real_fixture():
    return fixture_descriptor()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def raw(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


def part(data):
    return {'bytes_b64': base64.b64encode(data).decode('ascii'), 'sha256': sha(data)}


def file_sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def make_bundle(root, fleet):
    bundle = root / 'bundle'
    code = bundle / 'tools-bootstrap'
    code.mkdir(parents=True)
    tracked = subprocess.run(['git', 'ls-files', '-z', '--', 'tools', 'sandbox', 'core', 'waggledance'], cwd=ROOT,
                             capture_output=True, check=True).stdout.decode('utf-8').split('\0')
    files = {}
    for name in tracked:
        if not name or not name.endswith('.py'):
            continue
        source = ROOT / name
        target = code / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        files['tools-bootstrap/' + name] = file_sha(target).upper()
    for name in ('WdQ4ProcessJob.ps1', 'Invoke-WdQ4ContinuationWorker.ps1',
                 'Invoke-WdQ4Continuation.ps1', 'Invoke-WdQ4ContinuationOrchestrator.ps1'):
        shutil.copyfile(ROOT / 'ops/windows/reboot' / name, bundle / name)
        files[name] = file_sha(bundle / name).upper()
    (bundle / 'wd-fleet.json').write_bytes(fleet)
    files['wd-fleet.json'] = sha(fleet).upper()
    manifest = raw({'schema_version': 1, 'source_commit': 'a' * 40, 'files': files})
    (bundle / 'deployment-manifest.json').write_bytes(manifest)
    return bundle, sha(manifest)


@pytest.mark.skipif(os.environ.get('WD_Q4_REAL_E2E') != '1', reason='explicit isolated real-runtime opt-in required')
def test_real_outer_and_inner_jobs_run_two_real_producer_cutoffs(pinned_real_fixture, genesis_synthetic, monkeypatch, tmp_path):
    if os.name != 'nt':
        pytest.skip('real nested Windows Jobs require Windows')
    descriptor = pinned_real_fixture
    from tools import wd_q4_status_prefix_runner as runner
    from tools import wd_q4_production_continuation as continuation
    args, _ = genesis_synthetic
    fleet = Path(descriptor['fleet_path']).read_bytes()
    assert sha(fleet) == descriptor['fleet_sha256']
    manifest_bytes = Path(descriptor['runtime_manifest_path']).read_bytes()
    assert sha(manifest_bytes) == descriptor['runtime_manifest_sha256']
    runtime_manifest = json.loads(manifest_bytes)
    assert runtime_manifest['revision'] == descriptor['runtime_revision']
    model = Path(descriptor['runtime_root']) / 'model'
    assets = {item['path'][len('model/'):]: item['sha256'] for item in runtime_manifest['files']
              if item['path'].startswith('model/')}
    assert assets['model.safetensors'] == descriptor['model_weights_sha256']
    assert file_sha(model / 'model.safetensors') == descriptor['model_weights_sha256']
    policy = (ROOT / 'configs/mind/pilot_non_review_policy.json').read_bytes()
    public = (ROOT / 'configs/mind/pilot_public_status_policy.json').read_bytes()
    args.update(policy_bytes=policy, policy_sha256=sha(policy), public_policy_bytes=public,
                public_policy_sha256=sha(public), fleet_bytes=fleet, expected_fleet_sha256=descriptor['fleet_sha256'])
    monkeypatch.setattr(runner, '_POLICY_SHA', frozenset({sha(policy)}))
    monkeypatch.setattr(runner, '_PUBLIC_SHA', frozenset({sha(public)}))
    monkeypatch.setattr(runner, '_FLEET_SHA', frozenset({descriptor['fleet_sha256']}))
    base.test_late_skip_real_producer_publishes_every_public_row_after_the_skip(genesis_synthetic, monkeypatch)
    private = Path(args['private_state_root'])
    shared = Path(args['checkpoint_root'])
    tuple_path = private / 'q4-genesis-tuple-v1.json'
    document = json.loads(tuple_path.read_bytes())
    route = json.loads(base64.b64decode(document['route']['bytes_b64']))
    plan = json.loads(base64.b64decode(document['plan']['bytes_b64']))
    plan['encoder'].update(model_dir=str(model), revision=descriptor['model_revision'],
                           weights_sha256=descriptor['model_weights_sha256'], assets=assets)
    route['trusted_model_root'] = str(model)
    document['plan'] = part(raw(plan))
    route['plan_sha256'] = document['plan']['sha256']
    document['route'] = part(raw(route))
    tuple_path.write_bytes(raw(document))
    tuple_bytes = tuple_path.read_bytes()
    pointer_path = shared / 'q4-latest.json'
    # The synthetic setup establishes the first real persisted watermark; the
    # two subsequent calls are fresh isolated native Jobs without monkeypatches.
    first = continuation.continue_once(enabled=True, tuple_bytes=tuple_bytes, expected_tuple_sha256=sha(tuple_bytes),
        expected_pointer_sha256=sha(pointer_path.read_bytes()), policy_bytes=policy, public_policy_bytes=public,
        fleet_bytes=fleet, expected_fleet_sha256=descriptor['fleet_sha256'], cutoff_utc='2026-10-31T13:00:00Z',
        previous_cutoff_utc='2026-10-31T13:00:00Z', watermark_enabled=True, expected_watermark_record_sha256=None)
    assert first['status'] == 'DEFERRED'
    bundle, manifest_pin = make_bundle(tmp_path, fleet)
    inputs = tmp_path / 'inputs'
    inputs.mkdir()
    policy_path, public_path = inputs / 'policy.json', inputs / 'public.json'
    policy_path.write_bytes(policy)
    public_path.write_bytes(public)
    runtime_spec = {'schema': 'wd.q4-genesis-runtime.v1', 'bundle_root': str(bundle), 'manifest_sha256': manifest_pin,
        'interpreter_path': sys.executable, 'interpreter_sha256': file_sha(Path(sys.executable)),
        'runtime_root': descriptor['runtime_root'], 'runtime_manifest_path': descriptor['runtime_manifest_path'],
        'runtime_manifest_sha256': descriptor['runtime_manifest_sha256'], 'runtime_generation': runtime_manifest['generation'],
        'runtime_revision': descriptor['runtime_revision'], 'verifier_sha256': file_sha(bundle / 'tools-bootstrap/sandbox/mind/step2/runtime_manifest.py')}
    runtime_path = inputs / 'runtime.json'
    runtime_path.write_bytes(raw(runtime_spec))
    source = document['independent_source_pin']
    template = {'schema': 'wd.q4-continuation-cli.v3', 'bundle_root': str(bundle), 'manifest_sha256': manifest_pin,
        'tuple_path': str(tuple_path), 'tuple_sha256': sha(tuple_bytes), 'policy_path': str(policy_path),
        'policy_sha256': sha(policy), 'public_policy_path': str(public_path), 'public_policy_sha256': sha(public),
        'fleet_sha256': descriptor['fleet_sha256'], 'pointer_sha256': sha(pointer_path.read_bytes()), 'source_identity': source['identity'],
        'source_prefix_bytes': source['prefix_bytes'], 'source_prefix_sha256': source['prefix_sha256'],
        'cutoff_utc': '2026-10-31T13:00:00Z', 'previous_cutoff_utc': '2026-10-31T13:00:00Z',
        'watermark_previous_record_sha256': first['watermark_record_sha256']}
    template_path = inputs / 'template.json'
    template_path.write_bytes(raw(template))
    scratch = tmp_path / 'scratch'
    scratch.mkdir()
    host = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    config = {'schema': 'wd.q4-continuation-orchestrator-cli.v1', 'bundle_root': str(bundle),
        'template_path': str(template_path), 'template_sha256': file_sha(template_path), 'expected_commit': 'a' * 40,
        'expected_manifest_sha256': manifest_pin, 'runtime_path': str(runtime_path), 'runtime_sha256': file_sha(runtime_path),
        'scratch_root': str(scratch), 'expected_host_sha256': file_sha(host), 'timeout_seconds': 1500}
    config_path = inputs / 'orchestrator.json'
    previous = first
    for cutoff in ('2026-10-31T13:01:00Z', '2026-10-31T13:02:00Z'):
        config['cutoff_utc'] = cutoff
        config_path.write_bytes(raw(config))
        command = [str(host), '-NoProfile', '-NonInteractive', '-File', str(bundle / 'Invoke-WdQ4ContinuationOrchestrator.ps1'),
            '-Run', '-BundleRoot', str(bundle), '-ExpectedCommit', 'a' * 40, '-ExpectedManifestSha256', manifest_pin,
            '-ConfigPath', str(config_path), '-ExpectedConfigSha256', file_sha(config_path), '-RuntimePath', str(runtime_path),
            '-ExpectedRuntimeSha256', file_sha(runtime_path), '-ScratchRoot', str(scratch),
            '-ExpectedHostSha256', file_sha(host), '-TimeoutSeconds', '1650']
        completed = subprocess.run(command, capture_output=True, text=True, timeout=1700)
        assert completed.returncode == 0, (completed.returncode, completed.stdout, completed.stderr)
        result = json.loads(completed.stdout)
        assert result['status'] == 'DEFERRED' and result['previous_cutoff_utc'] == previous['cutoff_utc']
        assert result['pointer_sha256'] == previous['pointer_sha256']
        assert result['watermark_record_sha256'] != previous['watermark_record_sha256']
        assert not (private / 'q4-continuation-orchestrator-job.pending.json').exists()
        previous = result
