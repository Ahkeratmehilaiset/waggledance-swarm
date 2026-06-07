# SPDX-License-Identifier: BUSL-1.1
"""Tests for tools/check_bridge_changes_requested.py."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "check_bridge_changes_requested.py"

sys.path.insert(0, str(ROOT))

from tools.check_bridge_changes_requested import (  # noqa: E402
    check_bridge_clear_to_merge,
)


def _seed_bridge(tmp_path: Path, events: list[dict]) -> Path:
    bridge_root = tmp_path / ".agent-bridge"
    shared = bridge_root / "shared"
    shared.mkdir(parents=True)
    events_path = shared / "events.jsonl"
    with events_path.open("w", encoding="utf-8", newline="\n") as fh:
        for event in events:
            fh.write(json.dumps(event) + "\n")
    return bridge_root


def _event(ts_utc: str, agent: str, type_: str, status: str, task_id: str = "T") -> dict:
    return {
        "ts_utc": ts_utc,
        "agent": agent,
        "type": type_,
        "task_id": task_id,
        "status": status,
        "severity": "",
        "to": "",
        "message": "",
        "paths": [],
        "write_scope": [],
        "run_id": "",
        "pid": 0,
        "cwd": "",
    }


def test_clear_when_no_peer_signal_for_task() -> None:
    events = [_event("2026-05-21T10:00:00Z", "claude", "handoff", "rco_requested")]
    result = check_bridge_clear_to_merge(
        events=events, task_id="T", merging_agent="claude"
    )
    assert result["clear_to_merge"] is True
    assert result["latest_blocking_event"] is None


def test_blocked_when_peer_changes_requested_is_latest() -> None:
    events = [
        _event("2026-05-21T10:00:00Z", "claude", "handoff", "rco_requested"),
        _event("2026-05-21T10:05:00Z", "codex", "decision", "changes_requested"),
    ]
    result = check_bridge_clear_to_merge(
        events=events, task_id="T", merging_agent="claude"
    )
    assert result["clear_to_merge"] is False
    assert result["latest_blocking_event"]["agent"] == "codex"
    assert result["latest_blocking_event"]["status"] == "changes_requested"


def test_cleared_when_peer_approves_after_earlier_block() -> None:
    events = [
        _event("2026-05-21T10:00:00Z", "claude", "handoff", "rco_requested"),
        _event("2026-05-21T10:05:00Z", "codex", "decision", "changes_requested"),
        _event("2026-05-21T10:30:00Z", "codex", "decision", "rco_pass"),
    ]
    result = check_bridge_clear_to_merge(
        events=events, task_id="T", merging_agent="claude"
    )
    assert result["clear_to_merge"] is True
    assert result["latest_approval_event"]["status"] == "rco_pass"


def test_build_consensus_pass_clears_same_peer_block() -> None:
    events = [
        _event("2026-06-07T03:30:00Z", "codex-tools-1", "finding", "changes_requested"),
        _event("2026-06-07T03:54:00Z", "codex-tools-1", "decision", "build_consensus_pass"),
    ]
    result = check_bridge_clear_to_merge(
        events=events, task_id="T", merging_agent="codex-lead-1"
    )
    assert result["clear_to_merge"] is True
    assert result["latest_approval_event"]["status"] == "build_consensus_pass"


def test_clear_preflight_status_with_block_context_does_not_override_approval() -> None:
    events = [
        _event(
            "2026-06-07T17:38:40Z",
            "codex-tools-1",
            "decision",
            "build_consensus_pass",
        ),
        _event(
            "2026-06-07T17:39:47Z",
            "codex-tools-1",
            "test",
            "peer_block_preflight_clear_after_tools_build_consensus",
        ),
    ]
    result = check_bridge_clear_to_merge(
        events=events,
        task_id="T",
        merging_agent="codex-lead-1",
        pr_number=965,
    )
    assert result["clear_to_merge"] is True
    assert result["latest_blocking_event"] is None
    assert result["latest_approval_event"]["status"] == "build_consensus_pass"


def test_build_consensus_pass_typo_does_not_clear_same_peer_block() -> None:
    events = [
        _event("2026-06-07T03:30:00Z", "codex-tools-1", "finding", "changes_requested"),
        _event("2026-06-07T03:54:00Z", "codex-tools-1", "decision", "build_consensus_passhead"),
    ]
    result = check_bridge_clear_to_merge(
        events=events, task_id="T", merging_agent="codex-lead-1"
    )
    assert result["clear_to_merge"] is False
    assert result["latest_blocking_event"]["status"] == "changes_requested"
    assert result["latest_approval_event"] is None


def test_done_approved_ci_green_clears_same_peer_block() -> None:
    events = [
        _event("2026-06-06T17:53:00Z", "codex-tools-1", "finding", "changes_requested"),
        _event("2026-06-06T18:12:00Z", "codex-tools-1", "done", "approved_ci_green"),
    ]
    result = check_bridge_clear_to_merge(
        events=events, task_id="T", merging_agent="codex-lead-1"
    )
    assert result["clear_to_merge"] is True
    assert result["latest_approval_event"]["type"] == "done"
    assert result["latest_approval_event"]["status"] == "approved_ci_green"


def test_plain_done_does_not_clear_same_peer_block() -> None:
    events = [
        _event("2026-06-06T17:53:00Z", "codex-tools-1", "finding", "changes_requested"),
        _event("2026-06-06T18:12:00Z", "codex-tools-1", "done", "done"),
    ]
    result = check_bridge_clear_to_merge(
        events=events, task_id="T", merging_agent="codex-lead-1"
    )
    assert result["clear_to_merge"] is False
    assert result["latest_blocking_event"]["status"] == "changes_requested"
    assert result["latest_approval_event"] is None


def test_done_acknowledged_does_not_clear_same_peer_block() -> None:
    events = [
        _event("2026-06-06T17:53:00Z", "codex-tools-1", "finding", "changes_requested"),
        _event("2026-06-06T18:12:00Z", "codex-tools-1", "done", "acknowledged"),
    ]
    result = check_bridge_clear_to_merge(
        events=events, task_id="T", merging_agent="codex-lead-1"
    )
    assert result["clear_to_merge"] is False
    assert result["latest_blocking_event"]["status"] == "changes_requested"
    assert result["latest_approval_event"] is None


def test_different_peer_approval_does_not_clear_block() -> None:
    events = [
        _event("2026-05-21T10:00:00Z", "claude", "handoff", "rco_requested"),
        _event("2026-05-21T10:05:00Z", "codex", "decision", "changes_requested"),
        _event("2026-05-21T10:30:00Z", "mynhos", "decision", "rco_pass"),
    ]
    result = check_bridge_clear_to_merge(
        events=events, task_id="T", merging_agent="claude"
    )
    assert result["clear_to_merge"] is False
    assert result["latest_blocking_event"]["agent"] == "codex"


def test_prefixed_rco_pass_clears_same_peer_block() -> None:
    events = [
        _event("2026-05-21T10:00:00Z", "claude", "handoff", "rco_requested"),
        _event("2026-05-21T10:05:00Z", "codex", "decision", "changes_requested"),
        _event("2026-05-21T10:30:00Z", "codex", "decision", "rco_pass_pr529"),
    ]
    result = check_bridge_clear_to_merge(
        events=events, task_id="T", merging_agent="claude"
    )
    assert result["clear_to_merge"] is True
    assert result["latest_approval_event"]["status"] == "rco_pass_pr529"


def test_prefixed_changes_requested_status_blocks() -> None:
    events = [
        _event(
            "2026-05-21T10:05:00Z",
            "codex",
            "decision",
            "rco_changes_requested_pr530",
        ),
    ]
    result = check_bridge_clear_to_merge(
        events=events, task_id="T", merging_agent="claude"
    )
    assert result["clear_to_merge"] is False
    assert result["latest_blocking_event"]["status"] == "rco_changes_requested_pr530"


def test_changes_requested_clear_status_still_blocks() -> None:
    events = [
        _event(
            "2026-06-07T17:39:47Z",
            "codex-tools-1",
            "test",
            "changes_requested_clear_preflight",
        ),
    ]
    result = check_bridge_clear_to_merge(
        events=events, task_id="T", merging_agent="codex-lead-1"
    )
    assert result["clear_to_merge"] is False
    assert result["latest_blocking_event"]["status"] == "changes_requested_clear_preflight"


def test_veto_statuses_with_negation_words_still_block() -> None:
    for status in [
        "changes_requested_do_not_merge",
        "blocked_no_fix_yet",
        "block_without_fix",
        "rco_block_cleared",
    ]:
        result = check_bridge_clear_to_merge(
            events=[
                _event(
                    "2026-06-07T17:39:47Z",
                    "codex-tools-1",
                    "test",
                    status,
                )
            ],
            task_id="T",
            merging_agent="codex-lead-1",
        )
        assert result["clear_to_merge"] is False
        assert result["latest_blocking_event"]["status"] == status


def test_append_order_not_timestamp_string_order_decides_latest_signal() -> None:
    events = [
        _event("2026-05-21T10:05:00Z", "codex", "decision", "rco_pass"),
        _event("2026-05-21T10:05:00.100000Z", "codex", "decision", "changes_requested"),
    ]
    result = check_bridge_clear_to_merge(
        events=events, task_id="T", merging_agent="claude"
    )
    assert result["clear_to_merge"] is False
    assert result["latest_blocking_event"]["status"] == "changes_requested"


def test_self_events_are_ignored() -> None:
    """The merging agent's own decisions should not count as peer block."""
    events = [
        _event("2026-05-21T10:00:00Z", "claude", "decision", "changes_requested"),
    ]
    result = check_bridge_clear_to_merge(
        events=events, task_id="T", merging_agent="claude"
    )
    assert result["clear_to_merge"] is True


def test_other_task_blocks_do_not_affect_this_task() -> None:
    events = [
        _event(
            "2026-05-21T10:00:00Z", "codex", "decision", "changes_requested",
            task_id="other-task",
        ),
    ]
    result = check_bridge_clear_to_merge(
        events=events, task_id="T", merging_agent="claude"
    )
    assert result["clear_to_merge"] is True


def test_pr_scoped_finding_blocks_when_task_id_differs() -> None:
    events = [
        _event(
            "2026-05-27T07:31:39Z",
            "codex-lead-1",
            "finding",
            "confirmed_bug_blocks_merge",
            task_id="pr701-bridge-stale-ack-close-readonly-review-2026-05-27",
        )
        | {"message": "Lead BLOCK PR #701 exact head abc123."},
    ]
    result = check_bridge_clear_to_merge(
        events=events,
        task_id="fix-bridge-next-action-stale-ack-requester-close-2026-05-27",
        merging_agent="codex-tools-1",
        pr_number=701,
    )
    assert result["clear_to_merge"] is False
    assert result["latest_blocking_event"]["agent"] == "codex-lead-1"
    assert result["latest_blocking_event"]["status"] == "confirmed_bug_blocks_merge"


def test_pr_scoped_same_peer_approval_clears_older_task_id_mismatch_block() -> None:
    events = [
        _event(
            "2026-05-27T07:31:39Z",
            "claude-rco-1",
            "finding",
            "blocked",
            task_id="pr701-bridge-stale-ack-close-readonly-review-2026-05-27",
        )
        | {"message": "RCO BLOCK PR #701 exact head abc123."},
        _event(
            "2026-05-27T07:55:00Z",
            "claude-rco-1",
            "decision",
            "rco_pass",
            task_id="pr701-bridge-stale-ack-close-readonly-review-2026-05-27",
        )
        | {"payload": {"pr": 701}},
    ]
    result = check_bridge_clear_to_merge(
        events=events,
        task_id="fix-bridge-next-action-stale-ack-requester-close-2026-05-27",
        merging_agent="codex-tools-1",
        pr_number=701,
    )
    assert result["clear_to_merge"] is True
    assert result["latest_approval_event"]["status"] == "rco_pass"


def test_no_blocker_status_does_not_block_pr_scoped_preflight() -> None:
    events = [
        _event(
            "2026-05-27T11:13:27Z",
            "codex-lead-1",
            "decision",
            "lead_no_blocker_rco_pending",
            task_id="pr706-done-status-close-rco-request-2026-05-27",
        )
        | {"message": "No lead blocker; hold for RCO_PASS."},
        _event(
            "2026-05-27T11:24:04Z",
            "claude-rco-1",
            "decision",
            "rco_pass",
            task_id="pr706-done-status-close-rco-request-2026-05-27",
        )
        | {"message": "RCO_PASS PR #706 exact head 9fb2f18c."},
    ]
    result = check_bridge_clear_to_merge(
        events=events,
        task_id="pr706-done-status-close-rco-request-2026-05-27",
        merging_agent="codex-tools-1",
        pr_number=706,
    )
    assert result["clear_to_merge"] is True
    assert result["latest_blocking_event"] is None
    assert result["latest_approval_event"]["status"] == "rco_pass"


def test_task_id_mismatch_without_pr_number_stays_out_of_scope() -> None:
    events = [
        _event(
            "2026-05-27T07:31:39Z",
            "codex-lead-1",
            "finding",
            "confirmed_bug_blocks_merge",
            task_id="pr701-bridge-stale-ack-close-readonly-review-2026-05-27",
        )
        | {"message": "Lead BLOCK PR #701 exact head abc123."},
    ]
    result = check_bridge_clear_to_merge(
        events=events,
        task_id="fix-bridge-next-action-stale-ack-requester-close-2026-05-27",
        merging_agent="codex-tools-1",
    )
    assert result["clear_to_merge"] is True


def test_unrelated_event_types_are_ignored() -> None:
    """Heartbeats, claims, handoffs etc. should not affect the gate."""
    events = [
        _event("2026-05-21T10:00:00Z", "codex", "heartbeat", "active"),
        _event("2026-05-21T10:01:00Z", "codex", "claim", "active"),
    ]
    result = check_bridge_clear_to_merge(
        events=events, task_id="T", merging_agent="claude"
    )
    assert result["clear_to_merge"] is True


def test_cli_smoke_returns_exit_3_on_block(tmp_path: Path) -> None:
    bridge_root = _seed_bridge(
        tmp_path,
        [
            _event("2026-05-21T10:00:00Z", "claude", "handoff", "rco_requested"),
            _event("2026-05-21T10:05:00Z", "codex", "decision", "changes_requested"),
        ],
    )
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--task-id",
            "T",
            "--from-agent",
            "claude",
            "--bridge-root",
            str(bridge_root),
            "--json",
        ],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 3
    payload = json.loads(result.stdout)
    assert payload["clear_to_merge"] is False
    assert payload["decision"] == "blocked"


def test_cli_smoke_returns_exit_0_when_clear(tmp_path: Path) -> None:
    bridge_root = _seed_bridge(
        tmp_path,
        [_event("2026-05-21T10:00:00Z", "claude", "handoff", "rco_requested")],
    )
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--task-id",
            "T",
            "--from-agent",
            "claude",
            "--bridge-root",
            str(bridge_root),
            "--json",
        ],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["clear_to_merge"] is True


def test_cli_smoke_fails_closed_on_invalid_jsonl(tmp_path: Path) -> None:
    bridge_root = tmp_path / ".agent-bridge"
    shared = bridge_root / "shared"
    shared.mkdir(parents=True)
    (shared / "events.jsonl").write_text("{not-json}\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--task-id",
            "T",
            "--from-agent",
            "claude",
            "--bridge-root",
            str(bridge_root),
            "--json",
        ],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["decision"] == "invalid_events_file"


def test_cli_smoke_fails_closed_on_non_object_jsonl(tmp_path: Path) -> None:
    bridge_root = tmp_path / ".agent-bridge"
    shared = bridge_root / "shared"
    shared.mkdir(parents=True)
    (shared / "events.jsonl").write_text("[]\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--task-id",
            "T",
            "--from-agent",
            "claude",
            "--bridge-root",
            str(bridge_root),
            "--json",
        ],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["decision"] == "invalid_events_file"
    assert "JSON object" in payload["error"]


def test_cli_smoke_reproduces_pr_527_race_pattern(tmp_path: Path) -> None:
    """Reproduce the 2026-05-21 PR #527 race: Codex blocked at 13:32, claude
    autonomous-merged at 13:34. This tool must catch that block.
    """
    bridge_root = _seed_bridge(
        tmp_path,
        [
            _event(
                "2026-05-21T13:21:57Z",
                "claude",
                "handoff",
                "rco_requested",
                task_id="idle-protocol-late-round-invariant-2026-05-21",
            ),
            _event(
                "2026-05-21T13:32:05Z",
                "codex",
                "decision",
                "changes_requested",
                task_id="idle-protocol-late-round-invariant-2026-05-21",
            ),
        ],
    )
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--task-id",
            "idle-protocol-late-round-invariant-2026-05-21",
            "--from-agent",
            "claude",
            "--bridge-root",
            str(bridge_root),
            "--json",
        ],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 3, result.stdout
    payload = json.loads(result.stdout)
    assert payload["latest_blocking_event"]["agent"] == "codex"


def test_block_is_type_agnostic_fail_closed() -> None:
    # A veto posted as type=blocked/status=blocked (not a decision/finding)
    # must still register as a peer block, after an earlier approval, rather
    # than being dropped by the event-type filter (fail-closed veto path).
    events = [
        _event("2026-05-29T13:00:00Z", "claude-rco-1", "decision", "rco_pass"),
        _event("2026-05-29T13:01:00Z", "claude-rco-1", "blocked", "blocked"),
    ]
    result = check_bridge_clear_to_merge(
        events=events, task_id="T", merging_agent="codex-lead-1"
    )
    assert result["clear_to_merge"] is False
    assert result["latest_blocking_event"]["agent"] == "claude-rco-1"
