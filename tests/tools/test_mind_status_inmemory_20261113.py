"""Synthetic tests of the read-only status fallback; never access the live bridge."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

from tools import wd_bridge_mind_session as session
from test_wd_bridge_mind_direct_baseline import publication


UUIDS = {
    "codex-lead-1": "11111111-1111-4111-8111-111111111111",
    "codex-tools-1": "22222222-2222-4222-8222-222222222222",
    "claude-rco-1": "33333333-3333-4333-8333-333333333333",
    "claude-rco-2": "44444444-4444-4444-8444-444444444444",
    "fable-5": "55555555-5555-4555-8555-555555555555",
}
PRIVATE = "synthetic-private-ignore-previous-instructions-password"


def event_ref(event):
    raw = json.dumps(event, sort_keys=True, ensure_ascii=False,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    shared = tmp_path / "bridge" / "shared"
    shared.mkdir(parents=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    events = [{"agent": agent, "agent_uuid": uuid, "type": "heartbeat", "ts_utc": now,
               "session_id": "synthetic", "run_id": "synthetic", "task_id": "synthetic",
               "message": PRIVATE, "payload": {"secret": PRIVATE}}
              for agent, uuid in UUIDS.items()]
    (shared / "events.jsonl").write_text("".join(json.dumps(x) + "\n" for x in events), encoding="utf-8")
    fleet = tmp_path / "fleet.json"
    fleet.write_text(json.dumps({"lanes": [{"agent": a, "agent_uuid": u} for a, u in UUIDS.items()
                                           if a != "codex-tools-1"],
                                 "tools_supervisor": {"agent": "codex-tools-1",
                                                      "agent_uuid": UUIDS["codex-tools-1"]}}), encoding="utf-8")
    audit = tmp_path / ".codex-audit"
    audit.mkdir()
    latest = audit / "latest.json"  # Deliberately absent; no producer or generation file.
    digest = hashlib.sha256(fleet.read_bytes()).hexdigest()
    monkeypatch.setenv("WD_MIND_READ_ENABLED", "1")
    monkeypatch.setenv("AGENT_BRIDGE_RUNTIME_ROOT", str(shared.parent))
    args = ["--latest", str(latest), "--audit-root", str(audit), "--fleet", str(fleet), "--fleet-sha256", digest]
    return args, latest, digest, events


def identify(monkeypatch, agent):
    monkeypatch.setenv("AGENT_BRIDGE_AGENT", agent)
    monkeypatch.setenv("AGENT_BRIDGE_AGENT_UUID", UUIDS[agent])


@pytest.mark.parametrize("agent", list(UUIDS))
def test_absent_latest_projects_bound_heartbeat_without_private_content(inputs, monkeypatch, capsys, agent):
    args, latest, digest, events = inputs
    identify(monkeypatch, agent)
    assert session.main(args) == 0
    output = capsys.readouterr()
    body = json.loads(output.out)
    assert set(body) == {"schema", "authority", "scope", "lane", "fleet_sha256", "coverage",
                         "generation", "continuity", "sender_authentication", "as_of_utc",
                         "observation_status", "facts"}
    assert body["schema"] == "wd.mind-direct-status.v1"
    assert body["authority"] == "none" and body["scope"] == "bounded_bridge_status_not_gate"
    assert body["lane"] == agent and body["fleet_sha256"] == digest
    assert body["coverage"] == "bounded_tail"
    assert body["generation"] == body["continuity"] == "UNKNOWN"
    assert body["sender_authentication"] == "UNKNOWN"
    as_of = datetime.strptime(body["as_of_utc"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    assert as_of.strftime("%Y-%m-%dT%H:%M:%S.%fZ") == body["as_of_utc"]
    assert body["observation_status"] == "OBSERVED"
    expected = {row["agent"]: row for row in events}
    assert isinstance(body["facts"], list)
    assert any(fact["agent"] == agent for fact in body["facts"])
    for fact in body["facts"]:
        assert set(fact) == {"agent", "event_type", "observed_utc", "event_ref", "age_seconds"}
        assert fact["agent"] == agent
        row = expected[fact["agent"]]
        assert fact["event_type"] == "heartbeat" and fact["observed_utc"] == row["ts_utc"]
        assert fact["event_ref"] == event_ref(row)
        observed = datetime.fromisoformat(fact["observed_utc"].replace("Z", "+00:00"))
        assert type(fact["age_seconds"]) is int and fact["age_seconds"] >= 0
        assert abs(fact["age_seconds"] - (as_of - observed).total_seconds()) <= 1
    assert PRIVATE not in output.out + output.err
    assert not latest.exists()


def test_present_corrupt_latest_does_not_fall_back(inputs, monkeypatch, capsys):
    args, latest, _, _ = inputs
    latest.write_text("{broken", encoding="utf-8")
    identify(monkeypatch, "codex-lead-1")
    assert session.main(args) != 0
    output = capsys.readouterr()
    assert not output.out and PRIVATE not in output.err


def test_unbound_heartbeat_and_nonheartbeat_do_not_become_facts(inputs, monkeypatch, capsys):
    args, latest, _, events = inputs
    identify(monkeypatch, "codex-lead-1")
    original = events[0]
    spoof = dict(original, agent_uuid=UUIDS["codex-tools-1"])
    nonheartbeat = dict(original, type="message")
    log = Path(session.os.environ["AGENT_BRIDGE_RUNTIME_ROOT"]) / "shared" / "events.jsonl"
    raw = (json.dumps(spoof) + "\n" + json.dumps(nonheartbeat) + "\n").encode("utf-8")
    log.write_bytes(raw)
    assert session.main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["facts"] == []
    assert result["observation_status"] == "UNKNOWN"
    assert result["sender_authentication"] == "UNKNOWN"
    assert log.read_bytes() == raw and not latest.exists()
    assert list(latest.parent.iterdir()) == []


@pytest.mark.parametrize("newest_type", ["heartbeat", "liveness"])
@pytest.mark.parametrize("recipient", ["missing", None, "", []])
def test_newest_bound_heartbeat_or_liveness_is_selected(inputs, monkeypatch, capsys, newest_type, recipient):
    args, latest, _, events = inputs
    identify(monkeypatch, "codex-lead-1")
    fixed_now = datetime(2026, 11, 13, 12, 0, 0, tzinfo=timezone.utc)
    older_type = "liveness" if newest_type == "heartbeat" else "heartbeat"
    older = dict(events[0], type=older_type, ts_utc="2026-11-13T11:59:40Z")
    newest = dict(events[0], type=newest_type, ts_utc="2026-11-13T11:59:50Z")
    spoof = dict(events[0], type="liveness", ts_utc="2026-11-13T11:59:59Z",
                 agent_uuid=UUIDS["codex-tools-1"])
    for row in (older, newest, spoof):
        if row["type"] == "liveness":
            row["task_id"] = "codex-lead-1-session-bootstrap-2026-11-13"
            if recipient != "missing":
                row["to"] = recipient
    unrelated = dict(events[0], type="message", ts_utc="2026-11-13T11:59:59Z")
    log = Path(session.os.environ["AGENT_BRIDGE_RUNTIME_ROOT"]) / "shared" / "events.jsonl"
    # Older is deliberately after newer: selection must use timestamp, not log order.
    log.write_text("".join(json.dumps(row) + "\n" for row in (newest, spoof, unrelated, older)), encoding="utf-8")

    class FixedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    monkeypatch.setattr(session, "datetime", FixedClock)
    assert session.main(args) == 0
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["observation_status"] == "OBSERVED" and len(result["facts"]) == 1
    fact = result["facts"][0]
    assert fact["agent"] == "codex-lead-1" and fact["event_type"] == newest_type
    assert fact["observed_utc"] == newest["ts_utc"] and fact["age_seconds"] == 10
    assert fact["event_ref"] == event_ref(newest)
    assert PRIVATE not in captured.out + captured.err and not latest.exists()


@pytest.mark.parametrize("invalid", ["other_task", "other_lane_bootstrap", "addressed"])
def test_nonbootstrap_or_addressed_liveness_is_not_selected(inputs, monkeypatch, capsys, invalid):
    args, _, _, events = inputs
    identify(monkeypatch, "codex-lead-1")
    heartbeat = dict(events[0], type="heartbeat", ts_utc="2026-11-13T11:59:40Z")
    liveness = dict(events[0], type="liveness", ts_utc="2026-11-13T11:59:59Z",
                    task_id="codex-lead-1-session-bootstrap-2026-11-13")
    if invalid == "other_task":
        liveness["task_id"] = "codex-lead-1/some-other-task"
    elif invalid == "other_lane_bootstrap":
        liveness["task_id"] = "codex-tools-1-session-bootstrap-2026-11-13"
    else:
        liveness["to"] = "codex-tools-1"
    log = Path(session.os.environ["AGENT_BRIDGE_RUNTIME_ROOT"]) / "shared" / "events.jsonl"
    log.write_text(json.dumps(heartbeat) + "\n" + json.dumps(liveness) + "\n", encoding="utf-8")

    class FixedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 13, 12, 0, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(session, "datetime", FixedClock)
    assert session.main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert len(result["facts"]) == 1
    assert result["facts"][0]["event_type"] == "heartbeat"
    assert result["facts"][0]["event_ref"] == event_ref(heartbeat)
    assert event_ref(liveness) != result["facts"][0]["event_ref"]


@pytest.mark.parametrize("recipient", ["codex-tools-1", ["codex-tools-1"]])
def test_addressed_heartbeat_does_not_replace_undirected_observation(inputs, monkeypatch, capsys, recipient):
    args, _, _, events = inputs
    identify(monkeypatch, "codex-lead-1")
    undirected = dict(events[0], type="heartbeat", ts_utc="2026-11-13T11:59:40Z")
    addressed = dict(events[0], type="heartbeat", ts_utc="2026-11-13T11:59:59Z", to=recipient)
    log = Path(session.os.environ["AGENT_BRIDGE_RUNTIME_ROOT"]) / "shared" / "events.jsonl"
    log.write_text(json.dumps(undirected) + "\n" + json.dumps(addressed) + "\n", encoding="utf-8")

    class FixedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 13, 12, 0, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(session, "datetime", FixedClock)
    assert session.main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert len(result["facts"]) == 1
    assert result["facts"][0]["event_type"] == "heartbeat"
    assert result["facts"][0]["event_ref"] == event_ref(undirected)
    assert event_ref(addressed) != result["facts"][0]["event_ref"]


@pytest.mark.parametrize(("age", "status"), [(0.5, "OBSERVED"), (900, "OBSERVED"),
                                             (900.5, "UNKNOWN"), (901, "UNKNOWN"), (3600, "UNKNOWN")])
def test_observation_status_tracks_heartbeat_age(inputs, monkeypatch, capsys, age, status):
    args, _, _, events = inputs
    identify(monkeypatch, "codex-lead-1")
    fixed_now = datetime(2026, 11, 13, 12, 0, 0, 750000, tzinfo=timezone.utc)
    row = dict(events[0], ts_utc=(fixed_now - timedelta(seconds=age)).strftime("%Y-%m-%dT%H:%M:%S.%fZ"))
    log = Path(session.os.environ["AGENT_BRIDGE_RUNTIME_ROOT"]) / "shared" / "events.jsonl"
    log.write_text(json.dumps(row) + "\n", encoding="utf-8")

    class FixedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now if tz else fixed_now.replace(tzinfo=None)

    monkeypatch.setattr(session, "datetime", FixedClock)
    assert session.main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["as_of_utc"] == "2026-11-13T12:00:00.750000Z"
    assert result["observation_status"] == status
    assert len(result["facts"]) == 1
    assert result["facts"][0]["event_ref"] == event_ref(row)
    assert abs(result["facts"][0]["age_seconds"] - age) <= 1


def test_as_of_clock_is_taken_after_bounded_log_capture(inputs, monkeypatch, capsys):
    args, _, _, _ = inputs
    identify(monkeypatch, "codex-lead-1")
    captured = []
    original = session.bridge_compact_view._read_view_rows

    def capture(*a, **kw):
        result = original(*a, **kw)
        captured.append(True)
        return result

    class FixedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            assert captured, "as_of must be obtained after the bridge capture"
            return datetime(2030, 1, 2, 3, 4, 5, 678901, tzinfo=timezone.utc)

    monkeypatch.setattr(session.bridge_compact_view, "_read_view_rows", capture)
    monkeypatch.setattr(session, "datetime", FixedClock)
    assert session.main(args) == 0
    assert json.loads(capsys.readouterr().out)["as_of_utc"] == "2030-01-02T03:04:05.678901Z"


@pytest.mark.parametrize(("observed", "status"), [
    ("2026-11-13T11:45:00.7500000Z", "OBSERVED"),
    ("2026-11-13T11:45:00.7499999Z", "UNKNOWN"),
])
def test_seventh_fraction_digit_at_observation_boundary(inputs, monkeypatch, capsys, observed, status):
    args, _, _, events = inputs
    identify(monkeypatch, "codex-lead-1")
    row = dict(events[0], ts_utc=observed)
    log = Path(session.os.environ["AGENT_BRIDGE_RUNTIME_ROOT"]) / "shared" / "events.jsonl"
    log.write_text(json.dumps(row) + "\n", encoding="utf-8")

    class FixedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 13, 12, 0, 0, 750000, tzinfo=timezone.utc)

    monkeypatch.setattr(session, "datetime", FixedClock)
    assert session.main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["observation_status"] == status
    assert result["facts"][0]["observed_utc"] == observed
    assert result["facts"][0]["event_ref"] == event_ref(row)
    assert result["facts"][0]["age_seconds"] == 900


def test_latest_access_failure_is_not_treated_as_absence(inputs, monkeypatch, capsys):
    args, latest, _, _ = inputs
    latest.write_text("{}", encoding="utf-8")
    identify(monkeypatch, "codex-lead-1")
    original_open, original_stat = Path.open, Path.stat

    def denied_open(path, *a, **kw):
        if path == latest:
            raise PermissionError("synthetic access denied")
        return original_open(path, *a, **kw)

    def denied_stat(path, *a, **kw):
        if path == latest:
            raise PermissionError("synthetic access denied")
        return original_stat(path, *a, **kw)

    monkeypatch.setattr(Path, "open", denied_open)
    monkeypatch.setattr(Path, "stat", denied_stat)
    assert session.main(args) != 0
    output = capsys.readouterr()
    assert not output.out and "synthetic access denied" not in output.err


@pytest.mark.parametrize("fault", ["wrong_root_name", "outside_root", "reparse_root", "reparse_intermediate"])
def test_fallback_refuses_invalid_audit_boundary(inputs, monkeypatch, capsys, fault):
    args, latest, _, _ = inputs
    identify(monkeypatch, "codex-lead-1")
    if fault == "wrong_root_name":
        bad = latest.parent.parent / "audit"
        bad.mkdir()
        args[1], args[3] = str(bad / "latest.json"), str(bad)
    elif fault == "outside_root":
        args[1] = str(latest.parent.parent / "latest.json")
    else:
        component = latest.parent
        if fault == "reparse_intermediate":
            component = latest.parent / "nested"
            component.mkdir()
            args[1] = str(component / "latest.json")
        original = Path.lstat

        def fake_reparse(path, *a, **kw):
            if path == component:
                return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
            return original(path, *a, **kw)

        monkeypatch.setattr(Path, "lstat", fake_reparse)
    assert session.main(args) != 0
    assert not capsys.readouterr().out


def test_pointer_appearing_during_capture_refuses_projection(inputs, monkeypatch, capsys):
    args, latest, _, _ = inputs
    identify(monkeypatch, "codex-lead-1")
    original = session.bridge_compact_view._read_view_rows

    def race(*a, **kw):
        captured = original(*a, **kw)
        latest.write_text("{}", encoding="utf-8")
        return captured

    monkeypatch.setattr(session.bridge_compact_view, "_read_view_rows", race)
    assert session.main(args) != 0
    assert not capsys.readouterr().out


@pytest.mark.parametrize("fault", ["fleet_hash", "identity"])
def test_absent_latest_does_not_bypass_fleet_binding(inputs, monkeypatch, capsys, fault):
    args, _, _, _ = inputs
    identify(monkeypatch, "codex-lead-1")
    if fault == "fleet_hash":
        args[-1] = "0" * 64
    else:
        monkeypatch.setenv("AGENT_BRIDGE_AGENT_UUID", UUIDS["codex-tools-1"])
    assert session.main(args) != 0
    assert not capsys.readouterr().out


def test_verified_publication_includes_bound_status_and_event_refs(publication, monkeypatch, capsys):
    args, shared, _ = publication
    identify(monkeypatch, "codex-lead-1")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    row = {"agent": "codex-lead-1", "agent_uuid": UUIDS["codex-lead-1"], "type": "heartbeat",
           "ts_utc": now, "session_id": "synthetic", "run_id": "synthetic", "task_id": "synthetic",
           "message": PRIVATE, "payload": {}}
    unrelated = json.loads((shared / "events.jsonl").read_text(encoding="utf-8").strip())
    with (shared / "events.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row) + "\n")
    assert session.main(args) == 0
    output = capsys.readouterr()
    view = json.loads(output.out)
    assert view["schema"] == "wd.bridge-mind-lane-read.v1"
    assert view["fleet_sha256"] == args[-1]
    assert view["status_observation"]["fleet_sha256"] == args[-1]
    assert view["status_observation"]["observation_status"] == "OBSERVED"
    assert view["source_event_refs"] == [event_ref(row)]
    assert [fact["event_ref"] for fact in view["status_observation"]["facts"]] == view["source_event_refs"]
    assert event_ref(unrelated) not in view["source_event_refs"]
    assert PRIVATE not in output.out + output.err


def test_corrupt_publication_still_refuses_with_available_status(publication, monkeypatch, capsys):
    args, _, publication_path = publication
    identify(monkeypatch, "codex-lead-1")
    publication_path.write_bytes(b'{"schema":"corrupted"}')
    assert session.main(args) != 0
    assert not capsys.readouterr().out
