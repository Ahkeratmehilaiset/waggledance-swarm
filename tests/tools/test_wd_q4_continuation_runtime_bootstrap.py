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
