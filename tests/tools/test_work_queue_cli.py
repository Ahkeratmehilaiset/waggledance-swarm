from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tools.work_queue import main
from waggledance.core.work_queue import claim_task

_IDENTITY_ENV = (
    "AGENT_BRIDGE_AGENT",
    "AGENT_BRIDGE_OWNER_SESSION_ID",
    "AGENT_BRIDGE_OWNER_TOKEN",
    "AGENT_BRIDGE_RUN_ID",
    "AGENT_BRIDGE_OWNER_PID",
    "AGENT_BRIDGE_OWNER_PROCESS_START_UTC",
)


@pytest.fixture(autouse=True)
def _hermetic_owner_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test runs as one known B7 owner, bound to no agent label."""
    for name in _IDENTITY_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AGENT_BRIDGE_OWNER_SESSION_ID", "test-session")
    monkeypatch.setenv("AGENT_BRIDGE_OWNER_TOKEN", "test-token")


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


# -- QB-L1 (RCO2 FF7BC9A6): a writer's OWN OSError is a JSON io_error report, never a bare traceback --------------

import contextlib  # noqa: E402

import tools.work_queue as wq  # noqa: E402
from tools.bridge_v2_queue_transactions import QueueTransactionError  # noqa: E402


class _CoreIOError(OSError):
    """Stand-in for the core's typed I/O error (fable-5's side of the QB-L1 interface): an OSError carrying
    applied (True / False = proven nothing left / None = unknown), completed, rollback_errors and residual."""

    def __init__(self, text, *, applied=None, completed=(), rollback_errors=(), residual=()):
        super().__init__(13, text)
        self.applied, self.completed = applied, list(completed)
        self.rollback_errors, self.residual = list(rollback_errors), list(residual)


def _no_mutex(monkeypatch) -> None:
    monkeypatch.setattr(wq, "_root_mutex", lambda root: contextlib.nullcontext())


def _failing(monkeypatch, name: str, error: OSError) -> None:
    def fail(**kwargs):
        raise error
    monkeypatch.setattr(wq, name, fail)


def _release(capsys, bridge: Path) -> tuple[int, dict]:
    return _run(capsys, "--bridge-root", str(bridge), "release", "--agent", "codex-1", "--task-id", "task-io")


def test_an_untyped_writer_os_error_is_json_with_an_unknown_outcome_and_exit_4(tmp_path, capsys, monkeypatch):
    _no_mutex(monkeypatch)
    _failing(monkeypatch, "release_task", PermissionError(13, "Access is denied", "claims/task-io.json"))
    exit_code, report = _release(capsys, tmp_path / ".agent-bridge")
    assert exit_code == wq.RECONCILE_EXIT_CODE == 4
    assert report["ok"] is False and report["decision"] == "io_error"
    assert report["applied"] is None and report["outcome"] == "io_error_outcome_unknown"
    assert report["errors"][0].startswith("PermissionError: ") and "Access is denied" in report["errors"][0]
    assert report["rollback_errors"] == [] and report["residual"] == [] and "mutex_cleanup_errors" not in report


@pytest.mark.parametrize("error,code,outcome", [
    (_CoreIOError("unlink failed; done record rolled back", applied=False), 1, "io_error_nothing_applied"),
    (_CoreIOError("half applied", applied=True), 4, "io_error_applied"),
    (_CoreIOError("unlink failed; rollback failed", applied=False, rollback_errors=["PermissionError: done"],
                  residual=["work_queue/done/task-io.json"]), 4, "io_error_outcome_unknown"),
], ids=["proven_nothing_applied", "applied", "rollback_failed"])
def test_a_typed_writer_os_error_exits_by_what_it_proves(tmp_path, capsys, monkeypatch, error, code, outcome):
    _no_mutex(monkeypatch)
    _failing(monkeypatch, "release_task", error)
    exit_code, report = _release(capsys, tmp_path / ".agent-bridge")
    assert exit_code == code and report["outcome"] == outcome and report["decision"] == "io_error"
    assert report["applied"] is error.applied and report["rollback_errors"] == error.rollback_errors
    assert report["residual"] == error.residual


def test_a_heartbeat_write_error_is_json_and_never_claimed_harmless(tmp_path, capsys, monkeypatch):
    _no_mutex(monkeypatch)
    _failing(monkeypatch, "heartbeat", OSError(28, "No space left on device"))
    exit_code, report = _run(capsys, "--bridge-root", str(tmp_path / ".agent-bridge"), "heartbeat", "--agent",
                             "codex-1", "--task-id", "task-io")
    assert exit_code == 4 and report["decision"] == "io_error" and report["outcome"] == "io_error_outcome_unknown"


def test_a_body_error_keeps_its_place_and_lists_the_recorded_mutex_cleanup_failure(tmp_path, capsys, monkeypatch):
    # The port records a release failure ON the body's error (descriptor_close_unknown) and re-raises the body's error.
    @contextlib.contextmanager
    def recording(root):
        try:
            yield
        except BaseException as body_error:
            body_error.descriptor_close_unknown = [("mutex_release", 7, OSError(None, "release failed", None, 288))]
            raise

    monkeypatch.setattr(wq, "_root_mutex", recording)
    _failing(monkeypatch, "release_task", PermissionError(13, "Access is denied"))
    exit_code, report = _release(capsys, tmp_path / ".agent-bridge")
    assert exit_code == 4 and report["errors"][0].startswith("PermissionError: ")
    [cleanup] = report["mutex_cleanup_errors"]
    assert cleanup.startswith("mutex_release failed: OSError: ") and cleanup.endswith("release failed")


def test_a_safe_writer_and_the_refusals_are_unchanged(tmp_path, capsys, monkeypatch):
    bridge = tmp_path / ".agent-bridge"
    _no_mutex(monkeypatch)
    exit_code, report = _run(capsys, "--bridge-root", str(bridge), "claim", "--agent", "codex-1", "--task-id",
                             "task-io", "--summary", "s")
    assert exit_code == 0 and report["ok"] is True and "outcome" not in report and "applied" not in report
    exit_code, report = _run(capsys, "--bridge-root", str(bridge), "release", "--agent", "codex-2", "--task-id",
                             "task-io")                                       # not the owner: a refusal, not io_error
    assert exit_code not in (0, 4) and report["decision"] == "work_queue_error"

    @contextlib.contextmanager
    def busy(root):
        raise QueueTransactionError("runtime-root mutex busy")
        yield

    monkeypatch.setattr(wq, "_root_mutex", busy)
    exit_code, report = _release(capsys, bridge)
    assert exit_code == 1 and report["errors"] == ["runtime-root mutex: runtime-root mutex busy"]
