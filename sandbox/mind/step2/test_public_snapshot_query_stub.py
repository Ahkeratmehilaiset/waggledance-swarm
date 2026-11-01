"""End-to-end public snapshot query using only fake FAISS and a fake encoder."""
import json

import numpy as np
import pytest

from sandbox.mind.step2 import shared_index_checkpoint as checkpoint, wd_q4_private_read as reader
from sandbox.mind.step2.test_verified_snapshot_checkpoint import enc, make_case


class _Encoder:
    def query(self, _text):
        return np.ones(1, dtype=np.float32)


def _fixture(monkeypatch, variant=None):
    import core.faiss_store as store
    args = make_case(monkeypatch, variant)
    index_type = store.faiss.IndexFlatIP

    def search(self, queries, count):
        indices = np.tile(np.arange(count, dtype=np.int64), (len(queries), 1))
        return np.ones(indices.shape, dtype=np.float32), indices

    monkeypatch.setattr(index_type, "search", search, raising=False)
    encoder = json.loads(args["active_artifact_bytes"]["descriptor.json"])["encoder"]
    monkeypatch.setattr(reader, "RUNTIME_IDENTITY", lambda **kwargs: dict(encoder))
    monkeypatch.setattr(reader, "ENCODER_FACTORY", lambda **kwargs: _Encoder())
    args.update(query_bytes=enc({"situation": "message unseen", "time": "2026-10-20T13:00:00Z"}),
                model_dir="synthetic-model", expected_runtime_revision=encoder["revision"])
    return args


def test_public_snapshot_query_is_off_by_default():
    assert checkpoint.query_public_snapshot() == {"status": "OFF"}


def test_public_snapshot_query_returns_only_approved_synthetic_projection(monkeypatch):
    args = _fixture(monkeypatch)
    result = checkpoint.query_public_snapshot(**args)
    projection = json.loads(args["active_artifact_bytes"]["projection.json"])
    assert result["status"] == "ANSWER" and result["results"]
    assert all(item["stored_text"] in {r["stored_text"] for r in projection["records"]}
               and item["canonical_event"] is False for item in result["results"])
    assert result["source_authentication"] == result["freshness"] == "UNKNOWN"
    assert "message unseen" not in json.dumps(result)


def test_public_snapshot_query_refuses_wrong_pointer_pin(monkeypatch):
    args = _fixture(monkeypatch)
    args["expected_pointer_sha256"] = "0" * 64
    monkeypatch.setattr(reader, "private_read_snapshot", lambda **kw: pytest.fail("unexpected query"))
    with pytest.raises(checkpoint.CheckpointRefused):
        checkpoint.query_public_snapshot(**args)


def test_public_snapshot_query_refuses_inconsistent_projection(monkeypatch):
    args = _fixture(monkeypatch, "text")
    monkeypatch.setattr(reader, "private_read_snapshot", lambda **kw: pytest.fail("unexpected query"))
    with pytest.raises(checkpoint.CheckpointRefused):
        checkpoint.query_public_snapshot(**args)
