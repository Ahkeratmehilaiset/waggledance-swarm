"""Synthetic runtime-child tests; never run a model or native executable."""
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import time
import threading
from types import SimpleNamespace

import pytest

from tools import wd_mind_interpreter_identity as identity
from tools import wd_mind_runtime_child as child
from tools import wd_mind_runtime_manifest as verifier
from tools import wd_mind_runtime_process as anchor
from tools import wd_mind_e5_client as e5_client
from test_wd_mind_runtime_process import candidate


def digest(data):
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def staged(candidate, tmp_path, monkeypatch):
    bundle = candidate["bundle_root"]
    source = Path(__file__).resolve().parents[2]
    files = {}
    for name in ("wd_mind_runtime_bootstrap.py", "wd_mind_runtime_manifest.py", "wd_mind_runtime_mutex.py", "wd_mind_runtime_process.py",
                 "wd_mind_runtime_child.py", "wd_mind_interpreter_identity.py"):
        relative = "tools-bootstrap/tools/" + name
        path = bundle / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((source / "tools" / name).read_bytes())
        files[relative] = digest(path.read_bytes())
    runtime_manifest = bundle / "wd-mind-runtime-manifest.json"
    runtime_manifest.write_bytes(candidate["runtime_manifest"].read_bytes())
    files[runtime_manifest.name] = digest(runtime_manifest.read_bytes())
    interpreter_root = tmp_path / "interpreter-tree"
    records = []
    for relative, role, raw in (("bin/python.exe", "interpreter", b"synthetic executable"),
                                ("lib/stdlib.zip", "stdlib", b"synthetic stdlib"),
                                ("dll/runtime.dll", "dll", b"synthetic DLL")):
        path = interpreter_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        records.append({"role": role, "path": relative, "size": len(raw), "sha256": digest(raw)})
    interpreter = interpreter_root / "bin/python.exe"
    descriptor = bundle / "wd-mind-interpreter-identity.json"
    descriptor.write_bytes(json.dumps({"schema": "wd.q4-interpreter-identity.v1", "files": records}).encode())
    files[descriptor.name] = digest(descriptor.read_bytes())
    deployment = bundle / "deployment-manifest.json"
    deployment.write_bytes(json.dumps({"schema_version": 1, "files": files}, sort_keys=True).encode())
    monkeypatch.setattr(child, "__file__", str(bundle / "tools-bootstrap/tools/wd_mind_runtime_child.py"))
    monkeypatch.setattr(anchor, "__file__", str(bundle / "tools-bootstrap/tools/wd_mind_runtime_process.py"))
    monkeypatch.setattr(identity, "__file__", str(bundle / "tools-bootstrap/tools/wd_mind_interpreter_identity.py"))
    monkeypatch.setattr(verifier, "__file__", str(bundle / "tools-bootstrap/tools/wd_mind_runtime_manifest.py"))
    args = {"runtime-root": str(candidate["runtime_root"]), "runtime-manifest": str(runtime_manifest),
            "runtime-manifest-sha256": digest(runtime_manifest.read_bytes()),
            "runtime-generation": candidate["generation"], "runtime-revision": candidate["revision"],
            "memory": str(tmp_path / "synthetic-memory"), "descriptor": str(tmp_path / "descriptor.json"),
            "descriptor-sha256": "b" * 64, "lock-file": str(tmp_path / "read.lock"), "lock-timeout": "1"}
    return {"bundle_root": bundle, "deployment_manifest_sha256": digest(deployment.read_bytes()),
            "interpreter": interpreter, "interpreter_sha256": digest(interpreter.read_bytes()),
            "interpreter_root": interpreter_root, "tuple_args": args,
            "query": b'{"situation":"synthetic-private","time":"2026-01-01T00:00:00Z"}',
            "timeout": 5, "scratch_root": None}




class SyntheticStdin:
    def __init__(self): self.contents = b""
    def write(self, data): self.contents += data
    def close(self): pass


class SyntheticProcess:
    def __init__(self, *, timeout=False):
        self.stdin = SyntheticStdin()
        self.stdout = io.BytesIO(b'{"schema":"wd.bridge-mind-q4-private-read.v1",'
                                 b'"identity":"caller_declared_not_authenticated",'
                                 b'"status":"REFUSED","reason":"reader_busy","results":[]}')
        self.returncode = 2
        self.timeout = timeout
        self.waits = 0
    def wait(self, timeout=None):
        self.waits += 1
        if self.timeout and self.waits == 1: raise subprocess.TimeoutExpired("synthetic", timeout)
        return self.returncode
    def poll(self): return self.returncode
    def kill(self): pytest.fail("unexpected kill")








@pytest.mark.skipif(os.name != "nt", reason="Windows Job API requires Windows")
def test_real_windows_job_object_creation_and_close_without_process():
    job = child._Job()
    assert job.handle
    job.close()
    assert not job.handle


@pytest.fixture
def audit_scope(tmp_path, monkeypatch):
    audit = tmp_path / ".codex-audit"
    scope = audit / "wd-mind-runtime"
    scratch = scope / "scratch"
    scratch.mkdir(parents=True)
    monkeypatch.setattr(child, "_AUDIT_ROOT", audit)
    return audit, scope / "q4-reader.lock", scratch


@pytest.mark.parametrize("case", ["lock_live", "lock_sibling", "lock_traversal", "scratch_live",
                                  "scratch_sibling", "scratch_traversal"])
def test_non_audit_or_traversal_write_targets_refuse_without_side_effects(audit_scope, tmp_path, case):
    audit, lock, _ = audit_scope
    scratch = None
    live = tmp_path / "live"
    live.mkdir()
    before = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*"))
    if case == "lock_live": lock = live / "q4-reader.lock"
    elif case == "lock_sibling": lock = audit / "other.lock"
    elif case == "lock_traversal": lock = audit / "wd-mind-runtime" / ".." / "q4-reader.lock"
    elif case == "scratch_live": scratch = live
    elif case == "scratch_sibling": scratch = audit
    else: scratch = audit / "wd-mind-runtime" / ".." / "scratch"
    with pytest.raises(child.LaunchRefused, match="^audit_write_scope_refused$"):
        child._validate_audit_write_scope(lock, scratch)
    assert before == sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*"))
    assert not any(live.iterdir())


@pytest.mark.parametrize("target", ["scope", "lock"])
def test_reparse_target_refuses_before_any_write(audit_scope, monkeypatch, target):
    _, lock, scratch = audit_scope
    original = child.os.lstat
    def fake_lstat(path, *args, **kwargs):
        if Path(path) == (scratch.parent if target == "scope" else lock):
            return SimpleNamespace(st_mode=(stat.S_IFDIR if target == "scope" else stat.S_IFREG),
                                   st_file_attributes=0x400, st_nlink=1)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(child.os, "lstat", fake_lstat)
    with pytest.raises(child.LaunchRefused, match="^audit_write_scope_refused$"):
        child._validate_audit_write_scope(lock, None)


def test_validated_audit_scope_does_not_create_file_or_scratch(audit_scope):
    _, lock, scratch = audit_scope
    child._require_safe_writer_scope(lock, None)
    assert not lock.exists() and not any(scratch.iterdir())




@pytest.fixture
def staged_e5(staged, monkeypatch):
    bundle = staged["bundle_root"]
    deployment = bundle / "deployment-manifest.json"
    manifest = json.loads(deployment.read_text())
    source = Path(__file__).resolve().parents[2]
    for name in ("wd_mind_e5_worker.py", "wd_mind_e5_client.py"):
        path = bundle / "tools-bootstrap/tools" / name
        path.write_bytes((source / "tools" / name).read_bytes())
        manifest["files"]["tools-bootstrap/tools/" + name] = digest(path.read_bytes())
    monkeypatch.setattr(e5_client, "__file__", str(bundle / "tools-bootstrap/tools/wd_mind_e5_client.py"))
    weights = b"synthetic weights; no model"
    weights_path = Path(staged["tuple_args"]["runtime-root"]) / "model/model.safetensors"
    weights_path.write_bytes(weights)
    runtime_path = bundle / "wd-mind-runtime-manifest.json"
    runtime = json.loads(runtime_path.read_text())
    runtime["files"].append({"path": "model/model.safetensors", "size": len(weights), "sha256": digest(weights)})
    runtime_path.write_text(json.dumps(runtime, sort_keys=True))
    staged["tuple_args"]["runtime-manifest-sha256"] = digest(runtime_path.read_bytes())
    manifest["files"][runtime_path.name] = digest(runtime_path.read_bytes())
    policy = {"truncate_chars": 2000, "normalize": True, "dtype": "float32", "dim": 384}
    binding = {"schema": "wd.mind-memory-encoder-binding.v1",
               "encoder": {"model": "intfloat/multilingual-e5-small", "revision": staged["tuple_args"]["runtime-revision"],
                           "weights_sha256": digest(weights)},
               "code_sha256": manifest["files"]["tools-bootstrap/tools/wd_mind_e5_worker.py"],
               "passage_policy": dict(policy, prefix="passage: "), "query_policy": dict(policy, prefix="query: ")}
    binding_path = bundle / "wd-mind-e5-encoder-binding.json"
    binding_path.write_text(json.dumps(binding, sort_keys=True))
    manifest["files"][binding_path.name] = digest(binding_path.read_bytes())
    deployment.write_text(json.dumps(manifest, sort_keys=True))
    staged["deployment_manifest_sha256"] = digest(deployment.read_bytes())
    return staged






@pytest.fixture
def staged_graceful(staged_e5, monkeypatch):
    from tools import wd_mind_e5_lifecycle as lifecycle
    bundle = staged_e5["bundle_root"]
    relative = "tools-bootstrap/tools/wd_mind_e5_lifecycle.py"
    source = Path(__file__).resolve().parents[2] / "tools/wd_mind_e5_lifecycle.py"
    path = bundle / relative
    path.write_bytes(source.read_bytes())
    deployment = bundle / "deployment-manifest.json"
    manifest = json.loads(deployment.read_bytes())
    manifest["files"][relative] = digest(path.read_bytes())
    deployment.write_text(json.dumps(manifest, sort_keys=True))
    staged_e5["deployment_manifest_sha256"] = digest(deployment.read_bytes())
    monkeypatch.setattr(lifecycle, "__file__", str(path))
    monkeypatch.setattr(child, "_require_safe_writer_scope", lambda *args: None)
    return {key: value for key, value in staged_e5.items() if key != "query"}


def test_bulk_worker_only_launch_is_pinned_and_preserves_owner(staged_graceful, monkeypatch):
    from test_wd_mind_e5_lifecycle import Job as OwnedJob, Process as OwnedProcess, finish
    from test_wd_mind_e5_client import response
    process = OwnedProcess(response(0, 0), on_eof=False)
    calls = {}
    class Job(OwnedJob):
        def __init__(self, *, kill_on_close, _owner=None):
            assert kill_on_close is False
            super().__init__()
        def attach(self, value): assert value is process
    def popen(argv, **kwargs):
        calls['argv'], calls['env'] = argv, kwargs['env']
        return process
    monkeypatch.setattr(child, '_Job', Job)
    monkeypatch.setattr(subprocess, 'Popen', popen)
    options = dict(staged_graceful)
    options['tuple_args'] = {k:v for k,v in options['tuple_args'].items()
                             if k not in ('memory','descriptor','descriptor-sha256')}
    result = child.start_e5_worker_graceful(enabled=True, worker_only=True,
                bulk_contract_sha256=child.BULK_CONTRACT_SHA256, **options)
    owner = result['owner']
    try:
        assert result['status'] == 'READY' and result['bulk_contract_sha256'] == child.BULK_CONTRACT_SHA256
        assert result['client']._bulk_contract_sha256 == owner._bulk_contract_sha256 == child.BULK_CONTRACT_SHA256
        assert '--worker-only' in calls['argv'] and '--bulk-contract-sha256' in calls['argv']
        assert all('--'+key not in calls['argv'] for key in ('memory','descriptor','descriptor-sha256'))
        assert not any('BULK' in key for key in calls['env'])
        assert result['client'].close(timeout=0.02)['state'] == 'HOLD'
    finally:
        finish(owner, process)


@pytest.mark.parametrize('fault', ['bad_contract', 'wrong_shape', 'worker_only_int'])
def test_bulk_worker_only_refuses_bad_contract_before_spawn(staged_graceful, monkeypatch, fault):
    monkeypatch.setattr(subprocess, 'Popen', lambda *a,**k: pytest.fail('spawn forbidden'))
    options = dict(staged_graceful)
    options['tuple_args'] = {k:v for k,v in options['tuple_args'].items()
                             if k not in ('memory','descriptor','descriptor-sha256')}
    flag = 1 if fault == 'worker_only_int' else True
    contract = '0'*64 if fault == 'bad_contract' else child.BULK_CONTRACT_SHA256
    if fault == 'wrong_shape': options['tuple_args']['memory'] = 'untrusted fake'
    with pytest.raises(child.LaunchRefused):
        child.start_e5_worker_graceful(enabled=True, worker_only=flag, bulk_contract_sha256=contract, **options)


@pytest.mark.parametrize('name',['wd_mind_e5_worker.py','wd_mind_runtime_bootstrap.py'])
def test_bulk_refuses_manifest_pinned_code_without_contract(staged_graceful, monkeypatch, name):
    monkeypatch.setattr(subprocess, 'Popen', lambda *a,**k: pytest.fail('spawn forbidden'))
    bundle=staged_graceful['bundle_root']
    path=bundle/'tools-bootstrap/tools'/name
    raw=path.read_bytes()
    assert child.BULK_CONTRACT in raw
    path.write_bytes(raw.replace(child.BULK_CONTRACT,b'not-the-bulk-contract'))
    manifest_path=bundle/'deployment-manifest.json'
    manifest=json.loads(manifest_path.read_bytes())
    manifest['files']['tools-bootstrap/tools/'+name]=digest(path.read_bytes())
    if name == 'wd_mind_e5_worker.py':
        binding_path=bundle/'wd-mind-e5-encoder-binding.json'
        binding=json.loads(binding_path.read_bytes())
        binding['code_sha256']=digest(path.read_bytes())
        binding_path.write_bytes(json.dumps(binding,sort_keys=True).encode())
        manifest['files'][binding_path.name]=digest(binding_path.read_bytes())
    manifest_path.write_bytes(json.dumps(manifest,sort_keys=True).encode())
    options=dict(staged_graceful, deployment_manifest_sha256=digest(manifest_path.read_bytes()))
    options['tuple_args']={k:v for k,v in options['tuple_args'].items()
                           if k not in ('memory','descriptor','descriptor-sha256')}
    with pytest.raises(child.LaunchRefused):
        child.start_e5_worker_graceful(enabled=True,worker_only=True,
            bulk_contract_sha256=child.BULK_CONTRACT_SHA256,**options)


def test_graceful_default_does_not_read_pins_or_create_job(staged_e5, monkeypatch):
    monkeypatch.setattr(anchor, "_read_pinned_file", lambda *a, **k: pytest.fail("read before opt in"))
    monkeypatch.setattr(child, "_Job", lambda *a, **k: pytest.fail("job before opt in"))
    with pytest.raises(child.LaunchRefused):
        child.start_e5_worker_graceful(**{k: v for k, v in staged_e5.items() if k != "query"})


@pytest.mark.parametrize("fault", ["missing_pin", "lifecycle", "origin", "worker", "binding", "weights", "bootstrap", "identity"])
def test_graceful_all_pins_before_spawn(staged_graceful, monkeypatch, fault):
    from tools import wd_mind_e5_lifecycle as lifecycle
    bundle = staged_graceful["bundle_root"]
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("process before pins"))
    if fault == "missing_pin":
        deployment = bundle / "deployment-manifest.json"
        manifest = json.loads(deployment.read_bytes())
        del manifest["files"]["tools-bootstrap/tools/wd_mind_e5_lifecycle.py"]
        deployment.write_text(json.dumps(manifest, sort_keys=True))
        staged_graceful["deployment_manifest_sha256"] = digest(deployment.read_bytes())
    elif fault == "origin":
        monkeypatch.setattr(lifecycle, "__file__", str(bundle / "wrong.py"))
    else:
        paths = {"lifecycle": bundle / "tools-bootstrap/tools/wd_mind_e5_lifecycle.py",
                 "worker": bundle / "tools-bootstrap/tools/wd_mind_e5_worker.py",
                 "binding": bundle / "wd-mind-e5-encoder-binding.json",
                 "weights": Path(staged_graceful["tuple_args"]["runtime-root"]) / "model/model.safetensors",
                 "bootstrap": bundle / "tools-bootstrap/tools/wd_mind_runtime_bootstrap.py",
                 "identity": bundle / "wd-mind-interpreter-identity.json"}
        paths[fault].write_bytes(b"tampered")
    with pytest.raises(child.LaunchRefused):
        child.start_e5_worker_graceful(enabled=True, **staged_graceful)


@pytest.mark.parametrize("fault", [None, "attach", "handshake", "late_spawn"])
def test_graceful_launch_retains_owner_on_post_spawn_failure(staged_graceful, monkeypatch, fault):
    from test_wd_mind_e5_lifecycle import Job as OwnedJob, Process as OwnedProcess, finish
    from test_wd_mind_e5_client import response
    calls = []
    process = OwnedProcess(response(0, 0) if fault != "handshake" else b"", on_eof=False)
    clock = [time.monotonic()]
    monkeypatch.setattr(child, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    class Job(OwnedJob):
        def __init__(self, *, kill_on_close, _owner=None):
            assert kill_on_close is False
            super().__init__()
            calls.append("job")

        def attach(self, value):
            assert value is process
            calls.append("attach")
            if fault == "attach":
                raise ValueError("synthetic private failure")

    def popen(args, **kwargs):
        calls.append("spawn")
        assert kwargs["stderr"] == subprocess.PIPE
        assert kwargs["env"]["HF_HUB_OFFLINE"] == "1"
        assert args[1:4] == ["-I", "-S", "-B"]
        if fault == "late_spawn":
            clock[0] += 2
        return process

    monkeypatch.setattr(child, "_Job", Job)
    monkeypatch.setattr(subprocess, "Popen", popen)
    staged_graceful["timeout"] = 0.03 if fault == "handshake" else 1
    result = child.start_e5_worker_graceful(enabled=True, **staged_graceful)
    owner = result["owner"]
    try:
        assert calls[:2] == ["job", "spawn"]
        assert owner._process is process and result["authority"] == "none"
        if fault is None:
            assert result["status"] == "READY" and process.written == b"\0"
            assert result["client"].close(timeout=0.02)["owned"]
        else:
            assert result["status"] == "HOLD" and result["client"] is None
            assert owner.status()["owned"] and not owner._job.closed
            if fault in ("attach", "late_spawn"):
                assert process.written == b""
    finally:
        finish(owner, process)


def test_graceful_expired_preflight_never_spawns(staged_graceful, monkeypatch):
    clock = [time.monotonic()]
    monkeypatch.setattr(child, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    original = anchor.verify_runtime_process_preflight

    def verify(**kwargs):
        original(**kwargs)
        clock[0] += 100

    monkeypatch.setattr(anchor, "verify_runtime_process_preflight", verify)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("late spawn"))
    with pytest.raises(child.LaunchRefused):
        child.start_e5_worker_graceful(enabled=True, **staged_graceful)


def test_graceful_spawn_failure_closes_only_unstarted_job(staged_graceful, monkeypatch):
    from test_wd_mind_e5_lifecycle import Job
    from tools import wd_mind_e5_lifecycle as lifecycle
    job = Job()
    monkeypatch.setattr(child, "_Job", lambda **kwargs: job)

    def unavailable(*args, **kwargs):
        raise OSError("private synthetic path")

    monkeypatch.setattr(subprocess, "Popen", unavailable)
    result = child.start_e5_worker_graceful(enabled=True, **staged_graceful)
    assert result["client"] is None and result["owner"]._job is job
    assert not result["owner"].shutdown(timeout=2)["owned"] and job.closed
    owner = lifecycle.reserve(enabled=True)
    owner.cancel_unstarted()


def test_cooperative_job_close_failure_retains_actual_handle():
    job = object.__new__(child._Job)
    job.handle, job.kill_on_close = 123, False
    job.api = SimpleNamespace(CloseHandle=lambda handle: 0)
    with pytest.raises(child.LaunchRefused, match="^job_close_failed$"):
        job.close()
    assert job.handle == 123


@pytest.mark.parametrize("registered", [False, True])
def test_review_bind_failure_keeps_live_process_owned(staged_graceful, monkeypatch, registered):
    from test_wd_mind_e5_lifecycle import Job, Process
    from tools import wd_mind_e5_lifecycle as lifecycle
    process, job = Process(on_eof=False), Job()
    monkeypatch.setattr(child, "_Job", lambda **kwargs: job)
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: process)
    original = lifecycle._Owner.bind

    def fail(owner, worker):
        if registered:
            original(owner, worker)
        raise RuntimeError("synthetic pre/post-bind failure")

    monkeypatch.setattr(lifecycle._Owner, "bind", fail)
    try:
        result = child.start_e5_worker_graceful(enabled=True, **staged_graceful)
        owner = result["owner"]
        assert result["status"] == "HOLD" and owner.status()["owned"]
        assert owner._process is process and owner._job is job
        assert process.poll() is None and not job.closed
        with pytest.raises(lifecycle.LifecycleRefused):
            lifecycle.reserve(enabled=True)
    finally:
        process.finish()
        if lifecycle._active is not None:
            assert not lifecycle._active.shutdown(timeout=2)["owned"]


@pytest.mark.parametrize("registered", [False, True])
def test_review_set_job_failure_retains_job_only_hold_channel(staged_graceful, monkeypatch, registered):
    from test_wd_mind_e5_lifecycle import Job
    from tools import wd_mind_e5_lifecycle as lifecycle
    job = Job()
    entered, unblock = threading.Event(), threading.Event()
    original_close, original_set = job.close, lifecycle._Owner.set_job

    def close():
        entered.set()
        unblock.wait()
        original_close()

    def fail(owner, supplied):
        if registered:
            original_set(owner, supplied)
        raise RuntimeError("synthetic pre/post-registration failure")

    job.close = close
    monkeypatch.setattr(child, "_Job", lambda **kwargs: job)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("spawn forbidden"))
    monkeypatch.setattr(lifecycle._Owner, "set_job", fail)
    results, errors = [], []

    def launch():
        try:
            results.append(child.start_e5_worker_graceful(enabled=True, **staged_graceful))
        except Exception as exc:
            errors.append(type(exc).__name__)

    caller = threading.Thread(target=launch, daemon=True)
    try:
        caller.start()
        caller.join(timeout=2)
        assert not caller.is_alive(), "registration failure blocked on Job.close"
        assert not errors, "registration failure lost the owner channel"
        result = results[0]
        assert result["status"] == "HOLD" and result["owner"]._job is job
        assert result["owner"].status()["pid"] is None
        assert entered.wait(2) and not job.closed
        with pytest.raises(lifecycle.LifecycleRefused):
            lifecycle.reserve(enabled=True)
    finally:
        unblock.set()
        caller.join(timeout=2)
        if lifecycle._active is not None:
            assert not lifecycle._active.shutdown(timeout=2)["owned"]


def test_review_spawn_and_job_close_failure_return_owner_without_raw_error(staged_graceful, monkeypatch):
    from test_wd_mind_e5_lifecycle import Job
    from tools import wd_mind_e5_lifecycle as lifecycle
    job, failed, allow_close = Job(), threading.Event(), threading.Event()

    def close():
        if not allow_close.is_set():
            failed.set()
            raise OSError("synthetic private error")
        job.closed = True

    def unavailable(*args, **kwargs):
        raise OSError("synthetic spawn failure")

    job.close = close
    monkeypatch.setattr(child, "_Job", lambda **kwargs: job)
    monkeypatch.setattr(subprocess, "Popen", unavailable)
    try:
        result = child.start_e5_worker_graceful(enabled=True, **staged_graceful)
        owner = result["owner"]
        assert result["status"] == "HOLD" and failed.wait(2)
        assert owner.status()["owned"] and owner._job is job and owner._process is None
        assert "private" not in repr(result["lifecycle"]) and "private" not in repr(owner.status())
        assert owner._keeper.is_alive() and not owner._keeper.daemon
        with pytest.raises(lifecycle.LifecycleRefused):
            lifecycle.reserve(enabled=True)
    finally:
        allow_close.set()
        if lifecycle._active is not None:
            assert not lifecycle._active.shutdown(timeout=2)["owned"]


def test_review_partial_job_constructor_failure_retains_created_handle(staged_graceful, monkeypatch):
    from tools import wd_mind_e5_lifecycle as lifecycle
    allow_close = threading.Event()

    class Call:
        def __init__(self, function):
            self.function = function

        def __call__(self, *args):
            return self.function(*args)

    api = SimpleNamespace(CreateJobObjectW=Call(lambda *args: 123),
                          SetInformationJobObject=Call(lambda *args: 0),
                          AssignProcessToJobObject=Call(lambda *args: pytest.fail("attach forbidden")),
                          TerminateJobObject=Call(lambda *args: pytest.fail("terminate forbidden")),
                          CloseHandle=Call(lambda *args: 1 if allow_close.is_set() else 0))
    native = SimpleNamespace(**{name: getattr(child.ctypes, name) for name in ("c_void_p", "c_int", "byref", "sizeof")},
                             WinDLL=lambda *a, **k: api)
    monkeypatch.setattr(child, "ctypes", native)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("spawn forbidden"))
    try:
        result = child.start_e5_worker_graceful(enabled=True, **staged_graceful)
        owner = result["owner"]
        assert result["status"] == "HOLD" and owner.status()["owned"]
        assert owner._job is not None and owner._job.handle == 123
        assert owner._process is None
    finally:
        allow_close.set()
        if lifecycle._active is not None:
            assert not lifecycle._active.shutdown(timeout=2)["owned"]


@pytest.mark.parametrize('signal_name', ['keyboard', 'cancelled'])
def test_graceful_launch_preserves_owner_after_spawn_interruption(staged_graceful, monkeypatch, signal_name):
    import asyncio
    from test_wd_mind_e5_lifecycle import Job as OwnedJob, Process as OwnedProcess, finish
    from tools import wd_mind_e5_lifecycle as lifecycle
    signal = KeyboardInterrupt if signal_name == 'keyboard' else asyncio.CancelledError
    process, captured, calls = OwnedProcess(on_eof=False), [], []
    class Job(OwnedJob):
        def __init__(self, *, kill_on_close, _owner=None):
            assert kill_on_close is False
            super().__init__()
        def attach(self, value):
            assert value is process
            assert captured and captured[0]._process is process
            raise signal()
    def spawn(*args, **kwargs):
        assert len(captured) == 1 and captured[0]._process is None
        calls.append('spawn')
        return process
    monkeypatch.setattr(child, '_Job', Job)
    monkeypatch.setattr(subprocess, 'Popen', spawn)
    try:
        with pytest.raises(signal):
            child.start_e5_worker_graceful(enabled=True, owner_sink=captured.append, **staged_graceful)
        assert calls == ['spawn'] and captured[0] is lifecycle._active
        assert captured[0]._process is process and captured[0].status()['owned'] is True
        assert process.poll() is None and not captured[0]._job.closed
    finally:
        if captured:
            finish(captured[0], process)


def test_single_owner_caller_interrupt_keeps_real_launch_owner_during_spawn(staged_graceful, monkeypatch, tmp_path):
    from test_wd_mind_e5_lifecycle import Job as OwnedJob, Process as OwnedProcess, finish
    from test_wd_mind_e5_client import response
    from tools import wd_mind_e5_client, wd_mind_full_runtime as runtime
    process = OwnedProcess(response(0, 0), on_eof=False)
    entered, release = threading.Event(), threading.Event()
    class Job(OwnedJob):
        def __init__(self, *, kill_on_close, _owner=None):
            assert kill_on_close is False
            super().__init__()
        def attach(self, value): assert value is process
    def spawn(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return process
    monkeypatch.setattr(child, '_Job', Job)
    monkeypatch.setattr(subprocess, 'Popen', spawn)
    fleet = json.dumps({'lanes':[{'agent':'codex-lead-1'}],
                        'tools_supervisor':{'agent':'codex-tools-1'}}, sort_keys=True).encode()
    snapshot = {'projection_sha256':'b'*64}
    binding = runtime._PrivateBinding(snapshot, tmp_path, 'a'*64, (fleet,), {})
    factory = runtime._ManifestWorkerFactory(str(tmp_path), 'a'*64, {}, tmp_path/'lock', 5, 0,
                                             wd_mind_e5_client.BULK_CONTRACT_SHA256)
    monkeypatch.setattr(runtime._PrivateBinding, 'snapshot_for_query', lambda self: snapshot)
    monkeypatch.setattr(runtime, '_flat_snapshot', lambda candidate, frozen: frozen)
    def launch(self, *, owner_sink):
        return child.start_e5_worker_graceful(enabled=True, owner_sink=owner_sink, **staged_graceful)
    def interrupt(self):
        assert entered.wait(2)
        raise KeyboardInterrupt()
    monkeypatch.setattr(runtime._ManifestWorkerFactory, '__call__', launch)
    monkeypatch.setattr(runtime._SingleManifestOwner, '_wait_for_launch', interrupt)
    owner = runtime.create_single_manifest_owner(enabled=True, binding=binding,
        lane='codex-lead-1', worker_factory=factory, shutdown_timeout=0)
    try:
        with pytest.raises(KeyboardInterrupt): owner.start()
        held = owner._owner
        assert held is not None and held.status()['owned'] is True
        assert owner.status() == {'state':'HOLD', 'owned':True, 'authority':'none'}
        assert owner.close() == {'state':'HOLD', 'owned':True, 'authority':'none'}
        release.set()
        owner._launch_thread.join(timeout=5)
        assert not owner._launch_thread.is_alive()
        assert held._process is process and process.poll() is None
        assert owner.status() == {'state':'HOLD', 'owned':True, 'authority':'none'}
    finally:
        release.set()
        if owner._launch_thread is not None: owner._launch_thread.join(timeout=5)
        if owner._owner is not None: finish(owner._owner, process)


@pytest.mark.skipif(os.name != 'nt', reason='Windows handle inheritance test')
def test_windows_two_synthetic_children_receive_separate_bootstrap_channels():
    """No model, secrets in argv/env, files, shell, or non-test processes."""
    import hmac, secrets, sys
    channel_key = secrets.token_bytes(32)
    owner_read, owner_write = os.pipe()
    consumer_read, consumer_write = os.pipe()
    data_read, data_write = os.pipe()
    fds = {owner_read, owner_write, consumer_read, consumer_write, data_read, data_write}
    owner = consumer = None
    owner_code = r'''
import hashlib,hmac,sys
key = sys.stdin.buffer.read(32)
assert len(key) == 32
sys.stdout.buffer.write(hmac.new(key,b'synthetic-data',hashlib.sha256).digest())
sys.stdout.buffer.flush()
'''
    consumer_code = r'''
import ctypes,hashlib,hmac,sys
kernel=ctypes.WinDLL('kernel32',use_last_error=True)
kernel.GetStdHandle.argtypes=[ctypes.c_ulong]
kernel.GetStdHandle.restype=ctypes.c_void_p
kernel.ReadFile.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_ulong,ctypes.POINTER(ctypes.c_ulong),ctypes.c_void_p]
kernel.ReadFile.restype=ctypes.c_int
handle=kernel.GetStdHandle((-12) & 0xffffffff)
buffer=ctypes.create_string_buffer(32)
received=ctypes.c_ulong()
assert kernel.ReadFile(handle,buffer,32,ctypes.byref(received),None) and received.value == 32
key=buffer.raw
actual=sys.stdin.buffer.read(32)
assert len(actual)==32 and hmac.compare_digest(actual,hmac.new(key,b'synthetic-data',hashlib.sha256).digest())
sys.stdout.buffer.write(b'synthetic-ok\n')
sys.stdout.buffer.flush()
'''
    try:
        owner = subprocess.Popen([sys.executable, '-I', '-S', '-B', '-c', owner_code],
            stdin=owner_read, stdout=data_write, stderr=subprocess.DEVNULL,
            close_fds=True, creationflags=subprocess.CREATE_NO_WINDOW)
        consumer = subprocess.Popen([sys.executable, '-I', '-S', '-B', '-c', consumer_code],
            stdin=data_read, stdout=subprocess.PIPE, stderr=consumer_read,
            close_fds=True, creationflags=subprocess.CREATE_NO_WINDOW)
        for descriptor in (owner_read, data_write, data_read, consumer_read):
            os.close(descriptor)
            fds.remove(descriptor)
        assert os.write(owner_write, channel_key) == 32
        assert os.write(consumer_write, channel_key) == 32
        os.close(owner_write); fds.remove(owner_write)
        os.close(consumer_write); fds.remove(consumer_write)
        out, _ = consumer.communicate(timeout=10)
        assert consumer.returncode == 0 and out == b'synthetic-ok\n'
        assert owner.wait(timeout=10) == 0
    finally:
        for descriptor in fds: os.close(descriptor)
        for process in (owner, consumer):
            if process is not None and process.poll() is None:
                process.wait(timeout=5)


def test_parent_child_spawn_default_refuses_before_command_or_process(monkeypatch):
    monkeypatch.setattr(child, '_verified_parent_child_command', lambda **_: pytest.fail('command resolution forbidden'))
    with pytest.raises(child.LaunchRefused):
        child.create_parent_child_launch(enabled=True, role='owner', bundle_root='C:\\synthetic',
            expected_manifest_sha256='a'*64, stdin_fd=0, stdout_fd=1, stderr_fd=2,
            deadline_monotonic=time.monotonic()+5)


def test_parent_child_spawn_interrupted_constructor_retains_unknown_owner(monkeypatch):
    import threading
    created = []
    class InterruptedPopen:
        def __new__(cls, *args, **kwargs): return object.__new__(cls)
        def __init__(self, *args, **kwargs):
            created.append(self)
            raise KeyboardInterrupt()
    monkeypatch.setattr(child, '_require_verified_parent_spawn', lambda: None)
    monkeypatch.setattr(child, '_verified_parent_child_command', lambda **_: ('synthetic-interpreter',))
    monkeypatch.setattr(child, '_validate_parent_pipe_fds', lambda *_: None)
    monkeypatch.setattr(child.subprocess, 'Popen', InterruptedPopen)
    launch = child.create_parent_child_launch(enabled=True, role='owner', bundle_root='C:\\synthetic',
        expected_manifest_sha256='a'*64, stdin_fd=0, stdout_fd=1, stderr_fd=2,
        deadline_monotonic=time.monotonic()+5)
    launch.start()
    assert launch._done.wait(5)
    launch._thread.join(timeout=5)
    assert len(created) == 1 and launch._process is created[0]
    assert launch.status() == {'state':'HOLD', 'owned':None, 'worker_active':False}
    with pytest.raises(child.LaunchRefused): launch.close()


def test_parent_child_interrupted_thread_start_remains_hold_after_late_spawn(monkeypatch):
    actual_thread = threading.Thread
    class InterruptedStartThread(actual_thread):
        def start(self):
            super().start()
            raise KeyboardInterrupt()
    class SyntheticPopen:
        def __new__(cls, *args, **kwargs): return object.__new__(cls)
        def __init__(self, *args, **kwargs): pass
        def poll(self): return None
    monkeypatch.setattr(child, '_require_verified_parent_spawn', lambda: None)
    monkeypatch.setattr(child, '_verified_parent_child_command', lambda **_: ('synthetic-interpreter',))
    monkeypatch.setattr(child, '_validate_parent_pipe_fds', lambda *_: None)
    monkeypatch.setattr(child.subprocess, 'Popen', SyntheticPopen)
    monkeypatch.setattr(child.threading, 'Thread', InterruptedStartThread)
    launch = child.create_parent_child_launch(enabled=True, role='owner', bundle_root='C:\\synthetic',
        expected_manifest_sha256='a'*64, stdin_fd=0, stdout_fd=1, stderr_fd=2,
        deadline_monotonic=time.monotonic()+5)
    with pytest.raises(KeyboardInterrupt): launch.start()
    launch._thread.join(timeout=5)
    assert launch.status() == {'state':'HOLD', 'owned':True, 'worker_active':False}
    with pytest.raises(child.LaunchRefused): launch.close()


def test_parent_child_late_constructor_cannot_self_adopt_after_start_cancellation(monkeypatch):
    import inspect, sys
    entered, release = threading.Event(), threading.Event()
    class DelayedPopen:
        def __new__(cls, *args, **kwargs): return object.__new__(cls)
        def __init__(self, *args, **kwargs): entered.set(); release.wait(5)
        def poll(self): return None
    monkeypatch.setattr(child, '_require_verified_parent_spawn', lambda: None)
    monkeypatch.setattr(child, '_verified_parent_child_command', lambda **_: ('synthetic-interpreter',))
    monkeypatch.setattr(child, '_validate_parent_pipe_fds', lambda *_: None)
    monkeypatch.setattr(child.subprocess, 'Popen', DelayedPopen)
    launch = child.create_parent_child_launch(enabled=True, role='owner', bundle_root='C:\\synthetic',
        expected_manifest_sha256='a'*64, stdin_fd=0, stdout_fd=1, stderr_fd=2,
        deadline_monotonic=time.monotonic()+5)
    lines, first = inspect.getsourcelines(child._ParentChildLaunch.start)
    target = first + next(i for i, line in enumerate(lines) if line.strip() == 'if cancelled_type is not None:')
    def trace(frame, event, arg):
        if frame.f_code is child._ParentChildLaunch.start.__code__ and event == 'line' and frame.f_lineno == target:
            raise KeyboardInterrupt()
        return trace
    sys.settrace(trace)
    try:
        with pytest.raises(KeyboardInterrupt): launch.start()
    finally:
        sys.settrace(None)
        release.set()
    launch._thread.join(timeout=5)
    assert entered.is_set()
    assert launch.status() == {'state':'HOLD', 'owned':True, 'worker_active':False}


@pytest.mark.skipif(os.name != 'nt', reason='Windows handle inheritance test')
def test_windows_handle_list_excludes_unrelated_inheritable_handle():
    """Probe in an isolated synthetic parent, without mutating fleet handles."""
    import sys
    probe = r'''
import ctypes,msvcrt,os,struct,subprocess,sys
read_fd,write_fd=os.pipe()
handle=msvcrt.get_osfhandle(read_fd)
os.set_handle_inheritable(handle,True)
child=r"""
import ctypes,struct,sys
value=struct.unpack('<Q',sys.stdin.buffer.read(8))[0]
kernel=ctypes.WinDLL('kernel32',use_last_error=True)
kernel.GetFileType.argtypes=[ctypes.c_void_p]
kernel.GetFileType.restype=ctypes.c_ulong
kind=kernel.GetFileType(ctypes.c_void_p(value))
error=ctypes.get_last_error()
assert kind==0 and error==6
sys.stdout.write('excluded\\n')
"""
try:
    worker=subprocess.Popen([sys.executable,'-I','-S','-B','-c',child],stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE,stderr=subprocess.PIPE,close_fds=True,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    result,error=worker.communicate(struct.pack('<Q',handle),timeout=10)
    assert worker.returncode==0 and result==b'excluded\\n'
    print('isolated-handle-exclusion-ok')
finally:
    os.close(read_fd)
    os.close(write_fd)
'''
    result = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', probe],
        capture_output=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode == 0, 'isolated handle exclusion probe failed'
    assert result.stdout.strip() == b'isolated-handle-exclusion-ok'


def test_export_frame_codec_is_default_off_bounded_and_keyed():
    import hmac
    key, nonce, payload = b'k' * 32, b'n' * 32, b'synthetic-payload'
    with pytest.raises(child.LaunchRefused):
        child._encode_export_frame(channel_key=key, nonce=nonce, kind=b'M', sequence=0, payload=payload)
    frame = child._encode_export_frame(enabled=True, channel_key=key, nonce=nonce, kind=b'M', sequence=0, payload=payload)
    header = struct.pack('>4sB3xII', b'MEX1', ord('M'), 0, len(payload))
    tag = hmac.new(key, b'wd.mind-export-frame.v1\0' + nonce + header + payload, hashlib.sha256).digest()
    assert frame == header + payload + tag and len(frame) <= 65536
    for change in ({'sequence': True}, {'kind':b'D','sequence':0}, {'payload':b'x'*65489},
                   {'channel_key':b'x'}, {'nonce':b'x'}, {'kind':b'X'}):
        values = dict(enabled=True, channel_key=key, nonce=nonce, kind=b'M', sequence=0, payload=payload)
        values.update(change)
        with pytest.raises(child.LaunchRefused): child._encode_export_frame(**values)


def test_export_frame_refusal_never_exposes_keys_in_traceback():
    import traceback
    channel_key, payload = b'k'*32, b'synthetic-private-payload'
    with pytest.raises(child.LaunchRefused) as raised:
        child._encode_export_frame(enabled=True, channel_key=channel_key, nonce=b'n'*32,
                                   kind=b'D', sequence=0, payload=payload)
    trace = traceback.TracebackException.from_exception(raised.value, capture_locals=True)
    frames = [frame for frame in trace.stack if frame.name == '_encode_export_frame']
    assert frames
    assert all(repr(secret) not in value for secret in (channel_key, payload)
               for frame in frames for value in (frame.locals or {}).values())


def test_export_wire_budget_counts_metadata_all_blocks_headers_and_final():
    lengths = (1, 65488, 65489, 0, 32, 100, 200, 300, 400, 500, 600, 700)
    data_frames = sum((length + 65487) // 65488 for length in lengths)
    expected = 123 + sum(lengths) + 48 * (1 + data_frames + 1) + 44
    assert child._export_wire_size(enabled=True, metadata_length=123, block_lengths=lengths,
                                   max_wire_bytes=expected+1) == expected
    with pytest.raises(child.LaunchRefused):
        child._export_wire_size(enabled=True, metadata_length=123, block_lengths=lengths,
                                max_wire_bytes=expected)
    for bad in ((), lengths[:-1], (True,) + lengths[1:], (-1,) + lengths[1:]):
        with pytest.raises(child.LaunchRefused):
            child._export_wire_size(enabled=True, metadata_length=123, block_lengths=bad,
                                    max_wire_bytes=2**63-1)


@pytest.mark.skipif(os.name != 'nt', reason='Windows pipe cancellation')
def test_cancelable_pipe_read_interrupts_blocked_native_read_without_termination():
    import msvcrt
    read_fd, write_fd = os.pipe()
    reader = None
    try:
        reader = child.create_cancelable_pipe_reader(enabled=True, handle=msvcrt.get_osfhandle(read_fd), deadline_monotonic=time.monotonic()+0.2)
        with pytest.raises(child.LaunchRefused): reader.read(1)
        status = reader.status()
        assert status['state'] == 'HOLD' and status['worker_active'] is False
        assert status['cancellation'] == 'CONFIRMED'
    finally:
        if reader is not None: reader.close()
        os.close(write_fd)
        os.close(read_fd)


@pytest.mark.skipif(os.name != 'nt', reason='Windows pipe cancellation')
def test_failed_pipe_cancellation_retains_live_thread_and_handle(monkeypatch):
    import msvcrt
    read_fd, write_fd = os.pipe()
    reader = None
    try:
        reader = child.create_cancelable_pipe_reader(enabled=True, handle=msvcrt.get_osfhandle(read_fd), deadline_monotonic=time.monotonic()+0.05)
        monkeypatch.setattr(child._CancelablePipeReader, '_cancel_worker', lambda self: False)
        with pytest.raises(child.LaunchRefused): reader.read(1)
        status = reader.status()
        assert status == {'state':'HOLD','worker_active':True,'cancellation':'UNCONFIRMED'}
        with pytest.raises(child.LaunchRefused): reader.close()
        assert reader.status()['worker_active'] is True
        assert os.write(write_fd,b'x') == 1
        reader._thread.join(timeout=2)
        assert not reader._thread.is_alive() and reader._result is None
        assert reader.status()['state'] == 'HOLD'
        reader.close()
        assert reader.status()['state'] == 'DETACHED'
    finally:
        os.close(write_fd)
        os.close(read_fd)


@pytest.mark.skipif(os.name != 'nt', reason='Windows pipe cancellation')
def test_cancelable_pipe_reader_reads_bytes_and_borrows_original_handle():
    import msvcrt
    read_fd, write_fd = os.pipe()
    reader = None
    try:
        reader = child.create_cancelable_pipe_reader(enabled=True, handle=msvcrt.get_osfhandle(read_fd), deadline_monotonic=time.monotonic()+2)
        assert os.write(write_fd, b'abc') == 3
        assert reader.read(3) == b'abc'
        os.close(write_fd)
        write_fd = None
        assert reader.read(1) == b''
        reader.close()
        assert reader.status()['state'] == 'DETACHED'
        assert msvcrt.get_osfhandle(read_fd) != -1
    finally:
        if write_fd is not None: os.close(write_fd)
        os.close(read_fd)


@pytest.mark.skipif(os.name != 'nt', reason='Windows pipe cancellation')
def test_cancelable_pipe_reader_uses_one_absolute_deadline_across_reads():
    import msvcrt
    read_fd, write_fd = os.pipe()
    reader = None
    try:
        deadline = time.monotonic() + 0.2
        reader = child.create_cancelable_pipe_reader(enabled=True, handle=msvcrt.get_osfhandle(read_fd),
                                                      deadline_monotonic=deadline)
        assert os.write(write_fd, b'a') == 1 and reader.read(1) == b'a'
        time.sleep(max(0, deadline - time.monotonic()) + 0.05)
        before = time.monotonic()
        with pytest.raises(child.LaunchRefused): reader.read(1)
        assert time.monotonic() - before < 0.1
        assert reader.status()['state'] == 'HOLD' and reader.status()['worker_active'] is False
    finally:
        if reader is not None: reader.close()
        os.close(write_fd)
        os.close(read_fd)


@pytest.mark.skipif(os.name != 'nt', reason='Windows pipe cancellation')
def test_interrupted_thread_start_never_detaches_unresolved_worker(monkeypatch):
    import msvcrt
    read_fd, write_fd = os.pipe()
    class DelayedStart:
        def __init__(self, *args, **kwargs): self.finished = False
        def start(self): raise KeyboardInterrupt()
        def is_alive(self): return False
        def join(self, timeout=None):
            if not self.finished: raise RuntimeError('start unresolved')
    reader = child.create_cancelable_pipe_reader(enabled=True, handle=msvcrt.get_osfhandle(read_fd),
                                                  deadline_monotonic=time.monotonic()+2)
    monkeypatch.setattr(child.threading, 'Thread', DelayedStart)
    try:
        with pytest.raises(KeyboardInterrupt): reader.read(1)
        assert reader.status()['state'] == 'HOLD' and reader.status()['worker_active'] is None
        with pytest.raises(child.LaunchRefused): reader.close()
        reader._thread.finished = True
        reader._done.set()
        reader.close()
        assert reader.status()['state'] == 'DETACHED'
    finally:
        os.close(write_fd)
        os.close(read_fd)


@pytest.mark.skipif(os.name != 'nt', reason='Windows pipe cancellation')
def test_pipe_cancel_uses_retained_thread_handle_not_recycled_native_id(monkeypatch):
    import msvcrt
    read_fd, write_fd = os.pipe()
    original = child._CancelablePipeReader._cancel_worker
    observed = []
    def cancel(reader):
        observed.append(reader._thread_handle.value)
        reader._thread._native_id = 0x7fffffff  # Synthetic stale/reused identity value.
        return original(reader)
    monkeypatch.setattr(child._CancelablePipeReader, '_cancel_worker', cancel)
    reader = child.create_cancelable_pipe_reader(enabled=True, handle=msvcrt.get_osfhandle(read_fd),
                                                  deadline_monotonic=time.monotonic()+0.2)
    try:
        with pytest.raises(child.LaunchRefused): reader.read(1)
        assert observed and observed[0] is not None
        assert reader.status() == {'state':'HOLD','worker_active':False,'cancellation':'CONFIRMED'}
        reader.close()
    finally:
        os.close(write_fd)
        os.close(read_fd)


def test_source_gate_timeout_retains_late_worker_and_discards_result():
    started, release = threading.Event(), threading.Event()
    def blocked_gate():
        started.set()
        assert release.wait(5)
        return object()
    holder = child.create_retained_source_gate_call(enabled=True, callback=blocked_gate,
                                                     deadline_monotonic=time.monotonic()+0.1)
    try:
        holder.start()
        assert started.wait(1)
        with pytest.raises(child.LaunchRefused): holder.wait()
        assert holder.status() == {'state':'HOLD', 'worker_active':True}
        with pytest.raises(child.LaunchRefused): holder.close()
        release.set()
        holder._thread.join(timeout=2)
        assert not holder._thread.is_alive()
        with pytest.raises(child.LaunchRefused): holder.wait()
        assert holder._result is None and holder.status()['state'] == 'HOLD'
        holder.close()
        assert holder.status()['state'] == 'DETACHED'
    finally:
        release.set()
        if holder._thread is not None: holder._thread.join(timeout=2)


def test_source_gate_success_is_one_shot_and_default_off():
    value = object()
    with pytest.raises(child.LaunchRefused):
        child.create_retained_source_gate_call(callback=lambda: value, deadline_monotonic=time.monotonic()+2)
    holder = child.create_retained_source_gate_call(enabled=True, callback=lambda: value,
                                                     deadline_monotonic=time.monotonic()+2)
    holder.start()
    assert holder.wait() is value and holder.status() == {'state':'SUCCEEDED','worker_active':False}
    with pytest.raises(child.LaunchRefused): holder.wait()
    holder.close()
    assert holder.status()['state'] == 'DETACHED'


def test_source_gate_interrupted_start_never_releases_uncertain_worker(monkeypatch):
    class DelayedStart:
        def __init__(self, *args, **kwargs): self.finished = False
        def start(self): raise KeyboardInterrupt()
        def is_alive(self): return False
        def join(self, timeout=None):
            if not self.finished: raise RuntimeError('unresolved')
    holder = child.create_retained_source_gate_call(enabled=True, callback=lambda: object(),
                                                     deadline_monotonic=time.monotonic()+2)
    monkeypatch.setattr(child.threading, 'Thread', DelayedStart)
    with pytest.raises(KeyboardInterrupt): holder.start()
    assert holder.status() == {'state':'HOLD','worker_active':None}
    with pytest.raises(child.LaunchRefused): holder.close()
    holder._thread.finished = True
    holder._done.set()
    holder.close()
    assert holder.status()['state'] == 'DETACHED'


def test_source_gate_factory_refusal_scrubs_callback_from_traceback():
    import traceback
    fake_secret = 'synthetic-private-callback-value'
    class PrivateCallback:
        def __call__(self): return None
        def __repr__(self): return fake_secret
    with pytest.raises(child.LaunchRefused) as raised:
        child.create_retained_source_gate_call(enabled=True, callback=PrivateCallback(), deadline_monotonic=False)
    trace = traceback.TracebackException.from_exception(raised.value, capture_locals=True)
    frames = [frame for frame in trace.stack if frame.name == 'create_retained_source_gate_call']
    assert frames and all(fake_secret not in value for frame in frames for value in (frame.locals or {}).values())


@pytest.mark.parametrize('signal_name', ['keyboard','cancelled','generator'])
def test_source_gate_worker_propagates_only_sanitized_builtin_cancellation(signal_name):
    import asyncio, traceback
    base = {'keyboard':KeyboardInterrupt, 'cancelled':asyncio.CancelledError, 'generator':GeneratorExit}[signal_name]
    fake_secret = b'synthetic-worker-only-secret'
    class UntrustedSignal(base):
        def __setattr__(self, name, value):
            if name in ('__traceback__','__context__','__cause__'):
                raise RuntimeError('synthetic private failure')
            return super().__setattr__(name, value)
    event = UntrustedSignal(fake_secret)
    def gate():
        private = fake_secret
        raise event
    holder = child.create_retained_source_gate_call(enabled=True, callback=gate,
                                                     deadline_monotonic=time.monotonic()+2)
    holder.start()
    with pytest.raises(base) as raised: holder.wait()
    assert type(raised.value) is base and raised.value.args == () and raised.value.__context__ is None
    trace = traceback.TracebackException.from_exception(raised.value, capture_locals=True)
    frames = [frame for frame in trace.stack if frame.name != 'test_source_gate_worker_propagates_only_sanitized_builtin_cancellation']
    assert all(repr(fake_secret) not in value for frame in frames for value in (frame.locals or {}).values())
    assert holder.status() == {'state':'HOLD','worker_active':False}
    holder.close()


def test_deferred_historical_launch_never_spawns_or_terminates(staged, staged_e5, monkeypatch):
    calls = []
    monkeypatch.setattr(child, '_require_safe_writer_scope', lambda *args: None)
    def spawn(*args, **kwargs):
        calls.append('spawn')
        raise OSError('synthetic')
    monkeypatch.setattr(subprocess, 'Popen', spawn)
    for fn, options in ((child.launch, staged), (child.start_e5_worker, {k:v for k,v in staged_e5.items() if k != 'query'})):
        with pytest.raises(child.LaunchRefused): fn(**options)
    assert calls == []


def test_job_refuses_kill_on_close_before_native_calls(monkeypatch):
    monkeypatch.setattr(child.ctypes, 'WinDLL', lambda *args, **kwargs: pytest.fail('native call forbidden'))
    with pytest.raises(child.LaunchRefused): child._Job(kill_on_close=True)
    assert not hasattr(child._Job, 'terminate')
