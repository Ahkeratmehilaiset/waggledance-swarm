# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools.idle_protocol_session import SessionError, run_idle_protocol_session

def _proposal() -> dict:
    return {
        "protocol_version": "idle-protocol.v1",
        "event_type": "idle_proposal",
        "proposal_id": "idle-prop-20260518-001",
        "round_number": 1,
        "proposes_substrate_change": True,
        "problem_statement": "Strategic idle deliberation needs a next peer response.",
        "proposal": "Request a peer counter proposal without creating implementation work.",
        "tradeoff_axis": "Structured bridge progression versus unmanaged idle discussion drift.",
        "simulation_evidence": {
            "kind": "scenario_simulation",
            "summary": "A round one payload causes the session tool to request round two.",
        },
        "charter_alignment": {
            "compatible": True,
            "reasoning": "The request is local bridge coordination and keeps operator approval.",
        },
    }


def _consensus(proposal_id: str = "idle-prop-20260518-005a") -> dict:
    payload = _proposal()
    payload.update(
        {
            "event_type": "idle_consensus_reached",
            "proposal_id": proposal_id,
            "round_number": 5,
            "proposes_substrate_change": False,
            "consensus_target_proposal_id": "idle-prop-20260518-001",
            "operator_gate_required": True,
            "auto_execute": False,
        }
    )
    del payload["proposal"]
    return payload


def _event(payload: dict) -> dict:
    return {
        "ts_utc": "2026-05-18T08:00:00Z",
        "agent": "codex",
        "type": "message",
        "task_id": "idle-session-test",
        "status": payload["event_type"],
        "severity": "",
        "to": "claude",
        "message": "Idle protocol test event with substantive content.",
        "paths": [],
        "write_scope": [],
        "run_id": "",
        "pid": 1234,
        "cwd": "C:\\Python\\project2-master",
        "payload": payload,
    }


def _write_events(path: Path, events: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(event, sort_keys=True) + "\n" for event in events),
        encoding="utf-8",
    )


def _run(tmp_path: Path, *, events: list[dict] | None = None) -> dict:
    events_path = tmp_path / "events.jsonl"
    _write_events(events_path, events if events is not None else [_event(_proposal())])
    return run_idle_protocol_session(
        events_path=events_path,
    )


def test_dry_run_reports_next_step_without_bridge_write(tmp_path: Path) -> None:
    report = _run(tmp_path)

    assert report["decision"] == "next_required"
    assert report["read_only"] is True
    assert report["summary"]["next_required_event"]["event_type"] == (
        "idle_counter_proposal"
    )
    scaffold = report["manual_payload_scaffold"]
    assert scaffold["not_a_payload"] is True
    assert scaffold["manual_content_required"] is True
    assert scaffold["event_type"] == "idle_counter_proposal"
    assert scaffold["round_number"] == 2
    assert scaffold["reference_hints"]["responds_to"] == "idle-prop-20260518-001"
    assert "alternative_proposal" in scaffold["required_fields"]
    assert "reasoning_points" in scaffold["required_fields"]
    assert "proposal" in scaffold["forbidden_fields"]
    assert "idle_protocol_activate.py" in scaffold["dry_run_command"]
    assert not (tmp_path / "bridge" / "shared" / "events.jsonl").exists()


def test_terminal_session_does_not_emit_request(tmp_path: Path) -> None:
    events = [
        _event(_proposal()),
        _event(_consensus("idle-prop-20260518-005a")),
        _event(_consensus("idle-prop-20260518-005b")),
    ]

    report = _run(tmp_path, events=events)

    assert report["decision"] == "operator_review_required"
    assert report["read_only"] is True
    assert "manual_payload_scaffold" not in report
    assert not (tmp_path / "bridge" / "shared" / "events.jsonl").exists()


def test_invalid_json_fails_without_bridge_write(tmp_path: Path) -> None:
    events_path = tmp_path / "events.jsonl"
    events_path.write_text("{not-json}\n", encoding="utf-8")

    with pytest.raises(SessionError) as excinfo:
        run_idle_protocol_session(
            events_path=events_path,
        )

    assert excinfo.value.report["decision"] == "unknown"
    assert not (tmp_path / "bridge" / "shared" / "events.jsonl").exists()


def test_cli_runs_by_file_path_from_repo_root(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    events_path = tmp_path / "events.jsonl"
    _write_events(events_path, [_event(_proposal())])

    completed = subprocess.run(
        [
            sys.executable,
            str(root / "tools" / "idle_protocol_session.py"),
            "--events",
            str(events_path),
            "--dry-run",
            "--json",
        ],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report["decision"] == "next_required"
    assert report["read_only"] is True
    assert report["summary"]["next_required_event"]["round_number"] == 2
    assert report["manual_payload_scaffold"]["not_a_payload"] is True
    assert not (tmp_path / "bridge" / "shared" / "events.jsonl").exists()


def test_cli_rejects_emit_request_flag(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    events_path = tmp_path / "events.jsonl"
    _write_events(events_path, [_event(_proposal())])

    completed = subprocess.run(
        [
            sys.executable,
            str(root / "tools" / "idle_protocol_session.py"),
            "--events",
            str(events_path),
            "--emit-request",
        ],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert not (tmp_path / "bridge" / "shared" / "events.jsonl").exists()
