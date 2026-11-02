"""Offline synthetic snapshot through the actual local FAISS and Q4 reader."""
import hashlib
import io
import json

import pytest

faiss = pytest.importorskip("faiss", reason="native FAISS is an optional local dependency")
np = pytest.importorskip("numpy", reason="NumPy is an optional local dependency")

from sandbox.mind.step2 import cell, memory, wd_q4_private_read as reader


def _enc(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _fixture(monkeypatch, *, invalid_index=False):
    vectors = np.ones((8, 1), dtype=np.float32)
    native = faiss.IndexFlatIP(1)
    native.add(vectors)
    index_bytes = b"synthetic-invalid-index" if invalid_index else faiss.serialize_index(native).tobytes()
    ids = [f"id-{i}-{view}" for i in range(4) for view in ("w", "d")]
    rows = [{"record_id": doc_id, "kind": "event", "cell_id": "learning",
             "time_utc": "2026-01-01T00:00:00.000000Z", "provenance": {
                 "record": f"record-{n // 2}", "view": "w" if n % 2 == 0 else "d",
                 "available_utc": "2026-01-01T00:00:00.000000Z",
                 "event_ref": "c" * 64, "ref_kind": reader.q4_mod.PROJECTED_REF_KIND,
                 "canonical_event": False, "tier": {"source_type": "bridge"}}}
            for n, doc_id in enumerate(ids)]
    data = {"index": index_bytes, "vectors": vectors.astype("<f4").tobytes(),
            "meta": _enc({"dim": 1, "doc_ids": ids,
                          "texts": ["retry stale connection" if i % 2 == 0 else "retry" for i in range(8)],
                          "metadata": [{} for _ in ids]}),
            "provenance": b"\n".join(_enc(row) for row in rows) + b"\n"}
    encoder = {"model": "synthetic", "revision": "test-rev", "weights_sha256": "b" * 64}
    content = {"cell_id": "learning", "count": 8, "dim": 1, "cutoff_utc": "2027-01-01T00:00:00Z",
               "encoder": encoder, "files": {key: hashlib.sha256(raw).hexdigest() for key, raw in data.items()},
               "ids_sha256": cell.sha256("\n".join(ids).encode()), "pairs_sha256": cell.pairs_sha256(ids, vectors)}
    data["receipt"] = _enc({"schema": cell.SCHEMA, "content": content,
                            "content_sha256": cell.sha256(cell.canonical(content).encode())})
    receipt_pin = cell.sha256(data["receipt"])
    pointer = {"generation": 1, "seq": 1, "receipt_sha256": receipt_pin}
    log = {"schema": cell.LOG_SCHEMA, "seq": 1, "generation": 1, "receipt_sha256": receipt_pin,
           "prev_line_sha256": cell.ZERO}
    snapshot = {"learning": {"pointer": _enc(pointer), "log": _enc(log) + b"\n", "artifacts": data}}
    manifest = _enc({"schema": memory.SCHEMA, "dim": 1,
                     "cells": {"learning": {"generation": 1, "receipt_sha256": receipt_pin}}})
    memory_pin = cell.sha256(manifest)
    descriptor = _enc({"schema": reader.DESCRIPTOR_SCHEMA, "scope": reader.SHARED_SCOPE,
                       "memory_sha256": memory_pin, "dim": 1, "encoder": encoder})
    monkeypatch.setattr(reader, "RUNTIME_IDENTITY", lambda **kw: dict(encoder))
    class FakeEncoder:
        def __init__(self, **kw): pass
        def query(self, text): return np.array([1.0], dtype=np.float32)
    monkeypatch.setattr(reader, "ENCODER_FACTORY", FakeEncoder)
    return dict(descriptor_bytes=descriptor, descriptor_sha256=cell.sha256(descriptor), manifest_bytes=manifest,
                expected_memory_sha256=memory_pin, cell_snapshots=snapshot, model_dir="synthetic-model",
                expected_runtime_revision="test-rev", enabled=True)


def test_native_faiss_snapshot_returns_projected_reference(monkeypatch):
    args = _fixture(monkeypatch)
    stream = io.BytesIO(b'{"situation":"retry network","time":"2026-01-02T00:00:00Z"}')
    result = reader.private_read_snapshot(stream=stream, **args)
    assert result["status"] == "ANSWER" and result["results"]
    assert all(item["ref_sha256"] and item["doc_id_sha256"] for item in result["results"])
    assert "retry network" not in json.dumps(result)
    assert "retry stale connection" not in json.dumps(result)


def test_native_faiss_rejects_hash_bound_invalid_serialization(monkeypatch):
    args = _fixture(monkeypatch, invalid_index=True)
    called = []
    deserialize = faiss.deserialize_index
    def spy(data):
        called.append(True)
        return deserialize(data)
    monkeypatch.setattr(faiss, "deserialize_index", spy)
    class NoRead:
        def read(self, *args): pytest.fail("invalid index consumed input")
    with pytest.raises(reader.PrivateReadRefused, match="snapshot_refused"):
        reader.private_read_snapshot(stream=NoRead(), **args)
    assert called
