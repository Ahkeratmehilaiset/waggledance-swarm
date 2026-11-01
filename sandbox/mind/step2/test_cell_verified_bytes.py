"""Synthetic witnesses only; never loads a native FAISS index or a model."""

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from sandbox.mind.step2 import cell


def _encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


@pytest.mark.parametrize("changed_kind", ["meta", "provenance"])
@pytest.mark.parametrize("use_real_collection", [False, True])
def test_cell_consumer_uses_verified_data_bytes_after_path_swap(tmp_path, monkeypatch, changed_kind,
                                                               use_real_collection):
    generation = tmp_path / "gen"
    generation.mkdir()
    files = cell.paths(generation, "learning")
    files["index"].parent.mkdir(parents=True)
    files["index"].write_bytes(b"fake-index")
    meta = {"name": "cell_learning", "dim": 1, "doc_ids": ["synthetic-id"],
            "texts": ["verified text"], "metadata": [{"tag": "verified"}]}
    row = {"record_id": "synthetic-id", "kind": "event", "time_utc": "2026-01-01T00:00:00Z",
           "cell_id": "learning", "provenance": {"tag": "verified"}}
    meta_bytes, row_bytes = _encoded(meta), _encoded(row) + b"\n"
    files["meta"].write_bytes(meta_bytes)
    files["provenance"].write_bytes(row_bytes)
    vectors = np.array([[1.0]], dtype="<f4")
    content = {"cell_id": "learning", "count": 1, "dim": 1, "cutoff_utc": "2027-01-01T00:00:00Z",
               "files": {name: hashlib.sha256(files[name].read_bytes()).hexdigest()
                         for name in ("index", "meta", "provenance")},
               "ids_sha256": hashlib.sha256(b"synthetic-id").hexdigest(),
               "pairs_sha256": cell.pairs_sha256(["synthetic-id"], vectors)}
    receipt = _encoded({"schema": cell.SCHEMA, "content": content,
                        "content_sha256": cell.sha256(cell.canonical(content).encode())})
    (generation / "receipt.json").write_bytes(receipt)

    class FakeIndex:
        ntotal, d = 1, 1

        def reconstruct(self, index):
            return vectors[index]

    class FakeCollection:
        def __init__(self, name, dim, persist_dir, *, verified_meta):
            parsed = verified_meta
            self._index, self.count = FakeIndex(), 1
            self._doc_ids, self._texts, self._metadata = parsed["doc_ids"], parsed["texts"], parsed["metadata"]

    if use_real_collection:
        import core.faiss_store as store
        monkeypatch.setattr(store.faiss, "IndexFlatIP", lambda dim: FakeIndex(), raising=False)
        monkeypatch.setattr(store.faiss, "read_index", lambda path: FakeIndex(), raising=False)
    else:
        monkeypatch.setattr(cell, "FaissCollection", FakeCollection)
    original_read = Path.read_bytes
    swapped = []

    def swap_after_read(path):
        raw = original_read(path)
        if path == files[changed_kind]:
            swapped.append(True)
            if changed_kind == "meta":
                changed = dict(meta, texts=["substituted text"], metadata=[{"tag": "substituted"}])
                path.write_bytes(_encoded(changed))
            else:
                changed = dict(row, provenance={"tag": "substituted"})
                path.write_bytes(_encoded(changed) + b"\n")
        return raw

    monkeypatch.setattr(Path, "read_bytes", swap_after_read)
    _, rows, collection, _ = cell.verify_generation(generation, "learning", cell.sha256(receipt))
    assert swapped == [True]
    assert rows[0]["provenance"]["tag"] == "verified"
    assert collection._texts == ["verified text"] and collection._metadata == [{"tag": "verified"}]


def test_strict_collection_failure_is_quarantined_without_private_detail(tmp_path, monkeypatch):
    cell_dir = tmp_path / "learning"
    generation = cell_dir / "gen-000001"
    generation.mkdir(parents=True)
    files = cell.paths(generation, "learning")
    files["index"].parent.mkdir(parents=True)
    files["index"].write_bytes(b"fake-index")
    files["meta"].write_bytes(_encoded({"dim": 1, "doc_ids": [], "texts": [], "metadata": []}))
    files["provenance"].write_bytes(b"")
    content = {"cell_id": "learning", "count": 0, "dim": 1, "cutoff_utc": "2027-01-01T00:00:00Z",
               "files": {name: cell.sha256(files[name].read_bytes()) for name in ("index", "meta", "provenance")},
               "ids_sha256": cell.sha256(b""), "pairs_sha256": cell.sha256(b"")}
    receipt = _encoded({"schema": cell.SCHEMA, "content": content,
                        "content_sha256": cell.sha256(cell.canonical(content).encode())})
    (generation / "receipt.json").write_bytes(receipt)
    receipt_sha = cell.sha256(receipt)
    (cell_dir / "current.json").write_bytes(_encoded({"generation": 1, "seq": 1, "receipt_sha256": receipt_sha}))
    monkeypatch.setattr(cell, "read_log", lambda _: [{"generation": 1, "seq": 1, "receipt_sha256": receipt_sha}])

    def refuse_collection(*args, **kwargs):
        raise ValueError("synthetic private detail")

    monkeypatch.setattr(cell, "FaissCollection", refuse_collection)
    view = cell.load(cell_dir, "learning")
    assert view.status == "QUARANTINED" and view.reason == "index_unreadable"
