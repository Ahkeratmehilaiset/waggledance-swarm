# SPDX-License-Identifier: BUSL-1.1
"""Tests for the opt-in bridge idle detection primitive."""
from __future__ import annotations

import importlib
import io
import json
from pathlib import Path
import subprocess
import sys
from contextlib import redirect_stdout


NOW = "2026-05-17T12:00:00Z"


def _event(
    *,
    ts_utc: str,
    agent: str = "codex",
    type: str = "message",
    task_id: str = "idle-smoke",
    status: str = "note",
    to: str = "claude",
    message: str = "Substantive bridge content that should count as agent activity.",
) -> dict[str, object]:
    return {
        "ts_utc": ts_utc,
        "agent": agent,
        "type": type,
        "task_id": task_id,
        "status": status,
        "severity": "",
        "to": to,
        "message": message,
        "paths": [],
        "write_scope": [],
        "run_id": "",
        "pid": 1234,
        "cwd": "C:\\Python\\project2-master",
        "payload": {},
    }


def _write_events(path: Path, events: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(event, sort_keys=True) + "\n" for event in events),
        encoding="utf-8",
    )


def _base_idle_events() -> list[dict[str, object]]:
    return [
        _event(
            ts_utc="2026-05-17T10:30:00Z",
            type="done",
            status="merged_postmerge_green",
            message="PR merged with postmerge verification green.",
        ),
        _event(
            ts_utc="2026-05-17T10:31:00Z",
            agent="claude",
            type="message",
            status="scout_recommendation",
            message="A substantive design response with concrete scope, tests, and risks.",
        ),
        _event(
            ts_utc="2026-05-17T10:32:00Z",
            agent="operator",
            type="message",
            status="operator_note",
            to="codex",
            message="Operator-directed bridge note older than the idle window.",
        ),
    ]


def _run(
    tmp_path: Path,
    events: list[dict[str, object]],
    *args: str,
    claims: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    mod = importlib.import_module("tools.idle_check")
    events_path = tmp_path / "events.jsonl"
    claims_dir = tmp_path / "claims"
    claims_dir.mkdir(exist_ok=True)
    _write_events(events_path, events)
    for index, claim in enumerate(claims or []):
        (claims_dir / f"claim-{index}.json").write_text(
            json.dumps(claim, sort_keys=True),
            encoding="utf-8",
        )

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        rc = mod.main(
            [
                "--events",
                str(events_path),
                "--claims-dir",
                str(claims_dir),
                "--now",
                NOW,
                "--json",
                *args,
            ]
        )

    assert rc == 0
    return json.loads(stdout.getvalue())


def test_idle_when_all_predicates_are_quiet_for_the_window(tmp_path: Path) -> None:
    payload = _run(tmp_path, _base_idle_events())

    assert payload["decision"] == "idle"
    assert payload["idle"] is True
    assert payload["blockers"] == []


def test_pending_ci_count_keeps_bridge_active(tmp_path: Path) -> None:
    payload = _run(tmp_path, _base_idle_events(), "--pending-ci-count", "1")

    assert payload["decision"] == "active"
    assert payload["idle"] is False
    assert "pending_ci" in payload["blockers"]


def test_open_work_claim_keeps_bridge_active_without_pr_or_rco(
    tmp_path: Path,
) -> None:
    payload = _run(
        tmp_path,
        _base_idle_events(),
        claims=[
            {
                "task_id": "long-running-implementation",
                "summary": "Agent is still implementing without a PR yet.",
                "claimed_at_utc": "2026-05-17T10:45:00Z",
            }
        ],
    )

    assert payload["decision"] == "active"
    assert "open_work_claims" in payload["blockers"]
    assert payload["criteria"]["open_work_claims"]["task_ids"] == [
        "long-running-implementation"
    ]


def test_open_scout_or_rco_request_keeps_bridge_active_until_answered(tmp_path: Path) -> None:
    open_scout = _base_idle_events() + [
        _event(
            ts_utc="2026-05-17T10:00:00Z",
            task_id="claude-scout-idle-dreaming",
            status="request_scout",
            message="Please scout idle dreaming risks.",
        )
    ]
    payload = _run(tmp_path, open_scout)
    assert payload["decision"] == "active"
    assert "open_scout_requests" in payload["blockers"]

    answered_scout = open_scout + [
        _event(
            ts_utc="2026-05-17T10:10:00Z",
            agent="claude",
            task_id="claude-scout-idle-dreaming",
            status="scout_answered",
            message="Scout answered with risks and smallest safe scope.",
        )
    ]
    payload = _run(tmp_path, answered_scout)
    assert payload["decision"] == "idle"

    open_rco = _base_idle_events() + [
        _event(
            ts_utc="2026-05-17T10:00:00Z",
            type="handoff",
            task_id="claude-rco-pr999",
            status="rco_requested",
            message="RCO requested for PR #999.",
        )
    ]
    payload = _run(tmp_path, open_rco)
    assert payload["decision"] == "active"
    assert "open_rco_requests" in payload["blockers"]


def test_retroactive_stale_rco_closure_does_not_count_as_recent_merge(
    tmp_path: Path,
) -> None:
    events = _base_idle_events() + [
        _event(
            ts_utc="2026-05-17T10:00:00Z",
            type="handoff",
            task_id="stale-rco-pr508",
            status="rco_requested",
            message="RCO requested for PR #508.",
        ),
        _event(
            ts_utc="2026-05-17T11:55:00Z",
            agent="claude",
            type="done",
            task_id="stale-rco-pr508",
            status="merged",
            to="codex,operator",
            message=(
                "Retroactive close of stale RCO handoff for task stale-rco-pr508. "
                "PR #508 was merged earlier into main (merge commit 1e6ec5b0). "
                "Structural fix follows as a separate PR."
            ),
        ),
    ]

    payload = _run(tmp_path, events)

    assert payload["decision"] == "idle"
    assert payload["blockers"] == []
    assert payload["criteria"]["open_rco_requests"]["ok"] is True
    assert payload["criteria"]["recent_merge"]["ok"] is True


def test_stale_unclosed_request_is_reported_but_does_not_block_idle(
    tmp_path: Path,
) -> None:
    stale_scout = _base_idle_events() + [
        _event(
            ts_utc="2026-05-16T12:00:00Z",
            task_id="claude-scout-stale-historical-record",
            status="request_scout",
            message="Historical scout request missing its terminal bridge event.",
        )
    ]

    payload = _run(tmp_path, stale_scout)

    assert payload["decision"] == "idle"
    assert payload["criteria"]["stale_open_requests_ignored"]["task_ids"] == [
        "claude-scout-stale-historical-record"
    ]


def test_short_cron_poll_is_ignored_but_recent_substantive_message_is_active(
    tmp_path: Path,
) -> None:
    cron_only = _base_idle_events() + [
        _event(
            ts_utc="2026-05-17T11:50:00Z",
            agent="claude",
            status="cron_poll",
            message="cron poll heartbeat",
        )
    ]
    payload = _run(tmp_path, cron_only)
    assert payload["decision"] == "idle"

    substantive = cron_only + [
        _event(
            ts_utc="2026-05-17T11:55:00Z",
            agent="codex",
            status="request_scout",
            message="Substantive idle dreaming implementation note with concrete scope and risks.",
        )
    ]
    payload = _run(tmp_path, substantive)
    assert payload["decision"] == "active"
    assert "recent_agent_message" in payload["blockers"]

    multi_agent = cron_only + [
        _event(
            ts_utc="2026-05-17T11:55:00Z",
            agent="codex-2",
            status="request_scout",
            message="Substantive multi-agent bridge note with concrete scope and risks.",
        )
    ]
    payload = _run(tmp_path, multi_agent)
    assert payload["decision"] == "active"
    assert "recent_agent_message" in payload["blockers"]


def test_recent_merge_and_recent_operator_activity_keep_bridge_active(
    tmp_path: Path,
) -> None:
    recent_merge = _base_idle_events() + [
        _event(
            ts_utc="2026-05-17T11:30:00Z",
            type="done",
            status="merged_postmerge_green",
            message="PR merged thirty minutes ago.",
        )
    ]
    payload = _run(tmp_path, recent_merge)
    assert payload["decision"] == "active"
    assert "recent_merge" in payload["blockers"]

    recent_operator = _base_idle_events() + [
        _event(
            ts_utc="2026-05-17T11:45:00Z",
            agent="operator",
            type="message",
            status="operator_note",
            to="codex",
            message="Operator bridge note fifteen minutes ago.",
        )
    ]
    payload = _run(tmp_path, recent_operator)
    assert payload["decision"] == "active"
    assert "recent_operator_activity" in payload["blockers"]


def test_cli_runs_by_file_path_from_repo_root(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    events_path = tmp_path / "events.jsonl"
    claims_dir = tmp_path / "claims"
    claims_dir.mkdir()
    _write_events(events_path, _base_idle_events())

    completed = subprocess.run(
        [
            sys.executable,
            str(root / "tools" / "idle_check.py"),
            "--events",
            str(events_path),
            "--claims-dir",
            str(claims_dir),
            "--now",
            NOW,
            "--json",
        ],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert payload["decision"] == "idle"


def test_empty_bridge_returns_unknown_error(tmp_path: Path) -> None:
    mod = importlib.import_module("tools.idle_check")
    events_path = tmp_path / "events.jsonl"
    claims_dir = tmp_path / "claims"
    events_path.write_text("", encoding="utf-8")
    claims_dir.mkdir()

    rc = mod.main(
        [
            "--events",
            str(events_path),
            "--claims-dir",
            str(claims_dir),
            "--now",
            NOW,
            "--json",
        ]
    )

    assert rc == 2
