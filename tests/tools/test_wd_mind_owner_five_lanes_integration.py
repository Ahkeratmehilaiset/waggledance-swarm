"""Synthetic five-lane readers against one in-memory owner and local pipe.

This covers repeated caller invocations while the owner remains alive; it does
not claim persistence across an owner/host restart or use a real model/log.
"""
import datetime
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from test_wd_mind_full_runtime import canonical, synthetic_bundle
from test_wd_mind_rotation_integration import _prepared_bundle


ROOT = Path(__file__).resolve().parents[2]
LANES = ('codex-lead-1', 'codex-tools-1', 'fable-5', 'claude-rco-1', 'claude-rco-2')


def _stage(case):
    """Build an independently pinned synthetic bundle, source and policy."""
    _, policy_sha = _prepared_bundle(case)
    root = case['bundle']
    manifest_path = root / 'deployment-manifest.json'
    manifest = json.loads(manifest_path.read_bytes())
    paths = ('tools/__init__.py', 'waggledance/__init__.py', 'waggledance/core/__init__.py',
             'tools/bridge_compact_view.py', 'tools/wd_bridge_mind.py',
             'tools/wd_bridge_mind_lane_read.py', 'tools/wd_bridge_mind_session.py',
             'tools/wd_mind_retained_query.py')
    for path in paths:
        relative = 'tools-bootstrap/' + path
        if relative in manifest['files']:
            continue  # Preserve the fixture's original hash-pinned LF bytes.
        raw = (ROOT / path).read_bytes()
        target = root.joinpath(*relative.split('/'))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        manifest['files'][relative] = hashlib.sha256(raw).hexdigest()
    raw = canonical(manifest)
    manifest_path.write_bytes(raw)
    pin = hashlib.sha256(raw).hexdigest()
    fleet = json.loads((root / 'wd-fleet.json').read_bytes())
    identities = {entry['agent']: entry['agent_uuid'] for entry in fleet['lanes']}
    identities[fleet['tools_supervisor']['agent']] = fleet['tools_supervisor']['agent_uuid']
    when = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=2)).isoformat().replace('+00:00', 'Z')
    def event(agent, message, recipient=''):
        return {'agent': agent, 'agent_uuid': identities[agent], 'to': recipient, 'type': 'message',
                'status': 'in_progress', 'task_id': 'synthetic-five-lane', 'ts_utc': when, 'message': message}
    events = [event(name, 'synthetic work outcome for ' + name) for name in LANES[:3]]
    events += [event('codex-lead-1', 'password=synthetic-suppressed-secret'),
               event('claude-rco-1', 'synthetic excluded rco work'),
               event('codex-tools-1', 'synthetic rco addressed', 'claude-rco-2')]
    case['source'].write_bytes(b''.join(canonical(item) + b'\n' for item in events))
    allowed = {item['agent']: hashlib.sha256(canonical(item)).hexdigest() for item in events[:3]}
    policy = b'{"identifiers":[],"schema":"wd.mind-identifier-policy.v2"}'
    policy_path = case['source'].parent / 'synthetic-policy.json'
    policy_path.write_bytes(policy)
    assert hashlib.sha256(policy).hexdigest() == policy_sha
    return pin, policy_sha, policy_path, allowed


SCRIPT = r'''
import hashlib, json, pathlib, subprocess, sys
root, manifest, policy_sha, policy_path, testdir, expected_json = sys.argv[1:]
root = pathlib.Path(root)
sys.path.insert(0, str(root / 'tools-bootstrap'))
sys.path.append(testdir)
from tools import wd_mind_full_runtime as runtime
from tools import wd_mind_e5_client as e5, wd_mind_e5_lifecycle as lifecycle
from tools import wd_mind_runtime_process as process
from test_wd_mind_e5_client import response
from test_wd_mind_e5_lifecycle import Job, Process

allowed = json.loads(expected_json)
policy = pathlib.Path(policy_path).read_bytes()
binding = runtime.assemble_full_runtime(enabled=True, bundle_root=str(root), expected_manifest_sha256=manifest,
    identifier_policy_bytes=policy, expected_identifier_policy_sha256=policy_sha)
anchor = runtime.create_operator_pinned_policy_anchor(enabled=True, bundle_root=str(root),
    expected_manifest_sha256=manifest, operator_policy_sha256=policy_sha, operator_approval_reference_sha256='b'*64)
factory = runtime._ManifestWorkerFactory(str(root), manifest, {}, root / 'synthetic.lock',
    30.0, 0.0, e5.BULK_CONTRACT_SHA256)
runtime_hash = hashlib.sha256((root / 'wd-mind-e5-encoder-binding.json').read_bytes()).hexdigest()
processes, jobs, calls = [], [], {'passage':0, 'query':0}
def synthetic_factory(self, *, owner_sink):
    assert lifecycle._active is None
    worker = Process(response(0, 0)); processes.append(worker)
    class Input:
        def write(self, packet):
            length = int.from_bytes(packet[:4], 'little')
            body = json.loads(packet[4:].decode('ascii'))
            assert length == len(packet)-4 and body['op'] in calls
            calls[body['op']] += 1
            frame = response(body['id'], len(body['texts']), nonce=bytes.fromhex(body['nonce']),
                             digest=bytes.fromhex(body['request_sha256']))
            with worker.stdout.condition:
                worker.stdout.data.extend(frame); worker.stdout.condition.notify_all()
            return len(packet)
        def flush(self): pass
        def close(self): worker.finish(0)
    worker.stdin = Input()
    owner = lifecycle.reserve(enabled=True, bulk_contract_sha256=e5.BULK_CONTRACT_SHA256)
    owner_sink(owner)
    job = Job(); jobs.append(job); owner.set_job(job); owner.bind(worker)
    client = e5.GracefulE5WorkerClient(owner, timeout=29.99, encoder_binding_sha256=runtime_hash,
        bulk_contract_sha256=e5.BULK_CONTRACT_SHA256)
    owner.mark_ready()
    return {'status':'READY','owner':owner,'client':client,'bulk_contract_sha256':e5.BULK_CONTRACT_SHA256}
runtime._ManifestWorkerFactory.__call__ = synthetic_factory
owner = runtime.create_single_manifest_owner(enabled=True, binding=binding, lane='codex-lead-1',
    worker_factory=factory, shutdown_timeout=5, policy_anchor=anchor, worker_rotation=True)
service = None
try:
    started = owner.start()
    assert started['state'] == 'READY' and started['owned'] is True, started
    passage_at_ready = calls['passage']
    assert passage_at_ready > 0 and len(processes) == 1
    name = process.local_candidate_pipe_name(manifest_sha256=manifest)
    service = process.create_local_candidate_service(enabled=True, owner=owner, pipe_name=name)
    service.start()
    lanes = ('codex-lead-1','codex-tools-1','fable-5','claude-rco-1','claude-rco-2')
    reader_script = """
import sys
sys.path.insert(0, sys.argv[1] + '/tools-bootstrap')
from tools import wd_bridge_mind_session as session
args = ['--mind-local-candidate-read','--policy-path',sys.argv[4],'--policy-sha256',sys.argv[3],
        '--approval-reference-sha256','b'*64,'--lane',sys.argv[5],'--timeout-seconds','90']
raise SystemExit(session._local_candidate_entry(args, sys.stdout.buffer, sys.stderr.buffer, sys.argv[1], sys.argv[2]))
"""
    for iteration in range(2):
        for lane in lanes:
            reader = subprocess.run([sys.executable, '-I', '-B', '-c', reader_script, str(root), manifest,
                policy_sha, str(policy_path), lane], capture_output=True, timeout=100,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            assert reader.returncode == 0, (lane, iteration, reader.returncode, reader.stderr)
            result = json.loads(reader.stdout)
            assert result['lane'] == lane and result['authority'] == 'none'
            assert result['sender_authentication'] == 'UNKNOWN' and result['results']
            refs = {hit['source_event_sha256'] for hit in result['results']}
            assert refs <= set(allowed.values()), (lane, refs)
            if lane in allowed: assert refs == {allowed[lane]}, (lane, refs)
            assert all('synthetic work outcome' in hit['text'] and 'password=' not in hit['text']
                       and hit['source_agent'] in allowed for hit in result['results'])
            assert calls['passage'] == passage_at_ready and len(processes) == 1
    assert calls['query'] == 10, calls
    assert service.close(timeout=10)['state'] == 'STOPPED'; service = None
    stopped = owner.close()
    assert stopped['state'] == 'STOPPED' and stopped['owned'] is False, stopped
    assert all(worker.poll() == 0 for worker in processes) and all(job.closed for job in jobs)
    assert lifecycle._active is None
    print(json.dumps({'status':'ok','lanes':len(lanes),'reads':10,'passage_calls':passage_at_ready}))
finally:
    if service is not None: service.close(timeout=10)
    for worker in processes: worker.finish(0)
    owner.close()
'''


@pytest.mark.skipif(os.name != 'nt', reason='Windows named pipes only')
def test_one_synthetic_owner_serves_five_lane_readers_without_rebuilding(synthetic_bundle):
    pin, policy_sha, policy_path, allowed = _stage(synthetic_bundle)
    result = subprocess.run([sys.executable, '-I', '-B', '-c', SCRIPT,
        str(synthetic_bundle['bundle']), pin, policy_sha, str(policy_path), str(ROOT / 'tests/tools'),
        json.dumps(allowed)], capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)['status'] == 'ok'
