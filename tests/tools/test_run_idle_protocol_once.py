# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools.run_idle_protocol_once import (
    IdleRunnerError,
    build_round_one_payload,
    main,
    run_idle_protocol_once,
)
from waggledance.core.bridge_event_schema import validate_event
from waggledance.core.idle_protocol import validate_idle_proposal


NOW = datetime(2026, 5, 17, 12, 0, tzinfo=timezone.utc)


def _event(
    *,
    ts_utc: str,
    agent: str = "codex",
    type: str = "message",
    task_id: str = "idle-runner-smoke",
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


def _base_idle_events() -> list[dict[str, object]]:
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


def _write_events(path: Path, events: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(event, sort_keys=True) + "\n" for event in events),
        encoding="utf-8",
    )


def _run(
    tmp_path: Path,
    *,
    events: list[dict[str, object]] | None = None,
    emit: bool = False,
    proposal_id: str | None = "idle-prop-20260517-runner",
    from_agent: str = "codex",
) -> dict:
    events_path = tmp_path / "events.jsonl"
    claims_dir = tmp_path / "claims"
    bridge_root = tmp_path / "bridge"
    scratch_dir = tmp_path / "scratch"
    claims_dir.mkdir()
    _write_events(events_path, events if events is not None else _base_idle_events())
    return run_idle_protocol_once(
        events_path=events_path,
        claims_dir=claims_dir,
        bridge_root=bridge_root,
        from_agent=from_agent,
        to_agent=None,
        task_id=None,
        proposal_id=proposal_id,
        idle_minutes=60,
        pending_ci_count=0,
        open_request_max_age_hours=12.0,
        operator_last_activity_utc=None,
        now_utc=NOW,
        emit=emit,
        receipt_out_dir=None,
        scratch_dir=scratch_dir,
    )


def test_default_round_one_payload_passes_quality_validator() -> None:
    payload = build_round_one_payload(proposal_id="idle-prop-20260517-runner")

    ok, errors = validate_idle_proposal(payload)

    assert ok is True
    assert errors == []
    assert payload["event_type"] == "idle_proposal"
    assert "auto_execute" not in payload
    assert "operator_gate_required" not in payload
    serialized = json.dumps(payload, sort_keys=True)
    assert "DREAM_MODE_AGENDA.md" in serialized
    assert "COMPETITIVE_EVIDENCE_MATRIX_2026.md" in serialized
    assert "rival_local_checks" in serialized
    assert "consensus_grade" in serialized


def test_active_bridge_is_noop_even_when_emit_requested(tmp_path: Path) -> None:
    active_events = _base_idle_events() + [
        _event(
            ts_utc="2026-05-17T11:55:00Z",
            message="Recent substantive work means idle runner must stay silent.",
        )
    ]

    report = _run(tmp_path, events=active_events, emit=True)

    assert report["decision"] == "active"
    assert report["emitted"] is False
    assert "recent_agent_message" in report["blockers"]
    assert not (tmp_path / "bridge" / "shared" / "events.jsonl").exists()
    assert not (tmp_path / "scratch").exists()


def test_idle_dry_run_builds_activation_without_bridge_write(tmp_path: Path) -> None:
    report = _run(tmp_path, emit=False)

    assert report["decision"] == "idle_ready"
    assert report["emitted"] is False
    assert report["idle_report"]["idle"] is True
    activation = report["activation"]
    assert activation["decision"] == "ready"
    assert activation["event_type"] == "idle_proposal"
    assert activation["proposed_bridge_event"]["status"] == "idle_proposal"
    assert activation["proposed_bridge_event"]["payload"]["proposal_id"] == (
        "idle-prop-20260517-runner"
    )
    assert not (tmp_path / "bridge" / "shared" / "events.jsonl").exists()
    assert list((tmp_path / "scratch").glob("*.json")) == []


def test_idle_emit_appends_one_valid_bridge_event(tmp_path: Path) -> None:
    report = _run(tmp_path, emit=True)

    events_path = tmp_path / "bridge" / "shared" / "events.jsonl"
    outbox_path = tmp_path / "bridge" / "outbox" / "codex" / "2026-05-17.jsonl"
    emitted = json.loads(events_path.read_text(encoding="utf-8").strip())

    assert report["decision"] == "idle_ready"
    assert report["emitted"] is True
    assert outbox_path.exists()
    assert emitted["status"] == "idle_proposal"
    assert emitted["to"] == "claude"
    assert emitted["payload"]["protocol_version"] == "idle-protocol.v1"
    assert emitted["payload"]["proposal_id"] == "idle-prop-20260517-runner"
    assert "auto_execute" not in emitted["payload"]
    validate_event(emitted)
    assert list((tmp_path / "scratch").glob("*.json")) == []


def test_duplicate_proposal_id_is_refused_before_bridge_write(tmp_path: Path) -> None:
    proposal = build_round_one_payload(proposal_id="idle-prop-20260517-runner")
    events = _base_idle_events() + [
        _event(
            ts_utc="2026-05-17T10:45:00Z",
            status="idle_proposal",
            payload=proposal,
        )
    ]

    with pytest.raises(IdleRunnerError) as excinfo:
        _run(tmp_path, events=events, emit=True)

    assert excinfo.value.report["decision"] == "invalid_sequence"
    assert excinfo.value.report["emitted"] is False
    assert any("already present" in error for error in excinfo.value.report["errors"])
    assert not (tmp_path / "bridge" / "shared" / "events.jsonl").exists()
    assert list((tmp_path / "scratch").glob("*.json")) == []


def test_daily_rate_limit_is_preserved_before_bridge_write(tmp_path: Path) -> None:
    events = _base_idle_events() + [
        _event(
            ts_utc=f"2026-05-17T09:{index:02d}:00Z",
            status="idle_proposal",
            payload=build_round_one_payload(
                proposal_id=f"idle-prop-20260517-rate-{index:03d}",
            ),
        )
        for index in range(5)
    ]

    with pytest.raises(IdleRunnerError) as excinfo:
        _run(tmp_path, events=events, emit=True, proposal_id="idle-prop-20260517-sixth")

    assert excinfo.value.report["decision"] == "rate_limited"
    assert excinfo.value.report["exit_code"] == 5
    assert excinfo.value.report["emitted"] is False
    assert not (tmp_path / "bridge" / "shared" / "events.jsonl").exists()
    assert list((tmp_path / "scratch").glob("*.json")) == []


def test_from_agent_claude_targets_codex(tmp_path: Path) -> None:
    report = _run(
        tmp_path,
        emit=True,
        proposal_id="idle-prop-20260517-claude",
        from_agent="claude",
    )

    events_path = tmp_path / "bridge" / "shared" / "events.jsonl"
    emitted = json.loads(events_path.read_text(encoding="utf-8").strip())

    assert report["emitted"] is True
    assert emitted["agent"] == "claude"
    assert emitted["to"] == "codex"
    assert emitted["payload"]["proposal_id"] == "idle-prop-20260517-claude"
    validate_event(emitted)


def test_missing_events_file_returns_unknown_without_bridge_write(tmp_path: Path) -> None:
    with pytest.raises(IdleRunnerError) as excinfo:
        run_idle_protocol_once(
            events_path=tmp_path / "missing.jsonl",
            claims_dir=tmp_path / "claims",
            bridge_root=tmp_path / "bridge",
            from_agent="codex",
            to_agent=None,
            task_id=None,
            proposal_id=None,
            idle_minutes=60,
            pending_ci_count=0,
            open_request_max_age_hours=12.0,
            operator_last_activity_utc=None,
            now_utc=NOW,
            emit=True,
            receipt_out_dir=None,
            scratch_dir=tmp_path / "scratch",
        )

    assert excinfo.value.report["decision"] == "unknown"
    assert excinfo.value.report["emitted"] is False
    assert not (tmp_path / "bridge" / "shared" / "events.jsonl").exists()
    assert not (tmp_path / "scratch").exists()


def test_path_like_proposal_id_is_refused_before_scratch_write(tmp_path: Path) -> None:
    outside = tmp_path / "escape.json"
    outside.write_text("keep", encoding="utf-8")

    with pytest.raises(IdleRunnerError) as excinfo:
        _run(tmp_path, emit=False, proposal_id="../escape")

    assert excinfo.value.report["decision"] == "invalid_proposal_id"
    assert excinfo.value.report["emitted"] is False
    assert outside.read_text(encoding="utf-8") == "keep"
    assert not (tmp_path / "bridge" / "shared" / "events.jsonl").exists()
    assert not (tmp_path / "scratch").exists()


def test_cli_runs_by_file_path_from_repo_root(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    events_path = tmp_path / "events.jsonl"
    claims_dir = tmp_path / "claims"
    claims_dir.mkdir()
    _write_events(events_path, _base_idle_events())

    completed = subprocess.run(
        [
            sys.executable,
            str(root / "tools" / "run_idle_protocol_once.py"),
            "--events",
            str(events_path),
            "--claims-dir",
            str(claims_dir),
            "--bridge-root",
            str(tmp_path / "bridge"),
            "--scratch-dir",
            str(tmp_path / "scratch"),
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

    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report["decision"] == "idle_ready"
    assert report["emitted"] is False
    assert report["activation"]["proposed_bridge_event"]["payload"]["proposal_id"] == (
        "idle-prop-20260517t120000000000z-codex"
    )
    assert not (tmp_path / "bridge" / "shared" / "events.jsonl").exists()


def test_cli_defaults_paths_to_runtime_bridge_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bridge_root = tmp_path / "runtime-bridge"
    events_path = bridge_root / "shared" / "events.jsonl"
    claims_dir = bridge_root / "work_queue" / "claims"
    events_path.parent.mkdir(parents=True)
    claims_dir.mkdir(parents=True)
    _write_events(events_path, _base_idle_events())
    monkeypatch.setenv("AGENT_BRIDGE_RUNTIME_ROOT", str(bridge_root))

    assert main([
        "--scratch-dir",
        str(tmp_path / "scratch"),
        "--now",
        "2026-05-17T12:00:00Z",
        "--dry-run",
        "--json",
    ]) == 0

    report = json.loads(capsys.readouterr().out)
    assert report["decision"] == "idle_ready"
    assert report["emitted"] is False
    assert report["idle_report"]["idle"] is True
    assert not events_path.read_text(encoding="utf-8").endswith("idle_proposal\n")


def test_cli_bridge_root_argument_defaults_paths(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bridge_root = tmp_path / "explicit-bridge"
    events_path = bridge_root / "shared" / "events.jsonl"
    claims_dir = bridge_root / "work_queue" / "claims"
    events_path.parent.mkdir(parents=True)
    claims_dir.mkdir(parents=True)
    _write_events(events_path, _base_idle_events())

    assert main([
        "--bridge-root",
        str(bridge_root),
        "--scratch-dir",
        str(tmp_path / "scratch"),
        "--now",
        "2026-05-17T12:00:00Z",
        "--dry-run",
        "--json",
    ]) == 0

    report = json.loads(capsys.readouterr().out)
    assert report["decision"] == "idle_ready"
    assert report["emitted"] is False
    assert report["idle_report"]["idle"] is True


def test_cli_explicit_paths_override_runtime_bridge_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime_root = tmp_path / "runtime-bridge"
    runtime_events = runtime_root / "shared" / "events.jsonl"
    runtime_claims = runtime_root / "work_queue" / "claims"
    runtime_events.parent.mkdir(parents=True)
    runtime_claims.mkdir(parents=True)
    _write_events(
        runtime_events,
        _base_idle_events()
        + [
            _event(
                ts_utc="2026-05-17T11:55:00Z",
                message="Runtime-root event would block if explicit events lost.",
            )
        ],
    )
    explicit_events = tmp_path / "explicit-events.jsonl"
    explicit_claims = tmp_path / "explicit-claims"
    explicit_claims.mkdir()
    _write_events(explicit_events, _base_idle_events())
    monkeypatch.setenv("AGENT_BRIDGE_RUNTIME_ROOT", str(runtime_root))

    assert main([
        "--events",
        str(explicit_events),
        "--claims-dir",
        str(explicit_claims),
        "--scratch-dir",
        str(tmp_path / "scratch"),
        "--now",
        "2026-05-17T12:00:00Z",
        "--dry-run",
        "--json",
    ]) == 0

    report = json.loads(capsys.readouterr().out)
    assert report["decision"] == "idle_ready"
    assert report["emitted"] is False
    assert "recent_agent_message" not in report["idle_report"]["blockers"]
