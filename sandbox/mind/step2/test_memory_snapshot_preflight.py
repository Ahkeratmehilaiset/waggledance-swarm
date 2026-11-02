"""Synthetic multi-cell integrity preflight; FAISS is faked by the test runner."""
import json

import pytest

from sandbox.mind.step2 import cell, memory
from sandbox.mind.step2.test_memory_snapshot import _case, _enc, _load


def _two_cells(monkeypatch):
    manifest, snapshots = _case(monkeypatch)
    general = dict(snapshots["learning"])
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
    snapshots["general"] = general
    manifest["cells"]["general"] = {"generation": 1, "receipt_sha256": digest}
    return manifest, snapshots


@pytest.mark.parametrize("name", ["index", "meta", "provenance", "vectors"])
def test_second_cell_hash_mismatch_refused_before_first_cell_load(monkeypatch, name):
    manifest, snapshots = _two_cells(monkeypatch)
    snapshots["learning"]["artifacts"][name] += b"synthetic-tamper"
    monkeypatch.setattr(cell, "load_snapshot", lambda *a, **k: pytest.fail("load reached before all hashes"))
    with pytest.raises(memory.MemoryError_, match="snapshot_refused"):
        _load(manifest, snapshots)


def test_valid_two_cell_snapshot_loads_after_preflight(monkeypatch):
    manifest, snapshots = _two_cells(monkeypatch)
    original = cell.load_snapshot
    calls = []
    def observed(cell_id, **kwargs):
        calls.append(cell_id)
        return original(cell_id, **kwargs)
    monkeypatch.setattr(cell, "load_snapshot", observed)
    loaded = _load(manifest, snapshots)
    assert calls == ["general", "learning"]
    assert all(view.healthy for view in loaded.views.values())


def test_missing_optional_vectors_remains_supported(monkeypatch):
    manifest, snapshots = _two_cells(monkeypatch)
    del snapshots["learning"]["artifacts"]["vectors"]
    loaded = _load(manifest, snapshots)
    assert loaded.views["learning"].healthy
    assert not loaded.views["learning"].rebuild_possible


def test_second_cell_index_budget_refused_before_first_load(monkeypatch):
    manifest, snapshots = _two_cells(monkeypatch)
    item = snapshots["learning"]
    data = item["artifacts"]
    data["index"] += b"oversize"
    receipt = json.loads(data["receipt"])
    receipt["content"]["files"]["index"] = cell.sha256(data["index"])
    receipt["content_sha256"] = cell.sha256(cell.canonical(receipt["content"]).encode())
    data["receipt"] = _enc(receipt)
    digest = cell.sha256(data["receipt"])
    manifest["cells"]["learning"]["receipt_sha256"] = digest
    pointer, log = json.loads(item["pointer"]), json.loads(item["log"])
    pointer["receipt_sha256"] = log["receipt_sha256"] = digest
    item["pointer"], item["log"] = _enc(pointer), _enc(log) + b"\n"
    monkeypatch.setattr(cell, "MAX_Q4_INDEX_BYTES", len(snapshots["general"]["artifacts"]["index"]))
    monkeypatch.setattr(cell, "load_snapshot", lambda *a, **k: pytest.fail("load reached before budget check"))
    with pytest.raises(memory.MemoryError_, match="snapshot_refused"):
        _load(manifest, snapshots)


@pytest.mark.parametrize("name", ["index", "meta", "provenance"])
def test_missing_required_second_cell_artifact_refused_before_load(monkeypatch, name):
    manifest, snapshots = _two_cells(monkeypatch)
    del snapshots["learning"]["artifacts"][name]
    monkeypatch.setattr(cell, "load_snapshot", lambda *a, **k: pytest.fail("load reached before presence check"))
    with pytest.raises(memory.MemoryError_, match="snapshot_refused"):
        _load(manifest, snapshots)
