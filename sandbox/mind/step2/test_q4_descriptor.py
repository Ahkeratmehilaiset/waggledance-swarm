"""Pure Q4 descriptor parsing with synthetic bytes; no model or native code."""
import hashlib
import importlib
import json
import traceback

import pytest


class Refused(ValueError):
    pass


def _descriptor():
    return {"schema": "wd.bridge-mind-q4-memory-descriptor.v2",
            "scope": "wd.q4-shared-projected-events.v1", "memory_sha256": "a" * 64,
            "dim": 1, "encoder": {"model": "synthetic", "revision": "1", "weights_sha256": "b" * 64}}


@pytest.mark.parametrize("fault,reason", [
    ("pin", "descriptor_pin_invalid"), ("bytes", "descriptor_unreadable"),
    ("budget", "descriptor_over_budget"), ("digest", "descriptor_pin_mismatch"),
    ("json", "descriptor_unreadable"), ("scope", "descriptor_scope_mismatch"),
    ("shape", "descriptor_shape"),
])
def test_descriptor_refusals_preserve_existing_reason_and_exception(fault, reason):
    parser = importlib.import_module("sandbox.mind.step2.q4_descriptor")
    value = _descriptor()
    if fault == "scope":
        value["scope"] = "other"
    if fault == "shape":
        value["dim"] = True
    raw = json.dumps(value).encode()
    if fault == "json":
        raw = b'{"sentinel":"unterminated'
    if fault == "budget":
        raw = b" " * (65536 + 1)
    pin = hashlib.sha256(raw).hexdigest()
    if fault == "pin":
        pin = "invalid"
    elif fault == "bytes":
        raw = bytearray(raw)
    elif fault == "digest":
        pin = "0" * 64
    with pytest.raises(Refused, match="^" + reason + "$") as caught:
        parser.read_descriptor_bytes(raw, pin, error_type=Refused)
    assert caught.value.__cause__ is None


def test_descriptor_valid_roundtrip_without_native():
    parser = importlib.import_module("sandbox.mind.step2.q4_descriptor")
    raw = json.dumps(_descriptor()).encode()
    assert parser.read_descriptor_bytes(raw, hashlib.sha256(raw).hexdigest(), error_type=Refused) == _descriptor()


@pytest.mark.parametrize("kind", ["invalid_pin", "mismatch", "malformed", "shape"])
def test_refusal_does_not_retain_raw_payload_or_cause(kind):
    parser = importlib.import_module("sandbox.mind.step2.q4_descriptor")
    sentinel = b'{"private-sentinel":"unfinished'
    raw = sentinel
    pin = hashlib.sha256(raw).hexdigest()
    if kind == "invalid_pin":
        pin = "bad"
    elif kind == "mismatch":
        pin = "0" * 64
    elif kind == "shape":
        raw = b'{"scope":"wd.q4-shared-projected-events.v1","private-sentinel":"x"}'
        pin = hashlib.sha256(raw).hexdigest()
    with pytest.raises(Refused) as caught:
        parser.read_descriptor_bytes(raw, pin, error_type=Refused)
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    for frame, _ in traceback.walk_tb(caught.value.__traceback__):
        if frame.f_globals.get("__name__") == parser.__name__:
            assert all(value is not raw for value in frame.f_locals.values())


def test_unexpected_pin_error_is_scrubbed_and_fixed():
    parser = importlib.import_module("sandbox.mind.step2.q4_descriptor")
    class HostilePin(str):
        def __len__(self):
            raise RuntimeError("synthetic-private-sentinel")
    raw = b"synthetic-private-raw"
    pin = HostilePin("a" * 64)
    with pytest.raises(Refused, match="^descriptor_unreadable$") as caught:
        parser.read_descriptor_bytes(raw, pin, error_type=Refused)
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    for frame, _ in traceback.walk_tb(caught.value.__traceback__):
        if frame.f_globals.get("__name__") == parser.__name__:
            assert all(value is not raw and value is not pin for value in frame.f_locals.values())
