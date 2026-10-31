"""Synthetic public-status boundary; no live data, files outside tmp, or publisher."""
from dataclasses import asdict, replace
import hashlib
import json

import pytest

from sandbox.mind.step2 import shared_bridge_snapshot as snapshot
from waggledance.core.bridge_log_reader import read_bridge_log

ROSTER = {"codex-lead-1": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
          "codex-tools-1": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
          "claude-rco-1": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
          "claude-rco-2": "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
          "fable-5": "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"}


def enc(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def digest(value):
    return hashlib.sha256(value).hexdigest()


def read_fixture(tmp_path, events, *, use_public=True, public_policy=None):
    path = tmp_path / "synthetic.jsonl"
    path.write_bytes(b"")
    gen = tmp_path / "generation.json"
    gen.write_bytes(enc({"generation": "synthetic-generation"}))
    start = read_bridge_log(path, generation_path=gen).candidate_cursor
    tasks = frozenset({"public-task", "nonreview-private"})
    policy = enc({"schema": "wd.q4-non-review-policy.v1", "approved_tasks": sorted(tasks)})
    roster = enc({"lanes": [{"agent": k, "agent_uuid": v} for k, v in ROSTER.items() if k != "codex-tools-1"],
                  "tools_supervisor": {"agent": "codex-tools-1", "agent_uuid": ROSTER["codex-tools-1"]}})
    public = enc({"schema": "wd.mind-public-status-policy.v1", "public_tasks": ["public-task"],
                  "public_types": ["message"]} if public_policy is None else public_policy)
    anchor = {"schema": "wd.q4-source-anchor.v1", "source_path": str(path), "generation_path": str(gen),
              "cursor_start": asdict(start), "policy_sha256": digest(policy), "roster_sha256": digest(roster)}
    if use_public:
        anchor.update(status_mode="public-v1", public_policy_sha256=digest(public))
    path.write_bytes(b"".join(enc(event) + b"\n" for event in events))
    kwargs = {"policy_bytes": policy, "policy_sha256": digest(policy), "roster_bytes": roster,
              "roster_sha256": digest(roster)}
    if use_public:
        kwargs.update(public_policy_bytes=public, public_policy_sha256=digest(public))
    result = snapshot.read_anchored_delta(path, start, tasks, anchor_bytes=enc(anchor),
        anchor_sha256=digest(enc(anchor)), generation_path=gen, max_bytes=65536, max_rows=100, **kwargs)
    pins = {"anchor_bytes": enc(anchor), "anchor_sha256": digest(enc(anchor)), **kwargs}
    return result, pins, path


def event(**changes):
    item = {"ts_utc": "2026-10-31T12:00:00Z", "agent": "codex-lead-1",
            "agent_uuid": ROSTER["codex-lead-1"], "type": "message", "status": "done",
            "task_id": "public-task", "to": "", "payload": {"public_status": True}}
    if "To" in changes:
        item.pop("to")
    item.update(changes)
    return item


@pytest.mark.parametrize("changed", [
    {"to": "codex-tools-1"}, {"To": "fable-5"}, {"to": ["codex-tools-1"]}, {"to": None},
    {"to": " "}, {"recipient": "codex-tools-1"}, {"recipients": ["fable-5"]},
    {"payload": {"public_status": True, "to": "fable-5"}},
    {"payload": {"public_status": True, "ReCiPiEnT": "codex-tools-1"}},
    {"payload": {"public_status": True, "nested": {"recipient": "fable-5"}}},
    {"payload": {"public_status": True, "to": None}},
    {"to": "", "recipient": "codex-tools-1"},
    {"Receiver": "fable-5"}, {"message": "synthetic private never printed"},
    {"paths": ["C:\\synthetic-private"]}, {"write_scope": ["private"]},
    {"payload": {"public_status": True, "path": "C:\\synthetic-private"}},
    {"task_id": "nonreview-private"}, {"payload": {}}, {"payload": {"public_status": 1}},
])
def test_addressed_ambiguous_or_private_task_never_projects_but_cursor_advances(tmp_path, changed):
    from sandbox.mind.step2 import status_metadata_producer as producer
    good = event(to="", message="")
    result, pins, path = read_fixture(tmp_path, [good, event(**changed)])
    output = producer.build_status(result, enabled=True, **pins)
    decoded = json.loads(output)
    assert len(decoded["records"]) == 1
    assert result["candidate_cursor"].offset == path.stat().st_size
    assert b"synthetic private never printed" not in output
    assert b"recipient" not in output and b"dropped_fields" not in output


def test_legacy_or_wrong_public_pin_refused(tmp_path):
    from sandbox.mind.step2 import status_metadata_producer as producer
    legacy, pins, _ = read_fixture(tmp_path, [event()], use_public=False)
    public = enc({"schema": "wd.mind-public-status-policy.v1", "public_tasks": ["public-task"],
                  "public_types": ["message"]})
    pins.update(public_policy_bytes=public, public_policy_sha256=digest(public))
    with pytest.raises(producer.StatusRefused, match="^status_refused$"):
        producer.build_status(legacy, enabled=True, **pins)


def test_forged_legacy_mode_cannot_publish_directed_event(tmp_path):
    from sandbox.mind.step2 import status_metadata_producer as producer
    legacy, pins, _ = read_fixture(tmp_path, [event(to="codex-tools-1")], use_public=False)
    public = enc({"schema": "wd.mind-public-status-policy.v1", "public_tasks": ["public-task"],
                  "public_types": ["message"]})
    pins.update(public_policy_bytes=public, public_policy_sha256=digest(public))
    legacy["_projection_binding"] = replace(legacy["_projection_binding"], status_mode="public-v1",
                                             public_policy_sha256=digest(public))
    with pytest.raises(snapshot.SnapshotRefused, match="^snapshot_refused$"):
        snapshot.verify_anchored_projection(legacy, legacy["projection"], expected_anchor_sha256=pins["anchor_sha256"],
            expected_policy_sha256=pins["policy_sha256"], expected_roster_sha256=pins["roster_sha256"],
            expected_mode="public-v1", expected_public_policy_sha256=digest(public),
            expected_anchor_bytes=pins["anchor_bytes"])
    with pytest.raises(producer.StatusRefused, match="^status_refused$"):
        producer.build_status(legacy, enabled=True, **pins)


def test_projection_requires_exact_public_anchor_policy_and_binding(tmp_path):
    from sandbox.mind.step2 import status_metadata_producer as producer
    result, pins, _ = read_fixture(tmp_path, [event()])
    for key in ("anchor_sha256", "public_policy_sha256"):
        changed = dict(pins, **{key: "0" * 64})
        with pytest.raises(producer.StatusRefused, match="^status_refused$"):
            producer.build_status(result, enabled=True, **changed)
    for value in (None, b"{}", pins["anchor_bytes"] + b" "):
        with pytest.raises(producer.StatusRefused, match="^status_refused$"):
            producer.build_status(result, enabled=True, **dict(pins, anchor_bytes=value))


@pytest.mark.parametrize("policy", [
    {},
    {"schema": "wrong", "public_tasks": ["public-task"], "public_types": ["message"]},
    {"schema": "wd.mind-public-status-policy.v1", "public_tasks": ["unknown"], "public_types": ["message"]},
    {"schema": "wd.mind-public-status-policy.v1", "public_tasks": ["public-task"], "public_types": ["wrong"]},
    {"schema": "wd.mind-public-status-policy.v1", "public_tasks": ["public-task", "public-task"],
     "public_types": ["message"]},
    {"schema": "wd.mind-public-status-policy.v1", "public_tasks": ["public-task"], "public_types": ["message"],
     "extra": True},
])
def test_public_policy_is_exact_nonempty_pinned_subset(tmp_path, policy):
    with pytest.raises(snapshot.SnapshotRefused, match="^snapshot_refused$"):
        read_fixture(tmp_path, [event()], public_policy=policy)


def test_unmarked_ordinary_events_yield_empty_public_status(tmp_path):
    from sandbox.mind.step2 import status_metadata_producer as producer
    result, pins, path = read_fixture(tmp_path, [event(payload={})])
    output = json.loads(producer.build_status(result, enabled=True, **pins))
    assert output["records"] == [] and result["candidate_cursor"].offset == path.stat().st_size


def test_missing_recipient_field_is_ambiguous_and_never_public(tmp_path):
    from sandbox.mind.step2 import status_metadata_producer as producer
    missing_to = event()
    missing_to.pop("to")
    result, pins, path = read_fixture(tmp_path, [missing_to])
    output = json.loads(producer.build_status(result, enabled=True, **pins))
    assert output["records"] == [] and result["candidate_cursor"].offset == path.stat().st_size
