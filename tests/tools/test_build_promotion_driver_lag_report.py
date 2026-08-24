# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tools.build_promotion_driver_lag_report import (
    build_promotion_driver_lag_report,
)

HEAD = "1234567890abcdef1234567890abcdef12345678"
BASE = "abcdef1234567890abcdef1234567890abcdef12"
TASK = "codex-lead-1/promotion-driver-lag-report-20260606"
SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "tools"
    / "build_promotion_driver_lag_report.py"
)
AGENT_UUIDS = {
    "claude-rco-1": "2b2f6ff9-06c2-4ec8-b526-f10071ce7103",
    "claude-rco-2": "76739997-0058-41a2-8514-78ff295537aa",
    "codex-lead-1": "d3c9d1d1-96a9-4eb8-a8e2-6f05f9d1a101",
    "codex-tools-1": "7a8af68d-20bc-4598-9953-23c5dd98b102",
    "fable-5": "f8b1e5c0-3d2a-4e6b-9c1f-7a0d5e2b4c80",
}
CLEAR_ACCEPTED_QUEUE = {
    "ok": True,
    "complete": True,
    "decision": "accepted_queue_complete",
    "errors": [],
}


def _status(**overrides: object) -> dict:
    status = {
        "pr_number": 934,
        "state": "OPEN",
        "isDraft": True,
        "mergeStateStatus": "CLEAN",
        "head_sha": HEAD,
        "base_sha": BASE,
        "changed_paths": ["tools/idle_daily_summary.py"],
        "diff_text": "+ def helper():\n+     return 1\n",
        "checks": [
            {"name": "unified", "state": "success"},
            {"name": "test (3.13)", "conclusion": "success"},
        ],
    }
    status.update(overrides)
    return status


def _event(
    agent: str,
    status: str,
    *,
    task_id: str = TASK,
    head: str = HEAD,
    pr: int = 934,
) -> dict:
    event = {
        "ts_utc": "2026-06-06T22:30:00Z",
        "agent": agent,
        "type": "decision",
        "status": status,
        "task_id": task_id,
        "message": f"{status} exact head {head}",
        "payload": {"head": head, "pr": pr},
    }
    if agent in AGENT_UUIDS:
        event["agent_uuid"] = AGENT_UUIDS[agent]
    return event


def _full_events() -> list[dict]:
    return [
        _event("codex-lead-1", "build_consensus_pass"),
        _event("codex-tools-1", "build_consensus_pass"),
        _event("claude-rco-2", "rco_pass"),
    ]


def _report(
    *,
    status: dict | None = None,
    events: list[dict] | None = None,
    accepted_queue_preflight: dict | None = CLEAR_ACCEPTED_QUEUE,
) -> dict:
    return build_promotion_driver_lag_report(
        pr_status=status or _status(),
        events=events if events is not None else _full_events(),
        task_id=TASK,
        head=HEAD,
        origin_main_sha=BASE,
        author_agent="fable-5",
        accepted_queue_preflight=accepted_queue_preflight,
    )


def _events_path(tmp_path: Path, events: list[dict]) -> Path:
    path = tmp_path / "events.jsonl"
    path.write_text(
        "\n".join(json.dumps(event, sort_keys=True) for event in events),
        encoding="utf-8",
    )
    return path


def test_open_draft_eligible_pr_reports_undraft_and_merge_pending() -> None:
    report = _report()

    assert report["decision"] == "promotion_driver_lag_detected"
    assert report["driver_action_required"] is True
    assert report["required_driver_actions"] == ["undraft", "merge"]
    assert report["lag_reason"] == "driver_action_pending"
    assert report["would_undraft"] is True
    assert report["would_merge"] is True
    assert report["pr"]["state"] == "open"
    assert report["pr"]["is_draft"] is True
    assert report["promotion"]["eligible"] is True
    assert report["promotion"]["gate_ok"]["bridge_consensus"] is True
    assert report["read_only"] is True
    assert report["network_access"] is False
    assert report["writes_bridge_events"] is False
    assert report["external_writes_applied"] is False


def test_open_nondraft_eligible_pr_reports_merge_only_pending() -> None:
    report = _report(status=_status(isDraft=False))

    assert report["decision"] == "promotion_driver_lag_detected"
    assert report["driver_action_required"] is True
    assert report["required_driver_actions"] == ["merge"]
    assert report["lag_reason"] == "driver_action_pending"


def test_unresolved_accepted_queue_suppresses_driver_action() -> None:
    report = _report(
        accepted_queue_preflight={
            "ok": True,
            "complete": False,
            "decision": "accepted_queue_incomplete",
            "errors": [],
        }
    )

    assert report["decision"] == "promotion_driver_lag_absent"
    assert report["driver_action_required"] is False
    assert report["required_driver_actions"] == []
    assert report["promotion"]["eligible"] is False
    assert report["promotion"]["gate_ok"]["accepted_queue"] is False


def test_nonclean_raw_merge_state_fails_closed() -> None:
    report = _report(status=_status(mergeStateStatus="UNSTABLE"))

    assert report["decision"] == "promotion_driver_lag_absent"
    assert report["driver_action_required"] is False
    assert report["promotion"]["eligible"] is False
    assert report["promotion"]["gate_ok"]["snapshot_state"] is False


def test_equivalent_raw_and_canonical_aliases_are_accepted() -> None:
    report = _report(
        status=_status(
            is_draft=True,
            mergeable="MERGEABLE",
        )
    )

    assert report["decision"] == "promotion_driver_lag_detected"
    assert report["promotion"]["eligible"] is True
    assert report["required_driver_actions"] == ["undraft", "merge"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"mergeStateStatus": "UNSTABLE", "mergeable": "MERGEABLE"},
        {"isDraft": True, "is_draft": False},
        {"mergeStateStatus": 7, "mergeable": "MERGEABLE"},
        {"isDraft": "yes", "is_draft": False},
        {"mergeable": True},
        {"is_draft": "yes"},
        {"pr_state": 7},
        {"draft": "yes"},
        {"merge_state": 7},
        {"mergedAt": 7},
    ],
)
def test_malformed_or_conflicting_status_aliases_fail_closed(
    overrides: dict[str, object],
) -> None:
    report = _report(status=_status(**overrides))

    assert report["ok"] is False
    assert report["decision"] == "invalid_input"
    assert report["driver_action_required"] is False
    assert report["would_merge"] is False


def test_summary_only_status_aliases_normalize_to_verifier_boundary() -> None:
    status = _status()
    status["pr_state"] = status.pop("state")
    status["draft"] = status.pop("isDraft")
    status["merge_state"] = status.pop("mergeStateStatus")
    status["mergedAt"] = None

    report = _report(status=status)

    assert report["decision"] == "promotion_driver_lag_detected"
    assert report["promotion"]["eligible"] is True
    assert report["required_driver_actions"] == ["undraft", "merge"]
    assert report["pr"]["state"] == "open"
    assert report["pr"]["is_draft"] is True
    assert report["pr"]["merge_state"] == "MERGEABLE"
    assert report["pr"]["merged_at"] == ""


@pytest.mark.parametrize(
    "overrides",
    [
        {"merge_state": "BLOCKED"},
        {"pr_state": "CLOSED"},
        {"mergedAt": "2026-07-24T12:00:00Z"},
        {"draft": False},
    ],
)
def test_additional_conflicting_status_aliases_fail_closed(
    overrides: dict[str, object],
) -> None:
    report = _report(status=_status(**overrides))

    assert report["ok"] is False
    assert report["decision"] == "invalid_input"
    assert report["driver_action_required"] is False
    assert report["would_merge"] is False


def test_ineligible_promotion_has_no_driver_action() -> None:
    report = _report(events=_full_events()[:2])

    assert report["decision"] == "promotion_driver_lag_absent"
    assert report["driver_action_required"] is False
    assert report["required_driver_actions"] == []
    assert report["lag_reason"] == "promotion_not_eligible"
    assert report["promotion"]["eligible"] is False
    assert "missing exact-head RCO_PASS from recognized non-author RCO" in report[
        "promotion"
    ]["reasons"]


def test_closed_pr_has_no_driver_action_and_is_not_promotion_eligible() -> None:
    report = _report(status=_status(state="MERGED", isDraft=False))

    assert report["decision"] == "promotion_driver_lag_absent"
    assert report["driver_action_required"] is False
    assert report["required_driver_actions"] == []
    assert report["lag_reason"] == "promotion_not_eligible"
    assert report["promotion"]["eligible"] is False


def test_report_does_not_echo_diff_text_or_changed_paths() -> None:
    report = _report()
    encoded = json.dumps(report, sort_keys=True)

    assert "diff_text" not in encoded
    assert "changed_paths" not in encoded
    assert "+ def helper()" not in encoded


def test_invalid_verifier_input_is_fail_closed() -> None:
    report = build_promotion_driver_lag_report(
        pr_status={**_status(), "changed_paths": "tools/not-a-list.py"},
        events=_full_events(),
        task_id=TASK,
        head=HEAD,
        origin_main_sha=BASE,
        author_agent="codex-lead-1",
    )

    assert report["ok"] is False
    assert report["decision"] == "invalid_input"
    assert report["driver_action_required"] is False
    assert report["would_merge"] is False
    assert report["external_effect"] is False


def test_cli_outputs_json_report_without_writing(tmp_path: Path) -> None:
    status_path = tmp_path / "status.json"
    status_path.write_text(json.dumps(_status(), sort_keys=True), encoding="utf-8")
    bridge_root = tmp_path / ".agent-bridge"
    shared = bridge_root / "shared"
    shared.mkdir(parents=True)
    events_path = _events_path(shared, _full_events())

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--pr-status-file",
            str(status_path),
            "--events",
            str(events_path),
            "--bridge-root",
            str(bridge_root),
            "--task-id",
            TASK,
            "--head",
            HEAD,
            "--pr-number",
            "934",
            "--origin-main-sha",
            BASE,
            "--author-agent",
            "fable-5",
            "--json",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    report = json.loads(result.stdout)
    assert report["decision"] == "promotion_driver_lag_detected"
    assert report["driver_action_required"] is True
    assert report["required_driver_actions"] == ["undraft", "merge"]
    assert report["read_only"] is True


def test_cli_default_events_uses_runtime_bridge_root_env_from_other_cwd(
    tmp_path: Path,
) -> None:
    status_path = tmp_path / "status.json"
    status_path.write_text(json.dumps(_status(), sort_keys=True), encoding="utf-8")
    bridge_root = tmp_path / "runtime" / ".agent-bridge"
    events_path = bridge_root / "shared" / "events.jsonl"
    events_path.parent.mkdir(parents=True)
    events_path.write_text(
        "\n".join(json.dumps(event, sort_keys=True) for event in _full_events()),
        encoding="utf-8",
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
            "--pr-status-file",
            str(status_path),
            "--task-id",
            TASK,
            "--head",
            HEAD,
            "--pr-number",
            "934",
            "--origin-main-sha",
            BASE,
            "--author-agent",
            "fable-5",
            "--json",
        ],
        cwd=str(other_cwd),
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["decision"] == "promotion_driver_lag_detected"
    assert report["driver_action_required"] is True
