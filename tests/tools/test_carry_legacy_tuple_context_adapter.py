"""Run the unchanged independent legacy corpus with its missing fleet context.

Lead Source GO 2026-11-09T22:54:54Z. Only test-owned configuration is added;
the original corpus's inputs, assertions and production entrypoint stay intact.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _corpus_digest(path):
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


CORPUS = ROOT / "tests/tools/_carry_legacy_tuple_adversarial.py"
assert _corpus_digest(CORPUS) == "58a04e9a556d4ad2972a8150d577c5985201a373be34bc6e5f72f5245be429d3"
spec = importlib.util.spec_from_file_location("legacy_corpus_with_fleet_context", CORPUS)
legacy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(legacy)
REALSHAPE = ROOT / "tests/tools/_carry_legacy_realshape.py"
assert _corpus_digest(REALSHAPE) == "3de097b37a045285836bf08109a2287bb73906e6b5552a65bff0fe3752b428a4"
real_spec = importlib.util.spec_from_file_location("realshape_corpus_with_fleet_context", REALSHAPE)
realshape = importlib.util.module_from_spec(real_spec)
real_spec.loader.exec_module(realshape)
histories = legacy.histories
anchored_fleet = legacy.anchored_fleet
collision = realshape.collision
_raw_adapter = legacy._adapter


def _configure_context(tmp_path, history, monkeypatch, fault=None):
    bundle = Path(os.environ["WD_BRIDGE_BUNDLE_ROOT"])
    fleet_path = bundle / "wd-fleet.json"
    fleet = json.loads(fleet_path.read_bytes())
    runtime = tmp_path / "synthetic-runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    fleet.update(primary_repo_root=str(history.repo.path), runtime_root=str(runtime))
    if fault == "missing_primary":
        fleet.pop("primary_repo_root")
    elif fault == "wrong_origin":
        foreign = legacy.corpus.Repo(tmp_path / "foreign-repo")
        foreign.git("remote", "add", "origin", "https://github.com/other/repository.git")
        fleet["primary_repo_root"] = str(foreign.path)
    raw = json.dumps(fleet).encode("utf-8")
    fleet_path.write_bytes(raw)
    manifest_path = bundle / "deployment-manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["files"]["wd-fleet.json"] = hashlib.sha256(raw).hexdigest()
    data = json.dumps(manifest).encode("utf-8")
    manifest_path.write_bytes(data)
    monkeypatch.setenv("WD_REBOOT_EXPECTED_MANIFEST_HASH", hashlib.sha256(data).hexdigest())
    monkeypatch.setenv("AGENT_BRIDGE_RUNTIME_ROOT", str(history.repo.path if fault == "wrong_runtime" else runtime))


@pytest.fixture(autouse=True)
def configured_legacy_adapter(anchored_fleet, monkeypatch):
    def with_context(delegate):
        def adapter(tmp_path, history, events, *, gh=None):
            _configure_context(tmp_path, history, monkeypatch)
            return delegate(tmp_path, history, events, gh=gh)
        return adapter
    for harness in (legacy, realshape.lg):
        monkeypatch.setattr(harness, "_adapter", with_context(harness._adapter))


# Collect the independent functions unchanged; globals still belong to their
# original module. The sole seam above supplies synthetic fleet context.
for _module in (legacy, realshape):
    for _name, _function in vars(_module).copy().items():
        if _name.startswith("test_"):
            assert _name not in globals(), "Independent cases must be collected once"
            globals()[_name] = _function


@pytest.mark.parametrize("fault", ["missing_primary", "wrong_origin", "wrong_runtime"])
def test_legacy_context_refusals_are_differential(histories, tmp_path, monkeypatch, fault):
    history = histories["legit"]
    legacy._assert_carries(legacy._adapter(tmp_path / "valid", history, legacy._legacy_events(history)), history)
    target = tmp_path / "invalid"
    _configure_context(target, history, monkeypatch, fault)
    result = _raw_adapter(target, history, legacy._legacy_events(history))
    assert result.carry is False, result.text
    assert not result.result or not result.result.get("carried")
    assert legacy.of._merge_calls(result.gh) == []


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
def test_corpus_digest_accepts_git_checkout_line_endings(tmp_path, newline):
    canonical = b"print('synthetic corpus')\n"
    fixture = tmp_path / "synthetic.py"
    fixture.write_bytes(canonical.replace(b"\n", newline))
    assert _corpus_digest(fixture) == hashlib.sha256(canonical).hexdigest()


def test_corpus_digest_still_detects_changed_source(tmp_path):
    fixture = tmp_path / "changed.py"
    fixture.write_bytes(b"print('changed corpus')\r\n")
    assert _corpus_digest(fixture) != hashlib.sha256(b"print('synthetic corpus')\n").hexdigest()
