"""Tests for waggledance/core/magma/vector_events.py (Stage 1 MAGMA
event contract for the FAISS storage layer).
"""
from __future__ import annotations

import json

import pytest

from waggledance.core.magma.vector_events import (
    VectorEvent,
    EVT_SOLVER_UPSERTED,
    EVT_VECTOR_UPSERT_REQUESTED,
    EVT_VECTOR_DELETE_REQUESTED,
    EVT_VECTOR_COMMIT_APPLIED,
    ALL_VECTOR_EVENT_NAMES,
    VECTOR_EVENT_SCHEMA_VERSION,
    solver_upserted,
    vector_upsert_requested,
    vector_delete_requested,
    vector_commit_applied,
    validate_payload,
)


def test_all_four_event_names_exist():
    assert ALL_VECTOR_EVENT_NAMES == (
        "solver.upserted",
        "vector.upsert_requested",
        "vector.delete_requested",
        "vector.commit_applied",
    )


def test_schema_version_exported():
    assert VECTOR_EVENT_SCHEMA_VERSION >= 1


def test_unknown_event_rejected_at_construction():
    with pytest.raises(ValueError, match="unknown vector event"):
        VectorEvent(event="nope", cell_id="thermal")


def test_solver_upserted_requires_model_id_signature_path():
    with pytest.raises(ValueError, match="missing keys"):
        VectorEvent(
            event=EVT_SOLVER_UPSERTED,
            cell_id="thermal",
            payload={"model_id": "x"},  # missing signature + source_path
        )


def test_vector_upsert_requested_requires_model_id_signature():
    with pytest.raises(ValueError, match="missing keys"):
        VectorEvent(
            event=EVT_VECTOR_UPSERT_REQUESTED,
            cell_id="thermal",
            payload={"model_id": "x"},
        )


def test_vector_delete_requested_requires_model_id():
    with pytest.raises(ValueError, match="missing keys"):
        VectorEvent(
            event=EVT_VECTOR_DELETE_REQUESTED,
            cell_id="thermal",
            payload={},
        )


def test_vector_commit_applied_requires_commit_fields():
    with pytest.raises(ValueError, match="missing keys"):
        VectorEvent(
            event=EVT_VECTOR_COMMIT_APPLIED,
            cell_id="thermal",
            payload={"faiss_commit_id": "x"},
        )


def test_validate_payload_direct_call():
    validate_payload(EVT_VECTOR_DELETE_REQUESTED, {"model_id": "m"})
    with pytest.raises(ValueError):
        validate_payload(EVT_VECTOR_DELETE_REQUESTED, {})


def test_solver_upserted_helper_produces_valid_event():
    e = solver_upserted(
        cell_id="thermal",
        model_id="heat_loss",
        signature="abc123def4567890",
        source_path="configs/axioms/cottage/heat_loss.yaml",
    )
    assert e.event == EVT_SOLVER_UPSERTED
    assert e.cell_id == "thermal"
    assert e.solver_id == "heat_loss"
    assert "model_id" in e.payload


def test_vector_upsert_requested_helper_with_reason():
    e = vector_upsert_requested(
        cell_id="thermal", model_id="heat_loss",
        signature="abc123", reason="signature changed",
    )
    assert e.payload["reason"] == "signature changed"


def test_vector_delete_requested_helper_minimal():
    e = vector_delete_requested("thermal", "obsolete_solver")
    assert e.event == EVT_VECTOR_DELETE_REQUESTED
    assert "reason" not in e.payload


def test_vector_commit_applied_helper_with_source_events():
    e = vector_commit_applied(
        cell_id="thermal",
        faiss_commit_id="faiss_abc123",
        artifact_path="data/vector/thermal/index.faiss",
        vector_count=12,
        checksum="sha256:def456",
        source_events=["evt_a", "evt_b", "evt_c"],
    )
    assert e.payload["source_events"] == ["evt_a", "evt_b", "evt_c"]


def test_to_dict_round_trips_through_json():
    e = solver_upserted("thermal", "x", "sig", "path")
    d = e.to_dict()
    assert d["event"] == "solver.upserted"
    assert d["cell_id"] == "thermal"
    # Round-trip via JSON
    serialized = json.dumps(d)
    reparsed = json.loads(serialized)
    assert reparsed == d


def test_to_json_is_canonical_and_byte_stable():
    """Same event twice → same byte string (sorted keys, compact sep)."""
    e = vector_upsert_requested("thermal", "x", "sig")
    # Pin ts so the two events are truly identical
    e2 = VectorEvent(
        event=e.event, cell_id=e.cell_id,
        solver_id=e.solver_id, ts=e.ts,
        payload=dict(e.payload),
    )
    assert e.to_json() == e2.to_json()
    # Canonical: sorted keys. Verify "event" comes before "payload"
    # alphabetically, etc.
    blob = e.to_json()
    assert '"cell_id":"thermal"' in blob
    # No whitespace (compact separators)
    assert ", " not in blob
    assert ": " not in blob


def test_event_id_excludes_ts_for_idempotent_dedup():
    """Two events with same content but different ts must share id."""
    e1 = solver_upserted("thermal", "x", "sig", "path")
    # Construct a second event with an explicitly different ts (the
    # default timespec is seconds, so a sleep might not register).
    e2 = VectorEvent(
        event=e1.event, cell_id=e1.cell_id, solver_id=e1.solver_id,
        ts="2099-01-01T00:00:00+00:00",
        payload=dict(e1.payload),
    )
    assert e1.ts != e2.ts
    assert e1.event_id() == e2.event_id()


def test_event_id_moves_on_payload_change():
    e1 = solver_upserted("thermal", "x", "sig1", "path")
    e2 = solver_upserted("thermal", "x", "sig2", "path")
    assert e1.event_id() != e2.event_id()


def test_event_is_frozen():
    """Frozen dataclass — audit events cannot be mutated after construction."""
    e = solver_upserted("thermal", "x", "sig", "path")
    with pytest.raises(Exception):  # FrozenInstanceError or AttributeError
        e.cell_id = "energy"


# ── Event log writer / reader ─────────────────────────────────────

def test_emit_appends_canonical_jsonl(tmp_path):
    from waggledance.core.magma.vector_events import emit, read_events
    log = tmp_path / "events.jsonl"
    e1 = solver_upserted("thermal", "a", "sig1", "configs/axioms/a.yaml")
    e2 = vector_upsert_requested("thermal", "a", "sig1", reason="test")
    emit(e1, log)
    emit(e2, log)

    lines = log.read_text("utf-8").splitlines()
    assert len(lines) == 2
    # Both lines must be standalone valid JSON
    d1 = json.loads(lines[0])
    d2 = json.loads(lines[1])
    assert d1["event"] == "solver.upserted"
    assert d2["event"] == "vector.upsert_requested"


def test_emit_creates_parent_dir(tmp_path):
    from waggledance.core.magma.vector_events import emit
    deep = tmp_path / "not" / "yet" / "created" / "events.jsonl"
    assert not deep.parent.exists()
    emit(solver_upserted("thermal", "a", "sig", "p"), deep)
    assert deep.exists()


def test_emit_many_batches_into_one_file(tmp_path):
    from waggledance.core.magma.vector_events import emit_many, read_events
    log = tmp_path / "events.jsonl"
    events = [
        solver_upserted("thermal", f"m{i}", f"s{i}", f"p{i}")
        for i in range(5)
    ]
    emit_many(events, log)
    read_back = list(read_events(log))
    assert len(read_back) == 5
    assert [e.payload["model_id"] for e in read_back] == [f"m{i}" for i in range(5)]


def test_read_events_skips_malformed_lines(tmp_path):
    from waggledance.core.magma.vector_events import emit, read_events
    log = tmp_path / "events.jsonl"
    # One valid event, one garbage line, one unknown event-type line
    emit(solver_upserted("thermal", "a", "sig", "p"), log)
    with open(log, "a", encoding="utf-8") as f:
        f.write("{ this is not valid json\n")
        f.write(json.dumps({"event": "nope.unknown", "cell_id": "thermal"}) + "\n")

    events = list(read_events(log))
    # Only the valid known-event line survives
    assert len(events) == 1
    assert events[0].event == "solver.upserted"


def test_read_events_missing_file_yields_empty(tmp_path):
    from waggledance.core.magma.vector_events import read_events
    missing = tmp_path / "nonexistent.jsonl"
    events = list(read_events(missing))
    assert events == []


def test_emit_uses_env_var_when_no_path(tmp_path, monkeypatch):
    from waggledance.core.magma.vector_events import emit, read_events
    target = tmp_path / "from_env.jsonl"
    monkeypatch.setenv("WAGGLE_VECTOR_EVENT_LOG", str(target))
    emit(solver_upserted("thermal", "a", "sig", "p"))
    assert target.exists()
    assert len(list(read_events())) == 1


def test_round_trip_emit_read_preserves_event_id(tmp_path):
    """The consumer must be able to dedup by event_id across a
    restart. Emit, read, and confirm the same id comes back."""
    from waggledance.core.magma.vector_events import emit, read_events
    log = tmp_path / "events.jsonl"
    e = vector_commit_applied(
        cell_id="thermal",
        faiss_commit_id="faiss_abcd",
        artifact_path="data/vector/thermal/index.faiss",
        vector_count=20,
        checksum="sha256:deadbeef",
    )
    original_id = e.event_id()
    emit(e, log)
    [reparsed] = list(read_events(log))
    assert reparsed.event_id() == original_id
