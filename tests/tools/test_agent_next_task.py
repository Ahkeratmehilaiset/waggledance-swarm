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
    assert (
        items["production_two_agent_activation_loop"]["state"]
        == "partial_read_only_ready"
    )
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

    assert report["decision"] == "defer_to_bridge_next_action"
    assert report["bridge_recommendation"]["action"] == "answer_incoming"
    assert report["bridge_recommendation"]["task_id"] == "bridge-follow-nudge-20260520"
    assert report["bridge_recommendation"]["incoming"]["type"] == "wake_request"


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


def test_does_not_prioritize_non_peer_rco_liveness_for_lead(tmp_path: Path) -> None:
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

    assert report["decision"] == "claim_substrate_smoke"
    assert report["candidate"]["kind"] == "run_substrate_smoke"
    assert report["bridge_recommendation"]["production_liveness"][
        "stalled_agent_count"
    ] == 1


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
