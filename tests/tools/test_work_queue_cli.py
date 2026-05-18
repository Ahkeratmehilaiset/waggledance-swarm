from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from tools.work_queue import main
from waggledance.core.work_queue import claim_task


def _run(capsys, *args: str) -> tuple[int, dict]:
    exit_code = main(["--json", *args])
    captured = capsys.readouterr()
    return exit_code, json.loads(captured.out)


def test_claim_and_list_round_trip(tmp_path: Path, capsys) -> None:
    bridge = tmp_path / ".agent-bridge"
    exit_code, report = _run(
        capsys,
        "--bridge-root",
        str(bridge),
        "claim",
        "--agent",
        "codex-1",
        "--task-id",
        "task-001",
        "--summary",
        "inspect files",
    )
    assert exit_code == 0
    assert report["decision"] == "claimed"
    assert report["claim"]["agent"] == "codex-1"

    exit_code, report = _run(
        capsys,
        "--bridge-root",
        str(bridge),
        "list",
    )
    assert exit_code == 0
    assert report["decision"] == "listed"
    assert len(report["claims"]) == 1
    assert report["claims"][0]["task_id"] == "task-001"


def test_release_archives_claim(tmp_path: Path, capsys) -> None:
    bridge = tmp_path / ".agent-bridge"
    _run(
        capsys,
        "--bridge-root",
        str(bridge),
        "claim",
        "--agent",
        "codex-1",
        "--task-id",
        "task-001",
        "--summary",
        "inspect files",
    )
    exit_code, report = _run(
        capsys,
        "--bridge-root",
        str(bridge),
        "release",
        "--agent",
        "codex-1",
        "--task-id",
        "task-001",
        "--status",
        "done",
        "--message",
        "green",
    )
    assert exit_code == 0
    assert report["decision"] == "released"
    assert report["release"]["release_message"] == "green"

    exit_code, report = _run(capsys, "--bridge-root", str(bridge), "list")
    assert exit_code == 0
    assert report["claims"] == []


def test_release_wrong_agent_returns_refused_exit_code(tmp_path: Path, capsys) -> None:
    bridge = tmp_path / ".agent-bridge"
    _run(
        capsys,
        "--bridge-root",
        str(bridge),
        "claim",
        "--agent",
        "codex-1",
        "--task-id",
        "task-001",
        "--summary",
        "inspect files",
    )
    exit_code, report = _run(
        capsys,
        "--bridge-root",
        str(bridge),
        "release",
        "--agent",
        "claude-1",
        "--task-id",
        "task-001",
    )
    assert exit_code == 1
    assert report["ok"] is False
    assert "held by codex-1" in report["errors"][0]


def test_write_claim_requires_scope_and_returns_error(tmp_path: Path, capsys) -> None:
    bridge = tmp_path / ".agent-bridge"
    exit_code, report = _run(
        capsys,
        "--bridge-root",
        str(bridge),
        "claim",
        "--agent",
        "codex-1",
        "--task-id",
        "task-001",
        "--summary",
        "edit files",
        "--mode",
        "write",
    )
    assert exit_code == 2
    assert report["ok"] is False
    assert report["decision"] == "work_queue_error"
    assert "write claims require" in report["errors"][0]


def test_check_overlap_reports_conflicting_write_claim(tmp_path: Path, capsys) -> None:
    bridge = tmp_path / ".agent-bridge"
    _run(
        capsys,
        "--bridge-root",
        str(bridge),
        "claim",
        "--agent",
        "codex-1",
        "--task-id",
        "task-001",
        "--summary",
        "edit tools tree",
        "--mode",
        "write",
        "--write-scope",
        "tools",
    )
    exit_code, report = _run(
        capsys,
        "--bridge-root",
        str(bridge),
        "check-overlap",
        "--write-scope",
        "tools/foo.py",
    )
    assert exit_code == 0
    assert report["decision"] == "scope_overlap"
    assert len(report["claims"]) == 1
    assert report["claims"][0]["task_id"] == "task-001"


def test_heartbeat_refreshes_claim(tmp_path: Path, capsys) -> None:
    bridge = tmp_path / ".agent-bridge"
    _run(
        capsys,
        "--bridge-root",
        str(bridge),
        "claim",
        "--agent",
        "codex-1",
        "--task-id",
        "task-001",
        "--summary",
        "inspect files",
    )
    _, before = _run(capsys, "--bridge-root", str(bridge), "list")
    exit_code, report = _run(
        capsys,
        "--bridge-root",
        str(bridge),
        "heartbeat",
        "--agent",
        "codex-1",
        "--task-id",
        "task-001",
    )
    assert exit_code == 0
    assert report["decision"] == "heartbeat"
    assert report["claim"]["claimed_at_utc"] == before["claims"][0]["claimed_at_utc"]
    assert report["claim"]["claimed_at_utc"] <= report["claim"]["last_heartbeat_utc"]


def test_stale_command_outputs_json(tmp_path: Path, capsys) -> None:
    bridge = tmp_path / ".agent-bridge"
    exit_code, report = _run(
        capsys,
        "--bridge-root",
        str(bridge),
        "stale",
        "--max-age-seconds",
        "1",
    )
    assert exit_code == 0
    assert report == {"claims": [], "decision": "stale_claims", "ok": True}


def test_stale_command_returns_exit_three_for_stale_claims(
    tmp_path: Path,
    capsys,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    claim_task(
        agent="codex-1",
        task_id="old-task",
        summary="old",
        bridge_root=bridge,
        now_utc=datetime(2026, 5, 18, 0, 0, tzinfo=timezone.utc),
    )
    exit_code, report = _run(
        capsys,
        "--bridge-root",
        str(bridge),
        "stale",
        "--max-age-seconds",
        "1",
    )
    assert exit_code == 3
    assert report["claims"][0]["task_id"] == "old-task"
