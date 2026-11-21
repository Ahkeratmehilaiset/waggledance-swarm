"""Exercise pinned rotation end to end with only synthetic workers and data."""
import hashlib
import json
import subprocess
from wd_mind_owned_compat import run_owned
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
# Synthetic identity only; these tests exercise worker/rotator behavior,
# not the OS reservation. Other tests cover the real Windows reservation.
from tools import wd_mind_runtime_child as child
guard = object.__new__(child._LocalOwnerReservation)
guard.fd = 1
guard._check = lambda: None
guard._read = lambda: child._OWNER_RESERVATION_MAGIC + b'R'
admission = child._LocalAdmission(guard)
identity = object.__new__(child._LocalManifestQuery)
admission.bind_owner(identity)
factory._admission, factory._admission_owner = admission, identity
admission.bind_factory(identity, factory)
# Confined to the synthetic subprocess; never changes production defaults.
runtime._STARTUP_OWNER_SOURCE_BUDGET_ENABLED = True
runtime_hash = hashlib.sha256((pathlib.Path(root) / 'wd-mind-e5-encoder-binding.json').read_bytes()).hexdigest()
processes, jobs, trace = [], [], []
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
def synthetic_factory(self, *, owner_sink, call_deadline):
    # Rotation must release every preceding worker before reserving the next.
    assert all(item.poll() == 0 for item in processes)
    assert all(item.closed for item in jobs)
    assert lifecycle._active is None
    trace.append(('start', len(processes)))
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
                before = path.read_bytes()
                after = before.replace(b'ordinary', b'Ordinary')
                trace.append(('source_write', len(processes), before != after))
                path.write_bytes(after)
            if mode == 'policy_mutation':
                (pathlib.Path(root) / 'wd-mind-message-policy.json').write_bytes(b'{}')
            return len(packet)
        def flush(self): pass
        def close(self):
            if mode != 'hold': process.finish(0)
    process.stdin = Input()
    owner = lifecycle.reserve(enabled=True, bulk_contract_sha256=e5.BULK_CONTRACT_SHA256)
    owner_sink(owner)
    job = Job(); jobs.append(job)
    owner.set_job(job); owner.bind(process)
    # The native launcher spends part of its startup deadline on the handshake.
    client = e5.GracefulE5WorkerClient(owner, timeout=29.99, encoder_binding_sha256=runtime_hash,
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
        assert started['state'] == 'HOLD' and type(started['owned']) is bool, started
        assert len(processes) == len(jobs) and processes, (mode, started, trace)
        try:
            owner.query_local(enabled=True, lane='codex-lead-1', scope='own', query='rotation task')
        except runtime.FullRuntimeRefused:
            pass
        else:
            raise AssertionError('held owner returned a query result')
        if mode == 'hold':
            assert len(processes) == len(jobs) == 1
            assert started['owned'] is True and processes[0].poll() is None
            assert lifecycle._active is owner._client.owner and not jobs[0].closed
        if not started['owned']:
            assert all(process.poll() == 0 for process in processes)
            assert all(job.closed for job in jobs)
            assert lifecycle._active is None
        if mode == 'source_mutation':
            assert any(item[0] == 'source_write' and item[2] for item in trace), trace
    else:
        assert started['state'] == 'READY', started
        assert len(processes) >= 2 and processes[0].poll() == 0
        result = owner.query_local(enabled=True, lane='codex-lead-1', scope='own', query='rotation task')
        assert result['results'] and result['results'][0]['source_event_sha256']
        assert owner.status()['state'] == 'READY'
    assert checks['source'] and checks['policy'], checks
    print(json.dumps({'status':'ok','workers':len(processes)}))
finally:
    count = len(processes)
    for process in processes: process.finish(0)
    closed = owner.close()
    # A fail-closed owner can remain HOLD after its resources are released.
    assert closed['state'] in ('STOPPED', 'HOLD') and closed['owned'] is False, closed
    assert len(processes) == count and all(process.poll() == 0 for process in processes)
    assert all(job.closed for job in jobs) and lifecycle._active is None
'''


@pytest.mark.parametrize('mode', ['rotate', 'byte_budget', 'hold', 'source_mutation',
                                 'policy_mutation', 'tamper_pin'])
def test_pinned_rotation_and_owned_hold(synthetic_bundle, mode):
    digest, policy_sha = _prepared_bundle(synthetic_bundle)
    result = run_owned([sys.executable, '-c', SCRIPT, str(synthetic_bundle['bundle']),
        digest, policy_sha, str(ROOT / 'tests/tools'), mode, str(synthetic_bundle['source'])],
        capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)['status'] == 'ok'
