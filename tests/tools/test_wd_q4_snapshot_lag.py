"""Small synthetic fixtures for the default-off observed-position guard."""
import hashlib
import json
from dataclasses import replace

import pytest

from waggledance.core import bridge_log_reader as log_reader
from tools import wd_q4_snapshot_lag as guard


@pytest.fixture
def position(tmp_path):
    path = tmp_path / "events.jsonl"
    generation = tmp_path / "generation.json"
    raw = b'{"event":"synthetic"}\n'
    path.write_bytes(raw)
    generation.write_text(json.dumps({"generation": "synthetic-v1"}), encoding="utf-8")
    with path.open("rb") as stream:
        identity = log_reader._file_identity(stream)
    pin = log_reader.BridgePrefixPin(log_reader.BridgeCursor(len(raw), identity, "synthetic-v1"),
                                     hashlib.sha256(raw).hexdigest())
    return path, generation, pin


def check(position, **overrides):
    path, generation, pin = position
    args = dict(log_path=str(path), generation_path=str(generation), watermark=pin,
                max_prefix_bytes=1024, enabled=True)
    args.update(overrides)
    return guard.check_snapshot_position(**args)


def test_default_off_does_not_touch_arguments():
    assert guard.check_snapshot_position() == {"status": "OFF"}


def test_exact_observed_end_is_not_source_authentication_or_freshness(position):
    assert check(position) == {"status": "BOUNDED_BY_LOG_POSITION", "lag_bytes": 0,
                               "source_authentication": "UNKNOWN", "freshness": "UNKNOWN"}


@pytest.mark.parametrize("suffix", [b"x", b'{"event":1}\n', b'{}\npartial'])
def test_new_complete_or_partial_bytes_are_never_accepted(position, suffix):
    path, _, _ = position
    with path.open("ab") as stream:
        stream.write(suffix)
    assert check(position) == {"status": "UNKNOWN"}


def test_truncate_rotate_and_same_size_rewrite_are_not_accepted(position):
    path, _, _ = position
    original = path.read_bytes()
    path.write_bytes(original[:-1])
    assert check(position) == {"status": "UNKNOWN"}
    path.write_bytes(b'x' * len(original))
    assert check(position) == {"status": "UNKNOWN"}
    path.unlink()
    path.write_bytes(original)
    assert check(position) == {"status": "UNKNOWN"}


def test_generation_identity_and_prefix_mismatch_fail_closed(position):
    _, generation, pin = position
    for candidate in (replace(pin, sha256="0" * 64),
                      replace(pin, cursor=replace(pin.cursor, file_identity="wrong")),
                      replace(pin, cursor=replace(pin.cursor, generation=None)),
                      replace(pin, cursor=replace(pin.cursor, generation="other"))):
        assert check(position, watermark=candidate) == {"status": "UNKNOWN"}
    generation.write_text(json.dumps({"generation": "new-v2"}), encoding="utf-8")
    assert check(position) == {"status": "UNKNOWN"}
    generation.unlink()
    assert check(position) == {"status": "UNKNOWN"}


def test_invalid_switch_paths_and_prefix_budget_fail_closed(position):
    path, _, pin = position
    for overrides in ({"enabled": 1}, {"max_prefix_bytes": pin.cursor.offset - 1},
                      {"max_prefix_bytes": True}, {"log_path": path.name},
                      {"generation_path": None}, {"watermark": None}):
        assert check(position, **overrides) == {"status": "UNKNOWN"}


def test_reader_exception_is_unknown_no_sensitive_reason(position, monkeypatch):
    def refuse(*args, **kwargs):
        raise OSError("synthetic-private-path")
    monkeypatch.setattr(guard, "read_bridge_log", refuse)
    assert check(position) == {"status": "UNKNOWN"}


def test_explicit_bounded_lag_accepts_only_complete_records_and_reports_bytes(position):
    path, _, _ = position
    suffix = b'{"event":"a"}\n{"event":"b"}\n'
    with path.open("ab") as stream:
        stream.write(suffix)
    assert check(position, max_lag_bytes=len(suffix)) == {
        "status": "BOUNDED_BY_LOG_POSITION", "lag_bytes": len(suffix),
        "source_authentication": "UNKNOWN", "freshness": "UNKNOWN"}
    assert check(position, max_lag_bytes=len(suffix) - 1) == {"status": "UNKNOWN"}
    assert check(position) == {"status": "UNKNOWN"}  # default remains strict zero


def test_lag_bound_does_not_hide_partial_tail_or_prefix_rewrite(position):
    path, _, _ = position
    original = path.read_bytes()
    with path.open("ab") as stream:
        stream.write(b'{"event":"partial"}')
    assert check(position, max_lag_bytes=100) == {"status": "UNKNOWN"}
    path.write_bytes(original + b'{"event":"a"}\n')
    assert check(position, max_lag_bytes=100)["status"] == "BOUNDED_BY_LOG_POSITION"
    path.write_bytes(b'x' * len(original) + b'{"event":"a"}\n')
    assert check(position, max_lag_bytes=100) == {"status": "UNKNOWN"}


@pytest.mark.parametrize("lag", [-1, True, 1.0, None, 64 * 1024 * 1024 + 1])
def test_bad_lag_budget_is_unknown(position, lag):
    assert check(position, max_lag_bytes=lag) == {"status": "UNKNOWN"}
