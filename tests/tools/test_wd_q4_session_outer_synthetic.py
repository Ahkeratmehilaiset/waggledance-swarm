"""Synthetic session-outer checks; no real worker, deployment or live data."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from sandbox.mind.step2 import runtime_builder
from tools import wd_q4_session_outer_synthetic as outer

ROOT = Path(__file__).resolve().parents[2]
GENERATION, REVISION = "a" * 40, "b" * 40
BINDING = {key: "a" * 64 for key in ("pointer_sha256", "source_anchor_sha256", "encoder_sha256", "policy_sha256",
                                     "roster_sha256", "public_policy_sha256", "descriptor_sha256", "projection_sha256")}
TRUST = {"lane_authentication": "UNKNOWN", "freshness": "UNKNOWN", "publisher_authentication": "UNKNOWN",
         "projection_public_attestation": "UNKNOWN", "transport": "caller_injected", "projection": "caller_pinned"}
ANSWER = {"schema": "wd.q4-lane-answer.v1", "lane": "codex-lead-1", "status": "ABSTAIN", "reason": "",
          "result_count": 0, "results": [], "binding": BINDING, "trust": TRUST}
pytestmark = pytest.mark.skipif(os.name != "nt", reason="public outer requires Windows paths")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def synthetic(tmp_path):
    runtime = tmp_path / "runtime"
    code = runtime / "assets" / "code"
    entries = {
        "tools/__init__.py": b"",
        "tools/wd_q4_public_outer.py": (ROOT / "tools/wd_q4_public_outer.py").read_bytes(),
        "tools/wd_q4_public_child.py": b"def run_public(): return None\n",
        "tools/wd_q4_public_query.py": b"def query_public(): return None\n",
        "tools/wd_q4_lane_session_cli.py": (
            "import sys,json\ndef main(argv):\n data=sys.stdin.buffer.read()\n"
            f" value={ANSWER!r}\n"
            " if data==b'extra': value['private']='must-not-leak'\n"
            " if data==b'wrong-lane': value['lane']='fable-5'\n"
            " if data==b'wrong-binding': value['binding']['policy_sha256']='b'*64\n"
            " if data==b'old-schema': value['schema']='wd.q4-public-query.v2'\n"
            " if data==b'trust-extra': value['trust']['private']='must-not-leak'\n"
            " if data==b'result-extra': value['results']=[{'private':'must-not-leak'}];value['result_count']=1;value['status']='ANSWER'\n"
            " if data==b'partial': out=b'{\\\"schema\\\":'\n"
            " else: out=json.dumps(value,sort_keys=True,separators=(',',':')).encode()+b'\\n'\n"
            " if data==b'text-write': sys.stdout.write(out.decode('ascii'))\n"
            " else: sys.stdout.buffer.write(out)\n"
            " return {b'nonzero':2,b'cleanup':3,b'timeout':4,b'interrupt':130}.get(data,0)\n").encode(),
        "sandbox/mind/step2/runtime_manifest.py": (ROOT / "sandbox/mind/step2/runtime_manifest.py").read_bytes(),
    }
    for name, data in entries.items():
        path = code / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (runtime / "site").mkdir()
    (runtime / "site" / "synthetic.py").write_bytes(b"VALUE=1\n")
    (runtime / "model").mkdir()
    (runtime / "model" / "synthetic.bin").write_bytes(b"synthetic")
    manifest = tmp_path / "runtime-manifest.json"
    built = runtime_builder.build_runtime_manifest(runtime, manifest, generation=GENERATION, revision=REVISION)
    return {"runtime_root": str(runtime), "runtime_manifest": str(manifest),
            "runtime_manifest_sha256": built["manifest_sha256"], "runtime_generation": GENERATION,
            "runtime_revision": REVISION, "verifier_sha256": sha(entries["sandbox/mind/step2/runtime_manifest.py"]),
            "outer_sha256": sha(entries["tools/wd_q4_public_outer.py"]),
            "child_sha256": sha(entries["tools/wd_q4_public_child.py"]),
            "query_sha256": sha(entries["tools/wd_q4_public_query.py"]),
            "worker_sha256": sha(entries["tools/wd_q4_lane_session_cli.py"]),
            "expected_lane": "codex-lead-1", "expected_binding": BINDING}


def run_outer(synthetic, *, enabled=True, query="ok", tamper=None, foreign_path=None):
    plan = dict(synthetic)
    if tamper:
        plan.update(tamper)
    bootstrap = ROOT / "tools/wd_q4_session_outer_synthetic.py"
    source = bootstrap.read_bytes()
    program = ("import hashlib,json,sys\n"
               f"data=open({str(bootstrap)!r},'rb').read()\n"
               f"assert hashlib.sha256(data).hexdigest()=={sha(source)!r}\n"
               "scope={'__name__':'_session_outer_bootstrap'}\n"
               "exec(compile(data,'<synthetic-outer>','exec'),scope)\n"
               + (f"sys.path.insert(0,{str(foreign_path)!r})\n" if foreign_path else "") +
               "try:\n"
               f" result=scope['run_session'](enabled={enabled!r},query={query.encode()!r},argv=['--enabled'],**{plan!r})\n"
               " print(json.dumps(result))\n"
               "except Exception as exc:\n print(json.dumps({'error':str(exc)}))\n")
    run = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", program],
                         capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


def test_disabled_does_not_import_or_inspect_pins():
    assert outer.run_session() == {"status": "OFF"}


def test_refusal_traceback_does_not_retain_query_or_arguments():
    marker = "synthetic-private-traceback-marker"
    with pytest.raises(outer.SessionOuterRefused) as caught:
        outer.run_session(enabled=True, query=marker.encode(), argv=[marker])
    tb = caught.value.__traceback__
    frames = []
    while tb:
        if tb.tb_frame.f_code.co_name == "run_session":
            frames.append(tb.tb_frame.f_locals)
        tb = tb.tb_next
    assert frames and all(marker not in repr(frame) for frame in frames)


@pytest.mark.parametrize("namespace", [False, True])
def test_import_guard_refuses_scripts_or_foreign_namespace(monkeypatch, tmp_path, namespace):
    import importlib.machinery
    alien = Path(sys.base_prefix) / "Scripts" / "pywin32_postinstall.py"
    spec = importlib.machinery.ModuleSpec("synthetic_alien", loader=None,
                                          origin=None if namespace else str(alien), is_package=namespace)
    if namespace:
        spec.submodule_search_locations = [str(Path(sys.base_prefix) / "Scripts" / "alien_ns")]
    monkeypatch.setattr(importlib.machinery.PathFinder, "find_spec", lambda *_: spec)
    guard = outer._ImportGuard(tmp_path / "code", tmp_path / "site")
    with pytest.raises(ImportError, match="session_outer_refused"):
        guard.find_spec("synthetic_alien")


@pytest.mark.parametrize("parts", [("Lib", "json", "__init__.py"), ("DLLs", "_socket.pyd"),
                                   (f"python{sys.version_info.major}{sys.version_info.minor}.zip", "json", "__init__.py")])
def test_import_guard_allows_expected_interpreter_search_roots(monkeypatch, tmp_path, parts):
    import importlib.machinery
    base = tmp_path / "synthetic-python"
    (base / "Lib/json").mkdir(parents=True)
    (base / "Lib/json/__init__.py").write_bytes(b"")
    (base / "DLLs").mkdir()
    (base / "DLLs/_socket.pyd").write_bytes(b"")
    (base / f"python{sys.version_info.major}{sys.version_info.minor}.zip").write_bytes(b"synthetic-zip")
    monkeypatch.setattr(sys, "base_prefix", str(base))
    monkeypatch.setattr(sys, "base_exec_prefix", str(base))
    path = str(base.joinpath(*parts))
    spec = importlib.machinery.ModuleSpec("synthetic_stdlib", loader=None, origin=path)
    monkeypatch.setattr(importlib.machinery.PathFinder, "find_spec", lambda *_: spec)
    assert outer._ImportGuard(tmp_path / "code", tmp_path / "site").find_spec("synthetic_stdlib") is spec


@pytest.mark.parametrize("namespace", [False, True])
def test_import_guard_rejects_shortname_site_packages_alias(monkeypatch, tmp_path, namespace):
    import importlib.machinery
    alias = Path(sys.base_prefix) / "Lib" / "SITE-P~1" / "yaml"
    spec = importlib.machinery.ModuleSpec("synthetic_shortname", loader=None,
                                          origin=None if namespace else str(alias / "__init__.py"),
                                          is_package=namespace)
    if namespace:
        spec.submodule_search_locations = [str(alias)]
    monkeypatch.setattr(importlib.machinery.PathFinder, "find_spec", lambda *_: spec)
    with pytest.raises(ImportError, match="session_outer_refused"):
        outer._ImportGuard(tmp_path / "code", tmp_path / "site").find_spec("synthetic_shortname")


def test_import_guard_rejects_reparse_in_stdlib_chain(monkeypatch, tmp_path):
    import importlib.machinery
    from types import SimpleNamespace
    base = tmp_path / "synthetic-python"
    directory = base / "Lib" / "alias"
    directory.mkdir(parents=True)
    target = directory / "module.py"
    target.write_bytes(b"")
    monkeypatch.setattr(sys, "base_prefix", str(base))
    monkeypatch.setattr(sys, "base_exec_prefix", str(base))
    spec = importlib.machinery.ModuleSpec("synthetic_reparse", loader=None, origin=str(target))
    monkeypatch.setattr(importlib.machinery.PathFinder, "find_spec", lambda *_: spec)
    original = os.lstat
    def lstat(path, *args, **kwargs):
        info = original(path, *args, **kwargs)
        if os.path.normcase(str(path)) == os.path.normcase(str(directory)):
            return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
        return info
    monkeypatch.setattr(os, "lstat", lstat)
    with pytest.raises(ImportError, match="session_outer_refused"):
        outer._ImportGuard(tmp_path / "code", tmp_path / "site").find_spec("synthetic_reparse")


def test_actual_stdlib_zip_import_works_through_guard(monkeypatch, tmp_path):
    import importlib.machinery
    import importlib.util
    import zipfile
    base = tmp_path / "synthetic-python"
    base.mkdir()
    archive = base / f"python{sys.version_info.major}{sys.version_info.minor}.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("q4_synthetic_zip_probe.py", "VALUE = 7\n")
    monkeypatch.setattr(sys, "base_prefix", str(base))
    monkeypatch.setattr(sys, "base_exec_prefix", str(base))
    monkeypatch.setattr(sys, "path", [str(archive)] + sys.path)
    spec = outer._ImportGuard(tmp_path / "code", tmp_path / "site").find_spec("q4_synthetic_zip_probe")
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.VALUE == 7


def test_manifest_bound_synthetic_worker_complete_answer(synthetic):
    assert run_outer(synthetic) == ANSWER


def test_worker_text_write_uses_lf_not_windows_crlf(synthetic):
    assert run_outer(synthetic, query="text-write") == ANSWER


@pytest.mark.parametrize("key", ["runtime_manifest_sha256", "verifier_sha256", "outer_sha256",
                                  "worker_sha256"])
def test_invalid_pin_is_refused(synthetic, key):
    assert run_outer(synthetic, tamper={key: "0" * 64}) == {"error": "session_outer_refused"}


def test_foreign_import_path_is_refused(synthetic, tmp_path):
    assert run_outer(synthetic, foreign_path=tmp_path) == {"error": "session_outer_refused"}


def test_incomplete_json_is_refused(synthetic):
    assert run_outer(synthetic, query="partial") == {"error": "session_outer_refused"}


def test_complete_json_with_nonzero_exit_is_refused(synthetic):
    assert run_outer(synthetic, query="nonzero") == {"error": "session_outer_refused"}


@pytest.mark.parametrize("query", ["extra", "wrong-lane", "wrong-binding", "old-schema", "trust-extra", "result-extra"])
def test_answer_rejects_private_fields_wrong_identity_pins_and_schema(synthetic, query):
    assert run_outer(synthetic, query=query) == {"error": "session_outer_refused"}


@pytest.mark.parametrize("query,reason", [("cleanup", "worker_cleanup_unknown"), ("timeout", "worker_timeout"),
                                         ("interrupt", "worker_interrupted")])
def test_worker_critical_states_are_distinct_without_returning_stdout(synthetic, query, reason):
    assert run_outer(synthetic, query=query) == {"error": reason}


def test_real_session_cli_output_matches_closed_lane_validator(tmp_path, monkeypatch, capsys):
    import io
    from tools import wd_q4_lane_query, wd_q4_lane_session_cli as cli

    value, error = wd_q4_lane_query._answer("codex-lead-1", {"status": "ABSTAIN", "binding": BINDING,
                                                             "results": []}, BINDING, {}, None)
    assert error is None and value == ANSWER
    options = {"lane": "codex-lead-1", "bundle-root": str(tmp_path), "public-plan": str(tmp_path / "plan"),
               "interpreter": str(tmp_path / "python.exe"), "interpreter-sha256": "a" * 64,
               "interpreter-max-bytes": "10", "cwd": str(tmp_path), "timeout-seconds": "1",
               "max-output-bytes": "1000"}
    for name in cli._FILES:
        options[name] = str(tmp_path / name)
        options[name + "-sha256"] = "a" * 64
    monkeypatch.setattr(cli, "_parse", lambda _: options)
    monkeypatch.setattr(cli, "_file", lambda *_: b"synthetic")
    monkeypatch.setattr(cli.session, "query_session", lambda **_: value)
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"synthetic question"), encoding="utf-8"))
    assert cli.main(["--enabled"]) == 0
    raw = capsys.readouterr().out
    assert outer._validate_lane_answer(json.loads(raw), "codex-lead-1", BINDING) == ANSWER


def _projected_answer(task, *, include_refs=False):
    from sandbox.mind.step2 import shared_projection as projection
    from sandbox.mind.step2.test_status_public_filter import ROSTER
    from tools import wd_q4_lane_query

    event = {"ts_utc": "2026-11-01T12:00:12Z", "agent": "codex-lead-1", "agent_uuid": ROSTER["codex-lead-1"],
             "type": "message", "status": "done", "task_id": task, "ci": "green", "pr": 123,
             "head": "a" * 40}
    projected = projection.project_events([event], approved_tasks=frozenset({task}), roster=ROSTER,
                                          include_refs=include_refs, exclude_review_signal=True)
    assert len(projected["records"]) == 1
    record = projected["records"][0]
    ref = sha(record["provenance"]["event_ref"].encode())
    doc = sha(record["record_id"].encode())
    item = {"ref_sha256": ref, "doc_id_sha256": doc, "stored_text": record["stored_text"]}
    value = {"status": "ANSWER", "binding": BINDING, "results": [item]}
    answer, reason = wd_q4_lane_query._answer("codex-lead-1", value, BINDING, {(ref, doc): record},
                                             "2026-11-01T12:10:00Z")
    return record, answer, reason


def test_real_projected_metadata_answer_passes_current_chain():
    record, answer, reason = _projected_answer("m0-snapshot-ready")
    assert reason is None and answer["status"] == "ANSWER"
    assert answer["results"][0]["stored_text"] == record["stored_text"]
    assert "ci:green" in record["stored_text"] and "head:" not in record["stored_text"]
    assert outer._validate_lane_answer(answer, "codex-lead-1", BINDING) == answer


@pytest.mark.parametrize("task", ["abcdef0123456789abcdef0123456789", "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"])
def test_valid_approved_task_can_be_refused_by_existing_text_filter(task):
    record, answer, reason = _projected_answer(task)
    assert task in record["stored_text"]
    assert answer is None and reason == "result_refused"


def test_opt_in_head_text_does_not_pass_current_lane_answer_filter():
    from sandbox.mind.step2 import shared_projection as projection
    from sandbox.mind.step2.test_status_public_filter import ROSTER
    event = {"ts_utc": "2026-11-01T12:00:12Z", "agent": "codex-lead-1", "agent_uuid": ROSTER["codex-lead-1"],
             "type": "message", "status": "done", "task_id": "m0-snapshot-ready", "head": "a" * 40, "pr": 123}
    projected = projection.project_events([event], approved_tasks=frozenset({"m0-snapshot-ready"}), roster=ROSTER,
                                          include_refs=True, exclude_review_signal=True)
    with pytest.raises(projection.ProjectionRefused):
        projection.validate_projection(projected, approved_tasks=frozenset({"m0-snapshot-ready"}), roster=ROSTER,
                                       include_refs=False, exclude_review_signal=True)
    # This direct _answer call is a defense-in-depth test only; the real path
    # rejects the reference-bearing projection first, before answer creation.
    record, answer, reason = _projected_answer("m0-snapshot-ready", include_refs=True)
    assert "head:" + "a" * 40 in record["stored_text"]
    assert answer is None and reason == "result_refused"


def test_worker_cannot_import_outside_the_verified_roots(synthetic, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "executed"
    (outside / "alien.py").write_text(f"open({str(marker)!r},'w').write('bad')\n", encoding="utf-8")
    worker = Path(synthetic["runtime_root"]) / "assets/code/tools/wd_q4_lane_session_cli.py"
    worker.write_text(f"import sys\nsys.path.insert(0,{str(outside)!r})\nimport alien\n", encoding="utf-8")
    manifest = tmp_path / "new-runtime-manifest.json"
    built = runtime_builder.build_runtime_manifest(Path(synthetic["runtime_root"]), manifest,
                                                    generation=GENERATION, revision=REVISION)
    modified = dict(synthetic, runtime_manifest=str(manifest), runtime_manifest_sha256=built["manifest_sha256"],
                    worker_sha256=sha(worker.read_bytes()))
    assert run_outer(modified) == {"error": "session_outer_refused"}
    assert not marker.exists()
