"""Synthetic bootstrap integration tests; no real model, memory or deployment."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

BOOTSTRAP = Path(__file__).with_name("runtime_bootstrap.py")
GENERATION = "a" * 40
REVISION = "b" * 40
READER = "assets/code/sandbox/mind/step2/wd_q4_private_read.py"


@pytest.fixture
def runtime(tmp_path):
    root = tmp_path / "runtime"
    files = {
        READER: (
            "import hashlib,json,os,sys\n"
            "import probe_dependency\n"
            "open(os.environ['SYNTHETIC_MARKER'], 'w').write('imported')\n"
            "raw=sys.stdin.buffer.read()\n"
            "print(json.dumps({'query_hash':hashlib.sha256(raw).hexdigest(),"
            "'probe':probe_dependency.VALUE,'args':sys.argv[1:]}))\n"
        ).encode(),
        "site/probe_dependency.py": b"VALUE='synthetic-site'\n",
        "model/config.json": b'{"synthetic":true}\n',
    }
    records = []
    for name, data in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        records.append({"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    manifest = tmp_path / "manifest.json"
    raw = json.dumps({"schema": "wd.q4-runtime-manifest.v1", "generation": GENERATION,
                      "revision": REVISION, "files": records}, sort_keys=True).encode()
    manifest.write_bytes(raw)
    return root, manifest, hashlib.sha256(raw).hexdigest(), tmp_path / "imported"


def invoke(runtime, *, generation=GENERATION, revision=REVISION, pin=None, isolated=True, query=b"synthetic query"):
    root, manifest, digest, marker = runtime
    args = [sys.executable] + (["-I", "-S", "-B"] if isolated else []) + [str(BOOTSTRAP),
        "--runtime-root", str(root), "--runtime-manifest", str(manifest),
        "--runtime-manifest-sha256", pin or digest, "--runtime-generation", generation,
        "--runtime-revision", revision, "--memory", str(root.parent / "memory"),
        "--descriptor", str(root.parent / "descriptor"), "--descriptor-sha256", "1" * 64,
        "--lock-file", str(root.parent / "lock"), "--lock-timeout", "0.25"]
    return subprocess.run(args, input=query, capture_output=True,
                          env={**os.environ, "SYNTHETIC_MARKER": str(marker)}, timeout=30)


def test_anchored_runtime_runs_reader_via_stdin(runtime):
    result = invoke(runtime)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["query_hash"] == hashlib.sha256(b"synthetic query").hexdigest()
    assert payload["probe"] == "synthetic-site"
    assert b"synthetic query" not in result.stdout + result.stderr
    assert runtime[3].read_text() == "imported"
    assert payload["args"][-2:] == ["--model-dir", str(runtime[0] / "model")]
    assert payload["args"][payload["args"].index("--lock-timeout") + 1] == "0.25"


@pytest.mark.parametrize("kind", ["manifest_pin", "generation", "revision", "site", "reader"])
def test_bad_pin_or_runtime_tamper_refuses_before_reader_import(runtime, kind):
    kwargs = {}
    if kind == "manifest_pin":
        kwargs["pin"] = "0" * 64
    elif kind == "generation":
        kwargs["generation"] = "c" * 40
    elif kind == "revision":
        kwargs["revision"] = "d" * 40
    else:
        path = runtime[0] / ("site/probe_dependency.py" if kind == "site" else READER)
        path.write_bytes(b"tampered\n")
    result = invoke(runtime, **kwargs)
    assert result.returncode == 2
    assert json.loads(result.stdout)["status"] == "REFUSED"
    assert not runtime[3].exists()
    assert b"synthetic query" not in result.stdout + result.stderr


def test_nonisolated_interpreter_refuses_before_reader_import(runtime):
    result = invoke(runtime, isolated=False)
    assert result.returncode == 2
    assert json.loads(result.stdout)["status"] == "REFUSED"
    assert not runtime[3].exists()


def test_missing_expected_reader_refuses_even_with_valid_manifest(runtime):
    root, manifest, _, marker = runtime
    document = json.loads(manifest.read_bytes())
    document["files"] = [item for item in document["files"] if item["path"] != READER]
    (root / READER).unlink()
    raw = json.dumps(document, sort_keys=True).encode()
    manifest.write_bytes(raw)
    result = invoke(runtime, pin=hashlib.sha256(raw).hexdigest())
    assert result.returncode == 2
    assert json.loads(result.stdout)["status"] == "REFUSED"
    assert not marker.exists()


def test_missing_expected_model_refuses_even_with_valid_manifest(runtime):
    root, manifest, _, marker = runtime
    document = json.loads(manifest.read_bytes())
    document["files"] = [item for item in document["files"] if item["path"] != "model/config.json"]
    (root / "model" / "config.json").unlink()
    (root / "model").rmdir()
    raw = json.dumps(document, sort_keys=True).encode()
    manifest.write_bytes(raw)
    result = invoke(runtime, pin=hashlib.sha256(raw).hexdigest())
    assert result.returncode == 2
    assert json.loads(result.stdout)["status"] == "REFUSED"
    assert not marker.exists()
