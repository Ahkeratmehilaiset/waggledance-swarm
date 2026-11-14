"""Synthetic-only runtime-process preflight tests; no real model is loaded."""

import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys

import pytest

from tools import wd_mind_runtime_process as preflight


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
    verifier = tmp_path / "synthetic-verifier.py"
    interpreter.write_bytes(b"synthetic interpreter, never execute")
    bootstrap.write_bytes(b"raise AssertionError('must never run')\n")
    verifier.write_bytes((Path(__file__).resolve().parents[2] / "tools/wd_mind_runtime_manifest.py").read_bytes())
    return {"interpreter": interpreter, "interpreter_sha256": _hash(interpreter.read_bytes()),
            "bootstrap": bootstrap, "bootstrap_sha256": _hash(bootstrap.read_bytes()),
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


@pytest.mark.parametrize("key", ["interpreter_sha256", "bootstrap_sha256", "verifier_sha256",
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


def test_preflight_has_no_entrypoint_in_packaged_bridge_definition():
    root = Path(__file__).resolve().parents[2]
    definition = json.loads((root / "ops/windows/reboot/bridge-code-files.json").read_bytes())
    path = "tools/wd_mind_runtime_process.py"
    assert definition["python_files"].count(path) == 1
    assert definition["import_smoke"]["package_modules"].count("tools.wd_mind_runtime_process") == 1
    assert path not in definition["python_entrypoints"].values()
