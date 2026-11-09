"""Synthetic canonical fixed-bundle budget only; no runtime or production data."""
import base64
import json

import pytest

from tools import wd_q4_secure_transport as transport


def inputs():
    return dict(pointer_bytes=b"a", policy_bytes=b"b", roster_bytes=b"c", public_policy_bytes=b"d",
                active_artifact_bytes={}, history_bytes={}, cell_snapshots={})


@pytest.mark.parametrize("shape", ["single", "aggregate"])
def test_over_budget_refuses_before_any_base64_copy(monkeypatch, shape):
    monkeypatch.setattr(transport, "_MAX_BUNDLE", 1024)
    params = inputs()
    if shape == "single":
        params["pointer_bytes"] = b"x" * 1024
    else:
        params["cell_snapshots"] = {"x": {"pointer": b"x" * 250, "log": b"x" * 250,
                                                  "artifacts": {"x": b"x" * 250}}}
    called = []
    real = transport._b64
    def spy(value):
        called.append(len(value))
        return real(value)
    monkeypatch.setattr(transport, "_b64", spy)
    with pytest.raises(transport._Refused):
        transport.fixed_snapshot_bundle(**params)
    assert called == []


def test_escaped_keys_exact_boundary_and_existing_wire_bytes(monkeypatch):
    params = inputs()
    params["active_artifact_bytes"] = {'a"\\\n\x00é😀': b"\x00\x01\x02\x03"}
    raw = transport.fixed_snapshot_bundle(**params)
    b64 = lambda value: base64.b64encode(value).decode("ascii")
    legacy = json.dumps({"schema": transport._BUNDLE_SCHEMA,
        "pointer": b64(params["pointer_bytes"]), "policy": b64(params["policy_bytes"]),
        "roster": b64(params["roster_bytes"]), "public_policy": b64(params["public_policy_bytes"]),
        "active_artifacts": {key: b64(value) for key, value in params["active_artifact_bytes"].items()},
        "history": {}, "cells": {}}, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    assert raw == legacy
    monkeypatch.setattr(transport, "_MAX_BUNDLE", len(raw))
    assert transport.fixed_snapshot_bundle(**params) == raw
    assert transport._fixed_bundle_decode(raw) == params
    monkeypatch.setattr(transport, "_MAX_BUNDLE", len(raw) - 1)
    with pytest.raises(transport._Refused):
        transport.fixed_snapshot_bundle(**params)


@pytest.mark.parametrize("key", ["", "\x01\b\t\n\f\r\x1f", "\x7f\u0080\u07ff\u0800\ud800\uffff😀"])
def test_json_string_size_matches_encoder_for_each_character_class(key):
    params = inputs()
    params["history_bytes"] = {key: b"three"}
    raw = transport.fixed_snapshot_bundle(**params)
    assert transport._fixed_bundle_decode(raw) == params


def test_budget_check_refuses_large_key_before_encoding_any_value(monkeypatch):
    monkeypatch.setattr(transport, "_MAX_BUNDLE", 1024)
    params = inputs()
    params["history_bytes"] = {"😀" * 200: b"x"}
    called = []
    real = transport._b64
    monkeypatch.setattr(transport, "_b64", lambda x: called.append(True) or real(x))
    with pytest.raises(transport._Refused):
        transport.fixed_snapshot_bundle(**params)
    assert called == []


def test_cell_object_rejects_non_string_key_impersonating_pointer_before_encoding(monkeypatch):
    class ForgedPointer:
        def __hash__(self):
            return hash("pointer")
        def __eq__(self, other):
            return other == "pointer"
    params = inputs()
    params["cell_snapshots"] = {"c": {ForgedPointer(): b"not-a-real-key", "log": b"l", "artifacts": {}}}
    called = []
    real = transport._b64
    monkeypatch.setattr(transport, "_b64", lambda value: called.append(True) or real(value))
    with pytest.raises(transport._Refused):
        transport.fixed_snapshot_bundle(**params)
    assert called == []
