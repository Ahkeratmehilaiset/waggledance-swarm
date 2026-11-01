"""Synthetic disk/lock snapshot candidate; never accesses a live store."""
import hashlib
import os
from pathlib import Path

import pytest

from tools import wd_q4_snapshot_candidate as candidate
from tools import wd_q4_secure_transport as transport
from sandbox.mind.step2 import shared_index_checkpoint as checkpoint

pytestmark = pytest.mark.skipif(os.name != "nt", reason="fixed-volume verifier requires Windows")


def _fixture(tmp_path):
    state = tmp_path
    (state / checkpoint.LOCK).write_bytes(b"")
    paths = {}
    def add(name, data):
        path = state / name
        path.write_bytes(data)
        return {"path": str(path.relative_to(tmp_path)), "sha256": hashlib.sha256(data).hexdigest()}
    paths["pointer_bytes"] = add(checkpoint.POINTER, b"synthetic pointer")
    for key in ("policy_bytes", "roster_bytes", "public_policy_bytes"):
        paths[key] = add(key, key.encode())
    paths["active_artifact_bytes"] = {name: add("active-" + name, name.encode())
                                     for name in checkpoint.ARTIFACTS.values()}
    paths["history_bytes"] = {}
    paths["cell_snapshots"] = {"learning": {"pointer": add("cell-pointer", b"cp"),
        "log": add("cell-log", b"cl"), "artifacts": {"receipt": add("cell-receipt", b"cr")}}}
    return paths


def test_candidate_is_default_off_and_is_a_real_locked_disk_capture(tmp_path):
    descriptors = _fixture(tmp_path)
    with pytest.raises(candidate.CandidateRefused):
        candidate.capture(root=tmp_path, descriptors=descriptors)
    result = candidate.capture(root=tmp_path, descriptors=descriptors, enabled=True)
    assert result["status"] == "CANDIDATE_UNVERIFIED"
    assert result["bundle_sha256"] == hashlib.sha256(result["bundle_bytes"]).hexdigest()
    parts = transport._fixed_bundle_decode(result["bundle_bytes"])
    assert parts["pointer_bytes"] == b"synthetic pointer"
    assert parts["cell_snapshots"]["learning"]["artifacts"]["receipt"] == b"cr"


@pytest.mark.parametrize("attack", ["wrong_sha", "outside", "rooted", "missing_lock", "extra_field", "missing_file"])
def test_candidate_refuses_unpinned_unlocked_or_changed_inputs(tmp_path, attack):
    descriptors = _fixture(tmp_path)
    if attack == "wrong_sha":
        descriptors["pointer_bytes"]["sha256"] = "0" * 64
    elif attack == "outside":
        descriptors["pointer_bytes"]["path"] = "../outside"
    elif attack == "rooted":
        descriptors["pointer_bytes"]["path"] = "\\state\\" + checkpoint.POINTER
    elif attack == "missing_lock":
        (tmp_path / checkpoint.LOCK).unlink()
    elif attack == "extra_field":
        descriptors["pointer_bytes"]["other"] = True
    else:
        (tmp_path / "cell-log").unlink()
    with pytest.raises(candidate.CandidateRefused):
        candidate.capture(root=tmp_path, descriptors=descriptors, enabled=True)


def test_capture_holds_existing_slot_during_all_reads(tmp_path, monkeypatch):
    from sandbox.mind.step2.reader_slot import reader_slot, ReaderBusy
    descriptors = _fixture(tmp_path)
    lock = tmp_path / checkpoint.LOCK
    original = checkpoint._read
    seen = []
    def locked_read(*args, **kwargs):
        with pytest.raises(ReaderBusy):
            with reader_slot(lock, timeout_seconds=0):
                pytest.fail("capture did not hold the writer slot")
        seen.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(checkpoint, "_read", locked_read)
    assert candidate.capture(root=tmp_path, descriptors=descriptors, enabled=True)["status"] == "CANDIDATE_UNVERIFIED"
    assert len(seen) == 11


def test_file_changed_during_capture_refuses(tmp_path, monkeypatch):
    descriptors = _fixture(tmp_path)
    original = checkpoint._read
    def alter_after_first(*args, **kwargs):
        raw = original(*args, **kwargs)
        (tmp_path / "cell-log").write_bytes(b"different")
        monkeypatch.setattr(checkpoint, "_read", original)
        return raw
    monkeypatch.setattr(checkpoint, "_read", alter_after_first)
    with pytest.raises(candidate.CandidateRefused):
        candidate.capture(root=tmp_path, descriptors=descriptors, enabled=True)


@pytest.mark.parametrize("after_read", [False, True])
def test_refusal_traceback_does_not_expose_inputs_or_read_bytes(tmp_path, monkeypatch, after_read):
    private = tmp_path / "private_root_sentinel"
    private.mkdir()
    descriptors = _fixture(private)
    secret = b"synthetic_sensitive_byte_sentinel"
    path = private / descriptors["pointer_bytes"]["path"]
    path.write_bytes(secret)
    descriptors["pointer_bytes"]["sha256"] = hashlib.sha256(secret).hexdigest()
    if after_read:
        (private / "cell-log").unlink()
    else:
        descriptors["pointer_bytes"]["sha256"] = "0" * 64
    with pytest.raises(candidate.CandidateRefused) as failure:
        candidate.capture(root=private, descriptors=descriptors, enabled=True)
    tb = failure.value.__traceback__
    frames = []
    while tb:
        if Path(tb.tb_frame.f_code.co_filename).name == "wd_q4_snapshot_candidate.py":
            frames.append(repr(tb.tb_frame.f_locals))
        tb = tb.tb_next
    assert frames
    assert all("private_root_sentinel" not in frame and "synthetic_sensitive_byte_sentinel" not in frame
               for frame in frames)
    assert failure.value.__context__ is None


def test_pointer_and_lock_cannot_be_selected_from_decoy_subdirectory(tmp_path):
    descriptors = _fixture(tmp_path)
    decoy = tmp_path / "decoy"
    decoy.mkdir()
    (decoy / checkpoint.POINTER).write_bytes(b"synthetic pointer")
    (decoy / checkpoint.LOCK).write_bytes(b"")
    descriptors["pointer_bytes"]["path"] = str((decoy / checkpoint.POINTER).relative_to(tmp_path))
    with pytest.raises(candidate.CandidateRefused):
        candidate.capture(root=tmp_path, descriptors=descriptors, enabled=True)
