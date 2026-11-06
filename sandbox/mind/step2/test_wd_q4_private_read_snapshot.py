"""Synthetic byte-backed reader checks; no real model or native FAISS."""
import hashlib
import io
import json
import traceback
from pathlib import Path

import numpy as np
import pytest

from sandbox.mind.step2 import cell, wd_q4_private_read as reader
from sandbox.mind.step2.test_memory_snapshot import _case, _enc


def _fixture(monkeypatch, *, invalid_row=False, invalid_receipt_encoder=False):
    manifest, snapshots = _case(monkeypatch)
    item = snapshots["learning"]
    artifacts = item["artifacts"]
    encoder = {"model": "synthetic", "revision": "test-rev", "weights_sha256": "b" * 64}
    rows = [json.loads(line) for line in artifacts["provenance"].splitlines()]
    for row in rows:
        row["kind"] = "lesson" if invalid_row else "event"
        p = row["provenance"]
        p.pop("file")
        p.update(event_ref="c" * 64, ref_kind=reader.q4_mod.PROJECTED_REF_KIND, canonical_event=False)
    artifacts["provenance"] = b"\n".join(_enc(row) for row in rows) + b"\n"
    receipt = json.loads(artifacts["receipt"])
    receipt["content"]["encoder"] = dict(encoder, model="other") if invalid_receipt_encoder else encoder
    receipt["content"]["files"]["provenance"] = cell.sha256(artifacts["provenance"])
    receipt["content_sha256"] = cell.sha256(cell.canonical(receipt["content"]).encode())
    artifacts["receipt"] = _enc(receipt)
    receipt_pin = cell.sha256(artifacts["receipt"])
    manifest["cells"]["learning"]["receipt_sha256"] = receipt_pin
    pointer = json.loads(item["pointer"])
    pointer["receipt_sha256"] = receipt_pin
    item["pointer"] = _enc(pointer)
    log = json.loads(item["log"])
    log["receipt_sha256"] = receipt_pin
    item["log"] = _enc(log) + b"\n"
    manifest_bytes = _enc(manifest)
    memory_pin = hashlib.sha256(manifest_bytes).hexdigest()
    descriptor_bytes = _enc({"schema": reader.DESCRIPTOR_SCHEMA, "scope": reader.SHARED_SCOPE,
                             "memory_sha256": memory_pin, "dim": 1, "encoder": encoder})
    monkeypatch.setattr(reader, "RUNTIME_IDENTITY", lambda **kw: dict(encoder))
    class FakeEncoder:
        def __init__(self, **kw): pass
        def query(self, text): return np.array([1.0], dtype=np.float32)
    monkeypatch.setattr(reader, "ENCODER_FACTORY", FakeEncoder)
    return dict(descriptor_bytes=descriptor_bytes, descriptor_sha256=cell.sha256(descriptor_bytes),
                manifest_bytes=manifest_bytes, expected_memory_sha256=memory_pin, cell_snapshots=snapshots,
                model_dir="synthetic-model", expected_runtime_revision="test-rev")


def _stream():
    return io.BytesIO(b'{"situation":"retry network","time":"2026-01-02T00:00:00Z"}')


def test_default_off_does_not_read_stream(monkeypatch):
    args = _fixture(monkeypatch)
    class NoRead:
        def read(self, *args): pytest.fail("disabled reader consumed input")
    with pytest.raises(reader.PrivateReadRefused):
        reader.private_read_snapshot(stream=NoRead(), **args)


def test_real_snapshot_q4_projection_without_path_reopen(monkeypatch):
    args = _fixture(monkeypatch)
    with monkeypatch.context() as guard:
        guard.setattr(Path, "open", lambda *a, **kw: pytest.fail("path reopen"))
        out = reader.private_read_snapshot(stream=_stream(), enabled=True, **args)
    assert out["status"] == "ANSWER" and out["results"]
    assert "retry network" not in json.dumps(out)
    assert "retry stale connection" not in json.dumps(out)
    assert "memory" not in out


@pytest.mark.parametrize("fault", ["descriptor_pin", "memory_pin", "manifest_bytes", "mutable_manifest",
                                    "runtime_revision", "runtime_identity", "cell"])
def test_binding_failures_refuse_before_query(monkeypatch, fault):
    args = _fixture(monkeypatch)
    if fault == "descriptor_pin": args["descriptor_sha256"] = "0" * 64
    if fault == "memory_pin": args["expected_memory_sha256"] = "0" * 64
    if fault == "manifest_bytes": args["manifest_bytes"] += b" "
    if fault == "mutable_manifest": args["manifest_bytes"] = bytearray(args["manifest_bytes"])
    if fault == "runtime_revision": args["expected_runtime_revision"] = "other"
    if fault == "runtime_identity": monkeypatch.setattr(reader, "RUNTIME_IDENTITY", lambda **kw: {})
    if fault == "cell": args["cell_snapshots"]["learning"]["log"] = b"bad"
    class NoRead:
        def read(self, *args): pytest.fail("invalid binding consumed input")
    with pytest.raises(reader.PrivateReadRefused):
        reader.private_read_snapshot(stream=NoRead(), enabled=True, **args)


def test_failure_does_not_retain_query_or_backend_exception(monkeypatch):
    args = _fixture(monkeypatch)
    class Failing:
        def __init__(self, **kw): raise ValueError("secret-backend-sentinel")
    monkeypatch.setattr(reader, "ENCODER_FACTORY", Failing)
    with pytest.raises(reader.PrivateReadRefused) as caught:
        reader.private_read_snapshot(stream=_stream(), enabled=True, **args)
    assert caught.value.__context__ is None
    assert "secret-backend-sentinel" not in repr(caught.value)
    for frame, _ in traceback.walk_tb(caught.value.__traceback__):
        if frame.f_code.co_name == "private_read_snapshot":
            assert "stream" not in frame.f_locals
            assert "cell_snapshots" not in frame.f_locals
            assert "model_dir" not in frame.f_locals


@pytest.mark.parametrize("fault", ["row", "encoder"])
def test_snapshot_enforces_legacy_scope_and_cell_encoder_before_read(monkeypatch, fault):
    args = _fixture(monkeypatch, invalid_row=fault == "row", invalid_receipt_encoder=fault == "encoder")
    class NoRead:
        def read(self, *args): pytest.fail("invalid cell consumed input")
    with pytest.raises(reader.PrivateReadRefused):
        reader.private_read_snapshot(stream=NoRead(), enabled=True, **args)
