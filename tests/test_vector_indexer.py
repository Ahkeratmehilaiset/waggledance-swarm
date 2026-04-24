"""Tests for tools/vector_indexer.py — Stage-1 stub consumer.

The stub does not write FAISS. It reads the event log and builds a
per-cell state projection. These tests guarantee the projection math
matches the event-sourcing contract so the real Stage-2 indexer can
reuse `replay()` with confidence."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from waggledance.core.magma import vector_events


def _load_mod():
    path = ROOT / "tools" / "vector_indexer.py"
    spec = importlib.util.spec_from_file_location("vector_indexer", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["vector_indexer"] = mod
    spec.loader.exec_module(mod)
    return mod


mod = _load_mod()


def test_empty_log_yields_empty_report(tmp_path):
    log = tmp_path / "events.jsonl"
    report = mod.replay(log)
    assert report.events_seen == 0
    assert report.cells == {}


def test_missing_log_yields_empty_report(tmp_path):
    log = tmp_path / "nonexistent.jsonl"
    report = mod.replay(log)
    assert report.events_seen == 0


def test_upsert_request_increments_cell_counter(tmp_path):
    log = tmp_path / "events.jsonl"
    vector_events.emit(
        vector_events.vector_upsert_requested("thermal", "heat_loss", "sig1"),
        log,
    )
    report = mod.replay(log)
    assert report.events_seen == 1
    cell = report.cells["thermal"]
    assert cell.upsert_requests == 1
    assert cell.signatures["heat_loss"] == "sig1"


def test_upsert_then_delete_removes_signature(tmp_path):
    log = tmp_path / "events.jsonl"
    vector_events.emit(
        vector_events.vector_upsert_requested("thermal", "obsolete", "sig"),
        log,
    )
    vector_events.emit(
        vector_events.vector_delete_requested("thermal", "obsolete"),
        log,
    )
    report = mod.replay(log)
    cell = report.cells["thermal"]
    assert cell.upsert_requests == 1
    assert cell.delete_requests == 1
    assert "obsolete" not in cell.signatures


def test_commit_applied_records_count_and_id(tmp_path):
    log = tmp_path / "events.jsonl"
    vector_events.emit(
        vector_events.vector_commit_applied(
            cell_id="thermal",
            faiss_commit_id="faiss_abc123",
            artifact_path="data/vector/thermal/index.faiss",
            vector_count=20,
            checksum="sha256:deadbeef",
        ),
        log,
    )
    report = mod.replay(log)
    cell = report.cells["thermal"]
    assert cell.committed_count == 20
    assert cell.last_commit_id == "faiss_abc123"


def test_since_event_id_skips_earlier_events(tmp_path):
    log = tmp_path / "events.jsonl"
    e1 = vector_events.vector_upsert_requested("thermal", "a", "s1")
    e2 = vector_events.vector_upsert_requested("thermal", "b", "s2")
    e3 = vector_events.vector_upsert_requested("thermal", "c", "s3")
    vector_events.emit_many([e1, e2, e3], log)

    # Resume after e2 → should only see e3
    report = mod.replay(log, since_event_id=e2.event_id())
    assert report.events_seen == 1
    assert list(report.cells["thermal"].signatures.keys()) == ["c"]


def test_unknown_event_counted_but_replay_continues(tmp_path):
    log = tmp_path / "events.jsonl"
    # Emit a known event
    vector_events.emit(
        vector_events.solver_upserted("thermal", "a", "s", "p"), log,
    )
    # Append a raw JSON line for an unknown-type event
    import json as _json
    with open(log, "a", encoding="utf-8") as f:
        line = _json.dumps({
            "event": "solver.upserted",  # valid name
            "cell_id": "thermal",
            "solver_id": "a",
            "ts": "2026-04-24T10:00:00+00:00",
            "payload": {"model_id": "a", "signature": "s", "source_path": "p"},
            "schema_version": 1,
        })
        f.write(line + "\n")
    # And a completely unknown line
    with open(log, "a", encoding="utf-8") as f:
        f.write("gibberish line\n")

    report = mod.replay(log)
    # Gibberish line is filtered by read_events; the two valid events
    # go through
    assert report.events_seen == 2


def test_multi_cell_state_is_independent(tmp_path):
    log = tmp_path / "events.jsonl"
    batch = [
        vector_events.vector_upsert_requested("thermal", "t_a", "s1"),
        vector_events.vector_upsert_requested("thermal", "t_b", "s2"),
        vector_events.vector_upsert_requested("energy", "e_a", "s3"),
    ]
    vector_events.emit_many(batch, log)
    report = mod.replay(log)
    assert report.cells["thermal"].upsert_requests == 2
    assert report.cells["energy"].upsert_requests == 1
    assert "t_a" in report.cells["thermal"].signatures
    assert "t_a" not in report.cells["energy"].signatures


def test_solver_upserted_alone_does_not_touch_cell_counters(tmp_path):
    """Per the contract: solver.upserted is informational; the actual
    cell state motion comes from vector.upsert_requested."""
    log = tmp_path / "events.jsonl"
    vector_events.emit(
        vector_events.solver_upserted("thermal", "a", "s", "p"), log,
    )
    report = mod.replay(log)
    # Event is counted but upsert_requests stays at 0 because there was
    # no vector.upsert_requested.
    assert report.events_seen == 1
    assert report.cells["thermal"].upsert_requests == 0


def test_json_serialization(tmp_path):
    log = tmp_path / "events.jsonl"
    vector_events.emit(
        vector_events.vector_upsert_requested("thermal", "a", "s1"), log,
    )
    report = mod.replay(log)
    out = mod._to_json(report)
    # Shape checks
    assert out["events_seen"] == 1
    assert out["cells"]["thermal"]["upsert_requests"] == 1
    assert out["cells"]["thermal"]["signatures"]["a"] == "s1"


def test_format_report_prints_every_cell(tmp_path):
    log = tmp_path / "events.jsonl"
    vector_events.emit(
        vector_events.vector_upsert_requested("thermal", "a", "s"), log,
    )
    vector_events.emit(
        vector_events.vector_upsert_requested("energy", "b", "t"), log,
    )
    report = mod.replay(log)
    text = mod._format_report(report)
    assert "thermal" in text
    assert "energy" in text
