# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools.idle_consensus_artifact import ArtifactError, write_idle_consensus_artifact


NOW = datetime(2026, 5, 18, 9, 0, tzinfo=timezone.utc)
PROHIBITED_HINTS = (
    "create pr",
    "open pr",
    "git checkout",
    "git switch",
    "new branch",
    "scaffold code",
    "work_queue",
)


def _base(proposal_id: str, event_type: str, round_number: int) -> dict:
    return {
        "protocol_version": "idle-protocol.v1",
        "event_type": event_type,
        "proposal_id": proposal_id,
        "round_number": round_number,
        "proposes_substrate_change": True,
        "problem_statement": "Idle consensus needs an operator review artifact before work begins.",
        "tradeoff_axis": "Evidence handoff versus automatic implementation conversion.",
        "simulation_evidence": {
            "kind": "scenario_simulation",
            "summary": "A completed transcript can be written as review evidence only.",
        },
        "charter_alignment": {
            "compatible": True,
            "reasoning": "The artifact preserves operator approval and has no execution path.",
        },
    }


def _proposal() -> dict:
    payload = _base("idle-artifact-001", "idle_proposal", 1)
    payload["proposal"] = "Write an operator review artifact for completed idle consensus."
    return payload


def _counter(proposal_id: str = "idle-artifact-002", round_number: int = 2) -> dict:
    payload = _base(proposal_id, "idle_counter_proposal", round_number)
    payload["responds_to"] = (
        "idle-artifact-001" if round_number == 2 else f"idle-artifact-{round_number - 1:03d}"
    )
    payload["alternative_proposal"] = (
        "Use a read only artifact and require the operator to decide separately."
    )
    payload["reasoning_points"] = [
        "If artifact creation makes a task, the operator gate has been bypassed.",
        "When the artifact is evidence only, review remains independent.",
        "If privacy markers are present, the artifact must not be written.",
    ]
    return payload


def _adversarial() -> dict:
    payload = _base("idle-artifact-003", "idle_adversarial_review", 3)
    payload["responds_to"] = "idle-artifact-002"
    payload["proposes_substrate_change"] = False
    payload["counterexamples"] = [
        "If the artifact includes branch commands, it pressures implementation.",
        "When transcript data contains private markers, writing the artifact leaks data.",
    ]
    return payload


def _consensus(proposal_id: str) -> dict:
    payload = _base(proposal_id, "idle_consensus_reached", 5)
    payload["proposes_substrate_change"] = False
    payload["consensus_target_proposal_id"] = "idle-artifact-002"
    payload["operator_gate_required"] = True
    payload["auto_execute"] = False
    return payload


def _charter_violation() -> dict:
    payload = _base("idle-artifact-004", "idle_charter_violation", 4)
    payload["proposes_substrate_change"] = False
    payload["violating_proposal_id"] = "idle-artifact-002"
    payload["violation_reason"] = "The reviewed idea would turn consensus into automatic work."
    payload["terminate_protocol"] = True
    payload["operator_escalation_required"] = True
    payload["charter_alignment"] = {
        "compatible": False,
        "reasoning": "Automatic work conversion bypasses operator approval.",
    }
    return payload


def _soft_events() -> list[dict]:
    return [
        _proposal(),
        _counter(),
        _adversarial(),
        _consensus("idle-artifact-005a"),
        _consensus("idle-artifact-005b"),
    ]


def _hard_events() -> list[dict]:
    events = [_proposal(), _counter(), _adversarial()]
    for round_number in range(4, 11):
        events.append(_counter(f"idle-artifact-{round_number:03d}", round_number))
    return events


def _bridge_event(payload: dict) -> dict:
    return {
        "ts_utc": "2026-05-18T09:00:00Z",
        "agent": "codex",
        "type": "message",
        "task_id": "idle-artifact-test",
        "status": payload["event_type"],
        "severity": "",
        "to": "claude",
        "message": "Idle artifact test event with substantive content.",
        "paths": [],
        "write_scope": [],
        "run_id": "",
        "pid": 1234,
        "cwd": "C:\\Python\\project2-master",
        "payload": payload,
    }


def _write_events(path: Path, payloads: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(_bridge_event(payload), sort_keys=True) + "\n" for payload in payloads),
        encoding="utf-8",
    )


def _write_artifact(tmp_path: Path, payloads: list[dict]) -> dict:
    events_path = tmp_path / "events.jsonl"
    _write_events(events_path, payloads)
    return write_idle_consensus_artifact(
        events_path=events_path,
        out_dir=tmp_path / "artifacts",
        now_utc=NOW,
    )


def test_soft_consensus_writes_operator_review_artifact(tmp_path: Path) -> None:
    report = _write_artifact(tmp_path, _soft_events())

    assert report["decision"] == "operator_review_required"
    assert report["convergence_status"] == "soft_convergence"
    assert report["auto_execute"] is False
    artifact = json.loads(Path(report["json_path"]).read_text(encoding="utf-8"))
    markdown = Path(report["markdown_path"]).read_text(encoding="utf-8")
    assert artifact["operator_gate_required"] is True
    assert artifact["auto_execute"] is False
    assert len(artifact["transcript"]) == 5
    assert "Operator review required" in markdown
    assert "no task creation" in markdown
    assert all(hint not in markdown.lower() for hint in PROHIBITED_HINTS)


def test_hard_consensus_writes_finalist_artifact(tmp_path: Path) -> None:
    report = _write_artifact(tmp_path, _hard_events())

    artifact = json.loads(Path(report["json_path"]).read_text(encoding="utf-8"))
    assert report["convergence_status"] == "hard_convergence"
    assert artifact["convergence"]["finalist_proposal_ids"] == [
        "idle-artifact-010",
        "idle-artifact-009",
        "idle-artifact-008",
    ]
    assert artifact["prohibited_actions"] == [
        "no_task_creation",
        "no_branch_creation",
        "no_pull_request_creation",
        "no_external_effect",
    ]


def test_charter_violation_refuses_artifact(tmp_path: Path) -> None:
    events_path = tmp_path / "events.jsonl"
    _write_events(events_path, [_proposal(), _counter(), _charter_violation()])

    with pytest.raises(ArtifactError) as excinfo:
        write_idle_consensus_artifact(
            events_path=events_path,
            out_dir=tmp_path / "artifacts",
            now_utc=NOW,
        )

    assert excinfo.value.report["decision"] == "charter_violation"
    assert not (tmp_path / "artifacts").exists()


def test_no_consensus_refuses_without_output(tmp_path: Path) -> None:
    events_path = tmp_path / "events.jsonl"
    _write_events(events_path, [_proposal(), _counter()])

    with pytest.raises(ArtifactError) as excinfo:
        write_idle_consensus_artifact(
            events_path=events_path,
            out_dir=tmp_path / "artifacts",
            now_utc=NOW,
        )

    assert excinfo.value.report["decision"] == "no_consensus"
    assert not (tmp_path / "artifacts").exists()


def test_privacy_marker_refuses_without_output(tmp_path: Path) -> None:
    payload = _proposal()
    payload["simulation_evidence"]["summary"] = "PRIVATE_MARKER must not be written."
    events_path = tmp_path / "events.jsonl"
    _write_events(events_path, [payload])

    with pytest.raises(ArtifactError) as excinfo:
        write_idle_consensus_artifact(
            events_path=events_path,
            out_dir=tmp_path / "artifacts",
            now_utc=NOW,
        )

    assert excinfo.value.report["decision"] == "privacy_marker_detected"
    assert not (tmp_path / "artifacts").exists()


def test_existing_artifact_refuses_overwrite(tmp_path: Path) -> None:
    report = _write_artifact(tmp_path, _soft_events())

    with pytest.raises(ArtifactError) as excinfo:
        _write_artifact(tmp_path, _soft_events())

    assert excinfo.value.report["decision"] == "refuse_overwrite"
    assert Path(report["json_path"]).exists()


def test_cli_runs_by_file_path_from_repo_root(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    events_path = tmp_path / "events.jsonl"
    _write_events(events_path, _soft_events())

    completed = subprocess.run(
        [
            sys.executable,
            str(root / "tools" / "idle_consensus_artifact.py"),
            "--events",
            str(events_path),
            "--out-dir",
            str(tmp_path / "artifacts"),
            "--now",
            "2026-05-18T09:00:00Z",
            "--json",
        ],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report["decision"] == "operator_review_required"
    assert Path(report["json_path"]).exists()
