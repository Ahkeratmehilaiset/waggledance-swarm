"""Synthetic caller-pinned prefix checks, never a live bridge log."""
import hashlib
from dataclasses import replace

import pytest

from waggledance.core import bridge_log_reader as reader


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def anchored(tmp_path):
    log = tmp_path / "synthetic.jsonl"
    first = b'{"event":"first"}\n'
    log.write_bytes(first)
    cursor = reader.read_bridge_log(log).candidate_cursor
    assert cursor is not None
    return log, first, cursor


def read(log, cursor, pin, budget=4096):
    return reader.read_bridge_log(log, cursor=cursor, expected_prefix_sha256=pin,
                                  max_prefix_bytes=budget)


def test_append_returns_cursor_and_prefix_pin_together(anchored):
    log, first, cursor = anchored
    delta = b'{"event":"second"}\n'
    with log.open("ab") as stream:
        stream.write(delta)
    result = read(log, cursor, digest(first))
    assert result.status == reader.BridgeReadStatus.OK
    assert result.rows == ({"event": "second"},)
    assert result.candidate_prefix.cursor == result.candidate_cursor
    assert result.candidate_prefix.sha256 == digest(first + delta)


def test_rewrite_and_regrow_same_file_is_refused(anchored):
    log, first, cursor = anchored
    with log.open("r+b") as stream:
        stream.truncate(0)
        stream.write(b'{"event":"other"}\n' + b'{"event":"next"}\n')
    result = read(log, cursor, digest(first))
    assert result.status in (reader.BridgeReadStatus.BLOCKED, reader.BridgeReadStatus.RETRY)
    assert not result.rows and result.candidate_cursor is None


def test_incorrect_pin_and_budget_refuse(anchored):
    log, first, cursor = anchored
    for pin, budget in (("0" * 64, 4096), (digest(first), len(first) - 1)):
        result = read(log, cursor, pin, budget)
        assert result.status in (reader.BridgeReadStatus.BLOCKED, reader.BridgeReadStatus.RETRY)
        assert not result.rows and result.candidate_cursor is None


def test_concurrent_rewrite_after_initial_check_is_refused(anchored, monkeypatch):
    log, first, cursor = anchored
    with log.open("ab") as stream:
        stream.write(b'{"event":"next"}\n')
    original = reader._hash_prefix
    count = 0

    def mutate(stream, length):
        nonlocal count
        result = original(stream, length)
        count += 1
        if count == 1:
            with log.open("r+b") as writer:
                writer.write(b'{"event":"other"}\n')
        return result

    monkeypatch.setattr(reader, "_hash_prefix", mutate)
    result = read(log, cursor, digest(first))
    assert result.status in (reader.BridgeReadStatus.BLOCKED, reader.BridgeReadStatus.RETRY)
    assert not result.rows and result.candidate_cursor is None


def test_delta_rewrite_after_read_is_refused(anchored, monkeypatch):
    log, first, cursor = anchored
    with log.open("ab") as stream:
        stream.write(b'{"event":"next"}\n')
    original = reader._hash_prefix
    count = 0

    def mutate(stream, length):
        nonlocal count
        count += 1
        if count == 2:
            with log.open("r+b") as writer:
                writer.seek(cursor.offset)
                writer.write(b'{"event":"else"}\n')
        return original(stream, length)

    monkeypatch.setattr(reader, "_hash_prefix", mutate)
    result = read(log, cursor, digest(first))
    assert result.status in (reader.BridgeReadStatus.BLOCKED, reader.BridgeReadStatus.RETRY)
    assert not result.rows and result.candidate_cursor is None


def test_empty_prefix_and_candidate_budget(tmp_path):
    log = tmp_path / "empty.jsonl"
    log.write_bytes(b"")
    cursor = reader.read_bridge_log(log).candidate_cursor
    with log.open("ab") as stream:
        stream.write(b'{"a":1}\n')
    good = read(log, cursor, digest(b""), 8)
    assert good.candidate_prefix.sha256 == digest(b'{"a":1}\n')
    bad = read(log, cursor, digest(b""), 7)
    assert bad.status == reader.BridgeReadStatus.BLOCKED and bad.rows == ()
    assert bad.candidate_cursor is None


def test_missing_half_of_opt_in_and_unbounded_budget_refused(anchored):
    log, first, cursor = anchored
    for opts in ({"expected_prefix_sha256": digest(first)}, {"max_prefix_bytes": 100},
                 {"expected_prefix_sha256": digest(first), "max_prefix_bytes": reader.MAX_PREFIX_BYTES + 1}):
        out = reader.read_bridge_log(log, cursor=cursor, **opts)
        assert out.status == reader.BridgeReadStatus.BLOCKED and out.rows == ()


def test_wrong_file_identity_and_generation_refused(tmp_path):
    log = tmp_path / "synthetic.jsonl"
    log.write_bytes(b'{"a":1}\n')
    generation = tmp_path / "generation.json"
    generation.write_bytes(b'{"generation":"first"}')
    cursor = reader.read_bridge_log(log, generation_path=generation).candidate_cursor
    assert cursor is not None
    pin = digest(log.read_bytes())
    bad_id = replace(cursor, file_identity=cursor.file_identity + "-incorrect")
    wrong_id = reader.read_bridge_log(log, cursor=bad_id, generation_path=generation,
                                      expected_prefix_sha256=pin, max_prefix_bytes=100)
    assert wrong_id.status == reader.BridgeReadStatus.RETRY and not wrong_id.rows
    assert wrong_id.candidate_cursor is None
    generation.write_bytes(b'{"generation":"second"}')
    wrong_generation = reader.read_bridge_log(log, cursor=cursor, generation_path=generation,
                                              expected_prefix_sha256=pin, max_prefix_bytes=100)
    assert wrong_generation.status == reader.BridgeReadStatus.RETRY and not wrong_generation.rows
    assert wrong_generation.candidate_cursor is None


def test_generation_change_during_final_prefix_hash_refused(tmp_path, monkeypatch):
    log = tmp_path / "synthetic.jsonl"
    raw = b'{"a":1}\n'
    log.write_bytes(raw)
    generation = tmp_path / "generation.json"
    generation.write_bytes(b'{"generation":"first"}')
    cursor = reader.read_bridge_log(log, generation_path=generation).candidate_cursor
    original = reader._hash_prefix
    count = 0

    def mutate(stream, length):
        nonlocal count
        result = original(stream, length)
        count += 1
        if count == 2:
            generation.write_bytes(b'{"generation":"second"}')
        return result

    monkeypatch.setattr(reader, "_hash_prefix", mutate)
    result = reader.read_bridge_log(log, cursor=cursor, generation_path=generation,
                                    expected_prefix_sha256=digest(raw), max_prefix_bytes=100)
    assert result.status == reader.BridgeReadStatus.RETRY and not result.rows
    assert result.candidate_cursor is None


def test_no_opt_in_keeps_original_result_type(anchored):
    log, _, cursor = anchored
    result = reader.read_bridge_log(log, cursor=cursor)
    assert type(result) is reader.BridgeReadResult
    assert not hasattr(result, "candidate_prefix")
