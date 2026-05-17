# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools.idle_check import _is_substantive_agent_message
from tools.idle_protocol_activate import ActivationError, activate_idle_protocol
from waggledance.core.bridge_event_schema import validate_event


NOW = datetime(2026, 5, 17, 12, 0, tzinfo=timezone.utc)


def _event(
    *,
    ts_utc: str,
    agent: str = "codex",
    type: str = "message",
    task_id: str = "idle-activation-smoke",
    status: str = "note",
    to: str = "claude",
    message: str = "Substantive bridge content that should count as agent activity.",
    payload: dict | None = None,
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
        "payload": payload or {},
    }


def _proposal(proposal_id: str = "idle-prop-20260517-001") -> dict:
    return {
        "protocol_version": "idle-protocol.v1",
        "event_type": "idle_proposal",
        "proposal_id": proposal_id,
        "round_number": 1,
        "proposes_substrate_change": True,
        "problem_statement": "Strategic bridge deliberation stalls when no PR vehicle exists.",
        "proposal": (
            "Emit a manual idle proposal only after the detector reports an idle bridge "
            "and keep all execution behind the operator gate."
        ),
        "tradeoff_axis": "Operator-gated activation versus slower unattended strategic deliberation.",
        "simulation_evidence": {
            "kind": "scenario_simulation",
            "summary": (
                "A quiet ninety minute bridge window accepts round one while a recent "
                "message rejects it as active."
            ),
        },
        "charter_alignment": {
            "compatible": True,
            "reasoning": (
                "Manual emission has no auto-execute path and consensus remains operator gated."
            ),
        },
    }


def _counter() -> dict:
    event = _proposal("idle-prop-20260517-002")
    event.update(
        {
            "event_type": "idle_counter_proposal",
            "round_number": 2,
            "responds_to": "idle-prop-20260517-001",
            "alternative_proposal": (
                "Continue the protocol after the first idle proposal without requiring "
                "the bridge to remain idle during the active deliberation."
            ),
            "reasoning_points": [
                "If round two required idle, the first idle proposal would block the required reply.",
                "If prior idle payloads are absent, a round two event must fail before emission.",
                "When a bridge event already carries idle-protocol.v1, continuation remains traceable.",
            ],
        }
    )
    del event["proposal"]
    return event


def _consensus(proposal_id: str) -> dict:
    event = _proposal(proposal_id)
    event.update(
        {
            "event_type": "idle_consensus_reached",
            "round_number": 5,
            "proposes_substrate_change": False,
            "consensus_target_proposal_id": "idle-prop-20260517-002",
            "operator_gate_required": True,
            "auto_execute": False,
        }
    )
    del event["proposal"]
    return event


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


def _write_events(path: Path, events: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(event, sort_keys=True) + "\n" for event in events),
        encoding="utf-8",
    )


def _base_events() -> list[dict[str, object]]:
    return [
        _event(
            ts_utc="2026-05-17T10:20:00Z",
            type="done",
            status="merged_postmerge_green",
            message="Merged work was verified more than one idle window ago.",
        ),
        _event(
            ts_utc="2026-05-17T10:30:00Z",
            agent="claude",
            status="scout_answered",
            message="Substantive scout response older than the idle window.",
        ),
    ]


def _activate(
    tmp_path: Path,
    payload: dict,
    *,
    events: list[dict[str, object]] | None = None,
    emit: bool = False,
) -> dict:
    payload_path = tmp_path / "payload.json"
    events_path = tmp_path / "events.jsonl"
    claims_dir = tmp_path / "claims"
    bridge_root = tmp_path / "bridge"
    claims_dir.mkdir()
    _write_json(payload_path, payload)
    _write_events(events_path, events if events is not None else _base_events())
    return activate_idle_protocol(
        payload_path=payload_path,
        events_path=events_path,
        claims_dir=claims_dir,
        bridge_root=bridge_root,
        from_agent="codex",
        to_agent=None,
        task_id=None,
        idle_minutes=60,
        pending_ci_count=0,
        open_request_max_age_hours=12.0,
        now_utc=NOW,
        emit=emit,
    )


def test_round_one_dry_run_requires_idle_and_does_not_emit(tmp_path: Path) -> None:
    report = _activate(tmp_path, _proposal())

    assert report["decision"] == "ready"
    assert report["emitted"] is False
    assert report["proposed_bridge_event"]["status"] == "idle_proposal"
    assert report["to"] == "claude"
    assert not (tmp_path / "bridge" / "shared" / "events.jsonl").exists()


def test_round_one_refuses_active_bridge_before_emitting(tmp_path: Path) -> None:
    active_events = _base_events() + [
        _event(
            ts_utc="2026-05-17T11:55:00Z",
            message="Recent substantive work means the bridge is not idle enough.",
        )
    ]

    with pytest.raises(ActivationError) as excinfo:
        _activate(tmp_path, _proposal(), events=active_events, emit=True)

    assert excinfo.value.report["decision"] == "active"
    assert "recent_agent_message" in excinfo.value.report["blockers"]
    assert not (tmp_path / "bridge" / "shared" / "events.jsonl").exists()


def test_emit_appends_bridge_event_outbox_and_last_file(tmp_path: Path) -> None:
    report = _activate(tmp_path, _proposal(), emit=True)

    events_path = tmp_path / "bridge" / "shared" / "events.jsonl"
    outbox_path = tmp_path / "bridge" / "outbox" / "codex" / "2026-05-17.jsonl"
    last_path = tmp_path / "bridge" / "shared" / "last_codex.json"
    emitted = json.loads(events_path.read_text(encoding="utf-8").strip())

    assert report["emitted"] is True
    assert outbox_path.exists()
    assert last_path.exists()
    assert emitted["status"] == "idle_proposal"
    assert emitted["payload"]["protocol_version"] == "idle-protocol.v1"
    assert "auto_execute" not in emitted["payload"]
    validate_event(emitted)
    assert _is_substantive_agent_message(emitted) is True


def test_privacy_canary_refuses_before_bridge_event_output(tmp_path: Path) -> None:
    payload = _proposal()
    payload["simulation_evidence"]["summary"] = "This payload contains _DO_NOT_LEAK and must fail."

    with pytest.raises(ActivationError) as excinfo:
        _activate(tmp_path, payload, emit=True)

    assert excinfo.value.report["decision"] == "privacy_canary_detected"
    assert "proposed_bridge_event" not in excinfo.value.report
    assert not (tmp_path / "bridge" / "shared" / "events.jsonl").exists()


def test_round_two_continues_after_prior_idle_event_even_when_bridge_is_active(
    tmp_path: Path,
) -> None:
    prior_payload = _proposal()
    events = _base_events() + [
        _event(
            ts_utc="2026-05-17T11:30:00Z",
            status="idle_proposal",
            payload=prior_payload,
        )
    ]

    report = _activate(tmp_path, _counter(), events=events, emit=True)

    assert report["decision"] == "ready"
    assert report["event_type"] == "idle_counter_proposal"
    assert report["emitted"] is True


def test_round_two_requires_prior_idle_event(tmp_path: Path) -> None:
    with pytest.raises(ActivationError) as excinfo:
        _activate(tmp_path, _counter(), emit=True)

    assert excinfo.value.report["decision"] == "missing_prior_idle_event"
    assert not (tmp_path / "bridge" / "shared" / "events.jsonl").exists()


def test_consensus_report_is_operator_gated_and_not_auto_execute(tmp_path: Path) -> None:
    events = _base_events() + [
        _event(
            ts_utc="2026-05-17T11:00:00Z",
            status="idle_proposal",
            payload=_proposal(),
        ),
        _event(
            ts_utc="2026-05-17T11:10:00Z",
            status="idle_consensus_reached",
            payload=_consensus("idle-prop-20260517-005a"),
        ),
    ]

    report = _activate(
        tmp_path,
        _consensus("idle-prop-20260517-005b"),
        events=events,
        emit=False,
    )

    assert report["convergence"]["status"] == "soft_convergence"
    assert report["convergence"]["operator_gate_required"] is True
    assert report["convergence"]["auto_execute"] is False


def test_cli_runs_by_file_path_from_repo_root(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    payload_path = tmp_path / "payload.json"
    events_path = tmp_path / "events.jsonl"
    claims_dir = tmp_path / "claims"
    claims_dir.mkdir()
    _write_json(payload_path, _proposal())
    _write_events(events_path, _base_events())

    completed = subprocess.run(
        [
            sys.executable,
            str(root / "tools" / "idle_protocol_activate.py"),
            "--payload",
            str(payload_path),
            "--events",
            str(events_path),
            "--claims-dir",
            str(claims_dir),
            "--bridge-root",
            str(tmp_path / "bridge"),
            "--now",
            "2026-05-17T12:00:00Z",
            "--dry-run",
            "--json",
        ],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    report = json.loads(completed.stdout)
    assert report["decision"] == "ready"
    assert report["emitted"] is False


def test_cli_rejects_dry_run_and_apply_together(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    payload_path = tmp_path / "payload.json"
    events_path = tmp_path / "events.jsonl"
    claims_dir = tmp_path / "claims"
    bridge_root = tmp_path / "bridge"
    claims_dir.mkdir()
    _write_json(payload_path, _proposal())
    _write_events(events_path, _base_events())

    completed = subprocess.run(
        [
            sys.executable,
            str(root / "tools" / "idle_protocol_activate.py"),
            "--payload",
            str(payload_path),
            "--events",
            str(events_path),
            "--claims-dir",
            str(claims_dir),
            "--bridge-root",
            str(bridge_root),
            "--now",
            "2026-05-17T12:00:00Z",
            "--dry-run",
            "--apply",
        ],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert not (bridge_root / "shared" / "events.jsonl").exists()
