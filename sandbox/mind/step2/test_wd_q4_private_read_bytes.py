"""Synthetic descriptor bytes parity; no model, FAISS execution or live data."""
import hashlib
import json
from pathlib import Path

import pytest

from sandbox.mind.step2 import wd_q4_private_read as reader


def _descriptor():
    return {"schema": reader.DESCRIPTOR_SCHEMA, "scope": reader.SHARED_SCOPE,
            "memory_sha256": "a" * 64, "dim": 1,
            "encoder": {"model": "synthetic", "revision": "123", "weights_sha256": "b" * 64}}


def _raw(value):
    return json.dumps(value, sort_keys=True).encode()


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def test_descriptor_bytes_match_existing_path_without_path_io(tmp_path, monkeypatch):
    raw = _raw(_descriptor())
    path = tmp_path / "synthetic-descriptor.json"
    path.write_bytes(raw)
    expected = reader.read_descriptor(path, _digest(raw))
    with monkeypatch.context() as guard:
        guard.setattr(Path, "open", lambda *a, **kw: pytest.fail("snapshot reopened a path"))
        result = reader.read_descriptor_bytes(raw, _digest(raw))
    assert result == expected == _descriptor()


@pytest.mark.parametrize("fault, reason", [
    ("bad_pin", "descriptor_pin_invalid"), ("wrong_pin", "descriptor_pin_mismatch"),
    ("too_large", "descriptor_over_budget"), ("invalid_json", "descriptor_unreadable"),
    ("invalid_scope", "descriptor_scope_mismatch"), ("invalid_shape", "descriptor_shape"),
])
def test_descriptor_bytes_refusals_match_path_api(tmp_path, fault, reason):
    descriptor = _descriptor()
    if fault == "invalid_scope":
        descriptor["scope"] = "other"
    if fault == "invalid_shape":
        descriptor["dim"] = "1"
    raw = b"{" if fault == "invalid_json" else _raw(descriptor)
    if fault == "too_large":
        raw = b"x" * (reader.DESCRIPTOR_BUDGET + 1)
    pin = "invalid" if fault == "bad_pin" else ("0" * 64 if fault == "wrong_pin" else _digest(raw))
    path = tmp_path / "descriptor.json"
    path.write_bytes(raw)
    for parse in (lambda: reader.read_descriptor(path, pin), lambda: reader.read_descriptor_bytes(raw, pin)):
        with pytest.raises(reader.PrivateReadRefused, match="^" + reason + "$"):
            parse()


def test_descriptor_bytes_refuse_mutable_input():
    raw = _raw(_descriptor())
    with pytest.raises(reader.PrivateReadRefused):
        reader.read_descriptor_bytes(bytearray(raw), _digest(raw))


def test_descriptor_exact_budget_is_accepted_and_pin_precedes_read(tmp_path, monkeypatch):
    raw = _raw(_descriptor())
    raw += b" " * (reader.DESCRIPTOR_BUDGET - len(raw))
    assert reader.read_descriptor_bytes(raw, _digest(raw)) == _descriptor()
    monkeypatch.setattr(Path, "open", lambda *a, **kw: pytest.fail("invalid pin read a path"))
    with pytest.raises(reader.PrivateReadRefused, match="^descriptor_pin_invalid$"):
        reader.read_descriptor(tmp_path / "missing", "bad-pin")


@pytest.mark.parametrize("raw", [b'{"private-sentinel":"unterminated', b"\xff"])
def test_malformed_descriptor_bytes_do_not_retain_json_exception(raw):
    with pytest.raises(reader.PrivateReadRefused, match="^descriptor_unreadable$") as caught:
        reader.read_descriptor_bytes(raw, _digest(raw))
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None
