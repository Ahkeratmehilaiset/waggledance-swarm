"""Proposed source-anchor contract; synthetic files only, no live bridge.

Expected API, intentionally absent when these tests were written:
``read_anchored_delta(path, cursor, tasks, *, anchor_bytes, anchor_sha256,
generation_path, max_bytes, max_rows, policy_bytes, policy_sha256,
roster_bytes, roster_sha256)`` returns a snapshot including its
projection and an in-memory binding to the actual read; the pin is handed in
by an already authenticated caller, not authenticated by this API.
``verify_anchored_projection(snapshot_result, projection, *,
expected_anchor_sha256, expected_policy_sha256, expected_roster_sha256)`` returns True only
for the projection derived from that same snapshot, otherwise raises the
fixed ``SnapshotRefused``. This check alone is not an origin-authentication or
same-user/TOCTOU guarantee. No checkpoint publication is performed here.
"""

import copy
import hashlib
import json
from dataclasses import asdict, replace

import pytest

from sandbox.mind.step2 import shared_bridge_snapshot as snapshot
from waggledance.core.bridge_log_reader import read_bridge_log


TASKS = frozenset({"synthetic-task"})
GENERATION = "synthetic-generation"
ROSTER = {"codex-lead-1": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
          "codex-tools-1": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
          "claude-rco-1": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
          "claude-rco-2": "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
          "fable-5": "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"}


def _bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _line(status):
    return _bytes({"ts_utc": "2026-10-31T12:00:00Z", "agent": "codex-lead-1",
                   "agent_uuid": ROSTER["codex-lead-1"],
                   "type": "message", "status": status,
                   "task_id": "synthetic-task"}) + b"\n"


def _pins():
    policy = _bytes({"schema": "wd.q4-non-review-policy.v1", "approved_tasks": sorted(TASKS)})
    roster = _bytes({"lanes": [{"agent": agent, "agent_uuid": uuid} for agent, uuid in ROSTER.items()
                                 if agent != "codex-tools-1"],
                     "tools_supervisor": {"agent": "codex-tools-1", "agent_uuid": ROSTER["codex-tools-1"]}})
    return {"policy_bytes": policy, "policy_sha256": _sha(policy),
            "roster_bytes": roster, "roster_sha256": _sha(roster)}


@pytest.fixture
def synthetic(tmp_path):
    path = tmp_path / "synthetic.jsonl"
    path.write_bytes(b"")
    generation_path = tmp_path / "generation.json"
    generation_path.write_bytes(_bytes({"generation": GENERATION}))
    cursor = read_bridge_log(path, generation_path=generation_path).candidate_cursor
    assert cursor is not None and cursor.generation == GENERATION
    anchor = {"schema": "wd.q4-source-anchor.v1", "source_path": str(path),
              "generation_path": str(generation_path), "cursor_start": asdict(cursor),
              "policy_sha256": _pins()["policy_sha256"], "roster_sha256": _pins()["roster_sha256"]}
    path.write_bytes(_line("done"))
    return path, generation_path, cursor, anchor


def _read(path, generation_path, cursor, anchor, tasks=TASKS, *, pin=None, pins=None):
    raw = _bytes(anchor)
    return snapshot.read_anchored_delta(path, cursor, tasks, anchor_bytes=raw,
        anchor_sha256=_sha(raw) if pin is None else pin,
        generation_path=generation_path, max_bytes=4096, max_rows=20, **(_pins() if pins is None else pins))


def _verify(result, projection, anchor, **overrides):
    expected = {"expected_anchor_sha256": _sha(_bytes(anchor)),
                "expected_policy_sha256": anchor["policy_sha256"],
                "expected_roster_sha256": anchor["roster_sha256"]}
    expected.update(overrides)
    return snapshot.verify_anchored_projection(result, projection, **expected)


def test_same_delta_projection_is_bound_to_anchored_source(synthetic):
    path, gen, cursor, anchor = synthetic
    result = _read(path, gen, cursor, anchor)
    assert result["candidate_cursor"].offset == len(path.read_bytes())
    assert _verify(result, result["projection"], anchor) is True


@pytest.mark.parametrize("wrong", ["source", "identity", "generation", "cursor", "policy"])
def test_wrong_source_identity_generation_cursor_or_policy_refused(synthetic, wrong):
    path, gen, cursor, anchor = synthetic
    anchor = json.loads(_bytes(anchor))
    if wrong == "source":
        anchor["source_path"] = str(path.parent / "other.jsonl")
    elif wrong == "identity":
        identity = anchor["cursor_start"]["file_identity"]
        anchor["cursor_start"]["file_identity"] = identity[:-1] + ("0" if identity[-1] != "0" else "1")
    elif wrong == "generation":
        anchor["cursor_start"]["generation"] = "other-generation"
    elif wrong == "cursor":
        anchor["cursor_start"]["offset"] += 1
    else:
        anchor["policy_sha256"] = _sha(_bytes(["other-task"]))
    with pytest.raises(snapshot.SnapshotRefused, match="^snapshot_refused$"):
        _read(path, gen, cursor, anchor)


def test_wrong_supplied_cursor_refused_even_if_anchor_is_unchanged(synthetic):
    path, gen, cursor, anchor = synthetic
    with pytest.raises(snapshot.SnapshotRefused, match="^snapshot_refused$"):
        _read(path, gen, replace(cursor, offset=cursor.offset + 1), anchor)


def test_manifest_bytes_do_not_match_external_pin(synthetic):
    path, gen, cursor, anchor = synthetic
    with pytest.raises(snapshot.SnapshotRefused, match="^snapshot_refused$"):
        _read(path, gen, cursor, anchor, pin="0" * 64)


def test_changed_in_memory_anchor_hash_does_not_validate(synthetic):
    path, gen, cursor, anchor = synthetic
    result = _read(path, gen, cursor, anchor)
    result["_projection_binding"] = replace(result["_projection_binding"], anchor_sha256="0" * 64)
    with pytest.raises(snapshot.SnapshotRefused, match="^snapshot_refused$"):
        _verify(result, result["projection"], anchor)


@pytest.mark.parametrize("field", ["expected_anchor_sha256", "expected_policy_sha256", "expected_roster_sha256"])
def test_external_expected_pins_are_required_and_compared(synthetic, field):
    path, gen, cursor, anchor = synthetic
    result = _read(path, gen, cursor, anchor)
    for value in ("0" * 64, None):
        with pytest.raises(snapshot.SnapshotRefused, match="^snapshot_refused$"):
            _verify(result, result["projection"], anchor, **{field: value})


def test_bad_anchor_is_rejected_before_reading_source(synthetic, monkeypatch):
    path, gen, cursor, anchor = synthetic
    calls = []
    monkeypatch.setattr(snapshot, "read_shared_delta", lambda *args, **kwargs: calls.append(True))
    with pytest.raises(snapshot.SnapshotRefused, match="^snapshot_refused$"):
        _read(path, gen, cursor, anchor, pin="0" * 64)
    assert calls == []


def test_duplicate_anchor_keys_and_relative_path_refused(synthetic):
    path, gen, cursor, anchor = synthetic
    raw = b'{"schema":"wd.q4-source-anchor.v1","schema":"wd.q4-source-anchor.v1"}'
    with pytest.raises(snapshot.SnapshotRefused, match="^snapshot_refused$"):
        snapshot.read_anchored_delta(path, cursor, TASKS, anchor_bytes=raw,
            anchor_sha256=_sha(raw), generation_path=gen, max_bytes=4096, max_rows=20, **_pins())
    anchor["source_path"] = "synthetic.jsonl"
    with pytest.raises(snapshot.SnapshotRefused, match="^snapshot_refused$"):
        _read(path, gen, cursor, anchor)


def test_unknown_generation_refused(synthetic):
    path, gen, cursor, anchor = synthetic
    cursor = replace(cursor, generation=None)
    anchor["cursor_start"] = asdict(cursor)
    with pytest.raises(snapshot.SnapshotRefused, match="^snapshot_refused$"):
        _read(path, gen, cursor, anchor)


@pytest.mark.parametrize("part", ["projection", "source", "candidate_cursor"])
def test_mutating_result_cannot_keep_binding(synthetic, part):
    path, gen, cursor, anchor = synthetic
    result = _read(path, gen, cursor, anchor)
    original_projection = copy.deepcopy(result["projection"])
    if part == "projection":
        result["projection"]["accepted"] += 1
    elif part == "source":
        result["source"]["coverage"] = "untrusted"
    else:
        result["candidate_cursor"] = replace(result["candidate_cursor"], offset=0)
    with pytest.raises(snapshot.SnapshotRefused, match="^snapshot_refused$"):
        _verify(result, original_projection, anchor)


def test_well_formed_projection_from_another_delta_refused(synthetic):
    path, gen, cursor, anchor = synthetic
    first = _read(path, gen, cursor, anchor)
    # Equal-sized valid JSONL, same temporary file/cursor; this is a separate
    # synthetic read, not a claim that identity prevents in-place mutation.
    before = path.read_bytes()
    other = _line("info")
    assert len(before) == len(other) and other != before
    path.write_bytes(other)
    second = _read(path, gen, cursor, anchor)
    assert first["projection"] != second["projection"]
    assert _verify(second, second["projection"], anchor) is True
    with pytest.raises(snapshot.SnapshotRefused, match="^snapshot_refused$"):
        _verify(first, second["projection"], anchor)


def _rco_line(secret):
    return _bytes({"ts_utc": "2026-10-31T12:00:00Z", "agent": "claude-rco-1",
                   "agent_uuid": ROSTER["claude-rco-1"], "type": "message", "status": "done",
                   "task_id": "synthetic-task", "message": secret}) + b"\n"


def _commitment(result):
    return result["_projection_binding"].semantic_commitment


def test_semantic_commitment_is_repeatable_and_binds_filtered_row(synthetic):
    path, gen, cursor, anchor = synthetic
    path.write_bytes(_line("done") + _rco_line("synthetic-private-one"))
    first = _read(path, gen, cursor, anchor)
    same = _read(path, gen, cursor, anchor)
    assert _commitment(first) == _commitment(same)
    assert len(_commitment(first)) == 64
    path.write_bytes(_line("done") + _rco_line("synthetic-private-two"))
    changed = _read(path, gen, cursor, anchor)
    assert first["projection"] == changed["projection"]
    assert _commitment(first) != _commitment(changed)
    assert "synthetic-private-one" not in repr(first)
    assert "synthetic-private-two" not in repr(changed)


@pytest.mark.parametrize("mutation", ["order", "duplicate"])
def test_semantic_commitment_covers_row_order_and_duplicates(synthetic, mutation):
    path, gen, cursor, anchor = synthetic
    first_line, other_line = _rco_line("synthetic-private-one"), _rco_line("synthetic-private-two")
    path.write_bytes(first_line + other_line)
    original = _read(path, gen, cursor, anchor)
    path.write_bytes(other_line + first_line if mutation == "order" else first_line + first_line)
    changed = _read(path, gen, cursor, anchor)
    assert original["projection"] == changed["projection"]
    assert _commitment(original) != _commitment(changed)


def test_semantic_commitment_binds_cursor_interval(synthetic):
    path, gen, cursor, anchor = synthetic
    first = _read(path, gen, cursor, anchor)
    next_cursor = first["candidate_cursor"]
    next_anchor = dict(anchor, cursor_start=asdict(next_cursor))
    second = _read(path, gen, next_cursor, next_anchor)
    assert _commitment(first) != _commitment(second)


@pytest.mark.parametrize("name", ["policy", "roster"])
def test_semantic_commitment_binds_exact_pinned_policy_and_roster_bytes(synthetic, name):
    path, gen, cursor, anchor = synthetic
    original = _read(path, gen, cursor, anchor)
    pins = _pins()
    raw_key, hash_key = name + "_bytes", name + "_sha256"
    pins[raw_key] = json.dumps(json.loads(pins[raw_key]), indent=1).encode("utf-8")
    pins[hash_key] = _sha(pins[raw_key])
    changed_anchor = dict(anchor, **{hash_key: pins[hash_key]})
    changed = _read(path, gen, cursor, changed_anchor, pins=pins)
    assert original["projection"] == changed["projection"]
    assert _commitment(original) != _commitment(changed)


def test_semantic_commitment_uses_parsed_rows_not_json_key_order(synthetic):
    path, gen, cursor, anchor = synthetic
    raw = _line("done")
    path.write_bytes(raw)
    first = _read(path, gen, cursor, anchor)
    event = json.loads(raw)
    reordered = json.dumps(dict(reversed(list(event.items()))), ensure_ascii=True,
                           separators=(",", ":")).encode() + b"\n"
    assert raw != reordered and len(raw) == len(reordered)
    path.write_bytes(reordered)
    second = _read(path, gen, cursor, anchor)
    assert _commitment(first) == _commitment(second)


def test_mutated_semantic_commitment_fails_binding_verification(synthetic):
    path, gen, cursor, anchor = synthetic
    result = _read(path, gen, cursor, anchor)
    result["_projection_binding"] = replace(result["_projection_binding"], semantic_commitment="0" * 64)
    with pytest.raises(snapshot.SnapshotRefused, match="^snapshot_refused$"):
        _verify(result, result["projection"], anchor)
