# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "build_self_drive_queue_planner.py"

sys.path.insert(0, str(ROOT))

from tools.build_self_drive_queue_planner import (  # noqa: E402
    SelfDriveQueuePlannerError,
    build_self_drive_queue_planner,
    render_markdown,
)


NOW = datetime(2026, 6, 27, 17, 0, tzinfo=timezone.utc)
HEAD = "a" * 40


def _event(
    *,
    agent: str = "codex-lead-1",
    to: str = "codex-tools-1",
    task_id: str = "task-1",
    event_type: str = "wake_request",
    status: str = "open",
    ts: str = "2026-06-27T16:40:00Z",
    payload: dict | None = None,
    message: str = "visible request",
) -> dict:
    return {
        "ts_utc": ts,
        "agent": agent,
        "to": to,
        "type": event_type,
        "task_id": task_id,
        "status": status,
        "message": message,
        "payload": payload or {"head": HEAD, "pr": 1412},
        "cwd": "C:\\Python\\project2-master",
        "paths": ["C:\\Python\\project2-master\\secret\\raw.txt"],
    }


def _answer(
    *,
    agent: str = "codex-tools-1",
    task_id: str = "task-1",
    status: str = "answered",
    event_type: str = "done",
    ts: str = "2026-06-27T16:45:00Z",
) -> dict:
    return {
        "ts_utc": ts,
        "agent": agent,
        "to": "codex-lead-1",
        "type": event_type,
        "task_id": task_id,
        "status": status,
        "message": "answered at exact head",
        "payload": {"head": HEAD, "pr": 1412},
    }


def _claim(
    *,
    agent: str = "codex-tools-1",
    task_id: str = "task-claim",
    last_heartbeat_utc: str = "2026-06-27T16:59:00Z",
    claim_lease_expires_utc: str = "2026-06-27T17:04:00Z",
) -> dict:
    return {
        "agent": agent,
        "task_id": task_id,
        "summary": "active work",
        "mode": "write",
        "write_scope": ["tools/example.py"],
        "run_id": "run-1",
        "claimed_at_utc": "2026-06-27T16:30:00Z",
        "last_heartbeat_utc": last_heartbeat_utc,
        "lease_seconds": 300,
        "claim_lease_expires_utc": claim_lease_expires_utc,
    }


def _write_events(path: Path, events: list[dict]) -> Path:
    path.write_text(
        "\n".join(json.dumps(event, sort_keys=True) for event in events) + "\n",
        encoding="utf-8",
    )
    return path


def test_planner_ranks_operator_gate_before_agent_request_and_redacts_paths() -> None:
    report = build_self_drive_queue_planner(
        events=[
            _event(to="operator", task_id="operator-signature", status="operator_required"),
            _event(to="claude-rco-1", task_id="needs-rco", status="rco_requested"),
        ],
        claims=[],
        now_utc=NOW,
    )

    assert report["ok"] is True
    assert report["queue"]["classification_counts"] == {
        "answer_open_bridge_request": 1,
        "operator_gate": 1,
        "ready_for_review_or_lead_action": 1,
    }
    actions = report["queue"]["next_actions"]
    assert actions[0]["classification"] == "operator_gate"
    assert actions[0]["owner_agent"] == "operator"
    assert actions[0]["action"] == "surface_operator_gate_do_not_bypass"
    assert actions[1]["classification"] == "answer_open_bridge_request"
    assert actions[1]["owner_agent"] == "claude-rco-1"
    encoded = json.dumps(report, sort_keys=True)
    assert "C:\\Python" not in encoded
    assert "secret" not in encoded
    assert "visible request" not in encoded
    assert report["authority_boundary"]["bridge_append_allowed"] is False
    assert report["authority_boundary"]["merge_allowed"] is False


def test_operator_addressed_proposal_is_not_treated_as_operator_gate() -> None:
    report = build_self_drive_queue_planner(
        events=[
            _event(
                to="operator",
                task_id="roadmap-proposal",
                status="tools_slice_proposal_self_drive_queue_planner",
            ),
        ],
        claims=[],
        now_utc=NOW,
    )

    assert report["queue"]["next_actions"] == []


def test_answered_and_merged_requests_do_not_reopen_queue() -> None:
    report = build_self_drive_queue_planner(
        events=[
            _event(to="claude-rco-1", task_id="answered-task", status="rco_requested"),
            _answer(agent="claude-rco-1", task_id="answered-task", status="rco_pass"),
            _event(to="codex-lead-1", task_id="merged-task", status="full_consensus_driver_ready"),
            _answer(agent="codex-lead-1", task_id="merged-task", status="merged"),
        ],
        claims=[],
        now_utc=NOW,
    )

    assert report["queue"]["next_actions"] == []
    assert report["queue"]["classification_counts"] == {}


def test_planner_reports_stale_and_current_claims() -> None:
    report = build_self_drive_queue_planner(
        events=[],
        claims=[
            _claim(
                task_id="stale-claim",
                last_heartbeat_utc="2026-06-27T16:00:00Z",
                claim_lease_expires_utc="2026-06-27T16:05:00Z",
            ),
            _claim(task_id="current-claim"),
        ],
        now_utc=NOW,
        stale_claim_minutes=15,
    )

    actions = report["queue"]["next_actions"]
    assert [action["classification"] for action in actions] == [
        "stale_active_claim",
        "continue_active_claim",
    ]
    assert actions[0]["task_id"] == "stale-claim"
    assert actions[0]["reason"] == "claim_lease_expired"
    assert actions[1]["task_id"] == "current-claim"
    assert report["lanes"]["active_claims_by_agent"] == {"codex-tools-1": 2}


def test_claim_write_scope_redacts_absolute_paths() -> None:
    claim = _claim(task_id="claim-with-absolute-path")
    claim["write_scope"] = [
        "tools/build_self_drive_queue_planner.py",
        "C:\\Python\\wd-agent-prompts\\handoff\\codex-tools-1.md",
    ]

    report = build_self_drive_queue_planner(
        events=[],
        claims=[claim],
        now_utc=NOW,
    )

    encoded = json.dumps(report, sort_keys=True)
    action = report["queue"]["next_actions"][0]
    assert action["write_scope"] == [
        "<absolute-path-redacted>",
        "tools/build_self_drive_queue_planner.py",
    ]
    assert report["source"]["path_free"] is True
    assert report["source"]["path_free_derived_from_output"] is True
    assert report["source"]["path_free_scan"] == {
        "unix_absolute_path_found": False,
        "url_scheme_found": False,
        "windows_absolute_path_found": False,
        "worktree_marker_found": False,
    }
    assert "C:\\Python" not in encoded
    assert "wd-agent-prompts" not in encoded


def test_path_free_is_derived_from_final_output() -> None:
    report = build_self_drive_queue_planner(
        events=[
            _event(
                to="codex-tools-1",
                task_id="C:\\Python\\leaky-task",
                status="open",
            ),
        ],
        claims=[],
        now_utc=NOW,
    )

    encoded = json.dumps(report, sort_keys=True)
    assert "C:\\\\Python" in encoded
    assert report["source"]["path_free"] is False
    assert report["source"]["path_free_derived_from_output"] is True
    assert report["source"]["path_free_scan"]["windows_absolute_path_found"] is True


def test_ready_review_item_uses_latest_non_terminal_status() -> None:
    report = build_self_drive_queue_planner(
        events=[
            _event(
                agent="codex-tools-1",
                to="claude-rco-1",
                task_id="pr-ready",
                event_type="handoff",
                status="ci_green_rco_requested",
                payload={"head": HEAD, "pr": 1412},
            )
        ],
        claims=[],
        now_utc=NOW,
    )

    actions = report["queue"]["next_actions"]
    assert any(
        action["classification"] == "ready_for_review_or_lead_action"
        and action["owner_agent"] == "claude-rco-1"
        and action["head_prefix"] == HEAD[:12]
        and action["pr"] == "1412"
        for action in actions
    )


def test_planner_fails_closed_on_redaction_sentinel() -> None:
    with pytest.raises(SelfDriveQueuePlannerError) as excinfo:
        build_self_drive_queue_planner(
            events=[
                _event(message="contains " + ("PRIVATE" + "_MARKER")),
            ],
            claims=[],
            now_utc=NOW,
        )

    assert excinfo.value.report["decision"] == "self_drive_queue_planner_error"
    assert excinfo.value.report["errors"] == ["redaction_sentinel_present"]


def test_cli_json_defaults_to_runtime_bridge_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge_root = tmp_path / "runtime" / ".agent-bridge"
    events_path = bridge_root / "shared" / "events.jsonl"
    events_path.parent.mkdir(parents=True)
    _write_events(events_path, [_event(to="codex-tools-1", task_id="runtime-task")])
    monkeypatch.setenv("AGENT_BRIDGE_RUNTIME_ROOT", str(bridge_root))

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--now",
            "2026-06-27T17:00:00Z",
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    report = json.loads(result.stdout)
    encoded = json.dumps(report, sort_keys=True)
    assert report["ok"] is True
    assert report["source"]["event_count"] == 1
    assert report["queue"]["next_actions"][0]["task_id"] == "runtime-task"
    assert str(bridge_root) not in encoded
    assert "events.jsonl" not in encoded


def test_markdown_renders_authority_boundary() -> None:
    report = build_self_drive_queue_planner(events=[], claims=[], now_utc=NOW)

    markdown = render_markdown(report)

    assert "# WD Self-Drive Queue Planner" in markdown
    assert "bridge append allowed: `false`" in markdown
    assert "github write allowed: `false`" in markdown
    assert "merge allowed: `false`" in markdown
    assert "C:\\Python" not in markdown
