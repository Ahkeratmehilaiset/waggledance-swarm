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
