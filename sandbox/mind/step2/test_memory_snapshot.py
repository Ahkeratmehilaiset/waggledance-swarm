"""Synthetic memory snapshots only; FAISS calls are replaced by fakes."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from sandbox.mind.step2 import cell, memory, q4


def _enc(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _case(monkeypatch):
    import core.faiss_store as store

    ids = [f"id-{i}-{view}" for i in range(4) for view in ("w", "d")]
    vectors = np.ones((8, 1), dtype=np.float32)
    rows = [{"record_id": doc_id, "kind": "lesson", "cell_id": "learning",
             "time_utc": "2026-01-01T00:00:00.000000Z", "provenance": {
                 "record": f"record-{n // 2}", "view": "w" if n % 2 == 0 else "d",
                 "available_utc": "2026-01-01T00:00:00.000000Z", "file": "synthetic.md",
                 "tier": {"source_type": "learned_from_case"}}} for n, doc_id in enumerate(ids)]
    data = {"index": b"fake-index", "vectors": vectors.astype("<f4").tobytes(),
            "meta": _enc({"dim": 1, "doc_ids": ids,
                          "texts": ["retry stale connection" if i % 2 == 0 else "retry" for i in range(8)],
                          "metadata": [{} for _ in ids]}),
            "provenance": b"\n".join(map(_enc, rows)) + b"\n"}
    content = {"cell_id": "learning", "count": 8, "dim": 1, "cutoff_utc": "2027-01-01T00:00:00Z",
               "files": {key: hashlib.sha256(raw).hexdigest() for key, raw in data.items()},
               "ids_sha256": cell.sha256("\n".join(ids).encode()), "pairs_sha256": cell.pairs_sha256(ids, vectors)}
    data["receipt"] = _enc({"schema": cell.SCHEMA, "content": content,
                            "content_sha256": cell.sha256(cell.canonical(content).encode())})
    receipt_sha = cell.sha256(data["receipt"])
    pointer = {"generation": 1, "seq": 1, "receipt_sha256": receipt_sha}
    line = {"schema": cell.LOG_SCHEMA, "seq": 1, "generation": 1, "receipt_sha256": receipt_sha,
            "prev_line_sha256": cell.ZERO}
    snapshot = {"learning": {"pointer": _enc(pointer), "log": _enc(line) + b"\n", "artifacts": data}}
    manifest = {"schema": memory.SCHEMA, "dim": 1,
                "cells": {"learning": {"generation": 1, "receipt_sha256": receipt_sha}}}

    class FakeIndex:
        ntotal, d, metric_type = 8, 1, 0
        def __init__(self, dimension=1):
            pass
        def reconstruct(self, i):
            return vectors[i]
        def reconstruct_n(self, start, count):
            return vectors[start:start + count]

    monkeypatch.setattr(store.faiss, "IndexFlatIP", FakeIndex, raising=False)
    monkeypatch.setattr(store.faiss, "METRIC_INNER_PRODUCT", 0, raising=False)
    monkeypatch.setattr(store.faiss, "deserialize_index", lambda data: FakeIndex(), raising=False)
    return manifest, snapshot


def _load(manifest, snapshot, *, enabled=True):
    raw = _enc(manifest)
    return memory.Memory.from_snapshots(manifest_bytes=raw, expected_sha256=cell.sha256(raw),
                                        cell_snapshots=snapshot, enabled=enabled)


def test_snapshot_memory_answers_nonempty_q4_without_path_io(monkeypatch):
    manifest, snapshot = _case(monkeypatch)
    def no_io(*a, **kw):
        pytest.fail("snapshot path I/O")
    with monkeypatch.context() as guard:
        for name in ("open", "read_bytes", "exists", "is_dir"):
            guard.setattr(Path, name, no_io)
        guard.setattr(cell, "file_sha256", no_io)
        guard.setattr(cell, "_read_control", no_io)
        guard.setattr(cell, "verify_generation", no_io)
        loaded = _load(manifest, snapshot)
        encoder = SimpleNamespace(query=lambda text: np.array([1.0], dtype=np.float32))
        answer = q4.Q4(loaded, encoder).ask("retry network", "2026-01-02T00:00:00Z")
    assert answer["status"] == "ANSWER" and answer["results"]


@pytest.mark.parametrize("fault", ["missing_cell", "extra_cell", "bad_log", "wrong_receipt", "wrong_generation",
                                   "wrong_dim", "wrong_pin", "unknown_manifest_cell", "extra_snapshot_field"])
def test_snapshot_memory_refuses_missing_extra_or_unbound_data(monkeypatch, fault):
    manifest, snapshot = _case(monkeypatch)
    if fault == "missing_cell":
        snapshot.clear()
    elif fault == "extra_cell":
        snapshot["general"] = dict(snapshot["learning"])
    elif fault == "bad_log":
        snapshot["learning"]["log"] = b'{"bad":true}\n'
    elif fault == "wrong_receipt":
        manifest["cells"]["learning"]["receipt_sha256"] = "0" * 64
    elif fault == "wrong_generation":
        manifest["cells"]["learning"]["generation"] = 2
    elif fault == "wrong_dim":
        manifest["dim"] = 2
    elif fault == "unknown_manifest_cell":
        manifest["cells"]["../unknown"] = manifest["cells"]["learning"]
    elif fault == "extra_snapshot_field":
        snapshot["learning"]["extra"] = b"extra"
    if fault == "wrong_pin":
        raw = _enc(manifest)
        with pytest.raises(memory.MemoryError_):
            memory.Memory.from_snapshots(manifest_bytes=raw, expected_sha256="0" * 64,
                                         cell_snapshots=snapshot, enabled=True)
    else:
        with pytest.raises(memory.MemoryError_):
            _load(manifest, snapshot)


def test_snapshot_memory_is_default_off(monkeypatch):
    manifest, snapshot = _case(monkeypatch)
    with pytest.raises(memory.MemoryError_):
        _load(manifest, snapshot, enabled=False)


@pytest.mark.parametrize("fault", ["wrong_receipt", "wrong_generation", "bad_log", "aggregate_budget"])
def test_snapshot_memory_refuses_binding_and_budget_before_native_deserialization(monkeypatch, fault):
    import core.faiss_store as store
    manifest, snapshot = _case(monkeypatch)
    if fault == "wrong_receipt":
        manifest["cells"]["learning"]["receipt_sha256"] = "0" * 64
    elif fault == "wrong_generation":
        manifest["cells"]["learning"]["generation"] = 2
    elif fault == "bad_log":
        line = json.loads(snapshot["learning"]["log"])
        line["prev_line_sha256"] = "f" * 64
        snapshot["learning"]["log"] = _enc(line) + b"\n"
    else:
        monkeypatch.setattr(memory, "MAX_MEMORY_SNAPSHOT_BYTES", 1)
    monkeypatch.setattr(store.faiss, "deserialize_index", lambda *a: pytest.fail("native index reached"))
    with pytest.raises(memory.MemoryError_):
        _load(manifest, snapshot)


@pytest.mark.parametrize("fault", ["missing_log", "extra_artifact", "mutable_pointer", "wrong_sequence",
                                   "oversize_pointer", "oversize_log"])
def test_snapshot_control_and_artifact_limits_fail_closed(monkeypatch, fault):
    manifest, snapshot = _case(monkeypatch)
    item = snapshot["learning"]
    if fault == "missing_log":
        del item["log"]
    elif fault == "extra_artifact":
        item["artifacts"]["unexpected"] = b"data"
    elif fault == "mutable_pointer":
        item["pointer"] = bytearray(item["pointer"])
    elif fault == "wrong_sequence":
        pointer = json.loads(item["pointer"])
        pointer["seq"] = 2
        item["pointer"] = _enc(pointer)
    elif fault == "oversize_pointer":
        monkeypatch.setattr(cell, "MAX_POINTER_BYTES", 1)
    else:
        monkeypatch.setattr(cell, "MAX_LOG_BYTES", 1)
    with pytest.raises(memory.MemoryError_):
        _load(manifest, snapshot)


def test_snapshot_memory_rejects_numeric_string_receipt_dim(monkeypatch):
    manifest, snapshot = _case(monkeypatch)
    data = snapshot["learning"]["artifacts"]
    receipt = json.loads(data["receipt"])
    receipt["content"]["dim"] = "1"
    receipt["content_sha256"] = cell.sha256(cell.canonical(receipt["content"]).encode())
    data["receipt"] = _enc(receipt)
    digest = cell.sha256(data["receipt"])
    manifest["cells"]["learning"]["receipt_sha256"] = digest
    pointer = json.loads(snapshot["learning"]["pointer"])
    pointer["receipt_sha256"] = digest
    snapshot["learning"]["pointer"] = _enc(pointer)
    log = json.loads(snapshot["learning"]["log"])
    log["receipt_sha256"] = digest
    snapshot["learning"]["log"] = _enc(log) + b"\n"
    with pytest.raises(memory.MemoryError_):
        _load(manifest, snapshot)


def test_snapshot_memory_checks_entire_two_entry_log_chain(monkeypatch):
    manifest, snapshot = _case(monkeypatch)
    first = snapshot["learning"]["log"].rstrip(b"\n")
    second = json.loads(first)
    second["seq"] = 2
    second["prev_line_sha256"] = cell.sha256(first)
    pointer = json.loads(snapshot["learning"]["pointer"])
    pointer["seq"] = 2
    snapshot["learning"]["pointer"] = _enc(pointer)
    snapshot["learning"]["log"] = first + b"\n" + _enc(second) + b"\n"
    assert _load(manifest, snapshot).views["learning"].healthy
    second["prev_line_sha256"] = "f" * 64
    snapshot["learning"]["log"] = first + b"\n" + _enc(second) + b"\n"
    with pytest.raises(memory.MemoryError_):
        _load(manifest, snapshot)


@pytest.mark.parametrize("position,field", [("first", "seq"), ("first", "generation"), ("last", "generation")])
def test_snapshot_rejects_boolean_log_numbers_even_in_prior_lines(monkeypatch, position, field):
    import core.faiss_store as store
    manifest, snapshot = _case(monkeypatch)
    first = json.loads(snapshot["learning"]["log"])
    second = dict(first, seq=2)
    if position == "first":
        first[field] = True
    else:
        second[field] = True
    first_raw = _enc(first)
    second["prev_line_sha256"] = cell.sha256(first_raw)
    pointer = json.loads(snapshot["learning"]["pointer"])
    pointer["seq"] = 2
    snapshot["learning"]["pointer"] = _enc(pointer)
    snapshot["learning"]["log"] = first_raw + b"\n" + _enc(second) + b"\n"
    monkeypatch.setattr(store.faiss, "deserialize_index", lambda *a: pytest.fail("native index reached"))
    with pytest.raises(memory.MemoryError_):
        _load(manifest, snapshot)


def test_malformed_snapshot_refusal_does_not_chain_raw_input(monkeypatch):
    _, snapshot = _case(monkeypatch)
    raw = b'{"private-synthetic-sentinel":"unfinished'
    with pytest.raises(memory.MemoryError_) as caught:
        memory.Memory.from_snapshots(manifest_bytes=raw, expected_sha256=cell.sha256(raw),
                                     cell_snapshots=snapshot, enabled=True)
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    tb = caught.value.__traceback__
    while tb:
        if tb.tb_frame.f_code.co_name == "from_snapshots":
            assert raw not in tb.tb_frame.f_locals.values()
        tb = tb.tb_next


def test_snapshot_memory_aggregate_limit_covers_all_cells_before_native_call(monkeypatch):
    import core.faiss_store as store
    manifest, snapshot = _case(monkeypatch)
    manifest["cells"]["general"] = dict(manifest["cells"]["learning"])
    snapshot["general"] = dict(snapshot["learning"])
    item = snapshot["learning"]
    one_cell = len(item["pointer"]) + len(item["log"]) + sum(map(len, item["artifacts"].values()))
    monkeypatch.setattr(memory, "MAX_MEMORY_SNAPSHOT_BYTES", len(_enc(manifest)) + one_cell + 1)
    monkeypatch.setattr(store.faiss, "deserialize_index", lambda *a: pytest.fail("native index reached"))
    with pytest.raises(memory.MemoryError_):
        _load(manifest, snapshot)


@pytest.mark.parametrize("fault", ["pointer", "log", "receipt_hash", "receipt_dim"])
def test_all_cells_control_preflight_before_any_native_deserialization(monkeypatch, fault):
    import core.faiss_store as store
    manifest, snapshot = _case(monkeypatch)
    general = dict(snapshot["learning"])
    data = dict(general["artifacts"])
    rows = [json.loads(line) for line in data["provenance"].splitlines()]
    for row in rows:
        row["cell_id"] = "general"
    data["provenance"] = b"\n".join(map(_enc, rows)) + b"\n"
    receipt = json.loads(data["receipt"])
    receipt["content"]["cell_id"] = "general"
    receipt["content"]["files"]["provenance"] = cell.sha256(data["provenance"])
    receipt["content_sha256"] = cell.sha256(cell.canonical(receipt["content"]).encode())
    data["receipt"] = _enc(receipt)
    digest = cell.sha256(data["receipt"])
    pointer = json.loads(general["pointer"])
    pointer["receipt_sha256"] = digest
    log = json.loads(general["log"])
    log["receipt_sha256"] = digest
    general.update(pointer=_enc(pointer), log=_enc(log) + b"\n", artifacts=data)
    snapshot["general"] = general
    manifest["cells"]["general"] = {"generation": 1, "receipt_sha256": digest}
    if fault == "pointer":
        bad_pointer = json.loads(snapshot["learning"]["pointer"])
        bad_pointer["seq"] = 2
        snapshot["learning"]["pointer"] = _enc(bad_pointer)
    elif fault == "log":
        snapshot["learning"]["log"] = b'{"corrupt":true}\n'
    elif fault == "receipt_hash":
        snapshot["learning"]["artifacts"]["receipt"] += b" "
    else:
        learning_data = snapshot["learning"]["artifacts"]
        learning_receipt = json.loads(learning_data["receipt"])
        learning_receipt["content"]["dim"] = "1"
        learning_receipt["content_sha256"] = cell.sha256(cell.canonical(learning_receipt["content"]).encode())
        learning_data["receipt"] = _enc(learning_receipt)
        learning_digest = cell.sha256(learning_data["receipt"])
        manifest["cells"]["learning"]["receipt_sha256"] = learning_digest
        learning_pointer = json.loads(snapshot["learning"]["pointer"])
        learning_pointer["receipt_sha256"] = learning_digest
        snapshot["learning"]["pointer"] = _enc(learning_pointer)
        learning_log = json.loads(snapshot["learning"]["log"])
        learning_log["receipt_sha256"] = learning_digest
        snapshot["learning"]["log"] = _enc(learning_log) + b"\n"
    monkeypatch.setattr(store.faiss, "IndexFlatIP", lambda *a: pytest.fail("native constructor called before all control checks"))
    monkeypatch.setattr(store.faiss, "deserialize_index", lambda *a: pytest.fail("native deserializer called before all control checks"))
    with pytest.raises(memory.MemoryError_):
        _load(manifest, snapshot)
