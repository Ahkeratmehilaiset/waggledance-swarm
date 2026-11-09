"""Synthetic guard provisioning tests; never touch the deployed runtime."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import types

import pytest

from tools import wd_q4_guard_state_binding as binding

ROOT = Path(__file__).resolve().parents[2]
LANES = ("codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5")


@pytest.fixture
def provisioner():
    spec = importlib.util.spec_from_file_location("synthetic_guard_provision", ROOT / "tools/wd_q4_guard_state_provision.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def layout(tmp_path):
    runtime = tmp_path / "runtime"
    audit = runtime / "shared" / ".codex-audit"
    audit.mkdir(parents=True)
    raw = json.dumps({"runtime_root": str(runtime)}).encode("ascii")
    return runtime, audit, raw, hashlib.sha256(raw).hexdigest()


def test_disabled_does_not_inspect_inputs(provisioner):
    class NoTouch:
        def __getattribute__(self, name):
            raise AssertionError("OFF inspected input")
    assert provisioner.provision_guard_state(fleet_bytes=NoTouch(), expected_fleet_sha256=NoTouch()) == {"status": "OFF"}


def test_all_five_then_idempotent_preserves_existing_marker(provisioner, layout):
    runtime, audit, raw, digest = layout
    state = audit / LANES[0] / "q4-caller-state"
    state.mkdir(parents=True)
    marker = state / (LANES[0] + ".pending.json")
    marker.write_bytes(b"synthetic-existing-pending")
    for _ in range(2):
        assert provisioner.provision_guard_state(fleet_bytes=raw, expected_fleet_sha256=digest, enabled=True) == {"status": "READY"}
        assert marker.read_bytes() == b"synthetic-existing-pending"
        for lane in LANES:
            assert binding.bind_state_root(fleet_bytes=raw, expected_fleet_sha256=digest, lane=lane).revalidate() == audit / lane / "q4-caller-state"
    assert sorted(path.name for path in audit.iterdir()) == sorted(LANES)


@pytest.mark.parametrize("pin", [None, "", "0" * 64, "g" * 64, 12])
def test_bad_pin_cannot_create_anything(provisioner, layout, pin):
    _, audit, raw, _ = layout
    with pytest.raises(provisioner.ProvisionUnavailable):
        provisioner.provision_guard_state(fleet_bytes=raw, expected_fleet_sha256=pin, enabled=True)
    assert list(audit.iterdir()) == []


@pytest.mark.parametrize("enabled", [None, 1, "true"])
def test_enable_must_be_exact_bool(provisioner, layout, enabled):
    _, audit, raw, pin = layout
    with pytest.raises(provisioner.ProvisionUnavailable):
        provisioner.provision_guard_state(fleet_bytes=raw, expected_fleet_sha256=pin, enabled=enabled)
    assert list(audit.iterdir()) == []


@pytest.mark.parametrize("bad", [b"{}", b"[]", b'{"runtime_root":"a","runtime_root":"b"}', b"garbage"])
def test_bad_fleet_refused_without_creation(provisioner, layout, bad):
    _, audit, _, _ = layout
    with pytest.raises(provisioner.ProvisionUnavailable):
        provisioner.provision_guard_state(fleet_bytes=bad, expected_fleet_sha256=hashlib.sha256(bad).hexdigest(), enabled=True)
    assert list(audit.iterdir()) == []


@pytest.mark.parametrize("node", ["runtime", "audit", "lane", "state"])
@pytest.mark.parametrize("kind", ["symlink", "reparse"])
def test_link_or_reparse_refused(provisioner, layout, monkeypatch, node, kind):
    runtime, audit, raw, pin = layout
    state = audit / LANES[0] / "q4-caller-state"
    state.mkdir(parents=True)
    selected = {"runtime": runtime, "audit": audit, "lane": state.parent, "state": state}[node]
    original = Path.lstat
    def fake(path, *args, **kwargs):
        info = original(path, *args, **kwargs)
        if path != selected:
            return info
        return types.SimpleNamespace(st_mode=stat.S_IFLNK if kind == "symlink" else info.st_mode,
                                     st_file_attributes=0x400 if kind == "reparse" else 0,
                                     st_dev=info.st_dev, st_ino=info.st_ino)
    monkeypatch.setattr(Path, "lstat", fake)
    with pytest.raises(provisioner.ProvisionUnavailable):
        provisioner.provision_guard_state(fleet_bytes=raw, expected_fleet_sha256=pin, enabled=True)
    assert sorted(p.name for p in audit.iterdir()) == [LANES[0]]


def test_existing_non_directory_and_missing_audit_refuse(provisioner, layout):
    _, audit, raw, pin = layout
    (audit / LANES[0]).write_bytes(b"do-not-change")
    with pytest.raises(provisioner.ProvisionUnavailable):
        provisioner.provision_guard_state(fleet_bytes=raw, expected_fleet_sha256=pin, enabled=True)
    assert (audit / LANES[0]).read_bytes() == b"do-not-change"
    (audit / LANES[0]).unlink()
    audit.rmdir()
    with pytest.raises(provisioner.ProvisionUnavailable):
        provisioner.provision_guard_state(fleet_bytes=raw, expected_fleet_sha256=pin, enabled=True)
    assert not audit.exists()


@pytest.mark.parametrize("error", [PermissionError, KeyboardInterrupt])
def test_partial_creation_is_retained_without_cleanup(provisioner, layout, monkeypatch, error):
    _, audit, raw, pin = layout
    original = os.mkdir
    def fail(path, *args, **kwargs):
        if Path(path) == audit / LANES[1]:
            raise error("synthetic")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(os, "mkdir", fail)
    expected = KeyboardInterrupt if error is KeyboardInterrupt else provisioner.ProvisionUnavailable
    with pytest.raises(expected):
        provisioner.provision_guard_state(fleet_bytes=raw, expected_fleet_sha256=pin, enabled=True)
    assert (audit / LANES[0] / "q4-caller-state").is_dir()
    assert not (audit / LANES[1]).exists()


def test_replaced_earlier_state_during_later_creation_is_refused(provisioner, layout, monkeypatch):
    _, audit, raw, pin = layout
    state = audit / LANES[0] / "q4-caller-state"
    state.mkdir(parents=True)
    original_mkdir = os.mkdir
    def replace(path, *args, **kwargs):
        if Path(path) == audit / LANES[1]:
            state.rename(state.with_name("displaced-synthetic-state"))
            original_mkdir(state)
        return original_mkdir(path, *args, **kwargs)
    monkeypatch.setattr(os, "mkdir", replace)
    with pytest.raises(provisioner.ProvisionUnavailable):
        provisioner.provision_guard_state(fleet_bytes=raw, expected_fleet_sha256=pin, enabled=True)
    assert state.is_dir() and state.with_name("displaced-synthetic-state").is_dir()
