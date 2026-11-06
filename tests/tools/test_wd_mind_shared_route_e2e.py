"""Synthetic bridge-status route; not semantic Q4 and not a production-use test.

Only the bridge reader transport and PR metadata lookup are substituted. The
producer cycle, shadow publication, committed pointer, session entrypoint and
lane projection are the actual source functions. All files are synthetic.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from types import SimpleNamespace

import pytest

from sandbox.mind import mind_capture_producer as producer
from tools import bridge_compact_view, bridge_mind_workspace, wd_bridge_mind, wd_bridge_mind_lane_read
from tools import wd_bridge_mind_session, wd_bridge_mind_shadow
from waggledance.core.bridge_log_reader import parse_bridge_json_object

ROSTER = {
    "codex-lead-1": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    "codex-tools-1": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    "claude-rco-1": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
    "claude-rco-2": "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
    "fable-5": "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
}


def test_synthetic_published_route_five_sessions_refuses_stale_and_rotated(tmp_path, monkeypatch, capsys):
    audit = tmp_path / ".codex-audit"
    live = audit / "mind-live"
    (live / "cycles").mkdir(parents=True)
    (live / "files-cache").mkdir()
    bridge = audit / "synthetic-bridge" / "shared"
    bridge.mkdir(parents=True)
    now = datetime.now(timezone.utc)
    event = {"agent": "codex-lead-1", "agent_uuid": ROSTER["codex-lead-1"],
             "type": "message", "status": "milestone", "task_id": "synthetic-task",
             "ts_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "message": "synthetic only"}
    log = bridge / "events.jsonl"
    log.write_text(json.dumps(event) + "\n", encoding="utf-8")
    generation = bridge / "events.generation.json"
    generation.write_text('{"generation":"synthetic-g1"}', encoding="utf-8")
    _, cursor, _ = bridge_compact_view._read_view_rows(log, 1, "")
    ref = bridge_compact_view.event_id(event)
    row = {"event": event, "ref": ref, "row_sha256": producer.row_digest(event), "source": "detail"}
    for key, value in {"core": wd_bridge_mind, "shadow": wd_bridge_mind_shadow,
                       "ws": bridge_mind_workspace, "event_id": bridge_compact_view.event_id,
                       "parse_bridge_json_object": parse_bridge_json_object,
                       "live": live, "audit_root": audit,
                       "args": SimpleNamespace(window=50, keep=2)}.items():
        monkeypatch.setattr(producer, key, value, raising=False)
    monkeypatch.setattr(producer, "compact_list", lambda *args: ([ref], cursor, {}))
    monkeypatch.setattr(producer, "details", lambda *args: [row])
    monkeypatch.setattr(producer, "pr_context", lambda *args: (None, {"ms": 0, "status": "UNKNOWN"}))
    monkeypatch.setattr(producer.OutputBudget, "allowance", 16 * 1024 * 1024)
    monkeypatch.setattr(producer.OutputBudget, "exhausted", False)
    committed = producer.one_cycle(1, None, {})
    capsys.readouterr()
    assert committed is not None
    latest = live / "latest.json"
    assert json.loads(latest.read_text(encoding="utf-8"))["last_ok"] == committed

    fleet = audit / "synthetic-fleet.json"
    fleet.write_text(json.dumps({"lanes": [{"agent": k, "agent_uuid": v} for k, v in ROSTER.items()
                                              if k != "codex-tools-1"],
                                "tools_supervisor": {"agent": "codex-tools-1",
                                                     "agent_uuid": ROSTER["codex-tools-1"]}}), encoding="utf-8")
    fleet_hash = hashlib.sha256(fleet.read_bytes()).hexdigest()
    args = ["--latest", str(latest), "--audit-root", str(audit), "--fleet", str(fleet),
            "--fleet-sha256", fleet_hash]
    monkeypatch.setenv("WD_MIND_READ_ENABLED", "1")
    monkeypatch.setenv("AGENT_BRIDGE_RUNTIME_ROOT", str(bridge.parent))
    for lane, uuid in ROSTER.items():
        monkeypatch.setenv("AGENT_BRIDGE_AGENT", lane)
        monkeypatch.setenv("AGENT_BRIDGE_AGENT_UUID", uuid)
        assert wd_bridge_mind_session.main(args) == 0
        output = capsys.readouterr()
        assert output.err == ""
        result = json.loads(output.out)
        assert result["lane"] == lane and result["authority"] == "none"
        assert result["freshness"]["status"] == "fresh"
        assert "synthetic only" not in output.out and uuid not in output.out

    view = {"schema": wd_bridge_mind_lane_read.VIEW_SCHEMA, "authority": "none", "cursor": cursor}
    with pytest.raises(wd_bridge_mind_lane_read.LaneReadRefused, match="snapshot_stale"):
        wd_bridge_mind_lane_read.lane_read(latest_path=latest, audit_root=audit, lane="codex-lead-1",
            as_of=now + timedelta(minutes=16), binding=view, roster=ROSTER)
    generation.write_text('{"generation":"synthetic-g2"}', encoding="utf-8")
    assert wd_bridge_mind_session.main(args) == 2
    output = capsys.readouterr()
    assert output.out == "" and "log_identity_mismatch" in output.err
