"""Exercise pinned rotation end to end with only synthetic workers and data."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from test_wd_mind_full_runtime import canonical, synthetic_bundle


ROOT = Path(__file__).resolve().parents[2]


def _prepared_bundle(case):
    root = case['bundle']
    manifest_path = root / 'deployment-manifest.json'
    manifest = json.loads(manifest_path.read_bytes())
    relative = 'tools-bootstrap/tools/wd_mind_e5_rotating_client.py'
    data = (ROOT / 'tools/wd_mind_e5_rotating_client.py').read_bytes()
    root.joinpath(*relative.split('/')).write_bytes(data)
    manifest['files'][relative] = hashlib.sha256(data).hexdigest()
    policy = b'{"identifiers":[],"schema":"wd.mind-identifier-policy.v2"}'
    descriptor = canonical({'schema': 'wd.mind-identifier-policy-approval.v1',
        'identifier_policy_sha256': hashlib.sha256(policy).hexdigest(),
        'approval_reference_sha256': 'b' * 64})
    (root / 'wd-mind-identifier-policy-approval.json').write_bytes(descriptor)
    manifest['files']['wd-mind-identifier-policy-approval.json'] = hashlib.sha256(descriptor).hexdigest()
    raw = canonical(manifest)
    manifest_path.write_bytes(raw)
    source = case['source']
    first = json.loads(source.read_bytes().splitlines()[0])
    with source.open('ab') as stream:
        for number in range(40):
            first['message'] = 'synthetic rotation task ' + str(number)
            stream.write(canonical(first) + b'\n')
    return hashlib.sha256(raw).hexdigest(), hashlib.sha256(policy).hexdigest()


SCRIPT = r'''
import hashlib, json, pathlib, sys
root, manifest, policy_sha, testdir, mode, source = sys.argv[1:]
sys.path.insert(0, str(pathlib.Path(root) / 'tools-bootstrap'))
sys.path.append(testdir)
from tools import wd_mind_full_runtime as runtime
from tools import wd_mind_e5_client as e5, wd_mind_e5_lifecycle as lifecycle
from tools import wd_mind_e5_snapshot_adapter as adapter
from test_wd_mind_e5_client import response
from test_wd_mind_e5_lifecycle import Job, Process

# Authenticate before executing the real module; lower its request budget only
# after loading so this fixture needs no real model or 64 MiB output.
if mode == 'tamper_pin':
    path = pathlib.Path(root) / 'tools-bootstrap/tools/wd_mind_e5_rotating_client.py'
    path.write_bytes(path.read_bytes() + b'\n# synthetic mutation\n')
    try:
        adapter._load_pinned_rotating_module(root, manifest)
    except Exception:
        assert 'tools.wd_mind_e5_rotating_client' not in sys.modules
        print(json.dumps({'status':'ok', 'workers':0})); sys.exit(0)
    raise AssertionError('changed code was loaded')
rotation = adapter._load_pinned_rotating_module(root, manifest)
assert rotation.__file__ == str(pathlib.Path(root) / 'tools-bootstrap/tools/wd_mind_e5_rotating_client.py')
if mode == 'byte_budget':
    # One handshake plus the four frames of one complete B16 request.
    rotation.BULK_STDOUT_BYTES = lifecycle.BULK_STDOUT_BYTES = 79 + 4 * 79 + 16 * 1536
else:
    rotation._BULK_NONCE_LIMIT = 1
policy = b'{"identifiers":[],"schema":"wd.mind-identifier-policy.v2"}'
binding = runtime.assemble_full_runtime(enabled=True, bundle_root=root,
    expected_manifest_sha256=manifest, identifier_policy_bytes=policy,
    expected_identifier_policy_sha256=policy_sha)
anchor = runtime.create_operator_pinned_policy_anchor(enabled=True, bundle_root=root,
    expected_manifest_sha256=manifest, operator_policy_sha256=policy_sha,
    operator_approval_reference_sha256='b'*64)
factory = runtime._ManifestWorkerFactory(root, manifest, {}, pathlib.Path(root) / 'synthetic.lock',
    30.0, 0.0, e5.BULK_CONTRACT_SHA256)
runtime_hash = hashlib.sha256((pathlib.Path(root) / 'wd-mind-e5-encoder-binding.json').read_bytes()).hexdigest()
processes = []
checks = {'source': 0, 'policy': 0}
real_checkpoint = runtime._PrivateBinding._immutable_checkpoint
def checked_source(self, *args, **kwargs):
    checks['source'] += 1
    return real_checkpoint(self, *args, **kwargs)
runtime._PrivateBinding._immutable_checkpoint = checked_source
real_policy = runtime._SingleManifestOwner._check_policy_anchor
def checked_policy(self):
    checks['policy'] += 1
    return real_policy(self)
runtime._SingleManifestOwner._check_policy_anchor = checked_policy
def synthetic_factory(self, *, owner_sink):
    process = Process(response(0, 0))
    processes.append(process)
    class Input:
        def write(self, packet):
            size = int.from_bytes(packet[:4], 'little')
            body = json.loads(packet[4:].decode('ascii'))
            assert size == len(packet)-4
            frame = response(body['id'], len(body['texts']), nonce=bytes.fromhex(body['nonce']),
                             digest=bytes.fromhex(body['request_sha256']))
            with process.stdout.condition:
                process.stdout.data.extend(frame)
                process.stdout.condition.notify_all()
            if mode == 'source_mutation':
                path = pathlib.Path(source)
                path.write_bytes(path.read_bytes().replace(b'ordinary', b'Ordinary'))
            if mode == 'policy_mutation':
                (pathlib.Path(root) / 'wd-mind-message-policy.json').write_bytes(b'{}')
            return len(packet)
        def flush(self): pass
        def close(self):
            if mode != 'hold': process.finish(0)
    process.stdin = Input()
    owner = lifecycle.reserve(enabled=True, bulk_contract_sha256=e5.BULK_CONTRACT_SHA256)
    owner_sink(owner)
    owner.set_job(Job()); owner.bind(process)
    client = e5.GracefulE5WorkerClient(owner, timeout=30, encoder_binding_sha256=runtime_hash,
                                        bulk_contract_sha256=e5.BULK_CONTRACT_SHA256)
    owner.mark_ready()
    return {'status':'READY','owner':owner,'client':client,
            'bulk_contract_sha256':e5.BULK_CONTRACT_SHA256}
runtime._ManifestWorkerFactory.__call__ = synthetic_factory
owner = runtime.create_single_manifest_owner(enabled=True, binding=binding, lane='codex-lead-1',
    worker_factory=factory, shutdown_timeout=(0.05 if mode == 'hold' else 5),
    policy_anchor=anchor, worker_rotation=True)
try:
    started = owner.start()
    if mode in ('hold', 'source_mutation', 'policy_mutation'):
        assert started['state'] == 'HOLD' and started['owned'] is True, started
        assert len(processes) == 1
        if mode == 'hold': assert processes[0].poll() is None
        assert owner._client.owner is not None
    else:
        assert started['state'] == 'READY', started
        assert len(processes) >= 2 and processes[0].poll() == 0
        result = owner.query_local(enabled=True, lane='codex-lead-1', scope='own', query='rotation task')
        assert result['results'] and result['results'][0]['source_event_sha256']
        assert owner.status()['state'] == 'READY'
    assert checks['source'] and checks['policy'], checks
    print(json.dumps({'status':'ok','workers':len(processes)}))
finally:
    for process in processes: process.finish(0)
    owner.close()
'''


@pytest.mark.parametrize('mode', ['rotate', 'byte_budget', 'hold', 'source_mutation',
                                 'policy_mutation', 'tamper_pin'])
def test_pinned_rotation_and_owned_hold(synthetic_bundle, mode):
    digest, policy_sha = _prepared_bundle(synthetic_bundle)
    result = subprocess.run([sys.executable, '-c', SCRIPT, str(synthetic_bundle['bundle']),
        digest, policy_sha, str(ROOT / 'tests/tools'), mode, str(synthetic_bundle['source'])],
        capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)['status'] == 'ok'
