"""Tests-first strict guard consumer protocol; no native cleanup is proved.

The private synthetic owner completions below exercise protocol consistency
ONLY. They are not transport/attestation/adapter observations. The real chain
has no emitter and must remain fail closed; no CLI is imported or enabled.
Dependencies are the exact root-binding slice and primitive named by Lead.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import types

import pytest

ROOT = Path(__file__).resolve().parents[2]
LANE = "codex-tools-1"
LANES = ("codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5")
IDENTITY = "a" * 64
DESCRIPTOR = "b" * 64
OWNERS = ("transport", "attestation", "adapter")
RECORD_KEYS = {"schema", "lane", "call_id", "started_utc", "pid",
               "identity_sha256", "descriptor_sha256"}


def load_actual(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def api(monkeypatch):
    # Pin dependency resolution to this synthetic composition's actual files,
    # not a same-named module in the custodian's different current directory.
    import tools
    primitive = load_actual("synthetic_completion_primitive",
                            ROOT / "tools/wd_q4_lane_quarantine.py")
    root = load_actual("synthetic_completion_root",
                      ROOT / "tools/wd_q4_guard_state_binding.py")
    for name, dependency in (("wd_q4_lane_quarantine", primitive),
                             ("wd_q4_guard_state_binding", root)):
        monkeypatch.setattr(tools, name, dependency, raising=False)
        monkeypatch.setitem(sys.modules, "tools." + name, dependency)
    module = load_actual("synthetic_strict_guard_consumer",
                         ROOT / "tools/wd_q4_guard_completion.py")
    return module, primitive


def acquire(api, base, lane=LANE, identity=IDENTITY, descriptor=DESCRIPTOR):
    module, _primitive = api
    runtime = base / "synthetic-runtime"
    for candidate in LANES:
        (runtime / "shared" / ".codex-audit" / candidate / "q4-caller-state").mkdir(
            parents=True, exist_ok=True)
    fleet = json.dumps({"runtime_root": str(runtime),
                        "lanes": [{"agent": candidate} for candidate in LANES]},
                       sort_keys=True).encode("ascii")
    guard = module.acquire_guard(
        fleet_bytes=fleet, expected_fleet_sha256=hashlib.sha256(fleet).hexdigest(),
        lane=lane, identity_sha256=identity, descriptor_sha256=descriptor)
    marker = runtime / "shared" / ".codex-audit" / lane / "q4-caller-state" / (lane + ".pending.json")
    return guard, marker, fleet


def synthetic_complete(guard, owners=OWNERS):
    # Tests may use private owner capabilities. No public native-proof factory
    # is proposed; future actual resource owners must complete their own handle.
    for owner in owners:
        guard._registry._complete_owner(guard._registry._owners[owner])


def synthetic_witness(guard):
    synthetic_complete(guard)
    return guard._registry._issue()


def held_after(api, guard, marker, proof):
    before = marker.read_bytes()
    with pytest.raises(api[0].GuardCompletionUnavailable) as error:
        guard.release(proof)
    assert str(error.value) == "guard_completion_unavailable"
    assert marker.read_bytes() == before


@pytest.mark.parametrize("proof", [
    None, {}, {"status": "VALIDATED", "cleanup": "chain_confirmed"},
    "a" * 64, 0, object(),
], ids=["none", "empty-dict", "claimed-cleanup", "user-hash", "pid-absence", "object"])
def test_unregistered_proof_cannot_clear(api, tmp_path, proof):
    guard, marker, _ = acquire(api, tmp_path)
    held_after(api, guard, marker, proof)


@pytest.mark.parametrize("owners", [
    (), ("transport",), ("attestation",), ("adapter",),
    ("transport", "attestation"), ("transport", "adapter"),
    ("attestation", "adapter"),
], ids=["none", "transport", "attestation", "adapter", "no-adapter",
        "no-attestation", "no-transport"])
def test_every_missing_owner_contribution_keeps_pending(api, tmp_path, owners):
    guard, marker, _ = acquire(api, tmp_path)
    before = marker.read_bytes()
    synthetic_complete(guard, owners)
    with pytest.raises(api[0].GuardCompletionUnavailable):
        guard._registry._issue()
    assert marker.read_bytes() == before
    held_after(api, guard, marker, None)


def test_synthetic_all_owners_release_only_own_marker_and_never_legacy(api, tmp_path, monkeypatch):
    guard, marker, _ = acquire(api, tmp_path)

    def legacy_forbidden(*args, **kwargs):
        raise AssertionError("new consumer called legacy complete_confirmed")

    monkeypatch.setattr(api[1]._Lease, "complete_confirmed", legacy_forbidden)
    proof = synthetic_witness(guard)
    guard.release(proof)
    assert not marker.exists()
    second, second_marker, _ = acquire(api, tmp_path)
    assert second_marker.is_file()
    held_after(api, second, second_marker, proof)


def test_forged_same_type_object_is_not_registered_witness(api, tmp_path):
    guard, marker, _ = acquire(api, tmp_path)
    actual = synthetic_witness(guard)
    forged = object.__new__(type(actual))
    assert type(forged) is type(actual) and forged is not actual
    held_after(api, guard, marker, forged)


def test_same_field_object_is_not_registered_witness(api, tmp_path):
    guard, marker, _ = acquire(api, tmp_path)
    synthetic_witness(guard)
    forged = types.SimpleNamespace(**json.loads(marker.read_bytes()))
    held_after(api, guard, marker, forged)


@pytest.mark.parametrize("difference", ["call", "lane", "identity", "descriptor"])
def test_foreign_call_or_binding_cannot_clear(api, tmp_path, difference):
    guard, marker, _ = acquire(api, tmp_path / "first")
    options = {"lane": LANE, "identity": IDENTITY, "descriptor": DESCRIPTOR}
    if difference == "lane":
        options["lane"] = "codex-lead-1"
    if difference == "identity":
        options["identity"] = "c" * 64
    if difference == "descriptor":
        options["descriptor"] = "d" * 64
    other, other_marker, _ = acquire(api, tmp_path / "second", **options)
    foreign = synthetic_witness(other)
    held_after(api, guard, marker, foreign)
    # A failed foreign consumption must not consume the actual owner's proof.
    other.release(foreign)
    assert not other_marker.exists() and marker.is_file()


def test_replayed_witness_never_clears_new_call(api, tmp_path):
    first, marker, _ = acquire(api, tmp_path)
    proof = synthetic_witness(first)
    first.release(proof)
    second, second_marker, _ = acquire(api, tmp_path)
    held_after(api, second, second_marker, proof)
    held_after(api, first, second_marker, proof)


def test_duplicate_owner_cannot_stand_for_other_owners(api, tmp_path):
    guard, marker, _ = acquire(api, tmp_path)
    capability = guard._registry._owners["transport"]
    guard._registry._complete_owner(capability)
    with pytest.raises(api[0].GuardCompletionUnavailable):
        guard._registry._complete_owner(capability)
    with pytest.raises(api[0].GuardCompletionUnavailable):
        guard._registry._issue()
    assert marker.is_file()


@pytest.mark.parametrize("when", ["before-issue", "after-issue"])
def test_unknown_is_sticky_and_cannot_be_completed_later(api, tmp_path, when):
    guard, marker, _ = acquire(api, tmp_path)
    proof = synthetic_witness(guard) if when == "after-issue" else None
    guard.mark_unknown()
    for owner in OWNERS:
        with pytest.raises(api[0].GuardCompletionUnavailable):
            guard._registry._complete_owner(guard._registry._owners[owner])
    with pytest.raises(api[0].GuardCompletionUnavailable):
        guard._registry._issue()
    held_after(api, guard, marker, proof)


@pytest.mark.parametrize("capability", [object(), {}, "transport"],
                         ids=["object", "mapping", "owner-name"])
def test_unknown_owner_capability_is_rejected(api, tmp_path, capability):
    guard, marker, _ = acquire(api, tmp_path)
    before = marker.read_bytes()
    with pytest.raises(api[0].GuardCompletionUnavailable):
        guard._registry._complete_owner(capability)
    assert marker.read_bytes() == before
    with pytest.raises(api[0].GuardCompletionUnavailable):
        guard._registry._issue()


def test_revalidated_root_replacement_keeps_marker(api, tmp_path):
    guard, marker, _ = acquire(api, tmp_path)
    proof = synthetic_witness(guard)
    old_root = marker.parent
    moved = old_root.with_name(old_root.name + "-old")
    old_root.rename(moved)
    old_root.mkdir()
    replacement = old_root / marker.name
    original = (moved / marker.name).read_bytes()
    replacement.write_bytes(original)
    held_after(api, guard, replacement, proof)
    assert (moved / marker.name).read_bytes() == original


@pytest.mark.parametrize("change", ["empty", "foreign-bytes", "same-bytes-new-file"])
def test_changed_marker_never_clears(api, tmp_path, change):
    guard, marker, _ = acquire(api, tmp_path)
    proof = synthetic_witness(guard)
    raw = marker.read_bytes()
    if change == "same-bytes-new-file":
        marker.rename(marker.with_suffix(".original"))
        marker.write_bytes(raw)
    else:
        marker.write_bytes(b"" if change == "empty" else b'{"schema":"foreign"}')
    held_after(api, guard, marker, proof)


def test_delete_failure_is_unknown_and_has_no_automatic_retry(api, tmp_path, monkeypatch):
    guard, marker, _ = acquire(api, tmp_path)
    proof = synthetic_witness(guard)
    original = Path.unlink

    def fail_selected(path, *args, **kwargs):
        if path == marker:
            raise OSError("synthetic delete refusal")
        return original(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", fail_selected)
        held_after(api, guard, marker, proof)
    held_after(api, guard, marker, proof)


def test_pending_state_contains_control_metadata_only(api, tmp_path):
    guard, marker, _ = acquire(api, tmp_path)
    raw = marker.read_bytes()
    doc = json.loads(raw)
    assert set(doc) == RECORD_KEYS
    assert doc["lane"] == LANE and doc["identity_sha256"] == IDENTITY
    assert doc["descriptor_sha256"] == DESCRIPTOR
    assert doc["schema"] == "wd.q4-caller-pending.v1"
    assert "witness" not in doc and "owners" not in doc
    for canary in ("synthetic-query-only", "synthetic-owner-token-only", str(tmp_path)):
        assert canary.encode("utf-8") not in raw
        assert json.dumps(canary).encode("ascii")[1:-1] not in raw
    held_after(api, guard, marker, None)


def test_missing_native_emitter_leaves_guard_closed_and_lanes_independent(api, tmp_path):
    guard, marker, _ = acquire(api, tmp_path)
    other, other_marker, _ = acquire(api, tmp_path, lane="codex-lead-1")
    held_after(api, guard, marker, None)
    held_after(api, other, other_marker, None)
    with pytest.raises(api[0].GuardCompletionUnavailable):
        acquire(api, tmp_path)
    assert marker.is_file() and other_marker.is_file()
