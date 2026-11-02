"""Synthetic read-only checkpoint verification; no authenticated live source."""
import hashlib
import json
import os
from dataclasses import asdict

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("faiss")

from sandbox.mind.step2 import shared_bridge_snapshot as snapshot
from sandbox.mind.step2 import shared_index_builder as builder
from sandbox.mind.step2 import shared_index_checkpoint as checkpoint
from waggledance.core.bridge_log_reader import BridgeCursor, read_bridge_log

pytestmark = pytest.mark.skipif(os.name != "nt", reason="checkpoint is limited to fixed Windows volumes")
TASKS = frozenset({"synthetic-task"})
ROSTER = {"codex-lead-1": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
          "codex-tools-1": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
          "claude-rco-1": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
          "claude-rco-2": "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
          "fable-5": "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"}
IDENTITY = {"model": "synthetic", "revision": "a" * 40, "weights_sha256": "b" * 64}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def raw_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


class FakeE5:
    def __call__(self, texts):
        return np.asarray([[1.0, 0.0] for _ in texts], dtype="<f4")


@pytest.fixture
def publication(tmp_path):
    log = tmp_path / "synthetic.jsonl"
    log.write_bytes(b"")
    generation = tmp_path / "generation.json"
    generation.write_bytes(b'{"generation":"synthetic"}')
    start = read_bridge_log(log, generation_path=generation).candidate_cursor
    assert start is not None
    policy = raw_json({"schema": "wd.q4-non-review-policy.v1", "approved_tasks": sorted(TASKS)})
    roster = raw_json({"schema": "wd.q4-roster-snapshot.v1",
        "lanes": [{"agent": lane, "agent_uuid": uuid, "role": "synthetic"}
                  for lane, uuid in ROSTER.items() if lane != "codex-tools-1"],
        "tools_supervisor": {"agent": "codex-tools-1", "agent_uuid": ROSTER["codex-tools-1"],
                             "role": "synthetic"}})
    pins = {"policy_bytes": policy, "policy_sha256": sha(policy), "roster_bytes": roster,
            "roster_sha256": sha(roster)}
    strict = {"policy_bytes": policy, "expected_policy_sha256": sha(policy), "roster_bytes": roster,
              "expected_roster_sha256": sha(roster)}

    def append(minute, task):
        value = {"ts_utc": f"2026-01-01T12:{minute:02}:20Z", "agent": "codex-lead-1",
                 "agent_uuid": ROSTER["codex-lead-1"], "type": "message", "status": "done", "task_id": task}
        with log.open("ab") as stream:
            stream.write(raw_json(value) + b"\n")

    def delta(cursor):
        anchor = raw_json({"schema": "wd.q4-source-anchor.v1", "source_path": str(log.resolve()),
            "generation_path": str(generation.resolve()), "cursor_start": asdict(cursor),
            "policy_sha256": sha(policy), "roster_sha256": sha(roster)})
        result = snapshot.read_anchored_delta(log, cursor, TASKS, anchor_bytes=anchor, anchor_sha256=sha(anchor),
            generation_path=generation, max_bytes=65536, max_rows=100, **pins)
        return result, sha(anchor)

    for minute in range(4):
        append(minute, "synthetic-task")
    first, anchor = delta(start)
    root = tmp_path / "index"
    root.mkdir()
    builder.build_memory(first["projection"], FakeE5(), IDENTITY, 2, root, "one", approved_tasks=TASKS,
        roster=ROSTER, exclude_review_signal=True, pins=pins,
        source={"snapshot_result": first, "expected_anchor_sha256": anchor})
    artifacts = {key: sha((root / "one" / path).read_bytes()) for key, path in checkpoint.ARTIFACTS.items()}
    checkpoint.commit_generation(root, "one", start, first["candidate_cursor"], TASKS,
        expected_pointer_sha256=None, initial_cursor=start, expected_artifacts=artifacts,
        expected_anchor_sha256=anchor, **strict)
    first_raw = (root / checkpoint.POINTER).read_bytes()
    append(4, "outside-policy")
    empty, empty_anchor = delta(first["candidate_cursor"])
    assert empty["projection"]["accepted"] == 0
    checkpoint.advance_empty_delta(root, empty, TASKS, expected_pointer_sha256=sha(first_raw),
                                   expected_anchor_sha256=empty_anchor, **strict)
    return root, pins, first_raw


def read(root, pins, expected):
    return checkpoint.read_verified_pointer(root, TASKS, expected_pointer_sha256=expected, **pins)


def test_verified_pointer_returns_only_hashes_and_no_mutation(publication):
    root, pins, first_raw = publication
    current = (root / checkpoint.POINTER).read_bytes()
    pointer = json.loads(current)
    before = {str(path.relative_to(root)): sha(path.read_bytes()) for path in root.rglob("*") if path.is_file()}
    out = read(root, pins, sha(current))
    assert out == {"pointer_sha256": sha(current), "generation": "one", "artifacts": pointer["artifacts"],
        "anchor_sha256": pointer["anchor_sha256"],
        "cursor_end_sha256": sha(snapshot._token(BridgeCursor(**pointer["cursor_end"])).encode()),
        "watermark_sha256": sha(snapshot._token(BridgeCursor(**pointer["watermark"])).encode())}
    assert out["cursor_end_sha256"] != out["watermark_sha256"] and sha(first_raw) != sha(current)
    after = {str(path.relative_to(root)): sha(path.read_bytes()) for path in root.rglob("*") if path.is_file()}
    assert after == before


@pytest.mark.parametrize("fault", ["wrong_hash", "old_hash", "policy", "roster", "artifact", "archive"])
def test_invalid_current_pin_chain_or_artifact_refuses(publication, fault):
    root, pins, first_raw = publication
    pointer = root / checkpoint.POINTER
    current = pointer.read_bytes()
    expected = sha(current)
    pins = dict(pins)
    if fault == "wrong_hash":
        expected = "0" * 64
    elif fault == "old_hash":
        expected = sha(first_raw)
    elif fault in ("policy", "roster"):
        pins[fault + "_bytes"] += b" "
    elif fault == "artifact":
        (root / "one" / "projection.json").write_bytes(b"{}")
    else:
        (root / ("q4-pointer-" + sha(first_raw) + ".json")).write_bytes(b"{}")
    with pytest.raises(checkpoint.CheckpointRefused, match="^checkpoint_refused$"):
        read(root, pins, expected)
    assert pointer.read_bytes() == current


def test_pointer_change_during_verification_refuses_without_writing(publication, monkeypatch):
    root, pins, _ = publication
    pointer = root / checkpoint.POINTER
    raw = pointer.read_bytes()
    actual_read = checkpoint._read
    seen = 0

    def racing_read(path, **kwargs):
        nonlocal seen
        data = actual_read(path, **kwargs)
        if path == pointer:
            seen += 1
            if seen == 2:
                return b"synthetic changed pointer"
        return data

    monkeypatch.setattr(checkpoint, "_read", racing_read)
    with pytest.raises(checkpoint.CheckpointRefused, match="^checkpoint_refused$"):
        read(root, pins, sha(raw))
    assert seen == 2 and pointer.read_bytes() == raw
