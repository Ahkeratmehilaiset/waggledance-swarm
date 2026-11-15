"""Synthetic-only runtime-process preflight tests; no real model is loaded."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest

from tools import wd_mind_runtime_process as preflight


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def test_pipe_admits_a_bounded_full_source_query_deadline():
    # One observed complete source scan took 24.259 seconds on this host.
    # Its transport deadline must not be constrained to fifteen seconds.
    assert preflight._pipe_deadline(330) > preflight.time.monotonic()
    with pytest.raises(ValueError):
        preflight._pipe_deadline(361)


def test_callback_allows_synthetic_full_source_scan_without_losing_ownership(monkeypatch):
    import threading
    import time
    import types

    # Accelerate the service clock 50x: 24.259 simulated seconds finish in
    # under a second, without touching the real bridge or waiting for a model.
    start = time.monotonic()
    fake_time = types.SimpleNamespace(monotonic=lambda: (time.monotonic() - start) * 50,
                                      sleep=time.sleep)
    monkeypatch.setattr(preflight, 'time', fake_time)
    release = threading.Event()
    threads = []
    def handler(_request):
        threads.append(threading.current_thread())
        release.wait(24.259 / 50)
        return []
    service = preflight._CandidateService(None, None, handler, 5, 5,
                                         preflight._CANDIDATE_CALLBACK_TIMEOUT_SECONDS)
    request = {'schema':'wd.mind-pipe-request.v1', 'query_id':'startup_work_overview',
               'nonce':'a'*64, 'lane':'codex-lead-1', 'scope':'own'}
    try:
        assert service._call(request) == []
        assert service._callback is None
        assert fake_time.monotonic() >= 24.259
    finally:
        release.set()
        for thread in threads:
            thread.join(2)
            assert not thread.is_alive()


@pytest.mark.parametrize('seconds,allowed', [(14, False), (15, True), (600, True), (601, False), (True, False)])
def test_guardian_fast_release_accepts_only_bounded_manifest_deadlines(seconds, allowed):
    config = {'schema':'wd.mind-startup-guardian.v2', 'enabled':True,
              'entrypoint':'tools/wd_mind_runtime_process.py', 'ready_timeout_seconds':seconds,
              'fast_build':True}
    if allowed:
        assert preflight._guardian_fast_build({'schema_version':1, 'mind_startup_guardian':config}) is True
    else:
        with pytest.raises(ValueError):
            preflight._guardian_fast_build({'schema_version':1, 'mind_startup_guardian':config})


@pytest.mark.parametrize('field,seconds,accepted', [
    ('receive_timeout', 15, True), ('receive_timeout', 15.001, False),
    ('reply_timeout', 15, True), ('reply_timeout', 15.001, False),
    ('callback_timeout', 300, True), ('callback_timeout', 360, True),
    ('callback_timeout', 360.001, False),
    ('receive_timeout', True, False), ('reply_timeout', float('nan'), False),
])
def test_service_io_deadlines_are_short_but_callback_has_its_own_limit(monkeypatch, field, seconds, accepted):
    monkeypatch.setattr(preflight, '_guardian_controls', lambda *a, **kw: None)
    options = {'receive_timeout': 5, 'reply_timeout': 5, 'callback_timeout': 5}
    options[field] = seconds
    kwargs = dict(enabled=True, bundle_root='synthetic', deployment_manifest_sha256='a' * 64,
                  handler=lambda request: [], **options)
    if not accepted:
        try:
            unexpected = preflight.create_candidate_service(**kwargs)
        except preflight.CandidateServiceHold:
            unexpected = None
        if unexpected is not None:
            unexpected.stop(timeout=0)
            pytest.fail('service accepted an out-of-range deadline')
        assert preflight._SERVICE_OWNERS == set()
        return
    service = preflight.create_candidate_service(**kwargs)
    try:
        assert service.status()['state'] == 'NEW'
    finally:
        assert service.stop(timeout=0)['state'] == 'STOPPED'
    assert preflight._SERVICE_OWNERS == set()


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


def test_guardian_has_one_explicit_entrypoint_in_packaged_bridge_definition():
    root = Path(__file__).resolve().parents[2]
    definition = json.loads((root / "ops/windows/reboot/bridge-code-files.json").read_bytes())
    path = "tools/wd_mind_runtime_process.py"
    assert definition["python_files"].count(path) == 1
    assert definition["import_smoke"]["package_modules"].count("tools.wd_mind_runtime_process") == 1
    assert list(definition["python_entrypoints"].values()).count(path) == 1
    assert definition["python_entrypoints"]["mind_guardian"] == path


@pytest.fixture
def guardian_candidate(tmp_path, monkeypatch):
    # --basetemp must be a persistent synthetic C: audit directory for these
    # native tests. No live audit directory, bundle, model or process is used.
    audit = Path(r"C:\Python\project2\.codex-audit")
    if os.name != "nt" or not tmp_path.is_relative_to(audit):
        pytest.skip("native marker fixtures require a persistent C: audit basetemp")
    bundle = tmp_path / "bundle"
    module_path = bundle / "tools-bootstrap/tools/wd_mind_runtime_process.py"
    module_path.parent.mkdir(parents=True)
    module_path.write_bytes(Path(preflight.__file__).read_bytes())
    location = b'{"schema":"wd.mind-runtime-location.v1","source_commit":"' + b"a" * 40 + b'"}'
    (bundle / "wd-mind-runtime-location.json").write_bytes(location)
    manifest = json.dumps({"schema_version": 1, "source_commit": "a" * 40, "files": {
        "tools-bootstrap/tools/wd_mind_runtime_process.py": _hash(module_path.read_bytes()).upper(),
        "wd-mind-runtime-location.json": _hash(location).upper(),
    }}, sort_keys=True).encode()
    (bundle / "deployment-manifest.json").write_bytes(manifest)
    spec = importlib.util.spec_from_file_location("synthetic_guardian_preflight", module_path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    spec.loader.exec_module(module)
    marker_root = tmp_path / "marker root Å"
    marker_root.mkdir()
    monkeypatch.setattr(module, "_GUARDIAN_AUDIT_ROOT", marker_root, raising=False)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("process started"))
    monkeypatch.setattr(socket, "socket", lambda *a, **k: pytest.fail("network opened"))
    return module, marker_root, {"enabled": True, "bundle_root": bundle,
                                 "deployment_manifest_sha256": _hash(manifest)}


def test_guardian_attempt_default_off_has_no_io(monkeypatch):
    monkeypatch.setattr(preflight, "_read_pinned_file", lambda *a, **k: pytest.fail("read while disabled"))
    assert preflight.reserve_guardian_attempt() == {"status": "OFF"}


def test_guardian_attempt_native_create_retains_handles_and_never_deletes(guardian_candidate):
    module, root, kwargs = guardian_candidate
    attempt = module.reserve_guardian_attempt(**kwargs)
    try:
        evidence = attempt.check()
        assert evidence["status"] == "RESERVED_ATTEMPT_ONLY"
        assert evidence["authority"] == "none"
        assert evidence["process_started"] is False
        marker = root / "mind-owner-attempt.json"
        payload = json.loads(marker.read_bytes())
        assert payload["deployment_manifest_sha256"] == kwargs["deployment_manifest_sha256"]
        assert payload["source_commit"] == "a" * 40
        assert set(root.iterdir()) == {marker}
        with pytest.raises(OSError):
            root.rename(root.with_name("renamed"))
        with pytest.raises(OSError):
            marker.write_bytes(b"overwrite must fail")
    finally:
        attempt.close()
    assert marker.is_file()
    previous = marker.read_bytes()
    with pytest.raises(module.GuardianAttemptHold, match="^mind_guardian_attempt_hold$"):
        module.reserve_guardian_attempt(**kwargs)
    assert marker.read_bytes() == previous


@pytest.mark.parametrize("raw", [b"", b"{", b'{"different_manifest":"old"}'])
def test_guardian_attempt_existing_partial_or_old_marker_is_hold(guardian_candidate, raw):
    module, root, kwargs = guardian_candidate
    marker = root / "mind-owner-attempt.json"
    marker.write_bytes(raw)
    with pytest.raises(module.GuardianAttemptHold, match="^mind_guardian_attempt_hold$"):
        module.reserve_guardian_attempt(**kwargs)
    assert marker.read_bytes() == raw


@pytest.mark.parametrize("case", ["manifest_pin", "location_bytes", "module_bytes", "duplicate", "origin"])
def test_guardian_attempt_controls_refuse_before_marker_creation(guardian_candidate, monkeypatch, case):
    module, root, kwargs = guardian_candidate
    bundle = kwargs["bundle_root"]
    if case == "manifest_pin":
        kwargs["deployment_manifest_sha256"] = "0" * 64
    elif case == "location_bytes":
        (bundle / "wd-mind-runtime-location.json").write_bytes(b"{}")
    elif case == "module_bytes":
        Path(module.__file__).write_bytes(b"# changed after import")
    elif case == "origin":
        monkeypatch.setattr(module, "__file__", str(root / "unpackaged.py"))
    else:
        manifest = bundle / "deployment-manifest.json"
        raw = manifest.read_bytes().replace(b'"schema_version": 1', b'"schema_version": 1,"schema_version": 1')
        manifest.write_bytes(raw)
        kwargs["deployment_manifest_sha256"] = _hash(raw)
    with pytest.raises(module.GuardianAttemptHold, match="^mind_guardian_attempt_hold$") as caught:
        module.reserve_guardian_attempt(**kwargs)
    assert caught.value.__context__ is None
    assert list(root.iterdir()) == []


@pytest.mark.parametrize("case", ["partial_write", "flush", "read", "root_identity", "manifest_after_write"])
def test_guardian_attempt_post_creation_failure_leaves_marker_hold(guardian_candidate, monkeypatch, case):
    module, root, kwargs = guardian_candidate
    native = module._GuardianNative
    original_write, original_inspect = native.write, native.inspect
    written = []

    def write(self, handle, raw):
        if case == "partial_write":
            original_write(self, handle, raw[:9])
            raise OSError("synthetic short write")
        if case == "flush":
            monkeypatch.setattr(self.kernel, "FlushFileBuffers", lambda handle: False)
        original_write(self, handle, raw)
        written.append(True)
        if case == "manifest_after_write":
            (kwargs["bundle_root"] / "deployment-manifest.json").write_bytes(b"{}")

    def inspect(self, handle, path, *, directory):
        identity, length = original_inspect(self, handle, path, directory=directory)
        if case == "root_identity" and directory and written:
            identity = (identity[0], identity[1], identity[2] + 1)
        return identity, length

    monkeypatch.setattr(native, "write", write)
    monkeypatch.setattr(native, "inspect", inspect)
    if case == "read":
        monkeypatch.setattr(native, "read", lambda *a: b"changed marker")
    with pytest.raises(module.GuardianAttemptHold, match="^mind_guardian_attempt_hold$") as caught:
        module.reserve_guardian_attempt(**kwargs)
    assert caught.value.__context__ is None
    marker = root / "mind-owner-attempt.json"
    before = marker.read_bytes()
    assert len(before) == 9 if case == "partial_write" else len(before) > 9
    with pytest.raises(module.GuardianAttemptHold):
        module.reserve_guardian_attempt(**kwargs)
    assert marker.read_bytes() == before


@pytest.mark.parametrize("which", ["root", "ancestor", "marker"])
def test_guardian_attempt_real_junction_never_redirects_create(guardian_candidate, monkeypatch, which):
    import _winapi
    module, root, kwargs = guardian_candidate
    outside = root.parent / "outside"
    outside.mkdir()
    (outside / "untouched").write_bytes(b"synthetic boundary")
    if which == "root":
        root.rmdir()
        _winapi.CreateJunction(str(outside), str(root))
    elif which == "ancestor":
        (outside / "child").mkdir()
        root.rmdir()
        _winapi.CreateJunction(str(outside), str(root))
        monkeypatch.setattr(module, "_GUARDIAN_AUDIT_ROOT", root / "child")
    else:
        _winapi.CreateJunction(str(outside), str(root / "mind-owner-attempt.json"))
    before = {p.relative_to(outside): p.read_bytes() for p in outside.rglob("*") if p.is_file()}
    with pytest.raises(module.GuardianAttemptHold, match="^mind_guardian_attempt_hold$"):
        module.reserve_guardian_attempt(**kwargs)
    assert before == {p.relative_to(outside): p.read_bytes() for p in outside.rglob("*") if p.is_file()}


def test_guardian_attempt_name_switch_before_relative_create_is_blocked(guardian_candidate, monkeypatch):
    module, root, kwargs = guardian_candidate
    original = module._GuardianNative.open
    attempts = []

    def switching_open(self, parent, name, *, directory):
        if not directory:
            assert parent and name == "mind-owner-attempt.json"
            with pytest.raises(OSError):
                root.rename(root.with_name("switched"))
            attempts.append(True)
        return original(self, parent, name, directory=directory)

    monkeypatch.setattr(module._GuardianNative, "open", switching_open)
    attempt = module.reserve_guardian_attempt(**kwargs)
    try:
        assert attempts == [True]
        assert attempt.check()["status"] == "RESERVED_ATTEMPT_ONLY"
    finally:
        attempt.close()
    assert not root.with_name("switched").exists()


def test_guardian_attempt_check_rechecks_controls_and_close_failure_retains_handle(guardian_candidate, monkeypatch):
    module, root, kwargs = guardian_candidate
    attempt = module.reserve_guardian_attempt(**kwargs)
    original_close = attempt._native.close
    failed_handle = attempt._marker
    original_handles = list(attempt._handles)

    def failing_close(handle):
        if handle == failed_handle:
            raise OSError("synthetic close error")
        original_close(handle)

    monkeypatch.setattr(attempt._native, "close", failing_close)
    with pytest.raises(module.GuardianAttemptHold) as caught:
        attempt.close()
    assert caught.value.reservation is attempt
    assert attempt._handles == original_handles
    with pytest.raises(OSError):
        root.rename(root.with_name("must-stay-owned"))
    marker = root / "mind-owner-attempt.json"
    with pytest.raises(OSError):
        marker.write_bytes(b"still owned")
    monkeypatch.setattr(attempt._native, "close", original_close)
    attempt.close()
    attempt.close()
    assert marker.is_file()
    with pytest.raises(module.GuardianAttemptHold):
        attempt.check()


def test_guardian_attempt_manifest_mutation_after_reservation_is_hold(guardian_candidate):
    module, root, kwargs = guardian_candidate
    attempt = module.reserve_guardian_attempt(**kwargs)
    try:
        (kwargs["bundle_root"] / "deployment-manifest.json").write_bytes(b"{}")
        with pytest.raises(module.GuardianAttemptHold):
            attempt.check()
    finally:
        attempt.close()
    assert (root / "mind-owner-attempt.json").is_file()


@pytest.mark.parametrize("enabled", [None, 0, 1, "true"])
def test_guardian_attempt_requires_exact_opt_in(guardian_candidate, enabled):
    module, root, kwargs = guardian_candidate
    kwargs["enabled"] = enabled
    with pytest.raises(module.GuardianAttemptHold):
        module.reserve_guardian_attempt(**kwargs)
    assert list(root.iterdir()) == []


def test_guardian_attempt_unexpected_native_result_and_close_error_keep_owner(guardian_candidate, monkeypatch):
    module, root, kwargs = guardian_candidate
    original_init = module._GuardianNative.__init__
    original_close = module._GuardianNative.close
    instances = []

    def initialize(self):
        original_init(self)
        instances.append(self)
        create = self.nt.NtCreateFile

        def unexpected_disposition(*args):
            result = create(*args)
            if result == 0:
                self.c.cast(args[3], self.c.POINTER(self.IoStatus)).contents.Information = 99
            return result

        monkeypatch.setattr(self.nt, "NtCreateFile", unexpected_disposition)
        monkeypatch.setattr(self, "close", lambda handle: (_ for _ in ()).throw(OSError("close failed")))

    monkeypatch.setattr(module._GuardianNative, "__init__", initialize)
    try:
        with pytest.raises(module.GuardianAttemptHold) as caught:
            module.reserve_guardian_attempt(**kwargs)
        assert caught.value.reservation is not None
        assert len(caught.value.reservation._handles) == 1
        assert list(root.iterdir()) == []
    finally:
        for native in instances:
            while native.handles:
                original_close(native, native.handles.pop())


def test_guardian_attempt_outside_fixed_audit_boundary_refuses(guardian_candidate, monkeypatch):
    module, root, kwargs = guardian_candidate
    monkeypatch.setattr(module, "_GUARDIAN_AUDIT_ROOT", Path(r"C:\Python\project2\unapproved-marker-root"))
    with pytest.raises(module.GuardianAttemptHold):
        module.reserve_guardian_attempt(**kwargs)
    assert list(root.iterdir()) == []


@pytest.fixture
def pipe_candidate(guardian_candidate, monkeypatch):
    import uuid
    module, root, kwargs = guardian_candidate
    monkeypatch.setattr(module, "_PIPE_NAMESPACE", "wd-mind-synthetic-audit-" + uuid.uuid4().hex, raising=False)
    # This runner is elevated. Only the elevation decision is synthetic; the
    # logon SID, DACL, pipe and overlapped I/O below use actual Windows APIs.
    # A separate test exercises the unmodified elevated-refusal path.
    monkeypatch.setattr(module._PipeNative, "elevation", lambda self, token: 0)
    return module, kwargs


def test_candidate_pipe_default_off_has_no_io(monkeypatch):
    monkeypatch.setattr(preflight, "_read_pinned_file", lambda *a, **k: pytest.fail("read while disabled"))
    assert preflight.create_candidate_pipe() == {"status": "OFF"}
    assert preflight.query_candidate_pipe() == {"status": "OFF"}


@pytest.mark.parametrize('method', ['receive', 'reply', '_wait_for_client_close'])
def test_server_operations_refuse_overlong_deadline_before_io(pipe_candidate, monkeypatch, method):
    module, kwargs = pipe_candidate
    server = module.create_candidate_pipe(**kwargs)
    monkeypatch.setattr(server, '_io', lambda *a, **k: pytest.fail('overlong deadline reached native I/O'))
    try:
        with pytest.raises(module.CandidatePipeHold):
            if method == 'reply':
                server.reply(candidates=[], timeout=15.001)
            else:
                getattr(server, method)(timeout=15.001)
    finally:
        assert server.close() == {'state': 'CLOSED', 'owned': False}
    assert module._PIPE_OWNERS == set()


def test_candidate_pipe_read_wait_uses_the_full_validated_deadline(pipe_candidate, monkeypatch):
    import threading
    import time
    module, kwargs = pipe_candidate
    server = module.create_candidate_pipe(**kwargs)
    original_init = module._PipeNative.__init__
    waits = []
    def initialize(native):
        original_init(native)
        original_wait = native.kernel.WaitForSingleObject
        def observe_wait(handle, milliseconds):
            waits.append(milliseconds)
            return original_wait(handle, milliseconds)
        monkeypatch.setattr(native.kernel, 'WaitForSingleObject', observe_wait)
    monkeypatch.setattr(module._PipeNative, '__init__', initialize)
    outputs = []
    def client():
        try:
            outputs.append(module.query_candidate_pipe(**kwargs, lane='codex-lead-1', timeout=16))
        except Exception as exc:
            outputs.append(exc)
    thread = threading.Thread(target=client)
    thread.start()
    try:
        server.receive(timeout=3)
        time.sleep(0.08)  # Make the client's read pend, not the test for 16 seconds.
        server.reply(candidates=[], timeout=2)
        thread.join(3)
        assert not thread.is_alive()
        assert len(outputs) == 1 and type(outputs[0]) is dict and outputs[0]['status'] == 'ABSTAIN'
        assert any(wait > 15000 for wait in waits), waits
    finally:
        server.close()
        thread.join(3)
    assert module._PIPE_OWNERS == set()


def test_candidate_pipe_native_public_query_and_reference_only_reply(pipe_candidate):
    import threading
    module, kwargs = pipe_candidate
    server = module.create_candidate_pipe(**kwargs)
    outputs = []
    def client():
        try:
            outputs.append(module.query_candidate_pipe(**kwargs, lane="codex-lead-1", scope="shared", timeout=3))
        except Exception as exc:
            outputs.append(exc)
    thread = threading.Thread(target=client)
    thread.start()
    try:
        request = server.receive(timeout=3)
        assert request["query_id"] == "startup_work_overview"
        assert request["lane"] == "codex-lead-1" and request["scope"] == "shared"
        candidates = [{"ref_sha256": "a" * 64, "doc_id_sha256": "b" * 64, "source_event_sha256": "c" * 64}]
        server.reply(candidates=candidates, timeout=3)
        thread.join(5)
        assert not thread.is_alive()
        assert len(outputs) == 1 and type(outputs[0]) is dict
        assert outputs[0]["candidates"] == candidates
        assert outputs[0]["status"] == "CANDIDATES"
        assert outputs[0]["nonce"] == request["nonce"]
    finally:
        server.close()
        thread.join(5)
    assert module._PIPE_OWNERS == set()


def test_candidate_pipe_native_first_instance_refuses_existing_name(pipe_candidate):
    module, kwargs = pipe_candidate
    server = module.create_candidate_pipe(**kwargs)
    try:
        with pytest.raises(module.CandidatePipeHold, match="^mind_candidate_pipe_hold$"):
            module.create_candidate_pipe(**kwargs)
    finally:
        assert server.close() == {"state": "CLOSED", "owned": False}


def test_candidate_pipe_real_elevated_process_refuses_before_creation(guardian_candidate, monkeypatch):
    module, root, kwargs = guardian_candidate
    native = module._PipeNative()
    token = native.w.HANDLE()
    assert native.advapi.OpenProcessToken(native.kernel.GetCurrentProcess(), 8, native.c.byref(token))
    try:
        elevated = native.elevation(token)
    finally:
        native.close(token.value)
    if elevated != 1:
        pytest.skip("real elevated-refusal assertion requires an elevated test runner")
    monkeypatch.setattr(module._PipeNative, "server", lambda *a: pytest.fail("created while elevated"))
    with pytest.raises(module.CandidatePipeHold):
        module.create_candidate_pipe(**kwargs)
    assert module._PIPE_OWNERS == set()


def test_candidate_pipe_actual_protected_logon_dacl_and_explicit_flags(pipe_candidate, monkeypatch):
    module, kwargs = pipe_candidate
    original_init = module._PipeNative.__init__
    flags = []
    def initialize(self):
        original_init(self)
        create = self.kernel.CreateNamedPipeW
        def observed(*args):
            flags.append((args[1], args[2], args[3]))
            return create(*args)
        monkeypatch.setattr(self.kernel, "CreateNamedPipeW", observed)
    monkeypatch.setattr(module._PipeNative, "__init__", initialize)
    server = module.create_candidate_pipe(**kwargs)
    n = server._native
    c, w = n.c, n.w
    descriptor, text = c.c_void_p(), c.c_void_p()
    get = n.advapi.GetSecurityInfo
    get.argtypes = [w.HANDLE, w.DWORD, w.DWORD] + [c.c_void_p] * 4 + [c.POINTER(c.c_void_p)]
    get.restype = w.DWORD
    convert = n.advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW
    convert.argtypes = [c.c_void_p, w.DWORD, w.DWORD, c.POINTER(c.c_void_p), c.c_void_p]
    convert.restype = w.BOOL
    try:
        assert get(server._pipe, 1, 4, None, None, None, None, c.byref(descriptor)) == 0
        assert convert(descriptor, 1, 4, c.byref(text), None)
        sddl = c.wstring_at(text)
        sid = n.logon_sid()
        assert sddl == "D:P(A;;0x100183;;;" + sid + ")"
        assert flags == [(0x40080003, 0xE, 1)]
        assert 0x100183 & 4 == 0  # No FILE_CREATE_PIPE_INSTANCE.
    finally:
        if text.value: assert not n.kernel.LocalFree(text)
        if descriptor.value: assert not n.kernel.LocalFree(descriptor)
        assert server.close()["state"] == "CLOSED"


def _wire_request():
    return {"schema": "wd.mind-pipe-request.v1", "query_id": "startup_work_overview",
            "nonce": "d" * 64, "lane": "codex-lead-1", "scope": "shared"}


@pytest.mark.parametrize("case", ["duplicate", "nan", "utf16", "utf32", "length", "trailing", "oversized",
                                  "query", "lane", "scope", "extra", "array", "bom"])
def test_candidate_pipe_real_transport_rejects_malformed_request(pipe_candidate, case):
    import threading
    module, kwargs = pipe_candidate
    request = _wire_request()
    if case in ("query", "lane", "scope"):
        request[{"query": "query_id"}.get(case, case)] = "not_permitted"
    if case == "extra": request["private_text"] = "synthetic should not cross validated API"
    if case == "array": request = [request]
    raw = json.dumps(request, separators=(",", ":")).encode()
    if case == "duplicate": raw = raw.replace(b'"scope":"shared"', b'"scope":"shared","scope":"shared"')
    if case == "nan": raw = raw.replace(b'"scope":"shared"', b'"scope":NaN')
    if case in ("utf16", "utf32"): raw = raw.decode().encode("utf-16-le" if case == "utf16" else "utf-32-le")
    if case == "bom": raw = b"\xef\xbb\xbf" + raw
    if case == "oversized": raw = b" " * 2049
    frame = (len(raw) + (1 if case == "length" else 0)).to_bytes(4, "little") + raw
    if case == "trailing": frame += b"x"
    server = module.create_candidate_pipe(**kwargs)
    client = module._candidate_pipe_open(kwargs["bundle_root"], kwargs["deployment_manifest_sha256"], server=False)
    errors = []
    def sender():
        try: client._io("write", module._pipe_deadline(2), frame)
        except Exception: errors.append("refused")
    thread = threading.Thread(target=sender)
    thread.start()
    try:
        with pytest.raises(module.CandidatePipeHold, match="^mind_candidate_pipe_hold$") as caught:
            server.receive(timeout=2)
        assert caught.value.__context__ is None
    finally:
        thread.join(3)
        assert not thread.is_alive()
        client.close()
        server.close()
    assert module._PIPE_OWNERS == set()


@pytest.mark.parametrize("case", ["nonce", "lane", "scope", "extra", "duplicate", "four", "same", "status"])
def test_candidate_pipe_client_rejects_untrusted_response_binding_and_shape(pipe_candidate, case):
    import threading
    module, kwargs = pipe_candidate
    server = module.create_candidate_pipe(**kwargs)
    outputs = []
    def client():
        try: outputs.append(module.query_candidate_pipe(**kwargs, lane="codex-lead-1", timeout=3))
        except Exception as exc: outputs.append(exc)
    thread = threading.Thread(target=client)
    thread.start()
    try:
        request = server.receive(timeout=3)
        value = {"schema": "wd.mind-pipe-candidates.v1", "nonce": request["nonce"],
                 "lane": request["lane"], "scope": request["scope"], "status": "ABSTAIN", "candidates": []}
        if case == "nonce": value["nonce"] = "0" * 64
        if case == "lane": value["lane"] = "fable-5"
        if case == "scope": value["scope"] = "own"
        if case == "extra": value["stored_text"] = "synthetic forbidden text"
        if case in ("four", "same"):
            item = {"ref_sha256": "a" * 64, "doc_id_sha256": "b" * 64, "source_event_sha256": "c" * 64}
            value.update(status="CANDIDATES", candidates=[item] * (4 if case == "four" else 2))
        if case == "status": value["status"] = "CANDIDATES"
        raw = json.dumps(value, separators=(",", ":")).encode()
        if case == "duplicate": raw = raw.replace(b'"status":"ABSTAIN"', b'"status":"ABSTAIN","status":"ABSTAIN"')
        server._io("write", module._pipe_deadline(3), len(raw).to_bytes(4, "little") + raw)
        thread.join(4)
        assert not thread.is_alive()
        assert len(outputs) == 1 and isinstance(outputs[0], module.CandidatePipeHold)
        assert str(outputs[0]) == "mind_candidate_pipe_hold"
    finally:
        server.close()
        thread.join(4)
    assert module._PIPE_OWNERS == set()


def test_candidate_pipe_native_timeout_cancels_and_retains_until_completion(pipe_candidate, monkeypatch):
    module, kwargs = pipe_candidate
    server = module.create_candidate_pipe(**kwargs)
    native = server._native
    original = native.kernel.GetOverlappedResult
    original_close = native.close
    closed = []
    def incomplete(*args):
        native.c.set_last_error(996)
        return False
    monkeypatch.setattr(native.kernel, "GetOverlappedResult", incomplete)
    monkeypatch.setattr(native, "close", lambda handle: (closed.append(handle), original_close(handle))[1])
    try:
        with pytest.raises(module.CandidatePipeHold): server.receive(timeout=0.02)
        operation = server._pending
        assert operation is not None and operation["pending"]
        assert server.close() == {"state": "HOLD", "owned": True}
        assert server in module._PIPE_OWNERS and not closed
        assert server._pending is operation
    finally:
        monkeypatch.setattr(native.kernel, "GetOverlappedResult", original)
        # The real CancelIoEx was issued. Its actual completion must now be
        # observed; no forcibly closed pipe or freed pending OVERLAPPED.
        native.kernel.WaitForSingleObject(server._pending["overlapped"].hEvent, 1000)
        assert server.close() == {"state": "CLOSED", "owned": False}
    assert module._PIPE_OWNERS == set()


def test_candidate_pipe_close_failure_preserves_registry_and_handle(pipe_candidate, monkeypatch):
    module, kwargs = pipe_candidate
    server = module.create_candidate_pipe(**kwargs)
    original = server._native.close
    monkeypatch.setattr(server._native, "close", lambda handle: (_ for _ in ()).throw(OSError("synthetic")))
    assert server.close() == {"state": "HOLD", "owned": True}
    assert server._pipe in server._native.handles and server in module._PIPE_OWNERS
    monkeypatch.setattr(server._native, "close", original)
    assert server.close() == {"state": "CLOSED", "owned": False}


def test_candidate_pipe_owner_budget_refuses_before_native_allocation(pipe_candidate, monkeypatch):
    module, kwargs = pipe_candidate
    owners = {object() for _ in range(6)}
    module._PIPE_OWNERS.update(owners)
    monkeypatch.setattr(module, "_PipeNative", lambda: pytest.fail("allocated above ownership budget"))
    try:
        with pytest.raises(module.CandidatePipeHold): module.create_candidate_pipe(**kwargs)
    finally:
        module._PIPE_OWNERS.difference_update(owners)


@pytest.mark.parametrize("lane,count", [("codex-lead-1", 0), ("codex-tools-1", 3),
    ("fable-5", 0), ("claude-rco-1", 3), ("claude-rco-2", 0)])
def test_candidate_pipe_all_public_routes_abstain_three_refs_and_client_sqos(pipe_candidate, monkeypatch, lane, count):
    import threading
    module, kwargs = pipe_candidate
    original_init = module._PipeNative.__init__
    flags = []
    def initialize(self):
        original_init(self)
        original = self.kernel.CreateFileW
        def opened(*args):
            flags.append((args[1], args[5]))
            return original(*args)
        monkeypatch.setattr(self.kernel, "CreateFileW", opened)
    monkeypatch.setattr(module._PipeNative, "__init__", initialize)
    server = module.create_candidate_pipe(**kwargs)
    outputs = []
    def client():
        try: outputs.append(module.query_candidate_pipe(**kwargs, lane=lane, scope="own", timeout=3))
        except Exception as exc: outputs.append(exc)
    thread = threading.Thread(target=client)
    thread.start()
    try:
        request = server.receive(timeout=3)
        # Changing the caller's returned request cannot rewrite stored routing.
        request["lane"] = "forged"
        rows = [{"ref_sha256": str(i) * 64, "doc_id_sha256": "b" * 64,
                 "source_event_sha256": "c" * 64} for i in range(count)]
        server.reply(candidates=rows, timeout=3)
        thread.join(4)
        assert not thread.is_alive()
        assert len(outputs) == 1 and type(outputs[0]) is dict
        assert outputs[0]["lane"] == lane and outputs[0]["scope"] == "own"
        assert outputs[0]["status"] == ("CANDIDATES" if count else "ABSTAIN")
        assert outputs[0]["candidates"] == rows
        assert flags == [(0x100183, 0x40110000)]
    finally:
        server.close()
        thread.join(4)
    assert module._PIPE_OWNERS == set()


def test_candidate_pipe_exact_utf8_escaped_public_fields_are_accepted(pipe_candidate):
    module, kwargs = pipe_candidate
    server = module.create_candidate_pipe(**kwargs)
    client = module._candidate_pipe_open(kwargs["bundle_root"], kwargs["deployment_manifest_sha256"], server=False)
    raw = json.dumps(_wire_request()).encode().replace(b"shared", b"\\u0073hared")
    try:
        client._io("write", module._pipe_deadline(2), len(raw).to_bytes(4, "little") + raw)
        assert server.receive(timeout=2) == _wire_request()
        server.reply(candidates=[], timeout=2)
        response = module._pipe_parse(client._io("read", module._pipe_deadline(2)), response=True, request=_wire_request())
        assert response["status"] == "ABSTAIN"
    finally:
        client.close()
        server.close()


def test_candidate_pipe_late_successful_native_write_is_not_success(pipe_candidate, monkeypatch):
    import threading
    import time
    module, kwargs = pipe_candidate
    server = module.create_candidate_pipe(**kwargs)
    original_open = module._candidate_pipe_open
    writes = []
    def opened(*args, **options):
        channel = original_open(*args, **options)
        if not options["server"]:
            original = channel._native.kernel.WriteFile
            def delayed(*values):
                result = original(*values)
                writes.append(bool(result))
                time.sleep(0.2)
                return result
            monkeypatch.setattr(channel._native.kernel, "WriteFile", delayed)
        return channel
    monkeypatch.setattr(module, "_candidate_pipe_open", opened)
    outputs = []
    def client():
        try: outputs.append(module.query_candidate_pipe(**kwargs, lane="codex-lead-1", timeout=0.15))
        except Exception as exc: outputs.append(exc)
    thread = threading.Thread(target=client)
    thread.start()
    try:
        assert server.receive(timeout=2)["query_id"] == "startup_work_overview"
        thread.join(3)
        assert not thread.is_alive() and writes == [True]
        assert len(outputs) == 1 and isinstance(outputs[0], module.CandidatePipeHold)
    finally:
        server.close()
        thread.join(3)
    assert module._PIPE_OWNERS == set()


def test_candidate_pipe_client_close_error_returns_retained_owner(pipe_candidate, monkeypatch):
    import threading
    module, kwargs = pipe_candidate
    server = module.create_candidate_pipe(**kwargs)
    original_open = module._candidate_pipe_open
    retained = []
    def opened(*args, **options):
        channel = original_open(*args, **options)
        if not options["server"]:
            retained.append((channel, channel._native.close))
            monkeypatch.setattr(channel._native, "close", lambda handle: (_ for _ in ()).throw(OSError("close")))
        return channel
    monkeypatch.setattr(module, "_candidate_pipe_open", opened)
    outputs = []
    def client():
        try: outputs.append(module.query_candidate_pipe(**kwargs, lane="codex-lead-1", timeout=3))
        except Exception as exc: outputs.append(exc)
    thread = threading.Thread(target=client)
    thread.start()
    try:
        # The client's write event cannot close, so it fails before response read.
        server.receive(timeout=3)
        thread.join(4)
        assert not thread.is_alive()
        assert len(outputs) == 1 and isinstance(outputs[0], module.CandidatePipeHold)
        assert outputs[0].reservation is retained[0][0]
        assert outputs[0].reservation in module._PIPE_OWNERS
    finally:
        for channel, close in retained:
            monkeypatch.setattr(channel._native, "close", close)
            assert channel.close()["state"] == "CLOSED"
        server.close()
        thread.join(4)
    assert module._PIPE_OWNERS == set()


@pytest.mark.parametrize("timeout", [0, -1, 361, True, float("nan"), float("inf")])
def test_candidate_pipe_bad_deadline_refuses_before_native_io(pipe_candidate, monkeypatch, timeout):
    module, kwargs = pipe_candidate
    monkeypatch.setattr(module, "_PipeNative", lambda: pytest.fail("invalid deadline reached native I/O"))
    with pytest.raises(module.CandidatePipeHold):
        module.query_candidate_pipe(**kwargs, lane="codex-lead-1", timeout=timeout)


def _until(predicate, timeout=3):
    import time
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate(): return
        time.sleep(0.005)
    pytest.fail("synthetic service transition deadline")


def test_candidate_service_default_off_has_no_callback_or_io(monkeypatch):
    monkeypatch.setattr(preflight, "_read_pinned_file", lambda *a, **k: pytest.fail("disabled I/O"))
    assert preflight.create_candidate_service(handler=lambda request: pytest.fail("disabled callback")) == {"status": "OFF"}


@pytest.mark.parametrize("rows", [[], [{"ref_sha256": "a" * 64, "doc_id_sha256": "b" * 64, "source_event_sha256": "c" * 64}]])
def test_candidate_service_native_ref_only_callback_and_explicit_stop(pipe_candidate, rows):
    module, kwargs = pipe_candidate
    seen = []
    def handler(request):
        seen.append(dict(request))
        return rows
    service = module.create_candidate_service(**kwargs, handler=handler,
        receive_timeout=2, reply_timeout=2, callback_timeout=1)
    try:
        assert service.start()["owned"] is True
        _until(lambda: service.status()["state"] == "RECEIVING")
        response = module.query_candidate_pipe(**kwargs, lane="fable-5", scope="shared", timeout=3)
        assert response["candidates"] == rows
        assert response["status"] == ("CANDIDATES" if rows else "ABSTAIN")
        assert len(seen) == 1 and seen[0]["query_id"] == "startup_work_overview"
    finally:
        stopped = service.stop(timeout=3)
    assert stopped == {"state": "STOPPED", "owned": False, "callback_pending": False, "io_pending": False}
    assert module._PIPE_OWNERS == set() and module._SERVICE_OWNERS == set()


def test_candidate_pipe_retries_only_transient_absent_name_within_same_deadline(pipe_candidate):
    import threading
    import time
    module, kwargs = pipe_candidate
    outputs = []
    def client():
        try: outputs.append(module.query_candidate_pipe(**kwargs, lane="codex-lead-1", timeout=2))
        except Exception as exc: outputs.append(exc)
    thread = threading.Thread(target=client)
    thread.start()
    time.sleep(0.04)
    server = module.create_candidate_pipe(**kwargs)
    try:
        server.receive(timeout=2)
        server.reply(candidates=[], timeout=2)
        thread.join(3)
        assert not thread.is_alive()
        assert len(outputs) == 1 and type(outputs[0]) is dict and outputs[0]["status"] == "ABSTAIN"
    finally:
        server.close()
        thread.join(3)


@pytest.mark.parametrize("error", [5, 87, 233])
def test_candidate_pipe_never_retries_acl_mode_or_unknown_open_failure(pipe_candidate, monkeypatch, error):
    module, kwargs = pipe_candidate
    original = module._PipeNative.__init__
    calls = []
    def initialize(self):
        original(self)
        def refused(*args):
            calls.append(error)
            self.c.set_last_error(error)
            return self.c.c_void_p(-1).value
        monkeypatch.setattr(self.kernel, "CreateFileW", refused)
    monkeypatch.setattr(module._PipeNative, "__init__", initialize)
    with pytest.raises(module.CandidatePipeHold):
        module.query_candidate_pipe(**kwargs, lane="codex-lead-1", timeout=1)
    assert calls == [error] and module._PIPE_OWNERS == set()


def test_candidate_pipe_retry_rechecks_pins_before_second_native_open(pipe_candidate, monkeypatch):
    module, kwargs = pipe_candidate
    original = module._PipeNative.client
    calls = []
    def absent(self, name):
        calls.append(True)
        (kwargs["bundle_root"] / "wd-mind-runtime-location.json").write_bytes(b"{}")
        return original(self, name)
    monkeypatch.setattr(module._PipeNative, "client", absent)
    with pytest.raises(module.CandidatePipeHold):
        module.query_candidate_pipe(**kwargs, lane="codex-lead-1", timeout=1)
    assert calls == [True] and module._PIPE_OWNERS == set()


def test_candidate_pipe_real_elevated_client_gets_only_identification_token(pipe_candidate, monkeypatch):
    import threading
    module, kwargs = pipe_candidate
    server = module.create_candidate_pipe(**kwargs)
    native = server._native
    c, w = native.c, native.w
    # Read actual TokenElevation directly; the fixture seam never changes the
    # OS token. This proves SQOS with an elevated client, not a Limited server.
    process_token = w.HANDLE()
    assert native.advapi.OpenProcessToken(native.kernel.GetCurrentProcess(), 8, c.byref(process_token))
    elevation, size = w.DWORD(), w.DWORD()
    try:
        assert native.advapi.GetTokenInformation(process_token, 20, c.byref(elevation), 4, c.byref(size))
    finally:
        native.close(process_token.value)
    if elevation.value != 1:
        server.close()
        pytest.skip("elevated client probe requires elevated test runner")
    # Restore the real gate after creating the seam-only test server. The
    # client's entire token/elevation/logon selection is now unmodified.
    monkeypatch.setattr(module._PipeNative, "elevation", preflight._PipeNative.elevation)
    native.kernel.GetCurrentThread.restype = w.HANDLE
    impersonate = native.advapi.ImpersonateNamedPipeClient
    impersonate.argtypes, impersonate.restype = [w.HANDLE], w.BOOL
    revert = native.advapi.RevertToSelf
    revert.argtypes, revert.restype = [], w.BOOL
    open_thread = native.advapi.OpenThreadToken
    open_thread.argtypes, open_thread.restype = [w.HANDLE, w.DWORD, w.BOOL, c.POINTER(w.HANDLE)], w.BOOL
    outputs = []
    def client():
        try: outputs.append(module.query_candidate_pipe(**kwargs, lane="codex-lead-1", timeout=3))
        except Exception as exc: outputs.append(exc)
    thread = threading.Thread(target=client)
    thread.start()
    try:
        server.receive(timeout=3)
        assert impersonate(server._pipe)
        token = w.HANDLE()
        try:
            assert open_thread(native.kernel.GetCurrentThread(), 8, True, c.byref(token))
            level, length = w.DWORD(), w.DWORD()
            assert native.advapi.GetTokenInformation(token, 9, c.byref(level), 4, c.byref(length))
            assert length.value == 4 and level.value == 1  # SecurityIdentification.
        finally:
            if token.value: native.close(token.value)
            assert revert()
        server.reply(candidates=[], timeout=3)
        thread.join(4)
        assert not thread.is_alive() and len(outputs) == 1 and type(outputs[0]) is dict
    finally:
        server.close()
        thread.join(4)


@pytest.mark.parametrize("case", ["exception", "invalid_output", "mutated_request", "mutated_pin"])
def test_candidate_service_callback_failure_is_hold_without_response(pipe_candidate, case):
    module, kwargs = pipe_candidate
    def handler(request):
        if case == "exception": raise RuntimeError("synthetic callback failure")
        if case == "mutated_request": request["scope"] = "own"
        if case == "mutated_pin": (kwargs["bundle_root"] / "wd-mind-runtime-location.json").write_bytes(b"{}")
        return {"text": "forbidden synthetic output"} if case == "invalid_output" else []
    service = module.create_candidate_service(**kwargs, handler=handler,
        receive_timeout=1, reply_timeout=1, callback_timeout=1)
    try:
        service.start()
        _until(lambda: service.status()["state"] == "RECEIVING")
        with pytest.raises(module.CandidatePipeHold):
            module.query_candidate_pipe(**kwargs, lane="codex-lead-1", timeout=2)
        _until(lambda: service.status()["state"] == "HOLD")
        assert service.status()["owned"] is True
        with pytest.raises(module.CandidateServiceHold): service.start()
    finally:
        assert service.stop(timeout=2)["state"] == "STOPPED"
    assert module._PIPE_OWNERS == set() and module._SERVICE_OWNERS == set()


@pytest.mark.parametrize("explicit_stop", [False, True])
def test_candidate_service_hung_callback_keeps_slot_and_no_late_reply(pipe_candidate, explicit_stop):
    import threading
    module, kwargs = pipe_candidate
    entered, release = threading.Event(), threading.Event()
    def handler(request):
        entered.set()
        assert release.wait(5)
        return []
    service = module.create_candidate_service(**kwargs, handler=handler,
        receive_timeout=1, reply_timeout=1, callback_timeout=0.06)
    outputs = []
    def client():
        try: outputs.append(module.query_candidate_pipe(**kwargs, lane="codex-lead-1", timeout=2))
        except Exception as exc: outputs.append(exc)
    thread = threading.Thread(target=client)
    service.start()
    _until(lambda: service.status()["state"] == "RECEIVING")
    thread.start()
    try:
        assert entered.wait(2)
        if explicit_stop:
            assert service.stop(timeout=0)["state"] in ("BUSY", "HOLD")
        _until(lambda: service.status()["state"] == "HOLD")
        assert service.status()["callback_pending"] is True
        assert service.stop(timeout=0)["state"] == "HOLD"
        with pytest.raises(module.CandidateServiceHold):
            module.create_candidate_service(**kwargs, handler=lambda request: [])
        thread.join(3)
        assert not thread.is_alive() and len(outputs) == 1 and isinstance(outputs[0], module.CandidatePipeHold)
    finally:
        release.set()
        assert service.stop(timeout=2)["state"] == "STOPPED"
        thread.join(3)
    assert module._PIPE_OWNERS == set() and module._SERVICE_OWNERS == set()


def test_candidate_service_idle_timeout_and_pending_completion_are_hold(pipe_candidate, monkeypatch):
    module, kwargs = pipe_candidate
    service = module.create_candidate_service(**kwargs, handler=lambda request: pytest.fail("no request"),
        receive_timeout=0.08, reply_timeout=1, callback_timeout=1)
    service.start()
    _until(lambda: service.status()["state"] == "RECEIVING")
    channel = service._pipe
    native = channel._native
    original = native.kernel.GetOverlappedResult
    def pending(*args):
        native.c.set_last_error(996)
        return False
    monkeypatch.setattr(native.kernel, "GetOverlappedResult", pending)
    try:
        _until(lambda: service.status()["state"] == "HOLD")
        assert service.stop(timeout=0.1) == {"state": "HOLD", "owned": True, "callback_pending": False, "io_pending": True}
        assert channel in module._PIPE_OWNERS and service in module._SERVICE_OWNERS
    finally:
        monkeypatch.setattr(native.kernel, "GetOverlappedResult", original)
        native.kernel.WaitForSingleObject(channel._pending["overlapped"].hEvent, 1000)
        assert service.stop(timeout=2)["state"] == "STOPPED"


def test_candidate_service_sequential_own_shared_and_concurrent_start(pipe_candidate):
    import threading
    module, kwargs = pipe_candidate
    calls = []
    service = module.create_candidate_service(**kwargs, handler=lambda request: (calls.append(request["scope"]), [])[1],
        receive_timeout=2, reply_timeout=2, callback_timeout=1)
    starts = []
    def start():
        try: service.start(); starts.append("started")
        except module.CandidateServiceHold: starts.append("refused")
    threads = [threading.Thread(target=start) for _ in range(2)]
    for thread in threads: thread.start()
    for thread in threads: thread.join(2)
    try:
        assert sorted(starts) == ["refused", "started"]
        for scope in ("own", "shared"):
            response = module.query_candidate_pipe(**kwargs, lane="claude-rco-1", scope=scope, timeout=3)
            assert response["scope"] == scope and response["status"] == "ABSTAIN"
        assert calls == ["own", "shared"]
    finally:
        assert service.stop(timeout=3)["state"] == "STOPPED"


@pytest.mark.parametrize("started", [False, True])
def test_candidate_service_start_error_preserves_owner_and_fixed_refusal(pipe_candidate, monkeypatch, started):
    module, kwargs = pipe_candidate
    calls = []
    service = module.create_candidate_service(**kwargs, handler=lambda request: calls.append(request), receive_timeout=0.2)
    original = module.threading.Thread.start
    def broken(thread):
        if started: original(thread)
        raise OSError("synthetic details must not escape")
    monkeypatch.setattr(module.threading.Thread, "start", broken)
    try:
        with pytest.raises(module.CandidateServiceHold) as caught:
            service.start()
        assert caught.value.__context__ is None
        assert caught.value.service is service and service in module._SERVICE_OWNERS
        assert service._stop.is_set()
    finally:
        assert service.stop(timeout=2)["state"] == "STOPPED"
    assert calls == [] and module._PIPE_OWNERS == set()


def test_candidate_service_confirmed_idle_cancellation_can_receive_later_request(pipe_candidate):
    import time
    module, kwargs = pipe_candidate
    calls = []
    service = module.create_candidate_service(**kwargs, handler=lambda request: (calls.append(request), [])[1],
        receive_timeout=0.03, reply_timeout=1, callback_timeout=1)
    service.start()
    try:
        _until(lambda: service.status()["state"] == "RECEIVING")
        time.sleep(0.15)  # Five synthetic idle windows; no live five-second wait.
        assert service.status()["state"] != "HOLD"
        result = module.query_candidate_pipe(**kwargs, lane="codex-lead-1", timeout=2)
        assert result["status"] == "ABSTAIN" and len(calls) == 1
    finally:
        assert service.stop(timeout=2)["state"] == "STOPPED"
    assert module._PIPE_OWNERS == set() and module._SERVICE_OWNERS == set()


def test_candidate_service_connection_wins_cancellation_race_is_hold(pipe_candidate, monkeypatch):
    module, kwargs = pipe_candidate
    clients, creations = [], []
    original_init, original_server = module._PipeNative.__init__, module._PipeNative.server
    def initialize(self):
        original_init(self)
        original_cancel = self.kernel.CancelIoEx
        def race(*args):
            if not clients:
                clients.append(module._candidate_pipe_open(kwargs["bundle_root"], kwargs["deployment_manifest_sha256"], server=False))
            return original_cancel(*args)
        monkeypatch.setattr(self.kernel, "CancelIoEx", race)
    def created(self, *args):
        creations.append(True)
        return original_server(self, *args)
    monkeypatch.setattr(module._PipeNative, "__init__", initialize)
    monkeypatch.setattr(module._PipeNative, "server", created)
    service = module.create_candidate_service(**kwargs, handler=lambda request: pytest.fail("raced request"), receive_timeout=0.03)
    service.start()
    try:
        _until(lambda: service.status()["state"] == "HOLD")
        assert len(clients) == len(creations) == 1
        assert service.status()["owned"] is True
    finally:
        for client in clients: client.close()
        assert service.stop(timeout=2)["state"] == "STOPPED"


def test_candidate_service_idle_pipe_close_error_never_recreates_endpoint(pipe_candidate, monkeypatch):
    module, kwargs = pipe_candidate
    creations = []
    original_server = module._PipeNative.server
    def created(self, *args):
        creations.append(True)
        return original_server(self, *args)
    monkeypatch.setattr(module._PipeNative, "server", created)
    service = module.create_candidate_service(**kwargs, handler=lambda request: pytest.fail("no request"), receive_timeout=0.08)
    service.start()
    _until(lambda: service.status()["state"] == "RECEIVING")
    channel = service._pipe
    original_close = channel._native.close
    def close(handle):
        if handle == channel._pipe: raise OSError("synthetic pipe close failure")
        original_close(handle)
    monkeypatch.setattr(channel._native, "close", close)
    try:
        _until(lambda: service.status()["state"] == "HOLD")
        assert creations == [True]
        assert service.stop(timeout=0.1)["state"] == "HOLD"
        assert channel in module._PIPE_OWNERS
    finally:
        monkeypatch.setattr(channel._native, "close", original_close)
        assert service.stop(timeout=2)["state"] == "STOPPED"


def test_candidate_service_stop_during_idle_does_not_recreate_endpoint(pipe_candidate, monkeypatch):
    module, kwargs = pipe_candidate
    creations = []
    original = module._PipeNative.server
    def created(self, *args):
        creations.append(True)
        return original(self, *args)
    monkeypatch.setattr(module._PipeNative, "server", created)
    service = module.create_candidate_service(**kwargs, handler=lambda request: pytest.fail("stopped"), receive_timeout=0.05)
    service.start()
    _until(lambda: service.status()["state"] == "RECEIVING")
    assert service.stop(timeout=1)["state"] == "STOPPED"
    assert creations == [True] and module._PIPE_OWNERS == set() and module._SERVICE_OWNERS == set()


def test_manifest_guardian_default_off_never_creates_marker_or_process(monkeypatch):
    monkeypatch.setattr(preflight, "_read_pinned_file", lambda *a, **k: pytest.fail("disabled read"))
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("disabled process"))
    assert preflight.start_manifest_guardian() == {"status": "OFF"}


def test_manifest_guardian_wrong_external_manifest_pin_refuses_before_reservation(guardian_candidate):
    module, root, kwargs = guardian_candidate
    with pytest.raises(module.GuardianProcessHold, match="^mind_guardian_process_hold$"):
        module.start_manifest_guardian(enabled=True, bundle_root=kwargs["bundle_root"],
                                      expected_manifest_sha256="0" * 64)
    assert list(root.iterdir()) == []


def _guardian_cleanup_fake(module):
    from types import SimpleNamespace
    calls = []
    class Native:
        process_info = SimpleNamespace(process=123)
        def exit_code(self): calls.append("exit"); return 0
        def job_limits(self): calls.append("limits")
        def active(self): calls.append("active"); return 0
        def dispose(self, attempt): calls.append("dispose")
        def close_settled(self): calls.append("native_close")
    class Attempt:
        def check(self): calls.append("marker_check")
        def close(self): calls.append("marker_close")
    owner = module._ManifestGuardian(Native(), "synthetic bundle", "a" * 64)
    owner._attempt = Attempt()
    owner._eof = True
    owner._stop.set()
    owner._pumps = [SimpleNamespace(is_alive=lambda: False)] * 2
    owner._pump_closed = {1, 2}
    module._GUARDIAN_OWNERS.add(owner)
    return owner, calls


@pytest.mark.parametrize("case", ["no_eof", "alive", "nonzero", "job_live", "pump_pending", "close_failed", "fault"])
def test_manifest_guardian_cleanup_requires_every_terminal_proof(monkeypatch, case):
    owner, calls = _guardian_cleanup_fake(preflight)
    if case == "no_eof": owner._eof = False
    elif case == "alive": monkeypatch.setattr(owner._native, "exit_code", lambda: None)
    elif case == "nonzero": monkeypatch.setattr(owner._native, "exit_code", lambda: 3)
    elif case == "job_live": monkeypatch.setattr(owner._native, "active", lambda: 1)
    elif case == "pump_pending": owner._pumps[0] = type("Pending", (), {"is_alive": lambda self: True})()
    elif case == "close_failed": owner._pump_closed.remove(2)
    else: owner._fault = True
    try:
        with pytest.raises(ValueError): owner._finish()
        assert "dispose" not in calls and owner._committed is False
        assert owner in preflight._GUARDIAN_OWNERS
    finally:
        preflight._GUARDIAN_OWNERS.discard(owner)


def test_manifest_guardian_cleanup_order_and_commit_point():
    owner, calls = _guardian_cleanup_fake(preflight)
    owner._finish()
    assert calls == ["exit", "limits", "active", "marker_check", "exit", "active", "dispose", "marker_close", "native_close"]
    assert owner.status()["state"] == "STOPPED"
    assert owner.status()["marker_disposition_committed"] is True
    assert owner not in preflight._GUARDIAN_OWNERS


@pytest.mark.parametrize("after_commit", [False, True])
def test_manifest_guardian_cleanup_failure_distinguishes_disposition_commit(monkeypatch, after_commit):
    owner, calls = _guardian_cleanup_fake(preflight)
    def failure(*args): raise OSError("synthetic cleanup failure")
    monkeypatch.setattr(owner._attempt if after_commit else owner._native,
                        "close" if after_commit else "dispose", failure)
    try:
        with pytest.raises(OSError): owner._finish()
        assert owner._committed is after_commit
        assert owner in preflight._GUARDIAN_OWNERS
    finally:
        preflight._GUARDIAN_OWNERS.discard(owner)


@pytest.fixture
def guardian_launch_candidate(guardian_candidate, monkeypatch):
    """Real native audit child, inert fake runtime and explicit token seam."""
    import types
    module, marker_root, kwargs = guardian_candidate
    bundle = kwargs["bundle_root"]
    bootstrap = bundle / "tools-bootstrap"
    package = bootstrap / "tools"
    (package / "__init__.py").write_bytes(b"# synthetic package only\n")
    executable = str(Path(sys.executable).resolve())
    digest = _hash(Path(executable).read_bytes())
    raw = ("import sys,json,threading\n"
           "def _owner_preflight(**kwargs): return True\n"
           "def resolve_manifest_runtime_location(**kwargs):\n"
           f" return {{'runtime_status':'VERIFIED','interpreter_status':'VERIFIED','interpreter':{executable!r},'interpreter_sha256':{digest!r}}}\n"
           "if __name__ == '__main__':\n"
           " done=threading.Event()\n"
           " def eof():\n"
           "  if sys.stdin.buffer.read() == b'': done.set()\n"
           " threading.Thread(target=eof,daemon=True).start()\n"
           " for state in ('STARTING','READY'):\n"
           "  print(json.dumps({'schema':'wd.mind-owner-status.v1','state':state}),flush=True)\n"
           " sys.exit(0 if done.wait(5) else 2)\n").encode()
    (package / "wd_mind_full_runtime.py").write_bytes(raw)
    document = json.loads((bundle / "deployment-manifest.json").read_bytes())
    for path in (package / "__init__.py", package / "wd_mind_full_runtime.py"):
        document["files"][path.relative_to(bundle).as_posix()] = _hash(path.read_bytes())
    manifest = json.dumps(document, sort_keys=True).encode()
    (bundle / "deployment-manifest.json").write_bytes(manifest)
    kwargs["deployment_manifest_sha256"] = _hash(manifest)
    spec = importlib.util.spec_from_file_location("tools", package / "__init__.py", submodule_search_locations=[str(package)])
    synthetic_tools = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "tools", synthetic_tools)
    monkeypatch.delitem(sys.modules, "tools.wd_mind_full_runtime", raising=False)
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    # The host runner is elevated. This is an explicit seam, not a real
    # Limited-token proof. A separate test exercises the real refusal.
    monkeypatch.setattr(module._GuardianProcessNative, "elevation", lambda self, token: 0)
    return module, marker_root, kwargs


@pytest.mark.parametrize('fast_build', [False, True])
def test_manifest_guardian_native_suspend_assign_resume_eof_exit_and_disposition(guardian_launch_candidate, fast_build):
    module, marker_root, kwargs = guardian_launch_candidate
    if fast_build:
        bundle = kwargs['bundle_root']
        owner_path = bundle / 'tools-bootstrap/tools/wd_mind_full_runtime.py'
        raw = owner_path.read_bytes().replace(b"if __name__ == '__main__':\n",
            ("if __name__ == '__main__':\n"
             " assert sys.argv[1:] == ['--owner','--bundle-root'," + repr(str(bundle)) +
             ",'--deployment-manifest-sha256',sys.argv[-2],'--fast-build']\n").encode())
        owner_path.write_bytes(raw)
        document = json.loads((bundle / 'deployment-manifest.json').read_bytes())
        document['files']['tools-bootstrap/tools/wd_mind_full_runtime.py'] = _hash(raw)
        document['mind_startup_guardian'] = {'schema':'wd.mind-startup-guardian.v2', 'enabled':True,
            'entrypoint':'tools/wd_mind_runtime_process.py', 'ready_timeout_seconds':15, 'fast_build':True}
        manifest = json.dumps(document,sort_keys=True).encode()
        (bundle / 'deployment-manifest.json').write_bytes(manifest)
        kwargs['deployment_manifest_sha256'] = _hash(manifest)
    module._guardian_launch_inputs(kwargs["bundle_root"], kwargs["deployment_manifest_sha256"])
    guardian = module.start_manifest_guardian(enabled=True, bundle_root=kwargs["bundle_root"],
        expected_manifest_sha256=kwargs["deployment_manifest_sha256"])
    try:
        _until(lambda: guardian.status()["owner_report"] == "READY", timeout=3)
        native = guardian._native
        assert native.in_job(native.process_info.process, native.job)
        assert not native.in_job(native.kernel.GetCurrentProcess(), native.job)
        native.job_limits()
        assert native.active() >= 1  # Windows may include a console helper.
        assert (marker_root / module._GUARDIAN_MARKER).is_file()
        result = guardian.request_stop(timeout=3)
        assert result["state"] == "STOPPED" and result["owned"] is False
        assert result["marker_disposition_committed"] is True
        assert not (marker_root / module._GUARDIAN_MARKER).exists()
        assert native.job is None and native.process_info.process is None and native.process_info.thread is None
        assert not any(handle.value for pair in native.pipes for handle in pair)
    finally:
        guardian.request_stop(timeout=3)
        # Only release the test's Python lifetime keeper after the inert child's
        # real handle proves exit. Never kill or abandon a live native child.
        if guardian._runner.is_alive() and guardian._native.exit_code() is not None:
            guardian._hold.set()
            guardian._runner.join(2)
            if guardian._attempt is not None: guardian._attempt.close()
            module._GUARDIAN_OWNERS.discard(guardian)


def test_manifest_guardian_unpinned_import_member_refuses_before_owner_import(guardian_launch_candidate):
    module, marker_root, kwargs = guardian_launch_candidate
    extra = kwargs["bundle_root"] / "tools-bootstrap/tools/unbound.py"
    extra.write_bytes(b"raise AssertionError('unbound module executed')\n")
    with pytest.raises(module.GuardianProcessHold):
        module.start_manifest_guardian(enabled=True, bundle_root=kwargs["bundle_root"],
            expected_manifest_sha256=kwargs["deployment_manifest_sha256"])
    assert list(marker_root.iterdir()) == []


def test_guardian_direct_script_can_import_only_the_verified_parent_package(guardian_launch_candidate, monkeypatch):
    module, root, kwargs = guardian_launch_candidate
    monkeypatch.delitem(sys.modules, "tools", raising=False)
    monkeypatch.syspath_prepend(str(kwargs["bundle_root"] / "tools-bootstrap"))
    executable, bootstrap = module._guardian_launch_inputs(kwargs["bundle_root"], kwargs["deployment_manifest_sha256"])
    assert Path(executable) == Path(sys.executable).resolve()
    assert Path(bootstrap) == kwargs["bundle_root"] / "tools-bootstrap"
    assert list(root.iterdir()) == []


@pytest.mark.parametrize('case,fast,accepted', [
    ('absent',False,True), ('legacy',False,True), ('fast',True,True),
    ('missing',False,False), ('false',False,False), ('integer',False,False),
    ('string',False,False), ('extra',False,False), ('legacy_extra',False,False),
    ('unknown_schema',False,False), ('wrong_pin',False,False), ('mutated',False,False),
])
def test_guardian_owner_argv_manifest_fast_build_exact(tmp_path, case, fast, accepted):
    config = {'schema':'wd.mind-startup-guardian.v2', 'enabled':True,
              'entrypoint':'tools/wd_mind_runtime_process.py', 'ready_timeout_seconds':15, 'fast_build':True}
    if case in ('legacy','legacy_extra'):
        config['schema'] = 'wd.mind-startup-guardian.v1'
        if case == 'legacy': del config['fast_build']
    if case == 'missing': del config['fast_build']
    if case == 'false': config['fast_build'] = False
    if case == 'integer': config['fast_build'] = 1
    if case == 'string': config['fast_build'] = 'true'
    if case == 'extra': config['fallback'] = True
    if case == 'unknown_schema': config['schema'] = 'wd.mind-startup-guardian.v3'
    document = {'schema_version':1, 'mind_startup_guardian':config}
    if case == 'absent': del document['mind_startup_guardian']
    raw = json.dumps(document).encode()
    path = tmp_path / 'deployment-manifest.json'; path.write_bytes(raw)
    digest = _hash(raw)
    if case == 'wrong_pin': digest = '0'*64
    if case == 'mutated': path.write_bytes(raw+b' ')
    def arguments():
        return preflight._guardian_owner_argv('synthetic-python', 'synthetic-bootstrap', tmp_path, digest)
    if not accepted:
        with pytest.raises(ValueError): arguments()
        return
    expected = ['synthetic-python','-I','-S','-B','-c',preflight._OWNER_TRAMPOLINE,'synthetic-bootstrap',
                '--owner','--bundle-root',str(tmp_path),'--deployment-manifest-sha256',digest]
    if fast: expected.append('--fast-build')
    assert arguments() == expected


def test_guardian_cli_cannot_override_manifest_fast_build(monkeypatch):
    import io
    monkeypatch.setattr(preflight, 'start_manifest_guardian', lambda **kw: pytest.fail('unexpected start'))
    output = io.StringIO()
    assert preflight._guardian_entrypoint(['--guardian','--bundle-root','C:\\synthetic',
        '--deployment-manifest-sha256','a'*64,'--fast-build'],io.StringIO(),output) == 1


@pytest.fixture
def guardian_fake(guardian_candidate, monkeypatch):
    import threading
    from types import SimpleNamespace
    module, root, kwargs = guardian_candidate
    class Native:
        def __init__(self):
            self.handles, self.allocations, self.calls = [], [], []
            self.kernel = SimpleNamespace(GetCurrentProcess=lambda: -1)
            self.process_info = SimpleNamespace(process=None)
            self.eof = threading.Event()
            self.fail = None
            self.sent = False
            self.active_value = 0
        def logon_sid(self, **kwargs): return "S-1-5-5-1-2"
        def in_job(self, *args): return False
        def spawn_suspended(self, *args):
            self.calls.append("spawn")
            assert (root / module._GUARDIAN_MARKER).is_file()
            self.process_info.process = 123
            if self.fail == "assign": raise OSError("synthetic assign failure")
            self.calls.append("assigned")
        def resume(self):
            self.calls.append("resume")
            if self.fail == "resume": raise OSError("synthetic resume failure")
        def output(self, index):
            if index == 1 and not self.sent:
                self.sent = True
                if self.fail == "oversize": return b"x" * 4097
                if self.fail == "duplicate": return b'{"schema":"wd.mind-owner-status.v1","state":"READY","state":"READY"}\n'
                return b'{"schema":"wd.mind-owner-status.v1","state":"READY"}\n'
            if index == 2 and self.fail == "stderr": return b"synthetic private diagnostics"
            self.eof.wait()
            return b""
        def close_pipe(self, index, end):
            self.calls.append(("close_pipe", index, end))
            if self.fail == "close_" + str(index): raise OSError("synthetic close failure")
            if index == 0: self.eof.set()
        def exit_code(self): return 0 if self.eof.is_set() else None
        def job_limits(self): pass
        def active(self): return self.active_value
        def dispose(self, attempt): self.calls.append("dispose")
        def close_settled(self): self.calls.append("native_close")
    native = Native()
    monkeypatch.setattr(module, "_GuardianProcessNative", lambda: native)
    monkeypatch.setattr(module, "_guardian_launch_inputs", lambda *a: ("inert.exe", "inert-bootstrap"))
    yield module, root, kwargs, native
    # Only fake process handles are used here. Release Python test threads;
    # this is deliberately not a runtime reconciliation/cleanup API.
    native.active_value = 0
    native.eof.set()
    for owner in tuple(module._GUARDIAN_OWNERS):
        owner._hold.set()
        owner._stop.set()
        if owner._runner is not None: owner._runner.join(2)
        for thread in owner._pumps: thread.join(2)
        if owner._attempt is not None:
            try: owner._attempt.close()
            except module.GuardianAttemptHold: pass
        module._GUARDIAN_OWNERS.discard(owner)


@pytest.mark.parametrize("failure", ["assign", "resume", "oversize", "duplicate", "stderr", "close_0", "close_1", "close_2"])
def test_manifest_guardian_fake_failures_keep_marker_and_process_owner(guardian_fake, failure):
    module, root, kwargs, native = guardian_fake
    native.fail = failure
    guardian = module.start_manifest_guardian(enabled=True, bundle_root=kwargs["bundle_root"],
        expected_manifest_sha256=kwargs["deployment_manifest_sha256"])
    if failure.startswith("close_"):
        _until(lambda: guardian.status()["state"] == "ACTIVE")
        guardian.request_stop(timeout=0)
    _until(lambda: guardian.status()["state"] == "HOLD")
    assert guardian.status()["owned"] is True and guardian in module._GUARDIAN_OWNERS
    assert (root / module._GUARDIAN_MARKER).is_file() and "dispose" not in native.calls
    assert native.calls.index("spawn") < (native.calls.index("resume") if "resume" in native.calls else len(native.calls))


def test_manifest_guardian_pending_job_keeps_marker_and_bounded_stop(guardian_fake):
    import time
    module, root, kwargs, native = guardian_fake
    native.active_value = 1
    guardian = module.start_manifest_guardian(enabled=True, bundle_root=kwargs["bundle_root"],
        expected_manifest_sha256=kwargs["deployment_manifest_sha256"])
    _until(lambda: guardian.status()["state"] == "ACTIVE")
    before = time.monotonic()
    result = guardian.request_stop(timeout=0.02)
    assert time.monotonic() - before < 0.5
    assert result["state"] in ("STOPPING", "HOLD") and result["owned"] is True
    assert "dispose" not in native.calls and (root / module._GUARDIAN_MARKER).is_file()


def test_guardian_cli_parent_eof_does_not_request_owner_stop(monkeypatch):
    import io
    class Done(BaseException): pass
    calls = []
    class Guardian:
        def status(self): return {"state":"ACTIVE", "owner_report":"READY"}
        def request_stop(self, **kwargs): calls.append("stop"); pytest.fail("parent EOF must not stop owner")
    monkeypatch.setattr(preflight, "start_manifest_guardian", lambda **kwargs: Guardian())
    def end_wait(seconds):
        if seconds == 0.05: raise Done()
    monkeypatch.setattr(preflight.time, "sleep", end_wait)
    with pytest.raises(Done):
        preflight._guardian_entrypoint(["--guardian", "--bundle-root", "C:\\synthetic", "--deployment-manifest-sha256", "a"*64], io.StringIO(""), io.StringIO())
    assert calls == []


def test_guardian_cli_explicit_stop_observes_settled_exit(monkeypatch):
    import io
    calls = []
    class Guardian:
        def status(self): return {"state":"ACTIVE", "owner_report":"READY"}
        def request_stop(self, **kwargs): calls.append(kwargs); return {"state":"STOPPED", "owned":False}
    monkeypatch.setattr(preflight, "start_manifest_guardian", lambda **kwargs: Guardian())
    output = io.StringIO()
    assert preflight._guardian_entrypoint(["--guardian", "--bundle-root", "C:\\synthetic", "--deployment-manifest-sha256", "a"*64], io.StringIO("STOP\n"), output) == 0
    assert calls == [{"timeout":0}] and json.loads(output.getvalue().splitlines()[-1])["state"] == "STOPPED"


def test_manifest_guardian_real_elevated_token_refuses_before_marker(guardian_launch_candidate, monkeypatch):
    module, root, kwargs = guardian_launch_candidate
    monkeypatch.setattr(module._GuardianProcessNative, "elevation", module._PipeNative.elevation)
    probe = module._GuardianProcessNative()
    try:
        probe.logon_sid(require_non_elevated=True)
    except ValueError:
        pass
    else:
        pytest.skip("runner really is non-elevated")
    finally:
        for handle in tuple(probe.handles):
            probe.close(handle)
            probe.handles.remove(handle)
    with pytest.raises(module.GuardianProcessHold) as caught:
        module.start_manifest_guardian(enabled=True, bundle_root=kwargs["bundle_root"],
            expected_manifest_sha256=kwargs["deployment_manifest_sha256"])
    owner = caught.value.guardian
    assert owner is not None and owner._runner is None and not owner._spawn_attempted
    assert list(root.iterdir()) == []
    for handle in tuple(owner._native.handles):
        owner._native.close(handle)
        owner._native.handles.remove(handle)
    module._GUARDIAN_OWNERS.discard(owner)


def test_manifest_guardian_external_job_refuses_before_marker(guardian_fake, monkeypatch):
    module, root, kwargs, native = guardian_fake
    monkeypatch.setattr(native, "in_job", lambda *args: True)
    with pytest.raises(module.GuardianProcessHold):
        module.start_manifest_guardian(enabled=True, bundle_root=kwargs["bundle_root"],
            expected_manifest_sha256=kwargs["deployment_manifest_sha256"])
    assert native.calls == [] and list(root.iterdir()) == []


def test_manifest_guardian_changed_manifest_after_owner_start_blocks_cleanup(guardian_fake):
    module, root, kwargs, native = guardian_fake
    owner = module.start_manifest_guardian(enabled=True, bundle_root=kwargs["bundle_root"],
        expected_manifest_sha256=kwargs["deployment_manifest_sha256"])
    _until(lambda: owner.status()["state"] == "ACTIVE")
    (kwargs["bundle_root"] / "deployment-manifest.json").write_bytes(b"{}")
    result = owner.request_stop(timeout=1)
    assert result["state"] == "HOLD" and result["owned"] is True
    assert "dispose" not in native.calls and (root / module._GUARDIAN_MARKER).is_file()


def test_manifest_guardian_launch_binding_mutation_never_resumes(guardian_fake, monkeypatch):
    module, root, kwargs, native = guardian_fake
    reads = []
    def inputs(*args):
        reads.append(True)
        return ("inert.exe" if len(reads) == 1 else "changed.exe", "inert-bootstrap")
    monkeypatch.setattr(module, "_guardian_launch_inputs", inputs)
    owner = module.start_manifest_guardian(enabled=True, bundle_root=kwargs["bundle_root"],
        expected_manifest_sha256=kwargs["deployment_manifest_sha256"])
    _until(lambda: owner.status()["state"] == "HOLD")
    assert native.calls == ["spawn", "assigned"]
    assert owner.status()["owned"] is True and (root / module._GUARDIAN_MARKER).is_file()


def test_manifest_guardian_second_owner_never_spawns(guardian_fake):
    module, root, kwargs, native = guardian_fake
    owner = module.start_manifest_guardian(enabled=True, bundle_root=kwargs["bundle_root"],
        expected_manifest_sha256=kwargs["deployment_manifest_sha256"])
    _until(lambda: owner.status()["state"] == "ACTIVE")
    with pytest.raises(module.GuardianProcessHold):
        module.start_manifest_guardian(enabled=True, bundle_root=kwargs["bundle_root"],
            expected_manifest_sha256=kwargs["deployment_manifest_sha256"])
    assert native.calls.count("spawn") == 1 and owner in module._GUARDIAN_OWNERS


def test_manifest_guardian_unrequested_zero_exit_is_hold(guardian_fake):
    module, root, kwargs, native = guardian_fake
    owner = module.start_manifest_guardian(enabled=True, bundle_root=kwargs["bundle_root"],
        expected_manifest_sha256=kwargs["deployment_manifest_sha256"])
    _until(lambda: owner.status()["state"] == "ACTIVE")
    native.eof.set()  # Fake owner exits without the guardian sending EOF.
    _until(lambda: owner.status()["state"] == "HOLD")
    assert not owner._eof and not owner._committed and "dispose" not in native.calls
    assert (root / module._GUARDIAN_MARKER).is_file()


def test_manifest_guardian_blocking_close_does_not_block_stop_deadline(guardian_fake, monkeypatch):
    import threading
    import time
    module, root, kwargs, native = guardian_fake
    entered, release = threading.Event(), threading.Event()
    original = native.close_pipe
    def delayed(index, end):
        if index == 0:
            entered.set()
            release.wait()
        original(index, end)
    monkeypatch.setattr(native, "close_pipe", delayed)
    owner = module.start_manifest_guardian(enabled=True, bundle_root=kwargs["bundle_root"],
        expected_manifest_sha256=kwargs["deployment_manifest_sha256"])
    try:
        _until(lambda: owner.status()["state"] == "ACTIVE")
        owner.request_stop(timeout=0)
        assert entered.wait(1)
        before = time.monotonic()
        result = owner.request_stop(timeout=0.02)
        assert time.monotonic() - before < 0.5
        assert result["owned"] is True and not owner._committed and not owner._eof
        assert (root / module._GUARDIAN_MARKER).is_file()
    finally:
        release.set()


@pytest.mark.parametrize("argv", [[], ["--guardian"], ["--owner"],
    ["--guardian","--bundle-root","C:\\synthetic","--deployment-manifest-sha256","a"*64,"--extra"]])
def test_guardian_cli_closed_arguments_refuse_without_spawn(monkeypatch, argv):
    import io
    monkeypatch.setattr(preflight, "start_manifest_guardian", lambda **kwargs: pytest.fail("invalid argv spawned"))
    output = io.StringIO()
    assert preflight._guardian_entrypoint(argv, io.StringIO(""), output) == 1
    assert json.loads(output.getvalue())["state"] == "HOLD"
