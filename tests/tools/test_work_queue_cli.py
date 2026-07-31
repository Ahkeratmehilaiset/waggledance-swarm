from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tools.work_queue import main
from waggledance.core.work_queue import claim_task


@pytest.fixture(autouse=True)
def _valid_work_queue_owner_context(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_BRIDGE_AGENT", raising=False)
    monkeypatch.delenv("AGENT_BRIDGE_STALE_LEASE_SECONDS", raising=False)
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
    assert report["claim"]["owner_process_start_utc"].startswith(
        "2026-07-28T00:00:00"
    )
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
    assert report["decision"] == "work_queue_error"
    assert "claim_owner_context_invalid" in report["errors"][0]
    assert not bridge.exists()


def test_cli_refuses_legacy_tokenless_claim_mutations(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bridge = tmp_path / ".agent-bridge"
    claims_dir = bridge / "work_queue" / "claims"
    claims_dir.mkdir(parents=True)
    claim_path = claims_dir / "legacy-tokenless.json"
    legacy_claim = {
        "agent": "codex-1",
        "task_id": "legacy-tokenless",
        "summary": "legacy claim cannot acquire a new owner",
        "mode": "read-only",
        "write_scope": [],
        "run_id": "",
        "claimed_at_utc": "2026-07-28T00:00:00Z",
        "last_heartbeat_utc": "2026-07-28T00:00:00Z",
        "lease_seconds": 900,
    }
    claim_path.write_text(json.dumps(legacy_claim), encoding="utf-8")
    original = claim_path.read_bytes()

    for command in ("heartbeat", "release"):
        exit_code, report = _run(
            capsys,
            "--bridge-root",
            str(bridge),
            command,
            "--agent",
            "codex-1",
            "--task-id",
            "legacy-tokenless",
        )
        assert exit_code == 3
        assert report["ok"] is False
        assert "claim_owner_legacy_tokenless" in report["errors"][0]
        assert claim_path.read_bytes() == original

    assert not (bridge / "work_queue" / "done").exists()


def test_cli_refuses_same_agent_wrong_generation_mutations(
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

    mutation_commands = [
        (
            "heartbeat",
            "--agent",
            "codex-1",
            "--task-id",
            "generation-bound",
        ),
        (
            "release",
            "--agent",
            "codex-1",
            "--task-id",
            "generation-bound",
        ),
        (
            "claim",
            "--agent",
            "codex-1",
            "--task-id",
            "generation-bound",
            "--summary",
            "generation B must not force replace",
            "--force",
        ),
    ]
    for command in mutation_commands:
        exit_code, report = _run(
            capsys,
            "--bridge-root",
            str(bridge),
            *command,
        )
        assert exit_code == 3
        assert report["ok"] is False
        assert "claim_owner_wrong_generation" in report["errors"][0]
        assert claim_path.read_bytes() == original

    assert not (bridge / "work_queue" / "done").exists()


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


def test_stale_command_defaults_to_300_second_fallback(
    tmp_path: Path,
    capsys,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    claims_dir = bridge / "work_queue" / "claims"
    claims_dir.mkdir(parents=True)
    claimed_at = (
        datetime.now(timezone.utc) - timedelta(seconds=301)
    ).isoformat().replace("+00:00", "Z")
    (claims_dir / "legacy-default-stale.json").write_text(
        json.dumps(
            {
                "agent": "codex-1",
                "task_id": "legacy-default-stale",
                "summary": "legacy claim uses CLI fallback instead of stored lease",
                "mode": "read-only",
                "write_scope": [],
                "run_id": "",
                "claimed_at_utc": claimed_at,
                "last_heartbeat_utc": (
                    datetime.now(timezone.utc) + timedelta(days=1)
                ).isoformat().replace("+00:00", "Z"),
                "lease_seconds": 900,
            }
        ),
        encoding="utf-8",
    )

    exit_code, report = _run(
        capsys,
        "--bridge-root",
        str(bridge),
        "stale",
    )

    assert exit_code == 3
    assert [claim["task_id"] for claim in report["claims"]] == [
        "legacy-default-stale"
    ]


def test_claim_over_int32_lease_returns_work_queue_error_without_write(
    tmp_path: Path,
    capsys,
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
        "cli-invalid-lease",
        "--summary",
        "must not write",
        "--lease-seconds",
        str(2**31),
    )

    assert exit_code == 2
    assert report["decision"] == "work_queue_error"
    assert "positive Int32" in report["errors"][0]
    assert not bridge.exists()


@pytest.mark.parametrize(
    "command_args",
    [
        (
            "claim",
            "--agent",
            "codex-1",
            "--task-id",
            "cli-ascii-integer-claim",
            "--summary",
            "must not write",
            "--lease-seconds",
        ),
        (
            "heartbeat",
            "--agent",
            "codex-1",
            "--task-id",
            "cli-ascii-integer-heartbeat",
            "--lease-seconds",
        ),
        ("stale", "--max-age-seconds"),
    ],
    ids=["claim-lease", "heartbeat-lease", "stale-max-age"],
)
@pytest.mark.parametrize(
    "invalid_integer",
    ["٤٢", "1_0"],
    ids=["unicode-digits", "python-literal-separator"],
)
def test_integer_arguments_require_ascii_decimal_without_write(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    command_args: tuple[str, ...],
    invalid_integer: str,
) -> None:
    bridge = tmp_path / ".agent-bridge"

    with pytest.raises(SystemExit) as exc_info:
        main(
            [
                "--bridge-root",
                str(bridge),
                *command_args,
                invalid_integer,
            ]
        )

    captured = capsys.readouterr()
    assert exc_info.value.code == 2
    assert "expected an ASCII base-10 integer" in captured.err
    assert captured.out == ""
    assert not bridge.exists()
    assert list(tmp_path.iterdir()) == []


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
