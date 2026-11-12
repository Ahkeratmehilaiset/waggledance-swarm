"""Synthetic direct-session checks against main's unchanged bridge reader.

This is a bounded status projection, not evidence of semantic content recall.
No live agent, producer, materializer or bridge is invoked.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import base64
import hashlib
import json
from pathlib import Path

import pytest

from tools import bridge_compact_view, wd_bridge_mind as core
from tools import wd_bridge_mind_lane_read as lane
from tools import wd_bridge_mind_session as session


UUIDS = {
    "codex-lead-1": "11111111-1111-4111-8111-111111111111",
    "codex-tools-1": "22222222-2222-4222-8222-222222222222",
    "claude-rco-1": "33333333-3333-4333-8333-333333333333",
    "claude-rco-2": "44444444-4444-4444-8444-444444444444",
    "fable-5": "55555555-5555-4555-8555-555555555555",
}


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def publication(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # The fixture builds bytes itself. In particular no producer, materializer,
    # or production snapshot-writer entrypoint is called.
    monkeypatch.setattr(core, "write_snapshot", lambda *a, **k: pytest.fail("writer invoked"))
    root = tmp_path / "synthetic-bridge"
    shared = root / "shared"
    shared.mkdir(parents=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    event = {"agent": "codex-lead-1", "agent_uuid": UUIDS["codex-lead-1"],
             "session_id": "synthetic", "run_id": "synthetic", "type": "message",
             "status": "active", "task_id": "synthetic-task", "ts_utc": now,
             "message": "private-fixture-message", "payload": {}}
    log = shared / "events.jsonl"
    log.write_text(json.dumps(event, sort_keys=True) + "\n", encoding="utf-8")
    (shared / "events.generation.json").write_text('{"generation":"g1"}', encoding="ascii")
    _, cursor, _ = bridge_compact_view._read_view_rows(log, 1, "")
    cursor_body = cursor[4:]
    position = json.loads(base64.urlsafe_b64decode(cursor_body + "=" * (-len(cursor_body) % 4)))
    identity = position["file_identity"]
    source = {"log_identity": "capture-window:" + identity, "generation": "g1",
              "cursor_start": 0, "cursor_end": 1, "event_count": 1, "coverage": "complete"}
    state = core.full_rebuild([{"event": event, "ref": bridge_compact_view.event_id(event)}],
                              source=source, as_of_utc=now)
    snapshot = core.snapshot(state, as_of_utc=now)
    snapshot_sha = _sha(core.canonical_bytes(snapshot))
    envelope = {"schema": "wd.bridge-mind-publication.v1", "snapshot": snapshot,
                "snapshot_sha256": snapshot_sha}
    audit = tmp_path / ".codex-audit"
    cycle_name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    cycle = audit / "cycles" / cycle_name
    (cycle / "run").mkdir(parents=True)
    publication_path = cycle / "run" / "reflected.publication.json"
    publication_path.write_bytes(core.canonical_bytes(envelope))
    state_bytes = core.canonical_bytes({"schema": "wd.bridge-mind-producer-state.v1", "generation": 1,
                                        "cursor": cursor, "source": {"file_identity": identity, "log_generation": "g1"},
                                        "window_sha256": "0" * 64, "window": []})
    (cycle / "state.json").write_bytes(state_bytes)
    prefix = f"cycles/{cycle_name}/"
    last_ok = {"cycle_dir": cycle_name, "generation": 1, "capture_file": prefix + "capture.json",
               "capture_sha256": "0" * 64, "state_file": prefix + "state.json", "state_sha256": _sha(state_bytes),
               "publication_file": prefix + "run/reflected.publication.json", "snapshot_sha256": snapshot_sha,
               "board_file": prefix + "run/board.txt", "receipt_file": prefix + "run/receipt.json"}
    latest = audit / "latest.json"
    latest.write_text(json.dumps({"schema": "wd.bridge-mind-shadow-latest.v2", "authority": "none",
                                  "last_ok": last_ok}), encoding="ascii")
    fleet = tmp_path / "fleet.json"
    fleet_bytes = core.canonical_bytes({"lanes": [{"agent": name, "agent_uuid": uid} for name, uid in UUIDS.items()
                                                   if name != "codex-tools-1"],
                                       "tools_supervisor": {"agent": "codex-tools-1", "agent_uuid": UUIDS["codex-tools-1"]}})
    fleet.write_bytes(fleet_bytes)
    monkeypatch.setenv("WD_MIND_READ_ENABLED", "1")
    monkeypatch.setenv("AGENT_BRIDGE_RUNTIME_ROOT", str(root))
    args = ["--latest", str(latest), "--audit-root", str(audit), "--fleet", str(fleet), "--fleet-sha256", _sha(fleet_bytes)]
    return args, shared, publication_path


@pytest.mark.parametrize("agent", lane.LANES)
def test_direct_session_reads_synthetic_status_for_each_lane(publication, monkeypatch, capsys, agent):
    args, _, _ = publication
    monkeypatch.setenv("AGENT_BRIDGE_AGENT", agent)
    monkeypatch.setenv("AGENT_BRIDGE_AGENT_UUID", UUIDS[agent])
    assert session.main(args) == 0
    out = capsys.readouterr()
    body = json.loads(out.out)
    assert body["schema"] == lane.SCHEMA and body["lane"] == agent
    assert body["scope"] == "read_only_lane_view_not_gate"
    assert "private-fixture-message" not in out.out + out.err


def test_direct_session_refuses_identity_mismatch(publication, monkeypatch, capsys):
    args, _, _ = publication
    monkeypatch.setenv("AGENT_BRIDGE_AGENT", "codex-lead-1")
    monkeypatch.setenv("AGENT_BRIDGE_AGENT_UUID", UUIDS["codex-tools-1"])
    assert session.main(args) == 2
    captured = capsys.readouterr()
    assert not captured.out
    assert json.loads(captured.err)["authority"] == "none"


def test_direct_session_refuses_missing_generation(publication, monkeypatch, capsys):
    args, shared, _ = publication
    (shared / "events.generation.json").unlink()
    monkeypatch.setenv("AGENT_BRIDGE_AGENT", "codex-lead-1")
    monkeypatch.setenv("AGENT_BRIDGE_AGENT_UUID", UUIDS["codex-lead-1"])
    assert session.main(args) == 2
    captured = capsys.readouterr()
    assert not captured.out
    assert json.loads(captured.err)["error"] == "generation_unavailable"


def test_direct_session_refuses_corrupted_publication(publication, monkeypatch, capsys):
    args, _, publication_path = publication
    publication_path.write_bytes(b'{"schema":"corrupted"}')
    monkeypatch.setenv("AGENT_BRIDGE_AGENT", "codex-lead-1")
    monkeypatch.setenv("AGENT_BRIDGE_AGENT_UUID", UUIDS["codex-lead-1"])
    assert session.main(args) == 2
    captured = capsys.readouterr()
    assert not captured.out
    assert json.loads(captured.err)["authority"] == "none"


def test_direct_session_refuses_changed_log_generation(publication, monkeypatch, capsys):
    args, shared, _ = publication
    (shared / "events.generation.json").write_text('{"generation":"g2"}', encoding="ascii")
    monkeypatch.setenv("AGENT_BRIDGE_AGENT", "codex-lead-1")
    monkeypatch.setenv("AGENT_BRIDGE_AGENT_UUID", UUIDS["codex-lead-1"])
    assert session.main(args) == 2
    captured = capsys.readouterr()
    assert not captured.out
    assert json.loads(captured.err)["error"] == "log_identity_mismatch"


def test_direct_session_refuses_stale_snapshot(publication, monkeypatch, capsys):
    args, _, _ = publication
    monkeypatch.setenv("AGENT_BRIDGE_AGENT", "codex-lead-1")
    monkeypatch.setenv("AGENT_BRIDGE_AGENT_UUID", UUIDS["codex-lead-1"])

    class FutureClock:
        @classmethod
        def now(cls, tz):
            return datetime.now(tz) + timedelta(seconds=901)

    monkeypatch.setattr(session, "datetime", FutureClock)
    assert session.main(args) == 2
    captured = capsys.readouterr()
    assert not captured.out
    assert json.loads(captured.err)["error"] == "snapshot_stale"


def test_direct_session_refuses_wrong_fleet_digest(publication, monkeypatch, capsys):
    args, _, _ = publication
    monkeypatch.setenv("AGENT_BRIDGE_AGENT", "codex-lead-1")
    monkeypatch.setenv("AGENT_BRIDGE_AGENT_UUID", UUIDS["codex-lead-1"])
    args[-1] = "0" * 64
    assert session.main(args) == 2
    captured = capsys.readouterr()
    assert not captured.out
    assert json.loads(captured.err)["authority"] == "none"


def test_direct_session_disabled_never_returns_a_projection(publication, monkeypatch, capsys):
    args, _, _ = publication
    monkeypatch.setenv("WD_MIND_READ_ENABLED", "0")
    monkeypatch.setenv("AGENT_BRIDGE_AGENT", "codex-lead-1")
    monkeypatch.setenv("AGENT_BRIDGE_AGENT_UUID", UUIDS["codex-lead-1"])
    assert session.main(args) == 2
    captured = capsys.readouterr()
    assert not captured.out
    assert json.loads(captured.err)["authority"] == "none"
