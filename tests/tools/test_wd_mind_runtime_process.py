"""Synthetic-only runtime-process preflight tests; no real model is loaded."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading

import pytest

from tools import wd_mind_runtime_process as preflight


def _candidate_frame(request):
    return {'schema':'wd.mind-pipe-candidates.v1', 'nonce':request['nonce'],
            'lane':request['lane'], 'scope':request['scope'], 'status':'CANDIDATES',
            'candidates':[{'ref_sha256':'a' * 64, 'doc_id_sha256':'b' * 64,
                           'source_event_sha256':'c' * 64}]}


def _candidate_request(lane='codex-lead-1', scope='own'):
    return {'schema':'wd.mind-pipe-request.v1', 'query_id':'startup_work_overview',
            'nonce':'d' * 64, 'lane':lane, 'scope':scope}


class _SyntheticCandidateOwner:
    def __init__(self):
        self.requests = []

    def candidates(self, request):
        self.requests.append(request)
        return _candidate_frame(request)


def _located_request(lane='codex-lead-1', scope='own'):
    return {**_candidate_request(lane, scope), 'schema':'wd.mind-pipe-request.v2'}


def _located_frame(request):
    result = _candidate_frame(request)
    result['schema'] = 'wd.mind-pipe-candidates.v2'
    result['candidates'][0].update(source_offset=123, source_length=456, chunk_index=2)
    return result


def test_candidate_ipc_service_is_off_by_default():
    with pytest.raises(preflight.RuntimePreflightRefused):
        preflight.create_local_candidate_service(owner=_SyntheticCandidateOwner(), pipe_name='x')


@pytest.mark.skipif(os.name != 'nt', reason='Windows named pipes only')
def test_candidate_ipc_service_uncertain_thread_start_stays_owned(monkeypatch):
    import multiprocessing.connection as connection
    class Listener:
        closed = False
        def close(self): self.closed = True
    listener = Listener()
    class UncertainThread:
        def __init__(self, *args, **kwargs): self.target = kwargs['target']
        def start(self): raise KeyboardInterrupt()
        def is_alive(self): return False
        def fire(self): self.target()
    monkeypatch.setattr(connection, 'Listener', lambda **kwargs: listener)
    monkeypatch.setattr(preflight.threading, 'Thread', UncertainThread)
    owner = _SyntheticCandidateOwner()
    name = preflight.local_candidate_pipe_name(manifest_sha256='a' * 64)
    service = preflight.create_local_candidate_service(enabled=True, owner=owner, pipe_name=name)
    with pytest.raises(KeyboardInterrupt):
        service.start()
    assert service.status()['state'] == 'HOLD'
    assert service.close(timeout=0)['state'] == 'HOLD'
    assert service._owner is owner and not listener.closed
    service._thread.fire()
    assert service.status()['state'] == 'STOPPED' and listener.closed


@pytest.mark.skipif(os.name != 'nt', reason='Windows named pipes only')
def test_candidate_ipc_client_uncertain_thread_start_stays_owned(monkeypatch):
    import multiprocessing.connection as connection
    real_thread = threading.Thread
    class UncertainThread:
        def __init__(self, *args, **kwargs): self.target, self.ident = kwargs['target'], None
        def start(self): raise KeyboardInterrupt()
        def is_alive(self): return False
        def fire(self):
            actual = real_thread(target=self.target)
            pending = None
            try:
                actual.start()
            except BaseException as exc:
                pending = exc
            # Even repeated cancellations cannot discard an uncertain start.
            import time
            while True:
                try:
                    if actual.ident is not None:
                        actual.join(1)
                        if not actual.is_alive(): break
                except BaseException as exc:
                    if pending is None: pending = exc
                try:
                    time.sleep(0.05)
                except BaseException as exc:
                    if pending is None: pending = exc
            self.ident = actual.ident
            if pending is not None: raise pending
    monkeypatch.setattr(preflight.threading, 'Thread', UncertainThread)
    def refused(**kwargs): raise OSError('synthetic refusal')
    monkeypatch.setattr(connection, 'Client', refused)
    name = preflight.local_candidate_pipe_name(manifest_sha256='a' * 64)
    query = preflight.create_local_candidate_query(enabled=True, pipe_name=name, request=_candidate_request())
    with pytest.raises(KeyboardInterrupt):
        query.start()
    assert query.status()['state'] == 'HOLD'
    assert query.wait(timeout=0)['status'] == 'HOLD'
    assert query.close(timeout=0)['state'] == 'HOLD'
    query._thread.fire()
    assert query.status()['state'] == 'STOPPED'
    assert query.wait(timeout=0)['status'] == 'TIMEOUT'
    assert query.close(timeout=0)['state'] == 'STOPPED'


@pytest.mark.skipif(os.name != 'nt', reason='Windows named pipes only')
def test_candidate_ipc_closed_unstarted_client_cannot_start():
    name = preflight.local_candidate_pipe_name(manifest_sha256='a' * 64)
    query = preflight.create_local_candidate_query(enabled=True, pipe_name=name, request=_candidate_request())
    assert query.close(timeout=0)['state'] == 'STOPPED'
    assert query.status()['state'] == 'STOPPED'
    with pytest.raises(preflight.RuntimePreflightRefused):
        query.start()


@pytest.mark.skipif(os.name != 'nt', reason='Windows named pipes only')
def test_candidate_ipc_wake_creation_failure_can_retry(monkeypatch):
    name = preflight.local_candidate_pipe_name(manifest_sha256=_hash(os.urandom(32)))
    service = preflight.create_local_candidate_service(enabled=True, owner=_SyntheticCandidateOwner(), pipe_name=name)
    service.start()
    original = threading.Thread
    calls = []
    def factory(*args, **kwargs):
        if kwargs.get('name') == 'mind-local-wake' and not calls:
            calls.append('construction_failed_before_start')
            raise RuntimeError('synthetic pre-start construction failure')
        return original(*args, **kwargs)
    monkeypatch.setattr(preflight.threading, 'Thread', factory)
    try:
        assert service.close(timeout=0)['state'] == 'HOLD'
        assert service.close(timeout=5)['state'] == 'STOPPED'
    finally:
        monkeypatch.setattr(preflight.threading, 'Thread', original)
        service.close(timeout=5)


@pytest.mark.skipif(os.name != 'nt', reason='Windows named pipes only')
def test_candidate_ipc_completed_unsuccessful_wake_can_retry(monkeypatch):
    name = preflight.local_candidate_pipe_name(manifest_sha256=_hash(os.urandom(32)))
    service = preflight.create_local_candidate_service(enabled=True, owner=_SyntheticCandidateOwner(), pipe_name=name)
    service.start()
    original = service._wake
    calls = []
    def wake():
        calls.append('wake')
        if len(calls) > 1: original()
    monkeypatch.setattr(service, '_wake', wake)
    try:
        assert service.close(timeout=0)['state'] == 'HOLD'
        service._wake_thread.join(5)
        assert service.close(timeout=5)['state'] == 'STOPPED'
        assert len(calls) == 2
    finally:
        monkeypatch.setattr(service, '_wake', original)
        if service._thread.is_alive():
            from multiprocessing.connection import Client
            with Client(address=name, family='AF_PIPE', authkey=None): pass
            service._thread.join(5)


@pytest.mark.skipif(os.name != 'nt', reason='Windows named pipes only')
@pytest.mark.parametrize('error', [KeyboardInterrupt, RuntimeError])
def test_candidate_ipc_uncertain_wake_start_stays_hold_after_service_exit(monkeypatch, error):
    from multiprocessing.connection import Client
    name = preflight.local_candidate_pipe_name(manifest_sha256=_hash(os.urandom(32)))
    service = preflight.create_local_candidate_service(enabled=True, owner=_SyntheticCandidateOwner(), pipe_name=name)
    service.start()
    original = threading.Thread
    class UncertainWake:
        def __init__(self, *args, **kwargs): self.target = kwargs['target']
        def start(self): raise error()
        def is_alive(self): return False
        def fire(self): self.target()
    def factory(*args, **kwargs):
        return UncertainWake(*args, **kwargs) if kwargs.get('name') == 'mind-local-wake' else original(*args, **kwargs)
    monkeypatch.setattr(preflight.threading, 'Thread', factory)
    try:
        if error is KeyboardInterrupt:
            with pytest.raises(KeyboardInterrupt):
                service.close(timeout=0)
        else:
            assert service.close(timeout=0)['state'] == 'HOLD'
        with Client(address=name, family='AF_PIPE', authkey=None): pass
        service._thread.join(5)
        assert not service._thread.is_alive()
        assert service.status()['state'] == 'HOLD'
        assert service.close(timeout=0)['state'] == 'HOLD'
        service._wake_thread.fire()
        assert service.status()['state'] == 'STOPPED'
    finally:
        monkeypatch.setattr(preflight.threading, 'Thread', original)
        if service._thread.is_alive():
            with Client(address=name, family='AF_PIPE', authkey=None): pass
            service._thread.join(5)


@pytest.mark.skipif(os.name != 'nt', reason='Windows named pipes only')
def test_candidate_ipc_owner_baseexception_is_failed_not_stopped():
    class InterruptingOwner:
        def candidates(self, request): raise KeyboardInterrupt()
    name = preflight.local_candidate_pipe_name(manifest_sha256=_hash(os.urandom(32)))
    service = preflight.create_local_candidate_service(enabled=True, owner=InterruptingOwner(), pipe_name=name)
    service.start()
    query = preflight.create_local_candidate_query(enabled=True, pipe_name=name, request=_candidate_request())
    query.start()
    assert query.wait(timeout=5)['status'] == 'REFUSED'
    assert service.close(timeout=5)['state'] == 'FAILED'
    assert service.status()['state'] == 'FAILED'


@pytest.mark.skipif(os.name != 'nt', reason='Windows named pipes only')
def test_candidate_ipc_serves_all_five_lanes_as_unverified_references():
    owner = _SyntheticCandidateOwner()
    name = preflight.local_candidate_pipe_name(manifest_sha256=_hash(os.urandom(32)))
    service = preflight.create_local_candidate_service(enabled=True, owner=owner, pipe_name=name)
    service.start()
    try:
        lanes = ('codex-lead-1', 'codex-tools-1', 'claude-rco-1', 'claude-rco-2', 'fable-5')
        for lane in lanes:
            scope = 'shared' if lane.startswith('claude-') else 'own'
            request = _candidate_request(lane, scope)
            query = preflight.create_local_candidate_query(enabled=True, pipe_name=name, request=request)
            query.start()
            result = query.wait(timeout=10)
            assert result['status'] == 'CANDIDATES'
            assert result['authority'] == 'none' and result['sender_authentication'] == 'UNKNOWN'
            assert result['source_verification'] == 'UNVERIFIED'
            assert json.loads(result['candidate_frame']) == _candidate_frame(request)
        assert len(owner.requests) == 5
    finally:
        assert service.close(timeout=5)['state'] == 'STOPPED'


@pytest.mark.skipif(os.name != 'nt', reason='Windows named pipes only')
def test_candidate_ipc_five_simultaneous_lane_queries_close_cleanly():
    lanes = ('codex-lead-1', 'codex-tools-1', 'claude-rco-1', 'claude-rco-2', 'fable-5')
    release_owner = threading.Event()
    start_gate = threading.Barrier(6, timeout=10)
    started_gate = threading.Barrier(6, timeout=10)
    requests, queries, results, errors = {}, {}, {}, []

    class Owner:
        def candidates(self, request):
            if not release_owner.wait(10):
                raise TimeoutError('test owner not released')
            response = _candidate_frame(request)
            response['candidates'][0]['source_event_sha256'] = _hash(request['lane'].encode())
            return response

    name = preflight.local_candidate_pipe_name(manifest_sha256=_hash(os.urandom(32)))
    service = preflight.create_local_candidate_service(enabled=True, owner=Owner(), pipe_name=name)
    service.start()
    workers = []
    def run(lane):
        try:
            start_gate.wait()
            queries[lane].start()
            started_gate.wait()
            results[lane] = queries[lane].wait(timeout=30)
        except BaseException as exc:
            errors.append((lane, exc))

    try:
        for lane in lanes:
            request = _candidate_request(lane, 'shared' if lane.startswith('claude-') else 'own')
            request['nonce'] = _hash(('nonce:' + lane).encode())
            requests[lane] = request
            queries[lane] = preflight.create_local_candidate_query(enabled=True, pipe_name=name, request=request)
            worker = threading.Thread(target=run, args=(lane,))
            workers.append(worker)
            worker.start()
        start_gate.wait()
        started_gate.wait()
        release_owner.set()
        for worker in workers:
            worker.join(timeout=35)
        assert not errors and all(not worker.is_alive() for worker in workers)
        assert set(results) == set(lanes)
        for lane in lanes:
            result = results[lane]
            assert result['status'] == 'CANDIDATES'
            frame = json.loads(result['candidate_frame'])
            assert frame['lane'] == lane and frame['nonce'] == requests[lane]['nonce']
            assert frame['candidates'][0]['source_event_sha256'] == _hash(lane.encode())
    finally:
        release_owner.set()
        for worker in workers:
            worker.join(timeout=35)
        for query in queries.values():
            assert query.close(timeout=10)['state'] == 'STOPPED'
        assert service.close(timeout=10)['state'] == 'STOPPED'


@pytest.mark.skipif(os.name != 'nt', reason='Windows named pipes only')
def test_candidate_ipc_rejects_text_and_retains_owner_on_timeout():
    gate = threading.Event()
    entered = threading.Event()
    class SlowOwner:
        def candidates(self, request):
            entered.set()
            gate.wait(10)
            return {**_candidate_frame(request), 'text': 'must not be returned'}
    name = preflight.local_candidate_pipe_name(manifest_sha256=_hash(os.urandom(32)))
    service = preflight.create_local_candidate_service(enabled=True, owner=SlowOwner(), pipe_name=name)
    service.start()
    query = preflight.create_local_candidate_query(enabled=True, pipe_name=name, request=_candidate_request())
    try:
        query.start()
        assert entered.wait(5)
        assert query.wait(timeout=0.01)['status'] == 'TIMEOUT'
        assert service.close(timeout=0.01)['state'] == 'HOLD'
    finally:
        gate.set()
        assert service.close(timeout=5)['state'] == 'STOPPED'
    assert query.wait(timeout=5)['status'] == 'TIMEOUT'


@pytest.mark.skipif(os.name != 'nt', reason='Windows named pipes only')
@pytest.mark.parametrize('fault', ['text', 'nonce', 'lane', 'source_hash'])
def test_candidate_ipc_rejects_untrusted_or_malformed_reply(fault):
    class BadOwner:
        def candidates(self, request):
            result = _candidate_frame(request)
            if fault == 'text': result['text'] = 'must not be returned'
            elif fault == 'nonce': result['nonce'] = 'e' * 64
            elif fault == 'lane': result['lane'] = 'fable-5'
            else: result['candidates'][0]['source_event_sha256'] = 'not-a-hash'
            return result
    name = preflight.local_candidate_pipe_name(manifest_sha256=_hash(os.urandom(32)))
    service = preflight.create_local_candidate_service(enabled=True, owner=BadOwner(), pipe_name=name)
    service.start()
    try:
        query = preflight.create_local_candidate_query(enabled=True, pipe_name=name, request=_candidate_request())
        query.start()
        result = query.wait(timeout=10)
        assert result['status'] == 'REFUSED' and result['candidate_frame'] is None
        assert result['source_verification'] == 'UNVERIFIED'
    finally:
        assert service.close(timeout=5)['state'] == 'STOPPED'


@pytest.mark.skipif(os.name != 'nt', reason='Windows named pipes only')
def test_candidate_ipc_serves_an_independent_python_process():
    name = preflight.local_candidate_pipe_name(manifest_sha256=_hash(os.urandom(32)))
    service = preflight.create_local_candidate_service(enabled=True, owner=_SyntheticCandidateOwner(), pipe_name=name)
    service.start()
    script = '''
import json, sys
from tools import wd_mind_runtime_process as p
request = {'schema':'wd.mind-pipe-request.v1','query_id':'startup_work_overview',
           'nonce':'d'*64,'lane':'fable-5','scope':'own'}
query = p.create_local_candidate_query(enabled=True, pipe_name=sys.argv[1], request=request)
query.start()
result = query.wait(timeout=10)
print(json.dumps({'status':result['status'],'verified':result['source_verification'],
                  'frame':json.loads(result['candidate_frame'])}))
'''
    try:
        completed = subprocess.run([sys.executable, '-c', script, name], cwd=str(Path(__file__).resolve().parents[2]),
                                   capture_output=True, text=True, timeout=20, check=True)
        result = json.loads(completed.stdout)
        assert result == {'status':'CANDIDATES', 'verified':'UNVERIFIED',
                          'frame':_candidate_frame(_candidate_request('fable-5', 'own'))}
    finally:
        assert service.close(timeout=5)['state'] == 'STOPPED'


@pytest.mark.skipif(os.name != 'nt', reason='Windows named pipes only')
def test_candidate_ipc_v2_locations_remain_unverified_and_v1_is_unchanged():
    class Owner(_SyntheticCandidateOwner):
        def located_candidates(self, request): return _located_frame(request)
    name = preflight.local_candidate_pipe_name(manifest_sha256=_hash(os.urandom(32)))
    service = preflight.create_local_candidate_service(enabled=True, owner=Owner(), pipe_name=name)
    service.start()
    try:
        for request, expected in ((_candidate_request(), _candidate_frame), (_located_request(), _located_frame)):
            query = preflight.create_local_candidate_query(enabled=True, pipe_name=name, request=request)
            query.start()
            answer = query.wait(timeout=10)
            assert answer['status'] == 'CANDIDATES' and answer['source_verification'] == 'UNVERIFIED'
            assert json.loads(answer['candidate_frame']) == expected(request)
    finally:
        assert service.close(timeout=5)['state'] == 'STOPPED'


@pytest.mark.skipif(os.name != 'nt', reason='Windows named pipes only')
@pytest.mark.parametrize('field,value', [
    ('source_offset', -1), ('source_offset', True), ('source_offset', 2**63),
    ('source_length', 0), ('source_length', 4194305), ('source_length', '10'),
    ('chunk_index', -1), ('chunk_index', False), ('chunk_index', 4194305),
])
def test_candidate_ipc_v2_refuses_bad_untrusted_locations(field, value):
    class Owner(_SyntheticCandidateOwner):
        def located_candidates(self, request):
            result = _located_frame(request)
            result['candidates'][0][field] = value
            return result
    name = preflight.local_candidate_pipe_name(manifest_sha256=_hash(os.urandom(32)))
    service = preflight.create_local_candidate_service(enabled=True, owner=Owner(), pipe_name=name)
    service.start()
    try:
        query = preflight.create_local_candidate_query(enabled=True, pipe_name=name, request=_located_request())
        query.start()
        assert query.wait(timeout=10)['status'] == 'REFUSED'
    finally:
        assert service.close(timeout=5)['state'] == 'STOPPED'


@pytest.mark.skipif(os.name != 'nt', reason='Windows named pipes only')
def test_candidate_ipc_duplicate_keys_and_binary_frames_are_not_deserialized():
    from multiprocessing.connection import Client
    owner = _SyntheticCandidateOwner()
    name = preflight.local_candidate_pipe_name(manifest_sha256=_hash(os.urandom(32)))
    service = preflight.create_local_candidate_service(enabled=True, owner=owner, pipe_name=name)
    service.start()
    try:
        for raw in (b'{"schema":"one","schema":"two"}', b'\x80\x04N.'):
            with Client(address=name, family='AF_PIPE', authkey=None) as connection:
                connection.send_bytes(raw)
                try:
                    assert connection.poll(5)
                    with pytest.raises((EOFError, BrokenPipeError)):
                        connection.recv_bytes()
                except BrokenPipeError:
                    pass  # Windows can signal closure at poll or at recv.
        assert owner.requests == []
    finally:
        assert service.close(timeout=5)['state'] == 'STOPPED'


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()










@pytest.fixture
def candidate(tmp_path):
    root = tmp_path / "runtime"
    files = []
    for path, raw in (("site/synthetic.py", b"# inert synthetic site\n"),
                      ("model/config.json", b"{}"), ("assets/code/read.py", b"# inert\n")):
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        files.append({"path": path, "size": len(raw), "sha256": _hash(raw)})
    generation, revision = "a" * 40, "b" * 40
    data = json.dumps({"schema": "wd.q4-runtime-manifest.v1", "generation": generation,
                       "revision": revision, "files": files}, sort_keys=True).encode()
    manifest = tmp_path / "runtime-manifest.json"
    manifest.write_bytes(data)
    interpreter = tmp_path / "synthetic-interpreter.exe"
    bootstrap = tmp_path / "synthetic-bootstrap.py"
    bundle = tmp_path / "synthetic-bundle"
    verifier = bundle / "tools-bootstrap/tools/wd_mind_runtime_manifest.py"
    verifier.parent.mkdir(parents=True)
    interpreter.write_bytes(b"synthetic interpreter, never execute")
    bootstrap.write_bytes(b"raise AssertionError('must never run')\n")
    verifier.write_bytes((Path(__file__).resolve().parents[2] / "tools/wd_mind_runtime_manifest.py").read_bytes())
    deployment = json.dumps({"schema_version": 1, "files": {
        "tools-bootstrap/tools/wd_mind_runtime_manifest.py": _hash(verifier.read_bytes()).upper()}},
        sort_keys=True).encode()
    (bundle / "deployment-manifest.json").write_bytes(deployment)
    return {"interpreter": interpreter, "interpreter_sha256": _hash(interpreter.read_bytes()),
            "bootstrap": bootstrap, "bootstrap_sha256": _hash(bootstrap.read_bytes()),
            "bundle_root": bundle, "deployment_manifest_sha256": _hash(deployment),
            "verifier": verifier, "verifier_sha256": _hash(verifier.read_bytes()),
            "runtime_root": root, "runtime_manifest": manifest, "runtime_manifest_sha256": _hash(data),
            "generation": generation, "revision": revision}


def _refuses(candidate):
    with pytest.raises(preflight.RuntimePreflightRefused, match="^runtime_process_preflight_refused$") as exc:
        preflight.verify_runtime_process_preflight(**candidate)
    assert exc.value.__context__ is None


def test_verified_preflight_never_runs_process_opens_network_or_loads_model(candidate, monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("process started"))
    monkeypatch.setattr(socket, "socket", lambda *a, **k: pytest.fail("network opened"))
    root = candidate["runtime_root"].parent
    files_before = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    before = {name for name in sys.modules if name.split(".")[0] in {"torch", "faiss", "sentence_transformers"}}
    result = preflight.verify_runtime_process_preflight(**candidate)
    assert result["status"] == "VERIFIED_PREFLIGHT_ONLY"
    assert result["process_started"] is False and result["model_loaded"] is False
    assert result["native_closure"] == result["network_isolation"] == result["same_user_race"] == "UNKNOWN"
    assert result["file_count"] == 3 and result["runtime_manifest_sha256"] == candidate["runtime_manifest_sha256"]
    assert before == {name for name in sys.modules if name.split(".")[0] in {"torch", "faiss", "sentence_transformers"}}
    assert files_before == {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}


@pytest.mark.parametrize("key", ["interpreter_sha256", "bootstrap_sha256", "verifier_sha256", "deployment_manifest_sha256",
                                  "runtime_manifest_sha256", "generation", "revision"])
def test_wrong_independently_pinned_value_fails_closed(candidate, monkeypatch, key):
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("process started"))
    candidate[key] = "c" * (40 if key in {"generation", "revision"} else 64)
    _refuses(candidate)


@pytest.mark.parametrize("target", ["interpreter", "bootstrap", "verifier"])
def test_missing_or_mutated_code_fails_before_execution(candidate, monkeypatch, target):
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("process started"))
    candidate[target].unlink()
    _refuses(candidate)


def test_unapproved_verifier_import_never_executes(candidate, tmp_path, monkeypatch):
    marker = tmp_path / "must-not-exist"
    malicious = "from pathlib import Path\nPath(%r).write_text('import executed')\n" % str(marker)
    candidate["verifier"].write_text(malicious, encoding="utf-8")
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("process started"))
    _refuses(candidate)
    assert not marker.exists()


def test_self_pinned_unapproved_verifier_still_never_executes(candidate, tmp_path, monkeypatch):
    marker = tmp_path / "must-not-exist"
    malicious = ("from pathlib import Path\nPath(%r).write_text('unauthorized')\n" % str(marker)).encode()
    candidate["verifier"].write_bytes(malicious)
    # Matching attacker-supplied bytes and digest are not a trust anchor.
    candidate["verifier_sha256"] = _hash(malicious)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("process started"))
    _refuses(candidate)
    assert not marker.exists()


def test_self_pinned_manifest_and_verifier_still_refuse_unapproved_code(candidate, tmp_path):
    marker = tmp_path / "must-not-exist"
    malicious = ("from pathlib import Path\nPath(%r).write_text('unauthorized')\n" % str(marker)).encode()
    candidate["verifier"].write_bytes(malicious)
    candidate["verifier_sha256"] = _hash(malicious)
    manifest = json.dumps({"schema_version": 1, "files": {
        "tools-bootstrap/tools/wd_mind_runtime_manifest.py": _hash(malicious)}}, sort_keys=True).encode()
    (candidate["bundle_root"] / "deployment-manifest.json").write_bytes(manifest)
    candidate["deployment_manifest_sha256"] = _hash(manifest)
    _refuses(candidate)
    assert not marker.exists()


def test_verifier_outside_pinned_bundle_is_refused(candidate, tmp_path):
    outsider = tmp_path / "outside-verifier.py"
    outsider.write_bytes(candidate["verifier"].read_bytes())
    candidate["verifier"] = outsider
    _refuses(candidate)


def test_duplicate_deployment_manifest_keys_refuse(candidate):
    manifest = b'{"schema_version":1,"schema_version":1,"files":{}}'
    (candidate["bundle_root"] / "deployment-manifest.json").write_bytes(manifest)
    candidate["deployment_manifest_sha256"] = _hash(manifest)
    _refuses(candidate)


@pytest.mark.parametrize("mutation", ["missing", "changed", "extra"])
def test_runtime_tree_mutation_fails_closed(candidate, mutation):
    model = candidate["runtime_root"] / "model/config.json"
    if mutation == "missing":
        model.unlink()
    elif mutation == "changed":
        model.write_bytes(b"[]")  # same length, different content
    else:
        (candidate["runtime_root"] / "model/extra.json").write_bytes(b"{}")
    _refuses(candidate)


def test_syntax_error_in_pinned_verifier_is_fixed_refusal(candidate):
    bad = b"this is not valid python !\n"
    candidate["verifier"].write_bytes(bad)
    candidate["verifier_sha256"] = _hash(bad)
    _refuses(candidate)


@pytest.mark.parametrize('entrypoint', ['reserve_guardian_attempt','create_candidate_pipe',
    'query_candidate_pipe','create_candidate_service','start_manifest_guardian'])
def test_deferred_guardian_and_named_pipe_endpoints_never_start(monkeypatch, entrypoint):
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('process started'))
    monkeypatch.setattr(socket, 'socket', lambda *a, **k: pytest.fail('network opened'))
    with pytest.raises(preflight.RuntimePreflightRefused, match='^runtime_process_preflight_refused$'):
        getattr(preflight, entrypoint)(enabled=True)
    assert not hasattr(preflight, '_GuardianNative')
    assert not hasattr(preflight, '_CandidatePipe')
    assert not hasattr(preflight, '_CandidateService')
    assert not hasattr(preflight, '_ManifestGuardian')
