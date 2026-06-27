# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from tools.build_runtime_chain_status_dashboard import (
    REPORT_VERSION,
    build_runtime_chain_status_dashboard,
    render_markdown,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "build_runtime_chain_status_dashboard.py"
FIXED_NOW = datetime(2026, 6, 27, 11, 10, tzinfo=timezone.utc)
HEAD_1409 = "239792591bd174eca1da5d3bd1ea84e6383823c3"
MERGE_1408 = "b7ee1a245e08473abaea8677ee2b567f5eb6ac9e"
TASK_1409 = "fable-5/hex-subdivision-runtime-pipeline-e2e-proof-20260627"
TASK_1408 = "codex-lead-1/hex-subdivision-runtime-executor-admission-20260627"


def _event(
    *,
    agent: str,
    event_type: str,
    task_id: str,
    status: str,
    ts: str = "2026-06-27T11:00:00Z",
    message: str = "",
    payload: dict | None = None,
    paths: list[str] | None = None,
) -> dict:
    return {
        "ts_utc": ts,
        "agent": agent,
        "type": event_type,
        "task_id": task_id,
        "status": status,
        "to": "codex-tools-1",
        "message": message,
        "payload": payload or {},
        "paths": paths or [],
        "cwd": "C:\\Python\\project2-master",
    }


def _write_events(path: Path, events: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(event, sort_keys=True) for event in events) + "\n",
        encoding="utf-8",
    )


def test_dashboard_summarizes_open_runtime_pr_gate_ready() -> None:
    report = build_runtime_chain_status_dashboard(
        events=[
            _event(
                agent="fable-5",
                event_type="handoff",
                task_id=TASK_1409,
                status="pr_opened_review_requested",
                message=f"PR #1409 opened at exact head {HEAD_1409}",
            ),
            _event(
                agent="codex-tools-1",
                event_type="decision",
                task_id=TASK_1409,
                status="build_consensus_pass",
                message="CI 6/6 green; observability-only, no runtime authority.",
                payload={"pr": 1409, "head": HEAD_1409},
            ),
            _event(
                agent="codex-lead-1",
                event_type="decision",
                task_id=TASK_1409,
                status="build_consensus_pass",
                message="Lead build pass; no topology mutation.",
                payload={"pr": 1409, "head": HEAD_1409},
            ),
            _event(
                agent="claude-rco-1",
                event_type="decision",
                task_id=TASK_1409,
                status="rco_pass",
                message="RCO_PASS for PR #1409.",
                payload={"pr": 1409, "head": HEAD_1409},
            ),
        ],
        now_utc=FIXED_NOW,
    )

    assert report["report_version"] == REPORT_VERSION
    assert report["ok"] is True
    assert report["source"]["messages_redacted"] is True
    assert report["authority_boundary"]["merge_allowed"] is False
    assert len(report["stages"]) == 1
    stage = report["stages"][0]
    assert stage["pr_number"] == 1409
    assert stage["head_sha_prefix"] == HEAD_1409[:12]
    assert stage["state"] == "gate_complete_open"
    assert stage["next_gate"] == "merge"
    assert stage["build_consensus_agents"] == ["codex-lead-1", "codex-tools-1"]
    assert stage["missing_build_consensus_agents"] == []
    assert stage["rco_pass_agents"] == ["claude-rco-1"]
    assert stage["ci_state"] == "green"
    assert stage["safety_state"] == "no_live_runtime_claim_seen"


def test_dashboard_flags_missing_lead_build_before_merge() -> None:
    report = build_runtime_chain_status_dashboard(
        events=[
            _event(
                agent="codex-tools-1",
                event_type="decision",
                task_id=TASK_1409,
                status="build_consensus_pass",
                message="PR #1409 CI 6/6 green.",
                payload={"pr": 1409, "head": HEAD_1409},
            ),
            _event(
                agent="claude-rco-2",
                event_type="decision",
                task_id=TASK_1409,
                status="rco_pass",
                message="RCO_PASS; build slot still needs codex-lead-1.",
                payload={"pr": 1409, "head": HEAD_1409},
            ),
        ],
        now_utc=FIXED_NOW,
    )

    stage = report["stages"][0]
    assert stage["state"] == "blocked_or_incomplete"
    assert stage["next_gate"] == "build_consensus"
    assert stage["blockers"] == ["missing_build_consensus:codex-lead-1"]
    assert report["summary"]["active_blocker_count"] == 1


def test_dashboard_does_not_treat_clear_changes_requested_text_as_blocker() -> None:
    report = build_runtime_chain_status_dashboard(
        events=[
            _event(
                agent="codex-tools-1",
                event_type="decision",
                task_id=TASK_1409,
                status="build_consensus_pass",
                message="bridge changes_requested clear; CI 6/6 green",
                payload={"pr": 1409, "head": HEAD_1409},
            ),
            _event(
                agent="codex-lead-1",
                event_type="decision",
                task_id=TASK_1409,
                status="build_consensus_pass",
                message="lead pass",
                payload={"pr": 1409, "head": HEAD_1409},
            ),
            _event(
                agent="claude-rco-1",
                event_type="decision",
                task_id=TASK_1409,
                status="rco_pass",
                message="RCO_PASS; no live runtime.",
                payload={"pr": 1409, "head": HEAD_1409},
            ),
        ],
        now_utc=FIXED_NOW,
    )

    stage = report["stages"][0]
    assert stage["blockers"] == []
    assert stage["state"] == "gate_complete_open"
    assert stage["next_gate"] == "merge"
    assert stage["safety_state"] == "no_live_runtime_claim_seen"


def test_dashboard_tracks_merged_post_merge_ci_pending_and_green() -> None:
    pending = build_runtime_chain_status_dashboard(
        events=[
            _event(
                agent="codex-lead-1",
                event_type="done",
                task_id=TASK_1408,
                status="merged_post_merge_ci_pending",
                message=(
                    "PR #1408 merged by codex-lead-1. "
                    f"Merge commit/main head {MERGE_1408}. "
                    "Post-merge main CI started and is in_progress."
                ),
                payload={"pr": 1408},
            ),
        ],
        now_utc=FIXED_NOW,
    )

    pending_stage = pending["stages"][0]
    assert pending_stage["state"] == "merged_post_merge_ci_pending"
    assert pending_stage["next_gate"] == "post_merge_main_ci"
    assert pending_stage["merge_commit_prefix"] == MERGE_1408[:12]
    assert pending_stage["ci_state"] == "running"

    green = build_runtime_chain_status_dashboard(
        events=[
            _event(
                agent="codex-tools-1",
                event_type="test",
                task_id=TASK_1408,
                status="post_merge_main_ci_green",
                message="Post-merge main CI green for PR #1408.",
                payload={"pr": 1408},
            ),
        ],
        now_utc=FIXED_NOW,
    )

    green_stage = green["stages"][0]
    assert green_stage["state"] == "post_merge_main_ci_green"
    assert green_stage["next_gate"] == "complete"


def test_dashboard_refuses_redaction_sentinel_and_keeps_output_path_free() -> None:
    marker = "PRIVATE" + "_MARKER"
    report = build_runtime_chain_status_dashboard(
        events=[
            _event(
                agent="codex-tools-1",
                event_type="message",
                task_id=TASK_1409,
                status="active",
                message=f"contains {marker} C:\\Python\\project2-master\\secret.txt",
            )
        ],
        now_utc=FIXED_NOW,
    )

    encoded = json.dumps(report, sort_keys=True)
    assert report["ok"] is False
    assert report["blockers"] == ["events_input_refused:redaction_sentinel_present"]
    assert "C:\\Python" not in encoded
    assert "secret.txt" not in encoded


def test_cli_reads_events_and_outputs_json(tmp_path: Path) -> None:
    events_path = tmp_path / "events.jsonl"
    _write_events(
        events_path,
        [
            _event(
                agent="codex-tools-1",
                event_type="decision",
                task_id=TASK_1409,
                status="build_consensus_pass",
                message="PR #1409 CI 6/6 green.",
                payload={"pr": 1409, "head": HEAD_1409},
            )
        ],
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--events",
            str(events_path),
            "--required-build-agent",
            "codex-tools-1",
            "--json",
            "--now",
            "2026-06-27T11:10:00Z",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    report = json.loads(completed.stdout)

    assert report["generated_at_utc"] == "2026-06-27T11:10:00Z"
    assert report["stages"][0]["pr_number"] == 1409
    assert report["stages"][0]["next_gate"] == "rco_pass"


def test_markdown_renderer_is_compact() -> None:
    report = build_runtime_chain_status_dashboard(
        events=[
            _event(
                agent="codex-tools-1",
                event_type="decision",
                task_id=TASK_1409,
                status="build_consensus_pass",
                message="PR #1409 CI 6/6 green.",
                payload={"pr": 1409, "head": HEAD_1409},
            )
        ],
        required_build_agents=("codex-tools-1",),
        now_utc=FIXED_NOW,
    )

    rendered = render_markdown(report)
    assert "# Runtime Chain Status" in rendered
    assert "| 1409 |" in rendered
    assert "C:\\Python" not in rendered
