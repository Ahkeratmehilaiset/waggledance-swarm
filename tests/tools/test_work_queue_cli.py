from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
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


def test_claim_cli_uses_environment_lease_default(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    monkeypatch.setenv("AGENT_BRIDGE_STALE_LEASE_SECONDS", "1000")

    exit_code, report = _run(
        capsys,
        "--bridge-root",
        str(bridge),
        "claim",
        "--agent",
        "codex-1",
        "--task-id",
        "task-env-lease",
        "--summary",
        "environment lease",
    )

    assert exit_code == 0
    assert report["claim"]["lease_seconds"] == 1000


def test_explicit_claim_cli_lease_overrides_environment(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    monkeypatch.setenv("AGENT_BRIDGE_STALE_LEASE_SECONDS", "1000")

    exit_code, report = _run(
        capsys,
        "--bridge-root",
        str(bridge),
        "claim",
        "--agent",
        "codex-1",
        "--task-id",
        "task-explicit-lease",
        "--summary",
        "explicit lease",
        "--lease-seconds",
        "45",
    )

    assert exit_code == 0
    assert report["claim"]["lease_seconds"] == 45


def test_cli_defaults_to_runtime_bridge_root_env_for_list(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    runtime_bridge = tmp_path / "runtime" / ".agent-bridge"
    claim_task(
        agent="codex-1",
        task_id="runtime-task",
        summary="runtime claim",
        bridge_root=runtime_bridge,
    )

    monkeypatch.setenv("AGENT_BRIDGE_RUNTIME_ROOT", str(runtime_bridge))
    monkeypatch.delenv("AGENT_BRIDGE_ROOT", raising=False)

    exit_code, report = _run(capsys, "list")

    assert exit_code == 0
    assert report["decision"] == "listed"
    assert [claim["task_id"] for claim in report["claims"]] == ["runtime-task"]


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


def test_claim_splits_comma_separated_write_scope(tmp_path: Path, capsys) -> None:
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
        "--write-scope",
        "tools/foo.py, tests/bar.py,, tools/foo.py",
        "--write-scope",
        "docs/readme.md",
    )

    assert exit_code == 0
    assert report["claim"]["write_scope"] == [
        "tools/foo.py",
        "tests/bar.py",
        "docs/readme.md",
    ]


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


def test_check_overlap_splits_comma_separated_write_scope(
    tmp_path: Path,
    capsys,
) -> None:
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
        "docs/readme.md, tools/foo.py",
    )
    exit_code, report = _run(
        capsys,
        "--bridge-root",
        str(bridge),
        "check-overlap",
        "--write-scope",
        "tests/bar.py, tools/foo.py",
    )

    assert exit_code == 0
    assert report["decision"] == "scope_overlap"
    assert len(report["claims"]) == 1
    assert report["claims"][0]["task_id"] == "task-001"


def test_check_overlap_normalizes_legacy_literal_comma_claim_scope(
    tmp_path: Path,
    capsys,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    claims_dir = bridge / "work_queue" / "claims"
    claims_dir.mkdir(parents=True)
    (claims_dir / "legacy-task.json").write_text(
        json.dumps(
            {
                "agent": "codex-1",
                "task_id": "legacy-task",
                "summary": "legacy literal comma scope",
                "mode": "write",
                "write_scope": ["tools/foo.py, tests/bar.py"],
                "run_id": "legacy-run",
                "claimed_at_utc": "2026-06-19T20:00:00Z",
                "last_heartbeat_utc": "2026-06-19T20:00:00Z",
                "lease_seconds": 900,
            }
        ),
        encoding="utf-8",
    )

    exit_code, report = _run(capsys, "--bridge-root", str(bridge), "list")
    assert exit_code == 0
    assert report["claims"][0]["write_scope"] == ["tools/foo.py", "tests/bar.py"]

    exit_code, report = _run(
        capsys,
        "--bridge-root",
        str(bridge),
        "check-overlap",
        "--write-scope",
        "tests/bar.py",
    )

    assert exit_code == 0
    assert report["decision"] == "scope_overlap"
    assert len(report["claims"]) == 1
    assert report["claims"][0]["task_id"] == "legacy-task"


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


def test_stale_command_honors_environment_fallback_for_legacy_claim(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    claim_task(
        agent="codex-1",
        task_id="legacy-env-stale",
        summary="legacy fallback",
        bridge_root=bridge,
        now_utc=datetime.now(timezone.utc) - timedelta(seconds=500),
    )
    claim_path = bridge / "work_queue" / "claims" / "legacy-env-stale.json"
    payload = json.loads(claim_path.read_text(encoding="utf-8"))
    payload.pop("lease_seconds")
    payload.pop("claim_lease_expires_utc")
    claim_path.write_text(json.dumps(payload), encoding="utf-8")

    monkeypatch.setenv("AGENT_BRIDGE_STALE_LEASE_SECONDS", "1000")
    exit_code, report = _run(
        capsys,
        "--bridge-root",
        str(bridge),
        "stale",
    )
    assert exit_code == 0
    assert report["claims"] == []

    monkeypatch.setenv("AGENT_BRIDGE_STALE_LEASE_SECONDS", "100")
    exit_code, report = _run(
        capsys,
        "--bridge-root",
        str(bridge),
        "stale",
    )
    assert exit_code == 3
    assert [claim["task_id"] for claim in report["claims"]] == [
        "legacy-env-stale"
    ]


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
