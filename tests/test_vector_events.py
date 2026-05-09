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


# ── read_events_from_offset (Phase D Candidate 2) ─────────────────
#
# Stage 2 vector indexing is event-sourced. Consumers that re-scan the
# full JSONL on every poll will trend toward O(total_log_size) per tick;
# offset-based reads keep the cost O(new_events). The tests below pin
# the contract so that switching consumers from full scan to checkpoint
# reads is a behavior-preserving change.


def test_read_events_from_offset_zero_offset_returns_all(tmp_path):
    """offset=0 reads every event (parity with read_events list-form)."""
    from waggledance.core.magma.vector_events import (
        emit_many, read_events, read_events_from_offset,
    )
    log = tmp_path / "events.jsonl"
    events = [
        solver_upserted("thermal", f"m{i}", f"s{i}", f"p{i}")
        for i in range(8)
    ]
    emit_many(events, log)

    full_scan = list(read_events(log))
    offset_scan, next_offset = read_events_from_offset(log, byte_offset=0)

    assert len(offset_scan) == len(full_scan) == 8
    assert [e.event_id() for e in offset_scan] == [e.event_id() for e in full_scan]
    # next_offset must equal file size after a clean scan
    assert next_offset == log.stat().st_size


def test_read_events_from_offset_incremental_returns_only_new(tmp_path):
    """The headline contract: after a full scan, append more events,
    second call from saved offset returns ONLY the appended ones."""
    from waggledance.core.magma.vector_events import (
        emit_many, read_events_from_offset,
    )
    log = tmp_path / "events.jsonl"
    first_batch = [
        solver_upserted("thermal", f"m{i}", f"s{i}", f"p{i}")
        for i in range(5)
    ]
    emit_many(first_batch, log)

    batch1, offset_after_first = read_events_from_offset(log, byte_offset=0)
    assert len(batch1) == 5

    # Append two more
    second_batch = [
        vector_upsert_requested("thermal", "m100", "s100", reason="r1"),
        vector_delete_requested("thermal", "m100", reason="r2"),
    ]
    emit_many(second_batch, log)

    batch2, offset_after_second = read_events_from_offset(
        log, byte_offset=offset_after_first
    )
    assert len(batch2) == 2
    assert batch2[0].payload["model_id"] == "m100"
    assert batch2[1].payload["model_id"] == "m100"
    assert offset_after_second == log.stat().st_size


def test_read_events_from_offset_at_eof_returns_empty(tmp_path):
    """offset == file_size → no events available, offset unchanged."""
    from waggledance.core.magma.vector_events import (
        emit, read_events_from_offset,
    )
    log = tmp_path / "events.jsonl"
    emit(solver_upserted("thermal", "a", "sig", "p"), log)
    eof = log.stat().st_size

    events, next_offset = read_events_from_offset(log, byte_offset=eof)
    assert events == []
    assert next_offset == eof


def test_read_events_from_offset_missing_file_returns_empty(tmp_path):
    from waggledance.core.magma.vector_events import read_events_from_offset
    missing = tmp_path / "nonexistent.jsonl"
    events, next_offset = read_events_from_offset(missing, byte_offset=0)
    assert events == []
    assert next_offset == 0


def test_read_events_from_offset_stale_offset_raises(tmp_path):
    """offset > file_size means the log was rotated / truncated; raise
    so callers don't silently re-process from the start."""
    from waggledance.core.magma.vector_events import (
        emit, read_events_from_offset,
    )
    log = tmp_path / "events.jsonl"
    emit(solver_upserted("thermal", "a", "sig", "p"), log)
    bogus_offset = log.stat().st_size + 1000

    with pytest.raises(ValueError, match="rotated or truncated"):
        read_events_from_offset(log, byte_offset=bogus_offset)


def test_read_events_from_offset_skips_partial_trailing_line(tmp_path):
    """If the writer is mid-flush, the trailing partial line MUST NOT
    be parsed and its bytes MUST NOT advance the offset. Once the line
    is completed, a second call returns the now-complete event."""
    from waggledance.core.magma.vector_events import (
        emit, read_events_from_offset,
    )
    log = tmp_path / "events.jsonl"
    emit(solver_upserted("thermal", "a", "sig", "p"), log)
    offset_after_complete = log.stat().st_size

    # Append a partial line (no trailing newline)
    with open(log, "ab") as f:
        f.write(b'{"event":"solver.upserted","cell_id":"thermal"')

    events, next_offset = read_events_from_offset(
        log, byte_offset=offset_after_complete
    )
    # Partial line ignored
    assert events == []
    assert next_offset == offset_after_complete

    # Complete the line + valid payload, then re-read
    with open(log, "ab") as f:
        f.write(
            b',"solver_id":"m999","payload":{"model_id":"m999",'
            b'"signature":"s999","source_path":"p999"}}\n'
        )
    events2, next_offset2 = read_events_from_offset(
        log, byte_offset=offset_after_complete
    )
    assert len(events2) == 1
    assert events2[0].payload["model_id"] == "m999"
    assert next_offset2 == log.stat().st_size


def test_read_events_from_offset_skips_malformed_lines_but_advances(tmp_path):
    """Liberal-skip parity with read_events: garbage bytes between two
    valid events are skipped, and their bytes still advance the offset
    so subsequent reads don't re-process them."""
    from waggledance.core.magma.vector_events import (
        emit, read_events_from_offset,
    )
    log = tmp_path / "events.jsonl"
    emit(solver_upserted("thermal", "a", "sig", "p"), log)
    with open(log, "a", encoding="utf-8") as f:
        f.write("{ this is not valid json\n")
        f.write(json.dumps({"event": "nope.unknown", "cell_id": "thermal"}) + "\n")
    emit(solver_upserted("thermal", "b", "sig", "p"), log)

    events, next_offset = read_events_from_offset(log, byte_offset=0)
    # Only the two valid known-event lines survive
    assert len(events) == 2
    assert events[0].payload["model_id"] == "a"
    assert events[1].payload["model_id"] == "b"
    # All bytes consumed (including the skipped malformed lines)
    assert next_offset == log.stat().st_size


def test_read_events_from_offset_scaling_is_O_new(tmp_path):
    """Regression guard for the headline scaling property: reading 100
    new events from a 5000-event log must NOT take the same time as a
    full 5000-event scan. We don't pin a specific time (microbench
    does that) — just assert the incremental scan is at least an
    order of magnitude faster than the full scan on the same log."""
    import time
    from waggledance.core.magma.vector_events import (
        emit_many, read_events_from_offset,
    )
    log = tmp_path / "events.jsonl"
    bulk = [
        solver_upserted("thermal", f"m{i}", f"s{i}", f"p{i}")
        for i in range(5000)
    ]
    emit_many(bulk, log)

    # Measure full scan
    t0 = time.perf_counter()
    _, full_offset = read_events_from_offset(log, byte_offset=0)
    full_ms = (time.perf_counter() - t0) * 1000

    # Append 100 more
    extras = [
        solver_upserted("thermal", f"x{i}", f"sx{i}", f"px{i}")
        for i in range(100)
    ]
    emit_many(extras, log)

    # Measure incremental scan
    t0 = time.perf_counter()
    new_events, _ = read_events_from_offset(log, byte_offset=full_offset)
    incremental_ms = (time.perf_counter() - t0) * 1000

    assert len(new_events) == 100
    # 100 events / 5000 events ≈ 2% of the work; allow generous 20%
    # headroom for warmup variance, but anything close to full_ms
    # would mean the offset path is doing a full scan — regression.
    assert incremental_ms < full_ms * 0.2, (
        f"incremental={incremental_ms:.2f}ms full={full_ms:.2f}ms — "
        f"offset reader is not skipping consumed bytes"
    )
