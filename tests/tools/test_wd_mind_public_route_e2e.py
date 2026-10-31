"""Synthetic public-status publisher/session integration, never a live run.

The new reader is NOT connected to either installed or source agent starters.
"""
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json

import pytest

from sandbox.mind.step2 import shared_bridge_snapshot, status_metadata_producer
from tools import wd_bridge_mind_session
from waggledance.core.bridge_log_reader import read_bridge_log

ROSTER = {"codex-lead-1": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
          "codex-tools-1": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
          "claude-rco-1": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
          "claude-rco-2": "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
          "fable-5": "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"}


def enc(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def sha(data):
    return hashlib.sha256(data).hexdigest()


@pytest.mark.parametrize("lane", sorted(ROSTER))
def test_public_snapshot_reaches_each_declared_synthetic_lane_without_raw_data(tmp_path, monkeypatch, capsys, lane):
    audit = tmp_path / ".codex-audit"
    bridge = audit / "synthetic-bridge" / "shared"
    bridge.mkdir(parents=True)
    log, generation = bridge / "events.jsonl", bridge / "events.generation.json"
    log.write_bytes(b"")
    generation.write_bytes(enc({"generation": "synthetic-generation"}))
    start = read_bridge_log(log, generation_path=generation).candidate_cursor
    tasks = frozenset({"public-synthetic-task"})
    policy = enc({"schema": "wd.q4-non-review-policy.v1", "approved_tasks": sorted(tasks)})
    roster = enc({"lanes": [{"agent": k, "agent_uuid": v} for k, v in ROSTER.items() if k != "codex-tools-1"],
                  "tools_supervisor": {"agent": "codex-tools-1", "agent_uuid": ROSTER["codex-tools-1"]}})
    public = enc({"schema": "wd.mind-public-status-policy.v1", "public_tasks": sorted(tasks),
                  "public_types": ["message"]})
    anchor = enc({"schema": "wd.q4-source-anchor.v1", "source_path": str(log), "generation_path": str(generation),
                  "cursor_start": asdict(start), "policy_sha256": sha(policy), "roster_sha256": sha(roster),
                  "status_mode": "public-v1", "public_policy_sha256": sha(public)})
    row = {"ts_utc": datetime.now(timezone.utc).isoformat(), "agent": "codex-lead-1",
           "agent_uuid": ROSTER["codex-lead-1"], "type": "message", "task_id": "public-synthetic-task",
           "status": "done", "severity": "info", "to": "", "message": "", "paths": [], "write_scope": [],
           "run_id": "synthetic-run", "pid": 123, "cwd": "C:\\synthetic-only", "role": "lead",
           "session_id": "synthetic-session", "capabilities": ["implementation", "tests"],
           "payload": {"public_status": True}}
    private = dict(row, to="fable-5", message="synthetic-private-marker")
    log.write_bytes(enc(row) + b"\n" + enc(private) + b"\n")
    result = shared_bridge_snapshot.read_anchored_delta(log, start, tasks, anchor_bytes=anchor,
        anchor_sha256=sha(anchor), generation_path=generation, max_bytes=65536, max_rows=20,
        policy_bytes=policy, policy_sha256=sha(policy), roster_bytes=roster, roster_sha256=sha(roster),
        public_policy_bytes=public, public_policy_sha256=sha(public))
    data = status_metadata_producer.build_status(result, enabled=True, anchor_bytes=anchor, anchor_sha256=sha(anchor),
        policy_bytes=policy, policy_sha256=sha(policy), roster_bytes=roster, roster_sha256=sha(roster),
        public_policy_bytes=public, public_policy_sha256=sha(public))
    assert len(json.loads(data)["records"]) == 1
    assert b"synthetic-private-marker" not in data and b"synthetic-only" not in data

    # Actual new publisher and session reader. No fake legacy pointer and no
    # synthetic replacement of either component or the bound source.
    from tools import wd_mind_public_session as session
    instant = datetime.now(timezone.utc)
    pins = dict(anchor_bytes=anchor, anchor_sha256=sha(anchor), policy_bytes=policy, policy_sha256=sha(policy),
                roster_bytes=roster, roster_sha256=sha(roster), public_policy_bytes=public,
                public_policy_sha256=sha(public))
    session.publish_status(audit, result, as_of=instant, enabled=True, **pins)
    fleet = audit / "synthetic-fleet.json"
    fleet.write_bytes(roster)
    monkeypatch.setenv("WD_MIND_READ_ENABLED", "1")
    monkeypatch.setenv("AGENT_BRIDGE_RUNTIME_ROOT", str(bridge.parent))
    monkeypatch.setenv("AGENT_BRIDGE_AGENT", lane)
    monkeypatch.setenv("AGENT_BRIDGE_AGENT_UUID", ROSTER[lane])
    legacy_args = ["--latest", str(audit / "public-status.json"), "--audit-root", str(audit),
                   "--fleet", str(fleet), "--fleet-sha256", sha(roster)]
    assert wd_bridge_mind_session.main(legacy_args) == 2
    legacy = capsys.readouterr()
    assert legacy.out == "" and json.loads(legacy.err)["error"] == "pointer_invalid"
    parsed = json.loads(session.read_status(audit, lane=lane, agent_uuid=ROSTER[lane], as_of=instant,
                                           max_age_seconds=900, enabled=True, **pins))
    assert parsed["lane"] == lane and parsed["authority"] == "none"
    assert parsed["identity_verification"] == "caller_declared_not_authenticated"
    assert parsed["source_freshness"] == "UNKNOWN"
    assert parsed["records"] == json.loads(data)["records"]
    assert "synthetic-private-marker" not in json.dumps(parsed) and "synthetic-only" not in json.dumps(parsed)
    output = capsys.readouterr()
    assert output.out == "" and output.err == ""
