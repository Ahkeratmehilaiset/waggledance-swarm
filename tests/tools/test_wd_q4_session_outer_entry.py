"""Synthetic session entry tests: no live data, lane process or deployment."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
ENTRY = ROOT / "tools/wd_q4_session_outer_entry.py"
KEYS = ("pointer_sha256", "source_anchor_sha256", "encoder_sha256", "policy_sha256", "roster_sha256",
        "public_policy_sha256", "descriptor_sha256", "projection_sha256")
FAKE_OUTER = b'''\
class SessionOuterCleanupUnknown(Exception): pass
class SessionOuterTimeout(Exception): pass
class SessionOuterInterrupted(Exception): pass
def run_session(**kw):
    assert kw['enabled'] is True and kw['expected_lane'] == 'codex-lead-1'
    assert kw['expected_binding'] == {k:'a'*64 for k in __keys__}
    assert kw['argv'][0] == '--enabled'
    opts=dict(zip(kw['argv'][1::2],kw['argv'][2::2]))
    assert opts['--runtime-manifest'] == kw['runtime_manifest']
    assert opts['--runtime-manifest-sha256'] == kw['runtime_manifest_sha256']
    assert opts['--lane'] == kw['expected_lane']
    assert kw['worker_sha256'] == __worker_sha__
    if kw['query'] == b'cleanup': raise SessionOuterCleanupUnknown('private-sentinel')
    if kw['query'] == b'timeout': raise SessionOuterTimeout('private-sentinel')
    if kw['query'] == b'interrupted': raise SessionOuterInterrupted('private-sentinel')
    if kw['query'] == b'bad-result': return {'extra':'private-sentinel'}
    return {'schema':'wd.q4-lane-answer.v1','lane':kw['expected_lane'],'status':'ABSTAIN'}
def _validate_lane_answer(value, lane, binding):
    if value != {'schema':'wd.q4-lane-answer.v1','lane':lane,'status':'ABSTAIN'}: raise ValueError()
    return value
'''.replace(b'__keys__', repr(KEYS).encode()).replace(
    b'__worker_sha__', repr(hashlib.sha256(b"synthetic-worker").hexdigest()).encode())


@pytest.fixture
def plan(tmp_path):
    from sandbox.mind.step2 import runtime_builder
    runtime = tmp_path / "runtime"
    path = runtime / "assets/code/tools/wd_q4_session_outer_synthetic.py"
    path.parent.mkdir(parents=True)
    path.write_bytes(FAKE_OUTER)
    code = runtime / "assets/code"
    files = {"sandbox/mind/step2/runtime_manifest.py": (ROOT / "sandbox/mind/step2/runtime_manifest.py").read_bytes(),
             "tools/wd_q4_public_outer.py": b"synthetic-public-outer", "tools/wd_q4_public_child.py": b"synthetic-child",
             "tools/wd_q4_public_query.py": b"synthetic-query", "tools/wd_q4_lane_session_cli.py": b"synthetic-worker"}
    for name, raw in files.items():
        item = code / name
        item.parent.mkdir(parents=True, exist_ok=True)
        item.write_bytes(raw)
    (runtime / "site").mkdir()
    (runtime / "site/synthetic.py").write_bytes(b"synthetic-site")
    (runtime / "model").mkdir()
    (runtime / "model/synthetic.bin").write_bytes(b"synthetic-model")
    manifest = tmp_path / "runtime-manifest"
    built = runtime_builder.build_runtime_manifest(runtime, manifest, generation="b" * 40, revision="c" * 40)
    options = {"outer-path": str(path), "outer-sha256": hashlib.sha256(FAKE_OUTER).hexdigest(),
               "runtime-root": str(runtime), "runtime-generation": "b" * 40, "runtime-revision": "c" * 40,
               "verifier-sha256": hashlib.sha256(files["sandbox/mind/step2/runtime_manifest.py"]).hexdigest(),
               "public-outer-sha256": hashlib.sha256(files["tools/wd_q4_public_outer.py"]).hexdigest(),
               "public-child-sha256": hashlib.sha256(files["tools/wd_q4_public_child.py"]).hexdigest(),
               "public-query-sha256": hashlib.sha256(files["tools/wd_q4_public_query.py"]).hexdigest(),
               "worker-sha256": hashlib.sha256(files["tools/wd_q4_lane_session_cli.py"]).hexdigest(),
               "lane": "codex-lead-1", "bundle-root": str(tmp_path / "bundle"),
               "public-plan": str(tmp_path / "plan"), "interpreter": sys.executable,
               "interpreter-sha256": hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),
               "interpreter-max-bytes": "10000000", "cwd": str(tmp_path),
               "timeout-seconds": "30", "max-output-bytes": "4096"}
    for name in ("fleet", "binding", "fixed-plan", "snapshot", "runtime-manifest"):
        options[name] = str(tmp_path / name)
        options[name + "-sha256"] = "a" * 64
    options["runtime-manifest-sha256"] = built["manifest_sha256"]
    for key in KEYS:
        options["expected-" + key.replace("_", "-")] = "a" * 64
    return options


def run(options=(), *, input_=b"synthetic question", enabled=True):
    argv = [str(ENTRY)] + (["--enabled"] if enabled else [])
    for key, value in dict(options).items():
        argv.extend(("--" + key, value))
    return subprocess.run([sys.executable, "-I", "-S", "-B", *argv], input=input_, capture_output=True, timeout=20)


def test_default_off_refuses_without_echo(plan):
    result = run(plan, enabled=False, input_=b"private-sentinel")
    assert result.returncode == 2 and result.stdout == b""
    assert b"private-sentinel" not in result.stderr


def test_exact_caller_pins_forwarded_and_one_lf_json_emitted(plan):
    result = run(plan)
    assert result.returncode == 0 and result.stderr == b""
    assert result.stdout.count(b"\n") == 1 and result.stdout.endswith(b"\n") and b"\r" not in result.stdout
    assert json.loads(result.stdout) == {"schema": "wd.q4-lane-answer.v1", "lane": "codex-lead-1", "status": "ABSTAIN"}
    assert b"synthetic question" not in result.stdout


@pytest.mark.parametrize("mutation", ["wrong-outer-hash", "wrong-outer-path", "unknown-option", "missing-option"])
def test_bad_options_refuse_before_input_can_leak(plan, tmp_path, mutation):
    if mutation == "wrong-outer-hash": plan["outer-sha256"] = "0" * 64
    if mutation == "wrong-outer-path": plan["outer-path"] = str(tmp_path / "outside.py")
    if mutation == "unknown-option": plan["extra"] = "private-sentinel"
    if mutation == "missing-option": del plan["worker-sha256"]
    result = run(plan, input_=b"private-sentinel")
    assert result.returncode == 2 and result.stdout == b"" and b"private-sentinel" not in result.stderr


def test_partial_tuple_refuses_without_waiting_for_stdin(plan):
    del plan["worker-sha256"]
    argv = [sys.executable, "-I", "-S", "-B", str(ENTRY), "--enabled"]
    for key, value in plan.items():
        argv.extend(("--" + key, value))
    child = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert child.wait(timeout=8) == 2
        assert child.stdout.read() == b""
    finally:
        if child.poll() is None:
            child.kill()  # Only the synthetic test-owned child.
            child.wait(timeout=5)
        for stream in (child.stdin, child.stdout, child.stderr):
            if stream:
                stream.close()


@pytest.mark.parametrize("target", ["manifest", "site", "worker", "verifier", "interpreter-pin"])
def test_tampered_runtime_is_refused_before_stdin_read(plan, target):
    root = Path(plan["runtime-root"])
    if target == "interpreter-pin":
        plan["interpreter-sha256"] = "0" * 64
    else:
        item = {"manifest": Path(plan["runtime-manifest"]), "site": root / "site/synthetic.py",
                "worker": root / "assets/code/tools/wd_q4_lane_session_cli.py",
                "verifier": root / "assets/code/sandbox/mind/step2/runtime_manifest.py"}[target]
        item.write_bytes(item.read_bytes() + b"tamper")
    argv = [sys.executable, "-I", "-S", "-B", str(ENTRY), "--enabled"]
    for key, value in plan.items():
        argv.extend(("--" + key, value))
    child = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert child.wait(timeout=4) == 2
        assert child.stdout.read() == b""
    finally:
        if child.poll() is None:
            child.kill()  # Only the synthetic test-owned child.
            child.wait(timeout=5)
        for stream in (child.stdin, child.stdout, child.stderr):
            if stream:
                stream.close()


@pytest.mark.parametrize("query,code", [(b"cleanup", 3), (b"timeout", 4), (b"interrupted", 130), (b"bad-result", 2)])
def test_failure_states_are_distinct_and_never_emit_result(plan, query, code):
    result = run(plan, input_=query)
    assert result.returncode == code and result.stdout == b"" and b"private-sentinel" not in result.stderr


@pytest.mark.parametrize("query", [b"", b"x" * 16001])
def test_bounded_stdin_refuses(plan, query):
    result = run(plan, input_=query)
    assert result.returncode == 2 and result.stdout == b""


def test_malformed_outer_exception_class_refuses_without_traceback(plan, tmp_path):
    from sandbox.mind.step2 import runtime_builder
    path = Path(plan["outer-path"])
    raw = FAKE_OUTER.replace(b"class SessionOuterCleanupUnknown(Exception): pass", b"SessionOuterCleanupUnknown = 1")
    path.write_bytes(raw)
    plan["outer-sha256"] = hashlib.sha256(raw).hexdigest()
    manifest = tmp_path / "malformed-outer-manifest.json"
    built = runtime_builder.build_runtime_manifest(Path(plan["runtime-root"]), manifest,
                                                   generation=plan["runtime-generation"],
                                                   revision=plan["runtime-revision"])
    plan["runtime-manifest"] = str(manifest)
    plan["runtime-manifest-sha256"] = built["manifest_sha256"]
    result = run(plan, input_=b"private-sentinel")
    assert result.returncode == 2 and result.stdout == b""
    assert result.stderr.strip() == b"session_entry_refused"


@pytest.mark.skipif(sys.platform != "win32", reason="public outer requires Windows paths")
def test_entry_runs_actual_manifest_pinned_outer_with_only_synthetic_worker(plan, tmp_path):
    import importlib.util
    from sandbox.mind.step2 import runtime_builder

    spec = importlib.util.spec_from_file_location("synthetic_outer_fixture", Path(__file__).with_name(
        "test_wd_q4_session_outer_synthetic.py"))
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    inputs = helper.synthetic.__wrapped__(tmp_path / "actual")
    root = Path(inputs["runtime_root"])
    outer_path = root / "assets/code/tools/wd_q4_session_outer_synthetic.py"
    raw = (ROOT / "tools/wd_q4_session_outer_synthetic.py").read_bytes()
    outer_path.write_bytes(raw)
    manifest = tmp_path / "actual-manifest.json"
    built = runtime_builder.build_runtime_manifest(root, manifest, generation=helper.GENERATION, revision=helper.REVISION)
    plan.update({"outer-path": str(outer_path), "outer-sha256": hashlib.sha256(raw).hexdigest(),
                 "runtime-root": str(root), "runtime-generation": helper.GENERATION,
                 "runtime-revision": helper.REVISION, "runtime-manifest": str(manifest),
                 "runtime-manifest-sha256": built["manifest_sha256"], "verifier-sha256": inputs["verifier_sha256"],
                 "public-outer-sha256": inputs["outer_sha256"], "public-child-sha256": inputs["child_sha256"],
                 "public-query-sha256": inputs["query_sha256"], "worker-sha256": inputs["worker_sha256"]})
    result = run(plan)
    assert result.returncode == 0 and result.stderr == b""
    assert json.loads(result.stdout) == helper.ANSWER
