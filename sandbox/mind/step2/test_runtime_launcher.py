"""Synthetic launcher checks; no real memory, network, installation or model."""

import hashlib
import io
import json
import os
from pathlib import Path
import sys
import time

import pytest

from sandbox.mind.step2 import runtime_launcher as launcher


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows job containment required")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@pytest.fixture
def candidate(tmp_path):
    code = tmp_path / "code"
    code.mkdir()
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    bootstrap = code / "runtime_bootstrap.py"
    bootstrap.write_text("""import json,sys\nsys.stdin.buffer.read()\nprint(json.dumps({'schema':'wd.bridge-mind-q4-private-read.v1','identity':'caller_declared_not_authenticated','status':'REFUSED','reason':'reader_busy','results':[]}))\nprint('private stderr',file=sys.stderr)\nsys.exit(2)\n""", encoding="utf-8")
    verifier = code / "runtime_manifest.py"
    verifier.write_text("# synthetic only\n", encoding="utf-8")
    return dict(interpreter=Path(sys.executable), interpreter_sha256=digest(sys.executable),
                bootstrap=bootstrap, bootstrap_sha256=digest(bootstrap), verifier_sha256=digest(verifier),
                scratch_root=scratch,
                tuple_args={"runtime-root": str(runtime), "runtime-manifest": str(tmp_path / "manifest.json"),
                            "runtime-manifest-sha256": "a" * 64, "runtime-generation": "generation",
                            "runtime-revision": "revision", "memory": str(tmp_path / "memory"),
                            "descriptor": str(tmp_path / "descriptor.json"), "descriptor-sha256": "b" * 64,
                            "lock-file": str(tmp_path / "read.lock"), "lock-timeout": "1"},
                query=b'{"situation":"synthetic","time":"2026-01-01T00:00:00Z"}', timeout=5)


def test_private_stderr_suppressed_and_stdin_not_in_args(candidate, capsys):
    result = launcher.launch(**candidate)
    assert result == {"schema": "wd.bridge-mind-q4-private-read.v1", "identity": "caller_declared_not_authenticated",
                      "status": "REFUSED", "reason": "reader_busy", "results": []}
    assert capsys.readouterr().err == ""


def test_query_is_delivered_only_on_stdin_in_isolated_mode(candidate):
    candidate["bootstrap"].write_text(
        "import json,sys\n"
        "query=sys.stdin.buffer.read()\n"
        "assert b'synthetic_secret' in query and all('synthetic_secret' not in a for a in sys.argv)\n"
        "assert sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode\n"
        "print(json.dumps({'schema':'wd.bridge-mind-q4-private-read.v1',"
        "'identity':'caller_declared_not_authenticated','status':'REFUSED','reason':'reader_busy','results':[]}))\n"
        "sys.exit(2)\n", encoding="utf-8")
    candidate["bootstrap_sha256"] = digest(candidate["bootstrap"])
    candidate["query"] = b'{"situation":"synthetic_secret","time":"2026-01-01T00:00:00Z"}'
    assert launcher.launch(**candidate)["status"] == "REFUSED"


def test_extra_private_output_fields_are_discarded(candidate):
    candidate["bootstrap"].write_text(
        "import json,sys\n"
        "print(json.dumps({'schema':'wd.bridge-mind-q4-private-read.v1',"
        "'identity':'caller_declared_not_authenticated','status':'REFUSED','reason':'reader_busy',"
        "'results':[], 'extra_private_field':'private text'}))\n"
        "sys.exit(2)\n", encoding="utf-8")
    candidate["bootstrap_sha256"] = digest(candidate["bootstrap"])
    assert "private" not in json.dumps(launcher.launch(**candidate)).replace("private-read", "")


def test_duplicate_json_keys_are_rejected(candidate):
    candidate["bootstrap"].write_text(
        "print('{\\\"status\\\":\\\"REFUSED\\\",\\\"status\\\":\\\"ANSWER\\\"}')\n", encoding="utf-8")
    candidate["bootstrap_sha256"] = digest(candidate["bootstrap"])
    with pytest.raises(launcher.LaunchRefused, match="invalid_output"):
        launcher.launch(**candidate)


def test_projected_result_keeps_only_hash_checked_refs(candidate):
    ref = "a" * 64
    digest_ref = hashlib.sha256(ref.encode()).hexdigest()
    doc = "b" * 64
    payload = {"schema": "wd.bridge-mind-q4-private-read.v1", "identity": "caller_declared_not_authenticated",
               "status": "ANSWER", "reason": None, "rule": "q4-rule-v1.2", "private": "discard me",
               "binding": {"descriptor_sha256": "b" * 64, "memory_sha256": "c" * 64,
                           "scope": "wd.q4-shared-projected-events.v1"},
               "results": [{"ref": ref, "ref_sha256": digest_ref, "doc_id": doc,
                            "doc_id_sha256": hashlib.sha256(doc.encode()).hexdigest(),
                            "kind": "event", "cell": "cell1", "tier": "private", "time_utc": None,
                            "ref_kind": "sha256_of_projected_metadata", "canonical_event": False,
                            "score": 1.0, "fused": 0.5, "extra": "discard me"}]}
    result = launcher._validate_output(json.dumps(payload).encode(), 0, "b" * 64)
    assert result["results"][0]["ref_sha256"] == digest_ref
    assert "ref" not in result["results"][0]
    assert "discard me" not in json.dumps(result)
    payload["results"][0]["ref_sha256"] = "0" * 64
    with pytest.raises(launcher.LaunchRefused, match="invalid_output"):
        launcher._validate_output(json.dumps(payload).encode(), 0, "b" * 64)


def test_untrusted_tokens_are_never_forwarded(candidate):
    ref = "a" * 64
    item = {"ref": ref, "ref_sha256": hashlib.sha256(ref.encode()).hexdigest(), "doc_id": None,
            "doc_id_sha256": "b" * 64, "kind": "event", "cell": "secret_cell",
            "tier": "secret_tier", "time_utc": None, "ref_kind": "sha256_of_projected_metadata",
            "canonical_event": False,
            "score": 1, "fused": 1}
    payload = {"schema": "wd.bridge-mind-q4-private-read.v1", "identity": "caller_declared_not_authenticated",
               "status": "ANSWER", "reason": "secret_reason", "rule": "secret_rule",
               "binding": {"descriptor_sha256": "b" * 64, "memory_sha256": "c" * 64,
                           "scope": "wd.q4-shared-projected-events.v1"}, "results": [item]}
    result = launcher._validate_output(json.dumps(payload).encode(), 0, "b" * 64)
    assert "secret_" not in json.dumps(result)


def test_unknown_refusal_reason_fails_closed(candidate):
    payload = {"schema": "wd.bridge-mind-q4-private-read.v1", "identity": "caller_declared_not_authenticated",
               "status": "REFUSED", "reason": "secret_reason", "results": []}
    with pytest.raises(launcher.LaunchRefused, match="^invalid_output$"):
        launcher._validate_output(json.dumps(payload).encode(), 2, "b" * 64)


def test_known_reader_refusal_survives_and_unknown_bootstrap_refuses():
    payload = {"schema": "wd.bridge-mind-q4-private-read.v1", "identity": "caller_declared_not_authenticated",
               "status": "REFUSED", "reason": "reader_busy", "results": []}
    assert launcher._validate_output(json.dumps(payload).encode(), 2, "b" * 64)["reason"] == "reader_busy"
    bootstrap = {"schema": "wd.q4-runtime-bootstrap.v1", "status": "REFUSED", "reason": "synthetic_secret"}
    with pytest.raises(launcher.LaunchRefused, match="^invalid_output$"):
        launcher._validate_output(json.dumps(bootstrap).encode(), 2, "b" * 64)


def test_scratch_must_be_explicit(candidate):
    del candidate["scratch_root"]
    with pytest.raises(launcher.LaunchRefused, match="^scratch_required$"):
        launcher.launch(**candidate)


@pytest.fixture
def identity_candidate(tmp_path):
    """A private fixture with synthetic executable bytes; no host Python reads."""
    code = tmp_path / "code"
    code.mkdir()
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    interpreter = tmp_path / "synthetic-python.exe"
    interpreter.write_bytes(b"synthetic-interpreter-never-executed")
    bootstrap = code / "runtime_bootstrap.py"
    bootstrap.write_bytes(b"synthetic-bootstrap-never-executed")
    verifier = code / "runtime_manifest.py"
    verifier.write_bytes(b"synthetic-verifier-never-executed")
    return dict(interpreter=interpreter, interpreter_sha256=digest(interpreter),
                bootstrap=bootstrap, bootstrap_sha256=digest(bootstrap), verifier_sha256=digest(verifier),
                scratch_root=scratch, tuple_args={"runtime-root": str(tmp_path / "runtime"),
                "runtime-manifest": str(tmp_path / "manifest.json"), "runtime-manifest-sha256": "a" * 64,
                "runtime-generation": "generation", "runtime-revision": "revision",
                "memory": str(tmp_path / "memory"), "descriptor": str(tmp_path / "descriptor.json"),
                "descriptor-sha256": "b" * 64, "lock-file": str(tmp_path / "read.lock"),
                "lock-timeout": "1"}, query=b"synthetic-query", timeout=5)


@pytest.mark.parametrize("identity", [{}, {"root": "synthetic-private"},
    {"root": "synthetic-private", "descriptor_bytes": b"sensitive", "expected_sha256": "a" * 64, "extra": 1},
    {"root": "synthetic-private", "descriptor_bytes": "not-bytes", "expected_sha256": "a" * 64}])
def test_interpreter_identity_invalid_before_scratch_or_process(identity_candidate, monkeypatch, identity):
    candidate = identity_candidate
    monkeypatch.setattr(launcher, "_scratch_dir", lambda *_: pytest.fail("scratch created"))
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda *_a, **_k: pytest.fail("process created"))
    with pytest.raises(launcher.LaunchRefused, match="^interpreter_identity_refused$") as caught:
        launcher.launch(**candidate, interpreter_identity=identity)
    assert caught.value.__context__ is None
    assert "synthetic-private" not in repr(caught.value)


@pytest.mark.parametrize("verdict", ["raise", "mismatch", "off"])
def test_interpreter_identity_verifier_refusal_is_fixed_and_precedes_side_effects(identity_candidate, monkeypatch, verdict):
    candidate = identity_candidate
    from sandbox.mind.step2 import interpreter_identity as identity_module

    def verify(**kwargs):
        assert kwargs["expected_interpreter"] == candidate["interpreter"]
        assert kwargs["expected_interpreter_sha256"] == candidate["interpreter_sha256"]
        if verdict in ("raise", "mismatch"):
            raise ValueError("synthetic-private verifier detail")
        return {"status": "OFF"}

    monkeypatch.setattr(identity_module, "verify_interpreter_identity", verify)
    monkeypatch.setattr(launcher, "_scratch_dir", lambda *_: pytest.fail("scratch created"))
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda *_a, **_k: pytest.fail("process created"))
    identity = {"root": "synthetic-private", "descriptor_bytes": b"sensitive", "expected_sha256": "a" * 64}
    with pytest.raises(launcher.LaunchRefused, match="^interpreter_identity_refused$") as caught:
        launcher.launch(**candidate, interpreter_identity=identity)
    assert caught.value.__context__ is None
    assert "synthetic-private" not in repr(caught.value)


@pytest.mark.parametrize("mode", ["omitted", "none"])
def test_legacy_without_interpreter_identity_does_not_invoke_verifier(identity_candidate, monkeypatch, mode):
    from sandbox.mind.step2 import interpreter_identity as identity_module
    monkeypatch.setattr(identity_module, "verify_interpreter_identity", lambda **_: pytest.fail("unexpected verifier"))
    monkeypatch.setattr(launcher, "_scratch_dir", lambda *_: (_ for _ in ()).throw(launcher.LaunchRefused("synthetic_ready")))
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda *_a, **_k: pytest.fail("process created"))
    kwargs = {} if mode == "omitted" else {"interpreter_identity": None}
    with pytest.raises(launcher.LaunchRefused, match="^synthetic_ready$"):
        launcher.launch(**identity_candidate, **kwargs)


def test_interpreter_identity_root_cannot_contain_scratch(identity_candidate, tmp_path, monkeypatch):
    from sandbox.mind.step2 import interpreter_identity as identity_module
    candidate = identity_candidate
    tree = tmp_path / "identity-tree"
    interpreter_dir = tree / "bin"
    interpreter_dir.mkdir(parents=True)
    interpreter = interpreter_dir / "python.exe"
    interpreter.write_bytes(b"synthetic-interpreter-never-executed")
    scratch = tree / "scratch"
    scratch.mkdir()
    candidate.update(interpreter=interpreter, interpreter_sha256=digest(interpreter), scratch_root=scratch)
    monkeypatch.setattr(identity_module, "verify_interpreter_identity", lambda **_: {"status": "VERIFIED"})
    monkeypatch.setattr(launcher.tempfile, "mkdtemp", lambda **_: pytest.fail("scratch child created"))
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda *_a, **_k: pytest.fail("process created"))
    identity = {"root": tree, "descriptor_bytes": b"synthetic", "expected_sha256": "a" * 64}
    with pytest.raises(launcher.LaunchRefused, match="^scratch_refused$"):
        launcher.launch(**candidate, interpreter_identity=identity)
    assert not list(scratch.iterdir())


def test_matching_interpreter_identity_reaches_only_spied_transport_without_private_argv(identity_candidate, monkeypatch):
    candidate = identity_candidate
    from sandbox.mind.step2 import interpreter_identity as identity_module
    calls = []
    private_root = str(candidate["scratch_root"].parent / "synthetic-private-identity-root")
    private_bytes = b"synthetic-private-descriptor"
    identity = {"root": private_root, "descriptor_bytes": private_bytes, "expected_sha256": "a" * 64}

    def verify(**kwargs):
        calls.append(("verify", kwargs))
        assert kwargs == {**identity, "enabled": True, "expected_interpreter": candidate["interpreter"],
                          "expected_interpreter_sha256": candidate["interpreter_sha256"]}
        return {"status": "VERIFIED"}

    class Job:
        def attach(self, process): calls.append(("attach", process))
        def terminate(self): pass
        def close(self): pass

    class Stream(io.BytesIO):
        def close(self): pass

    class Process:
        def __init__(self):
            self.stdin, self.stdout, self.returncode = Stream(), Stream(b"synthetic"), 2
        def wait(self, timeout): return self.returncode
        def poll(self): return self.returncode

    def popen(args, **kwargs):
        calls.append(("popen", args, kwargs))
        assert private_root not in repr((args, kwargs))
        assert private_bytes.decode() not in repr((args, kwargs))
        return Process()

    monkeypatch.setattr(identity_module, "verify_interpreter_identity", verify)
    monkeypatch.setattr(launcher, "_scratch_dir", lambda *_: candidate["scratch_root"])
    monkeypatch.setattr(launcher, "_Job", Job)
    monkeypatch.setattr(launcher.subprocess, "Popen", popen)
    monkeypatch.setattr(launcher, "_validate_output", lambda *_: {"status": "REFUSED"})
    assert launcher.launch(**candidate, interpreter_identity=identity) == {"status": "REFUSED"}
    assert [call[0] for call in calls] == ["verify", "popen", "attach"]


@pytest.mark.parametrize("change,expected", [("none", "synthetic_ready"),
    ("descriptor_pin", "interpreter_identity_refused"), ("interpreter", "interpreter_identity_refused"),
    ("stdlib_contents", "interpreter_identity_refused")])
def test_real_interpreter_verifier_is_bound_before_scratch(identity_candidate, tmp_path, monkeypatch, change, expected):
    candidate = identity_candidate
    root = tmp_path / "synthetic-interpreter-tree"
    root.mkdir()
    contents = {"python.exe": ("interpreter", b"not-an-executable"),
                "stdlib.zip": ("stdlib", b"not-a-zip"), "library.dll": ("dll", b"not-a-dll")}
    records = []
    for name, (role, data) in contents.items():
        (root / name).write_bytes(data)
        records.append({"role": role, "path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    candidate["interpreter"] = root / "python.exe"
    candidate["interpreter_sha256"] = digest(candidate["interpreter"])
    raw = json.dumps({"schema": "wd.q4-interpreter-identity.v1", "files": records}).encode()
    pin = hashlib.sha256(raw).hexdigest()
    if change == "descriptor_pin": pin = "0" * 64
    elif change == "interpreter":
        candidate["interpreter"] = tmp_path / "synthetic-python.exe"
        candidate["interpreter_sha256"] = digest(candidate["interpreter"])
    elif change == "stdlib_contents": (root / "stdlib.zip").write_bytes(b"mutated!!")
    reached = []
    def scratch(*_):
        reached.append(True)
        raise launcher.LaunchRefused("synthetic_ready")
    monkeypatch.setattr(launcher, "_scratch_dir", scratch)
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda *_a, **_k: pytest.fail("process created"))
    with pytest.raises(launcher.LaunchRefused, match="^" + expected + "$"):
        launcher.launch(**candidate, interpreter_identity={"root": root, "descriptor_bytes": raw, "expected_sha256": pin})
    assert bool(reached) is (change == "none")


def test_fresh_temp_and_tmp_inside_explicit_scratch(candidate, monkeypatch):
    monkeypatch.setenv("TEMP", "synthetic-parent-temp")
    monkeypatch.setenv("TMP", "synthetic-parent-tmp")
    monkeypatch.setenv("WD_Q4_SECRET", "synthetic-parent-secret")
    candidate["bootstrap"].write_text(
        "import json,os,pathlib,sys,tempfile\n"
        "assert 'WD_Q4_SECRET' not in os.environ\n"
        "temp=pathlib.Path(os.environ['TEMP'])\n"
        "assert temp==pathlib.Path(os.environ['TMP']) and temp.is_dir()\n"
        "assert temp.parent==pathlib.Path(" + repr(str(candidate["scratch_root"])) + ")\n"
        "assert pathlib.Path(tempfile.gettempdir())==temp\n"
        "print(json.dumps({'schema':'wd.bridge-mind-q4-private-read.v1',"
        "'identity':'caller_declared_not_authenticated','status':'REFUSED','reason':'reader_busy','results':[]}))\n"
        "sys.exit(2)\n", encoding="utf-8")
    candidate["bootstrap_sha256"] = digest(candidate["bootstrap"])
    before = set(candidate["scratch_root"].iterdir())
    assert launcher.launch(**candidate)["reason"] == "reader_busy"
    assert launcher.launch(**candidate)["reason"] == "reader_busy"
    after = set(candidate["scratch_root"].iterdir())
    assert len(after - before) == 2
    assert os.environ["TEMP"] == "synthetic-parent-temp"
    assert os.environ["TMP"] == "synthetic-parent-tmp"


@pytest.mark.parametrize("target", ["code", "runtime", "memory", "common_ancestor"])
def test_scratch_must_not_overlap_protected_directories(candidate, target):
    memory = Path(candidate["tuple_args"]["memory"])
    memory.mkdir()
    protected = {"code": candidate["bootstrap"].parent,
                 "runtime": Path(candidate["tuple_args"]["runtime-root"]),
                 "memory": memory, "common_ancestor": candidate["scratch_root"].parent}
    candidate["scratch_root"] = protected[target]
    with pytest.raises(launcher.LaunchRefused, match="^scratch_refused$"):
        launcher.launch(**candidate)
    assert not list(protected[target].glob("wd-q4-*"))


def test_scratch_reparse_point_is_refused_before_creating_child(candidate, monkeypatch):
    from types import SimpleNamespace
    path = candidate["scratch_root"]
    original = launcher.os.lstat

    def fake_lstat(value, *args, **kwargs):
        result = original(value, *args, **kwargs)
        if Path(value) == path:
            return SimpleNamespace(st_mode=result.st_mode, st_file_attributes=0x400)
        return result

    monkeypatch.setattr(launcher.os, "lstat", fake_lstat)
    with pytest.raises(launcher.LaunchRefused, match="^scratch_refused$"):
        launcher.launch(**candidate)
    assert not list(path.iterdir())


@pytest.mark.parametrize("reason", sorted(launcher._BOOTSTRAP_REFUSALS))
def test_only_known_bootstrap_refusal_codes_are_forwarded(reason):
    value = {"schema": "wd.q4-runtime-bootstrap.v1", "status": "REFUSED", "reason": reason}
    assert launcher._validate_output(json.dumps(value).encode(), 2, "b" * 64)["reason"] == reason


def test_early_missing_import_refusal_without_identity_is_fixed_shape():
    early = {"schema": "wd.bridge-mind-q4-private-read.v1", "status": "REFUSED",
             "reason": "runtime_unavailable", "results": []}
    result = launcher._validate_output(json.dumps(early).encode(), 2, "b" * 64)
    assert result == early
    for alteration, code in (({"reason": "secret_reason"}, 2), ({"extra": "private"}, 2),
                             ({"results": ["private"]}, 2), ({"status": "ANSWER"}, 2), ({}, 0)):
        mutated = dict(early, **alteration)
        with pytest.raises(launcher.LaunchRefused, match="invalid_output"):
            launcher._validate_output(json.dumps(mutated).encode(), code, "b" * 64)


def test_correct_shared_scope_is_required_and_forwarded():
    scope = "wd.q4-shared-projected-events.v1"
    payload = {"schema": "wd.bridge-mind-q4-private-read.v1", "identity": "caller_declared_not_authenticated",
               "status": "ABSTAIN", "rule": "v1", "binding": {"descriptor_sha256": "b" * 64,
               "memory_sha256": "c" * 64, "scope": scope}, "results": []}
    assert launcher._validate_output(json.dumps(payload).encode(), 0, "b" * 64)["scope"] == scope
    for bad_scope in (None, "wd.q4-private-memory.v1", "wd.q4-shared-projected-events.v2"):
        if bad_scope is None:
            payload["binding"].pop("scope", None)
        else:
            payload["binding"]["scope"] = bad_scope
        with pytest.raises(launcher.LaunchRefused, match="invalid_output"):
            launcher._validate_output(json.dumps(payload).encode(), 0, "b" * 64)


def test_legacy_reference_is_refused_even_with_shared_binding():
    ref = "a" * 64
    item = {"ref": ref, "ref_sha256": hashlib.sha256(ref.encode()).hexdigest(), "doc_id": None,
            "doc_id_sha256": "b" * 64, "kind": "event", "cell": "c", "tier": "t", "time_utc": None,
            "ref_kind": None, "canonical_event": None, "score": 1, "fused": 1}
    payload = {"schema": "wd.bridge-mind-q4-private-read.v1", "identity": "caller_declared_not_authenticated",
               "status": "ANSWER", "rule": "v1", "binding": {"descriptor_sha256": "b" * 64,
               "memory_sha256": "c" * 64, "scope": "wd.q4-shared-projected-events.v1"}, "results": [item]}
    with pytest.raises(launcher.LaunchRefused, match="invalid_output"):
        launcher._validate_output(json.dumps(payload).encode(), 0, "b" * 64)


@pytest.mark.parametrize("kind,ref", [("lesson", "a" * 64), ("event", "a" * 40),
                                      ("event", "event:" + "a" * 64), ("event", None)])
def test_shared_binding_rejects_nonprojected_reference(kind, ref):
    item = {"ref": ref, "ref_sha256": hashlib.sha256((ref or "empty").encode()).hexdigest(), "doc_id": None,
            "doc_id_sha256": "b" * 64, "kind": kind, "cell": "c", "tier": "t", "time_utc": None,
            "ref_kind": "sha256_of_projected_metadata", "canonical_event": False, "score": 1, "fused": 1}
    payload = {"schema": "wd.bridge-mind-q4-private-read.v1", "identity": "caller_declared_not_authenticated",
               "status": "ANSWER", "rule": "v1", "binding": {"descriptor_sha256": "b" * 64,
               "memory_sha256": "c" * 64, "scope": "wd.q4-shared-projected-events.v1"}, "results": [item]}
    with pytest.raises(launcher.LaunchRefused, match="invalid_output"):
        launcher._validate_output(json.dumps(payload).encode(), 0, "b" * 64)


@pytest.mark.parametrize("canonical", [0, True, None])
def test_projected_reference_requires_explicit_false(candidate, canonical):
    ref = "a" * 64
    item = {"ref": ref, "ref_sha256": hashlib.sha256(ref.encode()).hexdigest(), "doc_id": None,
            "doc_id_sha256": "b" * 64, "kind": "event", "cell": "c", "tier": "t", "time_utc": None,
            "ref_kind": "sha256_of_projected_metadata", "canonical_event": canonical, "score": 1, "fused": 1}
    payload = {"schema": "wd.bridge-mind-q4-private-read.v1", "identity": "caller_declared_not_authenticated",
               "status": "ANSWER", "rule": "v1", "binding": {"descriptor_sha256": "b" * 64,
               "memory_sha256": "c" * 64, "scope": "wd.q4-shared-projected-events.v1"}, "results": [item]}
    with pytest.raises(launcher.LaunchRefused, match="invalid_output"):
        launcher._validate_output(json.dumps(payload).encode(), 0, "b" * 64)


@pytest.mark.parametrize("key", ["bootstrap_sha256", "verifier_sha256", "interpreter_sha256"])
def test_modified_trusted_file_refuses_before_launch(candidate, key):
    candidate[key] = "0" * 64
    with pytest.raises(launcher.LaunchRefused, match="trust_pin_mismatch"):
        launcher.launch(**candidate)


def test_nonlocal_and_relative_paths_refused(candidate):
    candidate["tuple_args"]["memory"] = "relative-memory"
    with pytest.raises(launcher.LaunchRefused, match="invalid_path"):
        launcher.launch(**candidate)


def test_invalid_output_is_not_forwarded(candidate):
    candidate["bootstrap"].write_text("print('private output should not propagate')", encoding="utf-8")
    candidate["bootstrap_sha256"] = digest(candidate["bootstrap"])
    with pytest.raises(launcher.LaunchRefused, match="invalid_output"):
        launcher.launch(**candidate)


def test_oversized_output_is_refused(candidate):
    candidate["bootstrap"].write_text("print('x'*3000000)", encoding="utf-8")
    candidate["bootstrap_sha256"] = digest(candidate["bootstrap"])
    with pytest.raises(launcher.LaunchRefused, match="output_limit"):
        launcher.launch(**candidate)


def test_watchdog_terminates_parent_and_descendant(candidate, tmp_path):
    marker = tmp_path / "descendant-ran.txt"
    candidate["bootstrap"].write_text(
        "import subprocess,sys,time\n"
        "subprocess.Popen([sys.executable, '-c', "
        + repr("import pathlib,time; time.sleep(2); pathlib.Path(" + repr(str(marker)) + ").write_text('late')")
        + "])\ntime.sleep(30)\n", encoding="utf-8")
    candidate["bootstrap_sha256"] = digest(candidate["bootstrap"])
    candidate["timeout"] = 0.5
    with pytest.raises(launcher.LaunchRefused, match="watchdog_timeout"):
        launcher.launch(**candidate)
    time.sleep(3)
    assert not marker.exists()


def test_query_shape_and_watchdog_are_bounded(candidate):
    candidate["query"] = b"x" * 65537
    with pytest.raises(launcher.LaunchRefused, match="invalid_query"):
        launcher.launch(**candidate)
    candidate["query"] = b"{}"
    candidate["timeout"] = 1801
    with pytest.raises(launcher.LaunchRefused, match="invalid_timeout"):
        launcher.launch(**candidate)
