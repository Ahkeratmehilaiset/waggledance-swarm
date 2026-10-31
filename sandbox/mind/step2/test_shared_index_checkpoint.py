"""Synthetic checkpoint state tests; no live bridge or real memory."""
import hashlib
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from sandbox.mind.step2 import shared_index_checkpoint as checkpoint
from waggledance.core.bridge_log_reader import BridgeCursor

IDENTITY = "windows-v1:00000001:0000000000000001"
POLICY = frozenset({"synthetic-task"})
WINDOWS = pytest.mark.skipif(os.name != "nt", reason="checkpoint publication supports fixed Windows volumes only")


def cursor(n):
    return BridgeCursor(n, IDENTITY, "synthetic")


@pytest.fixture
def store(tmp_path, monkeypatch):
    # Isolate transaction tests from the separate generation verifier, which
    # has its own shape, hash and index-readiness tests below.
    monkeypatch.setattr(checkpoint, "_verify_generation", lambda root, name, tasks: {
        "generation": name, "descriptor_sha256": "a" * 64, "memory_sha256": "b" * 64,
        "projection_sha256": "c" * 64, "receipt_sha256": "d" * 64})
    return tmp_path


def commit(root, name, start, end, previous=None, initial=None):
    return checkpoint.commit_generation(root, name, cursor(start), cursor(end), POLICY,
                                        expected_pointer_sha256=previous, initial_cursor=initial, timeout=0.2,
                                        expected_artifacts={"descriptor_sha256": "a" * 64,
                                                            "memory_sha256": "b" * 64,
                                                            "projection_sha256": "c" * 64,
                                                            "receipt_sha256": "d" * 64})


@WINDOWS
def test_initial_commit_requires_explicit_cursor_and_rejects_raw_input(store):
    with pytest.raises(checkpoint.CheckpointRefused):
        commit(store, "one", 0, 5)
    result = commit(store, "one", 0, 5, initial=cursor(0))
    assert result["cursor_end"]["offset"] == 5
    data = (store / "q4-latest.json").read_bytes()
    assert b"message" not in data and b"payload" not in data
    with pytest.raises(checkpoint.CheckpointRefused):
        commit(store, "two/private", 5, 8, previous=hashlib.sha256(data).hexdigest())


@WINDOWS
def test_cas_cursor_and_rollback_generation_are_bound(store):
    first = commit(store, "one", 0, 5, initial=cursor(0))
    old = (store / "q4-latest.json").read_bytes()
    previous = hashlib.sha256(old).hexdigest()
    with pytest.raises(checkpoint.CheckpointRefused):
        commit(store, "two", 5, 8, previous="0" * 64)
    with pytest.raises(checkpoint.CheckpointRefused):
        commit(store, "two", 4, 8, previous=previous)
    assert (store / "q4-latest.json").read_bytes() == old
    second = commit(store, "two", 5, 8, previous=previous)
    assert second["previous"] == first["generation"]
    assert second["previous_pointer_sha256"] == previous
    assert (store / ("q4-pointer-" + previous + ".json")).read_bytes() == old


@WINDOWS
def test_rename_failure_does_not_advance(store, monkeypatch):
    commit(store, "one", 0, 5, initial=cursor(0))
    old = (store / "q4-latest.json").read_bytes()
    def fail(*args):
        raise OSError("sensitive path never exposed")
    monkeypatch.setattr(checkpoint.os, "replace", fail)
    with pytest.raises(checkpoint.CheckpointRefused, match="^checkpoint_refused$"):
        commit(store, "two", 5, 8, previous=hashlib.sha256(old).hexdigest())
    assert (store / "q4-latest.json").read_bytes() == old


@pytest.mark.parametrize("failure", ["partial", "fsync"])
@WINDOWS
def test_partial_archive_stage_failure_is_recoverable_without_overwriting_files(store, monkeypatch, failure):
    commit(store, "one", 0, 5, initial=cursor(0))
    old = (store / "q4-latest.json").read_bytes()
    previous = hashlib.sha256(old).hexdigest()
    real_write = checkpoint._write_new
    def partial(path, data):
        if path.name.startswith(".q4-pointer-"):
            with path.open("xb") as out:
                out.write(data[:5])
            raise OSError("synthetic partial write")
        return real_write(path, data)
    with monkeypatch.context() as context:
        if failure == "partial":
            context.setattr(checkpoint, "_write_new", partial)
        else:
            def fail_fsync(_fd):
                raise OSError("synthetic sync failure")
            context.setattr(checkpoint.os, "fsync", fail_fsync)
        with pytest.raises(checkpoint.CheckpointRefused, match="^checkpoint_refused$"):
            commit(store, "two", 5, 8, previous=previous)
    assert (store / "q4-latest.json").read_bytes() == old
    assert not (store / ("q4-pointer-" + previous + ".json")).exists()
    stages = list(store.glob(".q4-pointer-*.staging"))
    retained = old[:5] if failure == "partial" else old
    assert len(stages) == 1 and stages[0].read_bytes() == retained
    result = commit(store, "two", 5, 8, previous=previous)
    assert result["cursor_end"]["offset"] == 8 and stages[0].read_bytes() == retained


@WINDOWS
def test_replace_succeeded_but_ack_failed_requires_pointer_reread(store, monkeypatch):
    commit(store, "one", 0, 5, initial=cursor(0))
    old = (store / "q4-latest.json").read_bytes()
    previous = hashlib.sha256(old).hexdigest()
    real_replace = checkpoint.os.replace
    def replaced_then_error(*args):
        real_replace(*args)
        raise OSError("synthetic acknowledgement failure")
    monkeypatch.setattr(checkpoint.os, "replace", replaced_then_error)
    with pytest.raises(checkpoint.CheckpointRefused, match="^checkpoint_refused$"):
        commit(store, "two", 5, 8, previous=previous)
    current = json.loads((store / "q4-latest.json").read_bytes())
    assert current["generation"] == "two" and current["cursor_end"]["offset"] == 8
    assert (store / ("q4-pointer-" + previous + ".json")).read_bytes() == old


@WINDOWS
def test_archive_publish_race_never_overwrites_competitor(store, monkeypatch):
    commit(store, "one", 0, 5, initial=cursor(0))
    old = (store / "q4-latest.json").read_bytes()
    previous = hashlib.sha256(old).hexdigest()
    real_rename = checkpoint.os.rename
    def compete(source, destination):
        with Path(destination).open("xb") as out:
            out.write(b"synthetic competitor")
        real_rename(source, destination)
    monkeypatch.setattr(checkpoint.os, "rename", compete)
    with pytest.raises(checkpoint.CheckpointRefused, match="^checkpoint_refused$"):
        commit(store, "two", 5, 8, previous=previous)
    assert (store / "q4-latest.json").read_bytes() == old
    assert (store / ("q4-pointer-" + previous + ".json")).read_bytes() == b"synthetic competitor"


@WINDOWS
def test_two_competing_writers_cas_one_success(store):
    commit(store, "one", 0, 5, initial=cursor(0))
    previous = hashlib.sha256((store / "q4-latest.json").read_bytes()).hexdigest()
    barrier = threading.Barrier(2)
    outcomes = []
    def run(name):
        barrier.wait()
        try:
            commit(store, name, 5, 8, previous=previous)
            outcomes.append("ok")
        except checkpoint.CheckpointRefused:
            outcomes.append("refused")
    threads = [threading.Thread(target=run, args=(name,)) for name in ("two", "three")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(outcomes) == ["ok", "refused"]


@WINDOWS
def test_missing_or_tampered_predecessor_refuses(store):
    commit(store, "one", 0, 5, initial=cursor(0))
    old_hash = hashlib.sha256((store / "q4-latest.json").read_bytes()).hexdigest()
    (store / "q4-latest.json").write_bytes(b'{"broken":true}')
    with pytest.raises(checkpoint.CheckpointRefused):
        commit(store, "two", 5, 8, previous=old_hash)


@WINDOWS
def test_duplicate_start_cursor_is_not_implicitly_skipped(store):
    commit(store, "one", 0, 5, initial=cursor(0))
    previous = hashlib.sha256((store / "q4-latest.json").read_bytes()).hexdigest()
    with pytest.raises(checkpoint.CheckpointRefused):
        commit(store, "two", 0, 5, previous=previous)


@WINDOWS
def test_two_processes_share_the_same_lock_and_one_wins_cas(store):
    commit(store, "one", 0, 5, initial=cursor(0))
    previous = hashlib.sha256((store / "q4-latest.json").read_bytes()).hexdigest()
    code = """
import sys
from sandbox.mind.step2 import shared_index_checkpoint as c
from waggledance.core.bridge_log_reader import BridgeCursor
c._verify_generation = lambda root, name, tasks: dict(generation=name, descriptor_sha256='a'*64,
 memory_sha256='b'*64, projection_sha256='c'*64, receipt_sha256='d'*64)
kw = dict(descriptor_sha256='a'*64, memory_sha256='b'*64, projection_sha256='c'*64, receipt_sha256='d'*64)
try:
 c.commit_generation(sys.argv[1], sys.argv[2], BridgeCursor(5, sys.argv[4], 'synthetic'),
  BridgeCursor(8, sys.argv[4], 'synthetic'), frozenset({'synthetic-task'}),
  expected_pointer_sha256=sys.argv[3], expected_artifacts=kw, timeout=3)
 print('ok')
except c.CheckpointRefused:
 print('refused')
"""
    processes = [subprocess.Popen([sys.executable, "-B", "-c", code, str(store), name, previous, IDENTITY],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                 for name in ("two", "three")]
    outcomes = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=15)
        assert process.returncode == 0, stderr
        outcomes.append(stdout.strip())
    assert sorted(outcomes) == ["ok", "refused"]


@WINDOWS
def test_expected_artifact_mismatch_never_commits(store):
    with pytest.raises(checkpoint.CheckpointRefused, match="^checkpoint_refused$"):
        checkpoint.commit_generation(store, "one", cursor(0), cursor(5), POLICY,
            expected_pointer_sha256=None, initial_cursor=cursor(0), timeout=0.2,
            expected_artifacts={"descriptor_sha256": "0" * 64, "memory_sha256": "b" * 64,
                                "projection_sha256": "c" * 64, "receipt_sha256": "d" * 64})
    assert not (store / "q4-latest.json").exists()


@WINDOWS
def test_policy_change_is_not_silent_and_must_not_advance(store):
    commit(store, "one", 0, 5, initial=cursor(0))
    old = (store / "q4-latest.json").read_bytes()
    with pytest.raises(checkpoint.CheckpointRefused):
        checkpoint.commit_generation(store, "two", cursor(5), cursor(8), frozenset({"other"}),
            expected_pointer_sha256=hashlib.sha256(old).hexdigest(), timeout=0.2,
            expected_artifacts={"descriptor_sha256": "a" * 64, "memory_sha256": "b" * 64,
                                "projection_sha256": "c" * 64, "receipt_sha256": "d" * 64})
    assert (store / "q4-latest.json").read_bytes() == old


@pytest.mark.parametrize("agent,task_id,allowed", [
    ("codex-lead-1", "synthetic-task", True),
    ("codex-lead-1", "not-approved", False),
    ("claude-rco-1", "synthetic-task", False),
    ("claude-rco-2", "synthetic-task", False),
])
@WINDOWS
def test_actual_reader_verifies_a_synthetic_index_and_rejects_artifact_tamper(tmp_path, agent, task_id, allowed):
    faiss = pytest.importorskip("faiss")
    np = pytest.importorskip("numpy")
    from sandbox.mind.step2 import cell, memory, shared_projection as sp, wd_q4_private_read as reader
    event = {"ts_utc": "2026-01-01T12:00:01Z", "agent": agent, "type": "progress",
             "status": "done", "task_id": task_id}
    record = sp.project_events([event, dict(event)], approved_tasks=POLICY)
    assert record["accepted"] == 2
    if task_id == "not-approved":
        assert record["records"][0]["meta"]["task_id"] == "UNKNOWN"
    projection = json.dumps(record, sort_keys=True, indent=1).encode()
    row = record["records"][0]
    gen = tmp_path / "one"
    cell_root = gen / "cells" / "general"
    cell_generation = cell_root / "gen-000001"
    files = cell.paths(cell_generation, "general")
    files["index"].parent.mkdir(parents=True)
    vectors = np.asarray([[1., 0.], [1., 0.]], dtype="<f4")
    index = faiss.IndexFlatIP(2)
    index.add(vectors)
    files["index"].write_bytes(faiss.serialize_index(index).tobytes())
    ids = [row["record_id"] + "#" + v for v in ("w", "d")]
    files["meta"].write_text(json.dumps({"dim": 2, "doc_ids": ids,
        "texts": [row["stored_text"]] * 2, "metadata": [{}, {}]}), encoding="utf-8")
    prov = [{"record_id": row["record_id"] + "#" + view, "kind": "event", "cell_id": "general",
             "time_utc": row["time_utc"], "provenance": dict(row["provenance"],
             record=row["record_id"], view=view)} for view in ("w", "d")]
    files["provenance"].write_text("".join(cell.canonical(value) + "\n" for value in prov), encoding="utf-8")
    encoder = {"model": "synthetic", "revision": "a" * 40, "weights_sha256": "b" * 64}
    content = {"cell_id": "general", "count": 2, "dim": 2, "encoder": encoder,
               "cutoff_utc": "2026-01-02T00:00:00.000000Z",
               "files": {n: cell.file_sha256(files[n]) for n in ("index", "meta", "provenance")},
               "ids_sha256": cell.sha256("\n".join(ids).encode()), "pairs_sha256": cell.pairs_sha256(ids, vectors)}
    cell_receipt = {"schema": cell.SCHEMA, "content": content,
                    "content_sha256": cell.sha256(cell.canonical(content).encode())}
    (cell_generation / "receipt.json").write_text(cell.canonical(cell_receipt), encoding="utf-8")
    pointer = {"generation": 1, "seq": 1, "receipt_sha256": cell.file_sha256(cell_generation / "receipt.json")}
    (cell_root / "current.json").write_text(cell.canonical(pointer), encoding="utf-8")
    (cell_root / "LOG.jsonl").write_text(cell.canonical({"schema": cell.LOG_SCHEMA,
        "prev_line_sha256": cell.ZERO, **pointer}) + "\n", encoding="utf-8")
    manifest = json.dumps({"schema": memory.SCHEMA, "dim": 2, "scope": reader.SHARED_SCOPE,
        "projection_schema": sp.SCHEMA, "projection_sha256": hashlib.sha256(projection).hexdigest(),
        "cells": {"general": pointer}}).encode()
    (gen / "projection.json").write_bytes(projection)
    (gen / "memory.json").write_bytes(manifest)
    descriptor = json.dumps({"schema": reader.DESCRIPTOR_SCHEMA, "scope": reader.SHARED_SCOPE,
        "memory_sha256": hashlib.sha256(manifest).hexdigest(), "dim": 2, "encoder": encoder}).encode()
    (gen / "descriptor.json").write_bytes(descriptor)
    receipt = json.dumps({"schema": "wd.bridge-mind-shared-index-build-receipt.v1", "scope": reader.SHARED_SCOPE,
        "descriptor_schema": reader.DESCRIPTOR_SCHEMA, "projection_schema": sp.SCHEMA,
        "projection_file": "projection.json", "encoder": encoder, "dim": 2,
        "memory_name": "one", "projection_sha256": hashlib.sha256(projection).hexdigest(),
        "memory_sha256": hashlib.sha256(manifest).hexdigest(),
        "descriptor_sha256": hashlib.sha256(descriptor).hexdigest()}).encode()
    (gen / "build_receipt.json").write_bytes(receipt)
    if not allowed:
        expected = {key: hashlib.sha256((gen / name).read_bytes()).hexdigest()
                    for key, name in checkpoint.ARTIFACTS.items()}
        with pytest.raises(checkpoint.CheckpointRefused, match="^checkpoint_refused$"):
            checkpoint.commit_generation(tmp_path, "one", cursor(0), cursor(5), POLICY,
                expected_pointer_sha256=None, expected_artifacts=expected, initial_cursor=cursor(0))
        assert not (tmp_path / "q4-latest.json").exists()
        return
    hashes = checkpoint._verify_generation(tmp_path, "one", POLICY)
    expected = {key: hashes[key] for key in checkpoint.ARTIFACTS}
    checkpoint.commit_generation(tmp_path, "one", cursor(0), cursor(5), POLICY,
        expected_pointer_sha256=None, expected_artifacts=expected, initial_cursor=cursor(0))
    old = (tmp_path / "q4-latest.json").read_bytes()
    (files["meta"]).write_bytes(b"synthetic tamper")
    with pytest.raises(Exception):
        checkpoint._verify_generation(tmp_path, "one", POLICY)
    with pytest.raises(checkpoint.CheckpointRefused, match="^checkpoint_refused$"):
        checkpoint.commit_generation(tmp_path, "two", cursor(5), cursor(8), POLICY,
            expected_pointer_sha256=hashlib.sha256(old).hexdigest(), expected_artifacts=expected)
    assert (tmp_path / "q4-latest.json").read_bytes() == old


def _assert_unsupported_platform_refuses_before_mutation(tmp_path, monkeypatch):
    sentinel = tmp_path / "existing-sentinel"
    sentinel.write_bytes(b"unchanged")
    calls = []
    monkeypatch.setattr(checkpoint, "reader_slot", lambda *args, **kwargs: calls.append("lock"))
    monkeypatch.setattr(checkpoint, "_verify_generation", lambda *args, **kwargs: calls.append("verify"))
    with pytest.raises(checkpoint.CheckpointRefused, match="^checkpoint_refused$"):
        commit(tmp_path, "one", 0, 5, initial=cursor(0))
    assert calls == []
    assert sorted(item.name for item in tmp_path.iterdir()) == ["existing-sentinel"]
    assert sentinel.read_bytes() == b"unchanged"


@pytest.mark.skipif(os.name == "nt", reason="checks the actual unsupported-platform branch")
def test_non_windows_fails_closed_before_writing_or_acquiring_lock(tmp_path, monkeypatch):
    with pytest.raises(checkpoint.CheckpointRefused, match="^checkpoint_refused$"):
        checkpoint._secure(tmp_path)
    _assert_unsupported_platform_refuses_before_mutation(tmp_path, monkeypatch)


def test_simulated_non_windows_fails_closed_before_writing_or_acquiring_lock(tmp_path, monkeypatch):
    # Replace this module's os binding only: do not alter pathlib's platform.
    monkeypatch.setattr(checkpoint, "os", SimpleNamespace(name="posix"))
    with pytest.raises(checkpoint.CheckpointRefused, match="^checkpoint_refused$"):
        checkpoint._secure(tmp_path)
    _assert_unsupported_platform_refuses_before_mutation(tmp_path, monkeypatch)
