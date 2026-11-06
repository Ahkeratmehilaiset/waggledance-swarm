"""Synthetic in-memory data snapshots; no real Q4 consumer or model."""
import hashlib
import importlib
import sys

import pytest


def module():
    return importlib.import_module("tools.wd_q4_verified_data")


def sha(value):
    return hashlib.sha256(value).hexdigest()


def build(data, pins, names, *, enabled=True):
    return module().make_snapshot(enabled=enabled, data=data, expected_sha256=pins, required_names=names)


def test_off_does_not_touch_any_input():
    class Poison:
        def __getattribute__(self, name):
            pytest.fail("OFF must not inspect input")

    poison = Poison()
    assert module().make_snapshot(data=poison, expected_sha256=poison, required_names=poison) is None


def test_verified_a_survives_source_file_and_caller_mapping_change(tmp_path):
    synthetic_source = tmp_path / "input.json"
    synthetic_source.write_bytes(b"synthetic-A")
    caller_data = {"configs/sample.json": synthetic_source.read_bytes()}
    snap = build(caller_data, {"configs/sample.json": sha(b"synthetic-A")}, frozenset(caller_data))
    synthetic_source.write_bytes(b"synthetic-B")
    caller_data["configs/sample.json"] = b"synthetic-B"
    assert snap.read("configs/sample.json") == b"synthetic-A"


def test_snapshot_does_not_open_files_and_cannot_be_mutated(monkeypatch):
    snapshot_module = module()
    pins = {"a.json": sha(b"x")}
    with monkeypatch.context() as patch:
        patch.setattr("builtins.open", lambda *a, **k: pytest.fail("unexpected filesystem access"))
        snapshot = snapshot_module.make_snapshot(enabled=True, data={"a.json": b"x"},
            expected_sha256=pins, required_names=frozenset({"a.json"}))
        pins["a.json"] = "0" * 64
        assert snapshot.read("a.json") == b"x"
    with pytest.raises(TypeError):
        snapshot._items["a.json"] = b"changed"


def test_aggregate_budget_rejects_individually_bounded_entries():
    large = b"x" * module().MAX_ITEM_BYTES
    data = {f"{i}.json": large for i in range(module().MAX_TOTAL_BYTES // len(large) + 1)}
    with pytest.raises(module().DataRefused, match="^verified_data_refused$"):
        build(data, {name: sha(raw) for name, raw in data.items()}, frozenset(data))


@pytest.mark.parametrize("oversized", ["data", "pins"])
def test_oversized_extra_mapping_refuses_without_iterating_it(oversized):
    snapshot_module = module()
    data, pins = {"a.json": b"x"}, {"a.json": sha(b"x")}
    extra = data if oversized == "data" else pins
    for i in range(500):
        extra[f"extra-{i}.json"] = b"x" if oversized == "data" else sha(b"x")
    seen = []
    previous = sys.gettrace()

    def trace(frame, event, arg):
        if (event == "line" and frame.f_code.co_filename == snapshot_module.__file__
                and frame.f_code.co_name == "<genexpr>"):
            seen.append(None)
            if len(seen) > 25:
                raise RuntimeError("synthetic iteration budget exceeded")
        return trace

    try:
        sys.settrace(trace)
        with pytest.raises(snapshot_module.DataRefused):
            snapshot_module.make_snapshot(enabled=True, data=data, expected_sha256=pins,
                                          required_names=frozenset({"a.json"}))
    finally:
        sys.settrace(previous)
    assert len(seen) <= 25, "oversized map was traversed before refusal"


@pytest.mark.parametrize("name", ["../bad.json", "./bad.json", "/bad.json", "a//b.json", "a\\b.json",
                                    "a/../b.json", "C:/b.json", "a/CON.json", "a/b.json.", ""])
def test_noncanonical_names_refuse(name):
    with pytest.raises(module().DataRefused, match="^verified_data_refused$"):
        build({name: b"x"}, {name: sha(b"x")}, frozenset({name}))


@pytest.mark.parametrize("kind", ["missing_data", "extra_data", "missing_pin", "extra_pin", "wrong_digest",
                                  "invalid_digest", "mutable_bytes", "memoryview", "mutable_names", "case_alias",
                                  "too_large", "too_many"])
def test_invalid_snapshot_refuses(kind):
    data, pins, names = {"a.json": b"x"}, {"a.json": sha(b"x")}, frozenset({"a.json"})
    if kind == "missing_data": data = {}
    elif kind == "extra_data": data["b.json"] = b"x"
    elif kind == "missing_pin": pins = {}
    elif kind == "extra_pin": pins["b.json"] = sha(b"x")
    elif kind == "wrong_digest": pins["a.json"] = "0" * 64
    elif kind == "invalid_digest": pins["a.json"] = "not-a-digest"
    elif kind == "mutable_bytes": data["a.json"] = bytearray(b"x")
    elif kind == "memoryview": data["a.json"] = memoryview(b"x")
    elif kind == "mutable_names": names = {"a.json"}
    elif kind == "case_alias":
        data["A.json"] = b"x"; pins["A.json"] = sha(b"x"); names = frozenset(data)
    elif kind == "too_large":
        data["a.json"] = b"x" * (module().MAX_ITEM_BYTES + 1); pins["a.json"] = sha(data["a.json"])
    elif kind == "too_many":
        data = {f"{i}.json": b"x" for i in range(module().MAX_FILES + 1)}
        pins = {name: sha(value) for name, value in data.items()}; names = frozenset(data)
    with pytest.raises(module().DataRefused, match="^verified_data_refused$"):
        build(data, pins, names)


@pytest.mark.parametrize("name", ["A.json", "../a.json", "a\\json", "b.json", 1])
def test_accessor_never_falls_back_for_wrong_name(name):
    snap = build({"a.json": b"x"}, {"a.json": sha(b"x")}, frozenset({"a.json"}))
    with pytest.raises(module().DataRefused, match="^verified_data_refused$") as caught:
        snap.read(name)
    assert caught.value.__cause__ is None and caught.value.__context__ is None
