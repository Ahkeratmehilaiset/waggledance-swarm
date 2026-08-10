from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tools.work_queue import main
from waggledance.core.work_queue import claim_task


@pytest.fixture(autouse=True)
def _valid_work_queue_owner_context(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_BRIDGE_AGENT", raising=False)
    monkeypatch.setenv("AGENT_BRIDGE_SESSION_ID", "pytest-session")
    monkeypatch.setenv("AGENT_BRIDGE_OWNER_SESSION_ID", "pytest-session")
    monkeypatch.setenv("AGENT_BRIDGE_OWNER_TOKEN", "a" * 64)
    monkeypatch.setenv("AGENT_BRIDGE_OWNER_PID", str(os.getpid()))
    monkeypatch.setenv(
        "AGENT_BRIDGE_OWNER_PROCESS_START_UTC",
        "2026-07-28T00:00:00Z",
    )
    for name in (
        "AGENT_BRIDGE_ROLE",
        "AGENT_BRIDGE_AGENT_UUID",
        "AGENT_BRIDGE_CAPABILITIES",
        "AGENT_BRIDGE_RUN_ID",
    ):
        monkeypatch.delenv(name, raising=False)


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
    assert report["claim"]["session_id"] == "pytest-session"
    assert report["claim"]["owner_session_id"] == "pytest-session"
    assert report["claim"]["owner_token_sha256"] == hashlib.sha256(
        ("a" * 64).encode("utf-8")
    ).hexdigest()
    assert report["claim"]["owner_pid"] == os.getpid()
    assert "owner_token" not in report["claim"]
    assert "a" * 64 not in json.dumps(report, sort_keys=True)

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


@pytest.mark.parametrize(
    "missing_name",
    [
        "AGENT_BRIDGE_OWNER_SESSION_ID",
        "AGENT_BRIDGE_OWNER_TOKEN",
        "AGENT_BRIDGE_OWNER_PID",
        "AGENT_BRIDGE_OWNER_PROCESS_START_UTC",
    ],
)
def test_claim_refuses_missing_owner_context_before_runtime_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    missing_name: str,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    monkeypatch.delenv(missing_name)

    exit_code, report = _run(
        capsys,
        "--bridge-root",
        str(bridge),
        "claim",
        "--agent",
        "codex-1",
        "--task-id",
        "missing-owner-context",
        "--summary",
        "must fail before creating runtime state",
    )

    assert exit_code == 3
    assert report["ok"] is False
    assert "claim_owner_context_invalid" in report["errors"][0]
    assert not bridge.exists()


def test_cli_claim_inherits_bound_session_metadata_and_release_preserves_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bridge = tmp_path / ".agent-bridge"
    monkeypatch.setenv("AGENT_BRIDGE_RUN_ID", "run-from-session")
    monkeypatch.setenv("AGENT_BRIDGE_ROLE", "tools_impl")
    monkeypatch.setenv(
        "AGENT_BRIDGE_AGENT_UUID",
        "7A8AF68D-20BC-4598-9953-23C5DD98B102",
    )
    monkeypatch.setenv("AGENT_BRIDGE_CAPABILITIES", "implementation,tests")

    exit_code, report = _run(
        capsys,
        "--bridge-root",
        str(bridge),
        "claim",
        "--agent",
        "codex-tools-1",
        "--task-id",
        "bound-cli-claim",
        "--summary",
        "preserve bound metadata",
    )
    assert exit_code == 0, report
    claim = report["claim"]
    assert claim["run_id"] == "run-from-session"
    assert claim["role"] == "tools_impl"
    assert claim["agent_uuid"] == "7a8af68d-20bc-4598-9953-23c5dd98b102"
    assert claim["capabilities"] == ["implementation", "tests"]

    heartbeat_code, heartbeat_report = _run(
        capsys,
        "--bridge-root",
        str(bridge),
        "heartbeat",
        "--agent",
        "codex-tools-1",
        "--task-id",
        "bound-cli-claim",
    )
    assert heartbeat_code == 0, heartbeat_report
    assert heartbeat_report["claim"]["agent_uuid"] == claim["agent_uuid"]

    release_code, release_report = _run(
        capsys,
        "--bridge-root",
        str(bridge),
        "release",
        "--agent",
        "codex-tools-1",
        "--task-id",
        "bound-cli-claim",
    )
    assert release_code == 0, release_report
    release = release_report["release"]
    assert release["run_id"] == "run-from-session"
    assert release["role"] == "tools_impl"
    assert release["agent_uuid"] == claim["agent_uuid"]
    assert release["capabilities"] == ["implementation", "tests"]


def test_cli_refuses_wrong_generation_mutations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bridge = tmp_path / ".agent-bridge"
    exit_code, report = _run(
        capsys,
        "--bridge-root",
        str(bridge),
        "claim",
        "--agent",
        "codex-1",
        "--task-id",
        "generation-bound",
        "--summary",
        "owned by generation A",
    )
    assert exit_code == 0, report
    claim_path = bridge / "work_queue" / "claims" / "generation-bound.json"
    original = claim_path.read_bytes()
    monkeypatch.setenv("AGENT_BRIDGE_OWNER_TOKEN", "b" * 64)

    for command in ("heartbeat", "release"):
        mutation_code, mutation_report = _run(
            capsys,
            "--bridge-root",
            str(bridge),
            command,
            "--agent",
            "codex-1",
            "--task-id",
            "generation-bound",
        )
        assert mutation_code == 3
        assert "claim_owner_wrong_generation" in mutation_report["errors"][0]
        assert claim_path.read_bytes() == original


def test_cli_refuses_agent_name_outside_bound_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bridge = tmp_path / ".agent-bridge"
    monkeypatch.setenv("AGENT_BRIDGE_AGENT", "codex-tools-1")

    exit_code, report = _run(
        capsys,
        "--bridge-root",
        str(bridge),
        "claim",
        "--agent",
        "codex-lead-1",
        "--task-id",
        "wrong-bound-agent",
        "--summary",
        "must not impersonate another agent",
    )

    assert exit_code == 3
    assert "claim_owner_agent_mismatch" in report["errors"][0]
    assert not bridge.exists()


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
