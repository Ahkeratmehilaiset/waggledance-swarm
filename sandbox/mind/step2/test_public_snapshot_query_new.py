"""Synthetic public snapshot queries: real local FAISS, no real model or data."""
import json
from pathlib import Path

import numpy as np
import pytest

faiss = pytest.importorskip("faiss")

from sandbox.mind.step2 import cell, shared_index_checkpoint as checkpoint, wd_q4_private_read as reader
from sandbox.mind.step2.test_verified_snapshot_checkpoint import enc, make_case, sha


class _Encoder:
    def query(self, _text):
        return np.ones(1, dtype=np.float32)


def _fixture(monkeypatch, variant=None):
    # make_case creates a fully consistent graph of synthetic hashes and a fake
    # index. Replace only that index with a REAL one, then rebind every parent.
    original_index, original_deserialize = faiss.IndexFlatIP, faiss.deserialize_index
    original_metric = faiss.METRIC_INNER_PRODUCT
    args = make_case(monkeypatch, variant)
    monkeypatch.setattr(faiss, "IndexFlatIP", original_index)
    monkeypatch.setattr(faiss, "deserialize_index", original_deserialize)
    monkeypatch.setattr(faiss, "METRIC_INNER_PRODUCT", original_metric)
    item = args["cell_snapshots"]["learning"]
    artifacts = item["artifacts"]
    meta = json.loads(artifacts["meta"])
    index = original_index(1)
    index.add(np.ones((len(meta["doc_ids"]), 1), dtype=np.float32))
    artifacts["index"] = faiss.serialize_index(index).tobytes()
    receipt = json.loads(artifacts["receipt"])
    receipt["content"]["files"]["index"] = sha(artifacts["index"])
    receipt["content_sha256"] = sha(cell.canonical(receipt["content"]).encode())
    artifacts["receipt"] = enc(receipt)
    pointer = json.loads(item["pointer"])
    pointer["receipt_sha256"] = sha(artifacts["receipt"])
    item["pointer"] = enc(pointer)
    log = json.loads(item["log"])
    log["receipt_sha256"] = pointer["receipt_sha256"]
    item["log"] = enc(log) + b"\n"
    active = args["active_artifact_bytes"]
    memory = json.loads(active["memory.json"])
    memory["cells"]["learning"]["receipt_sha256"] = pointer["receipt_sha256"]
    active["memory.json"] = enc(memory)
    descriptor = json.loads(active["descriptor.json"])
    descriptor["memory_sha256"] = sha(active["memory.json"])
    active["descriptor.json"] = enc(descriptor)
    build = json.loads(active["build_receipt.json"])
    build["memory_sha256"] = sha(active["memory.json"])
    build["descriptor_sha256"] = sha(active["descriptor.json"])
    active["build_receipt.json"] = enc(build)
    top = json.loads(args["pointer_bytes"])
    top["artifacts"] = {key: sha(active[name]) for key, name in checkpoint.ARTIFACTS.items()}
    args["pointer_bytes"] = enc(top)
    args["expected_pointer_sha256"] = sha(args["pointer_bytes"])
    identity = descriptor["encoder"]
    monkeypatch.setattr(reader, "RUNTIME_IDENTITY", lambda **kw: dict(identity))
    monkeypatch.setattr(reader, "ENCODER_FACTORY", lambda **kw: _Encoder())
    args.update(query_bytes=enc({"situation": "message unseen", "time": "2026-10-20T13:00:00Z"}),
                model_dir="synthetic-model", expected_runtime_revision=identity["revision"])
    return args


def test_off_is_off_and_no_reads():
    assert checkpoint.query_public_snapshot() == {"status": "OFF"}


def test_real_faiss_query_resolves_approved_synthetic_metadata(monkeypatch):
    args = _fixture(monkeypatch)
    result = checkpoint.query_public_snapshot(**args)
    assert result["status"] == "ANSWER"
    assert result["results"]
    projection = json.loads(args["active_artifact_bytes"]["projection.json"])
    assert all(item["stored_text"] in {r["stored_text"] for r in projection["records"]}
               for item in result["results"])
    assert result["source_authentication"] == result["freshness"] == "UNKNOWN"
    assert all("canonical_event" in item and item["canonical_event"] is False for item in result["results"])


@pytest.mark.parametrize("variant", ["text", "wrong_event_ref", "duplicate_view", "missing_view", "private_task", "private_type"])
def test_mismatched_projection_refuses_before_query(monkeypatch, variant):
    args = _fixture(monkeypatch, variant)
    monkeypatch.setattr(reader, "private_read_snapshot", lambda **kw: pytest.fail("backend queried"))
    with pytest.raises(checkpoint.CheckpointRefused):
        checkpoint.query_public_snapshot(**args)


@pytest.mark.parametrize("field", ["expected_pointer_sha256", "policy_sha256", "roster_sha256",
                                  "expected_source_sha256", "expected_encoder_sha256", "expected_public_policy_sha256"])
def test_wrong_pin_refuses_before_query(monkeypatch, field):
    args = _fixture(monkeypatch)
    args[field] = "0" * 64
    monkeypatch.setattr(reader, "private_read_snapshot", lambda **kw: pytest.fail("backend queried"))
    with pytest.raises(checkpoint.CheckpointRefused):
        checkpoint.query_public_snapshot(**args)


def test_missing_or_tampered_cell_refuses(monkeypatch):
    args = _fixture(monkeypatch)
    args["cell_snapshots"]["learning"]["artifacts"]["index"] += b"x"
    monkeypatch.setattr(reader, "private_read_snapshot", lambda **kw: pytest.fail("backend queried"))
    with pytest.raises(checkpoint.CheckpointRefused):
        checkpoint.query_public_snapshot(**args)


def test_no_checkpoint_or_cell_path_fallback(monkeypatch):
    args = _fixture(monkeypatch)
    def no_io(*a, **kw):
        pytest.fail("unexpected checkpoint or cell path read")
    with monkeypatch.context() as guard:
        guard.setattr(Path, "open", no_io)
        guard.setattr(Path, "read_bytes", no_io)
        guard.setattr(checkpoint, "_read", no_io)
        guard.setattr(checkpoint, "_secure", no_io)
        guard.setattr(reader, "read_descriptor", no_io)
        guard.setattr(reader, "bind_memory", no_io)
        assert checkpoint.query_public_snapshot(**args)["status"] == "ANSWER"


def test_unrecognized_hit_cannot_be_resolved(monkeypatch):
    args = _fixture(monkeypatch)
    original = reader.private_read_snapshot
    def altered(**kwargs):
        value = original(**kwargs)
        assert value["results"]
        value["results"][0]["ref_sha256"] = "0" * 64
        return value
    monkeypatch.setattr(reader, "private_read_snapshot", altered)
    with pytest.raises(checkpoint.CheckpointRefused):
        checkpoint.query_public_snapshot(**args)


def test_runtime_revision_mismatch_refuses_before_query(monkeypatch):
    args = _fixture(monkeypatch)
    args["expected_runtime_revision"] = "wrong"
    monkeypatch.setattr(reader, "private_read_snapshot", lambda **kw: pytest.fail("backend queried"))
    with pytest.raises(checkpoint.CheckpointRefused):
        checkpoint.query_public_snapshot(**args)


def test_wrong_runtime_encoder_refuses_before_loading_any_index(monkeypatch):
    from sandbox.mind.step2 import memory
    args = _fixture(monkeypatch)
    descriptor = json.loads(args["active_artifact_bytes"]["descriptor.json"])
    wrong = dict(descriptor["encoder"], weights_sha256="0" * 64)
    monkeypatch.setattr(reader, "RUNTIME_IDENTITY", lambda **kw: wrong)
    monkeypatch.setattr(memory.Memory, "from_snapshots", lambda **kw: pytest.fail("native index loaded"))
    with pytest.raises(checkpoint.CheckpointRefused):
        checkpoint.query_public_snapshot(**args)


@pytest.mark.parametrize("when", ["2999-01-01T00:00:00Z", "2026-10-20T13:00:00.0000001Z"])
def test_future_or_submicrosecond_query_refuses_before_loading_cells(monkeypatch, when):
    from sandbox.mind.step2 import memory
    args = _fixture(monkeypatch)
    args["query_bytes"] = enc({"situation": "synthetic", "time": when})
    monkeypatch.setattr(memory.Memory, "from_snapshots", lambda **kw: pytest.fail("native index loaded"))
    with pytest.raises(checkpoint.CheckpointRefused):
        checkpoint.query_public_snapshot(**args)


def test_failed_backend_refusal_does_not_keep_query_snapshot_or_policy_in_frame(monkeypatch):
    import traceback
    args = _fixture(monkeypatch)
    sentinel = "private-synthetic-query-sentinel"
    args["query_bytes"] = enc({"situation": "message unseen " + sentinel, "time": "2026-10-20T13:00:00Z"})
    private = tuple(args[key] for key in ("query_bytes", "cell_snapshots", "active_artifact_bytes",
                    "history_bytes", "policy_bytes", "public_policy_bytes", "roster_bytes"))
    def fail(**_kwargs):
        raise ValueError("synthetic backend refusal")
    monkeypatch.setattr(reader, "private_read_snapshot", fail)
    with pytest.raises(checkpoint.CheckpointRefused) as caught:
        checkpoint.query_public_snapshot(**args)
    frames = [frame for frame, _ in traceback.walk_tb(caught.value.__traceback__)
              if frame.f_code.co_name == "query_public_snapshot"]
    assert len(frames) == 1
    assert all(all(value is not secret for secret in private) for value in frames[0].f_locals.values())
    assert sentinel not in repr(frames[0].f_locals)
    assert caught.value.__context__ is None


def test_later_refusal_does_not_retain_untrusted_backend_binding(monkeypatch):
    import traceback
    args = _fixture(monkeypatch)
    sentinel = "private-synthetic-binding-sentinel"
    args["query_bytes"] = enc({"situation": "message unseen " + sentinel, "time": "2026-10-20T13:00:00Z"})
    original = reader.private_read_snapshot
    def poisoned(**kwargs):
        value = original(**kwargs)
        value["binding"]["extra"] = args["query_bytes"]
        value["results"][0]["ref_sha256"] = "0" * 64
        return value
    monkeypatch.setattr(reader, "private_read_snapshot", poisoned)
    with pytest.raises(checkpoint.CheckpointRefused) as caught:
        checkpoint.query_public_snapshot(**args)
    frames = [frame for frame, _ in traceback.walk_tb(caught.value.__traceback__)
              if frame.f_code.co_name == "query_public_snapshot"]
    assert len(frames) == 1
    assert sentinel not in repr(frames[0].f_locals)
    assert caught.value.__context__ is None


def test_keyboard_interrupt_propagates_without_retaining_own_frame_inputs(monkeypatch):
    import traceback
    args = _fixture(monkeypatch)
    sentinel = "private-synthetic-interrupt-sentinel"
    args["query_bytes"] = enc({"situation": sentinel, "time": "2026-10-20T13:00:00Z"})
    private = tuple(args[key] for key in ("query_bytes", "cell_snapshots", "active_artifact_bytes",
                    "history_bytes", "policy_bytes", "public_policy_bytes", "roster_bytes"))
    def interrupted(**_kwargs):
        raise KeyboardInterrupt()
    monkeypatch.setattr(reader, "private_read_snapshot", interrupted)
    with pytest.raises(KeyboardInterrupt) as caught:
        checkpoint.query_public_snapshot(**args)
    frames = [frame for frame, _ in traceback.walk_tb(caught.value.__traceback__)
              if frame.f_code.co_name == "query_public_snapshot"]
    assert len(frames) == 1
    assert all(all(value is not secret for secret in private) for value in frames[0].f_locals.values())
    assert sentinel not in repr(frames[0].f_locals)
