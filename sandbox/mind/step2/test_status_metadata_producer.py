"""Synthetic in-memory metadata boundary; no live bridge or publication."""
from dataclasses import asdict, replace
import copy
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
TASKS = frozenset({"synthetic-task"})
PRIVATE = "fake-secret-COLLECTOR-98"
PRIVATE_PATH = "C:\\Users\\synthetic\\private-folder\\secret.txt"


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def sha(value):
    return hashlib.sha256(value).hexdigest()


@pytest.fixture
def bound(tmp_path):
    path = tmp_path / "synthetic.jsonl"
    path.write_bytes(b"")
    generation = tmp_path / "generation.json"
    generation.write_bytes(encode({"generation": "synthetic-generation"}))
    cursor = read_bridge_log(path, generation_path=generation).candidate_cursor
    policy = encode({"schema": "wd.q4-non-review-policy.v1", "approved_tasks": sorted(TASKS)})
    roster = encode({"lanes": [{"agent": name, "agent_uuid": uuid} for name, uuid in ROSTER.items()
                               if name != "codex-tools-1"],
                     "tools_supervisor": {"agent": "codex-tools-1", "agent_uuid": ROSTER["codex-tools-1"]}})
    public = encode({"schema": "wd.mind-public-status-policy.v1", "public_tasks": sorted(TASKS),
                     "public_types": ["message"]})
    anchor = encode({"schema": "wd.q4-source-anchor.v1", "source_path": str(path),
                     "generation_path": str(generation), "cursor_start": asdict(cursor),
                     "policy_sha256": sha(policy), "roster_sha256": sha(roster),
                     "status_mode": "public-v1", "public_policy_sha256": sha(public)})
    event = {"ts_utc": "2026-10-31T12:00:00Z", "agent": "codex-lead-1",
             "agent_uuid": ROSTER["codex-lead-1"], "type": "message", "status": "done",
             "task_id": "synthetic-task", "to": "", "message": PRIVATE,
             "payload": {"path": PRIVATE_PATH, "public_status": True}}
    rco_event = dict(event, agent="claude-rco-1", agent_uuid=ROSTER["claude-rco-1"], message=PRIVATE)
    public_event = dict(event, message="", payload={"public_status": True})
    path.write_bytes(encode(event) + b"\n" + encode(rco_event) + b"\n" + encode(public_event) + b"\n")
    result = snapshot.read_anchored_delta(path, cursor, TASKS, anchor_bytes=anchor, anchor_sha256=sha(anchor),
        generation_path=generation, max_bytes=4096, max_rows=20, policy_bytes=policy, policy_sha256=sha(policy),
        roster_bytes=roster, roster_sha256=sha(roster), public_policy_bytes=public, public_policy_sha256=sha(public))
    pins = {"anchor_bytes": anchor, "anchor_sha256": sha(anchor), "policy_bytes": policy, "policy_sha256": sha(policy),
            "roster_bytes": roster, "roster_sha256": sha(roster),
            "public_policy_bytes": public, "public_policy_sha256": sha(public)}
    return result, pins


def test_default_off_and_metadata_only_no_file_or_console_output(bound, tmp_path, capsys):
    from sandbox.mind.step2 import status_metadata_producer as producer
    result, pins = bound
    with pytest.raises(producer.StatusRefused, match="^status_refused$"):
        producer.build_status(result, **pins)
    before = {p.relative_to(tmp_path) for p in tmp_path.rglob("*")}
    output = producer.build_status(result, enabled=True, **pins)
    assert type(output) is bytes
    assert PRIVATE.encode() not in output and PRIVATE_PATH.encode() not in output
    assert b"private-folder" not in output and b'"message":' not in output
    assert b'"type":"message"' in output
    assert b"payload" not in output and b"source_path" not in output and b"cursor" not in output
    assert b"sha256" not in output and b"dropped_fields" not in output
    decoded = json.loads(output)
    assert decoded["authority"] == "none" and decoded["records"][0]["task_id"] == "synthetic-task"
    assert len(decoded["records"]) == 1 and b"claude-rco-1" not in output
    assert {p.relative_to(tmp_path) for p in tmp_path.rglob("*")} == before
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


def test_fixed_failure_does_not_echo_untrusted_values(bound, capsys):
    from sandbox.mind.step2 import status_metadata_producer as producer
    result, pins = bound
    pins = dict(pins, anchor_sha256=PRIVATE)
    with pytest.raises(producer.StatusRefused) as failure:
        producer.build_status(result, enabled=True, **pins)
    assert str(failure.value) == "status_refused"
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


@pytest.mark.parametrize("mutation", ["anchor", "policy", "roster", "source", "cursor", "projection", "enabled"])
def test_bound_metadata_refuses_splicing_unpinned_roster_and_non_boolean_enable(bound, mutation):
    from sandbox.mind.step2 import status_metadata_producer as producer
    result, pins = bound
    result, pins = copy.deepcopy(result), dict(pins)
    enabled = True
    if mutation in ("anchor", "policy", "roster"):
        pins[mutation + "_sha256"] = "0" * 64
    elif mutation == "source":
        result["source"]["cursor_end_sha256"] = "0" * 64
    elif mutation == "cursor":
        result["candidate_cursor"] = replace(result["candidate_cursor"], offset=result["candidate_cursor"].offset + 1)
    elif mutation == "projection":
        result["projection"]["records"][0]["meta"]["task_id"] = "other-task"
    else:
        enabled = 1
    with pytest.raises(producer.StatusRefused, match="^status_refused$"):
        producer.build_status(result, enabled=enabled, **pins)
