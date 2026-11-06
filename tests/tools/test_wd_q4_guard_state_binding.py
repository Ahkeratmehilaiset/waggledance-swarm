"""Tests-first anchored guard directory contract, using synthetic directories only.

Lead supplies the exact CLI/guard composition and preserves RED before Source.
This helper consumes fleet bytes and their ALREADY externally anchored pin;
it is not an authentication boundary or an alternative manifest authority.
No installed runtime directory, ACL, native process or model is touched.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import types

import pytest


ROOT = Path(__file__).resolve().parents[2]
LANES = ("codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5")
LANE = "codex-tools-1"


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def binding_module():
    spec = importlib.util.spec_from_file_location(
        "synthetic_guard_state_binding", ROOT / "tools/wd_q4_guard_state_binding.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # Missing Source is RED, never skipped.
    return module


@pytest.fixture
def layout(tmp_path):
    runtime = tmp_path / "synthetic-runtime"
    paths = {}
    for lane in LANES:
        paths[lane] = runtime / "shared" / ".codex-audit" / lane / "q4-caller-state"
        paths[lane].mkdir(parents=True)
    fleet = {"runtime_root": str(runtime), "lanes": [{"agent": lane} for lane in LANES]}
    return runtime, paths, fleet


def bind(module, fleet, lane=LANE, pin=None):
    raw = encode(fleet)
    return module.bind_state_root(fleet_bytes=raw,
                                  expected_fleet_sha256=digest(raw) if pin is None else pin,
                                  lane=lane)


@pytest.mark.parametrize("lane", LANES, ids=LANES)
def test_exact_preprovisioned_per_lane_root(binding_module, layout, lane):
    runtime, paths, fleet = layout
    before = sorted(str(path.relative_to(runtime)) for path in runtime.rglob("*"))
    bound = bind(binding_module, fleet, lane)
    assert bound.path == paths[lane]
    assert bound.revalidate() == paths[lane]
    assert sorted(str(path.relative_to(runtime)) for path in runtime.rglob("*")) == before


@pytest.mark.parametrize("missing", ["runtime_root", "shared", "audit", "lane", "state"])
def test_missing_root_never_created(binding_module, layout, missing):
    runtime, paths, fleet = layout
    if missing == "runtime_root":
        del fleet["runtime_root"]
    else:
        target = {"state": paths[LANE], "lane": paths[LANE].parent,
                  "audit": runtime / "shared" / ".codex-audit",
                  "shared": runtime / "shared"}[missing]
        # Remove ONLY this test's synthetic, empty directory chain.
        if missing in ("audit", "shared"):
            for lane in LANES:
                paths[lane].rmdir()
                paths[lane].parent.rmdir()
        elif missing == "lane":
            paths[LANE].rmdir()
        if missing == "shared":
            (runtime / "shared" / ".codex-audit").rmdir()
        target.rmdir()
    before = sorted(str(path) for path in runtime.rglob("*"))
    with pytest.raises(binding_module.GuardStateUnavailable):
        bind(binding_module, fleet)
    assert sorted(str(path) for path in runtime.rglob("*")) == before


@pytest.mark.parametrize("value", [None, "", 42, {}, [], "relative/root", "C:relative",
                                  "//server/share", "\\\\?\\C:\\runtime"])
def test_invalid_runtime_root_fails_closed(binding_module, layout, value):
    _, _, fleet = layout
    fleet["runtime_root"] = value
    with pytest.raises(binding_module.GuardStateUnavailable):
        bind(binding_module, fleet)


@pytest.mark.parametrize("form", ["dot", "dotdot", "volume-root", "control"])
def test_noncanonical_runtime_root_fails_closed(binding_module, layout, form):
    runtime, _, fleet = layout
    fleet["runtime_root"] = {
        "dot": str(runtime) + os.sep + ".", "dotdot": str(runtime) + os.sep + "..",
        "volume-root": runtime.anchor, "control": str(runtime) + "\n",
    }[form]
    with pytest.raises(binding_module.GuardStateUnavailable):
        bind(binding_module, fleet)


@pytest.mark.parametrize("pin", [None, "", "g" * 64, "0" * 64, 42])
def test_missing_or_wrong_external_fleet_pin_refuses(binding_module, layout, pin):
    _, _, fleet = layout
    with pytest.raises(binding_module.GuardStateUnavailable):
        binding_module.bind_state_root(fleet_bytes=encode(fleet),
                                       expected_fleet_sha256=pin, lane=LANE)


@pytest.mark.parametrize("raw", [b"{}", b"[]", b"not-json", b'{"runtime_root":"a","runtime_root":"b"}'])
def test_malformed_or_ambiguous_fleet_refuses(binding_module, raw):
    with pytest.raises(binding_module.GuardStateUnavailable):
        binding_module.bind_state_root(fleet_bytes=raw,
                                       expected_fleet_sha256=digest(raw), lane=LANE)


@pytest.mark.parametrize("lane", ["unknown-lane", "../codex-tools-1", "", None])
def test_invalid_lane_never_selects_another_directory(binding_module, layout, lane):
    _, _, fleet = layout
    with pytest.raises(binding_module.GuardStateUnavailable):
        bind(binding_module, fleet, lane)


@pytest.mark.parametrize("node", ["runtime", "shared", "state"])
@pytest.mark.parametrize("kind", ["reparse", "symlink"])
def test_reparse_or_link_anywhere_in_root_chain_refuses(binding_module, layout, monkeypatch, node, kind):
    runtime, paths, fleet = layout
    selected = {"runtime": runtime, "shared": runtime / "shared", "state": paths[LANE]}[node]
    original = Path.lstat

    def observed(path, *args, **kwargs):
        info = original(path, *args, **kwargs)
        if path != selected:
            return info
        return types.SimpleNamespace(
            st_mode=stat.S_IFLNK if kind == "symlink" else info.st_mode,
            st_file_attributes=0x400 if kind == "reparse" else 0,
            st_dev=info.st_dev, st_ino=info.st_ino)

    monkeypatch.setattr(Path, "lstat", observed)
    with pytest.raises(binding_module.GuardStateUnavailable):
        bind(binding_module, fleet)


@pytest.mark.parametrize("node", ["runtime", "state"])
def test_root_replacement_after_binding_refuses(binding_module, layout, node):
    runtime, paths, fleet = layout
    bound = bind(binding_module, fleet)
    selected = runtime if node == "runtime" else paths[LANE]
    moved = selected.with_name(selected.name + "-old")
    selected.rename(moved)
    selected.mkdir()
    with pytest.raises(binding_module.GuardStateUnavailable):
        bound.revalidate()
    # Identity replacement must not cause a helper-created marker or repair.
    assert list(selected.iterdir()) == []


def test_unrelated_environment_root_cannot_redirect_binding(binding_module, layout, monkeypatch, tmp_path):
    _, paths, fleet = layout
    alternate = tmp_path / "query-selected-root"
    alternate.mkdir()
    monkeypatch.setenv("AGENT_BRIDGE_RUNTIME_ROOT", str(alternate))
    monkeypatch.setenv("WD_Q4_CALLER_STATE_ROOT", str(alternate))
    bound = bind(binding_module, fleet)
    assert bound.path == paths[LANE] and list(alternate.iterdir()) == []


def test_changed_fleet_bytes_cannot_reuse_original_pin(binding_module, layout, tmp_path):
    _, _, fleet = layout
    original_pin = digest(encode(fleet))
    fleet["runtime_root"] = str(tmp_path / "another-root")
    with pytest.raises(binding_module.GuardStateUnavailable):
        bind(binding_module, fleet, pin=original_pin)
