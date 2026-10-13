"""Bake-off harness: WD axial adjacency, frozen card rule, corrupt-cell detection and a byte-identical one-cell rebuild."""

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bakeoff as b  # noqa: E402


def test_the_failure_mesh_is_wd_axial_adjacency():
    assert set(b.axial_ring("input_validation")) == {name for name, _ in b.FAILURE_CLASSES[1:]}
    for name, _ in b.FAILURE_CLASSES[1:]:
        assert len(b.axial_ring(name)) == 3 and "input_validation" in b.axial_ring(name)
    assert b.axial_home("nothing to see", False) == ["input_validation"]
    assert b.axial_home("a partial write is not atomic", True)[0] == "filesystem_atomicity"


def test_card_rule_keeps_findings_and_blocking_statuses_only():
    events = [{"type": "finding", "status": "info", "message": "x"},
              {"type": "decision", "status": "changes_requested", "message": "y"},
              {"type": "message", "status": "info", "message": "z"},
              {"type": "claim", "status": "changes_requested", "message": "w"}]
    assert [b.is_card_event(e) for e in events] == [True, True, False, False]


def _store(tmp_path, name):
    records = [{"doc_id": f"lesson:l{i}@0", "kind": "lesson", "finding": i % 2 == 0,
                "text": f"note {i}: a partial write left a file; the cleanup failed and the retry narrowed {i}",
                "meta": {"kind": "lesson"}} for i in range(6)]
    emb = b.Embeddings("hash", [r["text"] for r in records])
    return b.Store(tmp_path / name, "axial7", False, records, emb), emb


def test_corrupt_cell_is_detected_quarantined_and_rebuilt_byte_identical(tmp_path):
    store, emb = _store(tmp_path, "s")
    cell = sorted(store.cells)[0]
    before = store.files()
    (store.root / f"cell_{cell}" / "index.faiss").write_bytes(b"corrupt")
    store.load()
    assert cell in store.quarantined
    result = b.run_variant("G", store, "the cleanup of a partial write failed", emb.query("cleanup"), None)
    assert cell not in result["cells_read"]
    assert result["degraded"] == ([cell] if store.findings[cell] else [])
    os.remove(store.root / f"cell_{cell}" / "index.faiss")
    store.write_cell(cell, fresh=True)
    store.load()
    assert not store.quarantined and store.files() == before


def test_two_builds_are_byte_identical(tmp_path):
    one, _ = _store(tmp_path, "a")
    two, _ = _store(tmp_path, "b")
    assert one.files() == two.files()
    assert json.dumps(one.receipt) == json.dumps(two.receipt)
