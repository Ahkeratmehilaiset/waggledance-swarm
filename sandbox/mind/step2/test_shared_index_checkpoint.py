"""Synthetic checkpoint state tests; no live bridge or real memory."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from sandbox.mind.step2 import shared_index_checkpoint as checkpoint
from waggledance.core.bridge_log_reader import BridgeCursor

IDENTITY = "windows-v1:00000001:0000000000000001"
POLICY = frozenset({"synthetic-task"})
WINDOWS = pytest.mark.skipif(os.name != "nt", reason="checkpoint publication supports fixed Windows volumes only")
ROSTER = {"codex-lead-1": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
          "codex-tools-1": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
          "claude-rco-1": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
          "claude-rco-2": "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
          "fable-5": "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"}
POLICY_BYTES = json.dumps({"schema": "wd.q4-non-review-policy.v1", "approved_tasks": sorted(POLICY)},
                          sort_keys=True, separators=(",", ":")).encode()
ROSTER_BYTES = json.dumps({"lanes": [{"agent": k, "agent_uuid": v} for k, v in ROSTER.items()
                                       if k != "codex-tools-1"],
                           "tools_supervisor": {"agent": "codex-tools-1", "agent_uuid": ROSTER["codex-tools-1"]}},
                          sort_keys=True, separators=(",", ":")).encode()
STRICT = {"policy_bytes": POLICY_BYTES, "expected_policy_sha256": hashlib.sha256(POLICY_BYTES).hexdigest(),
          "roster_bytes": ROSTER_BYTES, "expected_roster_sha256": hashlib.sha256(ROSTER_BYTES).hexdigest(),
          "expected_anchor_sha256": "e" * 64}


def cursor(n):
    return BridgeCursor(n, IDENTITY, "synthetic")


def binding(start, end, projection_sha256="c" * 64):
    from sandbox.mind.step2.shared_bridge_snapshot import _token
    return {"schema": "wd.q4-shared-index-source-binding.v1", "anchor_sha256": STRICT["expected_anchor_sha256"],
            "projection_sha256": projection_sha256,
            "cursor_start_sha256": hashlib.sha256(_token(start).encode()).hexdigest(),
            "cursor_end_sha256": hashlib.sha256(_token(end).encode()).hexdigest(),
            "policy_sha256": STRICT["expected_policy_sha256"], "roster_sha256": STRICT["expected_roster_sha256"]}


def _synthetic_anchored_delta(path, generation_path, start):
    from sandbox.mind.step2 import shared_bridge_snapshot as snapshot
    anchor = json.dumps({"schema": "wd.q4-source-anchor.v1", "source_path": str(path.resolve()),
                         "generation_path": str(generation_path.resolve()), "cursor_start": asdict(start),
                         "policy_sha256": STRICT["expected_policy_sha256"],
                         "roster_sha256": STRICT["expected_roster_sha256"]}, sort_keys=True).encode()
    anchor_sha = hashlib.sha256(anchor).hexdigest()
    result = snapshot.read_anchored_delta(path, start, POLICY, anchor_bytes=anchor, anchor_sha256=anchor_sha,
        generation_path=generation_path, max_bytes=1 << 20, max_rows=100, policy_bytes=POLICY_BYTES,
        policy_sha256=STRICT["expected_policy_sha256"], roster_bytes=ROSTER_BYTES,
        roster_sha256=STRICT["expected_roster_sha256"])
    return result, anchor_sha


def test_exact_256_pointer_history_is_valid_but_cannot_reserve_another(tmp_path, monkeypatch):
    from sandbox.mind.step2 import shared_projection as projection
    from sandbox.mind.step2.shared_bridge_snapshot import _token
    artifacts = {"descriptor_sha256": "a" * 64, "memory_sha256": "b" * 64,
                 "projection_sha256": "c" * 64, "receipt_sha256": "d" * 64}
    monkeypatch.setattr(checkpoint, "_verify_generation", lambda *args: {
        "generation": "one", **artifacts, "source_binding": binding(cursor(0), cursor(1))})
    pointer = {"schema": checkpoint.SCHEMA, "generation": "one", "artifacts": artifacts,
        "policy_sha256": STRICT["expected_policy_sha256"], "roster_sha256": STRICT["expected_roster_sha256"],
        "anchor_sha256": STRICT["expected_anchor_sha256"], "cursor_start": asdict(cursor(0)),
        "cursor_end": asdict(cursor(1)), "watermark": asdict(cursor(1)), "empty_advance": None,
        "previous": None, "previous_pointer_sha256": None}
    archives = {}
    empty_hash = hashlib.sha256(checkpoint._canonical(projection._projection([], {}, 0, ROSTER, True))).hexdigest()
    token_hash = lambda value: hashlib.sha256(_token(value).encode()).hexdigest()
    for offset in range(2, 257):
        raw = checkpoint._canonical(pointer)
        digest = hashlib.sha256(raw).hexdigest()
        archives["q4-pointer-" + digest + ".json"] = raw
        pointer = dict(pointer, watermark=asdict(cursor(offset)), previous="one", previous_pointer_sha256=digest,
            empty_advance={"schema": "wd.q4-empty-watermark.v1", "anchor_sha256": "f" * 64,
                "projection_sha256": empty_hash, "cursor_start_sha256": token_hash(cursor(offset - 1)),
                "cursor_end_sha256": token_hash(cursor(offset)), "policy_sha256": STRICT["expected_policy_sha256"],
                "roster_sha256": STRICT["expected_roster_sha256"]})
    monkeypatch.setattr(checkpoint, "_read", lambda path, **kwargs: archives[path.name])
    raw = checkpoint._canonical(pointer)
    args = (tmp_path, raw, POLICY, ROSTER, STRICT["expected_policy_sha256"], STRICT["expected_roster_sha256"])
    assert checkpoint._validated_previous(*args) == pointer
    with pytest.raises(checkpoint.CheckpointRefused, match="^checkpoint_refused$"):
        checkpoint._validated_previous(*args, reserve=1)


@WINDOWS
def test_real_verified_empty_deltas_advance_watermark_not_index_binding(tmp_path, monkeypatch):
    from waggledance.core.bridge_log_reader import read_bridge_log
    log = tmp_path / "synthetic-log.jsonl"
    log.write_bytes(b"")
    generation = tmp_path / "synthetic-generation.json"
    generation.write_text('{"generation":"synthetic"}', encoding="utf-8")
    initial = read_bridge_log(log, generation_path=generation).candidate_cursor
    assert initial is not None
    def append(event):
        with log.open("ab") as out:
            out.write(json.dumps(event, sort_keys=True).encode() + b"\n")
    base = {"ts_utc": "2026-01-01T12:00:00Z", "type": "message", "status": "done",
            "agent": "codex-lead-1", "agent_uuid": ROSTER["codex-lead-1"], "task_id": "synthetic-task"}
    append(base)
    initial_delta, _ = _synthetic_anchored_delta(log, generation, initial)
    indexed_end = initial_delta["candidate_cursor"]
    index_root = tmp_path / "index"
    index_root.mkdir()
    artifacts = {"descriptor_sha256": "a" * 64, "memory_sha256": "b" * 64,
                 "projection_sha256": "c" * 64, "receipt_sha256": "d" * 64}
    monkeypatch.setattr(checkpoint, "_verify_generation", lambda root, name, tasks, roster, ph, rh: {
        "generation": name, **artifacts, "source_binding": binding(initial, indexed_end)})
    checkpoint.commit_generation(index_root, "one", initial, indexed_end, POLICY, expected_pointer_sha256=None,
        initial_cursor=initial, expected_artifacts=artifacts, **STRICT)
    first_bytes = (index_root / "q4-latest.json").read_bytes()
    first = json.loads(first_bytes)
    append(dict(base, task_id="outside-policy"))
    empty1, anchor1 = _synthetic_anchored_delta(log, generation, indexed_end)
    assert empty1["projection"]["accepted"] == 0 and empty1["projection"]["refused"] == {}
    first_hash = hashlib.sha256(first_bytes).hexdigest()
    tampered = dict(empty1, source=dict(empty1["source"], cursor_start_sha256="0" * 64))
    for candidate, cas, anchor in ((empty1, "0" * 64, anchor1), (empty1, first_hash, "0" * 64),
                                   (tampered, first_hash, anchor1), (initial_delta, first_hash, anchor1)):
        with pytest.raises(checkpoint.CheckpointRefused, match="^checkpoint_refused$"):
            checkpoint.advance_empty_delta(index_root, candidate, POLICY, expected_pointer_sha256=cas,
                **dict(STRICT, expected_anchor_sha256=anchor))
        assert (index_root / "q4-latest.json").read_bytes() == first_bytes
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    legacy = dict(first, schema="wd.q4-shared-checkpoint.v2")
    legacy.pop("watermark")
    legacy.pop("empty_advance")
    legacy_bytes = json.dumps(legacy).encode()
    (legacy_root / "q4-latest.json").write_bytes(legacy_bytes)
    with pytest.raises(checkpoint.CheckpointRefused, match="^checkpoint_refused$"):
        checkpoint.advance_empty_delta(legacy_root, empty1, POLICY,
            expected_pointer_sha256=hashlib.sha256(legacy_bytes).hexdigest(),
            **dict(STRICT, expected_anchor_sha256=anchor1))
    assert (legacy_root / "q4-latest.json").read_bytes() == legacy_bytes
    with monkeypatch.context() as context:
        def interrupted_replace(*_args):
            raise OSError("synthetic interruption")
        context.setattr(checkpoint.os, "replace", interrupted_replace)
        with pytest.raises(checkpoint.CheckpointRefused, match="^checkpoint_refused$"):
            checkpoint.advance_empty_delta(index_root, empty1, POLICY, expected_pointer_sha256=first_hash,
                **dict(STRICT, expected_anchor_sha256=anchor1))
    assert (index_root / "q4-latest.json").read_bytes() == first_bytes
    gate = threading.Barrier(2)
    outcomes = []
    def compete():
        gate.wait()
        try:
            result = checkpoint.advance_empty_delta(index_root, empty1, POLICY, expected_pointer_sha256=first_hash,
                **dict(STRICT, expected_anchor_sha256=anchor1))
            outcomes.append(result["status"])
        except checkpoint.CheckpointRefused:
            outcomes.append("refused")
    threads = [threading.Thread(target=compete) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    assert sorted(outcomes) == ["advanced", "refused"]
    second_bytes = (index_root / "q4-latest.json").read_bytes()
    second = json.loads(second_bytes)
    assert second["watermark"] == asdict(empty1["candidate_cursor"])
    assert second["cursor_start"] == first["cursor_start"] and second["cursor_end"] == first["cursor_end"]
    assert second["artifacts"] == first["artifacts"]
    assert second["previous_pointer_sha256"] == hashlib.sha256(first_bytes).hexdigest()
    assert (index_root / ("q4-pointer-" + second["previous_pointer_sha256"] + ".json")).read_bytes() == first_bytes
    append(dict(base, task_id="another-unapproved"))
    empty2, anchor2 = _synthetic_anchored_delta(log, generation, empty1["candidate_cursor"])
    result2 = checkpoint.advance_empty_delta(index_root, empty2, POLICY,
        expected_pointer_sha256=hashlib.sha256(second_bytes).hexdigest(), **dict(STRICT, expected_anchor_sha256=anchor2))
    third_bytes = (index_root / "q4-latest.json").read_bytes()
    assert result2["status"] == "advanced" and json.loads(third_bytes)["cursor_end"] == first["cursor_end"]
    none, anchor3 = _synthetic_anchored_delta(log, generation, empty2["candidate_cursor"])
    result3 = checkpoint.advance_empty_delta(index_root, none, POLICY,
        expected_pointer_sha256=hashlib.sha256(third_bytes).hexdigest(), **dict(STRICT, expected_anchor_sha256=anchor3))
    assert result3["status"] == "no_change" and (index_root / "q4-latest.json").read_bytes() == third_bytes
    for mutation in ("projection", "archive", "older_missing", "older_corrupt", "broken_continuity"):
        copy_root = tmp_path / ("tamper-" + mutation)
        shutil.copytree(index_root, copy_root)
        pointer = copy_root / "q4-latest.json"
        if mutation == "projection":
            damaged = json.loads(third_bytes)
            damaged["empty_advance"]["projection_sha256"] = "0" * 64
            pointer.write_bytes(json.dumps(damaged).encode())
        elif mutation == "archive":
            previous = json.loads(third_bytes)["previous_pointer_sha256"]
            (copy_root / ("q4-pointer-" + previous + ".json")).write_bytes(b"synthetic-corruption")
        elif mutation == "broken_continuity":
            damaged = json.loads(third_bytes)
            previous_path = copy_root / ("q4-pointer-" + damaged["previous_pointer_sha256"] + ".json")
            previous = json.loads(previous_path.read_bytes())
            previous["watermark"]["offset"] -= 1
            altered = checkpoint._canonical(previous)
            altered_hash = hashlib.sha256(altered).hexdigest()
            (copy_root / ("q4-pointer-" + altered_hash + ".json")).write_bytes(altered)
            damaged["previous_pointer_sha256"] = altered_hash
            pointer.write_bytes(checkpoint._canonical(damaged))
        else:
            previous = json.loads(third_bytes)["previous_pointer_sha256"]
            previous_raw = (copy_root / ("q4-pointer-" + previous + ".json")).read_bytes()
            older = json.loads(previous_raw)["previous_pointer_sha256"]
            archive = copy_root / ("q4-pointer-" + older + ".json")
            if mutation == "older_missing":
                archive.unlink()
            else:
                archive.write_bytes(b"synthetic-corruption")
        old_copy = pointer.read_bytes()
        with pytest.raises(checkpoint.CheckpointRefused, match="^checkpoint_refused$"):
            checkpoint.advance_empty_delta(copy_root, none, POLICY,
                expected_pointer_sha256=hashlib.sha256(old_copy).hexdigest(),
                **dict(STRICT, expected_anchor_sha256=anchor3))
        assert pointer.read_bytes() == old_copy
    # At capacity, an unchanged check succeeds without publishing, but a real
    # advance must refuse before writing a pointer beyond the bound.
    with monkeypatch.context() as context:
        context.setattr(checkpoint, "_MAX_POINTER_HISTORY", 3)
        unchanged = checkpoint.advance_empty_delta(index_root, none, POLICY,
            expected_pointer_sha256=hashlib.sha256(third_bytes).hexdigest(),
            **dict(STRICT, expected_anchor_sha256=anchor3))
        assert unchanged["status"] == "no_change"
        assert (index_root / "q4-latest.json").read_bytes() == third_bytes
        append(dict(base, task_id="third-unapproved"))
        more, more_anchor = _synthetic_anchored_delta(log, generation, empty2["candidate_cursor"])
        with pytest.raises(checkpoint.CheckpointRefused, match="^checkpoint_refused$"):
            checkpoint.advance_empty_delta(index_root, more, POLICY,
                expected_pointer_sha256=hashlib.sha256(third_bytes).hexdigest(),
                **dict(STRICT, expected_anchor_sha256=more_anchor))
        assert (index_root / "q4-latest.json").read_bytes() == third_bytes
    append(dict(base, status="info"))
    next_end = read_bridge_log(log, cursor=empty2["candidate_cursor"], generation_path=generation).candidate_cursor
    assert next_end is not None
    def verified_after_advances(root, name, tasks, roster, ph, rh):
        start, end = (initial, indexed_end) if name == "one" else (empty2["candidate_cursor"], next_end)
        return {"generation": name, **artifacts, "source_binding": binding(start, end)}
    monkeypatch.setattr(checkpoint, "_verify_generation", verified_after_advances)
    next_index = checkpoint.commit_generation(index_root, "two", empty2["candidate_cursor"], next_end, POLICY,
        expected_pointer_sha256=hashlib.sha256(third_bytes).hexdigest(), expected_artifacts=artifacts, **STRICT)
    assert next_index["cursor_start"] == asdict(empty2["candidate_cursor"])
    assert next_index["watermark"] == asdict(next_end)
    fourth_bytes = (index_root / "q4-latest.json").read_bytes()
    assert checkpoint._validated_previous(index_root, fourth_bytes, POLICY, ROSTER,
        STRICT["expected_policy_sha256"], STRICT["expected_roster_sha256"]) == next_index
    copied = tmp_path / "after-new-index"
    shutil.copytree(index_root, copied)
    older = json.loads(second_bytes)["previous_pointer_sha256"]
    (copied / ("q4-pointer-" + older + ".json")).write_bytes(b"synthetic-corruption")
    with pytest.raises(checkpoint.CheckpointRefused, match="^checkpoint_refused$"):
        checkpoint._validated_previous(copied, fourth_bytes, POLICY, ROSTER,
            STRICT["expected_policy_sha256"], STRICT["expected_roster_sha256"])


@WINDOWS
def test_legacy_unbound_commit_refuses_even_if_a_receipt_is_spliced(tmp_path, monkeypatch):
    monkeypatch.setattr(checkpoint, "_verify_generation", lambda *args, **kwargs: {
        "generation": "one", "descriptor_sha256": "a" * 64, "memory_sha256": "b" * 64,
        "projection_sha256": "c" * 64, "receipt_sha256": "d" * 64,
        "source_binding": binding(cursor(0), cursor(8))})
    artifacts = {"descriptor_sha256": "a" * 64, "memory_sha256": "b" * 64,
                 "projection_sha256": "c" * 64, "receipt_sha256": "d" * 64}
    with pytest.raises(checkpoint.CheckpointRefused, match="^checkpoint_refused$"):
        checkpoint.commit_generation(tmp_path, "one", cursor(0), cursor(9), POLICY,
            expected_pointer_sha256=None, initial_cursor=cursor(0), expected_artifacts=artifacts)
    assert not (tmp_path / "q4-latest.json").exists()


@WINDOWS
def test_strict_commit_refuses_wrong_receipt_cursor_without_advancing(tmp_path, monkeypatch):
    artifacts = {"descriptor_sha256": "a" * 64, "memory_sha256": "b" * 64,
                 "projection_sha256": "c" * 64, "receipt_sha256": "d" * 64}
    def verified(_root, name, _tasks, *args, **kwargs):
        start, end = (cursor(0), cursor(5)) if name == "one" else (cursor(5), cursor(8))
        return {"generation": name, **artifacts, "source_binding": binding(start, end)}
    monkeypatch.setattr(checkpoint, "_verify_generation", verified)
    checkpoint.commit_generation(tmp_path, "one", cursor(0), cursor(5), POLICY,
        expected_pointer_sha256=None, initial_cursor=cursor(0), expected_artifacts=artifacts, **STRICT)
    old = (tmp_path / "q4-latest.json").read_bytes()
    with pytest.raises(checkpoint.CheckpointRefused, match="^checkpoint_refused$"):
        checkpoint.commit_generation(tmp_path, "two", cursor(5), cursor(9), POLICY,
            expected_pointer_sha256=hashlib.sha256(old).hexdigest(), expected_artifacts=artifacts, **STRICT)
    assert (tmp_path / "q4-latest.json").read_bytes() == old


@pytest.mark.parametrize("field", ["schema", "anchor_sha256", "projection_sha256", "cursor_start_sha256",
                                   "cursor_end_sha256", "policy_sha256", "roster_sha256", "extra", "missing"])
@WINDOWS
def test_each_receipt_binding_mismatch_preserves_active_pointer(tmp_path, monkeypatch, field):
    artifacts = {"descriptor_sha256": "a" * 64, "memory_sha256": "b" * 64,
                 "projection_sha256": "c" * 64, "receipt_sha256": "d" * 64}
    def verified(_root, name, _tasks, _roster, _policy_sha, _roster_sha):
        value = binding(cursor(0), cursor(5)) if name == "one" else binding(cursor(5), cursor(8))
        if name != "one":
            if field == "missing":
                del value["cursor_end_sha256"]
            else:
                value[field] = "0" * 64
        return {"generation": name, **artifacts, "source_binding": value}
    monkeypatch.setattr(checkpoint, "_verify_generation", verified)
    checkpoint.commit_generation(tmp_path, "one", cursor(0), cursor(5), POLICY,
        expected_pointer_sha256=None, initial_cursor=cursor(0), expected_artifacts=artifacts, **STRICT)
    old = (tmp_path / "q4-latest.json").read_bytes()
    with pytest.raises(checkpoint.CheckpointRefused, match="^checkpoint_refused$"):
        checkpoint.commit_generation(tmp_path, "two", cursor(5), cursor(8), POLICY,
            expected_pointer_sha256=hashlib.sha256(old).hexdigest(), expected_artifacts=artifacts, **STRICT)
    assert (tmp_path / "q4-latest.json").read_bytes() == old


@WINDOWS
def test_strict_commit_refuses_tampered_independently_pinned_policy_and_roster(tmp_path, monkeypatch):
    monkeypatch.setattr(checkpoint, "_verify_generation", lambda *args, **kwargs: pytest.fail("must not inspect generation"))
    artifacts = {"descriptor_sha256": "a" * 64, "memory_sha256": "b" * 64,
                 "projection_sha256": "c" * 64, "receipt_sha256": "d" * 64}
    for mutation in ({"policy_bytes": POLICY_BYTES + b" "}, {"roster_bytes": ROSTER_BYTES + b" "}):
        with pytest.raises(checkpoint.CheckpointRefused, match="^checkpoint_refused$"):
            checkpoint.commit_generation(tmp_path, "one", cursor(0), cursor(5), POLICY,
                expected_pointer_sha256=None, initial_cursor=cursor(0), expected_artifacts=artifacts,
                **dict(STRICT, **mutation))
    assert not (tmp_path / "q4-latest.json").exists()


@pytest.fixture
def store(tmp_path, monkeypatch):
    # Isolate transaction tests from the separate generation verifier, which
    # has its own shape, hash and index-readiness tests below.
    monkeypatch.setattr(checkpoint, "_verify_generation", lambda root, name, tasks, roster, policy_sha, roster_sha: {
        "generation": name, "descriptor_sha256": "a" * 64, "memory_sha256": "b" * 64,
        "projection_sha256": "c" * 64, "receipt_sha256": "d" * 64,
        "source_binding": binding(cursor(0), cursor(5)) if name == "one" else binding(cursor(5), cursor(8))})
    return tmp_path


def commit(root, name, start, end, previous=None, initial=None):
    return checkpoint.commit_generation(root, name, cursor(start), cursor(end), POLICY,
                                        expected_pointer_sha256=previous, initial_cursor=initial, timeout=0.2,
                                        expected_artifacts={"descriptor_sha256": "a" * 64,
                                                            "memory_sha256": "b" * 64,
                                                            "projection_sha256": "c" * 64,
                                                            "receipt_sha256": "d" * 64}, **STRICT)


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
import hashlib
from sandbox.mind.step2 import shared_index_checkpoint as c
from sandbox.mind.step2.shared_bridge_snapshot import _token
from waggledance.core.bridge_log_reader import BridgeCursor
p, r = sys.argv[5].encode(), sys.argv[6].encode()
ph, rh = hashlib.sha256(p).hexdigest(), hashlib.sha256(r).hexdigest()
def verified(root, name, tasks, roster, policy_sha, roster_sha):
 start, end = (0, 5) if name == 'one' else (5, 8)
 tokenhash = lambda n: hashlib.sha256(_token(BridgeCursor(n, sys.argv[4], 'synthetic')).encode()).hexdigest()
 return dict(generation=name, descriptor_sha256='a'*64, memory_sha256='b'*64,
  projection_sha256='c'*64, receipt_sha256='d'*64, source_binding=dict(
  schema='wd.q4-shared-index-source-binding.v1', anchor_sha256=sys.argv[7], projection_sha256='c'*64,
  cursor_start_sha256=tokenhash(start), cursor_end_sha256=tokenhash(end), policy_sha256=ph, roster_sha256=rh))
c._verify_generation = verified
kw = dict(descriptor_sha256='a'*64, memory_sha256='b'*64, projection_sha256='c'*64, receipt_sha256='d'*64)
try:
 c.commit_generation(sys.argv[1], sys.argv[2], BridgeCursor(5, sys.argv[4], 'synthetic'),
  BridgeCursor(8, sys.argv[4], 'synthetic'), frozenset({'synthetic-task'}),
  expected_pointer_sha256=sys.argv[3], expected_artifacts=kw, timeout=3, policy_bytes=p,
  expected_policy_sha256=ph, roster_bytes=r, expected_roster_sha256=rh, expected_anchor_sha256=sys.argv[7])
 print('ok')
except c.CheckpointRefused:
 print('refused')
"""
    processes = [subprocess.Popen([sys.executable, "-B", "-c", code, str(store), name, previous, IDENTITY,
                                  POLICY_BYTES.decode(), ROSTER_BYTES.decode(), STRICT["expected_anchor_sha256"]],
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
                                "projection_sha256": "c" * 64, "receipt_sha256": "d" * 64}, **STRICT)
    assert not (store / "q4-latest.json").exists()


@WINDOWS
def test_policy_change_is_not_silent_and_must_not_advance(store):
    commit(store, "one", 0, 5, initial=cursor(0))
    old = (store / "q4-latest.json").read_bytes()
    with pytest.raises(checkpoint.CheckpointRefused):
        checkpoint.commit_generation(store, "two", cursor(5), cursor(8), frozenset({"other"}),
            expected_pointer_sha256=hashlib.sha256(old).hexdigest(), timeout=0.2,
            expected_artifacts={"descriptor_sha256": "a" * 64, "memory_sha256": "b" * 64,
                                "projection_sha256": "c" * 64, "receipt_sha256": "d" * 64}, **STRICT)
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
    event = {"ts_utc": "2026-01-01T12:00:01Z", "agent": agent, "agent_uuid": ROSTER[agent], "type": "progress",
             "status": "done", "task_id": task_id}
    record = sp.project_events([event, dict(event)], approved_tasks=POLICY, roster=ROSTER,
                               exclude_review_signal=not agent.startswith("claude-rco-"))
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
    strict_policy = {"authorship": sp.AUTHORSHIP_ROSTER, "review_signal_excluded": True,
                     "policy_sha256": STRICT["expected_policy_sha256"],
                     "roster_sha256": STRICT["expected_roster_sha256"]}
    manifest = json.dumps({"schema": memory.SCHEMA, "dim": 2, "scope": reader.SHARED_SCOPE, "policy": strict_policy,
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
        "memory_name": "one", "projection_sha256": hashlib.sha256(projection).hexdigest(), "policy": strict_policy,
        "source_binding": binding(cursor(0), cursor(5), hashlib.sha256(projection).hexdigest()),
        "memory_sha256": hashlib.sha256(manifest).hexdigest(),
        "descriptor_sha256": hashlib.sha256(descriptor).hexdigest()}).encode()
    (gen / "build_receipt.json").write_bytes(receipt)
    if not allowed:
        expected = {key: hashlib.sha256((gen / name).read_bytes()).hexdigest()
                    for key, name in checkpoint.ARTIFACTS.items()}
        with pytest.raises(checkpoint.CheckpointRefused, match="^checkpoint_refused$"):
            checkpoint.commit_generation(tmp_path, "one", cursor(0), cursor(5), POLICY,
                expected_pointer_sha256=None, expected_artifacts=expected, initial_cursor=cursor(0), **STRICT)
        assert not (tmp_path / "q4-latest.json").exists()
        return
    receipt_object = json.loads(receipt)
    for bad_binding in (None, binding(cursor(0), cursor(9), hashlib.sha256(projection).hexdigest())):
        changed = dict(receipt_object)
        if bad_binding is None:
            changed.pop("source_binding")
        else:
            changed["source_binding"] = bad_binding
        (gen / "build_receipt.json").write_bytes(json.dumps(changed).encode())
        altered_expected = {key: hashlib.sha256((gen / name).read_bytes()).hexdigest()
                            for key, name in checkpoint.ARTIFACTS.items()}
        with pytest.raises(checkpoint.CheckpointRefused, match="^checkpoint_refused$"):
            checkpoint.commit_generation(tmp_path, "one", cursor(0), cursor(5), POLICY,
                expected_pointer_sha256=None, expected_artifacts=altered_expected, initial_cursor=cursor(0), **STRICT)
        assert not (tmp_path / "q4-latest.json").exists()
    (gen / "build_receipt.json").write_bytes(receipt)
    hashes = checkpoint._verify_generation(tmp_path, "one", POLICY, ROSTER,
                                           STRICT["expected_policy_sha256"], STRICT["expected_roster_sha256"])
    expected = {key: hashes[key] for key in checkpoint.ARTIFACTS}
    checkpoint.commit_generation(tmp_path, "one", cursor(0), cursor(5), POLICY,
        expected_pointer_sha256=None, expected_artifacts=expected, initial_cursor=cursor(0), **STRICT)
    old = (tmp_path / "q4-latest.json").read_bytes()
    # Exercise the real verifier and publication path for a complete successor;
    # recompute all derived outer hashes so rejection is not a stale-hash artifact.
    for generation, kind in (("two-cursor", "cursor"), ("two-policy", "policy"),
                             ("two-bool", "bool"), ("two-roster", "roster")):
        successor = tmp_path / generation
        shutil.copytree(gen, successor)
        valid_receipt = json.loads(receipt)
        valid_receipt["memory_name"] = generation
        valid_receipt["source_binding"] = binding(cursor(5), cursor(8), hashlib.sha256(projection).hexdigest())
        (successor / "build_receipt.json").write_text(json.dumps(valid_receipt), encoding="utf-8")
        checkpoint._verify_generation(tmp_path, generation, POLICY, ROSTER,
                                      STRICT["expected_policy_sha256"], STRICT["expected_roster_sha256"])
        next_memory = json.loads(manifest)
        if kind == "policy":
            next_memory["policy"]["policy_sha256"] = "0" * 64
        elif kind == "roster":
            next_memory["policy"]["roster_sha256"] = "0" * 64
        elif kind == "bool":
            next_memory["policy"]["review_signal_excluded"] = 1
        next_memory_bytes = json.dumps(next_memory).encode()
        next_descriptor = json.loads(descriptor)
        next_descriptor["memory_sha256"] = hashlib.sha256(next_memory_bytes).hexdigest()
        next_descriptor_bytes = json.dumps(next_descriptor).encode()
        next_receipt = json.loads(receipt)
        next_receipt["memory_name"] = generation
        next_receipt["memory_sha256"] = hashlib.sha256(next_memory_bytes).hexdigest()
        next_receipt["descriptor_sha256"] = hashlib.sha256(next_descriptor_bytes).hexdigest()
        next_receipt["policy"] = next_memory["policy"]
        next_receipt["source_binding"] = binding(cursor(5), cursor(9 if kind == "cursor" else 8),
                                                  hashlib.sha256(projection).hexdigest())
        (successor / "memory.json").write_bytes(next_memory_bytes)
        (successor / "descriptor.json").write_bytes(next_descriptor_bytes)
        (successor / "build_receipt.json").write_text(json.dumps(next_receipt), encoding="utf-8")
        successor_expected = {key: hashlib.sha256((successor / name).read_bytes()).hexdigest()
                              for key, name in checkpoint.ARTIFACTS.items()}
        with pytest.raises(checkpoint.CheckpointRefused, match="^checkpoint_refused$"):
            checkpoint.commit_generation(tmp_path, generation, cursor(5), cursor(8), POLICY,
                expected_pointer_sha256=hashlib.sha256(old).hexdigest(), expected_artifacts=successor_expected, **STRICT)
        assert (tmp_path / "q4-latest.json").read_bytes() == old
    positive_root = tmp_path / "positive-control"
    positive_root.mkdir()
    shutil.copytree(gen, positive_root / "one")
    shutil.copytree(gen, positive_root / "two-good")
    (positive_root / "q4-latest.json").write_bytes(old)
    valid_receipt = json.loads(receipt)
    valid_receipt["memory_name"] = "two-good"
    valid_receipt["source_binding"] = binding(cursor(5), cursor(8), hashlib.sha256(projection).hexdigest())
    (positive_root / "two-good" / "build_receipt.json").write_text(json.dumps(valid_receipt), encoding="utf-8")
    positive_expected = {key: hashlib.sha256((positive_root / "two-good" / name).read_bytes()).hexdigest()
                         for key, name in checkpoint.ARTIFACTS.items()}
    result = checkpoint.commit_generation(positive_root, "two-good", cursor(5), cursor(8), POLICY,
        expected_pointer_sha256=hashlib.sha256(old).hexdigest(), expected_artifacts=positive_expected, **STRICT)
    assert result["generation"] == "two-good" and result["cursor_end"]["offset"] == 8
    # JSON number 1 compares equal to True in Python, but is not an enabled policy.
    for field, value in (("review_signal_excluded", 1), ("policy_sha256", "0" * 64),
                         ("roster_sha256", "0" * 64), ("unexpected", "value")):
        malformed = json.loads(receipt)
        malformed["policy"][field] = value
        (gen / "build_receipt.json").write_text(json.dumps(malformed), encoding="utf-8")
        with pytest.raises(checkpoint.CheckpointRefused, match="^checkpoint_refused$"):
            checkpoint._verify_generation(tmp_path, "one", POLICY, ROSTER,
                                          STRICT["expected_policy_sha256"], STRICT["expected_roster_sha256"])
        assert (tmp_path / "q4-latest.json").read_bytes() == old
    (gen / "build_receipt.json").write_bytes(receipt)
    for field, value in (("review_signal_excluded", 1), ("policy_sha256", "0" * 64),
                         ("roster_sha256", "0" * 64), ("unexpected", "value")):
        changed_memory = json.loads(manifest)
        changed_memory["policy"][field] = value
        memory_bytes = json.dumps(changed_memory).encode()
        changed_descriptor = json.loads(descriptor)
        changed_descriptor["memory_sha256"] = hashlib.sha256(memory_bytes).hexdigest()
        descriptor_bytes = json.dumps(changed_descriptor).encode()
        changed_receipt = json.loads(receipt)
        changed_receipt["memory_sha256"] = hashlib.sha256(memory_bytes).hexdigest()
        changed_receipt["descriptor_sha256"] = hashlib.sha256(descriptor_bytes).hexdigest()
        (gen / "memory.json").write_bytes(memory_bytes)
        (gen / "descriptor.json").write_bytes(descriptor_bytes)
        (gen / "build_receipt.json").write_text(json.dumps(changed_receipt), encoding="utf-8")
        with pytest.raises(checkpoint.CheckpointRefused, match="^checkpoint_refused$"):
            checkpoint._verify_generation(tmp_path, "one", POLICY, ROSTER,
                                          STRICT["expected_policy_sha256"], STRICT["expected_roster_sha256"])
        assert (tmp_path / "q4-latest.json").read_bytes() == old
    (gen / "memory.json").write_bytes(manifest)
    (gen / "descriptor.json").write_bytes(descriptor)
    (gen / "build_receipt.json").write_bytes(receipt)
    (files["meta"]).write_bytes(b"synthetic tamper")
    with pytest.raises(Exception):
        checkpoint._verify_generation(tmp_path, "one", POLICY, ROSTER,
                                      STRICT["expected_policy_sha256"], STRICT["expected_roster_sha256"])
    with pytest.raises(checkpoint.CheckpointRefused, match="^checkpoint_refused$"):
        checkpoint.commit_generation(tmp_path, "two", cursor(5), cursor(8), POLICY,
            expected_pointer_sha256=hashlib.sha256(old).hexdigest(), expected_artifacts=expected, **STRICT)
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
