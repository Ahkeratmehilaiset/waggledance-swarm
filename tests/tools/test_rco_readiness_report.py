# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools.rco_readiness_report import (
    CLAIM_GATES,
    RcoReadinessError,
    build_rco_readiness_report,
    main,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "rco_readiness_report.py"


def _request(
    *,
    ts: str = "2026-06-14T12:00:00Z",
    task_id: str = "pr1208-rco-pass",
    status: str = "rco_pass_required_after_ci_green",
    agent: str = "codex-tools-1",
    to: str = "claude-rco-2",
) -> dict[str, object]:
    return {
        "ts_utc": ts,
        "agent": agent,
        "to": to,
        "type": "wake_request",
        "task_id": task_id,
        "status": status,
        "message": "PR #1208 is CI green; pass/block requested at exact head.",
        "payload": {"pr": 1208, "head": "c" * 40},
    }


def _general_review() -> dict[str, object]:
    return {
        "ts_utc": "2026-06-14T12:05:00Z",
        "agent": "codex-lead-1",
        "to": "claude-rco-2",
        "type": "message",
        "task_id": "general-review",
        "status": "review_requested",
        "message": "Please review a general proposal.",
    }


def _now() -> datetime:
    return datetime(2026, 6, 14, 12, 10, tzinfo=timezone.utc)


def _events_file(bridge_root: Path, events: list[dict[str, object]]) -> Path:
    shared = bridge_root / "shared"
    shared.mkdir(parents=True)
    events_path = shared / "events.jsonl"
    events_path.write_text(
        "\n".join(json.dumps(event, sort_keys=True) for event in events) + "\n",
        encoding="utf-8",
    )
    return events_path


def test_reports_direct_rco_pass_request_ahead_of_general_review(tmp_path: Path) -> None:
    (tmp_path / "wake_claude-rco-2").write_text(
        "2026-06-14T12:04:00Z",
        encoding="utf-8",
    )

    report = build_rco_readiness_report(
        agent="claude-rco-2",
        events=[_request(), _general_review()],
        bridge_root=tmp_path,
        claims=[],
        now_utc=_now(),
    )

    assert report["decision"] == "direct_rco_pass_block_request_ready"
    assert report["wake_file"]["present"] is True
    assert report["wake_consumed"] is False
    assert report["direct_pass_block_request_count"] == 1
    assert report["highest_priority_request"]["task_id"] == "pr1208-rco-pass"
    assert report["highest_priority_request"]["priority"] == "direct_rco_pass_block"
    assert report["bridge_next_action"]["open_incoming_count"] == 2
    assert "Test-BridgeWake.ps1 -Agent claude-rco-2" in report["wake_consume_step0_command"]


def test_wake_ack_does_not_close_underlying_direct_request(tmp_path: Path) -> None:
    report = build_rco_readiness_report(
        agent="claude-rco-2",
        events=[
            _request(),
            {
                "ts_utc": "2026-06-14T12:02:00Z",
                "agent": "claude-rco-2",
                "to": "codex-tools-1",
                "type": "message",
                "task_id": "pr1208-rco-pass",
                "status": "wake_ack_corrected_rco_pass_already_posted_clear_to_merge",
                "message": "Wake bit consumed; not a new RCO_PASS.",
                "payload": {"pr": 1208, "head": "c" * 40},
            },
        ],
        bridge_root=tmp_path,
        claims=[],
        now_utc=_now(),
    )

    assert report["decision"] == "direct_rco_pass_block_request_ready"
    assert report["direct_pass_block_request_count"] == 1
    assert report["request_closure_rule"].startswith("wake acknowledgements")


def test_real_rco_pass_closes_direct_request(tmp_path: Path) -> None:
    report = build_rco_readiness_report(
        agent="claude-rco-2",
        events=[
            _request(),
            {
                "ts_utc": "2026-06-14T12:03:00Z",
                "agent": "claude-rco-2",
                "to": "codex-tools-1,codex-lead-1",
                "type": "decision",
                "task_id": "pr1208-rco-pass",
                "status": "rco_pass",
                "message": "RCO_PASS PR #1208 at exact head.",
                "payload": {"pr": 1208, "head": "c" * 40},
            },
        ],
        bridge_root=tmp_path,
        claims=[],
        now_utc=_now(),
    )

    assert report["decision"] == "rco_ready_no_direct_pass_block_request"
    assert report["direct_pass_block_request_count"] == 0
    assert report["highest_priority_request"] == {}


def test_pass_words_in_done_or_decision_do_not_create_direct_requests(
    tmp_path: Path,
) -> None:
    report = build_rco_readiness_report(
        agent="claude-rco-2",
        events=[
            {
                "ts_utc": "2026-06-14T12:00:00Z",
                "agent": "codex-tools-1",
                "to": "claude-rco-2",
                "type": "done",
                "task_id": "old-merged",
                "status": "merged_observed",
                "message": "RCO_PASS was present before merge.",
            },
            {
                "ts_utc": "2026-06-14T12:01:00Z",
                "agent": "codex-tools-1",
                "to": "claude-rco-2",
                "type": "decision",
                "task_id": "tools-pass",
                "status": "build_consensus_pass",
                "message": "Needs no RCO action from this event.",
            },
        ],
        bridge_root=tmp_path,
        claims=[],
        now_utc=_now(),
    )

    assert report["decision"] == "rco_ready_no_direct_pass_block_request"
    assert report["direct_pass_block_request_count"] == 0


def test_stale_direct_requests_are_filtered_by_default(tmp_path: Path) -> None:
    report = build_rco_readiness_report(
        agent="claude-rco-2",
        events=[
            _request(ts="2026-06-13T00:00:00Z", task_id="stale-pass"),
            _request(ts="2026-06-14T12:00:00Z", task_id="fresh-pass"),
        ],
        bridge_root=tmp_path,
        claims=[],
        now_utc=_now(),
    )

    assert report["direct_pass_block_request_count"] == 1
    assert report["highest_priority_request"]["task_id"] == "fresh-pass"
    assert report["max_age_hours"] == 12.0


def test_invalid_non_rco_agent_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(RcoReadinessError) as excinfo:
        build_rco_readiness_report(
            agent="codex-tools-1",
            events=[],
            bridge_root=tmp_path,
            claims=[],
            now_utc=_now(),
        )

    assert "agent must be an RCO lane" in excinfo.value.report["errors"]


def test_authority_gates_are_false(tmp_path: Path) -> None:
    report = build_rco_readiness_report(
        agent="claude-rco-1",
        events=[],
        bridge_root=tmp_path,
        claims=[],
        now_utc=_now(),
    )

    assert CLAIM_GATES
    for gate in CLAIM_GATES:
        assert report[gate] is False
    assert report["authority_boundary"]["bridge_append_allowed"] is False
    assert report["authority_boundary"]["wake_file_consume_allowed"] is False
    assert report["authority_boundary"]["rco_pass_emit_allowed"] is False


def test_cli_json_uses_runtime_bridge_without_leaking_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bridge_root = tmp_path / "runtime-bridge"
    _events_file(bridge_root, [_request()])
    monkeypatch.setenv("AGENT_BRIDGE_RUNTIME_ROOT", str(bridge_root))

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--agent",
            "claude-rco-2",
            "--now",
            "2026-06-14T12:10:00Z",
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    report = json.loads(result.stdout)
    serialized = json.dumps(report, sort_keys=True)
    assert report["decision"] == "direct_rco_pass_block_request_ready"
    assert report["local_paths_recorded"] is False
    assert str(bridge_root) not in serialized
    assert "events.jsonl" not in serialized


def test_cli_returns_error_for_non_rco_agent(tmp_path: Path, capsys) -> None:
    _events_file(tmp_path, [])

    rc = main(
        [
            "--agent",
            "codex-tools-1",
            "--bridge-root",
            str(tmp_path),
            "--json",
        ]
    )

    assert rc == 2
    report = json.loads(capsys.readouterr().out)
    assert report["decision"] == "rco_readiness_report_error"
