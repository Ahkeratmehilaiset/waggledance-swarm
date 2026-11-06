"""Synthetic multi-row cutoff and prefix-I/O regression, never reads the real bridge."""
from datetime import datetime
import hashlib
import json

import pytest

from sandbox.mind.step2 import shared_bridge_snapshot as snapshot
from sandbox.mind.step2.test_status_public_filter import event, read_fixture
from waggledance.core import bridge_log_reader
from waggledance.core.bridge_log_reader import BridgeCursor, BridgePrefixPin, BridgeReadStatus


def _fixture(tmp_path, rows):
    _, pins, path = read_fixture(tmp_path, [])
    raw = b"".join(json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
                   + b"\r\n" for row in rows)
    path.write_bytes(raw)
    anchor = json.loads(pins["anchor_bytes"])
    cursor = BridgeCursor(**anchor["cursor_start"])
    start = BridgePrefixPin(cursor, hashlib.sha256(b"").hexdigest())
    return pins, path, anchor, start, raw


def _batch_read(path, pins, anchor, start, cutoff, **kwargs):
    return snapshot.read_public_catchup(
        path, start.cursor, frozenset({"public-task", "nonreview-private"}), enabled=True,
        generation_path=anchor["generation_path"], max_bytes=65536, max_rows=kwargs.get("max_rows", 100),
        max_windows=kwargs.get("max_windows", 64), expected_prefix_sha256=start.sha256, max_prefix_bytes=65536,
        cutoff_utc=cutoff, batch_rows=True, **pins)


def _verify(result, pins, start, cutoff, **kwargs):
    return snapshot.verify_public_catchup(
        result, expected_anchor_bytes=pins["anchor_bytes"], expected_anchor_sha256=pins["anchor_sha256"],
        expected_policy_sha256=pins["policy_sha256"], expected_roster_sha256=pins["roster_sha256"],
        expected_public_policy_sha256=pins["public_policy_sha256"], expected_start_prefix=start,
        expected_cutoff_utc=cutoff, expected_batch_rows=kwargs.get("expected_batch_rows", True))


def test_cutoff_batches_rows_without_skipping_or_quadratic_prefix_reads(tmp_path, monkeypatch):
    # Empty synthetic source first: the fixture's ASCII serializer intentionally refuses escaped
    # surrogate pairs, while the test below puts real multi-byte UTF-8 directly into private rows.
    _, pins, path = read_fixture(tmp_path, [])
    ready = [event(ts_utc=f"2026-10-31T11:{minute:02d}:00Z") for minute in range(20, 40)]
    private = [event(ts_utc="2026-10-31T11:45:00Z", to="fable-5", message="private-message-marker ä🐝"),
               event(ts_utc="2026-10-31T11:46:00Z", task_id="nonreview-private", message="excluded-message-marker ö")]
    younger = event(ts_utc="2026-10-31T12:01:10Z")
    later_ready = event(ts_utc="2026-10-31T11:10:00Z")
    lines = [json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8") + b"\r\n"
             for row in ready + private + [younger, later_ready]]
    source = b"".join(lines)
    path.write_bytes(source)
    expected_prefix = b"".join(lines[:len(ready) + len(private)])
    anchor = json.loads(pins["anchor_bytes"])
    cursor = BridgeCursor(**anchor["cursor_start"])
    start = BridgePrefixPin(cursor, hashlib.sha256(b"").hexdigest())
    cutoff = datetime.fromisoformat("2026-10-31T12:01:30+00:00")
    reads = []
    original_hash_prefix = bridge_log_reader._hash_prefix

    class CountReads:
        def __init__(self, stream):
            self._stream = stream

        def read(self, size=-1):
            chunk = self._stream.read(size)
            reads.append(len(chunk))
            return chunk

        def __getattr__(self, name):
            return getattr(self._stream, name)

    def counted_prefix(stream, length):
        return original_hash_prefix(CountReads(stream), length)

    monkeypatch.setattr(bridge_log_reader, "_hash_prefix", counted_prefix)
    result = snapshot.read_public_catchup(
        path, cursor, frozenset({"public-task", "nonreview-private"}), enabled=True,
        generation_path=anchor["generation_path"], max_bytes=65536, max_rows=100, max_windows=64,
        expected_prefix_sha256=start.sha256, max_prefix_bytes=65536, cutoff_utc=cutoff, batch_rows=True, **pins)
    assert snapshot.verify_public_catchup(
        result, expected_anchor_bytes=pins["anchor_bytes"], expected_anchor_sha256=pins["anchor_sha256"],
        expected_policy_sha256=pins["policy_sha256"], expected_roster_sha256=pins["roster_sha256"],
        expected_public_policy_sha256=pins["public_policy_sha256"], expected_start_prefix=start,
        expected_cutoff_utc=cutoff, expected_batch_rows=True)
    records = [record for window in result["windows"] for record in window["projection"]["records"]]
    assert len(records) == len(ready)
    assert result["end_reason"] == "deferred" and result["caught_up"] is False
    assert result["candidate_cursor"].offset == len(expected_prefix)
    assert result["candidate_prefix"].sha256 == hashlib.sha256(expected_prefix).hexdigest()
    assert "private-message-marker" not in repr(result) and "excluded-message-marker" not in repr(result)
    # Bound the measured prefix-hash I/O, not the legacy bytes_read field. A row-at-a-time
    # rescan grows quadratically as a batch grows. Allow four whole-source passes so the
    # optimization need not use a specific strategy or hash-call count.
    measured = sum(reads)
    print(f"synthetic_prefix_hash_bytes={measured}, source_bytes={len(source)}")
    assert measured <= 4 * len(source), f"prefix hash read {measured} bytes for {len(source)} source bytes"


def test_batch_young_first_has_no_windows_or_cursor_advance(tmp_path):
    pins, path, anchor, start, _ = _fixture(tmp_path, [event(ts_utc="2026-10-31T12:00:59Z"),
        event(ts_utc="2026-10-31T11:58:00Z")])
    cutoff = datetime.fromisoformat("2026-10-31T12:01:00+00:00")
    result = _batch_read(path, pins, anchor, start, cutoff)
    assert result["end_reason"] == "deferred" and result["windows"] == ()
    assert result["candidate_prefix"] == start and result["candidate_cursor"] == start.cursor
    assert _verify(result, pins, start, cutoff)
    with pytest.raises(snapshot.SnapshotRefused):
        _verify(result, pins, start, cutoff, expected_batch_rows=False)


def test_batch_max_rows_and_partial_tail_preserve_real_byte_boundary(tmp_path):
    pins, path, anchor, start, raw = _fixture(tmp_path, [event(ts_utc="2026-10-31T11:58:00Z"),
        event(ts_utc="2026-10-31T11:57:00Z"), event(ts_utc="2026-10-31T11:56:00Z")])
    with path.open("ab") as stream:
        stream.write(b'{"partial":')
    cutoff = datetime.fromisoformat("2026-10-31T12:01:01+00:00")
    result = _batch_read(path, pins, anchor, start, cutoff, max_rows=2, max_windows=1)
    end = raw.index(b"\n", raw.index(b"\n") + 1) + 1
    assert result["end_reason"] == "max_windows" and result["candidate_cursor"].offset == end
    assert result["candidate_prefix"].sha256 == hashlib.sha256(raw[:end]).hexdigest()
    assert _verify(result, pins, start, cutoff)
    complete = _batch_read(path, pins, anchor, start, cutoff, max_rows=10)
    assert complete["end_reason"] == "partial_record" and complete["candidate_cursor"].offset == len(raw)
    assert _verify(complete, pins, start, cutoff)


def test_batch_prefix_mutation_during_read_is_refused(tmp_path, monkeypatch):
    pins, path, anchor, start, raw = _fixture(tmp_path, [event(ts_utc="2026-10-31T11:58:00Z"),
        event(ts_utc="2026-10-31T12:00:59Z")])
    original = bridge_log_reader._hash_prefix
    calls = []

    def mutate_before_final_hash(stream, length):
        calls.append(length)
        if len(calls) == 2:
            path.write_bytes(b"X" + raw[1:])
        return original(stream, length)

    monkeypatch.setattr(bridge_log_reader, "_hash_prefix", mutate_before_final_hash)
    with pytest.raises(snapshot.SnapshotRefused):
        _batch_read(path, pins, anchor, start, datetime.fromisoformat("2026-10-31T12:00:59+00:00"))
    assert len(calls) >= 2


def test_batch_generation_change_during_read_is_refused(tmp_path, monkeypatch):
    pins, path, anchor, start, _ = _fixture(tmp_path, [event(ts_utc="2026-10-31T11:58:00Z"),
        event(ts_utc="2026-10-31T12:00:59Z")])
    original = bridge_log_reader._hash_prefix
    calls = []

    def change_generation_before_final_hash(stream, length):
        calls.append(length)
        if len(calls) == 2:
            from pathlib import Path
            Path(anchor["generation_path"]).write_text('{"generation":"changed"}', encoding="utf-8")
        return original(stream, length)

    monkeypatch.setattr(bridge_log_reader, "_hash_prefix", change_generation_before_final_hash)
    with pytest.raises(snapshot.SnapshotRefused):
        _batch_read(path, pins, anchor, start, datetime.fromisoformat("2026-10-31T12:00:59+00:00"))
    assert len(calls) >= 2


def test_batch_source_path_replaced_during_read_is_refused(tmp_path, monkeypatch):
    pins, path, anchor, start, raw = _fixture(tmp_path, [event(ts_utc="2026-10-31T11:58:00Z"),
        event(ts_utc="2026-10-31T12:00:59Z")])
    replacement = tmp_path / "synthetic-replacement.jsonl"
    replacement.write_bytes(raw)
    original = bridge_log_reader._open_log
    calls = []

    def changed_path_at_final_open(candidate):
        calls.append(candidate)
        return original(replacement if len(calls) > 1 else candidate)

    monkeypatch.setattr(bridge_log_reader, "_open_log", changed_path_at_final_open)
    with pytest.raises(snapshot.SnapshotRefused):
        _batch_read(path, pins, anchor, start, datetime.fromisoformat("2026-10-31T12:00:59+00:00"))
    assert len(calls) >= 2


def test_batch_real_path_rotation_during_read_is_refused(tmp_path, monkeypatch):
    pins, path, anchor, start, raw = _fixture(tmp_path, [event(ts_utc="2026-10-31T11:58:00Z"),
        event(ts_utc="2026-10-31T12:00:59Z")])
    replacement = tmp_path / "new-synthetic.jsonl"
    replacement.write_bytes(raw)
    original = bridge_log_reader._hash_prefix
    calls = []

    def rotate_before_final_hash(stream, length):
        calls.append(length)
        if len(calls) == 2:
            path.replace(tmp_path / "rotated-synthetic.jsonl")
            replacement.replace(path)
        return original(stream, length)

    monkeypatch.setattr(bridge_log_reader, "_hash_prefix", rotate_before_final_hash)
    with pytest.raises(snapshot.SnapshotRefused):
        _batch_read(path, pins, anchor, start, datetime.fromisoformat("2026-10-31T12:00:59+00:00"))
    assert len(calls) >= 2 and path.exists()


@pytest.mark.parametrize("mutation", ["top_level", "nested", "bool_to_int"])
def test_row_ready_callback_cannot_mutate_rows_bound_to_source_bytes(tmp_path, mutation):
    _, path, anchor, start, _ = _fixture(tmp_path, [event(ts_utc="2026-10-31T11:58:00Z")])

    def modify_and_allow(row):
        if mutation == "nested":
            row["payload"]["public_status"] = False
        elif mutation == "bool_to_int":
            row["payload"]["public_status"] = 1  # True == 1 in Python, but JSON types differ
        else:
            row["task_id"] = "synthetic-mutated"
        return True

    result = bridge_log_reader.read_bridge_log(
        path, cursor=start.cursor, generation_path=anchor["generation_path"], max_bytes=65536, max_rows=100,
        expected_prefix_sha256=start.sha256, max_prefix_bytes=65536, _row_ready=modify_and_allow)
    assert result.status is BridgeReadStatus.BLOCKED and result.candidate_cursor is None
    assert result.rows == ()
