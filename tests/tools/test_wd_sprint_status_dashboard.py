# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from tools.build_wd_sprint_status_dashboard import (
    build_wd_sprint_status_dashboard,
    render_markdown,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "build_wd_sprint_status_dashboard.py"
FIXED_NOW = datetime(2026, 6, 11, 16, 30, tzinfo=timezone.utc)
FRESH_BASE = "b5e9a95197084d2709223496f37036f518181c7e"
STALE_BASE = "992c251cd4bd9a1ca2efc29aac97b4fd917ba9b9"
SPRINT_TASK = "wd-50pr-sprint-plan-20260611-v2"


def _event(
    *,
    agent: str,
    event_type: str,
    task_id: str,
    status: str,
    ts: str = "2026-06-11T15:50:00Z",
    message: str = "",
    payload: dict | None = None,
) -> dict:
    return {
        "ts_utc": ts,
        "agent": agent,
        "type": event_type,
        "task_id": task_id,
        "status": status,
        "to": "codex-lead-1",
        "message": message,
        "payload": payload or {},
        "paths": ["C:\\Python\\project2-master\\secret\\raw.txt"],
        "cwd": "C:\\Python\\project2-master",
    }


def _write_events(path: Path, events: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(event, sort_keys=True) for event in events) + "\n",
        encoding="utf-8",
    )


def test_dashboard_summarizes_sprint_consensus_without_authority() -> None:
    report = build_wd_sprint_status_dashboard(
        events=[
            _event(
                agent="codex-lead-1",
                event_type="message",
                task_id=SPRINT_TASK,
                status="consensus_requested",
                payload={"base": FRESH_BASE},
            ),
            _event(
                agent="codex-tools-1",
                event_type="decision",
                task_id=SPRINT_TASK,
                status="consensus_pass",
                payload={"base": FRESH_BASE},
            ),
            _event(
                agent="mythos",
                event_type="message",
                task_id=SPRINT_TASK,
                status="advisory",
            ),
        ],
        sprint_task_id=SPRINT_TASK,
        expected_base_sha=FRESH_BASE,
        now_utc=FIXED_NOW,
    )

    assert report["report_version"] == "wd.50pr_sprint_status_dashboard.v0"
    assert report["ok"] is True
    assert report["blockers"] == []
    assert report["source"]["source_redacted"] is True
    assert report["source"]["messages_redacted"] is True
    assert report["consensus"]["request_observed"] is True
    assert report["consensus"]["responses"] == {
        "codex-tools-1": {
            "status": "consensus_pass",
            "type": "decision",
            "ts_utc": "2026-06-11T15:50:00Z",
        }
    }
    assert report["consensus"]["missing_agents"] == [
        "claude-rco-1",
        "claude-rco-2",
        "operator",
    ]
    assert report["consensus"]["complete"] is False
    assert report["consensus"]["execution_allowed_by_report"] is False
    assert report["authority_boundary"] == {
        "read_only_report": True,
        "bridge_append_allowed": False,
        "queue_write_allowed": False,
        "scheduler_enqueue_allowed": False,
        "scheduler_tick_allowed": False,
        "runtime_activation_allowed": False,
        "merge_allowed": False,
        "network_required": False,
        "payload_export_allowed": False,
    }


def test_dashboard_tracks_active_claims_and_terminal_done_events() -> None:
    report = build_wd_sprint_status_dashboard(
        events=[
            _event(
                agent="codex-tools-1",
                event_type="claim",
                task_id="tools/active-contract-slice",
                status="active",
                ts="2026-06-11T16:07:59Z",
            ),
            _event(
                agent="codex-tools-1",
                event_type="claim",
                task_id="tools/completed-read-only-audit",
                status="active",
            ),
            _event(
                agent="codex-tools-1",
                event_type="test",
                task_id="tools/completed-read-only-audit",
                status="pass",
            ),
            _event(
                agent="codex-tools-1",
                event_type="done",
                task_id="tools/completed-read-only-audit",
                status="done",
            ),
            _event(
                agent="codex-tools-1",
                event_type="finding",
                task_id="tools/finding",
                status="improvement",
            ),
        ],
        now_utc=FIXED_NOW,
    )

    assert report["queue"]["active_claim_count"] == 1
    assert report["queue"]["active_claims"] == [
        {
            "agent": "codex-tools-1",
            "task_id": "tools/active-contract-slice",
            "started_at_utc": "2026-06-11T16:07:59Z",
        }
    ]
    assert report["queue"]["done_event_count"] == 1
    assert report["queue"]["test_pass_count"] == 1
    assert report["queue"]["finding_count"] == 1
    assert report["queue"]["queue_write_performed_by_report"] is False


def test_dashboard_reports_stale_base_without_leaking_paths_or_messages() -> None:
    report = build_wd_sprint_status_dashboard(
        events=[
            _event(
                agent="codex-lead-1",
                event_type="message",
                task_id="wd-50pr-sprint-plan-20260611",
                status="consensus_requested",
                message=(
                    "old base "
                    f"{STALE_BASE} from C:\\Python\\project2-master\\secret\\raw.txt"
                ),
                payload={"base": STALE_BASE},
            )
        ],
        expected_base_sha=FRESH_BASE,
        now_utc=FIXED_NOW,
    )

    encoded = json.dumps(report, sort_keys=True)
    assert report["stale_base"]["warning_count"] == 2
    assert report["stale_base"]["warnings"][0]["sha_prefix"] == STALE_BASE[:12]
    assert "C:\\Python" not in encoded
    assert "secret" not in encoded
    assert "old base" not in encoded


def test_dashboard_fails_closed_on_non_finite_event_json(tmp_path: Path) -> None:
    events_path = tmp_path / "events.jsonl"
    events_path.write_text(
        '{"ts_utc":"2026-06-11T16:00:00Z","agent":"codex-tools-1",'
        '"type":"message","task_id":"x","status":"ok","payload":{"base":NaN}}\n',
        encoding="utf-8",
    )

    report = build_wd_sprint_status_dashboard(
        events_path=events_path,
        now_utc=FIXED_NOW,
    )

    assert report["ok"] is False
    assert report["blockers"] == ["events_input_refused:non_finite_json:NaN"]
    assert report["source"]["event_count"] == 0
    assert report["authority_boundary"]["merge_allowed"] is False


def test_redaction_sentinel_guard_does_not_embed_exact_tokens() -> None:
    first_sentinel = "PRIVATE" + "_MARKER"
    second_sentinel = "_DO" + "_NOT" + "_LEAK"
    report = build_wd_sprint_status_dashboard(
        events=[
            _event(
                agent="codex-lead-1",
                event_type="message",
                task_id="sentinel-check",
                status="active",
                message=f"contains {first_sentinel}",
            )
        ],
        now_utc=FIXED_NOW,
    )

    source = (ROOT / "tools" / "build_wd_sprint_status_dashboard.py").read_text(
        encoding="utf-8"
    )
    assert first_sentinel not in source
    assert second_sentinel not in source
    assert report["ok"] is False
    assert report["blockers"] == ["events_input_refused:redaction_sentinel_present"]


def test_heartbeats_do_not_mask_substantive_stall() -> None:
    report = build_wd_sprint_status_dashboard(
        events=[
            _event(
                agent="codex-lead-1",
                event_type="decision",
                task_id=SPRINT_TASK,
                status="consensus_pass",
                ts="2026-06-11T15:50:00Z",
            ),
            _event(
                agent="codex-lead-1",
                event_type="heartbeat",
                task_id="",
                status="active",
                ts="2026-06-11T16:29:00Z",
            ),
            _event(
                agent="claude-rco-1",
                event_type="decision",
                task_id=SPRINT_TASK,
                status="rco_pass",
                ts="2026-06-11T16:25:00Z",
            ),
        ],
        sprint_task_id=SPRINT_TASK,
        expected_agents=("codex-lead-1", "claude-rco-1"),
        now_utc=FIXED_NOW,
    )

    agents = report["agent_activity"]
    lead = agents["latest_by_agent"]["codex-lead-1"]
    # The fresh heartbeat is the latest event of any type...
    assert lead["last_type"] == "heartbeat"
    # ...but liveness keys on the newest non-keepalive event.
    assert lead["last_substantive_type"] == "decision"
    assert lead["last_substantive_ts_utc"] == "2026-06-11T15:50:00Z"
    assert lead["substantive_gap_minutes"] == 40.0
    assert lead["stalled"] is True

    rco = agents["latest_by_agent"]["claude-rco-1"]
    assert rco["substantive_gap_minutes"] == 5.0
    assert rco["stalled"] is False

    assert agents["stalled_after_minutes"] == 12.0
    assert agents["stalled_expected_agents"] == ["codex-lead-1"]


def test_heartbeat_only_agent_counts_as_stalled() -> None:
    report = build_wd_sprint_status_dashboard(
        events=[
            _event(
                agent="codex-lead-1",
                event_type="heartbeat",
                task_id="",
                status="active",
                ts="2026-06-11T16:29:00Z",
            ),
        ],
        sprint_task_id=SPRINT_TASK,
        expected_agents=("codex-lead-1",),
        now_utc=FIXED_NOW,
    )

    lead = report["agent_activity"]["latest_by_agent"]["codex-lead-1"]
    assert lead["last_substantive_ts_utc"] == ""
    assert lead["substantive_gap_minutes"] is None
    assert lead["stalled"] is True
    assert report["agent_activity"]["stalled_expected_agents"] == ["codex-lead-1"]
    # Heartbeat-only presence still counts as "seen", not "missing".
    assert report["agent_activity"]["missing_expected_agents"] == []


def test_stalled_threshold_is_configurable() -> None:
    report = build_wd_sprint_status_dashboard(
        events=[
            _event(
                agent="claude-rco-1",
                event_type="decision",
                task_id=SPRINT_TASK,
                status="rco_pass",
                ts="2026-06-11T16:25:00Z",
            ),
        ],
        sprint_task_id=SPRINT_TASK,
        expected_agents=("claude-rco-1",),
        stalled_after_minutes=4.0,
        now_utc=FIXED_NOW,
    )

    rco = report["agent_activity"]["latest_by_agent"]["claude-rco-1"]
    assert rco["substantive_gap_minutes"] == 5.0
    assert rco["stalled"] is True
    assert report["agent_activity"]["stalled_expected_agents"] == ["claude-rco-1"]


def test_markdown_renders_path_free_dashboard_sections() -> None:
    report = build_wd_sprint_status_dashboard(
        events=[
            _event(
                agent="codex-lead-1",
                event_type="message",
                task_id=SPRINT_TASK,
                status="consensus_requested",
            )
        ],
        sprint_task_id=SPRINT_TASK,
        now_utc=FIXED_NOW,
    )

    markdown = render_markdown(report)

    assert "# WD 50-PR Sprint Status Dashboard" in markdown
    assert "## Consensus" in markdown
    assert "## Queue" in markdown
    assert "expected agents stalled (no non-keepalive event in 12.0min): `0`" in markdown
    assert "bridge append allowed: `false`" in markdown
    assert "scheduler enqueue allowed: `false`" in markdown
    assert "C:\\Python" not in markdown


def test_cli_json_smoke(tmp_path: Path) -> None:
    events_path = tmp_path / "events.jsonl"
    _write_events(
        events_path,
        [
            _event(
                agent="codex-lead-1",
                event_type="message",
                task_id=SPRINT_TASK,
                status="consensus_requested",
                payload={"base": FRESH_BASE},
            ),
            _event(
                agent="codex-tools-1",
                event_type="decision",
                task_id=SPRINT_TASK,
                status="consensus_pass",
                payload={"base": FRESH_BASE},
            ),
        ],
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--events",
            str(events_path),
            "--sprint-task-id",
            SPRINT_TASK,
            "--expected-base-sha",
            FRESH_BASE,
            "--now",
            "2026-06-11T16:30:00Z",
            "--json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    assert payload["consensus"]["responded_agents"] == ["codex-tools-1"]
    assert payload["stale_base"]["warning_count"] == 0
    assert payload["authority_boundary"]["queue_write_allowed"] is False
