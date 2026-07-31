# SPDX-License-Identifier: BUSL-1.1
"""Tests for tools/check_bridge_changes_requested.py."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "check_bridge_changes_requested.py"

sys.path.insert(0, str(ROOT))

import tools.check_bridge_changes_requested as gate_module  # noqa: E402
from tools.check_bridge_changes_requested import (  # noqa: E402
    check_bridge_clear_to_merge as _raw_check_bridge_clear_to_merge,
    _read_events,
)
import waggledance.core.bridge_identity_registry as identity_registry_module  # noqa: E402

AGENT_UUIDS = {
    "claude-rco-1": "2b2f6ff9-06c2-4ec8-b526-f10071ce7103",
    "claude-rco-2": "76739997-0058-41a2-8514-78ff295537aa",
    "codex-lead-1": "d3c9d1d1-96a9-4eb8-a8e2-6f05f9d1a101",
    "codex-tools-1": "7a8af68d-20bc-4598-9953-23c5dd98b102",
    "fable-5": "f8b1e5c0-3d2a-4e6b-9c1f-7a0d5e2b4c80",
}
TASK_SCOPE_TEST_PR_NUMBER = 2_147_483_647


def check_bridge_clear_to_merge(**kwargs):
    """Exercise task-scope algorithms with a positive, nonmatching PR."""
    kwargs.setdefault("pr_number", TASK_SCOPE_TEST_PR_NUMBER)
    return _raw_check_bridge_clear_to_merge(**kwargs)


def _seed_bridge(tmp_path: Path, events: list[dict]) -> Path:
    bridge_root = tmp_path / ".agent-bridge"
    shared = bridge_root / "shared"
    shared.mkdir(parents=True)
    events_path = shared / "events.jsonl"
    with events_path.open("w", encoding="utf-8", newline="\n") as fh:
        for event in events:
            fh.write(json.dumps(event) + "\n")
    return bridge_root


def test_read_events_skips_bare_null_event_line(tmp_path: Path) -> None:
    events_path = tmp_path / "events.jsonl"
    event = _event("2026-06-20T13:12:00Z", "codex-lead-1", "message", "seen")
    events_path.write_text(
        "\n".join(["null", json.dumps(event)]),
        encoding="utf-8",
    )

    assert _read_events(events_path) == [event]


def _event(ts_utc: str, agent: str, type_: str, status: str, task_id: str = "T") -> dict:
    event = {
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
    if agent in AGENT_UUIDS:
        event["agent_uuid"] = AGENT_UUIDS[agent]
    return event


def test_clear_when_no_peer_signal_for_task() -> None:
    events = [_event("2026-05-21T10:00:00Z", "claude", "handoff", "rco_requested")]
    result = check_bridge_clear_to_merge(
        events=events, task_id="T", merging_agent="claude"
    )
    assert result["clear_to_merge"] is True
    assert result["latest_blocking_event"] is None


def test_missing_default_identity_registry_refuses_peer_gate(
    monkeypatch,
    tmp_path: Path,
) -> None:
    missing_registry = tmp_path / "missing_bridge_identity_registry.json"
    monkeypatch.setattr(
        identity_registry_module,
        "DEFAULT_BRIDGE_IDENTITY_REGISTRY_PATH",
        missing_registry,
    )

    result = check_bridge_clear_to_merge(
        events=[_event("2026-06-11T16:28:40Z", "codex", "decision", "rco_pass")],
        task_id="T",
        merging_agent="claude",
    )

    assert result["ok"] is False
    assert result["clear_to_merge"] is False
    assert result["decision"] == "invalid_identity_registry"
    assert "bridge identity registry not found" in result["error"]


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


def test_recognized_rco_uuid_mismatch_block_latches_fail_closed() -> None:
    # Contract fix (bridge audit 2026-07-02): identity binding stops FORGED
    # approvals; silently dropping an unverified VETO from a recognized-RCO
    # NAME inverted "veto outranks pass" under registry drift. Block-shaped
    # events from recognized RCO names now latch even when unverified.
    events = [
        _event(
            "2026-06-11T16:28:40Z",
            "claude-rco-2",
            "decision",
            "changes_requested",
        )
        | {"agent_uuid": AGENT_UUIDS["fable-5"]},
    ]

    result = check_bridge_clear_to_merge(
        events=events, task_id="T", merging_agent="codex-lead-1"
    )

    assert result["clear_to_merge"] is False
    assert result["unverified_rco_block_events"][0]["agent"] == "claude-rco-2"
    assert (
        result["unverified_rco_block_events"][0]["identity_binding_status"]
        == "mismatch_uuid"
    )
    assert result["unverified_rco_block_events"][0][
        "unverified_veto_fail_closed"
    ] is True


def test_cross_identity_drift_cannot_bypass_rco_veto() -> None:
    # The re-analysis scenario: rco-1's veto with drifted uuid + rco-2's
    # VERIFIED pass. Before the fix the drifted veto was dropped and the
    # verified pass carried the merge past an "absolute" veto.
    events = [
        _event("2026-06-11T16:00:00Z", "claude-rco-1", "finding", "blocking")
        | {"agent_uuid": "00000000-dead-beef-0000-000000000000"},
        _event("2026-06-11T16:10:00Z", "claude-rco-2", "decision", "rco_pass"),
    ]

    result = check_bridge_clear_to_merge(
        events=events, task_id="T", merging_agent="codex-lead-1"
    )

    assert result["clear_to_merge"] is False
    assert result["unverified_rco_block_events"][0]["agent"] == "claude-rco-1"


def test_unverified_rco_pass_still_gets_no_credit() -> None:
    # Asymmetry: an unverified APPROVAL stays ignored (forge resistance).
    events = [
        _event("2026-06-11T16:00:00Z", "claude-rco-2", "decision", "rco_pass")
        | {"agent_uuid": AGENT_UUIDS["fable-5"]},
    ]

    result = check_bridge_clear_to_merge(
        events=events, task_id="T", merging_agent="codex-lead-1"
    )

    assert result["clear_to_merge"] is True
    assert result["latest_approval_event"] is None
    assert result["ignored_identity_mismatch_events"][0]["agent"] == "claude-rco-2"


def test_unverified_clear_cannot_lift_verified_rco_veto() -> None:
    # Asymmetry: an unverified CLEAR must never lift a real veto.
    events = [
        _event("2026-06-11T16:00:00Z", "claude-rco-2", "finding", "blocking"),
        _event("2026-06-11T16:10:00Z", "claude-rco-2", "decision", "approved")
        | {"agent_uuid": AGENT_UUIDS["fable-5"]},
    ]

    result = check_bridge_clear_to_merge(
        events=events, task_id="T", merging_agent="codex-lead-1"
    )

    assert result["clear_to_merge"] is False


def test_later_verified_pass_clears_own_unverified_veto() -> None:
    # Recoverability: after registry drift is fixed, the SAME identity's
    # later VERIFIED signal supersedes its unverified veto (latest wins).
    events = [
        _event("2026-06-11T16:00:00Z", "claude-rco-2", "finding", "blocking")
        | {"agent_uuid": AGENT_UUIDS["fable-5"]},
        _event("2026-06-11T16:10:00Z", "claude-rco-2", "decision", "rco_pass"),
    ]

    result = check_bridge_clear_to_merge(
        events=events, task_id="T", merging_agent="codex-lead-1"
    )

    assert result["clear_to_merge"] is True


def test_non_rco_peer_uuid_mismatch_block_stays_ignored() -> None:
    # Scoping: the fail-closed latch is only for the recognized-RCO names
    # whose veto the contract calls absolute; other peers' unverified blocks
    # remain ignored as before.
    events = [
        _event(
            "2026-06-11T16:28:40Z",
            "codex-tools-1",
            "decision",
            "changes_requested",
        )
        | {"agent_uuid": AGENT_UUIDS["fable-5"]},
    ]

    result = check_bridge_clear_to_merge(
        events=events, task_id="T", merging_agent="codex-lead-1"
    )

    assert result["clear_to_merge"] is True
    assert result["ignored_identity_mismatch_events"][0]["agent"] == "codex-tools-1"


def test_author_rco_veto_still_blocks_when_author_agent_supplied() -> None:
    events = [
        _event(
            "2026-07-01T01:00:00Z",
            "claude-rco-2",
            "finding",
            "changes_requested",
        ),
    ]

    result = check_bridge_clear_to_merge(
        events=events,
        task_id="T",
        merging_agent="codex-lead-1",
        author_agent="claude-rco-2",
    )

    assert result["clear_to_merge"] is False
    assert result["latest_blocking_event"]["agent"] == "claude-rco-2"
    assert result["latest_blocking_event"]["status"] == "changes_requested"
    assert result["ignored_author_events"] == []


def test_author_rco_clear_can_clear_own_prior_veto() -> None:
    events = [
        _event(
            "2026-07-01T01:00:00Z",
            "claude-rco-2",
            "finding",
            "changes_requested",
        ),
        _event(
            "2026-07-01T01:05:00Z",
            "claude-rco-2",
            "decision",
            "changes_requested_resolved",
        ),
    ]

    result = check_bridge_clear_to_merge(
        events=events,
        task_id="T",
        merging_agent="codex-lead-1",
        author_agent="claude-rco-2",
    )

    assert result["clear_to_merge"] is True
    assert result["latest_blocking_event"] is None
    assert result["latest_approval_event"] is None
    assert result["ignored_author_events"] == []


def test_author_rco_approval_is_ignored_when_author_agent_supplied() -> None:
    events = [
        _event(
            "2026-07-01T01:00:00Z",
            "claude-rco-2",
            "decision",
            "rco_pass",
        ),
    ]

    result = check_bridge_clear_to_merge(
        events=events,
        task_id="T",
        merging_agent="codex-lead-1",
        author_agent="claude-rco-2",
    )

    assert result["clear_to_merge"] is True
    assert result["latest_approval_event"] is None
    assert result["ignored_author_events"][0]["agent"] == "claude-rco-2"
    assert result["ignored_author_events"][0]["status"] == "rco_pass"


def test_non_author_rco_signal_still_blocks_when_author_agent_supplied() -> None:
    events = [
        _event(
            "2026-07-01T01:00:00Z",
            "claude-rco-1",
            "finding",
            "content_pass_but_type_finding",
        ),
    ]

    result = check_bridge_clear_to_merge(
        events=events,
        task_id="T",
        merging_agent="codex-lead-1",
        author_agent="claude-rco-2",
    )

    assert result["clear_to_merge"] is False
    assert result["latest_blocking_event"]["agent"] == "claude-rco-1"
    assert result["latest_blocking_event"]["type"] == "finding"


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


def test_coordination_block_clear_request_does_not_override_approval() -> None:
    events = [
        _event(
            "2026-06-21T18:24:35Z",
            "codex-lead-1",
            "decision",
            "build_consensus_pass",
        ),
        _event(
            "2026-06-21T18:26:22Z",
            "codex-lead-1",
            "wake_request",
            "tools_peer_block_clear_needed_after_reattribution",
        ),
    ]
    result = check_bridge_clear_to_merge(
        events=events,
        task_id="T",
        merging_agent="codex-tools-1",
        pr_number=1364,
    )
    assert result["clear_to_merge"] is True
    assert result["latest_blocking_event"] is None
    assert result["latest_approval_event"]["status"] == "build_consensus_pass"


def test_classifier_artifact_no_real_veto_status_does_not_block() -> None:
    events = [
        _event(
            "2026-06-21T18:28:55Z",
            "claude-rco-1",
            "message",
            "peer_block_is_g4_classifier_artifact_no_real_veto",
        ),
    ]
    result = check_bridge_clear_to_merge(
        events=events,
        task_id="T",
        merging_agent="codex-tools-1",
        pr_number=1364,
    )
    assert result["clear_to_merge"] is True
    assert result["latest_blocking_event"] is None


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


def test_concurrence_changes_requested_status_does_not_block() -> None:
    events = [
        _event(
            "2026-06-19T03:00:00Z",
            "codex",
            "finding",
            "changes_requested_concurrence",
            task_id="wd/security/bridge-changes-requested-freetext-classifier-20260619",
        ),
    ]
    result = check_bridge_clear_to_merge(
        events=events,
        task_id="wd/security/bridge-changes-requested-freetext-classifier-20260619",
        merging_agent="codex-tools-1",
    )
    assert result["clear_to_merge"] is True
    assert result["latest_blocking_event"] is None


def test_changes_requested_resolution_statuses_are_non_blocking() -> None:
    events = [
        _event(
            "2026-06-19T03:00:00Z",
            "codex",
            "decision",
            "changes_requested_resolved",
        ),
        _event(
            "2026-06-19T03:01:00Z",
            "codex",
            "decision",
            "rco_changes_requested_cleared",
            task_id="T",
        ),
        _event(
            "2026-06-19T03:02:00Z",
            "codex",
            "decision",
            "changes_requested_retracted",
            task_id="T",
        ),
        _event(
            "2026-06-19T03:03:00Z",
            "codex",
            "decision",
            "rco_changes_requested_withdrawn",
            task_id="T",
        ),
    ]
    result = check_bridge_clear_to_merge(
        events=events, task_id="T", merging_agent="codex-lead-1"
    )
    assert result["clear_to_merge"] is True
    assert result["latest_blocking_event"] is None


def test_no_changes_requested_supersedes_same_agent_block() -> None:
    events = [
        _event(
            "2026-06-19T08:00:00Z",
            "claude-rco-1",
            "decision",
            "changes_requested",
        ),
        _event(
            "2026-06-19T08:01:00Z",
            "claude-rco-1",
            "decision",
            "no_changes_requested",
        ),
    ]
    result = check_bridge_clear_to_merge(
        events=events, task_id="T", merging_agent="codex-tools-1"
    )
    assert result["clear_to_merge"] is True
    assert result["latest_blocking_event"] is None
    assert result["latest_approval_event"] is None


def test_changes_requested_clear_status_supersedes_same_agent_block() -> None:
    events = [
        _event(
            "2026-06-19T08:00:00Z",
            "claude-rco-1",
            "decision",
            "changes_requested",
        ),
        _event(
            "2026-06-19T08:01:00Z",
            "claude-rco-1",
            "decision",
            "rco_changes_requested_cleared",
        ),
    ]
    result = check_bridge_clear_to_merge(
        events=events, task_id="T", merging_agent="codex-tools-1"
    )
    assert result["clear_to_merge"] is True
    assert result["latest_blocking_event"] is None
    assert result["latest_approval_event"] is None


def test_approved_waiver_block_cleared_supersedes_same_agent_block() -> None:
    events = [
        _event(
            "2026-06-21T18:00:00Z",
            "codex-tools-1",
            "blocked",
            "merge_blocked_operator_or_driver_waiver_required",
        ),
        _event(
            "2026-06-21T18:01:00Z",
            "codex-tools-1",
            "decision",
            "approved_waiver_block_cleared",
        ),
    ]
    result = check_bridge_clear_to_merge(
        events=events, task_id="T", merging_agent="codex-lead-1"
    )
    assert result["clear_to_merge"] is True
    assert result["latest_blocking_event"] is None
    assert result["latest_approval_event"] is None


def test_clear_status_does_not_supersede_other_agent_block() -> None:
    events = [
        _event(
            "2026-06-19T08:00:00Z",
            "claude-rco-1",
            "decision",
            "changes_requested",
        ),
        _event(
            "2026-06-19T08:01:00Z",
            "claude-rco-2",
            "decision",
            "no_changes_requested",
        ),
    ]
    result = check_bridge_clear_to_merge(
        events=events, task_id="T", merging_agent="codex-tools-1"
    )
    assert result["clear_to_merge"] is False
    assert result["latest_blocking_event"]["agent"] == "claude-rco-1"
    assert result["latest_blocking_event"]["status"] == "changes_requested"


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


def test_no_changes_requested_status_does_not_block() -> None:
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
            "no_changes_requested",
        ),
    ]
    result = check_bridge_clear_to_merge(
        events=events, task_id="T", merging_agent="codex-lead-1"
    )
    assert result["clear_to_merge"] is True
    assert result["latest_blocking_event"] is None
    assert result["latest_approval_event"]["status"] == "build_consensus_pass"


def test_no_changes_requested_approved_status_does_not_block() -> None:
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
            "no_changes_requested_approved",
        ),
    ]
    result = check_bridge_clear_to_merge(
        events=events, task_id="T", merging_agent="codex-lead-1"
    )
    assert result["clear_to_merge"] is True
    assert result["latest_blocking_event"] is None
    assert result["latest_approval_event"]["status"] == "build_consensus_pass"


def test_message_status_correction_with_changes_requested_token_does_not_block() -> None:
    events = [
        _event(
            "2026-06-21T01:57:31Z",
            "codex-lead-1",
            "message",
            "changes_requested_payload_corrected",
        ),
    ]
    result = check_bridge_clear_to_merge(
        events=events, task_id="T", merging_agent="codex-tools-1"
    )

    assert result["clear_to_merge"] is True
    assert result["latest_blocking_event"] is None


def test_non_authoritative_exact_changes_requested_status_does_not_block() -> None:
    for event_type, status in [
        ("message", "changes_requested"),
        ("handoff", "changes_requested"),
        ("status", "changes_requested"),
        ("wake_request", "changes_requested"),
    ]:
        result = check_bridge_clear_to_merge(
            events=[
                _event(
                    "2026-06-21T01:57:31Z",
                    "codex-lead-1",
                    event_type,
                    status,
                )
            ],
            task_id="T",
            merging_agent="codex-tools-1",
        )

        assert result["clear_to_merge"] is True
        assert result["latest_blocking_event"] is None


def test_authoritative_exact_block_statuses_still_block() -> None:
    for event_type, status in [
        ("decision", "changes_requested"),
        ("finding", "changes_requested"),
        ("rco_review", "rco_block"),
        ("blocked", "blocked"),
    ]:
        result = check_bridge_clear_to_merge(
            events=[
                _event(
                    "2026-06-21T01:57:31Z",
                    "codex-lead-1",
                    event_type,
                    status,
                )
            ],
            task_id="T",
            merging_agent="codex-tools-1",
        )

        assert result["clear_to_merge"] is False
        assert result["latest_blocking_event"]["status"] == status


def test_non_authoritative_decorated_veto_statuses_do_not_block() -> None:
    for event_type, status in [
        ("handoff", "changes_requested_do_not_merge"),
        ("handoff", "rco_block_critical"),
        ("handoff", "blocked_no_fix_yet"),
        ("handoff", "block_without_fix"),
        ("message", "rco_changes_requested_pr530"),
        ("message", "changes_requested_do_not_merge"),
        ("message", "changes_requested_critical"),
        ("message", "rco_block_critical"),
        ("message", "blocked_no_fix_yet"),
        ("message", "block_without_fix"),
    ]:
        result = check_bridge_clear_to_merge(
            events=[
                _event(
                    "2026-06-21T01:57:31Z",
                    "codex-lead-1",
                    event_type,
                    status,
                )
            ],
            task_id="T",
            merging_agent="codex-tools-1",
        )

        assert result["clear_to_merge"] is True
        assert result["latest_blocking_event"] is None


def test_authoritative_decorated_veto_statuses_still_block() -> None:
    for event_type, status in [
        ("decision", "rco_changes_requested_pr530"),
        ("finding", "changes_requested_do_not_merge"),
        ("rco_review", "changes_requested_critical"),
        ("finding", "rco_block_critical"),
        ("finding", "blocked_no_fix_yet"),
        ("finding", "block_without_fix"),
    ]:
        result = check_bridge_clear_to_merge(
            events=[
                _event(
                    "2026-06-21T01:57:31Z",
                    "codex-lead-1",
                    event_type,
                    status,
                )
            ],
            task_id="T",
            merging_agent="codex-tools-1",
        )

        assert result["clear_to_merge"] is False
        assert result["latest_blocking_event"]["status"] == status


def test_nonblocking_context_statuses_do_not_create_phantom_blocks() -> None:
    for event_type, status in [
        ("finding", "pr1344_producer_advisory_resolves_1340_1343_phantom_block"),
        ("message", "answered_changes_requested_forwarded_to_fable"),
        ("message", "ack_blocked_by_lead_changes_requested"),
        ("status", "changes_requested_addressed_exact_head_ci_pending"),
    ]:
        result = check_bridge_clear_to_merge(
            events=[
                _event(
                    "2026-06-21T01:57:31Z",
                    "fable-5",
                    event_type,
                    status,
                )
            ],
            task_id="T",
            merging_agent="codex-tools-1",
        )

        assert result["clear_to_merge"] is True
        assert result["latest_blocking_event"] is None


def test_negated_context_words_in_authoritative_changes_requested_status_still_block() -> None:
    for status in [
        "changes_requested_not_yet_addressed",
        "changes_requested_acknowledged_still_critical",
    ]:
        result = check_bridge_clear_to_merge(
            events=[
                _event(
                    "2026-06-21T01:57:31Z",
                    "claude-rco-1",
                    "finding",
                    status,
                )
            ],
            task_id="T",
            merging_agent="codex-lead-1",
        )

        assert result["clear_to_merge"] is False
        assert result["latest_blocking_event"]["status"] == status


def test_finding_descriptive_changes_requested_status_still_blocks() -> None:
    events = [
        _event(
            "2026-06-21T01:57:31Z",
            "claude-rco-1",
            "finding",
            "changes_requested_shape_validation",
        ),
    ]
    result = check_bridge_clear_to_merge(
        events=events, task_id="T", merging_agent="codex-lead-1"
    )

    assert result["clear_to_merge"] is False
    assert result["latest_blocking_event"]["status"] == "changes_requested_shape_validation"


def test_changes_requested_resolution_status_clears_prior_block_without_approval() -> None:
    for status in [
        "changes_requested_resolved",
        "changes_requested_resolved_ci_green",
        "changes_requested_resolved_ci_pending",
        "changes_requested_retracted",
        "changes_requested_withdrawn",
        "changes_requested_cleared",
        "changes_requested_cleared_ci_green",
        "changes_requested_cleared_ci_pending",
    ]:
        events = [
            _event(
                "2026-06-07T17:38:40Z",
                "codex-tools-1",
                "decision",
                "changes_requested",
            ),
            _event(
                "2026-06-07T17:39:47Z",
                "codex-tools-1",
                "done",
                status,
            ),
        ]
        result = check_bridge_clear_to_merge(
            events=events, task_id="T", merging_agent="codex-lead-1"
        )

        assert result["clear_to_merge"] is True
        assert result["latest_blocking_event"] is None
        assert result["latest_approval_event"] is None


def test_block_resolution_diagnostics_do_not_create_phantom_blocks() -> None:
    for status in [
        "block_cleared",
        "peer_block_cleared",
        "waiver_block_cleared",
        "block_resolved",
        "block_clear",
        "changes_requested_block_resolved",
        "block_cleared_no_remaining_issues",
        "block_resolved_still_monitoring",
        "block_cleared_open_followup",
        "fable_1368_failclosed_endorse_verify_block_cleared_coverage",
    ]:
        result = check_bridge_clear_to_merge(
            events=[
                _event(
                    "2026-06-21T18:47:13Z",
                    "fable-5",
                    "finding",
                    status,
                )
            ],
            task_id="T",
            merging_agent="codex-lead-1",
        )

        assert result["clear_to_merge"] is True
        assert result["latest_blocking_event"] is None


def test_no_changes_requested_text_does_not_downgrade_real_blocking_status() -> None:
    for status in [
        "no_changes_requested_but_blocked",
        "no_changes_requested_rco_blocked",
        "no_changes_requested_block_requested",
        "no_changes_requested_changes_requested",
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


def test_veto_statuses_with_negation_words_still_block() -> None:
    for status in [
        "changes_requested_do_not_merge",
        "blocked_no_fix_yet",
        "block_without_fix",
        "block_incomplete_clear",
        "block_active_clear_label",
        "block_persists_resolved",
        "block_requested",
        "block_not_resolved",
        "block_not_cleared",
        "peer_block_not_cleared",
        "blocked_not_withdrawn",
        "block_cannot_be_cleared",
        "block_cant_clear",
        "block_isnt_resolved",
        "block_wont_clear",
        "block_arent_resolved",
        "block_resolution_fails_cleared_label",
        "block_still_open_cleared_label",
        "block_ongoing_resolved_label",
        "block_outstanding_withdrawn_label",
        "block_unresolved",
        "block_unresolved_not_cleared",
        "blocking_issue_not_yet_resolved",
        "block_resolved_denied",
        "block_cleared_rejected",
        "block_retracted_refused",
        "changes_requested_block_clear_required",
        "merge_blocked_operator_or_driver_waiver_required",
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


def test_no_block_reemit_required_status_does_not_block() -> None:
    events = [
        _event(
            "2026-06-13T16:04:29Z",
            "codex-lead-1",
            "wake_request",
            "producer_no_block_reemit_required",
            task_id="codex-tools-1/agent-next-task-runtime-root-env-20260613",
        ),
        _event(
            "2026-06-13T16:03:07Z",
            "claude-rco-1",
            "decision",
            "rco_pass",
            task_id="codex-tools-1/agent-next-task-runtime-root-env-20260613",
        ),
    ]

    result = check_bridge_clear_to_merge(
        events=events,
        task_id="codex-tools-1/agent-next-task-runtime-root-env-20260613",
        merging_agent="codex-tools-1",
        pr_number=1133,
    )

    assert result["clear_to_merge"] is True
    assert result["latest_blocking_event"] is None
    assert result["latest_approval_event"]["status"] == "rco_pass"


def test_not_blocked_clarification_status_does_not_block() -> None:
    events = [
        _event(
            "2026-06-19T09:57:38Z",
            "claude-rco-1",
            "message",
            "rco1_clarify_1283_not_blocked_on_rco2",
            task_id="wd/security/bridge-changes-requested-freetext-classifier-20260619",
        )
    ]

    result = check_bridge_clear_to_merge(
        events=events,
        task_id="wd/security/bridge-changes-requested-freetext-classifier-20260619",
        merging_agent="codex-lead-1",
        pr_number=1283,
    )

    assert result["clear_to_merge"] is True
    assert result["latest_blocking_event"] is None


def test_no_block_text_does_not_downgrade_real_blocking_status() -> None:
    for status in [
        "changes_requested_no_block",
        "no_blocker_but_changes_requested",
        "no_block_changes_requested",
        "rco_blocked_no_block",
        "no_block_but_blocked",
    ]:
        result = check_bridge_clear_to_merge(
            events=[
                _event(
                    "2026-06-13T16:13:22Z",
                    "claude-rco-1",
                    "finding",
                    status,
                    task_id="codex-tools-1/bridge-peer-gate-no-block-status-20260613",
                )
            ],
            task_id="codex-tools-1/bridge-peer-gate-no-block-status-20260613",
            merging_agent="codex-tools-1",
            pr_number=1138,
        )

        assert result["clear_to_merge"] is False
        assert result["latest_blocking_event"]["status"] == status


def test_task_id_and_positive_pr_mismatch_stay_out_of_scope() -> None:
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


def test_public_library_rejects_incomplete_pr_scope_before_registry_or_scan(
    monkeypatch,
) -> None:
    def fail_registry_load():
        raise AssertionError("identity registry must not load")

    class ExplodingEvents:
        def __iter__(self):
            raise AssertionError("events must not be scanned")

    monkeypatch.setattr(
        gate_module,
        "load_bridge_identity_registry",
        fail_registry_load,
    )

    for pr_number in (None, 0, -1):
        result = _raw_check_bridge_clear_to_merge(
            events=ExplodingEvents(),
            task_id="T",
            merging_agent="claude",
            author_agent=" codex ",
            pr_number=pr_number,
        )

        assert result == {
            "ok": False,
            "clear_to_merge": False,
            "decision": "scope_incomplete",
            "task_id": "T",
            "pr_number": pr_number,
            "merging_agent": "claude",
            "author_agent": "codex",
            "latest_blocking_event": None,
            "latest_approval_event": None,
            "error": (
                "--pr-number must be a positive integer to evaluate "
                "complete PR scope"
            ),
        }


def test_cli_rejects_incomplete_pr_scope_before_bridge_resolution(
    tmp_path: Path,
) -> None:
    missing_bridge_root = tmp_path / "missing-bridge"
    cases = [
        ([], None),
        (["--pr-number", "0"], 0),
        (["--pr-number", "-1"], -1),
    ]

    for pr_args, expected_pr_number in cases:
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--task-id",
                "T",
                "--from-agent",
                "claude",
                *pr_args,
                "--bridge-root",
                str(missing_bridge_root),
                "--json",
            ],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            check=False,
        )

        assert result.returncode == 3
        assert result.stderr == ""
        payload = json.loads(result.stdout)
        assert payload["ok"] is False
        assert payload["clear_to_merge"] is False
        assert payload["decision"] == "scope_incomplete"
        assert payload["task_id"] == "T"
        assert payload["pr_number"] == expected_pr_number
        assert payload["merging_agent"] == "claude"
        assert payload["author_agent"] == ""
        assert payload["latest_blocking_event"] is None
        assert payload["latest_approval_event"] is None
        assert payload["error"] == (
            "--pr-number must be a positive integer to evaluate complete PR scope"
        )


def test_cli_machine_driver_shaped_argv_fails_closed_before_bridge_read(
    tmp_path: Path,
) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--task-id",
            "T",
            "--from-agent",
            "codex-lead-1",
            "--bridge-root",
            str(tmp_path / "missing-bridge"),
        ],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 3
    assert result.stdout == ""
    assert "scope_incomplete" in result.stderr
    assert "--pr-number must be a positive integer" in result.stderr
    assert "bridge events file not found" not in result.stderr


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
            "--pr-number",
            "1",
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
            "--pr-number",
            "1",
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


def test_cli_accepts_utf8_bom_events_file(tmp_path: Path) -> None:
    bridge_root = _seed_bridge(
        tmp_path,
        [
            _event("2026-05-21T10:00:00Z", "claude", "handoff", "rco_requested"),
            _event("2026-05-21T10:05:00Z", "codex", "decision", "changes_requested"),
        ],
    )
    events_path = bridge_root / "shared" / "events.jsonl"
    events_path.write_bytes(
        b"\xef\xbb\xbf" + events_path.read_bytes()
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--task-id",
            "T",
            "--from-agent",
            "claude",
            "--pr-number",
            "1",
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
    assert payload["decision"] == "blocked"
    assert payload["latest_blocking_event"]["status"] == "changes_requested"


def test_cli_defaults_to_runtime_bridge_root_env(tmp_path: Path) -> None:
    runtime_bridge = _seed_bridge(
        tmp_path / "runtime",
        [
            _event("2026-05-21T10:00:00Z", "claude", "handoff", "rco_requested"),
            _event("2026-05-21T10:05:00Z", "codex", "decision", "changes_requested"),
        ],
    )
    env = os.environ.copy()
    env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(runtime_bridge)
    env.pop("AGENT_BRIDGE_ROOT", None)

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--task-id",
            "T",
            "--from-agent",
            "claude",
            "--pr-number",
            "1",
            "--json",
        ],
        cwd=str(ROOT),
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 3
    payload = json.loads(result.stdout)
    assert payload["clear_to_merge"] is False
    assert payload["latest_blocking_event"]["agent"] == "codex"


def test_cli_explicit_bridge_root_overrides_runtime_bridge_root_env(
    tmp_path: Path,
) -> None:
    runtime_bridge = _seed_bridge(
        tmp_path / "runtime",
        [
            _event("2026-05-21T10:00:00Z", "claude", "handoff", "rco_requested"),
            _event("2026-05-21T10:05:00Z", "codex", "decision", "changes_requested"),
        ],
    )
    explicit_bridge = _seed_bridge(
        tmp_path / "explicit",
        [_event("2026-05-21T10:00:00Z", "claude", "handoff", "rco_requested")],
    )
    env = os.environ.copy()
    env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(runtime_bridge)
    env.pop("AGENT_BRIDGE_ROOT", None)

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--task-id",
            "T",
            "--from-agent",
            "claude",
            "--pr-number",
            "1",
            "--bridge-root",
            str(explicit_bridge),
            "--json",
        ],
        cwd=str(ROOT),
        env=env,
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
            "--pr-number",
            "1",
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
            "--pr-number",
            "1",
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
            "--pr-number",
            "527",
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
