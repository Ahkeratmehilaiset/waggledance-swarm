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
        ntotal, d, metric_type = 1, 1, 0

        def __init__(self, dimension=1):
            pass

        def reconstruct(self, index):
            return vectors[index]

    class FakeCollection:
        def __init__(self, name, dim, persist_dir, *, verified_meta, verified_index_bytes):
            assert verified_index_bytes == b"fake-index"
            parsed = verified_meta
            self._index, self.count = FakeIndex(), 1
            self._doc_ids, self._texts, self._metadata = parsed["doc_ids"], parsed["texts"], parsed["metadata"]

    import core.faiss_store as store
    monkeypatch.setattr(store.faiss, "IndexFlatIP", FakeIndex, raising=False)
    monkeypatch.setattr(store.faiss, "METRIC_INNER_PRODUCT", 0, raising=False)
    if use_real_collection:
        monkeypatch.setattr(store.faiss, "read_index", lambda path: FakeIndex(), raising=False)
        monkeypatch.setattr(store.faiss, "deserialize_index", lambda data: FakeIndex(), raising=False)
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


def _synthetic_vectors_case(tmp_path, monkeypatch, *, include_vectors=True, empty=False, generation_name="generation"):
    import core.faiss_store as store

    generation = tmp_path / generation_name
    generation.mkdir()
    files = cell.paths(generation, "learning")
    files["index"].parent.mkdir(parents=True)
    files["index"].write_bytes(b"fake-index")
    ids = [] if empty else ["synthetic-id"]
    files["meta"].write_bytes(_encoded({"dim": 1, "doc_ids": ids,
                                       "texts": [] if empty else ["synthetic"],
                                       "metadata": [] if empty else [{}]}))
    row = {"record_id": "synthetic-id", "kind": "event", "time_utc": "2026-01-01T00:00:00Z",
           "cell_id": "learning", "provenance": {}}
    files["provenance"].write_bytes(b"" if empty else _encoded(row) + b"\n")
    vectors = np.zeros((0, 1), dtype="<f4") if empty else np.array([[1.0]], dtype="<f4")
    if include_vectors:
        files["vectors"].write_bytes(vectors.tobytes())
    names = ("index", "meta", "provenance", "vectors") if include_vectors else ("index", "meta", "provenance")
    content = {"cell_id": "learning", "count": len(ids), "dim": 1, "cutoff_utc": "2027-01-01T00:00:00Z",
               "files": {name: cell.sha256(files[name].read_bytes()) for name in names},
               "ids_sha256": cell.sha256("\n".join(ids).encode()),
               "pairs_sha256": cell.pairs_sha256(ids, vectors)}
    receipt = _encoded({"schema": cell.SCHEMA, "content": content,
                        "content_sha256": cell.sha256(cell.canonical(content).encode())})
    (generation / "receipt.json").write_bytes(receipt)

    class FakeIndex:
        ntotal, d, metric_type = len(ids), 1, 0

        def __init__(self, dimension=1):
            pass

        def reconstruct(self, index):
            return vectors[index]

    monkeypatch.setattr(store.faiss, "IndexFlatIP", FakeIndex, raising=False)
    monkeypatch.setattr(store.faiss, "METRIC_INNER_PRODUCT", 0, raising=False)
    monkeypatch.setattr(store.faiss, "read_index", lambda path: FakeIndex(), raising=False)
    monkeypatch.setattr(store.faiss, "deserialize_index", lambda data: FakeIndex(), raising=False)
    return generation, files, cell.sha256(receipt)


def test_retained_vectors_swapped_after_streaming_hash_are_refused(tmp_path, monkeypatch):
    generation, files, receipt_sha = _synthetic_vectors_case(tmp_path, monkeypatch)
    original_hash = cell.file_sha256
    swaps = []

    def hash_then_swap(path):
        digest = original_hash(path)
        if path == files["vectors"]:
            path.write_bytes(np.array([[2.0]], dtype="<f4").tobytes())
            swaps.append(True)
        return digest

    monkeypatch.setattr(cell, "file_sha256", hash_then_swap)
    with pytest.raises(cell.CellError, match="^vectors_checksum$"):
        cell.verify_generation(generation, "learning", receipt_sha)
    assert swaps == [True]


def test_retained_vectors_swapped_after_snapshot_use_verified_bytes(tmp_path, monkeypatch):
    generation, files, receipt_sha = _synthetic_vectors_case(tmp_path, monkeypatch)
    original_read = Path.read_bytes
    swaps = []

    def read_then_swap(path):
        raw = original_read(path)
        if path == files["vectors"]:
            path.write_bytes(np.array([[2.0]], dtype="<f4").tobytes())
            swaps.append(True)
        return raw

    monkeypatch.setattr(Path, "read_bytes", read_then_swap)
    _, _, _, rebuild_possible = cell.verify_generation(generation, "learning", receipt_sha)
    assert swaps == [True] and rebuild_possible is True


def test_missing_retained_vectors_remains_readable_without_rebuild(tmp_path, monkeypatch):
    generation, _, receipt_sha = _synthetic_vectors_case(tmp_path, monkeypatch, include_vectors=False)
    _, _, _, rebuild_possible = cell.verify_generation(generation, "learning", receipt_sha)
    assert rebuild_possible is False


@pytest.mark.parametrize("kind", ["l2", "wrong_metric", "missing_metric", "wrapper", "subclass"])
def test_index_requires_exact_flat_inner_product_before_reconstruction(tmp_path, monkeypatch, kind):
    import core.faiss_store as store

    generation, _, receipt_sha = _synthetic_vectors_case(tmp_path, monkeypatch)
    reconstructed = []

    class IndexFlatIP:
        ntotal, d = 1, 1

        def __init__(self, dimension=1):
            self.metric_type = 0

        def reconstruct(self, index):
            reconstructed.append(index)
            return np.array([1.0], dtype="<f4")

    class IndexFlatL2(IndexFlatIP):
        pass

    class Wrapper(IndexFlatIP):
        pass

    class FakeSubclass(IndexFlatIP):
        pass

    candidate = {"l2": IndexFlatL2, "wrapper": Wrapper, "subclass": FakeSubclass}.get(kind, IndexFlatIP)()
    if kind in ("l2", "wrong_metric"):
        candidate.metric_type = 1
    if kind == "missing_metric":
        del candidate.metric_type
    monkeypatch.setattr(store.faiss, "IndexFlatIP", IndexFlatIP, raising=False)
    monkeypatch.setattr(store.faiss, "METRIC_INNER_PRODUCT", 0, raising=False)
    monkeypatch.setattr(store.faiss, "read_index", lambda path: candidate, raising=False)
    monkeypatch.setattr(store.faiss, "deserialize_index", lambda data: candidate, raising=False)
    with pytest.raises(cell.CellError, match="^index_semantics_invalid$"):
        cell.verify_generation(generation, "learning", receipt_sha)
    assert reconstructed == []


@pytest.mark.parametrize("empty", [False, True])
def test_verified_flat_inner_product_index_is_accepted_including_empty(tmp_path, monkeypatch, empty):
    generation, _, receipt_sha = _synthetic_vectors_case(tmp_path, monkeypatch, empty=empty)
    _, rows, _, rebuild_possible = cell.verify_generation(generation, "learning", receipt_sha)
    assert len(rows) == (0 if empty else 1) and rebuild_possible is True


def test_wrong_index_semantics_are_quarantined(tmp_path, monkeypatch):
    import core.faiss_store as store

    cell_dir = tmp_path / "learning"
    cell_dir.mkdir()
    _, _, receipt_sha = _synthetic_vectors_case(cell_dir, monkeypatch, generation_name="gen-000001")
    (cell_dir / "current.json").write_bytes(_encoded({"generation": 1, "seq": 1, "receipt_sha256": receipt_sha}))
    monkeypatch.setattr(cell, "read_log", lambda _: [{"generation": 1, "seq": 1, "receipt_sha256": receipt_sha}])

    class FakeL2(store.faiss.IndexFlatIP):
        metric_type = 1

    monkeypatch.setattr(store.faiss, "read_index", lambda path: FakeL2(), raising=False)
    monkeypatch.setattr(store.faiss, "deserialize_index", lambda data: FakeL2(), raising=False)
    view = cell.load(cell_dir, "learning")
    assert view.status == "QUARANTINED" and view.reason == "index_semantics_invalid"


def test_index_is_never_streamed_without_the_snapshot_budget(tmp_path, monkeypatch):
    import builtins

    generation, files, receipt_sha = _synthetic_vectors_case(tmp_path, monkeypatch)
    original_open = builtins.open
    streamed = []

    def spy_open(path, *args, **kwargs):
        if path == files["index"]:
            streamed.append(path)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", spy_open)
    assert cell.verify_generation(generation, "learning", receipt_sha)[3] is True
    assert streamed == []


def test_index_swap_before_snapshot_is_refused_before_deserialize(tmp_path, monkeypatch):
    import core.faiss_store as store

    generation, files, receipt_sha = _synthetic_vectors_case(tmp_path, monkeypatch)
    original_open = Path.open
    calls = []

    def open_after_swap(path, *args, **kwargs):
        if path == files["index"] and args and args[0] == "rb":
            path.write_bytes(b"fake-indeX")
            calls.append("swapped")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_after_swap)
    monkeypatch.setattr(store.faiss, "deserialize_index", lambda value: calls.append("deserialize"), raising=False)
    with pytest.raises(cell.CellError, match="^index_checksum$"):
        cell.verify_generation(generation, "learning", receipt_sha)
    assert calls == ["swapped"]


def test_index_swap_after_snapshot_deserializes_verified_bytes_and_meta(tmp_path, monkeypatch):
    import core.faiss_store as store

    generation, files, receipt_sha = _synthetic_vectors_case(tmp_path, monkeypatch)
    original_open = Path.open
    calls = []
    index_type = store.faiss.IndexFlatIP
    original_collection = cell.FaissCollection

    class SpyCollection(original_collection):
        def __init__(self, *args, **kwargs):
            assert kwargs["verified_meta"]["doc_ids"] == ["synthetic-id"]
            assert kwargs["verified_index_bytes"] == b"fake-index"
            calls.append("meta")
            super().__init__(*args, **kwargs)

    class SwapAfterRead:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def read(self, size):
            raw = self.stream.read(size)
            files["index"].write_bytes(b"fake-indeX")
            calls.append("swapped")
            return raw

    def open_snapshot(path, *args, **kwargs):
        stream = original_open(path, *args, **kwargs)
        if path == files["index"] and args and args[0] == "rb":
            return SwapAfterRead(stream)
        return stream

    def deserialize(array):
        assert array.dtype == np.uint8 and array.tobytes() == b"fake-index"
        calls.append("deserialize")
        return index_type()

    monkeypatch.setattr(Path, "open", open_snapshot)
    monkeypatch.setattr(cell, "FaissCollection", SpyCollection)
    monkeypatch.setattr(store.faiss, "read_index", lambda *_: pytest.fail("index path reopened"), raising=False)
    monkeypatch.setattr(store.faiss, "deserialize_index", deserialize, raising=False)
    _, _, _, rebuild_possible = cell.verify_generation(generation, "learning", receipt_sha)
    assert rebuild_possible is True and calls == ["swapped", "meta", "deserialize"]


@pytest.mark.parametrize("limit,accepted", [(0, False), (9, False), (10, True)])
def test_index_snapshot_limit_is_bounded_and_exact(tmp_path, monkeypatch, limit, accepted):
    generation, _, receipt_sha = _synthetic_vectors_case(tmp_path, monkeypatch)
    monkeypatch.setattr(cell, "MAX_Q4_INDEX_BYTES", limit)
    original_open = Path.open
    reads = []

    class ReadSizeSpy:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def read(self, size):
            reads.append(size)
            return self.stream.read(size)

    def open_spy(path, *args, **kwargs):
        stream = original_open(path, *args, **kwargs)
        if path.name == "index.faiss" and args and args[0] == "rb":
            return ReadSizeSpy(stream)
        return stream

    monkeypatch.setattr(Path, "open", open_spy)
    if accepted:
        assert cell.verify_generation(generation, "learning", receipt_sha)[3] is True
    else:
        with pytest.raises(cell.CellError, match="^index_over_budget$"):
            cell.verify_generation(generation, "learning", receipt_sha)
    assert reads == [limit + 1]


def test_missing_index_deserializer_refuses_without_path_fallback(tmp_path, monkeypatch):
    import core.faiss_store as store

    generation, _, receipt_sha = _synthetic_vectors_case(tmp_path, monkeypatch)
    monkeypatch.delattr(store.faiss, "deserialize_index")
    monkeypatch.setattr(store.faiss, "read_index", lambda *_: pytest.fail("index path reopened"), raising=False)
    with pytest.raises(cell.CellError, match="^index_unreadable$"):
        cell.verify_generation(generation, "learning", receipt_sha)


def test_missing_index_at_snapshot_quarantines(tmp_path, monkeypatch):
    cell_dir = tmp_path / "learning"
    cell_dir.mkdir()
    _, files, receipt_sha = _synthetic_vectors_case(cell_dir, monkeypatch, generation_name="gen-000001")
    (cell_dir / "current.json").write_bytes(_encoded({"generation": 1, "seq": 1, "receipt_sha256": receipt_sha}))
    monkeypatch.setattr(cell, "read_log", lambda _: [{"generation": 1, "seq": 1, "receipt_sha256": receipt_sha}])
    original_open = Path.open

    def missing_snapshot(path, *args, **kwargs):
        if path == files["index"] and args and args[0] == "rb":
            raise FileNotFoundError("synthetic private detail")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", missing_snapshot)
    view = cell.load(cell_dir, "learning")
    assert view.status == "QUARANTINED" and view.reason == "index_unreadable"
