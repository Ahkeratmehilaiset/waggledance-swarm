"""The continuation job is fixed, default-off, and uses synthetic inputs."""
import importlib
import importlib.util
import base64
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[2]
LAUNCH = ROOT / "ops/windows/reboot/Invoke-WdQ4Continuation.ps1"
_spec = importlib.util.spec_from_file_location("genesis_runtime_fixture",
    Path(__file__).with_name("test_wd_q4_genesis_runtime_bootstrap.py"))
_fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixture)
candidate = _fixture.candidate


def test_default_off_does_not_read_inputs(monkeypatch, capsys):
    mod = importlib.import_module("tools.wd_q4_continuation_runtime_bootstrap")
    monkeypatch.setattr(mod, "_read", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError()))
    assert mod.main([]) == 0
    assert json.loads(capsys.readouterr().out) == {"status": "OFF"}


def test_launcher_default_off():
    result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-File", str(LAUNCH)],
                            text=True, capture_output=True, timeout=20)
    assert result.returncode == 0
    assert json.loads(result.stdout) == {"status": "OFF"}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")


@pytest.fixture
def job(candidate):
    item = candidate
    bundle = item["bundle"]
    tools = bundle / "tools-bootstrap/tools"
    bootstrap = tools / "wd_q4_continuation_runtime_bootstrap.py"
    shutil.copyfile(ROOT / "tools" / bootstrap.name, bootstrap)
    worker = bundle / "Invoke-WdQ4ContinuationWorker.ps1"
    shutil.copyfile(ROOT / "ops/windows/reboot" / worker.name, worker)
    cli = tools / "wd_q4_production_continuation_cli.py"
    cli.write_text("def main(args):\n import synthetic_runtime_dependency\n"
        " assert synthetic_runtime_dependency.VALUE==42\n"
        " print('{\"status\":\"partial_ready_prefix\",\"pointer_sha256\":\"'+'e'*64+"
        "'\",\"runner_receipt_sha256\":\"'+'f'*64+'\"}')\n return 0\n", encoding="ascii")
    source = bundle.parent / "source/events.jsonl"
    source.write_bytes(b"public synthetic event\n")
    route = encode({"source": str(source), "generation_path": str(bundle.parent / "sidecar/gen.json"),
                    "checkpoint_root": str(bundle.parent / "checkpoint"),
                    "private_state_root": str(bundle.parent / "private"),
                    "trusted_model_root": str(item["runtime"] / "model")})
    tuple_path = item["config"].parent / "tuple.json"
    tuple_path.write_bytes(encode({"route": {"bytes_b64": base64.b64encode(route).decode(), "sha256": sha(route)}}))
    (item["config"].parent / "policy.json").write_text("{}")
    (item["config"].parent / "public.json").write_text("{}")
    item["config"].write_bytes(encode({"schema": "wd.q4-continuation-cli.v1", "bundle_root": str(bundle),
        "manifest_sha256": item["manifest_pin"], "tuple_path": str(tuple_path), "tuple_sha256": sha(tuple_path.read_bytes()),
        "policy_path": str(item["config"].parent / "policy.json"),
        "public_policy_path": str(item["config"].parent / "public.json")}))
    item.update(bootstrap=bootstrap, cli=cli, worker=worker, tuple=tuple_path)
    repin(item)
    return item


def repin(item):
    code = item["bundle"] / "tools-bootstrap"
    files = {"tools-bootstrap/" + p.relative_to(code).as_posix(): sha(p.read_bytes()).upper()
             for p in code.rglob("*") if p.is_file()}
    for name in ("WdQ4ProcessJob.ps1", "Invoke-WdQ4ContinuationWorker.ps1"):
        files[name] = sha((item["bundle"] / name).read_bytes()).upper()
    item["manifest"].write_bytes(encode({"schema_version": 1, "source_commit": "c" * 40, "files": files}))
    item["manifest_pin"] = sha(item["manifest"].read_bytes())
    for name, pin in (("config", "config_pin"), ("spec", "spec_pin")):
        value = json.loads(item[name].read_bytes())
        value["manifest_sha256"] = item["manifest_pin"]
        item[name].write_bytes(encode(value))
        item[pin] = sha(item[name].read_bytes())


def run_job(item, scratch, *, timeout=30):
    host = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    command = ["powershell", "-NoProfile", "-NonInteractive", "-File", str(LAUNCH), "-Run",
        "-BundleRoot", str(item["bundle"]), "-ExpectedCommit", "c" * 40,
        "-ExpectedManifestSha256", item["manifest_pin"], "-ConfigPath", str(item["config"]),
        "-ExpectedConfigSha256", item["config_pin"], "-RuntimePath", str(item["spec"]),
        "-ExpectedRuntimeSha256", item["spec_pin"], "-ScratchRoot", str(scratch),
        "-ExpectedHostSha256", sha(host.read_bytes()), "-TimeoutSeconds", str(timeout)]
    return subprocess.run(command, text=True, capture_output=True, timeout=timeout + 20)


def test_fixed_job_reaches_synthetic_cli_with_verified_runtime(job):
    scratch = job["bundle"].parent / "scratch"
    scratch.mkdir()
    result = run_job(job, scratch)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {"status": "partial_ready_prefix", "pointer_sha256": "e" * 64,
                                         "runner_receipt_sha256": "f" * 64}


@pytest.mark.parametrize("fault", ["tuple", "route", "runtime", "worker", "scratch"])
def test_job_preflight_refuses_bad_pin_or_unsafe_scratch(job, fault):
    scratch = job["bundle"].parent / "scratch"
    scratch.mkdir()
    if fault == "tuple": job["tuple"].write_bytes(b"{}")
    if fault == "route":
        value = json.loads(job["tuple"].read_bytes()); value["route"]["sha256"] = "0" * 64
        job["tuple"].write_bytes(encode(value))
        config = json.loads(job["config"].read_bytes()); config["tuple_sha256"] = sha(job["tuple"].read_bytes())
        job["config"].write_bytes(encode(config)); job["config_pin"] = sha(job["config"].read_bytes())
    if fault == "runtime": (job["runtime"] / "site/synthetic_runtime_dependency.py").write_text("VALUE=0")
    if fault == "worker": job["worker"].write_bytes(job["worker"].read_bytes() + b"# modified")
    if fault == "scratch": scratch = job["bundle"]
    result = run_job(job, scratch)
    assert result.returncode == 2
    assert json.loads(result.stdout) == {"status": "REFUSED"}
    assert "synthetic event" not in result.stdout + result.stderr


def test_started_cli_exception_is_unknown(job):
    job["cli"].write_text("def main(args):\n raise RuntimeError('secret-marker')\n", encoding="ascii")
    repin(job)
    scratch = job["bundle"].parent / "scratch"
    scratch.mkdir()
    result = run_job(job, scratch)
    assert result.returncode == 3 and json.loads(result.stdout) == {"status": "UNKNOWN"}
    assert "secret-marker" not in result.stdout + result.stderr


@pytest.mark.parametrize("output", ['{\"status\":\"partial_ready_prefix\"}',
                                   '{\"status\":\"REFUSED\",\"pointer_sha256\":\"' + 'a' * 64 + '\"}'])
def test_invalid_cli_receipt_is_unknown(job, output):
    job["cli"].write_text("def main(args):\n print(" + repr(output) + ")\n return 0\n", encoding="ascii")
    repin(job)
    scratch = job["bundle"].parent / "scratch"
    scratch.mkdir()
    result = run_job(job, scratch)
    assert result.returncode == 3 and json.loads(result.stdout) == {"status": "UNKNOWN"}


def test_job_timeout_is_unknown_and_synthetic_child_does_not_complete(job):
    marker = job["bundle"].parent / "late-child-marker"
    ready = job["bundle"].parent / "child-started"
    child = ("import time; from pathlib import Path; Path(" + repr(str(ready)) + ").write_text('started'); "
             "time.sleep(8); Path(" + repr(str(marker)) + ").write_text('late')")
    job["cli"].write_text("def main(args):\n import subprocess, sys, time\n"
        " subprocess.Popen([sys.executable, '-c', " + repr(child) + "])\n time.sleep(30)\n", encoding="ascii")
    repin(job)
    scratch = job["bundle"].parent / "scratch"
    scratch.mkdir()
    result = run_job(job, scratch, timeout=5)
    assert result.returncode == 3 and json.loads(result.stdout) == {"status": "UNKNOWN"}
    assert ready.exists(), "synthetic descendant did not actually start"
    time.sleep(8.5)
    assert not marker.exists()


@pytest.mark.parametrize("variant", ["direct", "nested", "short_alias"])
def test_private_tuple_under_checkpoint_root_is_refused(job, variant):
    checkpoint = job["bundle"].parent / "checkpoint"
    parent = checkpoint / "nested" if variant == "nested" else checkpoint
    parent.mkdir(exist_ok=True)
    target = parent / "private-tuple.json"
    target.write_bytes(job["tuple"].read_bytes())
    config = json.loads(job["config"].read_bytes())
    target_path = str(target)
    if variant == "short_alias":
        import ctypes
        buffer = ctypes.create_unicode_buffer(32768)
        if not ctypes.windll.kernel32.GetShortPathNameW(target_path, buffer, len(buffer)):
            pytest.skip("Windows short path unavailable")
        if os.path.normcase(buffer.value) == os.path.normcase(target_path):
            pytest.skip("Windows did not create a distinct short path")
        target_path = buffer.value
    config["tuple_path"] = target_path
    job["config"].write_bytes(encode(config))
    job["config_pin"] = sha(job["config"].read_bytes())
    scratch = job["bundle"].parent / "scratch"
    scratch.mkdir()
    result = run_job(job, scratch)
    assert result.returncode == 2
    assert json.loads(result.stdout) == {"status": "REFUSED"}
    # The pinned bootstrap independently enforces the same boundary even if
    # a caller omits the PowerShell launcher.
    direct = _fixture.execute(job)
    assert direct.returncode == 2
    assert json.loads(direct.stdout) == {"status": "REFUSED"}


def test_launcher_does_not_use_powershell_automatic_args_variable():
    import re
    assert re.search(r"\$args\b", LAUNCH.read_text(encoding="utf-8"), re.IGNORECASE) is None


def v2_job(item, *, schema="wd.q4-continuation-cli.v2", changes=None):
    config = json.loads(item["config"].read_bytes())
    config.update(schema=schema, cutoff_utc="2026-10-31T13:02:00Z", previous_cutoff_utc="2026-10-31T13:00:00Z")
    item["config"].write_bytes(encode(config))
    result = {"status": "caught_up_to_cutoff", "pointer_sha256": "e" * 64,
              "runner_receipt_sha256": "f" * 64, "cutoff_utc": config["cutoff_utc"],
              "previous_cutoff_utc": config["previous_cutoff_utc"], "route_sha256": "a" * 64,
              "cutoff_monotonicity": "CALLER_ASSERTED_REPLAY_CRASH_UNPROVEN"}
    if changes:
        changes(result)
    item["cli"].write_text("def main(args):\n print(" + repr(json.dumps(result)) + ")\n return 0\n", encoding="ascii")
    repin(item)
    return result


def v3_job(item):
    config = json.loads(item['config'].read_bytes())
    config.update(schema='wd.q4-continuation-cli.v3', cutoff_utc='2026-10-31T13:02:00Z',
                  previous_cutoff_utc='2026-10-31T13:00:00Z', watermark_previous_record_sha256=None)
    item['config'].write_bytes(encode(config))
    private = item['bundle'].parent / 'private'
    private.mkdir(exist_ok=True)
    pointer = item['bundle'].parent / 'checkpoint/q4-latest.json'
    pointer.write_bytes(encode({'schema': 'synthetic-pointer'}))
    pointer_pin = sha(pointer.read_bytes())
    record = {'schema': 'wd.q4-cutoff-record.v1', 'previous_record_sha256': None,
              'cutoff_utc': config['cutoff_utc'], 'attempt': 'd' * 64, 'route_sha256': 'a' * 64,
              'runner_receipt_sha256': 'f' * 64, 'pointer_sha256': pointer_pin}
    raw = encode(record)
    record_hash = sha(raw)
    record_path = private / ('q4-cutoff-record-' + record_hash + '.json')
    record_path.write_bytes(raw)
    (private / 'q4-cutoff-current.json').write_bytes(encode({'schema': 'wd.q4-cutoff-head.v1',
                                                            'record_sha256': record_hash}))
    result = {'status': 'DEFERRED', 'pointer_sha256': pointer_pin, 'runner_receipt_sha256': 'f' * 64,
              'cutoff_utc': config['cutoff_utc'], 'previous_cutoff_utc': config['previous_cutoff_utc'],
              'route_sha256': 'a' * 64, 'watermark_record_sha256': record_hash,
              'cutoff_monotonicity': 'COOPERATIVE_LEASE_PERSISTED_CRASH_UNKNOWN'}
    item['cli'].write_text('def main(args):\n print(' + repr(json.dumps(result)) + ')\n return 0\n', encoding='ascii')
    repin(item)
    return result, record_path


def test_v3_job_binds_persisted_record(job):
    expected, _ = v3_job(job)
    scratch = job['bundle'].parent / 'scratch'
    scratch.mkdir()
    result = run_job(job, scratch)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == expected


def test_v3_job_rejects_tampered_persisted_record(job):
    _, record = v3_job(job)
    record.write_bytes(record.read_bytes() + b' ')
    scratch = job['bundle'].parent / 'scratch'
    scratch.mkdir()
    result = run_job(job, scratch)
    assert result.returncode == 3
    assert json.loads(result.stdout) == {'status': 'UNKNOWN'}


@pytest.mark.parametrize('fault', ['record', 'head', 'pending', 'pointer', 'wrong_cutoff', 'wrong_previous'])
def test_v3_outer_powershell_independently_rejects_bad_record(job, fault):
    expected, record = v3_job(job)
    private = record.parent
    if fault == 'record':
        record.write_bytes(record.read_bytes() + b' ')
    if fault == 'head':
        (private / 'q4-cutoff-current.json').write_bytes(encode({'schema': 'wd.q4-cutoff-head.v1',
                                                                 'record_sha256': '9' * 64}))
    if fault == 'pending':
        (private / 'q4-cutoff.pending.json').write_bytes(b'{}')
    if fault == 'pointer':
        (job['bundle'].parent / 'checkpoint/q4-latest.json').write_bytes(b'changed')
    if fault in ('wrong_cutoff', 'wrong_previous'):
        document = json.loads(record.read_bytes())
        if fault == 'wrong_cutoff': document['cutoff_utc'] = '2026-10-31T13:00:00Z'
        if fault == 'wrong_previous': document['previous_record_sha256'] = '9' * 64
        raw = encode(document)
        new_pin = sha(raw)
        (private / ('q4-cutoff-record-' + new_pin + '.json')).write_bytes(raw)
        (private / 'q4-cutoff-current.json').write_bytes(encode({'schema': 'wd.q4-cutoff-head.v1',
                                                                 'record_sha256': new_pin}))
        expected['watermark_record_sha256'] = new_pin
    # Bypass only the synthetic bootstrap so the outer PS must validate itself.
    job['bootstrap'].write_text('print(' + repr(json.dumps(expected)) + ')\n', encoding='ascii')
    repin(job)
    scratch = job['bundle'].parent / 'scratch'
    scratch.mkdir()
    result = run_job(job, scratch)
    assert result.returncode == 3
    assert json.loads(result.stdout) == {'status': 'UNKNOWN'}


def test_fixed_job_accepts_only_config_bound_v2_receipt(job):
    expected = v2_job(job)
    scratch = job["bundle"].parent / "scratch"
    scratch.mkdir()
    result = run_job(job, scratch)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == expected


@pytest.mark.parametrize("fault", ["current_cutoff", "previous_cutoff", "route_hash", "monotonicity", "missing", "v1"])
def test_job_rejects_v2_receipt_inconsistent_with_config(job, fault):
    def change(value):
        if fault == "current_cutoff": value["cutoff_utc"] = "2026-10-31T13:03:00Z"
        if fault == "previous_cutoff": value["previous_cutoff_utc"] = "2026-10-31T12:00:00Z"
        if fault == "route_hash": value["route_sha256"] = "not-a-hash"
        if fault == "monotonicity": value["cutoff_monotonicity"] = "PROVEN"
        if fault == "missing": value.pop("route_sha256")
    v2_job(job, schema="wd.q4-continuation-cli.v1" if fault == "v1" else "wd.q4-continuation-cli.v2", changes=change)
    scratch = job["bundle"].parent / "scratch"
    scratch.mkdir()
    result = run_job(job, scratch)
    assert result.returncode == 3 and json.loads(result.stdout) == {"status": "UNKNOWN"}


@pytest.mark.parametrize("fault", ["current_cutoff", "previous_cutoff", "route_hash", "monotonicity", "missing"])
def test_outer_powershell_independently_rejects_bad_v2_receipt(job, fault):
    result = v2_job(job)
    if fault == "current_cutoff": result["cutoff_utc"] = "2026-10-31T13:03:00Z"
    if fault == "previous_cutoff": result["previous_cutoff_utc"] = "2026-10-31T12:00:00Z"
    if fault == "route_hash": result["route_sha256"] = "not-a-hash"
    if fault == "monotonicity": result["cutoff_monotonicity"] = "PROVEN"
    if fault == "missing": result.pop("route_sha256")
    # Exercise the outer PS validator independently: a synthetic manifest-pinned
    # bootstrap emits a forged receipt without the inner bootstrap validator.
    job["bootstrap"].write_text("print(" + repr(json.dumps(result)) + ")\n", encoding="ascii")
    repin(job)
    scratch = job["bundle"].parent / "scratch"
    scratch.mkdir()
    actual = run_job(job, scratch)
    assert actual.returncode == 3 and json.loads(actual.stdout) == {"status": "UNKNOWN"}


@pytest.mark.parametrize("fault", ["malformed", "backwards"])
def test_v2_invalid_cutoff_fails_before_job(job, fault):
    v2_job(job)
    config = json.loads(job["config"].read_bytes())
    config["cutoff_utc"] = "invalid" if fault == "malformed" else "2026-10-31T12:00:00Z"
    job["config"].write_bytes(encode(config))
    job["config_pin"] = sha(job["config"].read_bytes())
    scratch = job["bundle"].parent / "scratch"
    scratch.mkdir()
    result = run_job(job, scratch)
    assert result.returncode == 2 and json.loads(result.stdout) == {"status": "REFUSED"}
    assert not list(scratch.glob("wd-q4-job-*"))
    direct = _fixture.execute(job)
    assert direct.returncode == 2 and json.loads(direct.stdout) == {"status": "REFUSED"}
