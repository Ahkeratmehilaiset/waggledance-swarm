"""Synthetic exact-snapshot digest binding, including the tools parity port."""
import hashlib
import importlib
from dataclasses import replace

import pytest


@pytest.fixture(params=["waggledance.core.bridge_log_reader", "tools.bridge_v2_log_reader"])
def reader(request):
    return importlib.import_module(request.param)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def test_exact_snapshot_validates_and_parses(reader, tmp_path):
    raw = b'{"event":"synthetic"}\n'
    path = tmp_path / "snapshot.jsonl"
    path.write_bytes(raw)
    result = reader.read_bridge_log(path, expected_snapshot_sha256=sha(raw))
    assert result.status == reader.BridgeReadStatus.OK
    assert result.rows == ({"event": "synthetic"},)
    assert result.candidate_cursor.offset == len(raw)


def test_hash_mismatch_refuses_before_parser(reader, tmp_path, monkeypatch):
    path = tmp_path / "snapshot.jsonl"
    path.write_bytes(b'{"event":"second"}\n')
    monkeypatch.setattr(reader, "_parse_rows", lambda *args, **kwargs: pytest.fail("unverified bytes parsed"))
    result = reader.read_bridge_log(path, expected_snapshot_sha256=sha(b'{"event":"first"}\n'))
    assert result.status == reader.BridgeReadStatus.BLOCKED
    assert not result.rows and result.candidate_cursor is None


@pytest.mark.parametrize("kind", ["truncated_bytes", "truncated_rows", "partial_line", "bad_pin", "nonzero_cursor"])
def test_incomplete_or_wrong_snapshot_never_yields_rows(reader, tmp_path, monkeypatch, kind):
    raw = b'{"n":1}\n{"n":2}\n'
    path = tmp_path / "snapshot.jsonl"
    path.write_bytes(raw)
    options = {"expected_snapshot_sha256": sha(raw)}
    if kind == "truncated_bytes":
        options["max_bytes"] = 8
    elif kind == "truncated_rows":
        options["max_rows"] = 1
    elif kind == "partial_line":
        raw = raw[:-1]
        path.write_bytes(raw)
        options["expected_snapshot_sha256"] = sha(raw)
    elif kind == "bad_pin":
        options["expected_snapshot_sha256"] = "NOT_A_SHA"
    else:
        options["cursor"] = reader.read_bridge_log(path, max_rows=1).candidate_cursor
    monkeypatch.setattr(reader, "_parse_rows", lambda *args, **kwargs: pytest.fail("partial bytes parsed"))
    result = reader.read_bridge_log(path, **options)
    assert result.status == reader.BridgeReadStatus.BLOCKED
    assert not result.rows and result.candidate_cursor is None


def test_empty_exact_snapshot_is_valid(reader, tmp_path):
    path = tmp_path / "snapshot.jsonl"
    path.write_bytes(b"")
    result = reader.read_bridge_log(path, expected_snapshot_sha256=sha(b""))
    assert result.status == reader.BridgeReadStatus.IDLE
    assert result.rows == () and result.candidate_cursor.offset == 0


def test_none_preserves_legacy_partial_prefix(reader, tmp_path):
    path = tmp_path / "snapshot.jsonl"
    path.write_bytes(b'{"n":1}\n{"n":2}\n')
    result = reader.read_bridge_log(path, max_rows=1, expected_snapshot_sha256=None)
    assert result.status == reader.BridgeReadStatus.OK
    assert result.rows == ({"n": 1},)


def test_parser_consumes_verified_bytes_even_if_file_changes_after_read(reader, tmp_path, monkeypatch):
    path = tmp_path / "snapshot.jsonl"
    original = b'{"event":"first"}\n'
    changed = b'{"event":"other"}\n'
    assert len(original) == len(changed)
    path.write_bytes(original)
    parse = reader._parse_rows
    def change_then_parse(payload, **kwargs):
        path.write_bytes(changed)
        return parse(payload, **kwargs)
    monkeypatch.setattr(reader, "_parse_rows", change_then_parse)
    result = reader.read_bridge_log(path, expected_snapshot_sha256=sha(original))
    assert result.status == reader.BridgeReadStatus.OK
    assert result.rows == ({"event": "first"},)


def test_identity_and_generation_guards_remain_active(reader, tmp_path, monkeypatch):
    path = tmp_path / "snapshot.jsonl"
    generation = tmp_path / "generation.json"
    original = b'{"event":"first"}\n'
    path.write_bytes(original)
    generation.write_bytes(b'{"generation":"g1"}')
    base = reader.read_bridge_log(path, generation_path=generation).candidate_cursor
    wrong = replace(base, offset=0, file_identity="synthetic-wrong-identity")
    refused = reader.read_bridge_log(path, cursor=wrong, generation_path=generation,
                                    expected_snapshot_sha256=sha(original))
    assert refused.status in (reader.BridgeReadStatus.BLOCKED, reader.BridgeReadStatus.RETRY)
    assert not refused.rows
    parse = reader._parse_rows
    def change_generation(payload, **kwargs):
        generation.write_bytes(b'{"generation":"g2"}')
        return parse(payload, **kwargs)
    monkeypatch.setattr(reader, "_parse_rows", change_generation)
    changed = reader.read_bridge_log(path, cursor=replace(base, offset=0), generation_path=generation,
                                     expected_snapshot_sha256=sha(original))
    assert changed.status == reader.BridgeReadStatus.RETRY and not changed.rows
