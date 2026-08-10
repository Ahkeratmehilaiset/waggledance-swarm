# SPDX-License-Identifier: BUSL-1.1
"""Tests for tools/close_bridge_rco_request.py."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "close_bridge_rco_request.py"

sys.path.insert(0, str(ROOT))

import tools.close_bridge_rco_request as closer  # noqa: E402
from tools.close_bridge_rco_request import (  # noqa: E402
    CloseRcoError,
    _open_rco_for,
    close_bridge_rco_request,
    _read_events,
)
from tools.bridge_event_writer import _PortableTestBackend  # noqa: E402
from tools.idle_check import evaluate_idle_state  # noqa: E402


def _seed_bridge(tmp_path: Path, events: list[dict]) -> Path:
    bridge_root = tmp_path / ".agent-bridge"
    shared = bridge_root / "shared"
    claims = bridge_root / "work_queue" / "claims"
    shared.mkdir(parents=True)
    claims.mkdir(parents=True)
    events_path = shared / "events.jsonl"
    with events_path.open("w", encoding="utf-8", newline="\n") as fh:
        for event in events:
            fh.write(json.dumps(event) + "\n")
    return bridge_root


def _seed_bridge_raw(tmp_path: Path, lines: list[str]) -> Path:
    bridge_root = tmp_path / ".agent-bridge"
    shared = bridge_root / "shared"
    claims = bridge_root / "work_queue" / "claims"
    shared.mkdir(parents=True)
    claims.mkdir(parents=True)
    (shared / "events.jsonl").write_text("".join(lines), encoding="utf-8")
    return bridge_root


def test_read_events_skips_bare_null_event_line(tmp_path: Path) -> None:
    bridge_root = _seed_bridge_raw(
        tmp_path,
        [
            "null\n",
            json.dumps(_opening_handoff("task-1", "2026-05-20T18:00:00Z")) + "\n",
        ],
    )

    assert _read_events(bridge_root / "shared" / "events.jsonl") == [
        _opening_handoff("task-1", "2026-05-20T18:00:00Z")
    ]


def _opening_handoff(
    task_id: str,
    ts: str,
    agent: str = "claude",
    to_agent: str = "codex",
) -> dict:
    return {
        "ts_utc": ts,
        "agent": agent,
        "type": "handoff",
        "task_id": task_id,
        "status": "rco_requested",
        "severity": "",
        "to": to_agent,
        "message": f"Please RCO PR #999 task {task_id}",
        "paths": [],
        "write_scope": [],
        "run_id": "",
        "pid": 0,
        "cwd": "",
    }


def _rco_follow_up(
    *,
    agent: str,
    event_type: str,
    status: str,
    ts: str = "2026-05-20T18:01:00Z",
) -> dict:
    return {
        "ts_utc": ts,
        "agent": agent,
        "type": event_type,
        "task_id": "canonical-rco-closure",
        "status": status,
        "severity": "",
        "to": "codex" if agent != "codex" else "claude",
        "message": "RCO follow-up",
        "paths": [],
        "write_scope": [],
        "run_id": "",
        "pid": 0,
        "cwd": "",
    }


@pytest.mark.parametrize(
    ("agent", "event_type", "status", "expected_open"),
    [
        ("claude", "done", "ack", True),
        ("claude", "done", "request", True),
        ("claude", "decision", "request", True),
        ("fable", "done", "merged", True),
        ("codex", "status", "superseded", False),
        ("claude", "message", "late_status_response", False),
        ("claude", "review_verdict", "independent_pass", False),
    ],
)
def test_open_rco_uses_canonical_target_and_requester_closure_rules(
    agent: str,
    event_type: str,
    status: str,
    expected_open: bool,
) -> None:
    opening = _opening_handoff(
        "canonical-rco-closure",
        "2026-05-20T18:00:00Z",
        agent="codex",
        to_agent="claude",
    )
    follow_up = _rco_follow_up(
        agent=agent,
        event_type=event_type,
        status=status,
    )

    state = _open_rco_for(
        [opening, follow_up],
        "canonical-rco-closure",
    )

    assert state["has_open_rco"] is expected_open


@pytest.mark.parametrize(
    ("closure_identity", "expected_open"),
    [
        ({}, True),
        (
            {
                "agent_uuid": "11111111-2222-3333-4444-555555555555",
                "session_id": "stale-session",
            },
            True,
        ),
        (
            {
                "agent_uuid": "11111111-2222-3333-4444-555555555555",
                "session_id": "current-session",
            },
            False,
        ),
    ],
)
def test_open_rco_requester_closure_binds_identity(
    closure_identity: dict[str, str],
    expected_open: bool,
) -> None:
    opening = _opening_handoff(
        "canonical-rco-closure",
        "2026-05-20T18:00:00Z",
        agent="codex",
        to_agent="claude",
    )
    opening.update(
        {
            "agent_uuid": "11111111-2222-3333-4444-555555555555",
            "session_id": "current-session",
        }
    )
    closure = _rco_follow_up(
        agent="codex",
        event_type="status",
        status="superseded",
    )
    closure.update(closure_identity)

    state = _open_rco_for([opening, closure], "canonical-rco-closure")

    assert state["has_open_rco"] is expected_open


def test_close_emits_event_that_clears_open_rco(tmp_path: Path) -> None:
    bridge_root = _seed_bridge(
        tmp_path,
        [_opening_handoff("task-1", "2026-05-20T18:00:00Z")],
    )

    # Idle gate should report open_rco before close
    idle_before = evaluate_idle_state(
        events_path=bridge_root / "shared" / "events.jsonl",
        claims_dir=bridge_root / "work_queue" / "claims",
        now_utc=datetime(2026, 5, 21, 6, 0, tzinfo=timezone.utc),
        idle_minutes=60,
        pending_ci_count=0,
        open_request_max_age_hours=12.0,
    )
    assert idle_before["criteria"]["open_rco_requests"]["task_ids"] == ["task-1"]

    result = close_bridge_rco_request(
        task_id="task-1",
        pr_number=999,
        from_agent="claude",
        bridge_root=bridge_root,
        merge_commit="abcdef0123",
        merged_at="2026-05-20T19:00:00Z",
        now_utc=datetime(2026, 5, 21, 6, 0, tzinfo=timezone.utc),
        emit=True,
        writer_backend=_PortableTestBackend(),
    )
    assert result["ok"] is True
    assert result["emitted"] is True
    assert result["decision"] == "closed"
    assert result["proposed_event"]["type"] == "decision"
    assert result["proposed_event"]["status"] == "rco_closed_postmerge"
    assert result["proposed_event"]["task_id"] == "task-1"
    assert result["proposed_event"]["to"] == "codex,claude"
    assert "operator" not in result["proposed_event"]["to"]
    assert not (bridge_root / "outbox").exists()
    assert not (bridge_root / "shared" / "last_claude.json").exists()

    idle_after = evaluate_idle_state(
        events_path=bridge_root / "shared" / "events.jsonl",
        claims_dir=bridge_root / "work_queue" / "claims",
        now_utc=datetime(2026, 5, 21, 6, 0, tzinfo=timezone.utc),
        idle_minutes=60,
        pending_ci_count=0,
        open_request_max_age_hours=12.0,
    )
    assert idle_after["criteria"]["open_rco_requests"]["task_ids"] == []


def test_close_does_not_extend_recent_merge_window(tmp_path: Path) -> None:
    """The close event must NOT match _is_merge_event."""
    bridge_root = _seed_bridge(
        tmp_path,
        [_opening_handoff("task-2", "2026-05-20T18:00:00Z")],
    )
    close_bridge_rco_request(
        task_id="task-2",
        pr_number=42,
        from_agent="claude",
        bridge_root=bridge_root,
        merge_commit="cafe1234",
        merged_at="2026-05-20T19:00:00Z",
        now_utc=datetime(2026, 5, 21, 6, 0, tzinfo=timezone.utc),
        emit=True,
        writer_backend=_PortableTestBackend(),
    )

    # Idle gate query at the SAME instant as the close emit. If the close
    # had been classified as a merge event, recent_merge.ok would be False
    # (event ts within the 60-min window). The whole point of using
    # type=decision status=rco_closed_postmerge is that it must not.
    idle_state = evaluate_idle_state(
        events_path=bridge_root / "shared" / "events.jsonl",
        claims_dir=bridge_root / "work_queue" / "claims",
        now_utc=datetime(2026, 5, 21, 6, 0, tzinfo=timezone.utc),
        idle_minutes=60,
        pending_ci_count=0,
        open_request_max_age_hours=12.0,
    )
    # The bridge had no prior merge event AND the close must not register as one
    assert idle_state["criteria"]["recent_merge"]["ok"] is True
    assert idle_state["criteria"]["recent_merge"].get("latest_utc") is None


def test_dry_run_does_not_write(tmp_path: Path) -> None:
    bridge_root = _seed_bridge(
        tmp_path,
        [_opening_handoff("task-3", "2026-05-20T18:00:00Z")],
    )
    events_path = bridge_root / "shared" / "events.jsonl"
    before = events_path.read_text(encoding="utf-8")

    result = close_bridge_rco_request(
        task_id="task-3",
        pr_number=7,
        from_agent="claude",
        bridge_root=bridge_root,
        now_utc=datetime(2026, 5, 21, 6, 0, tzinfo=timezone.utc),
        emit=False,
    )
    assert result["emitted"] is False
    assert result["decision"] == "ready"
    assert events_path.read_text(encoding="utf-8") == before


@pytest.mark.parametrize(
    "case",
    ["missing_identity", "third_party", "multi_target", "backdated"],
)
def test_close_refuses_event_that_would_leave_rco_open_before_write(
    tmp_path: Path,
    case: str,
) -> None:
    opening = _opening_handoff(
        "close-preflight",
        "2026-05-20T18:00:00Z",
        agent="codex",
        to_agent="claude",
    )
    from_agent = "codex"
    now_utc = datetime(2026, 5, 20, 18, 1, tzinfo=timezone.utc)
    if case == "missing_identity":
        opening.update(
            {
                "agent_uuid": "11111111-2222-3333-4444-555555555555",
                "session_id": "identity-bound-session",
            }
        )
    elif case == "third_party":
        from_agent = "fable"
    elif case == "multi_target":
        opening["to"] = "claude,fable"
        from_agent = "claude"
    elif case == "backdated":
        now_utc = datetime(2026, 5, 20, 17, 59, tzinfo=timezone.utc)

    bridge_root = _seed_bridge(tmp_path, [opening])
    events_path = bridge_root / "shared" / "events.jsonl"
    before = events_path.read_bytes()

    with pytest.raises(CloseRcoError) as excinfo:
        close_bridge_rco_request(
            task_id="close-preflight",
            pr_number=999,
            from_agent=from_agent,
            bridge_root=bridge_root,
            now_utc=now_utc,
            emit=True,
            writer_backend=_PortableTestBackend(),
        )

    assert excinfo.value.decision == "close_event_would_not_close"
    assert events_path.read_bytes() == before
    assert not (bridge_root / "spool").exists()
    assert not (bridge_root / "outbox").exists()


def test_identity_bound_request_can_close_with_explicit_requester_identity(
    tmp_path: Path,
) -> None:
    agent_uuid = "11111111-2222-3333-4444-555555555555"
    session_id = "identity-bound-session"
    opening = _opening_handoff(
        "close-with-identity",
        "2026-05-20T18:00:00Z",
        agent="codex",
        to_agent="claude",
    )
    opening.update({"agent_uuid": agent_uuid, "session_id": session_id})
    bridge_root = _seed_bridge(tmp_path, [opening])

    result = close_bridge_rco_request(
        task_id="close-with-identity",
        pr_number=999,
        from_agent="codex",
        from_agent_uuid=agent_uuid,
        from_session_id=session_id,
        bridge_root=bridge_root,
        now_utc=datetime(2026, 5, 20, 18, 1, tzinfo=timezone.utc),
        emit=True,
        writer_backend=_PortableTestBackend(),
    )

    assert result["decision"] == "closed"
    assert result["proposed_event"]["agent_uuid"] == agent_uuid
    assert result["proposed_event"]["session_id"] == session_id
    events = _read_events(bridge_root / "shared" / "events.jsonl")
    assert _open_rco_for(events, "close-with-identity")["has_open_rco"] is False


def test_writer_failure_maps_to_typed_close_decision_without_sidecars(
    tmp_path: Path,
) -> None:
    bridge_root = _seed_bridge(
        tmp_path,
        [_opening_handoff("task-write-fail", "2026-05-20T18:00:00Z")],
    )
    events_path = bridge_root / "shared" / "events.jsonl"
    before = events_path.read_bytes()

    with pytest.raises(CloseRcoError) as excinfo:
        close_bridge_rco_request(
            task_id="task-write-fail",
            pr_number=8,
            from_agent="claude",
            bridge_root=bridge_root,
            now_utc=datetime(2026, 5, 21, 6, 0, tzinfo=timezone.utc),
            emit=True,
            writer_backend=_PortableTestBackend(mutex_outcomes=["timeout"]),
        )

    assert excinfo.value.decision == "bridge_write_failed"
    assert events_path.read_bytes() == before
    assert not (bridge_root / "outbox").exists()
    assert not (bridge_root / "shared" / "last_claude.json").exists()
    assert len(list((bridge_root / "spool").glob("failed-append-*.jsonl"))) == 1


def test_invalid_from_agent_fails_before_wal_or_canonical_mutation(
    tmp_path: Path,
) -> None:
    bridge_root = _seed_bridge(
        tmp_path,
        [_opening_handoff("task-invalid-agent", "2026-05-20T18:00:00Z")],
    )
    events_path = bridge_root / "shared" / "events.jsonl"
    before = events_path.read_bytes()

    with pytest.raises(CloseRcoError) as excinfo:
        close_bridge_rco_request(
            task_id="task-invalid-agent",
            pr_number=9,
            from_agent="Invalid.Agent",
            bridge_root=bridge_root,
            now_utc=datetime(2026, 5, 21, 6, 0, tzinfo=timezone.utc),
            emit=True,
            writer_backend=_PortableTestBackend(),
        )

    assert excinfo.value.decision == "invalid_args"
    assert events_path.read_bytes() == before
    assert not (bridge_root / "spool").exists()
    assert not Path(f"{events_path}.append-v1-validation.json").exists()


def test_invalid_close_event_type_fails_before_wal_or_canonical_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge_root = _seed_bridge(
        tmp_path,
        [_opening_handoff("task-invalid-type", "2026-05-20T18:00:00Z")],
    )
    events_path = bridge_root / "shared" / "events.jsonl"
    before = events_path.read_bytes()
    original = closer._build_close_event

    def invalid_type_event(**kwargs):
        event = original(**kwargs)
        event["type"] = "unknown-close-type"
        return event

    monkeypatch.setattr(closer, "_build_close_event", invalid_type_event)

    with pytest.raises(CloseRcoError) as excinfo:
        close_bridge_rco_request(
            task_id="task-invalid-type",
            pr_number=10,
            from_agent="claude",
            bridge_root=bridge_root,
            now_utc=datetime(2026, 5, 21, 6, 0, tzinfo=timezone.utc),
            emit=True,
            writer_backend=_PortableTestBackend(),
        )

    assert excinfo.value.decision == "invalid_args"
    assert events_path.read_bytes() == before
    assert not (bridge_root / "spool").exists()
    assert not Path(f"{events_path}.append-v1-validation.json").exists()


def test_refuses_when_no_open_rco_for_task(tmp_path: Path) -> None:
    bridge_root = _seed_bridge(
        tmp_path,
        [_opening_handoff("task-A", "2026-05-20T18:00:00Z")],
    )
    with pytest.raises(CloseRcoError) as excinfo:
        close_bridge_rco_request(
            task_id="other-task",
            pr_number=1,
            from_agent="claude",
            bridge_root=bridge_root,
            now_utc=datetime(2026, 5, 21, 6, 0, tzinfo=timezone.utc),
            emit=True,
        )
    assert excinfo.value.decision == "no_open_rco"
    assert excinfo.value.exit_code == 3


def test_refuses_malformed_events_file(tmp_path: Path) -> None:
    bridge_root = _seed_bridge_raw(tmp_path, ["{not-json}\n"])

    with pytest.raises(CloseRcoError) as excinfo:
        close_bridge_rco_request(
            task_id="task-malformed",
            pr_number=1,
            from_agent="codex",
            bridge_root=bridge_root,
            now_utc=datetime(2026, 5, 21, 6, 0, tzinfo=timezone.utc),
            emit=False,
        )

    assert excinfo.value.decision == "invalid_events_file"
    assert excinfo.value.exit_code == 2
    assert "line 1" in str(excinfo.value)


def test_refuses_non_object_event_line(tmp_path: Path) -> None:
    bridge_root = _seed_bridge_raw(tmp_path, ["[]\n"])

    with pytest.raises(CloseRcoError) as excinfo:
        close_bridge_rco_request(
            task_id="task-non-object",
            pr_number=1,
            from_agent="codex",
            bridge_root=bridge_root,
            now_utc=datetime(2026, 5, 21, 6, 0, tzinfo=timezone.utc),
            emit=False,
        )

    assert excinfo.value.decision == "invalid_events_file"
    assert excinfo.value.exit_code == 2
    assert "line 1" in str(excinfo.value)


def test_refuses_when_rco_already_closed(tmp_path: Path) -> None:
    """Calling twice does not produce duplicate close events."""
    bridge_root = _seed_bridge(
        tmp_path,
        [
            _opening_handoff("task-B", "2026-05-20T18:00:00Z"),
            {
                "ts_utc": "2026-05-20T18:30:00Z",
                "agent": "claude",
                "type": "decision",
                "task_id": "task-B",
                "status": "rco_closed_postmerge",
                "severity": "",
                "to": "codex",
                "message": "already closed",
                "paths": [],
                "write_scope": [],
                "run_id": "",
                "pid": 0,
                "cwd": "",
            },
        ],
    )
    with pytest.raises(CloseRcoError) as excinfo:
        close_bridge_rco_request(
            task_id="task-B",
            pr_number=2,
            from_agent="claude",
            bridge_root=bridge_root,
            now_utc=datetime(2026, 5, 21, 6, 0, tzinfo=timezone.utc),
            emit=True,
        )
    assert excinfo.value.decision == "no_open_rco"


def test_empty_task_id_rejected(tmp_path: Path) -> None:
    bridge_root = _seed_bridge(
        tmp_path,
        [_opening_handoff("task-x", "2026-05-20T18:00:00Z")],
    )
    with pytest.raises(CloseRcoError) as excinfo:
        close_bridge_rco_request(
            task_id="",
            pr_number=1,
            from_agent="claude",
            bridge_root=bridge_root,
            now_utc=datetime(2026, 5, 21, 6, 0, tzinfo=timezone.utc),
            emit=True,
        )
    assert excinfo.value.decision == "invalid_args"


def test_cli_reports_malformed_events_file(tmp_path: Path) -> None:
    bridge_root = _seed_bridge_raw(tmp_path, ["{not-json}\n"])

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--task-id",
            "task-cli-malformed",
            "--pr",
            "1234",
            "--from-agent",
            "codex",
            "--bridge-root",
            str(bridge_root),
            "--now",
            "2026-05-21T06:00:00Z",
            "--json",
        ],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["decision"] == "invalid_events_file"


def test_cli_default_bridge_root_uses_runtime_env_from_other_cwd(
    tmp_path: Path,
) -> None:
    bridge_root = _seed_bridge(
        tmp_path / "runtime",
        [_opening_handoff("task-cli-env", "2026-05-20T18:00:00Z")],
    )
    other_cwd = tmp_path / "other-cwd"
    other_cwd.mkdir()
    env = os.environ.copy()
    env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(bridge_root)
    env.pop("AGENT_BRIDGE_ROOT", None)

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--task-id",
            "task-cli-env",
            "--pr",
            "1234",
            "--from-agent",
            "codex",
            "--now",
            "2026-05-21T06:00:00Z",
            "--dry-run",
            "--json",
        ],
        cwd=str(other_cwd),
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["decision"] == "ready"
    assert payload["emitted"] is False


@pytest.mark.skipif(os.name != "nt", reason="production bridge writes are Windows-only")
def test_cli_smoke(tmp_path: Path) -> None:
    bridge_root = _seed_bridge(
        tmp_path,
        [_opening_handoff("task-cli", "2026-05-20T18:00:00Z")],
    )
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--task-id",
            "task-cli",
            "--pr",
            "1234",
            "--from-agent",
            "claude",
            "--bridge-root",
            str(bridge_root),
            "--merge-commit",
            "deadbeef",
            "--merged-at",
            "2026-05-20T19:00:00Z",
            "--now",
            "2026-05-21T06:00:00Z",
            "--apply",
            "--json",
        ],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["decision"] == "closed"
    assert payload["proposed_event"]["status"] == "rco_closed_postmerge"
    assert payload["proposed_event"]["to"] == "codex,claude"
    assert "operator" not in payload["proposed_event"]["to"]
