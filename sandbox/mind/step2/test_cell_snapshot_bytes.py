"""Synthetic cell snapshot checks; the FAISS dependency is a fake, not native."""
from pathlib import Path

import pytest

from sandbox.mind.step2 import cell
from sandbox.mind.step2.test_cell_verified_bytes import _synthetic_vectors_case


def _case(tmp_path, monkeypatch, *, include_vectors=True):
    generation, files, receipt_sha = _synthetic_vectors_case(tmp_path, monkeypatch, include_vectors=include_vectors)
    raw = {"receipt": (generation / "receipt.json").read_bytes(),
           **{kind: path.read_bytes() for kind, path in files.items() if path.exists()}}
    return generation, raw, receipt_sha


def _run_no_path_io(monkeypatch, call):
    def refuse(*args, **kwargs):
        pytest.fail("cell snapshot reopened a path")
    with monkeypatch.context() as guard:
        for attr in ("open", "read_bytes", "exists", "is_dir"):
            guard.setattr(Path, attr, refuse)
        return call()


def test_snapshot_verified_bytes_are_consumed_without_reopening_paths(tmp_path, monkeypatch):
    _, snapshot, receipt_sha = _case(tmp_path, monkeypatch)
    receipt, rows, collection, rebuild = _run_no_path_io(monkeypatch, lambda:
        cell.verify_generation_snapshot("learning", receipt_sha, snapshot_bytes=snapshot, enabled=True))
    assert receipt["content"]["cell_id"] == "learning"
    assert rows[0]["record_id"] == "synthetic-id"
    assert collection.count == 1 and rebuild is True


@pytest.mark.parametrize("kind", ["receipt", "index", "meta", "provenance", "vectors"])
def test_snapshot_tampered_bytes_refuse_before_use(tmp_path, monkeypatch, kind):
    _, snapshot, receipt_sha = _case(tmp_path, monkeypatch)
    snapshot[kind] += b"tamper"
    with pytest.raises(cell.CellError):
        _run_no_path_io(monkeypatch, lambda:
            cell.verify_generation_snapshot("learning", receipt_sha, snapshot_bytes=snapshot, enabled=True))


@pytest.mark.parametrize("kind", ["receipt", "index", "meta", "provenance"])
def test_snapshot_missing_required_bytes_never_falls_back(tmp_path, monkeypatch, kind):
    _, snapshot, receipt_sha = _case(tmp_path, monkeypatch)
    del snapshot[kind]
    with pytest.raises(cell.CellError):
        _run_no_path_io(monkeypatch, lambda:
            cell.verify_generation_snapshot("learning", receipt_sha, snapshot_bytes=snapshot, enabled=True))


def test_snapshot_missing_vectors_preserves_legacy_no_rebuild(tmp_path, monkeypatch):
    _, snapshot, receipt_sha = _case(tmp_path, monkeypatch, include_vectors=False)
    _, _, collection, rebuild = _run_no_path_io(monkeypatch, lambda:
        cell.verify_generation_snapshot("learning", receipt_sha, snapshot_bytes=snapshot, enabled=True))
    assert collection.count == 1 and rebuild is False


def test_snapshot_consumer_is_default_off_and_size_bounded(tmp_path, monkeypatch):
    _, snapshot, receipt_sha = _case(tmp_path, monkeypatch)
    with pytest.raises(cell.CellError):
        cell.verify_generation_snapshot("learning", receipt_sha, snapshot_bytes=snapshot)
    monkeypatch.setattr(cell, "MAX_Q4_CELL_SNAPSHOT_BYTES", sum(map(len, snapshot.values())) - 1, raising=False)
    with pytest.raises(cell.CellError):
        cell.verify_generation_snapshot("learning", receipt_sha, snapshot_bytes=snapshot, enabled=True)


def test_snapshot_map_mutated_during_deserialize_keeps_original_verified_bytes(tmp_path, monkeypatch):
    import core.faiss_store as store
    _, snapshot, receipt_sha = _case(tmp_path, monkeypatch)
    original_deserialize = store.faiss.deserialize_index
    original_vectors = snapshot["vectors"]

    def swap_during_deserialize(value):
        snapshot["vectors"] = b"changed-after-snapshot"
        return original_deserialize(value)

    monkeypatch.setattr(store.faiss, "deserialize_index", swap_during_deserialize)
    _, _, collection, rebuild = _run_no_path_io(monkeypatch, lambda:
        cell.verify_generation_snapshot("learning", receipt_sha, snapshot_bytes=snapshot, enabled=True))
    assert snapshot["vectors"] != original_vectors and collection.count == 1 and rebuild is True


def test_snapshot_rejects_mutable_bytes_and_unexpected_keys(tmp_path, monkeypatch):
    _, snapshot, receipt_sha = _case(tmp_path, monkeypatch)
    for candidate in ({**snapshot, "meta": bytearray(snapshot["meta"])}, {**snapshot, "other": b"extra"}):
        with pytest.raises(cell.CellError):
            cell.verify_generation_snapshot("learning", receipt_sha, snapshot_bytes=candidate, enabled=True)


@pytest.mark.parametrize("cell_id", ["../learning", "learning/..", "C:\\private", "/absolute", "", ".", "unknown"])
def test_snapshot_refuses_non_topology_and_path_cell_ids_before_verification(tmp_path, monkeypatch, cell_id):
    _, snapshot, receipt_sha = _case(tmp_path, monkeypatch)
    monkeypatch.setattr(cell, "_verify_generation", lambda *a, **kw: pytest.fail("unexpected verification"))
    with pytest.raises(cell.CellError):
        cell.verify_generation_snapshot(cell_id, receipt_sha, snapshot_bytes=snapshot, enabled=True)


def test_snapshot_collection_exposes_no_mutators_or_mutable_readback(tmp_path, monkeypatch):
    import numpy as np
    import core.faiss_store as store
    _, snapshot, receipt_sha = _case(tmp_path, monkeypatch)
    monkeypatch.setattr(store.faiss.IndexFlatIP, "search", lambda self, vec, k:
                        (np.array([[1.0]], dtype=np.float32), np.array([[0]], dtype=np.int64)), raising=False)
    _, _, collection, _ = cell.verify_generation_snapshot("learning", receipt_sha,
                                                           snapshot_bytes=snapshot, enabled=True)
    for attr in ("add", "add_batch", "save"):
        assert not hasattr(collection, attr)
    assert not hasattr(collection._index, "add") and not hasattr(collection._index, "save")
    with pytest.raises((AttributeError, TypeError)):
        collection._texts = ("replaced",)
    assert collection._texts == ("synthetic",) and collection.count == 1
    answer = collection.search(np.array([1.0], dtype=np.float32), 1)
    assert answer[0].doc_id == "synthetic-id"
    answer[0].metadata["mutated"] = True
    assert collection.search(np.array([1.0], dtype=np.float32), 1)[0].metadata == {}
    view = cell.CellView("learning", "HEALTHY", collection=collection,
                         receipt={"content": {"dim": 1}})
    assert view.search(np.array([1.0], dtype=np.float32), 1) == [("synthetic-id", 1.0)]


def test_snapshot_q4_reads_nonempty_pairs_without_io_and_reconstruct_is_defensive(tmp_path, monkeypatch):
    import hashlib
    import json
    from types import SimpleNamespace
    import numpy as np
    import core.faiss_store as store
    from sandbox.mind.step2 import q4

    _, _, _ = _case(tmp_path, monkeypatch)
    ids = [f"id-{i}-{view}" for i in range(4) for view in ("w", "d")]
    vectors = np.ones((8, 1), dtype=np.float32)
    texts = ["retry stale connection" if n % 2 == 0 else "retry" for n in range(8)]
    rows = [{"record_id": doc_id, "kind": "lesson", "cell_id": "learning",
             "time_utc": "2026-01-01T00:00:00.000000Z", "provenance": {
                 "record": f"record-{n // 2}", "view": "w" if n % 2 == 0 else "d",
                 "available_utc": "2026-01-01T00:00:00.000000Z", "file": "synthetic.md",
                 "tier": {"source_type": "learned_from_case"}}} for n, doc_id in enumerate(ids)]
    encode = lambda obj: json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()
    snapshot = {"index": b"fake-index", "vectors": vectors.astype("<f4").tobytes(),
                "meta": encode({"dim": 1, "doc_ids": ids, "texts": texts, "metadata": [{} for _ in ids]}),
                "provenance": b"\n".join(map(encode, rows)) + b"\n"}
    content = {"cell_id": "learning", "count": len(ids), "dim": 1, "cutoff_utc": "2027-01-01T00:00:00Z",
               "files": {key: hashlib.sha256(data).hexdigest() for key, data in snapshot.items()},
               "ids_sha256": cell.sha256("\n".join(ids).encode()), "pairs_sha256": cell.pairs_sha256(ids, vectors)}
    snapshot["receipt"] = encode({"schema": cell.SCHEMA, "content": content,
                                  "content_sha256": cell.sha256(cell.canonical(content).encode())})
    class FakeIndex:
        ntotal, d, metric_type = 8, 1, 0
        def __init__(self, dimension=1):
            pass
        def reconstruct(self, index):
            return vectors[index]
        def reconstruct_n(self, start, count):
            return vectors[start:start + count]
    monkeypatch.setattr(store.faiss, "IndexFlatIP", FakeIndex)
    monkeypatch.setattr(store.faiss, "deserialize_index", lambda data: FakeIndex())
    receipt, verified_rows, collection, _ = _run_no_path_io(monkeypatch, lambda:
        cell.verify_generation_snapshot("learning", cell.sha256(snapshot["receipt"]),
                                        snapshot_bytes=snapshot, enabled=True))
    one = collection._index.reconstruct_n(0, 8)
    one[0, 0] = 42.0
    assert collection._index.reconstruct_n(0, 8)[0, 0] == 1.0
    for start, count in ((-1, 1), (0, 9), (True, 1), (0, -1)):
        with pytest.raises((ValueError, cell.CellError)):
            collection._index.reconstruct_n(start, count)
    assert collection._texts is collection._texts
    view = cell.CellView("learning", "HEALTHY", collection=collection, rows=verified_rows, receipt=receipt)
    memory = SimpleNamespace(views={"learning": view}, memory={"dim": 1},
                             topology=SimpleNamespace(get_neighbors=lambda *a, **kw: ["learning"]))
    encoder = SimpleNamespace(query=lambda _: np.array([1.0], dtype=np.float32))
    answer = _run_no_path_io(monkeypatch, lambda: q4.Q4(memory, encoder).ask("retry network", "2026-01-02T00:00:00Z"))
    assert answer["status"] == "ANSWER" and answer["results"]


def test_snapshot_reconstruct_budget_refuses_before_native_call(monkeypatch):
    class FakeIndex:
        def reconstruct_n(self, start, count):
            pytest.fail("over-budget reconstruction reached native index")
    index = cell._ReadOnlySnapshotIndex(FakeIndex(), 2, 2)
    monkeypatch.setattr(cell, "MAX_Q4_INDEX_BYTES", 15)
    with pytest.raises(cell.CellError):
        index.reconstruct_n(0, 2)


def test_snapshot_collection_rejects_mutable_text_values():
    from types import SimpleNamespace
    candidate = SimpleNamespace(search=lambda *a: [], _texts=[{"mutable": "text"}],
                                _doc_ids=["a"], count=1, _index=None, dim=1)
    with pytest.raises(cell.CellError):
        cell._ReadOnlySnapshotCollection(candidate, verified_dim=1)


def test_snapshot_reconstruction_accepts_verified_numeric_string_metadata_dim(tmp_path, monkeypatch):
    import json
    import numpy as np
    import core.faiss_store as store

    _, snapshot, _ = _case(tmp_path, monkeypatch)
    meta = json.loads(snapshot["meta"])
    meta["dim"] = "1"
    snapshot["meta"] = json.dumps(meta, sort_keys=True, separators=(",", ":")).encode()
    receipt = json.loads(snapshot["receipt"])
    receipt["content"]["files"]["meta"] = cell.sha256(snapshot["meta"])
    receipt["content_sha256"] = cell.sha256(cell.canonical(receipt["content"]).encode())
    snapshot["receipt"] = json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode()
    monkeypatch.setattr(store.faiss.IndexFlatIP, "reconstruct_n",
                        lambda self, start, count: np.ones((count, 1), dtype=np.float32), raising=False)
    _, _, collection, _ = cell.verify_generation_snapshot(
        "learning", cell.sha256(snapshot["receipt"]), snapshot_bytes=snapshot, enabled=True)
    assert collection._index.reconstruct_n(0, 1).shape == (1, 1)
