"""Synthetic opt-in cutoff contract for the pure bridge reader port and core."""

import hashlib
import json

import pytest

from tools import bridge_v2_log_reader as port
from waggledance.core import bridge_log_reader as core


@pytest.fixture(params=[port, core], ids=["tools-port", "core"])
def reader(request):
    return request.param


def _source(tmp_path, reader):
    rows = [
        {"ready": True, "payload": {"flag": True}, "message": "synthetic ä"},
        {"ready": False, "payload": {"flag": True}},
        {"ready": True, "payload": {"flag": True}},
    ]
    lines = [json.dumps(row, ensure_ascii=False).encode("utf-8") + b"\r\n" for row in rows]
    path = tmp_path / "synthetic.jsonl"
    path.write_bytes(b"".join(lines))
    initial = reader.read_bridge_log(path, max_bytes=65536, max_rows=100)
    assert initial.status is reader.BridgeReadStatus.OK
    end = initial.candidate_cursor
    start = reader.BridgeCursor(offset=0, file_identity=end.file_identity, generation=end.generation)
    return path, rows, lines, start


def _read(reader, path, start, selector):
    return reader.read_bridge_log(
        path, cursor=start, max_bytes=65536, max_rows=100,
        expected_prefix_sha256=hashlib.sha256(b"").hexdigest(), max_prefix_bytes=65536,
        _row_ready=selector,
    )


def test_ready_prefix_stops_before_young_row_without_skipping_later_rows(tmp_path, reader):
    path, rows, lines, start = _source(tmp_path, reader)
    result = _read(reader, path, start, lambda row: row["ready"])
    assert result.status is reader.BridgeReadStatus.OK
    assert result.reason == "cutoff_deferred"
    assert result.rows == (rows[0],)
    assert result.candidate_cursor.offset == len(lines[0])
    assert result.candidate_prefix.sha256 == hashlib.sha256(lines[0]).hexdigest()

    later = reader.read_bridge_log(
        path, cursor=result.candidate_cursor, max_bytes=65536, max_rows=100,
        expected_prefix_sha256=result.candidate_prefix.sha256, max_prefix_bytes=65536,
        _row_ready=lambda row: True,
    )
    assert later.status is reader.BridgeReadStatus.OK
    assert later.rows == tuple(rows[1:])
    assert later.candidate_cursor.offset == sum(map(len, lines))
    assert later.candidate_prefix.sha256 == hashlib.sha256(b"".join(lines)).hexdigest()


@pytest.mark.parametrize("mutation", ["top", "nested", "bool_to_int"])
def test_selector_mutation_blocks_without_exposing_rows(tmp_path, reader, mutation):
    path, _, _, start = _source(tmp_path, reader)

    def mutate(row):
        if mutation == "top":
            row["ready"] = False
        elif mutation == "nested":
            row["payload"]["flag"] = False
        else:
            row["payload"]["flag"] = 1
        return True

    result = _read(reader, path, start, mutate)
    assert result.status is reader.BridgeReadStatus.BLOCKED
    assert result.candidate_cursor is None and result.rows == ()


def test_replaced_log_identity_is_retried_without_a_cursor(tmp_path, reader, monkeypatch):
    path, _, lines, start = _source(tmp_path, reader)
    replacement = tmp_path / "replacement.jsonl"
    replacement.write_bytes(b"".join(lines))
    original = reader._open_log
    calls = []

    def switched(candidate):
        calls.append(candidate)
        return original(replacement if len(calls) > 1 else candidate)

    monkeypatch.setattr(reader, "_open_log", switched)
    result = _read(reader, path, start, lambda row: row["ready"])
    assert len(calls) >= 2
    assert result.status is reader.BridgeReadStatus.RETRY
    assert result.reason == "file_identity_changed_during_read"
    assert result.candidate_cursor is None and result.rows == ()
