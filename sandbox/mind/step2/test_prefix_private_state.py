"""Synthetic private-state contract; no live fleet, source or model."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import importlib
import os
from pathlib import Path

import pytest

from waggledance.core.bridge_log_reader import BridgeCursor, BridgePrefixPin


def module():
    return importlib.import_module("sandbox.mind.step2.prefix_private_state")


@pytest.fixture
def dirs(tmp_path):
    private, shared = tmp_path / "private", tmp_path / "shared"
    private.mkdir()
    shared.mkdir()
    return private, shared


def pin(digest="1" * 64, offset=1):
    return BridgePrefixPin(BridgeCursor(offset, "posix-v1:1:2", "synthetic"), digest)


@pytest.mark.skipif(os.name != "nt", reason="secure root requires a Windows fixed volume")
def test_missing_wrong_or_pointer_derived_state_refuses(dirs):
    mod = module()
    private, shared = dirs
    with mod.locked_state(private, shared, enabled=True) as state:
        with pytest.raises(mod.StateRefused):
            state.require("a" * 64, pin())
        state.require(None, pin())
        state.begin(None)
        state.commit(None, "a" * 64, pin())
        state.require("a" * 64, pin())
        assert state.load("a" * 64) == pin()
        assert pin().sha256 not in repr(state.load("a" * 64))
        with pytest.raises(mod.StateRefused):
            state.require("b" * 64, pin())
        with pytest.raises(mod.StateRefused):
            state.require("a" * 64, pin("a" * 64))


@pytest.mark.skipif(os.name != "nt", reason="secure root requires a Windows fixed volume")
def test_pending_or_ambiguous_replace_never_becomes_genesis(dirs, monkeypatch):
    mod = module()
    private, shared = dirs
    with mod.locked_state(private, shared, enabled=True) as state:
        state.begin(None)
        real_replace = mod.os.replace
        def ambiguous(*args):
            real_replace(*args)
            raise OSError("synthetic ambiguity")
        monkeypatch.setattr(mod.os, "replace", ambiguous)
        with pytest.raises(mod.StateRefused):
            state.commit(None, "a" * 64, pin())
    with mod.locked_state(private, shared, enabled=True) as state:
        for pointer in (None, "a" * 64):
            with pytest.raises(mod.StateRefused):
                state.require(pointer, pin())


@pytest.mark.skipif(os.name != "nt", reason="secure root requires a Windows fixed volume")
def test_nested_roots_refused(dirs):
    mod = module()
    private, shared = dirs
    for left, right in ((private, private), (private, private.parent), (shared.parent, shared)):
        with pytest.raises(mod.StateRefused):
            with mod.locked_state(left, right, enabled=True):
                pytest.fail("overlapping roots accepted")


@pytest.mark.skipif(os.name != "nt", reason="secure root requires a Windows fixed volume")
def test_disabled_and_corrupted_private_state_fail_closed(dirs):
    mod = module()
    private, shared = dirs
    with pytest.raises(mod.StateRefused):
        with mod.locked_state(private, shared):
            pytest.fail("default must be off")
    with mod.locked_state(private, shared, enabled=True) as state:
        state.begin(None)
        state.commit(None, "a" * 64, pin())
        target = private / mod._STATE
        target.write_bytes(target.read_bytes().replace(b'"schema":', b'"schema":"forged","schema":'))
        with pytest.raises(mod.StateRefused) as error:
            state.load("a" * 64)
        assert error.value.__context__ is None and error.value.__cause__ is None


@pytest.mark.skipif(os.name != "nt", reason="secure root requires a Windows fixed volume")
def test_existing_reparse_state_is_refused(dirs):
    mod = module()
    private, shared = dirs
    external = shared / "synthetic"
    external.write_text("synthetic")
    try:
        (private / mod._STATE).symlink_to(external)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation unavailable to test process")
    with mod.locked_state(private, shared, enabled=True) as state:
        with pytest.raises(mod.StateRefused):
            state.require(None, pin())


@pytest.mark.skipif(os.name != "nt", reason="secure root requires a Windows fixed volume")
def test_concurrent_stale_cas_cannot_overwrite(dirs):
    mod = module()
    private, shared = dirs
    with mod.locked_state(private, shared, enabled=True) as state:
        state.begin(None)
        state.commit(None, "a" * 64, pin())
    def attempt(next_pointer):
        try:
            with mod.locked_state(private, shared, enabled=True, timeout=3) as state:
                state.require("a" * 64, pin())
                state.begin("a" * 64)
                state.commit("a" * 64, next_pointer, replace(pin(), sha256=next_pointer))
            return "committed"
        except mod.StateRefused:
            return "refused"
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(attempt, ("b" * 64, "c" * 64)))
    assert sorted(outcomes) == ["committed", "refused"]


def test_root_alias_comparison_checks_ancestor_identity(monkeypatch):
    mod = module()
    private, shared = Path("synthetic-private"), Path("synthetic-shared")
    monkeypatch.setattr(mod.os.path, "samefile", lambda left, right:
                        left == private and right == shared)
    assert mod._root_disjoint(private, shared) is False
    monkeypatch.setattr(mod.os.path, "samefile", lambda left, right: False)
    assert mod._root_disjoint(private, shared) is True


@pytest.mark.parametrize("direction", ["private_is_shared_ancestor", "shared_is_private_ancestor"])
def test_aliased_ancestor_identity_refuses_both_nested_directions(monkeypatch, direction):
    mod = module()
    private = Path("synthetic-private-alias") / "private-child"
    shared = Path("synthetic-shared-alias") / "shared-child"
    pair = ((private.parent, shared) if direction == "shared_is_private_ancestor"
            else (shared.parent, private))
    calls = []
    def samefile(left, right):
        calls.append((left, right))
        return (left, right) == pair
    monkeypatch.setattr(mod.os.path, "samefile", samefile)
    assert mod._root_disjoint(private, shared) is False
    assert pair in calls


@pytest.mark.skipif(os.name != "nt", reason="secure root requires a Windows fixed volume")
def test_root_identity_error_refuses_before_lock_open(dirs, monkeypatch):
    mod = module()
    private, shared = dirs
    def fail_identity(*_):
        raise OSError("synthetic identity error")
    def forbidden_lock(*_, **__):
        pytest.fail("lock must not be opened after an identity error")
    monkeypatch.setattr(mod.os.path, "samefile", fail_identity)
    monkeypatch.setattr(mod, "reader_slot", forbidden_lock)
    with pytest.raises(mod.StateRefused, match="^private_state_refused$") as error:
        with mod.locked_state(private, shared, enabled=True):
            pytest.fail("identity error must refuse")
    assert error.value.__context__ is None and error.value.__cause__ is None
