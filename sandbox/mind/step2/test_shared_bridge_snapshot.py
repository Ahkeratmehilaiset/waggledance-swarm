"""Synthetic JSONL only; no live bridge, producer, model, or network."""
import json
from dataclasses import replace

import pytest

from sandbox.mind.step2 import shared_bridge_snapshot as snapshot
from waggledance.core.bridge_log_reader import BridgeCursor, read_bridge_log

TASKS = frozenset({"synthetic-task"})


def event(**changes):
    result = {"ts_utc": "2026-10-31T12:00:00Z", "agent": "codex-lead-1", "type": "message",
              "status": "done", "task_id": "synthetic-task", "message": "synthetic-private-sentinel",
              "payload": {"secret": "synthetic-private-sentinel"}}
    result.update(changes)
    return result


def line(value):
    return json.dumps(value, separators=(",", ":")).encode("utf-8") + b"\n"


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "synthetic-events.jsonl"
    path.write_bytes(b"")
    # The fixture plays the external cursor authority; production must supply its own pin.
    cursor = read_bridge_log(path).candidate_cursor
    assert cursor is not None
    return path, cursor


def test_delta_projects_only_allowed_metadata_and_does_not_write(source):
    path, cursor = source
    raw = line(event()) + line(event(agent="claude-rco-1")) + line(event(to="claude-rco-2"))
    path.write_bytes(raw)
    out = snapshot.read_shared_delta(path, cursor, TASKS, max_bytes=4096, max_rows=10)
    assert out["projection"]["accepted"] == 1
    assert out["source"]["generation_status"] == "UNKNOWN"
    assert out["source"]["coverage"] == "delta_only_not_complete_history"
    assert out["candidate_cursor"].offset == len(raw)
    assert "synthetic-private-sentinel" not in repr(out)
    assert path.read_bytes() == raw and sorted(p.name for p in path.parent.iterdir()) == [path.name]


@pytest.mark.parametrize("fields", [{"To": "claude-rco-1"}, {"payload": {"Recipient": "claude-rco-2"}}])
def test_mixed_case_rco_recipient_is_excluded_in_real_reader_snapshot(source, fields):
    path, cursor = source
    path.write_bytes(line(event(**fields)))
    out = snapshot.read_shared_delta(path, cursor, TASKS, max_bytes=4096, max_rows=10)
    assert out["projection"]["accepted"] == 0


@pytest.mark.parametrize("fields", [{"to": "fable-5", "To": "claude-rco-1"},
                                    {"payload": {"recipient": "fable-5", "Recipient": "claude-rco-2"}}])
def test_case_colliding_recipient_keys_refuse_at_reader_snapshot(source, fields):
    path, cursor = source
    path.write_bytes(line(event(**fields)))
    with pytest.raises(snapshot.SnapshotRefused, match="^snapshot_refused$"):
        snapshot.read_shared_delta(path, cursor, TASKS, max_bytes=4096, max_rows=10)


def test_empty_delta_is_idle_with_same_candidate(source):
    path, cursor = source
    out = snapshot.read_shared_delta(path, cursor, TASKS, max_bytes=1024, max_rows=10)
    assert out["projection"]["accepted"] == 0
    assert out["candidate_cursor"] == cursor


@pytest.mark.parametrize("raw,max_bytes,max_rows", [
    (line(event()) + b'{"incomplete":', 4096, 10),
    (b'{"unterminated":', 4096, 10),
    (line(event()), 8, 10),
    (line(event()) + line(event(status="info")), 4096, 1),
])
def test_partial_trailing_oversized_or_paginated_refuse(source, raw, max_bytes, max_rows):
    path, cursor = source
    path.write_bytes(raw)
    with pytest.raises(snapshot.SnapshotRefused, match="^snapshot_refused$"):
        snapshot.read_shared_delta(path, cursor, TASKS, max_bytes=max_bytes, max_rows=max_rows)


def test_wrong_cursor_identity_refused_and_never_seeded(source):
    path, cursor = source
    path.write_bytes(line(event()))
    bad = BridgeCursor(cursor.offset, "windows-v1:0000abcd:0000000000000001", cursor.generation)
    if bad.file_identity == cursor.file_identity:
        bad = replace(bad, file_identity="windows-v1:0000abcd:0000000000000002")
    with pytest.raises(snapshot.SnapshotRefused, match="^snapshot_refused$"):
        snapshot.read_shared_delta(path, bad, TASKS, max_bytes=4096, max_rows=10)
    with pytest.raises(snapshot.SnapshotRefused, match="^snapshot_refused$"):
        snapshot.read_shared_delta(path, None, TASKS, max_bytes=4096, max_rows=10)


def test_inconsistent_reader_byte_accounting_refuses(source, monkeypatch):
    path, cursor = source
    path.write_bytes(line(event()))
    actual = read_bridge_log(path, cursor=cursor, max_bytes=4096, max_rows=10)
    monkeypatch.setattr(snapshot, "read_bridge_log", lambda *a, **kw: replace(actual, bytes_consumed=0))
    with pytest.raises(snapshot.SnapshotRefused, match="^snapshot_refused$"):
        snapshot.read_shared_delta(path, cursor, TASKS, max_bytes=4096, max_rows=10)


def test_truncation_after_a_nonzero_cursor_refuses(source):
    path, cursor = source
    path.write_bytes(line(event()))
    advanced = read_bridge_log(path, cursor=cursor, max_bytes=4096, max_rows=10).candidate_cursor
    assert advanced is not None and advanced.offset > 0
    path.write_bytes(b"")
    with pytest.raises(snapshot.SnapshotRefused, match="^snapshot_refused$"):
        snapshot.read_shared_delta(path, advanced, TASKS, max_bytes=4096, max_rows=10)


def test_generation_pin_checked_and_change_refuses(source):
    path, _ = source
    generation = path.parent / "synthetic-generation.json"
    generation.write_text('{"generation":"synthetic-generation"}', encoding="utf-8")
    cursor = read_bridge_log(path, generation_path=generation).candidate_cursor
    assert cursor is not None and cursor.generation == "synthetic-generation"
    path.write_bytes(line(event()))
    out = snapshot.read_shared_delta(path, cursor, TASKS, max_bytes=4096, max_rows=10,
                                     generation_path=generation)
    assert out["source"]["generation_status"] == "caller_asserted"
    generation.write_text('{"generation":"other-generation"}', encoding="utf-8")
    with pytest.raises(snapshot.SnapshotRefused, match="^snapshot_refused$"):
        snapshot.read_shared_delta(path, cursor, TASKS, max_bytes=4096, max_rows=10,
                                   generation_path=generation)


@pytest.mark.parametrize("max_bytes,max_rows", [(0, 1), (True, 1), (1, 0), (1, True), (snapshot.MAX_BYTES + 1, 1)])
def test_invalid_resource_limits_refuse_without_a_read(source, monkeypatch, max_bytes, max_rows):
    path, cursor = source
    calls = []
    monkeypatch.setattr(snapshot, "read_bridge_log", lambda *a, **kw: calls.append(True))
    with pytest.raises(snapshot.SnapshotRefused, match="^snapshot_refused$"):
        snapshot.read_shared_delta(path, cursor, TASKS, max_bytes=max_bytes, max_rows=max_rows)
    assert calls == []
