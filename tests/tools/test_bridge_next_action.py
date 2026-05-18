from __future__ import annotations

import json
from pathlib import Path

from tools.bridge_next_action import main, read_events, recommend_next_action
from waggledance.core.work_queue import claim_task


def _events_file(path: Path, events: list[dict[str, object]]) -> Path:
    events_path = path / "shared" / "events.jsonl"
    events_path.parent.mkdir(parents=True)
    events_path.write_text(
        "\n".join(json.dumps(event, sort_keys=True) for event in events) + "\n",
        encoding="utf-8",
    )
    return events_path


def test_recommends_continuing_own_claim_before_incoming_request(tmp_path: Path) -> None:
    bridge = tmp_path / ".agent-bridge"
    claim = claim_task(
        agent="codex",
        task_id="owned-task",
        summary="already started",
        mode="write",
        write_scope=["tools/x.py"],
        bridge_root=bridge,
    )
    events = [
        {
            "ts_utc": "2026-05-18T10:00:00Z",
            "agent": "claude",
            "to": "codex",
            "type": "message",
            "task_id": "incoming-task",
            "status": "request",
            "message": "please review",
        }
    ]

    report = recommend_next_action(agent="codex", events=events, claims=[claim])

    assert report["action"] == "continue_claim"
    assert report["task_id"] == "owned-task"
    assert report["safe_mode"] == "write"
    assert report["open_incoming_count"] == 1


def test_recommends_answering_latest_unanswered_incoming_request() -> None:
    events = [
        {
            "ts_utc": "2026-05-18T10:00:00Z",
            "agent": "claude",
            "to": "codex",
            "type": "decision",
            "task_id": "old-task",
            "status": "request_changes",
            "message": "old request",
        },
        {
            "ts_utc": "2026-05-18T10:05:00Z",
            "agent": "codex",
            "type": "decision",
            "task_id": "old-task",
            "status": "pass",
            "message": "answered",
        },
        {
            "ts_utc": "2026-05-18T10:10:00Z",
            "agent": "claude",
            "to": "codex",
            "type": "message",
            "task_id": "new-task",
            "status": "request",
            "message": "new request",
        },
    ]

    report = recommend_next_action(agent="codex", events=events, claims=[])

    assert report["action"] == "answer_incoming"
    assert report["task_id"] == "new-task"
    assert report["safe_mode"] == "read-only"
    assert report["incoming"]["agent"] == "claude"
    assert report["open_incoming_count"] == 1


def test_recommends_parallel_read_only_when_foreign_write_claim_exists(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    claim = claim_task(
        agent="claude",
        task_id="foreign-write",
        summary="other agent writes",
        mode="write",
        write_scope=["waggledance/core/work_queue.py"],
        bridge_root=bridge,
    )

    report = recommend_next_action(agent="codex", events=[], claims=[claim])

    assert report["action"] == "parallel_read_only"
    assert report["task_id"] == "bridge-review-or-scout"
    assert report["safe_mode"] == "read-only"
    assert report["foreign_write_claim_count"] == 1


def test_recommends_claiming_unblocked_work_when_bridge_is_clear() -> None:
    report = recommend_next_action(agent="codex", events=[], claims=[])

    assert report["action"] == "claim_unblocked_work"
    assert report["task_id"] == "next-unclaimed-scout-or-implementation"
    assert report["safe_mode"] == "write-or-read-only"


def test_read_events_ignores_malformed_lines_and_honors_tail(tmp_path: Path) -> None:
    events_path = tmp_path / "events.jsonl"
    events_path.write_text(
        "\n".join(
            [
                json.dumps({"task_id": "one"}),
                "{not-json",
                json.dumps({"task_id": "two"}),
                json.dumps(["not-object"]),
                json.dumps({"task_id": "three"}),
            ]
        ),
        encoding="utf-8",
    )

    events = read_events(events_path, tail=3)

    assert [event["task_id"] for event in events] == ["two", "three"]


def test_cli_outputs_json_recommendation(tmp_path: Path, capsys) -> None:
    bridge = tmp_path / ".agent-bridge"
    events_path = _events_file(
        bridge,
        [
            {
                "ts_utc": "2026-05-18T10:10:00Z",
                "agent": "claude",
                "to": "codex",
                "type": "message",
                "task_id": "new-task",
                "status": "request",
                "message": "new request",
            }
        ],
    )

    exit_code = main(
        [
            "--agent",
            "codex",
            "--bridge-root",
            str(bridge),
            "--events",
            str(events_path),
            "--json",
        ]
    )

    assert exit_code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["decision"] == "bridge_next_action"
    assert report["action"] == "answer_incoming"


def test_private_marker_in_selected_output_is_refused() -> None:
    events = [
        {
            "ts_utc": "2026-05-18T10:10:00Z",
            "agent": "claude",
            "to": "codex",
            "type": "message",
            "task_id": "secret-task",
            "status": "request",
            "message": "contains PRIVATE_MARKER",
        }
    ]

    try:
        recommend_next_action(agent="codex", events=events, claims=[])
    except Exception as exc:
        assert "private marker" in str(exc)
    else:
        raise AssertionError("private marker should refuse selected output")
