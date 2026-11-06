"""Synthetic builder -> snapshot Q4 -> resolver; actual local FAISS, no model."""
import io
import json

import pytest

pytest.importorskip("faiss", reason="native FAISS is an optional local dependency")
pytest.importorskip("numpy", reason="NumPy is an optional local dependency")

from sandbox.mind.step2 import cell, shared_result_resolver as resolver, wd_q4_private_read as reader
from sandbox.mind.step2.test_shared_index_builder import (
    APPROVED, FakeEncoder, IDENTITY, PINS, SENTINEL, build,
)


def test_synthetic_snapshot_q4_resolves_only_pinned_projection(tmp_path, monkeypatch):
    built = build(tmp_path)
    root = tmp_path / "shared-v1"
    manifest_bytes = (root / "memory.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    projection_bytes = (root / "projection.json").read_bytes()
    projection = json.loads(projection_bytes)
    snapshots = {}
    for cell_id, binding in manifest["cells"].items():
        folder = root / "cells" / cell_id
        generation = folder / f"gen-{binding['generation']:06d}"
        paths = cell.paths(generation, cell_id)
        artifacts = {name: path.read_bytes() for name, path in paths.items()}
        artifacts["receipt"] = (generation / "receipt.json").read_bytes()
        snapshots[cell_id] = {"pointer": (folder / "current.json").read_bytes(),
                              "log": (folder / "LOG.jsonl").read_bytes(), "artifacts": artifacts}
    descriptor = (root / "descriptor.json").read_bytes()
    monkeypatch.setattr(reader, "RUNTIME_IDENTITY", lambda **kwargs: dict(IDENTITY))
    monkeypatch.setattr(reader, "ENCODER_FACTORY", lambda **kwargs: FakeEncoder())
    stream = io.BytesIO(b'{"situation":"message milestone from lead","time":"2026-10-31T13:00:00Z"}')
    result = reader.private_read_snapshot(descriptor_bytes=descriptor, descriptor_sha256=built["descriptor_sha256"],
        manifest_bytes=manifest_bytes, expected_memory_sha256=built["memory_sha256"], cell_snapshots=snapshots,
        stream=stream, model_dir="synthetic-model", expected_runtime_revision=IDENTITY["revision"], enabled=True)
    assert result["status"] == "ANSWER" and result["results"]
    assert SENTINEL not in json.dumps(result)
    # Match the existing resolver's narrow wire shape, without inventing any hashes.
    keys = ("ref_sha256", "doc_id_sha256", "ref_kind", "canonical_event")
    hits = [{key: item[key] for key in keys} for item in result["results"]]
    kwargs = dict(memory_manifest=manifest_bytes, expected_memory_sha256=built["memory_sha256"],
                  projection_bytes=projection_bytes, results=hits, query_utc="2026-10-31T13:00:00Z",
                  approved_tasks=APPROVED, **PINS)
    resolved = resolver.resolve_results(**kwargs)
    assert resolved and all(item["stored_text"] in {record["stored_text"] for record in projection["records"]}
                            for item in resolved)
    assert SENTINEL not in json.dumps(resolved)
    unknown = [dict(hits[0], ref_sha256="0" * 64)]
    with pytest.raises(resolver.ResolveRefused):
        resolver.resolve_results(**dict(kwargs, results=unknown))
