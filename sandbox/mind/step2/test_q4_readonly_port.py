"""Synthetic tests for the read-only Q4 port; no model or real memory."""

import ast
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("faiss")

from sandbox.mind.step2 import cell, encode, memory, q4


class FakeEncoder:
    def query(self, text):
        return np.array([1.0, 0.0], dtype=np.float32)


class FakeIndex:
    def __init__(self, count):
        self.count = count

    def reconstruct_n(self, start, count):
        assert (start, count) == (0, self.count)
        return np.tile(np.array([[1.0, 0.0]], dtype=np.float32), (count, 1))


def fake_memory(*, quarantined=False, records=1):
    rows = []
    texts = []
    for index in range(records):
        for kind in ("w", "d"):
            rows.append({"kind": "lesson", "time_utc": "2026-01-01T00:00:00.000000Z",
                         "provenance": {"record": f"synthetic-{index}", "view": kind,
                                        "available_utc": "2026-01-01T00:00:00.000000Z",
                                        "file": "synthetic.md", "tier": {"source_type": "learned_from_case"}}})
            texts.append("retry stale connection" if kind == "w" else "retry")
    collection = SimpleNamespace(_index=FakeIndex(len(rows)), count=len(rows), _texts=texts)
    views = {"learning": SimpleNamespace(healthy=True, collection=collection, rows=rows)}
    if quarantined:
        views["safety"] = SimpleNamespace(healthy=False, reason="synthetic_refusal")
    topology = SimpleNamespace(get_neighbors=lambda cell_id, max_ring: ["learning"])
    return SimpleNamespace(views=views, memory={"dim": 2}, topology=topology)


def test_q4_finds_synthetic_record_without_returning_its_text():
    answer = q4.Q4(fake_memory(), FakeEncoder()).ask("retry network", "2026-01-02T00:00:00Z")
    assert answer["status"] == "ANSWER"
    assert answer["rule"] == "q4-rule-v1.1"
    assert answer["results"][0]["ref"] == "synthetic.md"
    assert "retry stale connection" not in str(answer)


def test_q4_refuses_invalid_time():
    engine = q4.Q4(fake_memory(), FakeEncoder())
    assert engine.ask("retry network", "not-a-time")["status"] == "REFUSED"


@pytest.mark.parametrize("count", [1, 2, 3, 5])
def test_q4_never_accepts_zero_bm25_candidates(count):
    engine = q4.Q4(fake_memory(records=count), FakeEncoder())
    assert engine.ask("unrelated token", "2026-01-02T00:00:00Z")["status"] == "ABSTAIN"


def test_q4_marks_quarantined_cells_degraded():
    answer = q4.Q4(fake_memory(quarantined=True), FakeEncoder()).ask(
        "retry network", "2026-01-02T00:00:00Z"
    )
    assert answer["status"] == "DEGRADED"
    assert answer["results"][0]["fallback"] is True


@pytest.mark.parametrize("quarantined,expected", [(False, "ABSTAIN"), (True, "DEGRADED")])
def test_q4_empty_memory_is_a_reasoned_result_not_an_exception(quarantined, expected):
    answer = q4.Q4(fake_memory(records=0, quarantined=quarantined), FakeEncoder()).ask(
        "synthetic query", "2026-01-02T00:00:00Z"
    )
    assert answer["status"] == expected
    assert answer["reason"] == "empty_cell"


def test_e5_query_contract_with_stubbed_dependencies(monkeypatch):
    calls = []

    class StubModel:
        def __init__(self, name, **kwargs):
            calls.append((name, kwargs))

        def encode(self, inputs, **kwargs):
            calls.append((inputs, kwargs))
            return np.array([[1.0, 0.0]], dtype=np.float32)

    monkeypatch.setitem(sys.modules, "sentence_transformers", SimpleNamespace(
        __version__="synthetic", SentenceTransformer=StubModel))
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(
        __version__="synthetic", get_num_threads=lambda: 1))
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")
    encoder = encode.E5(revision="synthetic-verified-revision")
    result = encoder.query("synthetic query")
    assert calls[0][1]["local_files_only"] is True
    assert calls[1][0] == ["query: synthetic query"]
    assert result.tolist() == [1.0, 0.0]


def test_e5_passes_the_verified_revision_to_the_offline_loader(monkeypatch):
    calls = []

    def fake_loader(name, **kwargs):
        calls.append((name, kwargs))
        return object()

    monkeypatch.setitem(sys.modules, "sentence_transformers", SimpleNamespace(
        __version__="synthetic", SentenceTransformer=fake_loader))
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(
        __version__="synthetic", get_num_threads=lambda: 1))
    encode.E5(revision="synthetic-verified-revision")
    assert calls == [(encode.MODEL, {"device": "cpu", "local_files_only": True,
                                     "revision": "synthetic-verified-revision"})]


def test_loads_and_queries_a_synthetic_faiss_generation_without_mutating_it(tmp_path):
    import faiss

    cell_root = tmp_path / "cells" / "learning"
    generation = cell_root / "gen-000001"
    files = cell.paths(generation, "learning")
    files["index"].parent.mkdir(parents=True)
    vectors = np.array([[1.0, 0.0], [1.0, 0.0]], dtype=np.float32)
    index = faiss.IndexFlatIP(2)
    index.add(vectors)
    files["index"].write_bytes(faiss.serialize_index(index).tobytes())
    ids = ["synthetic#w", "synthetic#d"]
    metadata = {"dim": 2, "doc_ids": ids,
                "texts": ["retry stale connection", "retry"], "metadata": [{}, {}]}
    files["meta"].write_text(json.dumps(metadata), encoding="utf-8")
    rows = [{"record_id": item, "kind": "lesson", "cell_id": "learning",
             "time_utc": "2026-01-01T00:00:00.000000Z",
             "provenance": {"record": "synthetic", "view": view,
                            "available_utc": "2026-01-01T00:00:00.000000Z",
                            "file": "synthetic.md", "tier": {"source_type": "learned_from_case"}}}
            for item, view in zip(ids, ("w", "d"))]
    files["provenance"].write_text("".join(cell.canonical(row) + "\n" for row in rows), encoding="utf-8")
    content = {"cell_id": "learning", "count": 2, "dim": 2,
               "cutoff_utc": "2026-01-02T00:00:00.000000Z",
               "files": {name: cell.file_sha256(files[name]) for name in ("index", "meta", "provenance")},
               "ids_sha256": cell.sha256("\n".join(ids).encode()),
               "pairs_sha256": cell.pairs_sha256(ids, vectors)}
    receipt = {"schema": cell.SCHEMA, "content": content,
               "content_sha256": cell.sha256(cell.canonical(content).encode())}
    (generation / "receipt.json").write_text(cell.canonical(receipt), encoding="utf-8")
    receipt_sha = cell.file_sha256(generation / "receipt.json")
    pointer = {"generation": 1, "seq": 1, "receipt_sha256": receipt_sha}
    (cell_root / "current.json").write_text(cell.canonical(pointer), encoding="utf-8")
    log = {"schema": cell.LOG_SCHEMA, "prev_line_sha256": cell.ZERO, **pointer}
    (cell_root / "LOG.jsonl").write_text(cell.canonical(log) + "\n", encoding="utf-8")
    (tmp_path / "memory.json").write_text(json.dumps({"schema": memory.SCHEMA, "dim": 2,
        "cells": {"learning": pointer}}), encoding="utf-8")
    before = {str(path.relative_to(tmp_path)): path.read_bytes()
              for path in tmp_path.rglob("*") if path.is_file()}
    loaded = memory.Memory(tmp_path)
    answer = q4.Q4(loaded, FakeEncoder()).ask("retry network", "2026-01-03T00:00:00Z")
    after = {str(path.relative_to(tmp_path)): path.read_bytes()
             for path in tmp_path.rglob("*") if path.is_file()}
    assert answer["status"] == "ANSWER"
    assert answer["results"][0]["ref"] == "synthetic.md"
    assert before == after


def test_readonly_modules_do_not_expose_original_build_or_rebuild_api():
    root = Path(__file__).parent
    forbidden = {"build", "publish", "write_atomic", "write_generation", "rebuild_from_retained"}
    for name in ("cell.py", "corpus.py", "memory.py", "encode.py"):
        tree = ast.parse((root / name).read_text(encoding="utf-8"))
        assert not ({node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))} & forbidden)


def test_memory_manifest_over_budget_is_refused_before_json_parse(tmp_path):
    (tmp_path / "memory.json").write_bytes(b"x" * (memory.MAX_MEMORY_MANIFEST_BYTES + 1))
    with pytest.raises(memory.MemoryError_, match="memory_over_budget"):
        memory.memory_sha256(tmp_path)
    with pytest.raises(memory.MemoryError_, match="memory_unreadable"):
        memory.Memory(tmp_path)


@pytest.mark.parametrize("which,limit,reason", [
    ("pointer", 64 * 1024, "pointer_over_budget"),
    ("receipt", 1024 * 1024, "receipt_over_budget"),
    ("log", 16 * 1024 * 1024, "log_over_budget"),
])
def test_oversized_cell_control_is_quarantined(tmp_path, which, limit, reason):
    cell_root = tmp_path / "cells" / "learning"
    generation = cell_root / "gen-000001"
    generation.mkdir(parents=True)
    receipt = b"x" * (limit + 1) if which == "receipt" else b"{}"
    (generation / "receipt.json").write_bytes(receipt)
    pointer = {"generation": 1, "seq": 1, "receipt_sha256": cell.sha256(receipt)}
    pointer_bytes = b"x" * (limit + 1) if which == "pointer" else cell.canonical(pointer).encode()
    (cell_root / "current.json").write_bytes(pointer_bytes)
    log = {"schema": cell.LOG_SCHEMA, "prev_line_sha256": cell.ZERO, **pointer}
    log_bytes = b"x" * (limit + 1) if which == "log" else (cell.canonical(log) + "\n").encode()
    (cell_root / "LOG.jsonl").write_bytes(log_bytes)
    view = cell.load(cell_root, "learning")
    assert (view.status, view.reason) == ("QUARANTINED", reason)


@pytest.mark.parametrize("size", [0, 16, 64])
def test_bounded_control_read_accepts_up_to_exact_limit(tmp_path, size):
    path = tmp_path / "control.json"
    body = b"x" * size
    path.write_bytes(body)
    assert cell._read_control(path, 64, "control") == body


def test_bounded_control_never_requests_an_unbounded_read(monkeypatch, tmp_path):
    calls = []

    class FakeFile:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self, size=-1):
            calls.append(size)
            return b"x" * size

    monkeypatch.setattr(Path, "open", lambda *args, **kwargs: FakeFile())
    with pytest.raises(cell.CellError, match="control_over_budget"):
        cell._read_control(tmp_path / "control.json", 64, "control")
    assert calls == [65]


def test_unreadable_control_fails_closed(monkeypatch, tmp_path):
    def refused(*args, **kwargs):
        raise PermissionError("synthetic path must not escape")

    monkeypatch.setattr(Path, "open", refused)
    with pytest.raises(cell.CellError, match="control_unreadable") as caught:
        cell._read_control(tmp_path / "control.json", 64, "control")
    assert "synthetic path" not in str(caught.value)
