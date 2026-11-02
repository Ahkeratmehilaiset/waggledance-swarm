"""Synthetic active-artifact semantic checks; no native or filesystem access."""
import builtins
import hashlib
import json
import traceback
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from sandbox.mind.step2 import shared_index_checkpoint as checkpoint
from sandbox.mind.step2 import shared_projection as projection
from waggledance.core.bridge_log_reader import BridgeCursor


def _raw(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _case(fault=None):
    roster = {
        "codex-lead-1": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "codex-tools-1": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        "claude-rco-1": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
        "claude-rco-2": "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
        "fable-5": "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
    }
    tasks = frozenset({"public-task"})
    policy_sha, roster_sha = "a" * 64, "b" * 64
    proj = projection.project_events([], approved_tasks=tasks, roster=roster, exclude_review_signal=True)
    projection_bytes = json.dumps(proj, sort_keys=True, indent=1).encode()
    policy = {"authorship": projection.AUTHORSHIP_ROSTER, "review_signal_excluded": True,
              "policy_sha256": policy_sha, "roster_sha256": roster_sha}
    memory = {"scope": "wd.q4-shared-projected-events.v1", "projection_schema": projection.SCHEMA,
              "projection_sha256": _sha(projection_bytes), "policy": dict(policy), "cells": {"synthetic": {}}}
    if fault == "memory_policy":
        memory["policy"]["policy_sha256"] = "0" * 64
    memory_bytes = _raw(memory)
    encoder = {"model": "synthetic", "revision": "1", "weights_sha256": "c" * 64}
    descriptor = {"schema": "wd.bridge-mind-q4-memory-descriptor.v2", "scope": memory["scope"],
                  "memory_sha256": _sha(memory_bytes), "dim": 1, "encoder": encoder}
    descriptor_bytes = _raw(descriptor)
    receipt = {"schema": "wd.bridge-mind-shared-index-build-receipt.v1", "scope": memory["scope"],
               "descriptor_schema": descriptor["schema"], "projection_schema": projection.SCHEMA,
               "projection_file": "projection.json", "encoder": dict(encoder), "dim": 1,
               "memory_sha256": _sha(memory_bytes), "descriptor_sha256": _sha(descriptor_bytes),
               "projection_sha256": _sha(projection_bytes), "policy": dict(policy), "memory_name": "gen-a"}
    if fault == "receipt_dim":
        receipt["dim"] = 2
    elif fault == "projection_schema":
        receipt["projection_schema"] = "wrong"
    elif fault == "encoder":
        receipt["encoder"]["revision"] = "other"
    elif fault == "receipt_policy":
        receipt["policy"]["roster_sha256"] = "0" * 64
    elif fault == "memory_name":
        receipt["memory_name"] = "other"
    contents = {"descriptor_sha256": descriptor_bytes, "memory_sha256": memory_bytes,
                "projection_sha256": projection_bytes, "receipt_sha256": _raw(receipt)}
    return contents, tasks, roster, policy_sha, roster_sha


@pytest.mark.parametrize("fault", [None, "receipt_dim", "projection_schema", "encoder",
                                   "memory_policy", "receipt_policy", "memory_name"])
def test_bytes_only_active_validation_preserves_semantics_without_io_or_native(monkeypatch, fault):
    contents, tasks, roster, policy_sha, roster_sha = _case(fault)
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name.split(".")[0] in ("faiss", "numpy"):
            pytest.fail("native module import")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    monkeypatch.setattr(builtins, "open", lambda *a, **k: pytest.fail("open"))
    monkeypatch.setattr(Path, "open", lambda *a, **k: pytest.fail("Path.open"))
    monkeypatch.setattr(checkpoint, "_read", lambda *a, **k: pytest.fail("filesystem read"))
    monkeypatch.setattr(checkpoint, "_secure", lambda *a, **k: pytest.fail("filesystem path"))
    call = lambda: checkpoint._validate_generation_bytes(
        contents, name="gen-a", tasks=tasks, roster=roster,
        policy_sha256=policy_sha, roster_sha256=roster_sha)
    if fault is None:
        assert call()["hashes"] == {key: _sha(value) for key, value in contents.items()}
    else:
        with pytest.raises(checkpoint.CheckpointRefused):
            call()


@pytest.mark.parametrize("fault", [None, "source_binding", "source_mode", "encoder_pin", "public_policy"])
def test_public_preflight_checks_binding_without_native_or_disk(monkeypatch, fault):
    contents, tasks, roster, policy_sha, roster_sha = _case()
    public_bytes = _raw({"schema": "wd.mind-public-status-policy.v1",
                         "public_tasks": ["public-task"], "public_types": ["message"]})
    public_sha = _sha(public_bytes)
    mode = {"status_mode": "public-v1", "public_policy_sha256": public_sha}
    memory = json.loads(contents["memory_sha256"])
    receipt = json.loads(contents["receipt_sha256"])
    memory["source_mode"] = mode
    receipt["source_mode"] = mode if fault != "source_mode" else {"status_mode": "legacy", "public_policy_sha256": None}
    contents["memory_sha256"] = _raw(memory)
    descriptor = json.loads(contents["descriptor_sha256"])
    descriptor["memory_sha256"] = _sha(contents["memory_sha256"])
    contents["descriptor_sha256"] = _raw(descriptor)
    receipt["memory_sha256"] = _sha(contents["memory_sha256"])
    receipt["descriptor_sha256"] = _sha(contents["descriptor_sha256"])
    cursor = BridgeCursor(offset=0, file_identity="posix-v1:1:2", generation=None)
    anchor_sha = "e" * 64
    receipt["source_binding"] = checkpoint._source_binding(
        cursor, cursor, anchor_sha, _sha(contents["projection_sha256"]), policy_sha, roster_sha)
    if fault == "source_binding":
        receipt["source_binding"]["anchor_sha256"] = "0" * 64
    contents["receipt_sha256"] = _raw(receipt)
    pointer = {"generation": "gen-a", "policy_sha256": policy_sha, "roster_sha256": roster_sha,
               "anchor_sha256": anchor_sha, "cursor_start": asdict(cursor), "cursor_end": asdict(cursor),
               "schema": checkpoint.PREFIX_SCHEMA,
               "artifacts": {key: _sha(value) for key, value in contents.items()}}
    by_name = {checkpoint.ARTIFACTS[key]: value for key, value in contents.items()}

    class Active:
        def read(self, name):
            return by_name[name]

    encoder_sha = _sha(_raw(descriptor["encoder"]))
    if fault == "encoder_pin":
        encoder_sha = "0" * 64
    if fault == "public_policy":
        public_sha = "0" * 64
    original_import = builtins.__import__
    def guarded_import(name, *args, **kwargs):
        if name.split(".")[0] in ("faiss", "numpy"):
            pytest.fail("native module import")
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guarded_import)
    monkeypatch.setattr(builtins, "open", lambda *a, **k: pytest.fail("open"))
    monkeypatch.setattr(Path, "open", lambda *a, **k: pytest.fail("Path.open"))
    monkeypatch.setattr(checkpoint, "_read", lambda *a, **k: pytest.fail("filesystem read"))
    monkeypatch.setattr(checkpoint, "_secure", lambda *a, **k: pytest.fail("filesystem path"))
    call = lambda: checkpoint._preflight_public_snapshot(
        pointer, Active(), tasks, roster, public_policy_bytes=public_bytes,
        public_policy_sha256=public_sha, expected_source_sha256=anchor_sha,
        expected_encoder_sha256=encoder_sha)
    if fault is None:
        assert call() is None
    else:
        with pytest.raises(checkpoint.CheckpointRefused):
            call()


@pytest.mark.parametrize("changed", [False, True])
def test_legacy_verifier_keeps_separate_descriptor_reread(monkeypatch, changed):
    from sandbox.mind import step2
    from sandbox.mind.step2 import q4_descriptor
    contents, tasks, roster, policy_sha, roster_sha = _case()
    reads = []
    raw = contents["descriptor_sha256"]
    descriptor = q4_descriptor.read_descriptor_bytes(raw, _sha(raw), error_type=ValueError)
    if changed:
        descriptor["dim"] = 2
    def separate_read(path, expected):
        reads.append((path.name, expected))
        return descriptor
    reader = SimpleNamespace(
        read_descriptor=separate_read,
        bind_memory=lambda *a: SimpleNamespace(views={
            "synthetic": SimpleNamespace(healthy=True, rows=[], collection=SimpleNamespace(_texts=[]))}))
    monkeypatch.setattr(step2, "wd_q4_private_read", reader, raising=False)
    monkeypatch.setattr(checkpoint, "_secure", lambda path, **kw: Path(path))
    monkeypatch.setattr(Path, "is_dir", lambda self: True)
    monkeypatch.setattr(checkpoint.os, "walk", lambda *a, **kw: iter(()))
    by_name = {checkpoint.ARTIFACTS[key]: value for key, value in contents.items()}
    monkeypatch.setattr(checkpoint, "_read", lambda path, **kw: by_name[Path(path).name])
    call = lambda: checkpoint._verify_generation(Path("synthetic-root"), "gen-a", tasks, roster,
                                                  policy_sha, roster_sha)
    if changed:
        with pytest.raises(checkpoint.CheckpointRefused):
            call()
    else:
        assert call()["descriptor_sha256"] == _sha(raw)
    assert reads == [("descriptor.json", _sha(raw))]


def test_active_validation_refusal_does_not_retain_private_input():
    contents, tasks, roster, policy_sha, roster_sha = _case()
    secret = b'{"synthetic-private-sentinel":"unfinished'
    contents["memory_sha256"] = secret
    with pytest.raises(checkpoint.CheckpointRefused) as caught:
        checkpoint._validate_generation_bytes(contents, name="gen-a", tasks=tasks, roster=roster,
                                              policy_sha256=policy_sha, roster_sha256=roster_sha)
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    for frame, _ in traceback.walk_tb(caught.value.__traceback__):
        if frame.f_globals.get("__name__") == checkpoint.__name__:
            assert all(value is not secret and value is not contents for value in frame.f_locals.values())
