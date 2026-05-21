# SPDX-License-Identifier: BUSL-1.1
"""Tests for tools/notify_unanswered_peer_messages.py."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "notify_unanswered_peer_messages.py"

sys.path.insert(0, str(ROOT))

from tools.notify_unanswered_peer_messages import (  # noqa: E402
    find_unanswered_peer_messages,
)


NOW = datetime(2026, 5, 21, 18, 30, tzinfo=timezone.utc)


def _event(
    ts_utc: str,
    agent: str,
    type_: str,
    status: str,
    *,
    task_id: str = "T",
    to: str = "claude",
    message: str = "",
) -> dict:
    return {
        "ts_utc": ts_utc,
        "agent": agent,
        "type": type_,
        "task_id": task_id,
        "status": status,
        "severity": "",
        "to": to,
        "message": message,
        "paths": [],
        "write_scope": [],
        "run_id": "",
        "pid": 0,
        "cwd": "",
    }


def _seed_bridge(tmp_path: Path, events: list[dict]) -> Path:
    bridge_root = tmp_path / ".agent-bridge"
    shared = bridge_root / "shared"
    shared.mkdir(parents=True)
    events_path = shared / "events.jsonl"
    with events_path.open("w", encoding="utf-8", newline="\n") as fh:
        for event in events:
            fh.write(json.dumps(event) + "\n")
    return bridge_root


def test_no_events_returns_zero_unanswered() -> None:
    report = find_unanswered_peer_messages(
        events=[], agent="claude", now_utc=NOW
    )
    assert report["unanswered_count"] == 0
    assert report["unanswered"] == []


def test_unanswered_handoff_to_claude_is_reported() -> None:
    events = [
        _event(
            "2026-05-21T18:00:00Z",
            "codex",
            "handoff",
            "rco_requested",
            message="please RCO PR #N",
        ),
    ]
    report = find_unanswered_peer_messages(
        events=events, agent="claude", now_utc=NOW
    )
    assert report["unanswered_count"] == 1
    entry = report["unanswered"][0]
    assert entry["agent"] == "codex"
    assert entry["type"] == "handoff"
    assert entry["status"] == "rco_requested"


def test_claude_response_clears_unanswered() -> None:
    events = [
        _event("2026-05-21T18:00:00Z", "codex", "handoff", "rco_requested"),
        _event(
            "2026-05-21T18:05:00Z",
            "claude",
            "rco_review",
            "rco_pass",
            to="codex",
        ),
    ]
    report = find_unanswered_peer_messages(
        events=events, agent="claude", now_utc=NOW
    )
    assert report["unanswered_count"] == 0


def test_response_must_be_strictly_after_request() -> None:
    """A claude response with the SAME ts as the peer ask is not enough."""
    same_ts = "2026-05-21T18:00:00Z"
    events = [
        _event(same_ts, "codex", "handoff", "rco_requested"),
        _event(same_ts, "claude", "rco_review", "rco_pass", to="codex"),
    ]
    report = find_unanswered_peer_messages(
        events=events, agent="claude", now_utc=NOW
    )
    assert report["unanswered_count"] == 1


def test_response_to_other_task_does_not_clear() -> None:
    events = [
        _event(
            "2026-05-21T18:00:00Z",
            "codex",
            "handoff",
            "rco_requested",
            task_id="task-A",
        ),
        _event(
            "2026-05-21T18:05:00Z",
            "claude",
            "rco_review",
            "rco_pass",
            task_id="task-B",
            to="codex",
        ),
    ]
    report = find_unanswered_peer_messages(
        events=events, agent="claude", now_utc=NOW
    )
    assert report["unanswered_count"] == 1
    assert report["unanswered"][0]["task_id"] == "task-A"


def test_old_event_outside_window_is_ignored() -> None:
    """Events older than the window cutoff are skipped."""
    events = [
        _event(
            "2026-05-21T16:00:00Z",  # 2.5h before NOW, window=60 min
            "codex",
            "handoff",
            "rco_requested",
        ),
    ]
    report = find_unanswered_peer_messages(
        events=events, agent="claude", now_utc=NOW, window_minutes=60
    )
    assert report["unanswered_count"] == 0


def test_self_addressed_event_is_ignored() -> None:
    """An event whose to=claude but emitted BY claude is not a peer message."""
    events = [
        _event(
            "2026-05-21T18:10:00Z",
            "claude",
            "handoff",
            "rco_requested",
            to="claude",
        ),
    ]
    report = find_unanswered_peer_messages(
        events=events, agent="claude", now_utc=NOW
    )
    assert report["unanswered_count"] == 0


def test_message_not_addressed_to_target_agent_ignored() -> None:
    events = [
        _event(
            "2026-05-21T18:10:00Z",
            "codex",
            "handoff",
            "rco_requested",
            to="operator",
        ),
    ]
    report = find_unanswered_peer_messages(
        events=events, agent="claude", now_utc=NOW
    )
    assert report["unanswered_count"] == 0


def test_response_expected_filter_excludes_fyi_types() -> None:
    """heartbeat, claim, done, release do not need a response."""
    events = [
        _event(
            "2026-05-21T18:10:00Z",
            "codex",
            "claim",
            "active",
            to="claude,operator",
        ),
        _event(
            "2026-05-21T18:11:00Z",
            "codex",
            "done",
            "merged",
            to="claude,operator",
        ),
        _event(
            "2026-05-21T18:12:00Z",
            "codex",
            "release",
            "released",
            to="claude,operator",
        ),
    ]
    report = find_unanswered_peer_messages(
        events=events, agent="claude", now_utc=NOW
    )
    assert report["unanswered_count"] == 0


def test_response_expected_includes_handoff_message_decision_finding() -> None:
    events = [
        _event("2026-05-21T18:10:00Z", "codex", "handoff", "rco_requested"),
        _event(
            "2026-05-21T18:11:00Z",
            "codex",
            "message",
            "status_query",
            task_id="task-2",
        ),
        _event(
            "2026-05-21T18:12:00Z",
            "codex",
            "decision",
            "changes_requested",
            task_id="task-3",
        ),
        _event(
            "2026-05-21T18:13:00Z",
            "codex",
            "finding",
            "reported",
            task_id="task-4",
        ),
    ]
    report = find_unanswered_peer_messages(
        events=events, agent="claude", now_utc=NOW
    )
    assert report["unanswered_count"] == 4
    types_seen = {e["type"] for e in report["unanswered"]}
    assert types_seen == {"handoff", "message", "decision", "finding"}


def test_to_field_with_multiple_recipients_matches() -> None:
    events = [
        _event(
            "2026-05-21T18:10:00Z",
            "codex",
            "handoff",
            "rco_requested",
            to="claude,operator",
        ),
    ]
    report = find_unanswered_peer_messages(
        events=events, agent="claude", now_utc=NOW
    )
    assert report["unanswered_count"] == 1


def test_cli_smoke_returns_exit_4_when_unanswered(tmp_path: Path) -> None:
    bridge_root = _seed_bridge(
        tmp_path,
        [_event("2026-05-21T18:10:00Z", "codex", "handoff", "rco_requested")],
    )
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--agent",
            "claude",
            "--bridge-root",
            str(bridge_root),
            "--now",
            "2026-05-21T18:30:00Z",
            "--json",
        ],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 4, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["unanswered_count"] == 1


def test_cli_smoke_returns_exit_0_when_clear(tmp_path: Path) -> None:
    bridge_root = _seed_bridge(
        tmp_path,
        [_event("2026-05-21T18:10:00Z", "codex", "claim", "active", to="claude,operator")],
    )
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--agent",
            "claude",
            "--bridge-root",
            str(bridge_root),
            "--now",
            "2026-05-21T18:30:00Z",
            "--json",
        ],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["unanswered_count"] == 0
