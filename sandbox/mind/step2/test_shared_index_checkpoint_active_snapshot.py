"""Synthetic tests for independently pinned active-artifact bytes only."""
import hashlib

import pytest

from sandbox.mind.step2 import shared_index_checkpoint as checkpoint


def _case():
    contents = {name: ("synthetic-" + name).encode() for name in checkpoint.ARTIFACTS.values()}
    pins = {kind: hashlib.sha256(contents[name]).hexdigest() for kind, name in checkpoint.ARTIFACTS.items()}
    return contents, pins


def test_active_bytes_are_pinned_copied_and_never_read_from_disk(monkeypatch):
    contents, pins = _case()
    old = contents["memory.json"]
    monkeypatch.setattr(checkpoint, "_read", lambda *args, **kw: pytest.fail("unexpected disk read"))
    result = checkpoint._bind_active_artifact_bytes(contents, expected_hashes=pins, enabled=True)
    contents["memory.json"] = b"replaced"
    assert result.read("memory.json") == old
    assert "synthetic-memory" not in repr(result)
    with pytest.raises(AttributeError):
        result._BoundActiveArtifactBytes__values = {}
    with pytest.raises(checkpoint.CheckpointRefused):
        result.read("../memory.json")


@pytest.mark.parametrize("kind", list(checkpoint.ARTIFACTS))
def test_active_snapshot_refuses_tampering(kind):
    contents, pins = _case()
    contents[checkpoint.ARTIFACTS[kind]] += b"tamper"
    with pytest.raises(checkpoint.CheckpointRefused):
        checkpoint._bind_active_artifact_bytes(contents, expected_hashes=pins, enabled=True)


@pytest.mark.parametrize("fault", ["missing_data", "extra_data", "mutable_data", "missing_pin", "extra_pin", "bad_pin"])
def test_active_snapshot_refuses_open_or_malformed_namespaces(fault):
    contents, pins = _case()
    if fault == "missing_data":
        del contents["descriptor.json"]
    elif fault == "extra_data":
        contents["extra.json"] = b"extra"
    elif fault == "mutable_data":
        contents["memory.json"] = bytearray(contents["memory.json"])
    elif fault == "missing_pin":
        del pins["memory_sha256"]
    elif fault == "extra_pin":
        pins["extra_sha256"] = "a" * 64
    else:
        pins["memory_sha256"] = "NOT-A-PIN"
    with pytest.raises(checkpoint.CheckpointRefused):
        checkpoint._bind_active_artifact_bytes(contents, expected_hashes=pins, enabled=True)


def test_active_snapshot_default_off_and_individual_and_combined_budget(monkeypatch):
    contents, pins = _case()
    with pytest.raises(checkpoint.CheckpointRefused):
        checkpoint._bind_active_artifact_bytes(contents, expected_hashes=pins)
    monkeypatch.setattr(checkpoint, "_MAX_ACTIVE_ARTIFACT_BYTES", 1, raising=False)
    with pytest.raises(checkpoint.CheckpointRefused):
        checkpoint._bind_active_artifact_bytes(contents, expected_hashes=pins, enabled=True)


def test_active_snapshot_uses_frozen_copy_during_hashing(monkeypatch):
    contents, pins = _case()
    original = contents["memory.json"]
    real_sha = checkpoint._sha

    def hash_with_swap(data):
        contents["memory.json"] = b"changed-after-copy"
        return real_sha(data)

    monkeypatch.setattr(checkpoint, "_sha", hash_with_swap)
    result = checkpoint._bind_active_artifact_bytes(contents, expected_hashes=pins, enabled=True)
    assert result.read("memory.json") == original


def test_active_snapshot_accepts_exact_budgets(monkeypatch):
    contents, pins = _case()
    monkeypatch.setattr(checkpoint, "_MAX_ACTIVE_ARTIFACT_BYTES", max(map(len, contents.values())))
    monkeypatch.setattr(checkpoint, "_MAX_ACTIVE_SNAPSHOT_BYTES", sum(map(len, contents.values())))
    assert checkpoint._bind_active_artifact_bytes(contents, expected_hashes=pins, enabled=True).read("memory.json")
    monkeypatch.setattr(checkpoint, "_MAX_ACTIVE_ARTIFACT_BYTES", 1024)
    monkeypatch.setattr(checkpoint, "_MAX_ACTIVE_SNAPSHOT_BYTES", sum(map(len, contents.values())) - 1, raising=False)
    with pytest.raises(checkpoint.CheckpointRefused):
        checkpoint._bind_active_artifact_bytes(contents, expected_hashes=pins, enabled=True)


@pytest.mark.parametrize("target", ["contents", "pins"])
def test_active_snapshot_rejects_oversized_mapping_before_copy(target):
    import tracemalloc

    contents, pins = _case()
    oversized = {f"unexpected-{i}": b"x" for i in range(50000)}
    if target == "contents":
        contents = oversized
    else:
        pins = oversized
    already_tracing = tracemalloc.is_tracing()
    if not already_tracing:
        tracemalloc.start()
    try:
        start, _ = tracemalloc.get_traced_memory()
        tracemalloc.reset_peak()
        with pytest.raises(checkpoint.CheckpointRefused):
            checkpoint._bind_active_artifact_bytes(contents, expected_hashes=pins, enabled=True)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        if not already_tracing:
            tracemalloc.stop()
    assert peak - start < 256 * 1024
