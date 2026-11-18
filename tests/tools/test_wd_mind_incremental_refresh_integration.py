"""Synthetic owner refresh through the actual rotating adapter and worker client.

Only the model and worker transport are simulated.  This test never opens the
live bridge or persists embeddings; it does not test owner/host restart.
"""
import hashlib
import json
import subprocess
from wd_mind_owned_compat import run_owned
import sys
from pathlib import Path

import pytest

from test_wd_mind_full_runtime import canonical, synthetic_bundle
from test_wd_mind_rotation_integration import _prepared_bundle


ROOT = Path(__file__).resolve().parents[2]


SCRIPT = r'''
import datetime, hashlib, json, os, pathlib, sys
root, manifest, policy_sha, testdir, source, case = sys.argv[1:]
root, source = pathlib.Path(root), pathlib.Path(source)
sys.path.insert(0, str(root / 'tools-bootstrap'))
sys.path.append(testdir)
from tools import wd_mind_full_runtime as runtime
from tools import wd_mind_e5_client as e5, wd_mind_e5_lifecycle as lifecycle
from tools import wd_mind_e5_snapshot_adapter as adapter
from test_wd_mind_e5_client import response
from test_wd_mind_e5_lifecycle import Job, Process

rotation = adapter._load_pinned_rotating_module(str(root), manifest)
policy = b'{"identifiers":[],"schema":"wd.mind-identifier-policy.v2"}'
binding = runtime.assemble_full_runtime(enabled=True, bundle_root=str(root),
    expected_manifest_sha256=manifest, identifier_policy_bytes=policy,
    expected_identifier_policy_sha256=policy_sha)
anchor = runtime.create_operator_pinned_policy_anchor(enabled=True, bundle_root=str(root),
    expected_manifest_sha256=manifest, operator_policy_sha256=policy_sha,
    operator_approval_reference_sha256='b'*64)
factory = runtime._ManifestWorkerFactory(str(root), manifest, {}, root / 'synthetic.lock',
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
runtime_hash = hashlib.sha256((root / 'wd-mind-e5-encoder-binding.json').read_bytes()).hexdigest()
processes, jobs, operations = [], [], {'passage': 0, 'query': 0}

def synthetic_factory(self, *, owner_sink, call_deadline=None):
    assert lifecycle._active is None
    assert all(process.poll() == 0 for process in processes)
    assert all(job.closed for job in jobs)
    process = Process(response(0, 0)); processes.append(process)
    class Input:
        def write(self, packet):
            size = int.from_bytes(packet[:4], 'little')
            body = json.loads(packet[4:].decode('ascii'))
            assert size == len(packet)-4 and body['op'] in operations
            operations[body['op']] += len(body['texts'])
            frame = response(body['id'], len(body['texts']), nonce=bytes.fromhex(body['nonce']),
                             digest=bytes.fromhex(body['request_sha256']))
            with process.stdout.condition:
                process.stdout.data.extend(frame)
                process.stdout.condition.notify_all()
            return len(packet)
        def flush(self): pass
        def close(self): process.finish(0)
    process.stdin = Input()
    worker = lifecycle.reserve(enabled=True, bulk_contract_sha256=e5.BULK_CONTRACT_SHA256)
    owner_sink(worker)
    job = Job(); jobs.append(job); worker.set_job(job); worker.bind(process)
    client = e5.GracefulE5WorkerClient(worker, timeout=29.99, encoder_binding_sha256=runtime_hash,
                                        bulk_contract_sha256=e5.BULK_CONTRACT_SHA256)
    worker.mark_ready()
    return {'status':'READY', 'owner':worker, 'client':client,
            'bulk_contract_sha256':e5.BULK_CONTRACT_SHA256}

runtime._ManifestWorkerFactory.__call__ = synthetic_factory
owner = runtime.create_single_manifest_owner(enabled=True, binding=binding, lane='codex-lead-1',
    worker_factory=factory, shutdown_timeout=5, policy_anchor=anchor, worker_rotation=True)

def disk_snapshot():
    # The synthetic fixture has no symlinks; only source or explicitly mutated
    # synthetic policy may change during the test.
    return {str(path.relative_to(root.parent)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.parent.rglob('*') if path.is_file()}

def append(event):
    raw = json.dumps(event, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    with source.open('ab') as stream: stream.write(raw + b'\n')
    return hashlib.sha256(raw).hexdigest()

try:
    assert owner.start()['state'] == 'READY'
    generation = owner._generation
    old_ids = tuple(owner._handler._memory.ids)
    old_passages = operations['passage']
    assert old_passages > 0 and old_ids
    old_handler = owner._handler
    old_view = old_handler.lane_view('codex-lead-1') if case == 'stale' else None
    old_export = None
    if case == 'stale':
        nonce = 'd' * 64
        request = {'schema':'wd.mind-pipe-request.v1', 'query_id':'startup_work_overview',
                   'nonce':nonce, 'lane':'codex-lead-1', 'scope':'own'}
        old_export = owner._prepare_consistent_delivery(enabled=True, request=request, expected_nonce=nonce,
            expected_identifier_policy_sha256=policy_sha, max_total_bytes=64*1024*1024)['export']
    on_disk_before = disk_snapshot()
    event = json.loads(source.read_bytes().splitlines()[0])
    new_ref = None
    if case == 'append':
        event['message'] = 'unique incremental synthetic new work result'
        event['ts_utc'] = (datetime.datetime.now(datetime.timezone.utc) -
                           datetime.timedelta(minutes=2)).isoformat().replace('+00:00','Z')
        new_ref = append(event)
    elif case == 'excluded':
        rco = dict(event, agent='claude-rco-1',
                   agent_uuid='00000000-0000-4000-8000-000000000001',
                   message='synthetic rco review excluded')
        secret = dict(event, message='password=syntheticfixturevalue')
        append(rco); append(secret)
    elif case == 'rewrite':
        original = source.read_bytes()
        changed = original.replace(b'ordinary', b'Ordinary')
        assert changed != original and len(changed) == len(original)
        source.write_bytes(changed)
    elif case == 'short':
        original = source.read_bytes(); source.write_bytes(original[:-1])
    elif case == 'identity':
        # Both paths are under the pytest-owned synthetic source directory.
        replacement = source.with_name('replacement.jsonl')
        replacement.write_bytes(source.read_bytes())
        os.replace(replacement, source)
    elif case == 'policy':
        (root / 'wd-mind-message-policy.json').write_bytes(b'{}')
    elif case == 'encoder':
        (root / 'wd-mind-e5-encoder-binding.json').write_bytes(b'{}')

    if case == 'busy':
        assert old_handler._lock.acquire(blocking=False)
        try:
            try: owner._refresh_generation(enabled=True)
            except runtime.FullRuntimeRefused: pass
            else: raise AssertionError('busy old handler was swapped')
        finally:
            old_handler._lock.release()
        assert owner._generation is generation and operations['passage'] == old_passages
        assert owner.status()['state'] == 'READY'
        query = owner.query_local(enabled=True, lane='codex-lead-1', scope='own',
                                  query='current mission overview')
        assert query['authority'] == 'none' and query['results']
        assert operations['passage'] == old_passages
    elif case in ('rewrite', 'short', 'identity', 'policy', 'encoder'):
        try: owner._refresh_generation(enabled=True)
        except runtime.FullRuntimeRefused: pass
        else: raise AssertionError('changed source or policy was accepted')
        assert owner.status()['state'] == 'HOLD'
        assert owner._generation is generation
        try: owner.query_local(enabled=True, lane='codex-lead-1', scope='own', query='synthetic work')
        except runtime.FullRuntimeRefused: pass
        else: raise AssertionError('held owner returned content')
    else:
        result = owner._refresh_generation(enabled=True)
        assert result['status'] == 'REFRESHED' and owner.status()['state'] == 'READY'
        new_ids = tuple(owner._handler._memory.ids)
        new_records = json.loads(owner._snapshot['projection_bytes'])['messages']['records']
        if case in ('noop', 'excluded', 'stale'):
            assert new_ids == old_ids
            assert operations['passage'] == old_passages, (case, operations, old_passages)
        else:
            assert set(old_ids) < set(new_ids)
            additions = [row for row in new_records if row['record_id'] not in old_ids]
            assert additions and all(row['provenance']['source_event_ref'] == new_ref for row in additions)
            assert operations['passage'] - old_passages == len(additions), (operations, old_passages, additions)
        if case == 'stale':
            request = {'schema':'wd.mind-flat-owner-request.v1', 'lane':'codex-lead-1',
                       'scope':'own', 'query':'current mission overview'}
            try: old_view.handle(request)
            except runtime.FullRuntimeRefused: pass
            else: raise AssertionError('stale lane view returned content')
            try: owner._check_generation_export(enabled=True, export=old_export)
            except runtime.FullRuntimeRefused: pass
            else: raise AssertionError('stale generation export was accepted')
            old_handler.close()
            assert owner.status()['state'] == 'READY', 'stale handler closed the shared owner'
        query = owner.query_local(enabled=True, lane='codex-lead-1', scope='own',
                                  query='current mission overview')
        assert query['authority'] == 'none' and query['results']
        assert all(hit['source_agent'] == 'codex-lead-1' and 'password=' not in hit['text']
                   for hit in query['results'])
        if case == 'append':
            assert new_ref in {hit['source_event_sha256'] for hit in query['results']}
    after = disk_snapshot()
    expected_mutations = {str(source.relative_to(root.parent))} if case in ('append','excluded','rewrite','short') else set()
    if case == 'policy': expected_mutations.add(str((root / 'wd-mind-message-policy.json').relative_to(root.parent)))
    if case == 'encoder': expected_mutations.add(str((root / 'wd-mind-e5-encoder-binding.json').relative_to(root.parent)))
    assert set(after) == set(on_disk_before), 'refresh created a file'
    assert {name for name in after if after[name] != on_disk_before[name]} == expected_mutations
    print(json.dumps({'status':'ok','case':case}))
finally:
    for process in processes: process.finish(0)
    closed = owner.close()
    assert closed['state'] in ('STOPPED','HOLD') and closed['owned'] is False, closed
    assert all(process.poll() == 0 for process in processes) and all(job.closed for job in jobs)
    assert lifecycle._active is None
'''


@pytest.mark.parametrize('case', ['noop', 'append', 'excluded', 'rewrite', 'short', 'identity', 'policy', 'encoder', 'stale', 'busy'])
def test_incremental_refresh_uses_only_verified_memory(synthetic_bundle, case):
    digest, policy_sha = _prepared_bundle(synthetic_bundle)
    source = synthetic_bundle['source']
    source.write_bytes(b'\n'.join(source.read_bytes().splitlines()[:5]) + b'\n')
    result = run_owned([sys.executable, '-B', '-c', SCRIPT, str(synthetic_bundle['bundle']),
        digest, policy_sha, str(ROOT / 'tests/tools'), str(source), case],
        capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {'status':'ok', 'case':case}
