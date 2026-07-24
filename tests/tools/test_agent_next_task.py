# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import pytest
import tools.agent_next_task as agent_next_task

from tools.agent_next_task import (
    DREAM_MODE_CANDIDATES,
    OPERATIONAL_SCOUT_CANDIDATES,
    SUBSTRATE_SMOKE_CANDIDATES,
    deferred_lift_state,
    _pick_dream_mode_seed,
    _pick_operational_scout,
    _pick_substrate_smoke,
    evaluate_agent_next_task,
    main,
)
from waggledance.core.work_queue import claim_task, release_task

NOW = datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc)
BRIDGE_AGENT_UUIDS = {
    "codex-lead-1": "d3c9d1d1-96a9-4eb8-a8e2-6f05f9d1a101",
    "codex-tools-1": "7a8af68d-20bc-4598-9953-23c5dd98b102",
    "fable-5": "f8b1e5c0-3d2a-4e6b-9c1f-7a0d5e2b4c80",
}
RCO_FAILOVER_TASK_ID = (
    "rco-lane-failover-scout-2026-05-20-"
    "claude-rco-2-since-20260520t112000z"
)


def _rco_lane_stall_event() -> dict:
    return {
        "ts_utc": "2026-05-20T11:20:00Z",
        "agent": "claude-rco-2",
        "type": "decision",
        "task_id": "rco-backup-work",
        "status": "active",
        "message": "backup review lane activity",
    }


def _rco_scout_claim_event(
    *,
    task_id: str = RCO_FAILOVER_TASK_ID,
    agent: str = "codex-lead-1",
    ts_utc: str = "2026-05-20T11:30:00Z",
) -> dict:
    return {
        "ts_utc": ts_utc,
        "agent": agent,
        "agent_uuid": BRIDGE_AGENT_UUIDS[agent],
        "type": "claim",
        "task_id": task_id,
        "status": "active",
        "message": "claim bounded RCO lane failover diagnostic",
    }


def _rco_scout_outcome_event(
    *,
    task_id: str = RCO_FAILOVER_TASK_ID,
    agent: str = "codex-lead-1",
    event_type: str = "finding",
    status: str = "open",
    ts_utc: str = "2026-05-20T11:50:00Z",
) -> dict:
    return {
        "ts_utc": ts_utc,
        "agent": agent,
        "agent_uuid": BRIDGE_AGENT_UUIDS[agent],
        "type": event_type,
        "task_id": task_id,
        "status": status,
        "message": "lane remains inactive; restart or verify it",
    }


def _assert_deferred_lift_state(state: dict) -> None:
    assert state["source"] == "docs/architecture/IDLE_PROTOCOL_V1.md#deferred"
    authority = state["authority"]
    assert authority["read_only_report"] is True
    for key in (
        "emits_bridge_events",
        "claims_work",
        "creates_tasks",
        "creates_branches",
        "creates_pull_requests",
        "merges",
        "skips_gates",
    ):
        assert authority[key] is False

    items = state["items"]
    production_loop = items["production_two_agent_activation_loop"]
    assert production_loop["state"] == "read_only_scheduler_ready"
    assert "tools/bridge_loop_tick.py" in production_loop["implemented_by"]
    assert "not scheduler authority" in production_loop["safe_next"]
    assert items["automatic_payload_generation"]["state"] == "deferred"
    assert (
        items["auto_conversion_consensus_to_implementation_work"]["state"]
        == "report_only_partial"
    )


def _events_file(bridge_root: Path, events: list[dict]) -> Path:
    events_path = bridge_root / "shared" / "events.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    events_path.write_text(
        "\n".join(json.dumps(event, sort_keys=True) for event in events) + "\n",
        encoding="utf-8",
    )
    return events_path


def _claims_dir(bridge_root: Path) -> Path:
    claims = bridge_root / "work_queue" / "claims"
    claims.mkdir(parents=True, exist_ok=True)
    return claims


def _empty_bridge(tmp_path: Path) -> tuple[Path, Path, Path]:
    bridge = tmp_path / ".agent-bridge"
    events_path = _events_file(
        bridge,
        [
            {
                "ts_utc": "2026-01-01T00:00:00Z",
                "agent": "codex",
                "type": "heartbeat",
                "task_id": "baseline",
                "status": "active",
                "message": "background heartbeat",
            }
        ],
    )
    # Ensure the canonical claims dir under bridge_root exists so list_claims
    # finds an empty (not missing) directory.
    claims_dir = _claims_dir(bridge)
    return bridge, events_path, claims_dir


# ---------------------------------------------------------------------------
# input validation
# ---------------------------------------------------------------------------


def test_invalid_agent_id_is_rejected(tmp_path: Path) -> None:
    bridge, events_path, claims_dir = _empty_bridge(tmp_path)

    report = evaluate_agent_next_task(
        agent="Invalid Agent!",
        events_path=events_path,
        bridge_root=bridge,
        now_utc=NOW,
    )

    assert report["decision"] == "agent_invalid"
    assert report["next_action"] == "operator_handles"
    assert report["exit_code"] == 2
    _assert_deferred_lift_state(report["deferred_lift_state"])


# ---------------------------------------------------------------------------
# defer-to-bridge-next-action branches
# ---------------------------------------------------------------------------


def test_defers_when_agent_has_active_claim(tmp_path: Path) -> None:
    bridge, events_path, claims_dir = _empty_bridge(tmp_path)
    claim_task(
        agent="claude",
        task_id="claude-already-running",
        summary="active work in progress",
        mode="write",
        write_scope=["tools/foo.py"],
        bridge_root=bridge,
    )

    report = evaluate_agent_next_task(
        agent="claude",
        events_path=events_path,
        bridge_root=bridge,
        now_utc=NOW,
    )

    assert report["decision"] == "defer_to_bridge_next_action"
    assert report["next_action"] == "follow_bridge_recommendation"
    assert report["bridge_recommendation"]["action"] == "continue_claim"
    assert report["bridge_recommendation"]["task_id"] == "claude-already-running"
    _assert_deferred_lift_state(report["deferred_lift_state"])


def test_defers_when_open_incoming_request(tmp_path: Path) -> None:
    bridge = tmp_path / ".agent-bridge"
    events_path = _events_file(
        bridge,
        [
            {
                "ts_utc": "2026-01-01T00:00:00Z",
                "agent": "codex",
                "type": "heartbeat",
                "task_id": "baseline",
                "status": "active",
                "message": "background heartbeat",
            },
            {
                "ts_utc": "2026-05-20T11:30:00Z",
                "agent": "codex",
                "to": "claude",
                "type": "message",
                "task_id": "important-request",
                "status": "request",
                "message": "please review my latest design proposal end to end",
            },
        ],
    )
    claims_dir = _claims_dir(bridge)

    report = evaluate_agent_next_task(
        agent="claude",
        events_path=events_path,
        bridge_root=bridge,
        now_utc=NOW,
    )

    assert report["decision"] == "defer_to_bridge_next_action"
    assert report["bridge_recommendation"]["action"] == "answer_incoming"


def test_defers_when_operator_wake_request_is_open(tmp_path: Path) -> None:
    bridge = tmp_path / ".agent-bridge"
    events_path = _events_file(
        bridge,
        [
            {
                "ts_utc": "2026-05-20T11:55:00Z",
                "agent": "operator",
                "to": "codex-tools-1",
                "type": "wake_request",
                "task_id": "operator-wake-request-20260520",
                "status": "open",
                "severity": "medium",
                "message": (
                    "please read the bridge and answer the visible request. "
                    "classification=rco_wake_requested"
                ),
            },
        ],
    )
    _claims_dir(bridge)

    report = evaluate_agent_next_task(
        agent="codex-tools-1",
        events_path=events_path,
        bridge_root=bridge,
        now_utc=NOW,
    )

    assert report["decision"] == "defer_to_bridge_next_action"
    assert report["bridge_recommendation"]["action"] == "answer_incoming"
    assert (
        report["bridge_recommendation"]["task_id"]
        == "operator-wake-request-20260520"
    )
    assert report["bridge_recommendation"]["incoming"]["type"] == "wake_request"


def test_ignores_operator_bridge_follow_nudge_as_work_request(tmp_path: Path) -> None:
    bridge = tmp_path / ".agent-bridge"
    events_path = _events_file(
        bridge,
        [
            {
                "ts_utc": "2026-05-20T11:55:00Z",
                "agent": "operator",
                "to": "codex-tools-1",
                "type": "wake_request",
                "task_id": "bridge-follow-nudge-20260520",
                "status": "open",
                "severity": "medium",
                "message": (
                    "jatka: read the bridge and answer open requests. "
                    "classification=rco_wake_requested openIncoming=1"
                ),
            },
        ],
    )
    _claims_dir(bridge)

    report = evaluate_agent_next_task(
        agent="codex-tools-1",
        events_path=events_path,
        bridge_root=bridge,
        now_utc=NOW,
    )

    assert report["decision"] == "claim_substrate_smoke"
    assert report["bridge_recommendation"]["action"] == "claim_unblocked_work"
    assert report["bridge_recommendation"]["open_incoming_count"] == 0
    assert report["bridge_recommendation"]["stale_incoming_count"] == 0


def test_ignores_stale_incoming_request_using_agent_now(tmp_path: Path) -> None:
    bridge = tmp_path / ".agent-bridge"
    events_path = _events_file(
        bridge,
        [
            {
                "ts_utc": "2026-05-18T10:10:00Z",
                "agent": "codex",
                "to": "claude",
                "type": "message",
                "task_id": "stale-request",
                "status": "request",
                "message": "old request",
            },
        ],
    )
    _claims_dir(bridge)

    report = evaluate_agent_next_task(
        agent="claude",
        events_path=events_path,
        bridge_root=bridge,
        now_utc=NOW,
    )

    assert report["decision"] == "claim_substrate_smoke"
    assert report["bridge_recommendation"]["action"] == "claim_unblocked_work"
    assert report["bridge_recommendation"]["open_incoming_count"] == 0
    assert report["bridge_recommendation"]["stale_incoming_count"] == 1
    assert report["bridge_recommendation"]["stale_incoming_task_ids"] == [
        "stale-request"
    ]


def test_prioritizes_stalled_primary_production_peer(tmp_path: Path) -> None:
    bridge = tmp_path / ".agent-bridge"
    events_path = _events_file(
        bridge,
        [
            {
                "ts_utc": "2026-05-20T11:20:00Z",
                "agent": "codex-tools-1",
                "type": "decision",
                "task_id": "tools-active-work",
                "status": "active",
                "message": "tools started a producer slice",
            },
            {
                "ts_utc": "2026-05-20T11:30:00Z",
                "agent": "operator",
                "to": "codex-tools-1",
                "type": "wake_request",
                "task_id": "tools-wake",
                "status": "open",
                "message": "please read bridge",
            },
            {
                "ts_utc": "2026-05-20T11:40:00Z",
                "agent": "operator",
                "to": "codex-tools-1",
                "type": "wake_request",
                "task_id": "tools-wake",
                "status": "open",
                "message": "please read bridge again",
            },
        ],
    )
    _claims_dir(bridge)
    (bridge / "wake_codex-tools-1").write_text(
        "2026-05-20T11:40:00Z", encoding="utf-8"
    )

    report = evaluate_agent_next_task(
        agent="codex-lead-1",
        events_path=events_path,
        bridge_root=bridge,
        now_utc=NOW,
    )

    assert report["decision"] == "defer_to_bridge_next_action"
    assert report["next_action"] == "follow_bridge_recommendation"
    recommendation = report["bridge_recommendation"]
    assert recommendation["action"] == "escalate_wake_delivery_stall"
    assert recommendation["safe_mode"] == "read-only"
    assert recommendation["operator_action_required"] is True
    assert recommendation["operator_action_target_agents"] == ["codex-tools-1"]
    assert recommendation["operator_action"] == (
        "restart_or_verify_target_agent_bridge_session_watcher"
    )
    assert report["bridge_recommendation"]["production_liveness"][
        "stalled_agent_count"
    ] == 1
    wake = report["bridge_recommendation"]["production_liveness"]["wake_delivery"][
        "stalled_wakes"
    ][0]
    assert wake["target_agent"] == "codex-tools-1"
    assert wake["wake_file_checked"] is True
    assert wake["wake_file_present"] is True
    _assert_deferred_lift_state(report["deferred_lift_state"])


def test_primary_production_liveness_scout_includes_live_cli_probe(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    events_path = _events_file(
        bridge,
        [
            {
                "ts_utc": "2026-05-20T11:20:00Z",
                "agent": "codex-tools-1",
                "type": "decision",
                "task_id": "tools-active-work",
                "status": "active",
                "message": "tools started a producer slice",
            },
        ],
    )
    _claims_dir(bridge)

    report = evaluate_agent_next_task(
        agent="codex-lead-1",
        events_path=events_path,
        bridge_root=bridge,
        now_utc=NOW,
    )

    assert report["decision"] == "claim_production_liveness_reactivation_scout"
    candidate = report["candidate"]
    assert candidate["kind"] == "production_liveness_reactivation_scout"
    assert any(
        "agent_cli_model_probe.py" in command
        and "--live" in command
        and "--json" in command
        for command in candidate["diagnostic_commands"]
    )
    assert "live CLI model diagnostics" in candidate["acceptance"]
    assert "delivery_escalation" not in candidate
    _assert_deferred_lift_state(report["deferred_lift_state"])


def test_completed_primary_production_liveness_scout_advances_to_smoke(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    events_path = _events_file(
        bridge,
        [
            {
                "ts_utc": "2026-05-20T11:20:00Z",
                "agent": "codex-tools-1",
                "type": "decision",
                "task_id": "tools-active-work",
                "status": "active",
                "message": "tools started a producer slice",
            },
        ],
    )
    _claims_dir(bridge)
    task_id = (
        "production-liveness-reactivation-scout-2026-05-20-"
        "codex-tools-1-since-20260520t112000z-repeat-1"
    )
    claim_task(
        agent="claude",
        task_id=task_id,
        summary="same-day production liveness scout already completed",
        mode="read-only",
        bridge_root=bridge,
        now_utc=NOW,
    )
    release_task(
        agent="claude",
        task_id=task_id,
        release_status="done",
        release_message="diagnostic completed",
        bridge_root=bridge,
        now_utc=NOW,
    )

    report = evaluate_agent_next_task(
        agent="codex-lead-1",
        events_path=events_path,
        bridge_root=bridge,
        now_utc=NOW,
    )

    assert report["decision"] == "claim_substrate_smoke"
    assert report["candidate"]["kind"] == "run_substrate_smoke"
    assert report["completed_production_liveness_task_ids"] == [task_id]
    assert report["active_production_liveness_task_ids"] == []
    _assert_deferred_lift_state(report["deferred_lift_state"])


def test_legacy_primary_production_liveness_scout_advances_to_smoke(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    events_path = _events_file(
        bridge,
        [
            {
                "ts_utc": "2026-05-20T11:20:00Z",
                "agent": "codex-tools-1",
                "type": "decision",
                "task_id": "tools-active-work",
                "status": "active",
                "message": "tools started a producer slice",
            },
        ],
    )
    _claims_dir(bridge)
    task_id = "production-liveness-reactivation-scout-2026-05-20-codex-tools-1"
    claim_task(
        agent="claude",
        task_id=task_id,
        summary="legacy same-day production liveness scout completed",
        mode="read-only",
        bridge_root=bridge,
        now_utc=NOW,
    )
    release_task(
        agent="claude",
        task_id=task_id,
        release_status="done",
        release_message="legacy diagnostic completed",
        bridge_root=bridge,
        now_utc=NOW,
    )

    report = evaluate_agent_next_task(
        agent="codex-lead-1",
        events_path=events_path,
        bridge_root=bridge,
        now_utc=NOW,
    )

    assert report["decision"] == "claim_substrate_smoke"
    assert report["candidate"]["kind"] == "run_substrate_smoke"
    assert report["completed_production_liveness_task_ids"] == [task_id]
    assert report["active_production_liveness_task_ids"] == []
    _assert_deferred_lift_state(report["deferred_lift_state"])


def test_legacy_primary_liveness_done_event_advances_to_smoke(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    task_id = "production-liveness-reactivation-scout-2026-05-20-codex-tools-1"
    events_path = _events_file(
        bridge,
        [
            {
                "ts_utc": "2026-05-20T11:20:00Z",
                "agent": "codex-tools-1",
                "type": "decision",
                "task_id": "tools-active-work",
                "status": "active",
                "message": "tools started a producer slice",
            },
            {
                "ts_utc": "2026-05-20T11:45:00Z",
                "agent": "codex-tools-1",
                "type": "done",
                "task_id": task_id,
                "status": "done",
                "message": "legacy diagnostic completed from bridge event only",
            },
        ],
    )
    _claims_dir(bridge)

    report = evaluate_agent_next_task(
        agent="codex-lead-1",
        events_path=events_path,
        bridge_root=bridge,
        now_utc=NOW,
    )

    assert report["decision"] == "claim_substrate_smoke"
    assert report["candidate"]["kind"] == "run_substrate_smoke"
    assert report["completed_production_liveness_task_ids"] == [task_id]
    assert report["active_production_liveness_task_ids"] == []
    _assert_deferred_lift_state(report["deferred_lift_state"])


def test_old_legacy_primary_production_liveness_scout_does_not_mask_new_episode(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    events_path = _events_file(
        bridge,
        [
            {
                "ts_utc": "2026-05-20T11:20:00Z",
                "agent": "codex-tools-1",
                "type": "decision",
                "task_id": "tools-active-work",
                "status": "active",
                "message": "tools started a later producer slice",
            },
        ],
    )
    _claims_dir(bridge)
    task_id = "production-liveness-reactivation-scout-2026-05-20-codex-tools-1"
    old_episode = datetime(2026, 5, 20, 10, 30, 0, tzinfo=timezone.utc)
    claim_task(
        agent="claude",
        task_id=task_id,
        summary="legacy production liveness scout completed before later episode",
        mode="read-only",
        bridge_root=bridge,
        now_utc=old_episode,
    )
    release_task(
        agent="claude",
        task_id=task_id,
        release_status="done",
        release_message="old legacy diagnostic completed",
        bridge_root=bridge,
        now_utc=old_episode,
    )

    report = evaluate_agent_next_task(
        agent="codex-lead-1",
        events_path=events_path,
        bridge_root=bridge,
        now_utc=NOW,
    )

    assert report["decision"] == "claim_production_liveness_reactivation_scout"
    assert report["candidate"]["task_id_suggestion"].endswith(
        "codex-tools-1-since-20260520t112000z"
    )
    assert report["candidate"]["rotation"]["skipped_completed_task_ids"] == []
    assert report["completed_production_liveness_task_ids"] == [task_id]
    _assert_deferred_lift_state(report["deferred_lift_state"])


def test_active_primary_production_liveness_scout_advances_to_smoke(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    events_path = _events_file(
        bridge,
        [
            {
                "ts_utc": "2026-05-20T11:20:00Z",
                "agent": "codex-tools-1",
                "type": "decision",
                "task_id": "tools-active-work",
                "status": "active",
                "message": "tools started a producer slice",
            },
        ],
    )
    _claims_dir(bridge)
    task_id = (
        "production-liveness-reactivation-scout-2026-05-20-"
        "codex-tools-1-since-20260520t112000z"
    )
    claim_task(
        agent="claude",
        task_id=task_id,
        summary="same-day production liveness scout already claimed",
        mode="read-only",
        bridge_root=bridge,
        now_utc=NOW,
    )

    report = evaluate_agent_next_task(
        agent="codex-lead-1",
        events_path=events_path,
        bridge_root=bridge,
        now_utc=NOW,
    )

    assert report["decision"] == "claim_substrate_smoke"
    assert report["candidate"]["kind"] == "run_substrate_smoke"
    assert report["completed_production_liveness_task_ids"] == []
    assert report["active_production_liveness_task_ids"] == [task_id]
    _assert_deferred_lift_state(report["deferred_lift_state"])


def test_new_primary_production_liveness_episode_ignores_prior_done_scout(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    events_path = _events_file(
        bridge,
        [
            {
                "ts_utc": "2026-05-20T11:20:00Z",
                "agent": "codex-tools-1",
                "type": "decision",
                "task_id": "tools-active-work",
                "status": "active",
                "message": "tools started a later producer slice",
            },
        ],
    )
    _claims_dir(bridge)
    prior_episode_task_id = (
        "production-liveness-reactivation-scout-2026-05-20-"
        "codex-tools-1-since-20260520t110000z"
    )
    claim_task(
        agent="claude",
        task_id=prior_episode_task_id,
        summary="earlier production liveness episode completed",
        mode="read-only",
        bridge_root=bridge,
        now_utc=NOW,
    )
    release_task(
        agent="claude",
        task_id=prior_episode_task_id,
        release_status="done",
        release_message="earlier diagnostic completed",
        bridge_root=bridge,
        now_utc=NOW,
    )

    report = evaluate_agent_next_task(
        agent="codex-lead-1",
        events_path=events_path,
        bridge_root=bridge,
        now_utc=NOW,
    )

    assert report["decision"] == "claim_production_liveness_reactivation_scout"
    assert report["candidate"]["task_id_suggestion"].endswith(
        "codex-tools-1-since-20260520t112000z"
    )
    assert report["candidate"]["rotation"]["skipped_completed_task_ids"] == []
    assert report["completed_production_liveness_task_ids"] == [
        prior_episode_task_id
    ]
    _assert_deferred_lift_state(report["deferred_lift_state"])


def test_prioritizes_stalled_rco_lane_failover_before_smoke(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    events_path = _events_file(
        bridge,
        [
            {
                "ts_utc": "2026-05-20T11:20:00Z",
                "agent": "claude-rco-2",
                "type": "decision",
                "task_id": "rco-backup-work",
                "status": "active",
                "message": "backup review lane activity",
            },
        ],
    )
    _claims_dir(bridge)

    report = evaluate_agent_next_task(
        agent="codex-lead-1",
        events_path=events_path,
        bridge_root=bridge,
        now_utc=NOW,
    )

    assert report["decision"] == "claim_rco_lane_failover_scout"
    assert report["next_action"] == "claim_and_run"
    candidate = report["candidate"]
    assert candidate["kind"] == "rco_lane_failover_scout"
    assert candidate["stalled_agent"] == "claude-rco-2"
    assert candidate["fallback_reviewers"] == ["claude-rco-1"]
    assert candidate["mode"] == "read-only"
    assert candidate["write_scope"] == []
    assert "report_unanswered_bridge_requests.py" in candidate["recommended_command"]
    assert "--agent claude-rco-2" in candidate["recommended_command"]
    assert "check_bridge_wake_delivery.py" in candidate["diagnostic_commands"][1]
    assert "do not substitute an RCO pass" in candidate["acceptance"]
    assert report["bridge_recommendation"]["production_liveness"][
        "stalled_agent_count"
    ] == 1
    _assert_deferred_lift_state(report["deferred_lift_state"])


def test_completed_stalled_rco_lane_failover_advances_to_smoke(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    events_path = _events_file(
        bridge,
        [
            {
                "ts_utc": "2026-05-20T11:20:00Z",
                "agent": "claude-rco-2",
                "type": "decision",
                "task_id": "rco-backup-work",
                "status": "active",
                "message": "backup review lane activity",
            },
        ],
    )
    _claims_dir(bridge)
    task_id = (
        "rco-lane-failover-scout-2026-05-20-"
        "claude-rco-2-since-20260520t112000z-repeat-1"
    )
    claim_task(
        agent="codex-lead-1",
        task_id=task_id,
        summary="same-day RCO lane failover scout already completed",
        mode="read-only",
        bridge_root=bridge,
        now_utc=NOW,
    )
    release_task(
        agent="codex-lead-1",
        task_id=task_id,
        release_status="done",
        release_message="diagnostic completed",
        bridge_root=bridge,
        now_utc=NOW,
    )
    done_file = next((bridge / "work_queue" / "done").glob("*.json"))
    done_payload = json.loads(done_file.read_text(encoding="utf-8"))
    done_payload["agent_uuid"] = BRIDGE_AGENT_UUIDS["codex-lead-1"]
    done_file.write_text(
        json.dumps(done_payload, sort_keys=True),
        encoding="utf-8",
    )

    report = evaluate_agent_next_task(
        agent="codex-lead-1",
        events_path=events_path,
        bridge_root=bridge,
        now_utc=NOW,
    )

    assert report["decision"] == "claim_substrate_smoke"
    assert report["candidate"]["kind"] == "run_substrate_smoke"
    _assert_deferred_lift_state(report["deferred_lift_state"])


@pytest.mark.parametrize(
    ("event_type", "status"),
    [
        ("finding", "open"),
        ("handoff", "rco_lane_restart_requested"),
        ("handoff", "handoff"),
        ("handoff", "rco_lane_inactive_diagnostics_clear"),
        ("handoff", "rco1_lane_stalled_verify_or_restart_requested"),
        ("handoff", "rco2_lane_stalled_verify_or_restart_requested"),
        ("done", "done"),
    ],
)
def test_identity_bound_claim_then_rco_scout_outcome_advances_to_smoke(
    tmp_path: Path,
    event_type: str,
    status: str,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    events_path = _events_file(
        bridge,
        [
            _rco_lane_stall_event(),
            _rco_scout_claim_event(),
            _rco_scout_outcome_event(event_type=event_type, status=status),
        ],
    )
    _claims_dir(bridge)

    report = evaluate_agent_next_task(
        agent="codex-lead-1",
        events_path=events_path,
        bridge_root=bridge,
        now_utc=NOW,
    )

    assert report["decision"] == "claim_substrate_smoke"
    assert report["candidate"]["kind"] == "run_substrate_smoke"


@pytest.mark.parametrize(
    ("event_type", "status"),
    [
        ("done", "done"),
        ("handoff", "rco_lane_inactive_diagnostics_clear"),
        ("handoff", "rco1_lane_stalled_verify_or_restart_requested"),
        ("handoff", "rco2_lane_stalled_verify_or_restart_requested"),
    ],
)
def test_identity_bound_outcome_can_use_matching_legacy_done_lifecycle(
    tmp_path: Path,
    event_type: str,
    status: str,
) -> None:
    """Legacy work-queue rows corroborate, but never supply, identity."""
    bridge = tmp_path / ".agent-bridge"
    events_path = _events_file(
        bridge,
        [
            _rco_lane_stall_event(),
            _rco_scout_outcome_event(
                event_type=event_type,
                status=status,
                ts_utc="2026-05-20T11:45:00Z",
            ),
        ],
    )
    done_dir = bridge / "work_queue" / "done"
    done_dir.mkdir(parents=True)
    (done_dir / "legacy-rco-scout.json").write_text(
        json.dumps(
            {
                "agent": "codex-lead-1",
                "task_id": RCO_FAILOVER_TASK_ID,
                "summary": "bounded RCO lane failover diagnostic",
                "release_status": "done",
                "release_message": "diagnostic complete",
                "claimed_at_utc": "2026-05-20T11:30:00Z",
                "released_at_utc": "2026-05-20T11:50:00Z",
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    _claims_dir(bridge)

    report = evaluate_agent_next_task(
        agent="codex-lead-1",
        events_path=events_path,
        bridge_root=bridge,
        now_utc=NOW,
    )

    assert report["decision"] == "claim_substrate_smoke"
    assert report["candidate"]["kind"] == "run_substrate_smoke"


@pytest.mark.parametrize(
    "hostile_case",
    [
        "missing_event_uuid",
        "mismatched_event_uuid",
        "wrong_record_agent",
        "mismatched_record_uuid",
        "event_before_claim",
        "event_after_release_grace",
        "nonterminal_release",
    ],
)
def test_legacy_done_lifecycle_corroboration_fails_closed(
    tmp_path: Path,
    hostile_case: str,
) -> None:
    outcome = _rco_scout_outcome_event(
        event_type="handoff",
        status="rco1_lane_stalled_verify_or_restart_requested",
        ts_utc="2026-05-20T11:45:00Z",
    )
    payload = {
        "agent": "codex-lead-1",
        "task_id": RCO_FAILOVER_TASK_ID,
        "summary": "bounded RCO lane failover diagnostic",
        "release_status": "done",
        "release_message": "diagnostic complete",
        "claimed_at_utc": "2026-05-20T11:30:00Z",
        "released_at_utc": "2026-05-20T11:50:00Z",
    }
    if hostile_case == "missing_event_uuid":
        del outcome["agent_uuid"]
    elif hostile_case == "mismatched_event_uuid":
        outcome["agent_uuid"] = "00000000-0000-0000-0000-000000000000"
    elif hostile_case == "wrong_record_agent":
        payload["agent"] = "fable-5"
    elif hostile_case == "mismatched_record_uuid":
        payload["agent_uuid"] = "00000000-0000-0000-0000-000000000000"
    elif hostile_case == "event_before_claim":
        outcome["ts_utc"] = "2026-05-20T11:29:59Z"
    elif hostile_case == "event_after_release_grace":
        outcome["ts_utc"] = "2026-05-20T11:50:06Z"
    elif hostile_case == "nonterminal_release":
        payload["release_status"] = "active"

    bridge = tmp_path / ".agent-bridge"
    events_path = _events_file(
        bridge,
        [_rco_lane_stall_event(), outcome],
    )
    done_dir = bridge / "work_queue" / "done"
    done_dir.mkdir(parents=True)
    (done_dir / "legacy-rco-scout.json").write_text(
        json.dumps(payload, sort_keys=True),
        encoding="utf-8",
    )
    _claims_dir(bridge)

    report = evaluate_agent_next_task(
        agent="codex-lead-1",
        events_path=events_path,
        bridge_root=bridge,
        now_utc=NOW,
    )

    assert report["decision"] == "claim_rco_lane_failover_scout"
    assert report["completed_rco_lane_failover_task_ids"] == []


def test_real_shaped_legacy_done_prevents_duplicate_rco_scout(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    events_path = _events_file(
        bridge,
        [
            _rco_lane_stall_event(),
            _rco_scout_outcome_event(
                event_type="handoff",
                status="rco1_lane_stalled_verify_or_restart_requested",
                ts_utc="2026-05-20T11:49:59Z",
            ),
        ],
    )
    done_dir = bridge / "work_queue" / "done"
    done_dir.mkdir(parents=True)
    (done_dir / "real-shaped-legacy-record.json").write_text(
        json.dumps(
            {
                "agent": "codex-lead-1",
                "task_id": RCO_FAILOVER_TASK_ID,
                "summary": "bounded RCO lane failover diagnostic",
                "release_status": "done",
                "release_message": "diagnostic complete; handoff emitted",
                "claimed_at_utc": "2026-05-20T11:30:00Z",
                "released_at_utc": "2026-05-20T11:50:00Z",
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    _claims_dir(bridge)

    report = evaluate_agent_next_task(
        agent="codex-lead-1",
        events_path=events_path,
        bridge_root=bridge,
        now_utc=NOW,
    )

    assert report["decision"] == "claim_substrate_smoke"
    assert report["candidate"]["kind"] == "run_substrate_smoke"


@pytest.mark.parametrize(
    "hostile_case",
    [
        "missing_claim",
        "mixed_case_type",
        "missing_agent_uuid",
        "identity_mismatch",
        "wrong_agent",
        "future_timestamp",
        "invalid_timestamp",
        "predates_episode",
        "preliminary_active_finding",
        "missing_status",
        "missing_message",
        "task_prefix_collision",
        "claim_identity_mismatch",
        "claim_missing_message",
        "claim_timestamp_after_outcome",
    ],
)
def test_untrusted_rco_scout_outcome_does_not_complete_task(
    tmp_path: Path,
    hostile_case: str,
) -> None:
    task_id = RCO_FAILOVER_TASK_ID
    claim = _rco_scout_claim_event()
    outcome = _rco_scout_outcome_event()

    if hostile_case == "mixed_case_type":
        outcome["type"] = "FiNdInG"
    elif hostile_case == "missing_agent_uuid":
        del outcome["agent_uuid"]
    elif hostile_case == "identity_mismatch":
        outcome["agent_uuid"] = "00000000-0000-0000-0000-000000000000"
    elif hostile_case == "wrong_agent":
        outcome["agent"] = "fable-5"
        outcome["agent_uuid"] = BRIDGE_AGENT_UUIDS["fable-5"]
    elif hostile_case == "future_timestamp":
        outcome["ts_utc"] = "2026-05-20T23:59:00Z"
    elif hostile_case == "invalid_timestamp":
        outcome["ts_utc"] = "not-a-time"
    elif hostile_case == "predates_episode":
        outcome["ts_utc"] = "2026-05-20T11:00:00Z"
    elif hostile_case == "preliminary_active_finding":
        outcome["status"] = "active"
    elif hostile_case == "missing_status":
        del outcome["status"]
    elif hostile_case == "missing_message":
        del outcome["message"]
    elif hostile_case == "task_prefix_collision":
        task_id = f"{RCO_FAILOVER_TASK_ID}-unrelated-maintenance"
        claim["task_id"] = task_id
        outcome["task_id"] = task_id
    elif hostile_case == "claim_identity_mismatch":
        claim["agent_uuid"] = "00000000-0000-0000-0000-000000000000"
    elif hostile_case == "claim_missing_message":
        del claim["message"]
    elif hostile_case == "claim_timestamp_after_outcome":
        claim["ts_utc"] = "2026-05-20T11:55:00Z"
    events = [_rco_lane_stall_event()]
    if hostile_case != "missing_claim":
        events.append(claim)
    events.append(outcome)
    bridge = tmp_path / ".agent-bridge"
    events_path = _events_file(bridge, events)
    _claims_dir(bridge)

    report = evaluate_agent_next_task(
        agent="codex-lead-1",
        events_path=events_path,
        bridge_root=bridge,
        now_utc=NOW,
    )

    assert report["decision"] == "claim_rco_lane_failover_scout"
    assert report["candidate"]["kind"] == "rco_lane_failover_scout"
    assert report["completed_rco_lane_failover_task_ids"] == []


@pytest.mark.parametrize(
    "status",
    [
        "in_progress",
        "incomplete",
        "banana",
        "active",
        "pending",
        "rco_lane_restart_requested_extra",
        "xrco_lane_restart_requested",
        "rco_lane__requested",
        "rco3_lane_restart_requested",
    ],
)
def test_nonterminal_or_unknown_rco_scout_handoff_does_not_complete_task(
    tmp_path: Path,
    status: str,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    events_path = _events_file(
        bridge,
        [
            _rco_lane_stall_event(),
            _rco_scout_claim_event(),
            _rco_scout_outcome_event(
                event_type="handoff",
                status=status,
            ),
        ],
    )
    _claims_dir(bridge)

    report = evaluate_agent_next_task(
        agent="codex-lead-1",
        events_path=events_path,
        bridge_root=bridge,
        now_utc=NOW,
    )

    assert report["decision"] == "claim_rco_lane_failover_scout"
    assert report["candidate"]["kind"] == "rco_lane_failover_scout"
    assert report["completed_rco_lane_failover_task_ids"] == []


def test_naive_rco_scout_timestamps_fail_closed(tmp_path: Path) -> None:
    bridge = tmp_path / ".agent-bridge"
    _claims_dir(bridge)

    naive_claim_events = [
        _rco_lane_stall_event(),
        _rco_scout_claim_event(ts_utc="2026-05-20T11:30:00"),
        _rco_scout_outcome_event(),
    ]
    naive_claim_report = evaluate_agent_next_task(
        agent="codex-lead-1",
        events_path=_events_file(bridge, naive_claim_events),
        bridge_root=bridge,
        now_utc=NOW,
    )
    assert naive_claim_report["completed_rco_lane_failover_task_ids"] == []

    naive_outcome_events = [
        _rco_lane_stall_event(),
        _rco_scout_claim_event(),
        _rco_scout_outcome_event(ts_utc="2026-05-20T11:50:00"),
    ]
    naive_outcome_report = evaluate_agent_next_task(
        agent="codex-lead-1",
        events_path=_events_file(bridge, naive_outcome_events),
        bridge_root=bridge,
        now_utc=NOW,
    )
    assert naive_outcome_report["completed_rco_lane_failover_task_ids"] == []


def test_pre_episode_outcome_lower_bound_fails_closed() -> None:
    identity_registry = agent_next_task.load_bridge_identity_registry()
    event = _rco_scout_outcome_event(ts_utc="2026-05-20T11:10:00Z")

    assert not agent_next_task._is_trusted_rco_scout_completion_event(
        event=event,
        claimed_at=datetime(
            2026,
            5,
            20,
            11,
            0,
            tzinfo=timezone.utc,
        ),
        now_utc=NOW,
        identity_registry=identity_registry,
    )


def test_rco_scout_completion_matching_accepts_only_exact_or_repeat_n() -> None:
    canonical = RCO_FAILOVER_TASK_ID
    task_ids = {
        canonical,
        f"{canonical}-repeat-1",
        f"{canonical}-repeat-27",
        f"{canonical}-repeat-0",
        f"{canonical}-repeat-01",
        f"{canonical}-unrelated-maintenance",
    }

    assert agent_next_task._matching_rco_lane_failover_task_ids(
        task_ids,
        canonical_task_id=canonical,
    ) == {
        canonical,
        f"{canonical}-repeat-1",
        f"{canonical}-repeat-27",
    }


def test_liveness_task_ids_require_known_targets_and_valid_structure() -> None:
    rco_prefix = "rco-lane-failover-scout-2026-05-20-"
    production_prefix = "production-liveness-reactivation-scout-2026-05-20-"

    assert agent_next_task._is_same_day_rco_lane_failover_task_id(
        RCO_FAILOVER_TASK_ID,
        NOW,
    )
    assert agent_next_task._is_same_day_rco_lane_failover_task_id(
        f"{RCO_FAILOVER_TASK_ID}-repeat-2",
        NOW,
    )
    assert not agent_next_task._is_same_day_rco_lane_failover_task_id(
        f"{rco_prefix}random-agent-since-20260520t112000z",
        NOW,
    )
    assert not agent_next_task._is_same_day_rco_lane_failover_task_id(
        f"{rco_prefix}claude-rco-2-since-20269999t999999z",
        NOW,
    )
    production_task = (
        f"{production_prefix}codex-tools-1-since-20260520t112000z"
    )
    assert agent_next_task._is_same_day_production_liveness_reactivation_task_id(
        production_task,
        NOW,
    )
    assert not agent_next_task._is_same_day_production_liveness_reactivation_task_id(
        f"{production_task}-unrelated",
        NOW,
    )


def test_complete_rco_scout_handoff_done_record_advances_to_smoke(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    events_path = _events_file(bridge, [_rco_lane_stall_event()])
    done_dir = bridge / "work_queue" / "done"
    done_dir.mkdir(parents=True)
    (done_dir / "rco-scout.json").write_text(
        json.dumps(
            {
                "agent": "codex-lead-1",
                "agent_uuid": BRIDGE_AGENT_UUIDS["codex-lead-1"],
                "task_id": RCO_FAILOVER_TASK_ID,
                "summary": "bounded RCO lane failover diagnostic",
                "release_status": "handoff",
                "release_message": "operator should verify or restart the lane",
                "claimed_at_utc": "2026-05-20T11:30:00Z",
                "released_at_utc": "2026-05-20T11:50:00Z",
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    _claims_dir(bridge)

    report = evaluate_agent_next_task(
        agent="codex-lead-1",
        events_path=events_path,
        bridge_root=bridge,
        now_utc=NOW,
    )

    assert report["decision"] == "claim_substrate_smoke"


@pytest.mark.parametrize(
    "hostile_case",
    [
        "missing_release_status",
        "status_fallback",
        "release_message_fallback",
        "missing_summary",
        "missing_release_message",
        "missing_claimed_at",
        "missing_released_at",
        "missing_agent_uuid",
        "identity_mismatch",
        "unregistered_agent",
        "future_release",
        "pre_episode_claim",
        "release_before_claim",
        "invalid_claim_time",
        "task_prefix_collision",
        "nonterminal_status",
    ],
)
def test_malformed_rco_scout_done_record_does_not_complete_task(
    tmp_path: Path,
    hostile_case: str,
) -> None:
    payload = {
        "agent": "codex-lead-1",
        "agent_uuid": BRIDGE_AGENT_UUIDS["codex-lead-1"],
        "task_id": RCO_FAILOVER_TASK_ID,
        "summary": "bounded RCO lane failover diagnostic",
        "release_status": "handoff",
        "release_message": "operator should verify or restart the lane",
        "claimed_at_utc": "2026-05-20T11:30:00Z",
        "released_at_utc": "2026-05-20T11:50:00Z",
    }
    if hostile_case == "missing_release_status":
        del payload["release_status"]
    elif hostile_case == "status_fallback":
        del payload["release_status"]
        payload["status"] = "done"
    elif hostile_case == "release_message_fallback":
        del payload["release_status"]
        payload["release_message"] = "done"
    elif hostile_case == "missing_summary":
        del payload["summary"]
    elif hostile_case == "missing_release_message":
        del payload["release_message"]
    elif hostile_case == "missing_claimed_at":
        del payload["claimed_at_utc"]
    elif hostile_case == "missing_released_at":
        del payload["released_at_utc"]
    elif hostile_case == "missing_agent_uuid":
        del payload["agent_uuid"]
    elif hostile_case == "identity_mismatch":
        payload["agent_uuid"] = "00000000-0000-0000-0000-000000000000"
    elif hostile_case == "unregistered_agent":
        payload["agent"] = "random-agent"
    elif hostile_case == "future_release":
        payload["released_at_utc"] = "2026-05-20T23:59:00Z"
    elif hostile_case == "pre_episode_claim":
        payload["claimed_at_utc"] = "2026-05-20T11:00:00Z"
    elif hostile_case == "release_before_claim":
        payload["released_at_utc"] = "2026-05-20T11:25:00Z"
    elif hostile_case == "invalid_claim_time":
        payload["claimed_at_utc"] = "not-a-time"
    elif hostile_case == "task_prefix_collision":
        payload["task_id"] = f"{RCO_FAILOVER_TASK_ID}-unrelated"
    elif hostile_case == "nonterminal_status":
        payload["release_status"] = "active"

    bridge = tmp_path / ".agent-bridge"
    events_path = _events_file(bridge, [_rco_lane_stall_event()])
    done_dir = bridge / "work_queue" / "done"
    done_dir.mkdir(parents=True)
    (done_dir / "hostile.json").write_text(
        json.dumps(payload, sort_keys=True),
        encoding="utf-8",
    )
    _claims_dir(bridge)

    report = evaluate_agent_next_task(
        agent="codex-lead-1",
        events_path=events_path,
        bridge_root=bridge,
        now_utc=NOW,
    )

    assert report["decision"] == "claim_rco_lane_failover_scout"
    assert report["completed_rco_lane_failover_task_ids"] == []


@pytest.mark.parametrize(
    "raw_payload",
    [
        b"[]",
        b"1",
        b'"not-an-object"',
        b"\xff\xfe\xfa",
        b"{",
        b'{"integer":' + (b"9" * 5000) + b"}",
        (b"[" * 2000) + b"0" + (b"]" * 2000),
    ],
    ids=[
        "array",
        "integer",
        "string",
        "invalid_utf8",
        "invalid_json",
        "integer_limit",
        "deep_recursion",
    ],
)
def test_malformed_done_json_is_ignored_fail_closed(
    tmp_path: Path,
    raw_payload: bytes,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    events_path = _events_file(bridge, [_rco_lane_stall_event()])
    done_dir = bridge / "work_queue" / "done"
    done_dir.mkdir(parents=True)
    (done_dir / "hostile.json").write_bytes(raw_payload)
    _claims_dir(bridge)

    report = evaluate_agent_next_task(
        agent="codex-lead-1",
        events_path=events_path,
        bridge_root=bridge,
        now_utc=NOW,
    )

    assert report["decision"] == "claim_rco_lane_failover_scout"
    assert report["candidate"]["kind"] == "rco_lane_failover_scout"
    assert report["completed_rco_lane_failover_task_ids"] == []


def test_active_stalled_rco_lane_failover_advances_to_smoke(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    events_path = _events_file(
        bridge,
        [
            {
                "ts_utc": "2026-05-20T11:20:00Z",
                "agent": "claude-rco-2",
                "type": "decision",
                "task_id": "rco-backup-work",
                "status": "active",
                "message": "backup review lane activity",
            },
        ],
    )
    _claims_dir(bridge)
    task_id = (
        "rco-lane-failover-scout-2026-05-20-"
        "claude-rco-2-since-20260520t112000z"
    )
    claim_task(
        agent="codex-tools-1",
        task_id=task_id,
        summary="same-day RCO lane failover scout already claimed",
        mode="read-only",
        bridge_root=bridge,
        now_utc=NOW,
    )

    report = evaluate_agent_next_task(
        agent="codex-lead-1",
        events_path=events_path,
        bridge_root=bridge,
        now_utc=NOW,
    )

    assert report["decision"] == "claim_substrate_smoke"
    assert report["candidate"]["kind"] == "run_substrate_smoke"
    _assert_deferred_lift_state(report["deferred_lift_state"])


def test_new_rco_lane_failover_episode_ignores_prior_done_scout(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    events_path = _events_file(
        bridge,
        [
            {
                "ts_utc": "2026-05-20T11:20:00Z",
                "agent": "claude-rco-2",
                "type": "decision",
                "task_id": "rco-backup-work",
                "status": "active",
                "message": "backup review lane activity after earlier stall",
            },
        ],
    )
    _claims_dir(bridge)
    prior_episode_task_id = (
        "rco-lane-failover-scout-2026-05-20-"
        "claude-rco-2-since-20260520t110000z"
    )
    claim_task(
        agent="codex-lead-1",
        task_id=prior_episode_task_id,
        summary="earlier RCO lane failover episode completed",
        mode="read-only",
        bridge_root=bridge,
        now_utc=NOW,
    )
    release_task(
        agent="codex-lead-1",
        task_id=prior_episode_task_id,
        release_status="done",
        release_message="earlier diagnostic completed",
        bridge_root=bridge,
        now_utc=NOW,
    )
    done_file = next((bridge / "work_queue" / "done").glob("*.json"))
    done_payload = json.loads(done_file.read_text(encoding="utf-8"))
    done_payload["agent_uuid"] = BRIDGE_AGENT_UUIDS["codex-lead-1"]
    done_file.write_text(
        json.dumps(done_payload, sort_keys=True),
        encoding="utf-8",
    )

    report = evaluate_agent_next_task(
        agent="codex-lead-1",
        events_path=events_path,
        bridge_root=bridge,
        now_utc=NOW,
    )

    assert report["decision"] == "claim_rco_lane_failover_scout"
    assert report["candidate"]["task_id_suggestion"].endswith(
        "claude-rco-2-since-20260520t112000z"
    )
    assert report["candidate"]["rotation"]["skipped_completed_task_ids"] == []
    assert report["completed_rco_lane_failover_task_ids"] == [prior_episode_task_id]
    _assert_deferred_lift_state(report["deferred_lift_state"])


def test_defers_wake_delivery_escalation_for_non_peer_target(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    events_path = _events_file(
        bridge,
        [
            {
                "ts_utc": "2026-05-20T11:20:00Z",
                "agent": "claude-rco-2",
                "type": "decision",
                "task_id": "rco-backup-work",
                "status": "active",
                "message": "backup review lane activity",
            },
            {
                "ts_utc": "2026-05-20T11:30:00Z",
                "agent": "operator",
                "to": "claude-rco-2",
                "type": "wake_request",
                "task_id": "rco-wake",
                "status": "open",
                "message": "please read bridge",
            },
            {
                "ts_utc": "2026-05-20T11:40:00Z",
                "agent": "operator",
                "to": "claude-rco-2",
                "type": "wake_request",
                "task_id": "rco-wake",
                "status": "open",
                "message": "please read bridge again",
            },
        ],
    )
    _claims_dir(bridge)
    (bridge / "wake_claude-rco-2").write_text(
        "2026-05-20T11:40:00Z", encoding="utf-8"
    )

    report = evaluate_agent_next_task(
        agent="codex-lead-1",
        events_path=events_path,
        bridge_root=bridge,
        now_utc=NOW,
    )

    assert report["decision"] == "defer_to_bridge_next_action"
    assert report["next_action"] == "follow_bridge_recommendation"
    assert "candidate" not in report
    recommendation = report["bridge_recommendation"]
    assert recommendation["action"] == "escalate_wake_delivery_stall"
    assert recommendation["operator_action_required"] is True
    assert recommendation["operator_action_target_agents"] == ["claude-rco-2"]


def test_prioritizes_unresolved_rco_reemit_pr_gate(tmp_path: Path) -> None:
    bridge = tmp_path / ".agent-bridge"
    head = "4" * 40
    task_id = "codex-tools-1-operator-feedback-action-bridge-template-20260613"
    events_path = _events_file(
        bridge,
        [
            {
                "ts_utc": "2026-05-20T11:58:00Z",
                "agent": "codex-tools-1",
                "type": "finding",
                "task_id": "continuous-operational-scout-open-pr-queue-scout-2026-05-20-0",
                "status": "queue_snapshot",
                "message": "open PR scout: #1122 needs RCO re-emit",
                "payload": {
                    "schema": "tools.continuous_scout.open_pr_queue.v1",
                    "open_prs": [
                        {
                            "pr": 1122,
                            "head": head,
                            "gate": "needs_rco_reemit_on_canonical_task_id",
                            "required_task_id": task_id,
                        }
                    ],
                },
            },
        ],
    )
    _claims_dir(bridge)

    report = evaluate_agent_next_task(
        agent="codex-lead-1",
        events_path=events_path,
        bridge_root=bridge,
        now_utc=NOW,
    )

    assert report["decision"] == "claim_rco_reemit_watch_scout"
    assert report["next_action"] == "claim_and_run"
    candidate = report["candidate"]
    assert candidate["kind"] == "rco_task_id_reemit_watch_scout"
    assert candidate["pr"] == 1122
    assert candidate["task_id"] == task_id
    assert candidate["head"] == head
    assert candidate["author_agent"] == "codex-tools-1"
    assert candidate["mode"] == "read-only"
    assert candidate["write_scope"] == []
    assert "check_rco_pass_present.py" in candidate["recommended_command"]
    assert f"--task-id {task_id}" in candidate["recommended_command"]
    assert f"--head {head}" in candidate["recommended_command"]
    _assert_deferred_lift_state(report["deferred_lift_state"])


def test_later_rco_response_clears_rco_reemit_priority(tmp_path: Path) -> None:
    bridge = tmp_path / ".agent-bridge"
    head = "5" * 40
    task_id = "codex-tools-1-operator-feedback-action-bridge-template-20260613"
    events_path = _events_file(
        bridge,
        [
            {
                "ts_utc": "2026-05-20T11:58:00Z",
                "agent": "codex-tools-1",
                "type": "finding",
                "task_id": "continuous-operational-scout-open-pr-queue-scout-2026-05-20-0",
                "status": "queue_snapshot",
                "message": "open PR scout: #1122 needs RCO re-emit",
                "payload": {
                    "schema": "tools.continuous_scout.open_pr_queue.v1",
                    "prs": [
                        {
                            "pr": 1122,
                            "head": head,
                            "rco_gate": "no_rco_events_for_task",
                            "required_task_id": task_id,
                        }
                    ],
                },
            },
            {
                "ts_utc": "2026-05-20T11:59:00Z",
                "agent": "claude-rco-1",
                "type": "decision",
                "task_id": task_id,
                "status": "rco_pass",
                "message": f"RCO_PASS PR #1122 head={head}",
            },
        ],
    )
    _claims_dir(bridge)

    report = evaluate_agent_next_task(
        agent="codex-lead-1",
        events_path=events_path,
        bridge_root=bridge,
        now_utc=NOW,
    )

    assert report["decision"] == "claim_substrate_smoke"
    assert report["candidate"]["kind"] == "run_substrate_smoke"


def test_wrong_head_rco_response_keeps_rco_reemit_priority(tmp_path: Path) -> None:
    bridge = tmp_path / ".agent-bridge"
    head = "5" * 40
    wrong_head = "7" * 40
    task_id = "codex-tools-1-operator-feedback-action-bridge-template-20260613"
    events_path = _events_file(
        bridge,
        [
            {
                "ts_utc": "2026-05-20T11:58:00Z",
                "agent": "codex-tools-1",
                "type": "finding",
                "task_id": "continuous-operational-scout-open-pr-queue-scout-2026-05-20-0",
                "status": "queue_snapshot",
                "message": "open PR scout: #1122 needs RCO re-emit",
                "payload": {
                    "open_prs": [
                        {
                            "pr": 1122,
                            "head": head,
                            "gate": "needs_rco_reemit_on_canonical_task_id",
                            "required_task_id": task_id,
                        }
                    ],
                },
            },
            {
                "ts_utc": "2026-05-20T11:59:00Z",
                "agent": "claude-rco-1",
                "type": "decision",
                "task_id": task_id,
                "status": "rco_pass",
                "message": f"RCO_PASS PR #1122 head={wrong_head}",
            },
        ],
    )
    _claims_dir(bridge)

    report = evaluate_agent_next_task(
        agent="codex-lead-1",
        events_path=events_path,
        bridge_root=bridge,
        now_utc=NOW,
    )

    assert report["decision"] == "claim_rco_reemit_watch_scout"
    assert report["candidate"]["head"] == head


def test_non_terminal_pr_test_pass_keeps_rco_reemit_priority(tmp_path: Path) -> None:
    bridge = tmp_path / ".agent-bridge"
    head = "6" * 40
    task_id = "codex-tools-1-operator-feedback-action-bridge-template-20260613"
    events_path = _events_file(
        bridge,
        [
            {
                "ts_utc": "2026-05-20T11:58:00Z",
                "agent": "codex-tools-1",
                "type": "finding",
                "task_id": "continuous-operational-scout-open-pr-queue-scout-2026-05-20-0",
                "status": "queue_snapshot",
                "message": "open PR scout: #1122 needs RCO re-emit",
                "payload": {
                    "open_prs": [
                        {
                            "pr": 1122,
                            "head": head,
                            "gate": "needs_rco_reemit_on_canonical_task_id",
                            "required_task_id": task_id,
                        }
                    ],
                },
            },
            {
                "ts_utc": "2026-05-20T11:59:00Z",
                "agent": "codex-tools-1",
                "type": "test",
                "task_id": "continuous-operational-scout-main-bridge-idle-health-smoke-2026-05-20-0",
                "status": "pass",
                "message": "PR #1122 related smoke passed; RCO still pending",
            },
            {
                "ts_utc": "2026-05-20T11:59:30Z",
                "agent": "codex-tools-1",
                "type": "done",
                "task_id": "continuous-operational-scout-open-pr-queue-scout-2026-05-20-0",
                "status": "done",
                "message": (
                    "Completed scout; PR #1124 merged; #1122 still needs "
                    "RCO re-emit"
                ),
            },
        ],
    )
    _claims_dir(bridge)

    report = evaluate_agent_next_task(
        agent="codex-lead-1",
        events_path=events_path,
        bridge_root=bridge,
        now_utc=NOW,
    )

    assert report["decision"] == "claim_rco_reemit_watch_scout"
    assert report["candidate"]["task_id"] == task_id


# ---------------------------------------------------------------------------
# substrate-smoke pick branches
# ---------------------------------------------------------------------------


def test_picks_substrate_smoke_when_bridge_says_claim_unblocked_work(
    tmp_path: Path,
) -> None:
    bridge, events_path, claims_dir = _empty_bridge(tmp_path)

    report = evaluate_agent_next_task(
        agent="claude",
        events_path=events_path,
        bridge_root=bridge,
        now_utc=NOW,
    )

    assert report["decision"] == "claim_substrate_smoke"
    assert report["next_action"] == "claim_and_run"
    assert report["underlying_bridge_action"] == "claim_unblocked_work"
    candidate = report["candidate"]
    assert candidate["kind"] == "run_substrate_smoke"
    assert candidate["mode"] == "read-only"
    assert candidate["write_scope"] == []
    assert candidate["target"] in {
        entry["target"] for entry in SUBSTRATE_SMOKE_CANDIDATES
    }
    assert candidate["task_id_suggestion"].startswith("claude-substrate-smoke-")
    assert candidate["task_id_suggestion"].endswith(
        f"-{candidate['rotation']['index']}"
    )
    assert "pytest" in candidate["recommended_command"]
    _assert_deferred_lift_state(report["deferred_lift_state"])


def test_recommended_command_uses_current_python_executable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge, events_path, claims_dir = _empty_bridge(tmp_path)
    runtime_python = str(tmp_path / "runtime" / ".venv" / "Scripts" / "python.exe")
    monkeypatch.setattr(agent_next_task.sys, "executable", runtime_python)

    report = evaluate_agent_next_task(
        agent="claude",
        events_path=events_path,
        bridge_root=bridge,
        now_utc=NOW,
    )

    command = report["candidate"]["recommended_command"]
    assert "C:\\Python\\project2-master" not in command
    assert runtime_python in command


def test_agent_next_task_applies_default_bridge_liveness_suppression_config(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    events_path = _events_file(
        bridge,
        [
            {
                "ts_utc": "2026-05-20T11:30:00Z",
                "agent": "grok-scout-1",
                "type": "blocked",
                "task_id": "grok-budget",
                "status": "redteam_blocked",
                "message": "budget unavailable",
            }
        ],
    )
    _claims_dir(bridge)
    suppression_config = bridge / "shared" / "production_liveness_suppression.json"
    suppression_config.write_text(
        json.dumps(
            {
                "version": 1,
                "suppressed_agents": {
                    "grok-scout-1": {
                        "reason": "budget unavailable until reset"
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    report = evaluate_agent_next_task(
        agent="codex-lead-1",
        events_path=events_path,
        bridge_root=bridge,
        now_utc=NOW,
    )

    liveness = report["bridge_recommendation"]["production_liveness"]
    assert liveness["stalled_agent_count"] == 0
    assert liveness["suppressed_stalled_agent_count"] == 1
    assert liveness["suppressed_stalled_agents"][0]["agent"] == "grok-scout-1"


def test_agent_next_task_defers_suppressed_agent_follow_nudge(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    events_path = _events_file(
        bridge,
        [
            {
                "ts_utc": "2026-05-20T11:55:00Z",
                "agent": "operator",
                "to": "fable-5",
                "type": "wake_request",
                "task_id": "bridge-follow-nudge-20260520",
                "status": "open",
                "severity": "medium",
                "message": (
                    "jatka: read the bridge and answer open requests. "
                    "classification=rco_wake_requested openIncoming=7"
                ),
            },
        ],
    )
    _claims_dir(bridge)
    suppression_config = bridge / "shared" / "production_liveness_suppression.json"
    suppression_config.write_text(
        json.dumps(
            {
                "version": 1,
                "suppressed_agents": {
                    "fable-5": {"reason": "operator reported lane unavailable"}
                },
            }
        ),
        encoding="utf-8",
    )

    report = evaluate_agent_next_task(
        agent="fable-5",
        events_path=events_path,
        bridge_root=bridge,
        now_utc=NOW,
    )

    assert report["decision"] == "defer_to_bridge_next_action"
    assert report["bridge_recommendation"]["action"] == "agent_suppressed_unavailable"
    assert report["bridge_recommendation"]["task_id"] == "agent-suppressed-unavailable"
    assert report["bridge_recommendation"]["open_incoming_count"] == 0
    assert (
        report["bridge_recommendation"]["suppression_reason"]
        == "operator reported lane unavailable"
    )


def test_deferred_lift_state_returns_copy() -> None:
    state = deferred_lift_state()
    _assert_deferred_lift_state(state)

    state["authority"]["creates_pull_requests"] = True

    fresh = deferred_lift_state()
    assert fresh["authority"]["creates_pull_requests"] is False


def test_agent_next_task_lifts_bridge_agent_profile_to_top_level(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    events_path = _events_file(
        bridge,
        [
            {
                "ts_utc": "2026-05-20T11:55:00Z",
                "agent": "claude-rco-1",
                "type": "heartbeat",
                "task_id": "heartbeat",
                "status": "active",
                "message": "background heartbeat",
                "role": "rco",
                "agent_uuid": "11111111-2222-3333-4444-555555555555",
                "capabilities": ["bridge_review", "rco"],
            }
        ],
    )
    _claims_dir(bridge)

    report = evaluate_agent_next_task(
        agent="claude-rco-1",
        events_path=events_path,
        bridge_root=bridge,
        now_utc=NOW,
    )

    assert report["decision"] == "claim_substrate_smoke"
    assert report["agent_profile"]["role"] == "rco"
    assert report["agent_profile"]["agent_uuid"] == (
        "11111111-2222-3333-4444-555555555555"
    )
    assert report["agent_profile"]["capabilities"] == ["bridge_review", "rco"]
    assert report["bridge_recommendation"]["agent_profile"] == report["agent_profile"]


def test_picks_substrate_smoke_when_bridge_says_parallel_read_only(
    tmp_path: Path,
) -> None:
    bridge, events_path, claims_dir = _empty_bridge(tmp_path)
    claim_task(
        agent="codex",
        task_id="codex-busy-write",
        summary="other agent owns write scope",
        mode="write",
        write_scope=["tools/bar.py"],
        bridge_root=bridge,
    )

    report = evaluate_agent_next_task(
        agent="claude",
        events_path=events_path,
        bridge_root=bridge,
        now_utc=NOW,
    )

    assert report["decision"] == "claim_substrate_smoke"
    assert report["underlying_bridge_action"] == "parallel_read_only"


def test_completed_same_day_test_pass_event_advances_to_next_candidate(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    first = _pick_substrate_smoke(agent="codex", now_utc=NOW)
    events_path = _events_file(
        bridge,
        [
            {
                "ts_utc": "2026-01-01T00:00:00Z",
                "agent": "codex",
                "type": "heartbeat",
                "task_id": "baseline",
                "status": "active",
                "message": "background heartbeat",
            },
            {
                "ts_utc": "2026-05-20T12:10:00Z",
                "agent": "codex",
                "type": "test",
                "task_id": first["task_id_suggestion"],
                "status": "pass",
                "message": "daily substrate smoke passed",
            },
        ],
    )
    _claims_dir(bridge)

    report = evaluate_agent_next_task(
        agent="codex",
        events_path=events_path,
        bridge_root=bridge,
        now_utc=NOW,
    )

    candidate = report["candidate"]
    assert candidate["target"] != first["target"]
    assert candidate["task_id_suggestion"] != first["task_id_suggestion"]
    assert candidate["rotation"]["start_index"] == first["rotation"]["index"]
    assert candidate["rotation"]["offset"] == 1
    assert (
        first["task_id_suggestion"]
        in candidate["rotation"]["skipped_completed_task_ids"]
    )


def test_legacy_completed_same_day_smoke_claim_advances_to_next_candidate(
    tmp_path: Path,
) -> None:
    bridge, events_path, claims_dir = _empty_bridge(tmp_path)
    first = _pick_substrate_smoke(agent="codex", now_utc=NOW)
    legacy_task_id = "codex-substrate-smoke-2026-05-20"
    claim_task(
        agent="codex",
        task_id=legacy_task_id,
        summary="legacy daily smoke claim before task ids had pool indexes",
        mode="read-only",
        bridge_root=bridge,
        now_utc=NOW,
    )
    release_task(
        agent="codex",
        task_id=legacy_task_id,
        release_status="done",
        release_message="legacy substrate smoke passed",
        bridge_root=bridge,
        now_utc=NOW,
    )
    assert sorted(p.name for p in claims_dir.iterdir()) == []

    report = evaluate_agent_next_task(
        agent="codex",
        events_path=events_path,
        bridge_root=bridge,
        now_utc=NOW,
    )

    candidate = report["candidate"]
    assert candidate["target"] != first["target"]
    assert candidate["rotation"]["offset"] == 1
    assert legacy_task_id in candidate["rotation"]["skipped_completed_task_ids"]


def test_all_completed_same_day_smokes_falls_back_to_dream_mode_seed(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    completed_events = [
        {
            "ts_utc": "2026-05-20T12:10:00Z",
            "agent": "codex",
            "type": "done",
            "task_id": f"codex-substrate-smoke-2026-05-20-{index}",
            "status": "done",
            "message": "daily substrate smoke passed",
        }
        for index in range(len(SUBSTRATE_SMOKE_CANDIDATES))
    ]
    events_path = _events_file(
        bridge,
        [
            {
                "ts_utc": "2026-01-01T00:00:00Z",
                "agent": "codex",
                "type": "heartbeat",
                "task_id": "baseline",
                "status": "active",
                "message": "background heartbeat",
            },
            *completed_events,
        ],
    )
    _claims_dir(bridge)

    report = evaluate_agent_next_task(
        agent="codex",
        events_path=events_path,
        bridge_root=bridge,
        now_utc=NOW,
    )

    assert report["decision"] == "claim_dream_mode_seed"
    assert report["next_action"] == "claim_and_run"
    candidate = report["candidate"]
    assert candidate["kind"] == "advance_dream_mode_seed"
    assert candidate["mode"] == "read-only"
    assert candidate["write_scope"] == []
    assert candidate["target"] in {entry["target"] for entry in DREAM_MODE_CANDIDATES}
    assert candidate["task_id_suggestion"].startswith("dream-mode-")
    assert candidate["task_id_suggestion"].endswith("-2026-05-20")
    assert len(report["completed_substrate_smoke_task_ids"]) == len(
        SUBSTRATE_SMOKE_CANDIDATES
    )
    assert report["completed_dream_mode_task_ids"] == []


def test_completed_same_day_dream_seed_advances_to_next_seed(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    completed_smokes = [
        {
            "ts_utc": "2026-05-20T12:10:00Z",
            "agent": "codex",
            "type": "done",
            "task_id": f"codex-substrate-smoke-2026-05-20-{index}",
            "status": "done",
            "message": "daily substrate smoke passed",
        }
        for index in range(len(SUBSTRATE_SMOKE_CANDIDATES))
    ]
    first = _pick_dream_mode_seed(agent="codex", now_utc=NOW)
    assert first is not None
    events_path = _events_file(
        bridge,
        [
            {
                "ts_utc": "2026-01-01T00:00:00Z",
                "agent": "codex",
                "type": "heartbeat",
                "task_id": "baseline",
                "status": "active",
                "message": "background heartbeat",
            },
            *completed_smokes,
            {
                "ts_utc": "2026-05-20T12:20:00Z",
                "agent": "claude",
                "type": "done",
                "task_id": first["task_id_suggestion"],
                "status": "done",
                "message": "dream-mode seed completed by another agent",
            },
        ],
    )
    _claims_dir(bridge)

    report = evaluate_agent_next_task(
        agent="codex",
        events_path=events_path,
        bridge_root=bridge,
        now_utc=NOW,
    )

    candidate = report["candidate"]
    assert report["decision"] == "claim_dream_mode_seed"
    assert candidate["task_id_suggestion"] != first["task_id_suggestion"]
    assert candidate["rotation"]["offset"] == 1
    assert (
        first["task_id_suggestion"]
        in candidate["rotation"]["skipped_completed_task_ids"]
    )


def test_spaced_success_status_dream_seed_advances_to_next_seed(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    completed_smokes = [
        {
            "ts_utc": "2026-05-20T12:10:00Z",
            "agent": "codex",
            "type": "done",
            "task_id": f"codex-substrate-smoke-2026-05-20-{index}",
            "status": "done",
            "message": "daily substrate smoke passed",
        }
        for index in range(len(SUBSTRATE_SMOKE_CANDIDATES))
    ]
    first = _pick_dream_mode_seed(agent="codex", now_utc=NOW)
    assert first is not None
    events_path = _events_file(
        bridge,
        [
            {
                "ts_utc": "2026-01-01T00:00:00Z",
                "agent": "codex",
                "type": "heartbeat",
                "task_id": "baseline",
                "status": "active",
                "message": "background heartbeat",
            },
            *completed_smokes,
            {
                "ts_utc": "2026-05-20T12:20:00Z",
                "agent": "codex-lead-1",
                "type": "done",
                "task_id": first["task_id_suggestion"],
                "status": (
                    "counterfactual eval extension inventory complete; "
                    "smallest next work is read-only readiness summary"
                ),
                "message": "dream-mode seed completed with a human-readable status",
            },
        ],
    )
    _claims_dir(bridge)

    report = evaluate_agent_next_task(
        agent="codex",
        events_path=events_path,
        bridge_root=bridge,
        now_utc=NOW,
    )

    candidate = report["candidate"]
    assert report["decision"] == "claim_dream_mode_seed"
    assert candidate["task_id_suggestion"] != first["task_id_suggestion"]
    assert (
        first["task_id_suggestion"]
        in candidate["rotation"]["skipped_completed_task_ids"]
    )


def test_legacy_compact_done_dream_seed_advances_to_next_seed(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    completed_smokes = [
        {
            "ts_utc": "2026-05-20T12:10:00Z",
            "agent": "codex",
            "type": "done",
            "task_id": f"codex-substrate-smoke-2026-05-20-{index}",
            "status": "done",
            "message": "daily substrate smoke passed",
        }
        for index in range(len(SUBSTRATE_SMOKE_CANDIDATES))
    ]
    first = _pick_dream_mode_seed(agent="codex", now_utc=NOW)
    assert first is not None
    compact_task_id = first["task_id_suggestion"].replace(
        "-2026-05-20",
        "-20260520",
    )
    events_path = _events_file(
        bridge,
        [
            {
                "ts_utc": "2026-01-01T00:00:00Z",
                "agent": "codex",
                "type": "heartbeat",
                "task_id": "baseline",
                "status": "active",
                "message": "background heartbeat",
            },
            *completed_smokes,
        ],
    )
    _claims_dir(bridge)
    claim_task(
        agent="claude",
        task_id=compact_task_id,
        summary="legacy compact dream-mode seed claim",
        mode="read-only",
        bridge_root=bridge,
        now_utc=NOW,
    )
    release_task(
        agent="claude",
        task_id=compact_task_id,
        release_status="done",
        release_message="legacy compact dream-mode seed completed",
        bridge_root=bridge,
        now_utc=NOW,
    )

    report = evaluate_agent_next_task(
        agent="codex",
        events_path=events_path,
        bridge_root=bridge,
        now_utc=NOW,
    )

    candidate = report["candidate"]
    assert report["decision"] == "claim_dream_mode_seed"
    assert candidate["task_id_suggestion"] != first["task_id_suggestion"]
    assert candidate["rotation"]["offset"] == 1
    assert first["task_id_suggestion"] in report["completed_dream_mode_task_ids"]
    assert compact_task_id not in report["completed_dream_mode_task_ids"]
    assert (
        first["task_id_suggestion"]
        in candidate["rotation"]["skipped_completed_task_ids"]
    )


def test_active_same_day_dream_seed_advances_to_next_seed(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    completed_smokes = [
        {
            "ts_utc": "2026-05-20T12:10:00Z",
            "agent": "codex",
            "type": "done",
            "task_id": f"codex-substrate-smoke-2026-05-20-{index}",
            "status": "done",
            "message": "daily substrate smoke passed",
        }
        for index in range(len(SUBSTRATE_SMOKE_CANDIDATES))
    ]
    first = _pick_dream_mode_seed(agent="codex", now_utc=NOW)
    assert first is not None
    events_path = _events_file(
        bridge,
        [
            {
                "ts_utc": "2026-01-01T00:00:00Z",
                "agent": "codex",
                "type": "heartbeat",
                "task_id": "baseline",
                "status": "active",
                "message": "background heartbeat",
            },
            *completed_smokes,
        ],
    )
    _claims_dir(bridge)
    claim_task(
        agent="claude",
        task_id=first["task_id_suggestion"],
        summary="same-day dream-mode seed already claimed by another agent",
        mode="read-only",
        bridge_root=bridge,
        now_utc=NOW,
    )

    report = evaluate_agent_next_task(
        agent="codex",
        events_path=events_path,
        bridge_root=bridge,
        now_utc=NOW,
    )

    candidate = report["candidate"]
    assert report["decision"] == "claim_dream_mode_seed"
    assert candidate["task_id_suggestion"] != first["task_id_suggestion"]
    assert candidate["rotation"]["offset"] == 1
    assert report["completed_dream_mode_task_ids"] == []
    assert report["active_dream_mode_task_ids"] == [first["task_id_suggestion"]]
    assert (
        first["task_id_suggestion"] in candidate["rotation"]["skipped_active_task_ids"]
    )


def test_completed_smoke_and_dream_pools_fall_back_to_operational_scout(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    completed_smokes = [
        {
            "ts_utc": "2026-05-20T12:10:00Z",
            "agent": "codex",
            "type": "done",
            "task_id": f"codex-substrate-smoke-2026-05-20-{index}",
            "status": "done",
            "message": "daily substrate smoke passed",
        }
        for index in range(len(SUBSTRATE_SMOKE_CANDIDATES))
    ]
    completed_dreams = [
        {
            "ts_utc": "2026-05-20T12:20:00Z",
            "agent": "claude",
            "type": "done",
            "task_id": (f"dream-mode-{entry['category']}-{entry['slug']}-2026-05-20"),
            "status": "done",
            "message": "dream-mode seed completed",
        }
        for entry in DREAM_MODE_CANDIDATES
    ]
    events_path = _events_file(
        bridge,
        [
            {
                "ts_utc": "2026-01-01T00:00:00Z",
                "agent": "codex",
                "type": "heartbeat",
                "task_id": "baseline",
                "status": "active",
                "message": "background heartbeat",
            },
            *completed_smokes,
            *completed_dreams,
        ],
    )
    _claims_dir(bridge)

    report = evaluate_agent_next_task(
        agent="codex",
        events_path=events_path,
        bridge_root=bridge,
        now_utc=NOW,
    )

    assert report["decision"] == "claim_operational_scout"
    assert report["next_action"] == "claim_and_run"
    candidate = report["candidate"]
    assert candidate["kind"] == "operational_read_only_scout"
    assert candidate["mode"] == "read-only"
    assert candidate["write_scope"] == []
    assert candidate["target"] in {
        entry["target"] for entry in OPERATIONAL_SCOUT_CANDIDATES
    }
    assert candidate["task_id_suggestion"].startswith("operational-scout-")
    assert candidate["task_id_suggestion"].endswith("-2026-05-20")
    assert candidate["recommended_command"]
    assert len(report["completed_substrate_smoke_task_ids"]) == len(
        SUBSTRATE_SMOKE_CANDIDATES
    )
    assert len(report["completed_dream_mode_task_ids"]) == len(DREAM_MODE_CANDIDATES)
    assert report["completed_operational_scout_task_ids"] == []


def test_active_same_day_operational_scout_advances_to_next_scout(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    completed_smokes = [
        {
            "ts_utc": "2026-05-20T12:10:00Z",
            "agent": "codex",
            "type": "done",
            "task_id": f"codex-substrate-smoke-2026-05-20-{index}",
            "status": "done",
            "message": "daily substrate smoke passed",
        }
        for index in range(len(SUBSTRATE_SMOKE_CANDIDATES))
    ]
    completed_dreams = [
        {
            "ts_utc": "2026-05-20T12:20:00Z",
            "agent": "claude",
            "type": "done",
            "task_id": (f"dream-mode-{entry['category']}-{entry['slug']}-2026-05-20"),
            "status": "done",
            "message": "dream-mode seed completed",
        }
        for entry in DREAM_MODE_CANDIDATES
    ]
    first = _pick_operational_scout(agent="codex", now_utc=NOW)
    assert first is not None
    events_path = _events_file(
        bridge,
        [
            {
                "ts_utc": "2026-01-01T00:00:00Z",
                "agent": "codex",
                "type": "heartbeat",
                "task_id": "baseline",
                "status": "active",
                "message": "background heartbeat",
            },
            *completed_smokes,
            *completed_dreams,
        ],
    )
    _claims_dir(bridge)
    claim_task(
        agent="claude",
        task_id=first["task_id_suggestion"],
        summary="same-day operational scout already claimed by another agent",
        mode="read-only",
        bridge_root=bridge,
        now_utc=NOW,
    )

    report = evaluate_agent_next_task(
        agent="codex",
        events_path=events_path,
        bridge_root=bridge,
        now_utc=NOW,
    )

    candidate = report["candidate"]
    assert report["decision"] == "claim_operational_scout"
    assert candidate["task_id_suggestion"] != first["task_id_suggestion"]
    assert candidate["rotation"]["offset"] == 1
    assert report["active_operational_scout_task_ids"] == [first["task_id_suggestion"]]
    assert (
        first["task_id_suggestion"] in candidate["rotation"]["skipped_active_task_ids"]
    )


def test_completed_daily_pools_fall_back_to_continuous_operational_scout(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    completed_smokes = [
        {
            "ts_utc": "2026-05-20T12:10:00Z",
            "agent": "codex",
            "type": "done",
            "task_id": f"codex-substrate-smoke-2026-05-20-{index}",
            "status": "done",
            "message": "daily substrate smoke passed",
        }
        for index in range(len(SUBSTRATE_SMOKE_CANDIDATES))
    ]
    completed_dreams = [
        {
            "ts_utc": "2026-05-20T12:20:00Z",
            "agent": "claude",
            "type": "done",
            "task_id": (f"dream-mode-{entry['category']}-{entry['slug']}-2026-05-20"),
            "status": "done",
            "message": "dream-mode seed completed",
        }
        for entry in DREAM_MODE_CANDIDATES
    ]
    completed_operational = [
        {
            "ts_utc": "2026-05-20T12:30:00Z",
            "agent": "codex",
            "type": "done",
            "task_id": f"operational-scout-{entry['slug']}-2026-05-20",
            "status": "done",
            "message": "operational scout completed",
        }
        for entry in OPERATIONAL_SCOUT_CANDIDATES
    ]
    events_path = _events_file(
        bridge,
        [
            {
                "ts_utc": "2026-01-01T00:00:00Z",
                "agent": "codex",
                "type": "heartbeat",
                "task_id": "baseline",
                "status": "active",
                "message": "background heartbeat",
            },
            *completed_smokes,
            *completed_dreams,
            *completed_operational,
        ],
    )
    _claims_dir(bridge)

    report = evaluate_agent_next_task(
        agent="codex",
        events_path=events_path,
        bridge_root=bridge,
        now_utc=NOW,
    )

    candidate = report["candidate"]
    assert report["decision"] == "claim_continuous_operational_scout"
    assert report["next_action"] == "claim_and_run"
    assert candidate["kind"] == "continuous_operational_read_only_scout"
    assert candidate["mode"] == "read-only"
    assert candidate["write_scope"] == []
    assert candidate["task_id_suggestion"].startswith("continuous-operational-scout-")
    assert candidate["task_id_suggestion"].endswith("-2026-05-20-0")
    assert candidate["recommended_command"]
    assert len(report["completed_operational_scout_task_ids"]) == len(
        OPERATIONAL_SCOUT_CANDIDATES
    )
    assert report["completed_continuous_operational_scout_task_ids"] == []
    exhaustion = report["continuous_operational_scout_state"]
    assert exhaustion["daily_pools_exhausted"] is True
    assert exhaustion["authority"] == "read_only_recommendation_only"
    assert exhaustion["continuous_sequence"] == 0
    assert exhaustion["continuous_cycle"] == 0
    assert exhaustion["completed_continuous_count"] == 0
    assert exhaustion["active_continuous_count"] == 0
    counts = exhaustion["daily_pool_counts"]
    assert counts["substrate_smoke"] == {
        "completed": len(SUBSTRATE_SMOKE_CANDIDATES),
        "active": 0,
        "pool_size": len(SUBSTRATE_SMOKE_CANDIDATES),
    }
    assert counts["dream_mode"] == {
        "completed": len(DREAM_MODE_CANDIDATES),
        "active": 0,
        "pool_size": len(DREAM_MODE_CANDIDATES),
    }
    assert counts["operational_scout"] == {
        "completed": len(OPERATIONAL_SCOUT_CANDIDATES),
        "active": 0,
        "pool_size": len(OPERATIONAL_SCOUT_CANDIDATES),
    }
    assert counts["continuous_operational_scout"] == {
        "completed": 0,
        "active": 0,
        "pool_size": len(OPERATIONAL_SCOUT_CANDIDATES),
    }


def test_completed_continuous_operational_scout_advances_sequence(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    base_events = [
        {
            "ts_utc": "2026-01-01T00:00:00Z",
            "agent": "codex",
            "type": "heartbeat",
            "task_id": "baseline",
            "status": "active",
            "message": "background heartbeat",
        },
        *[
            {
                "ts_utc": "2026-05-20T12:10:00Z",
                "agent": "codex",
                "type": "done",
                "task_id": f"codex-substrate-smoke-2026-05-20-{index}",
                "status": "done",
                "message": "daily substrate smoke passed",
            }
            for index in range(len(SUBSTRATE_SMOKE_CANDIDATES))
        ],
        *[
            {
                "ts_utc": "2026-05-20T12:20:00Z",
                "agent": "claude",
                "type": "done",
                "task_id": (
                    f"dream-mode-{entry['category']}-{entry['slug']}-2026-05-20"
                ),
                "status": "done",
                "message": "dream-mode seed completed",
            }
            for entry in DREAM_MODE_CANDIDATES
        ],
        *[
            {
                "ts_utc": "2026-05-20T12:30:00Z",
                "agent": "codex",
                "type": "done",
                "task_id": f"operational-scout-{entry['slug']}-2026-05-20",
                "status": "done",
                "message": "operational scout completed",
            }
            for entry in OPERATIONAL_SCOUT_CANDIDATES
        ],
    ]
    events_path = _events_file(bridge, base_events)
    _claims_dir(bridge)
    first = evaluate_agent_next_task(
        agent="codex",
        events_path=events_path,
        bridge_root=bridge,
        now_utc=NOW,
    )
    first_task_id = first["candidate"]["task_id_suggestion"]

    events_path = _events_file(
        bridge,
        [
            *base_events,
            {
                "ts_utc": "2026-05-20T12:40:00Z",
                "agent": "codex",
                "type": "done",
                "task_id": first_task_id,
                "status": "complete",
                "message": "continuous operational scout completed",
            },
        ],
    )

    report = evaluate_agent_next_task(
        agent="codex",
        events_path=events_path,
        bridge_root=bridge,
        now_utc=NOW,
    )

    candidate = report["candidate"]
    assert report["decision"] == "claim_continuous_operational_scout"
    assert candidate["task_id_suggestion"] != first_task_id
    assert candidate["rotation"]["sequence"] == 1
    assert report["completed_continuous_operational_scout_task_ids"] == [first_task_id]
    exhaustion = report["continuous_operational_scout_state"]
    assert exhaustion["daily_pools_exhausted"] is True
    assert exhaustion["continuous_sequence"] == 1
    assert exhaustion["continuous_cycle"] == 0
    assert exhaustion["completed_continuous_count"] == 1
    assert exhaustion["daily_pool_counts"]["continuous_operational_scout"] == {
        "completed": 1,
        "active": 0,
        "pool_size": len(OPERATIONAL_SCOUT_CANDIDATES),
    }


# ---------------------------------------------------------------------------
# rotation determinism
# ---------------------------------------------------------------------------


def test_same_agent_same_day_picks_same_candidate() -> None:
    first = _pick_substrate_smoke(agent="claude", now_utc=NOW)
    second = _pick_substrate_smoke(agent="claude", now_utc=NOW)
    assert first == second


def test_different_agents_get_different_salts() -> None:
    claude = _pick_substrate_smoke(agent="claude", now_utc=NOW)
    codex = _pick_substrate_smoke(agent="codex", now_utc=NOW)
    # salts differ because "claude" and "codex" hash to different ord-sums
    assert claude["rotation"]["agent_salt"] != codex["rotation"]["agent_salt"]


def test_rotation_advances_with_day_of_year() -> None:
    later_day = datetime(2026, 5, 21, 12, 0, 0, tzinfo=timezone.utc)
    today_pick = _pick_substrate_smoke(agent="claude", now_utc=NOW)
    tomorrow_pick = _pick_substrate_smoke(agent="claude", now_utc=later_day)
    assert tomorrow_pick["rotation"]["index"] == (
        today_pick["rotation"]["index"] + 1
    ) % len(SUBSTRATE_SMOKE_CANDIDATES)


def test_rotation_wraps_around_pool() -> None:
    pool_size = len(SUBSTRATE_SMOKE_CANDIDATES)
    today = _pick_substrate_smoke(agent="claude", now_utc=NOW)
    wrap_day = datetime(NOW.year, NOW.month, NOW.day, tzinfo=timezone.utc).replace(
        year=NOW.year + (NOW.timetuple().tm_yday + pool_size) // 366
    )
    # simpler: bump by exactly pool_size days using ordinal
    from datetime import timedelta

    wrap_day = NOW + timedelta(days=pool_size)
    wrap_pick = _pick_substrate_smoke(agent="claude", now_utc=wrap_day)
    assert wrap_pick["rotation"]["index"] == today["rotation"]["index"]


def test_candidates_pool_is_nonempty_and_each_entry_is_well_formed() -> None:
    assert SUBSTRATE_SMOKE_CANDIDATES
    for entry in SUBSTRATE_SMOKE_CANDIDATES:
        assert entry["target"]
        assert entry["rationale"]
    assert DREAM_MODE_CANDIDATES
    for entry in DREAM_MODE_CANDIDATES:
        assert entry["category"]
        assert entry["slug"]
        assert entry["target"]
        assert entry["rationale"]
    assert OPERATIONAL_SCOUT_CANDIDATES
    for entry in OPERATIONAL_SCOUT_CANDIDATES:
        assert entry["slug"]
        assert entry["target"]
        assert entry["rationale"]
        assert entry.get("recommended_command") or entry.get(
            "recommended_command_template"
        )


# ---------------------------------------------------------------------------
# read-only contract
# ---------------------------------------------------------------------------


def test_tick_does_not_write_bridge_events_or_claims(tmp_path: Path) -> None:
    bridge, events_path, claims_dir = _empty_bridge(tmp_path)
    events_before = events_path.read_text(encoding="utf-8")
    claims_before = sorted(p.name for p in claims_dir.iterdir())

    evaluate_agent_next_task(
        agent="claude",
        events_path=events_path,
        bridge_root=bridge,
        now_utc=NOW,
    )

    assert events_path.read_text(encoding="utf-8") == events_before
    assert sorted(p.name for p in claims_dir.iterdir()) == claims_before


def test_evaluation_is_deterministic_for_identical_inputs(tmp_path: Path) -> None:
    bridge, events_path, claims_dir = _empty_bridge(tmp_path)
    first = evaluate_agent_next_task(
        agent="claude",
        events_path=events_path,
        bridge_root=bridge,
        now_utc=NOW,
    )
    second = evaluate_agent_next_task(
        agent="claude",
        events_path=events_path,
        bridge_root=bridge,
        now_utc=NOW,
    )
    assert first["decision"] == second["decision"]
    assert first["candidate"] == second["candidate"]


# ---------------------------------------------------------------------------
# CLI smoke
# ---------------------------------------------------------------------------


def test_cli_main_emits_json(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    bridge, events_path, claims_dir = _empty_bridge(tmp_path)

    exit_code = main(
        [
            "--agent",
            "claude",
            "--events",
            str(events_path),
            "--bridge-root",
            str(bridge),
            "--now",
            "2026-05-20T12:00:00Z",
            "--json",
        ]
    )

    assert exit_code == 0
    out = capsys.readouterr().out.strip()
    parsed = json.loads(out)
    assert parsed["decision"] == "claim_substrate_smoke"
    assert parsed["candidate"]["kind"] == "run_substrate_smoke"


def test_cli_infers_bridge_root_from_events_path_for_claims(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    bridge, events_path, claims_dir = _empty_bridge(tmp_path)
    claim_task(
        agent="claude",
        task_id="claude-real-bridge-root-claim",
        summary="canonical claim under inferred bridge_root",
        mode="write",
        write_scope=["tools/y.py"],
        bridge_root=bridge,
    )

    exit_code = main(
        [
            "--agent",
            "claude",
            "--events",
            str(events_path),
            "--now",
            "2026-05-20T12:00:00Z",
            "--json",
        ]
    )

    assert exit_code == 0
    out = capsys.readouterr().out.strip()
    parsed = json.loads(out)
    assert parsed["decision"] == "defer_to_bridge_next_action"
    assert parsed["bridge_recommendation"]["action"] == "continue_claim"
    assert parsed["bridge_recommendation"]["task_id"] == "claude-real-bridge-root-claim"


def test_cli_defaults_to_runtime_bridge_root_env(
    tmp_path: Path,
    capsys: pytest.CaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_bridge = tmp_path / "runtime" / ".agent-bridge"
    _events_file(
        runtime_bridge,
        [
            {
                "ts_utc": "2026-05-20T11:59:00Z",
                "agent": "codex",
                "type": "heartbeat",
                "task_id": "runtime-baseline",
                "status": "active",
                "message": "runtime bridge heartbeat",
            }
        ],
    )
    claim_task(
        agent="claude",
        task_id="claude-runtime-bridge-root-claim",
        summary="canonical claim under runtime bridge root",
        mode="write",
        write_scope=["tools/runtime.py"],
        bridge_root=runtime_bridge,
    )

    monkeypatch.setenv("AGENT_BRIDGE_RUNTIME_ROOT", str(runtime_bridge))
    monkeypatch.delenv("AGENT_BRIDGE_ROOT", raising=False)

    exit_code = main(
        [
            "--agent",
            "claude",
            "--now",
            "2026-05-20T12:00:00Z",
            "--json",
        ]
    )

    assert exit_code == 0
    parsed = json.loads(capsys.readouterr().out.strip())
    assert parsed["decision"] == "defer_to_bridge_next_action"
    assert parsed["bridge_recommendation"]["action"] == "continue_claim"
    assert parsed["bridge_recommendation"]["task_id"] == (
        "claude-runtime-bridge-root-claim"
    )


def test_cli_bridge_root_without_events_uses_bridge_root_events(
    tmp_path: Path,
    capsys: pytest.CaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = tmp_path / "real" / ".agent-bridge"
    events_path = _events_file(
        bridge,
        [
            {
                "ts_utc": "2026-05-20T11:59:00Z",
                "agent": "codex-lead-1",
                "to": "claude",
                "type": "message",
                "task_id": "real-bridge-root-request",
                "status": "request",
                "message": "request from the explicit bridge root",
            }
        ],
    )
    _claims_dir(bridge)

    shadow_bridge = tmp_path / "shadow" / ".agent-bridge"
    _events_file(
        shadow_bridge,
        [
            {
                "ts_utc": "2026-01-01T00:00:00Z",
                "agent": "codex",
                "type": "heartbeat",
                "task_id": "shadow-baseline",
                "status": "active",
                "message": "cwd-local shadow bridge baseline",
            }
        ],
    )
    _claims_dir(shadow_bridge)
    monkeypatch.chdir(shadow_bridge.parent)

    exit_code = main(
        [
            "--agent",
            "claude",
            "--bridge-root",
            str(bridge),
            "--now",
            "2026-05-20T12:00:00Z",
            "--json",
        ]
    )

    assert exit_code == 0
    out = capsys.readouterr().out.strip()
    parsed = json.loads(out)
    assert parsed["decision"] == "defer_to_bridge_next_action"
    assert parsed["bridge_recommendation"]["action"] == "answer_incoming"
    assert parsed["bridge_recommendation"]["task_id"] == "real-bridge-root-request"


# ---------------------------------------------------------------------------
# regression: bridge_root is the authoritative source for claim loading
# ---------------------------------------------------------------------------


def test_bridge_root_is_authoritative_for_claims_not_some_external_dir(
    tmp_path: Path,
) -> None:
    """The tool MUST honor the active claim under bridge_root/work_queue/claims.

    Earlier the CLI accepted a separate ``--claims-dir`` argument that the
    evaluator silently ignored, so a wrapper that pointed ``claims_dir`` at the
    real bridge state while passing an empty ``bridge_root`` would receive a
    bogus ``claim_substrate_smoke`` recommendation instead of
    ``continue_claim``. This regression locks down the single-source contract:
    claims are loaded only from ``bridge_root/work_queue/claims``.
    """
    bridge = tmp_path / ".agent-bridge"
    events_path = _events_file(
        bridge,
        [
            {
                "ts_utc": "2026-01-01T00:00:00Z",
                "agent": "codex",
                "type": "heartbeat",
                "task_id": "baseline",
                "status": "active",
                "message": "background heartbeat",
            }
        ],
    )
    # An orphan claims directory that does NOT live under bridge_root.
    # Earlier code would have happily consulted it; the contract is now to
    # ignore it entirely.
    orphan_claims = tmp_path / "orphan_claims"
    orphan_claims.mkdir()
    (orphan_claims / "claude-shadow-claim.json").write_text(
        json.dumps(
            {
                "task_id": "claude-shadow-claim",
                "agent": "claude",
                "summary": "shadow claim that lives outside bridge_root",
                "mode": "write",
                "write_scope": ["tools/x.py"],
                "claimed_at_utc": "2026-05-20T11:00:00Z",
                "last_heartbeat_utc": "2026-05-20T11:59:00Z",
                "lease_seconds": 900,
                "run_id": "claude-test",
                "pid": 0,
                "cwd": "",
                "git_branch": "main",
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    # bridge_root has no real claim — the canonical claims dir is empty.
    _claims_dir(bridge)

    report = evaluate_agent_next_task(
        agent="claude",
        events_path=events_path,
        bridge_root=bridge,
        now_utc=NOW,
    )

    # Because the active claim lives outside bridge_root, the tool must NOT
    # see it; the bridge state is "no active claim" and the recommendation
    # is to pick a substrate-smoke candidate. The earlier bug would have
    # returned defer_to_bridge_next_action / continue_claim by reading the
    # orphan directory through a separate --claims-dir parameter.
    assert report["decision"] == "claim_substrate_smoke"
    assert report["bridge_recommendation"]["active_claim_count"] == 0


def test_active_claim_under_bridge_root_yields_continue_claim_regardless_of_external_dirs(
    tmp_path: Path,
) -> None:
    """Mirror of the regression above with the situation reversed.

    When the real active claim DOES live under ``bridge_root``, the tool
    must defer to ``continue_claim`` even if an unrelated directory tree
    happens to exist nearby. This proves the bridge_root path is read
    correctly and that no external state can shadow it.
    """
    bridge = tmp_path / ".agent-bridge"
    events_path = _events_file(
        bridge,
        [
            {
                "ts_utc": "2026-01-01T00:00:00Z",
                "agent": "codex",
                "type": "heartbeat",
                "task_id": "baseline",
                "status": "active",
                "message": "background heartbeat",
            }
        ],
    )
    _claims_dir(bridge)
    claim_task(
        agent="claude",
        task_id="claude-real-bridge-root-claim",
        summary="canonical claim under bridge_root",
        mode="write",
        write_scope=["tools/y.py"],
        bridge_root=bridge,
    )
    # An unrelated directory tree that should be ignored.
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()

    report = evaluate_agent_next_task(
        agent="claude",
        events_path=events_path,
        bridge_root=bridge,
        now_utc=NOW,
    )

    assert report["decision"] == "defer_to_bridge_next_action"
    assert report["bridge_recommendation"]["action"] == "continue_claim"
    assert report["bridge_recommendation"]["task_id"] == "claude-real-bridge-root-claim"
