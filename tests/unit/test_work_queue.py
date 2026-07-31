from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import waggledance.core.work_queue as work_queue_module
from waggledance.core.work_queue import (
    DEFAULT_LEASE_SECONDS,
    Claim,
    WorkQueueError,
    check_scope_overlap,
    claim_task,
    detect_stale_claims,
    heartbeat,
    list_claims,
    release_task,
)

OWNER_TOKEN = "a" * 64
OWNER_SESSION_ID = "pytest-owner-session"


@pytest.fixture(autouse=True)
def _valid_owner_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_BRIDGE_AGENT", raising=False)
    monkeypatch.setenv("AGENT_BRIDGE_SESSION_ID", "pytest-work-queue-session")
    monkeypatch.setenv("AGENT_BRIDGE_OWNER_SESSION_ID", OWNER_SESSION_ID)
    monkeypatch.setenv("AGENT_BRIDGE_OWNER_TOKEN", OWNER_TOKEN)
    monkeypatch.setenv("AGENT_BRIDGE_OWNER_PID", "4242")
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


def test_claim_creates_persistent_claim_file(tmp_path: Path) -> None:
    bridge = tmp_path / ".agent-bridge"
    claim = claim_task(
        agent="claude-1",
        task_id="test-task-001",
        summary="run smoke check",
        bridge_root=bridge,
    )
    assert claim.agent == "claude-1"
    assert claim.task_id == "test-task-001"
    assert claim.summary == "run smoke check"
    assert (bridge / "work_queue" / "claims" / "test-task-001.json").exists()


def test_claim_accepts_bridge_namespaced_task_id(tmp_path: Path) -> None:
    bridge = tmp_path / ".agent-bridge"
    task_id = "codex-tools-1/magma-share-admission-status-bridge-template-20260613"

    claim = claim_task(
        agent="codex-tools-1",
        task_id=task_id,
        summary="claim bridge task",
        bridge_root=bridge,
    )
    record = release_task(
        agent="codex-tools-1",
        task_id=task_id,
        release_status="done",
        bridge_root=bridge,
    )

    assert claim.task_id == task_id
    assert record.task_id == task_id
    assert not (bridge / "work_queue" / "claims" / "codex-tools-1").exists()
    assert len(list((bridge / "work_queue" / "done").glob("*.json"))) == 1


def test_bridge_namespaced_task_id_file_name_does_not_collide(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"

    claim_task(
        agent="codex-tools-1",
        task_id="codex-tools-1/task",
        summary="slash namespace",
        bridge_root=bridge,
    )
    claim_task(
        agent="codex-tools-1",
        task_id="codex-tools-1_task",
        summary="underscore namespace",
        bridge_root=bridge,
    )

    claim_files = sorted((bridge / "work_queue" / "claims").glob("*.json"))
    assert len(claim_files) == 2
    assert {path.stem for path in claim_files} != {"codex-tools-1_task"}


def test_claim_refuses_preferred_path_occupied_by_different_task_without_write(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    requested_task_id = "codex-tools-1/preferred-path-collision"
    claim_task(
        agent="codex-tools-1",
        task_id="different-logical-task",
        summary="occupy another task's preferred path",
        bridge_root=bridge,
    )
    claims_dir = bridge / "work_queue" / "claims"
    original_path = claims_dir / "different-logical-task.json"
    safe = requested_task_id.replace("/", "_")
    digest = hashlib.sha256(requested_task_id.encode("utf-8")).hexdigest()[:12]
    collision_path = claims_dir / f"{safe}-{digest}.json"
    original_path.rename(collision_path)
    before = {
        path.name: path.read_bytes()
        for path in claims_dir.glob("*.json")
    }

    with pytest.raises(WorkQueueError, match="claim filename collision"):
        claim_task(
            agent="codex-tools-1",
            task_id=requested_task_id,
            summary="must not overwrite a different logical task",
            bridge_root=bridge,
            force=True,
        )

    assert {
        path.name: path.read_bytes()
        for path in claims_dir.glob("*.json")
    } == before
    assert not (bridge / "work_queue" / "done").exists()


def test_heartbeat_accepts_legacy_powershell_namespaced_claim_file(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    task_id = "codex-tools-1/legacy-powershell-claim"
    initial_time = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)
    later_time = datetime(2026, 6, 15, 12, 5, tzinfo=timezone.utc)

    claim_task(
        agent="codex-tools-1",
        task_id=task_id,
        summary="legacy claim",
        bridge_root=bridge,
        now_utc=initial_time,
    )
    claims_dir = bridge / "work_queue" / "claims"
    preferred_path = next(claims_dir.glob("*.json"))
    legacy_path = claims_dir / "codex-tools-1_legacy-powershell-claim.json"
    preferred_path.rename(legacy_path)

    refreshed = heartbeat(
        agent="codex-tools-1",
        task_id=task_id,
        bridge_root=bridge,
        now_utc=later_time,
    )

    assert refreshed.task_id == task_id
    assert refreshed.last_heartbeat_utc == "2026-06-15T12:05:00Z"
    assert legacy_path.exists()


def test_release_accepts_legacy_powershell_namespaced_claim_file(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    task_id = "codex-tools-1/legacy-release-claim"

    claim_task(
        agent="codex-tools-1",
        task_id=task_id,
        summary="legacy release",
        bridge_root=bridge,
    )
    claims_dir = bridge / "work_queue" / "claims"
    preferred_path = next(claims_dir.glob("*.json"))
    legacy_path = claims_dir / "codex-tools-1_legacy-release-claim.json"
    preferred_path.rename(legacy_path)

    record = release_task(
        agent="codex-tools-1",
        task_id=task_id,
        release_status="done",
        bridge_root=bridge,
    )

    assert record.task_id == task_id
    assert not legacy_path.exists()
    assert len(list((bridge / "work_queue" / "done").glob("*.json"))) == 1


def test_heartbeat_legacy_lookup_ignores_malformed_claim_files(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    task_id = "codex-tools-1/legacy-with-noisy-claim-dir"

    claim_task(
        agent="codex-tools-1",
        task_id=task_id,
        summary="legacy claim",
        bridge_root=bridge,
    )
    claims_dir = bridge / "work_queue" / "claims"
    preferred_path = next(claims_dir.glob("*.json"))
    legacy_path = claims_dir / "codex-tools-1_legacy-with-noisy-claim-dir.json"
    preferred_path.rename(legacy_path)
    (claims_dir / "broken.json").write_text("{not json", encoding="utf-8")
    (claims_dir / "not-object.json").write_text("[]", encoding="utf-8")

    refreshed = heartbeat(
        agent="codex-tools-1",
        task_id=task_id,
        bridge_root=bridge,
    )

    assert refreshed.task_id == task_id
    assert legacy_path.exists()


def test_heartbeat_legacy_lookup_does_not_accept_colliding_safe_name_file(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    slash_task_id = "codex-tools-1/collision"
    underscore_task_id = "codex-tools-1_collision"

    claim_task(
        agent="codex-tools-1",
        task_id=underscore_task_id,
        summary="underscore claim",
        bridge_root=bridge,
    )
    claims_dir = bridge / "work_queue" / "claims"
    assert (claims_dir / "codex-tools-1_collision.json").exists()

    with pytest.raises(WorkQueueError, match="no active claim"):
        heartbeat(
            agent="codex-tools-1",
            task_id=slash_task_id,
            bridge_root=bridge,
        )


def test_heartbeat_missing_claims_dir_reports_missing_claim(tmp_path: Path) -> None:
    bridge = tmp_path / ".agent-bridge"

    with pytest.raises(WorkQueueError, match="no active claim"):
        heartbeat(
            agent="codex-tools-1",
            task_id="codex-tools-1/missing-claim-dir",
            bridge_root=bridge,
        )


def test_claim_rejects_invalid_agent(tmp_path: Path) -> None:
    bridge = tmp_path / ".agent-bridge"
    with pytest.raises(WorkQueueError):
        claim_task(
            agent="Claude-1",  # uppercase forbidden
            task_id="test-task-001",
            summary="run smoke check",
            bridge_root=bridge,
        )


def test_claim_rejects_empty_summary(tmp_path: Path) -> None:
    bridge = tmp_path / ".agent-bridge"
    with pytest.raises(WorkQueueError):
        claim_task(
            agent="claude-1",
            task_id="test-task-001",
            summary="",
            bridge_root=bridge,
        )


def test_claim_rejects_invalid_mode(tmp_path: Path) -> None:
    bridge = tmp_path / ".agent-bridge"
    with pytest.raises(WorkQueueError):
        claim_task(
            agent="claude-1",
            task_id="test-task-001",
            summary="run smoke check",
            mode="delete-everything",
            bridge_root=bridge,
        )


def test_claim_rejects_non_ascii_write_scope(tmp_path: Path) -> None:
    bridge = tmp_path / ".agent-bridge"

    with pytest.raises(WorkQueueError, match="printable ASCII"):
        claim_task(
            agent="codex-1",
            task_id="unicode-write-scope",
            summary="reject cross-runtime scope normalization divergence",
            mode="write",
            write_scope=["tools/\u0130mpl.py"],
            bridge_root=bridge,
        )

    assert not bridge.exists()


@pytest.mark.parametrize(
    "write_scope",
    [
        "tools//foo.py",
        "tools/./foo.py",
        "tools/sub/../foo.py",
    ],
)
def test_claim_rejects_noncanonical_write_scope_segments(
    tmp_path: Path,
    write_scope: str,
) -> None:
    bridge = tmp_path / ".agent-bridge"

    with pytest.raises(WorkQueueError, match="empty, '\\.' or '\\.\\.'"):
        claim_task(
            agent="codex-1",
            task_id="noncanonical-write-scope",
            summary="reject filesystem-alias scope spellings",
            mode="write",
            write_scope=[write_scope],
            bridge_root=bridge,
        )

    assert not bridge.exists()


@pytest.mark.parametrize(
    ("write_scope", "error"),
    [
        ("tools/foo.py.", "end in"),
        ("tools/dir. /foo.py", "end in"),
        ("tools/foo.py::$DATA", "must not contain ':'"),
        ("/tools/foo.py", "repository-relative"),
        ("\\\\server\\share\\foo.py", "repository-relative"),
    ],
)
def test_claim_rejects_windows_alias_write_scope(
    tmp_path: Path,
    write_scope: str,
    error: str,
) -> None:
    bridge = tmp_path / ".agent-bridge"

    with pytest.raises(WorkQueueError, match=error):
        claim_task(
            agent="codex-1",
            task_id="windows-alias-write-scope",
            summary="reject Windows path aliases",
            mode="write",
            write_scope=[write_scope],
            bridge_root=bridge,
        )

    assert not bridge.exists()


def test_claim_rejects_nonstring_requested_write_scope_entry(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"

    with pytest.raises(WorkQueueError, match="entries must be strings"):
        claim_task(
            agent="codex-1",
            task_id="nonstring-requested-write-scope",
            summary="reject partially coercible write scope",
            mode="write",
            write_scope=["tools", 123],  # type: ignore[list-item]
            bridge_root=bridge,
        )

    assert not bridge.exists()


def test_claim_write_mode_requires_scope(tmp_path: Path) -> None:
    bridge = tmp_path / ".agent-bridge"
    with pytest.raises(WorkQueueError, match="write claims require"):
        claim_task(
            agent="claude-1",
            task_id="task-001",
            summary="edit without scope",
            mode="write",
            bridge_root=bridge,
        )


@pytest.mark.parametrize("write_scope", [["///"], ["\\"], [" / , \\\\ "]])
def test_claim_write_mode_rejects_overlap_empty_scope_before_write(
    tmp_path: Path,
    write_scope: list[str],
) -> None:
    bridge = tmp_path / ".agent-bridge"

    with pytest.raises(WorkQueueError, match="repository-relative"):
        claim_task(
            agent="claude-1",
            task_id="overlap-empty-scope",
            summary="scope must normalize to a path",
            mode="write",
            write_scope=write_scope,
            bridge_root=bridge,
        )

    assert not bridge.exists()


def test_claim_refuses_overlapping_agent_claim(tmp_path: Path) -> None:
    bridge = tmp_path / ".agent-bridge"
    claim_task(
        agent="claude-1",
        task_id="task-001",
        summary="x",
        bridge_root=bridge,
    )
    with pytest.raises(WorkQueueError, match="already claimed"):
        claim_task(
            agent="codex-1",
            task_id="task-001",
            summary="y",
            bridge_root=bridge,
        )


def test_claim_same_agent_requires_force_for_refresh(tmp_path: Path) -> None:
    bridge = tmp_path / ".agent-bridge"
    claim_task(
        agent="claude-1",
        task_id="task-001",
        summary="first",
        bridge_root=bridge,
    )
    with pytest.raises(WorkQueueError, match="already claimed"):
        claim_task(
            agent="claude-1",
            task_id="task-001",
            summary="refresh without force",
            bridge_root=bridge,
        )
    refreshed = claim_task(
        agent="claude-1",
        task_id="task-001",
        summary="refresh",
        bridge_root=bridge,
        force=True,
    )
    assert refreshed.summary == "refresh"


@pytest.mark.parametrize(
    "missing_name",
    [
        "AGENT_BRIDGE_OWNER_SESSION_ID",
        "AGENT_BRIDGE_OWNER_TOKEN",
        "AGENT_BRIDGE_OWNER_PID",
        "AGENT_BRIDGE_OWNER_PROCESS_START_UTC",
    ],
)
def test_claim_requires_complete_owner_context_before_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    missing_name: str,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    monkeypatch.delenv(missing_name)

    with pytest.raises(WorkQueueError, match="claim_owner_context_invalid"):
        claim_task(
            agent="claude-1",
            task_id="task-owner-context",
            summary="must fail without owner context",
            bridge_root=bridge,
        )

    assert not bridge.exists()


def test_claim_rejects_owner_pid_outside_powershell_range_before_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    monkeypatch.setenv("AGENT_BRIDGE_OWNER_PID", str(2**31))

    with pytest.raises(WorkQueueError, match="claim_owner_context_invalid"):
        claim_task(
            agent="claude-1",
            task_id="task-owner-pid-overflow",
            summary="must fail before writing",
            bridge_root=bridge,
        )

    assert not bridge.exists()


@pytest.mark.parametrize(
    "owner_pid",
    [" 4242 ", "+4242", "\u0664\u0662", "\uff14\uff12"],
    ids=["whitespace", "plus-sign", "arabic-indic", "fullwidth"],
)
def test_claim_requires_ascii_only_ambient_owner_pid_before_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    owner_pid: str,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    monkeypatch.setenv("AGENT_BRIDGE_OWNER_PID", owner_pid)

    with pytest.raises(WorkQueueError, match="claim_owner_context_invalid"):
        claim_task(
            agent="claude-1",
            task_id="task-owner-pid-ascii-only",
            summary="must reject decorated or non-ASCII owner PID",
            bridge_root=bridge,
        )

    assert not bridge.exists()


def test_claim_serializes_owner_digest_and_optional_session_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    monkeypatch.setenv("AGENT_BRIDGE_ROLE", "lead_impl")
    monkeypatch.setenv(
        "AGENT_BRIDGE_AGENT_UUID",
        "D3C9D1D1-96A9-4EB8-A8E2-6F05F9D1A101",
    )
    monkeypatch.setenv(
        "AGENT_BRIDGE_CAPABILITIES",
        "implementation,work_queue;implementation",
    )

    claim = claim_task(
        agent="claude-1",
        task_id="task-owner-metadata",
        summary="persist owner metadata",
        bridge_root=bridge,
    )
    claim_path = bridge / "work_queue" / "claims" / "task-owner-metadata.json"
    payload_text = claim_path.read_text(encoding="utf-8")
    payload = json.loads(payload_text)
    expected_digest = hashlib.sha256(OWNER_TOKEN.encode("utf-8")).hexdigest()

    assert claim.session_id == "pytest-work-queue-session"
    assert claim.owner_session_id == OWNER_SESSION_ID
    assert claim.owner_token_sha256 == expected_digest
    assert claim.owner_pid == 4242
    assert claim.owner_process_start_utc == "2026-07-28T00:00:00Z"
    assert claim.role == "lead_impl"
    assert claim.agent_uuid == "d3c9d1d1-96a9-4eb8-a8e2-6f05f9d1a101"
    assert claim.capabilities == ("implementation", "work_queue")
    assert payload["owner_token_sha256"] == expected_digest
    assert OWNER_TOKEN not in payload_text


def test_claim_session_id_falls_back_to_owner_session_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    monkeypatch.delenv("AGENT_BRIDGE_SESSION_ID")

    claim = claim_task(
        agent="claude-1",
        task_id="task-session-fallback",
        summary="use owner session as session id",
        bridge_root=bridge,
    )

    assert claim.session_id == OWNER_SESSION_ID
    payload = json.loads(
        (
            bridge
            / "work_queue"
            / "claims"
            / "task-session-fallback.json"
        ).read_text(encoding="utf-8")
    )
    assert payload["session_id"] == OWNER_SESSION_ID


def test_same_agent_wrong_generation_cannot_mutate_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    task_id = "task-wrong-generation"
    claim_task(
        agent="claude-1",
        task_id=task_id,
        summary="owned by generation A",
        bridge_root=bridge,
    )
    claim_path = bridge / "work_queue" / "claims" / f"{task_id}.json"
    original = claim_path.read_bytes()
    monkeypatch.setenv("AGENT_BRIDGE_OWNER_TOKEN", "b" * 64)

    operations = (
        lambda: claim_task(
            agent="claude-1",
            task_id=task_id,
            summary="generation B force",
            bridge_root=bridge,
            force=True,
        ),
        lambda: heartbeat(
            agent="claude-1",
            task_id=task_id,
            bridge_root=bridge,
        ),
        lambda: release_task(
            agent="claude-1",
            task_id=task_id,
            bridge_root=bridge,
        ),
    )
    for operation in operations:
        with pytest.raises(
            WorkQueueError,
            match="claim_owner_wrong_generation",
        ):
            operation()
        assert claim_path.read_bytes() == original
        assert not (bridge / "work_queue" / "done").exists()


def test_owner_generation_comparison_is_case_sensitive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    claim_task(
        agent="claude-1",
        task_id="task-owner-case",
        summary="case-sensitive owner",
        bridge_root=bridge,
    )
    monkeypatch.setenv(
        "AGENT_BRIDGE_OWNER_SESSION_ID",
        OWNER_SESSION_ID.upper(),
    )

    with pytest.raises(
        WorkQueueError,
        match="claim_owner_wrong_generation",
    ):
        heartbeat(
            agent="claude-1",
            task_id="task-owner-case",
            bridge_root=bridge,
        )


@pytest.mark.parametrize("operation_name", ["force", "heartbeat", "release"])
def test_mutations_require_complete_ambient_owner_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation_name: str,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    task_id = f"task-context-{operation_name}"
    claim_task(
        agent="claude-1",
        task_id=task_id,
        summary="complete owner context",
        bridge_root=bridge,
    )
    claim_path = bridge / "work_queue" / "claims" / f"{task_id}.json"
    original = claim_path.read_bytes()
    monkeypatch.delenv("AGENT_BRIDGE_OWNER_PID")

    if operation_name == "force":
        operation = lambda: claim_task(
            agent="claude-1",
            task_id=task_id,
            summary="missing diagnostic context",
            bridge_root=bridge,
            force=True,
        )
    elif operation_name == "heartbeat":
        operation = lambda: heartbeat(
            agent="claude-1",
            task_id=task_id,
            bridge_root=bridge,
        )
    else:
        operation = lambda: release_task(
            agent="claude-1",
            task_id=task_id,
            bridge_root=bridge,
        )

    with pytest.raises(WorkQueueError, match="claim_owner_context_invalid"):
        operation()
    assert claim_path.read_bytes() == original
    assert not (bridge / "work_queue" / "done").exists()


def test_diagnostic_owner_fields_do_not_authorize_mutations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    task_id = "task-diagnostic-owner-fields"
    monkeypatch.setenv("AGENT_BRIDGE_ROLE", "lead_impl")
    monkeypatch.setenv(
        "AGENT_BRIDGE_AGENT_UUID",
        "d3c9d1d1-96a9-4eb8-a8e2-6f05f9d1a101",
    )
    monkeypatch.setenv(
        "AGENT_BRIDGE_CAPABILITIES",
        "implementation,work_queue",
    )
    created = claim_task(
        agent="claude-1",
        task_id=task_id,
        summary="diagnostic owner fields",
        bridge_root=bridge,
    )
    claim_path = bridge / "work_queue" / "claims" / f"{task_id}.json"
    claim_payload = json.loads(claim_path.read_text(encoding="utf-8"))
    claim_payload.update(
        {
            "writer_pid": "9876",
            "writer_pid_semantics": "diagnostic_only",
            "cwd": r"C:\Python\project2",
            "git_branch": "codex-lead-1/example",
            "owner_token": OWNER_TOKEN,
        }
    )
    claim_path.write_text(json.dumps(claim_payload), encoding="utf-8")

    monkeypatch.setenv("AGENT_BRIDGE_OWNER_PID", "9999")
    monkeypatch.setenv(
        "AGENT_BRIDGE_OWNER_PROCESS_START_UTC",
        "2026-07-29T00:00:00Z",
    )
    monkeypatch.setenv("AGENT_BRIDGE_ROLE", "other_role")
    monkeypatch.delenv("AGENT_BRIDGE_AGENT_UUID")
    monkeypatch.setenv("AGENT_BRIDGE_CAPABILITIES", "other_capability")

    refreshed = heartbeat(
        agent="claude-1",
        task_id=task_id,
        bridge_root=bridge,
    )
    after_heartbeat = json.loads(claim_path.read_text(encoding="utf-8"))
    released = release_task(
        agent="claude-1",
        task_id=task_id,
        bridge_root=bridge,
    )
    archived_path = next((bridge / "work_queue" / "done").glob("*.json"))
    archived = json.loads(archived_path.read_text(encoding="utf-8"))

    assert refreshed.owner_pid == created.owner_pid == 4242
    assert refreshed.owner_process_start_utc == created.owner_process_start_utc
    assert refreshed.writer_pid == 9876
    assert refreshed.writer_pid_semantics == "diagnostic_only"
    assert refreshed.cwd == r"C:\Python\project2"
    assert refreshed.git_branch == "codex-lead-1/example"
    assert after_heartbeat["writer_pid"] == 9876
    assert after_heartbeat["writer_pid_semantics"] == "diagnostic_only"
    assert after_heartbeat["cwd"] == r"C:\Python\project2"
    assert after_heartbeat["git_branch"] == "codex-lead-1/example"
    assert "owner_token" not in after_heartbeat
    assert released.owner_pid == 4242
    assert released.writer_pid == 9876
    assert released.writer_pid_semantics == "diagnostic_only"
    assert released.cwd == r"C:\Python\project2"
    assert released.git_branch == "codex-lead-1/example"
    assert archived["owner_pid"] == 4242
    assert archived["owner_process_start_utc"] == "2026-07-28T00:00:00Z"
    assert archived["session_id"] == "pytest-work-queue-session"
    assert archived["role"] == "lead_impl"
    assert archived["agent_uuid"] == (
        "d3c9d1d1-96a9-4eb8-a8e2-6f05f9d1a101"
    )
    assert archived["capabilities"] == ["implementation", "work_queue"]
    assert archived["writer_pid"] == 9876
    assert archived["writer_pid_semantics"] == "diagnostic_only"
    assert archived["cwd"] == r"C:\Python\project2"
    assert archived["git_branch"] == "codex-lead-1/example"
    assert "owner_token" not in archived


def test_force_preserves_powershell_audit_fields(tmp_path: Path) -> None:
    bridge = tmp_path / ".agent-bridge"
    task_id = "task-force-audit-fields"
    claim_task(
        agent="claude-1",
        task_id=task_id,
        summary="PowerShell-shaped diagnostic fixture",
        bridge_root=bridge,
    )
    claim_path = bridge / "work_queue" / "claims" / f"{task_id}.json"
    payload = json.loads(claim_path.read_text(encoding="utf-8"))
    diagnostics = {
        "writer_pid": 9876,
        "writer_pid_semantics": "diagnostic_only",
        "cwd": r"C:\Python\project2",
        "git_branch": "codex-lead-1/example",
    }
    payload.update(diagnostics)
    claim_path.write_text(json.dumps(payload), encoding="utf-8")

    forced = claim_task(
        agent="claude-1",
        task_id=task_id,
        summary="Python force refresh",
        bridge_root=bridge,
        force=True,
    )
    after_force = json.loads(claim_path.read_text(encoding="utf-8"))

    assert forced.writer_pid == diagnostics["writer_pid"]
    assert forced.writer_pid_semantics == diagnostics["writer_pid_semantics"]
    assert forced.cwd == diagnostics["cwd"]
    assert forced.git_branch == diagnostics["git_branch"]
    assert {name: after_force[name] for name in diagnostics} == diagnostics


def test_mutations_refuse_duplicate_exact_task_records(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    task_id = "duplicate/task-record"
    claim_task(
        agent="claude-1",
        task_id=task_id,
        summary="duplicate exact task fixture",
        bridge_root=bridge,
    )
    claims_dir = bridge / "work_queue" / "claims"
    claim_path = next(claims_dir.glob("*.json"))
    shadow_path = claims_dir / "duplicate-shadow.json"
    shadow_path.write_bytes(claim_path.read_bytes())
    original = {
        path.name: path.read_bytes() for path in (claim_path, shadow_path)
    }

    operations = (
        lambda: claim_task(
            agent="claude-1",
            task_id=task_id,
            summary="must not force one duplicate",
            bridge_root=bridge,
            force=True,
        ),
        lambda: heartbeat(
            agent="claude-1",
            task_id=task_id,
            bridge_root=bridge,
        ),
        lambda: release_task(
            agent="claude-1",
            task_id=task_id,
            bridge_root=bridge,
        ),
    )
    for operation in operations:
        with pytest.raises(
            WorkQueueError,
            match="duplicate active claim records for exact task_id",
        ):
            operation()
        assert {
            path.name: path.read_bytes() for path in (claim_path, shadow_path)
        } == original
        assert not (bridge / "work_queue" / "done").exists()


def test_malformed_powershell_audit_fields_coerce_to_safe_defaults(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    task_id = "task-malformed-audit-fields"
    claim_task(
        agent="claude-1",
        task_id=task_id,
        summary="malformed audit metadata",
        bridge_root=bridge,
    )
    claim_path = bridge / "work_queue" / "claims" / f"{task_id}.json"
    payload = json.loads(claim_path.read_text(encoding="utf-8"))
    payload.update(
        {
            "writer_pid": True,
            "writer_pid_semantics": ["not", "text"],
            "cwd": {"not": "text"},
            "git_branch": None,
        }
    )
    claim_path.write_text(json.dumps(payload), encoding="utf-8")

    listed = list_claims(bridge_root=bridge)
    assert len(listed) == 1
    assert listed[0].writer_pid == 0
    assert listed[0].writer_pid_semantics == ""
    assert listed[0].cwd == ""
    assert listed[0].git_branch == ""

    heartbeat(
        agent="claude-1",
        task_id=task_id,
        bridge_root=bridge,
    )
    normalized = json.loads(claim_path.read_text(encoding="utf-8"))
    assert "writer_pid" not in normalized
    assert "writer_pid_semantics" not in normalized
    assert "cwd" not in normalized
    assert "git_branch" not in normalized


def test_legacy_tokenless_claim_cannot_be_mutated(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    claims_dir = bridge / "work_queue" / "claims"
    claims_dir.mkdir(parents=True)
    task_id = "task-legacy-tokenless"
    claim_path = claims_dir / f"{task_id}.json"
    claim_path.write_text(
        json.dumps(
            {
                "agent": "claude-1",
                "task_id": task_id,
                "summary": "legacy tokenless claim",
                "mode": "read-only",
                "write_scope": [],
                "run_id": "legacy-run",
                "claimed_at_utc": "2026-07-28T00:00:00Z",
                "last_heartbeat_utc": "2026-07-28T00:00:00Z",
                "lease_seconds": 900,
            }
        ),
        encoding="utf-8",
    )
    original = claim_path.read_bytes()

    operations = (
        lambda: claim_task(
            agent="claude-1",
            task_id=task_id,
            summary="force legacy claim",
            bridge_root=bridge,
            force=True,
        ),
        lambda: heartbeat(
            agent="claude-1",
            task_id=task_id,
            bridge_root=bridge,
        ),
        lambda: release_task(
            agent="claude-1",
            task_id=task_id,
            bridge_root=bridge,
        ),
    )
    for operation in operations:
        with pytest.raises(
            WorkQueueError,
            match="claim_owner_legacy_tokenless",
        ):
            operation()
        assert claim_path.read_bytes() == original
        assert not (bridge / "work_queue" / "done").exists()


@pytest.mark.parametrize(
    ("field_name", "malformed_value"),
    [
        ("owner_session_id", "bad/session"),
        ("owner_session_id", 123),
        ("owner_token_sha256", "A" * 64),
        ("owner_pid", "not-a-pid"),
        ("owner_pid", " 4242 "),
        ("owner_pid", 2**31),
        ("owner_process_start_utc", "not-a-time"),
        ("owner_process_start_utc", "2026-07-28T00:00:00Z\n"),
    ],
)
def test_malformed_stored_owner_field_fails_closed_before_mutation(
    tmp_path: Path,
    field_name: str,
    malformed_value: object,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    task_id = f"task-malformed-{field_name}"
    claim_task(
        agent="claude-1",
        task_id=task_id,
        summary="tamper stored owner field",
        bridge_root=bridge,
    )
    claim_path = bridge / "work_queue" / "claims" / f"{task_id}.json"
    payload = json.loads(claim_path.read_text(encoding="utf-8"))
    payload[field_name] = malformed_value
    claim_path.write_text(json.dumps(payload), encoding="utf-8")
    original = claim_path.read_bytes()

    with pytest.raises(WorkQueueError):
        heartbeat(
            agent="claude-1",
            task_id=task_id,
            bridge_root=bridge,
        )

    assert claim_path.read_bytes() == original


@pytest.mark.parametrize(
    ("field_name", "malformed_value"),
    [
        ("owner_pid", "4242"),
        ("lease_seconds", "900"),
        ("write_scope", "tools/authority.py"),
        ("last_heartbeat_utc", 123),
        ("claim_lease_expires_utc", 123),
        ("owner_process_start_utc", "2026-07-31 11:00:00"),
    ],
)
def test_raw_authority_field_type_erasure_fails_closed(
    tmp_path: Path,
    field_name: str,
    malformed_value: object,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    task_id = f"raw-shape-{field_name}"
    claim_task(
        agent="claude-1",
        task_id=task_id,
        summary="tamper authority-bearing raw field",
        mode="write",
        write_scope=["tools/authority.py"],
        bridge_root=bridge,
    )
    claim_path = bridge / "work_queue" / "claims" / f"{task_id}.json"
    payload = json.loads(claim_path.read_text(encoding="utf-8"))
    payload[field_name] = malformed_value
    claim_path.write_text(json.dumps(payload), encoding="utf-8")
    original = claim_path.read_bytes()

    with pytest.raises(WorkQueueError, match=field_name):
        list_claims(bridge_root=bridge)
    with pytest.raises(WorkQueueError):
        claim_task(
            agent="codex-1",
            task_id=f"new-against-{field_name}",
            summary="malformed active claim must block acquisition",
            mode="write",
            write_scope=["tests/independent.py"],
            bridge_root=bridge,
        )

    assert claim_path.read_bytes() == original
    assert not (
        bridge
        / "work_queue"
        / "claims"
        / f"new-against-{field_name}.json"
    ).exists()


def test_active_claim_hard_link_fails_closed_for_reads_and_acquisition(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    task_id = "hard-linked-active-claim"
    claim_task(
        agent="claude-1",
        task_id=task_id,
        summary="hard-link attack fixture",
        mode="write",
        write_scope=["tools/hardlink.py"],
        bridge_root=bridge,
    )
    claim_path = bridge / "work_queue" / "claims" / f"{task_id}.json"
    os.link(claim_path, tmp_path / "external-claim-alias.json")

    with pytest.raises(WorkQueueError, match="one filesystem link"):
        list_claims(bridge_root=bridge)
    with pytest.raises(WorkQueueError, match="one filesystem link"):
        claim_task(
            agent="codex-1",
            task_id="new-against-hard-link",
            summary="hard-linked claim must block acquisition",
            mode="write",
            write_scope=["tests/independent.py"],
            bridge_root=bridge,
        )

    assert not (
        bridge
        / "work_queue"
        / "claims"
        / "new-against-hard-link.json"
    ).exists()


def test_nonstring_stored_agent_never_authorizes_mutation(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    task_id = "task-nonstring-agent"
    claim_task(
        agent="claude-1",
        task_id=task_id,
        summary="tamper stored agent type",
        bridge_root=bridge,
    )
    claim_path = bridge / "work_queue" / "claims" / f"{task_id}.json"
    payload = json.loads(claim_path.read_text(encoding="utf-8"))
    payload["agent"] = ["claude-1"]
    claim_path.write_text(json.dumps(payload), encoding="utf-8")
    original = claim_path.read_bytes()

    listed = list_claims(bridge_root=bridge)
    assert len(listed) == 1
    assert listed[0].agent == ""

    operations = (
        lambda: claim_task(
            agent="claude-1",
            task_id=task_id,
            summary="force must reject non-string stored agent",
            bridge_root=bridge,
            force=True,
        ),
        lambda: heartbeat(
            agent="claude-1",
            task_id=task_id,
            bridge_root=bridge,
        ),
        lambda: release_task(
            agent="claude-1",
            task_id=task_id,
            bridge_root=bridge,
        ),
    )
    for operation in operations:
        with pytest.raises(WorkQueueError):
            operation()
        assert claim_path.read_bytes() == original
        assert not (bridge / "work_queue" / "done").exists()


def test_claim_refuses_write_scope_conflict_across_tasks(tmp_path: Path) -> None:
    bridge = tmp_path / ".agent-bridge"
    claim_task(
        agent="claude-1",
        task_id="task-001",
        summary="edit tools",
        mode="write",
        write_scope=["tools"],
        bridge_root=bridge,
    )
    with pytest.raises(WorkQueueError, match="write-scope conflict"):
        claim_task(
            agent="codex-1",
            task_id="task-002",
            summary="edit nested file",
            mode="write",
            write_scope=["tools/foo.py"],
            bridge_root=bridge,
        )


@pytest.mark.parametrize(
    ("stored_scope", "stored_mode"),
    [
        (["tools/foo.py, tests/bar.py"], "write"),
        ([], "write"),
        ([", ,"], "write"),
        (None, "write"),
        ([123], "write"),
        (["unrelated/path.py", 123], "write"),
        (["unrelated/path.py", ""], "write"),
        (["///"], "write"),
        (["tools/foo.py."], "write"),
        (["tools/foo.py::$DATA"], "write"),
        (["tests/bar.py"], "WRITE"),
    ],
    ids=[
        "comma-packed",
        "empty",
        "commas-only",
        "null",
        "nonstring",
        "mixed-nonstring",
        "mixed-empty",
        "slash-only",
        "trailing-dot",
        "alternate-data-stream",
        "uppercase-write-mode",
    ],
)
def test_claim_refuses_legacy_write_claim_with_unsafe_scope_shape(
    tmp_path: Path,
    stored_scope: object,
    stored_mode: str,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    claim_task(
        agent="claude-1",
        task_id="legacy-write-holder",
        summary="legacy write claim fixture",
        mode="write",
        write_scope=["placeholder"],
        bridge_root=bridge,
    )
    claim_path = (
        bridge / "work_queue" / "claims" / "legacy-write-holder.json"
    )
    payload = json.loads(claim_path.read_text(encoding="utf-8"))
    payload["write_scope"] = stored_scope
    payload["mode"] = stored_mode
    claim_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(WorkQueueError):
        claim_task(
            agent="codex-1",
            task_id="overlapping-new-write",
            summary="must fail closed against legacy claim",
            mode="write",
            write_scope=["tests/bar.py"],
            bridge_root=bridge,
        )

    assert not (
        bridge / "work_queue" / "claims" / "overlapping-new-write.json"
    ).exists()


@pytest.mark.parametrize(
    ("mode_case", "stored_mode"),
    [
        ("missing", None),
        ("null", None),
        ("nonstring", ["write"]),
        ("unknown", "read_only"),
    ],
)
def test_claim_treats_malformed_or_missing_mode_as_wildcard_write(
    tmp_path: Path,
    mode_case: str,
    stored_mode: object,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    holder_task_id = f"malformed-mode-holder-{mode_case}"
    claim_task(
        agent="claude-1",
        task_id=holder_task_id,
        summary="malformed stored mode fixture",
        mode="read-only",
        bridge_root=bridge,
    )
    claim_path = (
        bridge / "work_queue" / "claims" / f"{holder_task_id}.json"
    )
    payload = json.loads(claim_path.read_text(encoding="utf-8"))
    if mode_case == "missing":
        payload.pop("mode")
    else:
        payload["mode"] = stored_mode
    claim_path.write_text(json.dumps(payload), encoding="utf-8")
    original = claim_path.read_bytes()

    with pytest.raises(WorkQueueError):
        claim_task(
            agent="codex-1",
            task_id=f"new-write-against-{mode_case}",
            summary="unknown mode must fail closed",
            mode="write",
            write_scope=["unrelated/path.py"],
            bridge_root=bridge,
        )

    assert claim_path.read_bytes() == original
    assert not (
        bridge
        / "work_queue"
        / "claims"
        / f"new-write-against-{mode_case}.json"
    ).exists()


@pytest.mark.parametrize(
    ("fixture_name", "fixture_bytes"),
    [
        ("malformed", b"{not-json"),
        ("array", b"[]"),
        ("null", b"null"),
        ("invalid-utf8", b'{"task_id":"noise","value":"\xff"}'),
        ("utf8-bom", b'\xef\xbb\xbf{"task_id":"noise"}'),
        ("trailing-comma", b'{"task_id":"noise",}'),
        ("comment", b'{/*comment*/"task_id":"noise"}'),
        ("nan", b'{"task_id":"noise","value":NaN}'),
        ("infinity", b'{"task_id":"noise","value":Infinity}'),
        ("overflow", b'{"task_id":"noise","value":1e9999}'),
        ("leading-zero", b'{"task_id":"noise","value":01}'),
        ("leading-plus", b'{"task_id":"noise","value":+1}'),
        ("single-quote", b"{'task_id':'noise'}"),
        ("leading-dot", b'{"task_id":"noise","value":.1}'),
        ("trailing-dot", b'{"task_id":"noise","value":1.}'),
        ("negative-leading-zero", b'{"task_id":"noise","value":-01}'),
        ("double-zero", b'{"task_id":"noise","value":00}'),
        ("dot-before-exponent", b'{"task_id":"noise","value":1.e2}'),
        ("negative-leading-dot", b'{"task_id":"noise","value":-.1}'),
        (
            "invalid-escape",
            b"""{"task_id":"noise","value":"bad\\'escape"}""",
        ),
        (
            "raw-newline",
            b"""{"task_id":"noise","value":"raw
newline"}""",
        ),
        ("unquoted-key", b'{task_id:"noise"}'),
        (
            "duplicate-task-id",
            b'{"task_id":"authorized","task_id":"foreign"}',
        ),
        (
            "duplicate-owner-session",
            b'{"owner_session_id":"authorized",'
            b'"owner_session_id":"foreign"}',
        ),
        (
            "case-colliding-task-id",
            b'{"task_id":"authorized","Task_Id":"foreign"}',
        ),
        ("nbsp-whitespace", b'{\xc2\xa0"task_id":"noise"}'),
        ("vertical-tab-whitespace", b'{\x0b"task_id":"noise"}'),
    ],
)
@pytest.mark.parametrize("mode", ["read-only", "write"])
def test_claim_acquisition_rejects_untrusted_active_record(
    tmp_path: Path,
    fixture_name: str,
    fixture_bytes: bytes,
    mode: str,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    claims_dir = bridge / "work_queue" / "claims"
    claims_dir.mkdir(parents=True)
    noise_path = claims_dir / f"legacy-{fixture_name}.json"
    noise_path.write_bytes(fixture_bytes)
    before = noise_path.read_bytes()
    task_id = f"strict-acquisition-{fixture_name}-{mode}"

    with pytest.raises(WorkQueueError):
        claim_task(
            agent="claude-1",
            task_id=task_id,
            summary="must reject untrusted active queue state",
            mode=mode,
            write_scope=["tools"] if mode == "write" else (),
            bridge_root=bridge,
        )

    assert noise_path.read_bytes() == before
    assert sorted(path.name for path in claims_dir.glob("*.json")) == [
        noise_path.name
    ]
    assert not (bridge / "work_queue" / "done").exists()


@pytest.mark.parametrize(
    "raw",
    [
        b'{"task_id":"authorized","task_id":"foreign"}',
        b'{"owner_session_id":"authorized","owner_session_id":"foreign"}',
        b'{"task_id":"authorized","Task_Id":"foreign"}',
    ],
)
def test_claim_reader_reports_duplicate_or_case_colliding_fields(
    tmp_path: Path,
    raw: bytes,
) -> None:
    claim_path = tmp_path / "duplicate-fields.json"
    claim_path.write_bytes(raw)

    with pytest.raises(WorkQueueError, match="unreadable claim file") as caught:
        work_queue_module._read_claim_file_snapshot(claim_path)

    assert caught.value.__cause__ is not None
    assert "duplicate JSON object field or case collision" in str(
        caught.value.__cause__
    )


def test_claim_acquisition_wraps_claim_enumeration_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    claims_dir = bridge / "work_queue" / "claims"
    claims_dir.mkdir(parents=True)
    original_glob = Path.glob

    def fail_claim_enumeration(path: Path, pattern: str):
        if path == claims_dir and pattern == "*.json":
            raise PermissionError("injected claim enumeration failure")
        return original_glob(path, pattern)

    monkeypatch.setattr(Path, "glob", fail_claim_enumeration)

    with pytest.raises(
        WorkQueueError,
        match="cannot enumerate active claim records",
    ):
        claim_task(
            agent="claude-1",
            task_id="strict-acquisition-enumeration-error",
            summary="must wrap enumeration failure",
            bridge_root=bridge,
        )

    assert not list(claims_dir.iterdir())
    assert not (bridge / "work_queue" / "done").exists()


def test_claim_acquisition_rejects_active_claim_read_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    claims_dir = bridge / "work_queue" / "claims"
    claims_dir.mkdir(parents=True)
    noise_path = claims_dir / "legacy-unreadable.json"
    noise_path.write_text('{"task_id":"legacy-unreadable"}', encoding="utf-8")
    before = noise_path.read_bytes()
    original_snapshot = (
        work_queue_module._read_single_link_regular_file_snapshot
    )

    def fail_claim_read(path: Path, *args, **kwargs):
        if path == noise_path:
            raise WorkQueueError("unreadable claim file: injected failure")
        return original_snapshot(path, *args, **kwargs)

    monkeypatch.setattr(
        work_queue_module,
        "_read_single_link_regular_file_snapshot",
        fail_claim_read,
    )

    with pytest.raises(WorkQueueError, match="unreadable claim file"):
        claim_task(
            agent="claude-1",
            task_id="strict-acquisition-read-error",
            summary="must reject unreadable active queue state",
            bridge_root=bridge,
        )

    assert noise_path.read_bytes() == before
    assert sorted(path.name for path in claims_dir.glob("*.json")) == [
        noise_path.name
    ]
    assert not (bridge / "work_queue" / "done").exists()


def test_release_archives_to_done_dir(tmp_path: Path) -> None:
    bridge = tmp_path / ".agent-bridge"
    claim_task(
        agent="claude-1",
        task_id="task-001",
        summary="x",
        bridge_root=bridge,
    )
    record = release_task(
        agent="claude-1",
        task_id="task-001",
        release_status="done",
        release_message="all green",
        bridge_root=bridge,
    )
    assert record.release_status == "done"
    assert record.release_message == "all green"
    claims_dir = bridge / "work_queue" / "claims"
    done_dir = bridge / "work_queue" / "done"
    assert not (claims_dir / "task-001.json").exists()
    assert len(list(done_dir.glob("task-001-*.json"))) == 1


def _run_normal_claim_mutation(
    *,
    operation_name: str,
    bridge: Path,
    task_id: str,
) -> Claim | object:
    if operation_name == "force":
        return claim_task(
            agent="claude-1",
            task_id=task_id,
            summary="force mutation result",
            bridge_root=bridge,
            now_utc=datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc),
            force=True,
        )
    if operation_name == "heartbeat":
        return heartbeat(
            agent="claude-1",
            task_id=task_id,
            bridge_root=bridge,
            now_utc=datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc),
        )
    return release_task(
        agent="claude-1",
        task_id=task_id,
        release_status="done",
        bridge_root=bridge,
        now_utc=datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc),
    )


def _foreign_claim_body(claim_path: Path) -> bytes:
    payload = json.loads(claim_path.read_text(encoding="utf-8"))
    payload["agent"] = "codex-1"
    payload["summary"] = "fresh foreign replacement"
    payload["owner_session_id"] = "foreign-owner-session"
    payload["owner_token_sha256"] = hashlib.sha256(
        b"foreign-owner-token"
    ).hexdigest()
    payload["last_heartbeat_utc"] = "2026-07-31T11:59:59Z"
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


@pytest.mark.parametrize("operation_name", ["force", "heartbeat", "release"])
def test_normal_mutation_rejects_and_restores_replacement_moved_to_quarantine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation_name: str,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    task_id = f"snapshot-swap-{operation_name}"
    claim_task(
        agent="claude-1",
        task_id=task_id,
        summary="authorized original",
        bridge_root=bridge,
    )
    claim_path = bridge / "work_queue" / "claims" / f"{task_id}.json"
    foreign_body = _foreign_claim_body(claim_path)
    foreign_temp = claim_path.with_name(f".{claim_path.name}.foreign")
    foreign_temp.write_bytes(foreign_body)
    original_rename = work_queue_module._rename_file_create_new

    def replace_before_quarantine(source, destination, *args, **kwargs):
        source_path = Path(source)
        destination_path = Path(destination)
        if (
            source_path == claim_path
            and ".mutation-quarantine." in destination_path.name
        ):
            foreign_temp.replace(claim_path)
        return original_rename(source, destination, *args, **kwargs)

    monkeypatch.setattr(
        work_queue_module,
        "_rename_file_create_new",
        replace_before_quarantine,
    )

    with pytest.raises(
        WorkQueueError,
        match="quarantined active claim identity mismatch",
    ):
        _run_normal_claim_mutation(
            operation_name=operation_name,
            bridge=bridge,
            task_id=task_id,
        )

    assert claim_path.read_bytes() == foreign_body
    quarantines = list(
        claim_path.parent.glob(
            f".{claim_path.name}.mutation-quarantine.*"
        )
    )
    assert len(quarantines) == 1
    assert quarantines[0].read_bytes() == foreign_body
    assert not list((bridge / "work_queue" / "done").glob("*.json"))


@pytest.mark.parametrize("operation_name", ["force", "heartbeat", "release"])
def test_normal_mutation_quarantine_collision_preserves_both_generations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation_name: str,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    task_id = f"quarantine-collision-{operation_name}"
    claim_task(
        agent="claude-1",
        task_id=task_id,
        summary="authorized original",
        bridge_root=bridge,
    )
    claim_path = bridge / "work_queue" / "claims" / f"{task_id}.json"
    original_body = claim_path.read_bytes()
    foreign_body = b"foreign preexisting mutation quarantine\n"
    original_rename = work_queue_module._rename_file_create_new
    collision_path: Path | None = None

    def collide_with_quarantine_destination(
        source: Path,
        destination: Path,
    ) -> None:
        nonlocal collision_path
        if source == claim_path and collision_path is None:
            collision_path = destination
            destination.write_bytes(foreign_body)
        original_rename(source, destination)

    monkeypatch.setattr(
        work_queue_module,
        "_rename_file_create_new",
        collide_with_quarantine_destination,
    )

    with pytest.raises(WorkQueueError, match="source quarantine failed"):
        _run_normal_claim_mutation(
            operation_name=operation_name,
            bridge=bridge,
            task_id=task_id,
        )

    assert collision_path is not None
    assert collision_path.read_bytes() == foreign_body
    assert claim_path.read_bytes() == original_body
    assert not list((bridge / "work_queue" / "done").glob("*.json"))


@pytest.mark.parametrize("operation_name", ["force", "heartbeat", "release"])
def test_normal_mutation_never_overwrites_fresh_active_claim_during_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation_name: str,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    task_id = f"publish-race-{operation_name}"
    claim_task(
        agent="claude-1",
        task_id=task_id,
        summary="authorized original",
        bridge_root=bridge,
    )
    claim_path = bridge / "work_queue" / "claims" / f"{task_id}.json"
    foreign_body = _foreign_claim_body(claim_path)
    done_dir = bridge / "work_queue" / "done"
    original_open = Path.open
    injected = False

    def recreate_active_before_publish(
        path: Path, mode: str = "r", *args: object, **kwargs: object
    ):
        nonlocal injected
        is_update_publish = (
            operation_name != "release"
            and path == claim_path
            and mode == "xb"
        )
        is_release_publish = (
            operation_name == "release"
            and path.parent == done_dir
            and path.suffix == ".json"
            and mode == "xb"
        )
        if not injected and (is_update_publish or is_release_publish):
            claim_path.write_bytes(foreign_body)
            injected = True
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", recreate_active_before_publish)

    if operation_name == "release":
        _run_normal_claim_mutation(
            operation_name=operation_name,
            bridge=bridge,
            task_id=task_id,
        )
        assert len(list(done_dir.glob("*.json"))) == 1
    else:
        with pytest.raises(WorkQueueError, match="publish failed"):
            _run_normal_claim_mutation(
                operation_name=operation_name,
                bridge=bridge,
                task_id=task_id,
            )
        assert not list(done_dir.glob("*.json"))

    assert injected
    assert claim_path.read_bytes() == foreign_body
    if operation_name != "release":
        quarantines = list(
            claim_path.parent.glob(
                f".{claim_path.name}.mutation-quarantine.*"
            )
        )
        assert len(quarantines) == 1
        assert quarantines[0].read_bytes() != foreign_body


@pytest.mark.parametrize("operation_name", ["force", "heartbeat", "release"])
def test_normal_mutation_publish_failure_restores_exact_original(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation_name: str,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    task_id = f"publish-failure-{operation_name}"
    claim_task(
        agent="claude-1",
        task_id=task_id,
        summary="exact rollback fixture",
        bridge_root=bridge,
    )
    claim_path = bridge / "work_queue" / "claims" / f"{task_id}.json"
    original_body = claim_path.read_bytes()
    done_dir = bridge / "work_queue" / "done"
    original_open = Path.open
    injected = False

    def fail_mutation_publish(
        path: Path, mode: str = "r", *args: object, **kwargs: object
    ):
        nonlocal injected
        is_update_publish = (
            operation_name != "release"
            and path == claim_path
            and mode == "xb"
        )
        is_release_publish = (
            operation_name == "release"
            and path.parent == done_dir
            and path.suffix == ".json"
            and mode == "xb"
        )
        if not injected and (is_update_publish or is_release_publish):
            injected = True
            raise PermissionError("injected normal mutation publish failure")
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_mutation_publish)

    with pytest.raises(WorkQueueError, match="publish failed"):
        _run_normal_claim_mutation(
            operation_name=operation_name,
            bridge=bridge,
            task_id=task_id,
        )

    assert injected
    assert claim_path.read_bytes() == original_body
    assert not list(done_dir.glob("*.json"))
    assert not list(bridge.rglob("*.mutation-temp.*"))
    recoveries = list(
        claim_path.parent.glob(
            f".{claim_path.name}.mutation-quarantine.*"
        )
    )
    assert len(recoveries) == 1
    assert recoveries[0].read_bytes() == original_body


def test_release_destination_collision_restores_active_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    task_id = "release-destination-collision"
    claim_task(
        agent="claude-1",
        task_id=task_id,
        summary="authorized original",
        bridge_root=bridge,
    )
    claim_path = bridge / "work_queue" / "claims" / f"{task_id}.json"
    original_body = claim_path.read_bytes()
    done_dir = bridge / "work_queue" / "done"
    foreign_body = b'{"fresh_foreign_release":true}\n'
    original_open = Path.open
    injected_paths: list[Path] = []

    def collide_with_release_destination(
        path: Path, mode: str = "r", *args: object, **kwargs: object
    ):
        if (
            not injected_paths
            and mode == "xb"
            and path.parent == done_dir
            and path.suffix == ".json"
        ):
            path.write_bytes(foreign_body)
            injected_paths.append(path)
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", collide_with_release_destination)

    with pytest.raises(WorkQueueError, match="publish failed"):
        release_task(
            agent="claude-1",
            task_id=task_id,
            release_status="done",
            bridge_root=bridge,
            now_utc=datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc),
        )

    assert len(injected_paths) == 1
    assert injected_paths[0].read_bytes() == foreign_body
    assert claim_path.read_bytes() == original_body
    recoveries = list(
        claim_path.parent.glob(
            f".{claim_path.name}.mutation-quarantine.*"
        )
    )
    assert len(recoveries) == 1
    assert recoveries[0].read_bytes() == original_body


@pytest.mark.parametrize("operation_name", ["force", "heartbeat", "release"])
def test_normal_mutation_retains_committed_snapshot_without_unlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation_name: str,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    task_id = f"cleanup-warning-{operation_name}"
    claim_task(
        agent="claude-1",
        task_id=task_id,
        summary="cleanup warning fixture",
        bridge_root=bridge,
    )
    claim_path = bridge / "work_queue" / "claims" / f"{task_id}.json"
    original_body = claim_path.read_bytes()
    original_unlink = Path.unlink

    def reject_quarantine_unlink(path: Path, *args, **kwargs) -> None:
        if ".mutation-quarantine." in path.name:
            raise AssertionError("committed recovery must never be unlinked")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", reject_quarantine_unlink)

    result = _run_normal_claim_mutation(
        operation_name=operation_name,
        bridge=bridge,
        task_id=task_id,
    )

    if operation_name == "release":
        assert result.release_status == "done"
        assert not claim_path.exists()
        assert len(
            list((bridge / "work_queue" / "done").glob("*.json"))
        ) == 1
    else:
        assert claim_path.exists()
        assert claim_path.read_bytes() != original_body
    quarantines = list(
        claim_path.parent.glob(
            f".{claim_path.name}.mutation-quarantine.*"
        )
    )
    assert len(quarantines) == 1
    assert quarantines[0].read_bytes() == original_body


def _normal_mutation_publish_target(
    *,
    operation_name: str,
    claim_path: Path,
    done_dir: Path,
    candidate: Path,
) -> bool:
    if operation_name == "release":
        return candidate.parent == done_dir and candidate.suffix == ".json"
    return candidate == claim_path


def _assert_failed_normal_mutation_restored_source(
    *,
    bridge: Path,
    claim_path: Path,
    original_body: bytes,
) -> None:
    assert claim_path.read_bytes() == original_body
    assert not list((bridge / "work_queue" / "done").glob("*.json"))


def _assert_failed_normal_mutation_preserved_foreign_destination(
    *,
    operation_name: str,
    bridge: Path,
    claim_path: Path,
    original_body: bytes,
    foreign_body: bytes,
) -> None:
    done_dir = bridge / "work_queue" / "done"
    if operation_name == "release":
        assert claim_path.read_bytes() == original_body
        canonical_destinations = list(done_dir.glob("*.json"))
        assert len(canonical_destinations) == 1
        assert canonical_destinations[0].read_bytes() == foreign_body
    else:
        assert claim_path.read_bytes() == foreign_body
        assert not list(done_dir.glob("*.json"))
    recoveries = list(
        claim_path.parent.glob(
            f".{claim_path.name}.mutation-quarantine.*"
        )
    )
    assert len(recoveries) == 1
    assert recoveries[0].read_bytes() == original_body


def _assert_failed_normal_mutation_retained_ambiguous_publish(
    *,
    operation_name: str,
    bridge: Path,
    claim_path: Path,
    original_body: bytes,
) -> Path:
    done_dir = bridge / "work_queue" / "done"
    if operation_name == "release":
        assert claim_path.read_bytes() == original_body
        canonical_destinations = list(done_dir.glob("*.json"))
        assert len(canonical_destinations) == 1
        published_path = canonical_destinations[0]
    else:
        assert claim_path.is_file()
        assert not list(done_dir.glob("*.json"))
        published_path = claim_path
    assert published_path.read_bytes() != original_body
    recoveries = list(
        claim_path.parent.glob(
            f".{claim_path.name}.mutation-quarantine.*"
        )
    )
    assert len(recoveries) == 1
    assert recoveries[0].read_bytes() == original_body
    return published_path


@pytest.mark.parametrize("operation_name", ["force", "heartbeat", "release"])
def test_normal_mutation_detects_publish_write_before_final_verify(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation_name: str,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    task_id = f"temp-alias-write-{operation_name}"
    claim_task(
        agent="claude-1",
        task_id=task_id,
        summary="authorized original",
        bridge_root=bridge,
    )
    claim_path = bridge / "work_queue" / "claims" / f"{task_id}.json"
    original_body = claim_path.read_bytes()
    done_dir = bridge / "work_queue" / "done"
    foreign_body = b'{"foreign_alias_write":true}\n'
    original_verify = (
        work_queue_module._verify_single_link_regular_file_identity
    )
    injected = False

    def mutate_publish_before_final_verify(
        path: Path,
        *,
        expected_sha256: str,
        expected_size: int,
        label: str,
    ) -> tuple[bool, str | None]:
        nonlocal injected
        if not injected and _normal_mutation_publish_target(
            operation_name=operation_name,
            claim_path=claim_path,
            done_dir=done_dir,
            candidate=path,
        ):
            path.write_bytes(foreign_body)
            injected = True
        return original_verify(
            path,
            expected_sha256=expected_sha256,
            expected_size=expected_size,
            label=label,
        )

    monkeypatch.setattr(
        work_queue_module,
        "_verify_single_link_regular_file_identity",
        mutate_publish_before_final_verify,
    )

    with pytest.raises(WorkQueueError, match="publish failed"):
        _run_normal_claim_mutation(
            operation_name=operation_name,
            bridge=bridge,
            task_id=task_id,
        )

    assert injected
    _assert_failed_normal_mutation_preserved_foreign_destination(
        operation_name=operation_name,
        bridge=bridge,
        claim_path=claim_path,
        original_body=original_body,
        foreign_body=foreign_body,
    )


@pytest.mark.parametrize("operation_name", ["force", "heartbeat", "release"])
def test_normal_mutation_partial_publish_failure_retains_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation_name: str,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    task_id = f"temp-consume-failure-{operation_name}"
    claim_task(
        agent="claude-1",
        task_id=task_id,
        summary="authorized original",
        bridge_root=bridge,
    )
    claim_path = bridge / "work_queue" / "claims" / f"{task_id}.json"
    original_body = claim_path.read_bytes()
    done_dir = bridge / "work_queue" / "done"
    original_open = Path.open
    injected = False

    def fail_after_partial_publish(
        path: Path, mode: str = "r", *args: object, **kwargs: object
    ):
        nonlocal injected
        is_publish = mode == "xb" and _normal_mutation_publish_target(
            operation_name=operation_name,
            claim_path=claim_path,
            done_dir=done_dir,
            candidate=path,
        )
        if not injected and is_publish:
            handle = original_open(path, mode, *args, **kwargs)
            handle.write(b"{")
            handle.close()
            injected = True
            raise PermissionError("injected partial publish failure")
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_after_partial_publish)

    with pytest.raises(WorkQueueError, match="publish failed"):
        _run_normal_claim_mutation(
            operation_name=operation_name,
            bridge=bridge,
            task_id=task_id,
        )

    assert injected
    published_path = (
        _assert_failed_normal_mutation_retained_ambiguous_publish(
            operation_name=operation_name,
            bridge=bridge,
            claim_path=claim_path,
            original_body=original_body,
        )
    )
    assert published_path.read_bytes() == b"{"
    assert not list(bridge.rglob("*.mutation-temp.*"))


@pytest.mark.parametrize("operation_name", ["force", "heartbeat", "release"])
def test_normal_mutation_rejects_external_publish_hardlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation_name: str,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    task_id = f"external-temp-link-{operation_name}"
    claim_task(
        agent="claude-1",
        task_id=task_id,
        summary="authorized original",
        bridge_root=bridge,
    )
    claim_path = bridge / "work_queue" / "claims" / f"{task_id}.json"
    original_body = claim_path.read_bytes()
    done_dir = bridge / "work_queue" / "done"
    original_verify = (
        work_queue_module._verify_single_link_regular_file_identity
    )
    aliases: list[Path] = []

    def add_external_alias_before_final_verify(
        path: Path,
        *,
        expected_sha256: str,
        expected_size: int,
        label: str,
    ) -> tuple[bool, str | None]:
        if not aliases and _normal_mutation_publish_target(
            operation_name=operation_name,
            claim_path=claim_path,
            done_dir=done_dir,
            candidate=path,
        ):
            alias = path.with_name(path.name + ".external-alias")
            os.link(path, alias)
            aliases.append(alias)
        return original_verify(
            path,
            expected_sha256=expected_sha256,
            expected_size=expected_size,
            label=label,
        )

    monkeypatch.setattr(
        work_queue_module,
        "_verify_single_link_regular_file_identity",
        add_external_alias_before_final_verify,
    )

    with pytest.raises(WorkQueueError, match="surviving hard-link alias"):
        _run_normal_claim_mutation(
            operation_name=operation_name,
            bridge=bridge,
            task_id=task_id,
        )

    assert len(aliases) == 1
    published_path = (
        _assert_failed_normal_mutation_retained_ambiguous_publish(
            operation_name=operation_name,
            bridge=bridge,
            claim_path=claim_path,
            original_body=original_body,
        )
    )
    assert aliases[0].is_file()
    assert aliases[0].samefile(published_path)


@pytest.mark.parametrize("operation_name", ["force", "heartbeat", "release"])
def test_normal_mutation_rejects_external_quarantine_hardlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation_name: str,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    task_id = f"external-quarantine-link-{operation_name}"
    claim_task(
        agent="claude-1",
        task_id=task_id,
        summary="authorized original",
        bridge_root=bridge,
    )
    claim_path = bridge / "work_queue" / "claims" / f"{task_id}.json"
    original_body = claim_path.read_bytes()
    original_verify = (
        work_queue_module._verify_single_link_regular_file_identity
    )
    aliases: list[Path] = []

    def add_external_alias_before_quarantine_verify(
        path: Path,
        *,
        expected_sha256: str,
        expected_size: int,
        label: str,
    ) -> tuple[bool, str | None]:
        if (
            not aliases
            and label == "quarantined active claim"
            and ".mutation-quarantine." in path.name
        ):
            alias = path.with_name(path.name + ".external-alias")
            os.link(path, alias)
            aliases.append(alias)
        return original_verify(
            path,
            expected_sha256=expected_sha256,
            expected_size=expected_size,
            label=label,
        )

    monkeypatch.setattr(
        work_queue_module,
        "_verify_single_link_regular_file_identity",
        add_external_alias_before_quarantine_verify,
    )

    with pytest.raises(WorkQueueError, match="surviving hard-link alias"):
        _run_normal_claim_mutation(
            operation_name=operation_name,
            bridge=bridge,
            task_id=task_id,
        )

    assert len(aliases) == 1
    quarantines = [
        path
        for path in claim_path.parent.glob(
            f".{claim_path.name}.mutation-quarantine.*"
        )
        if not path.name.endswith(".external-alias")
    ]
    assert len(quarantines) == 1
    assert aliases[0].samefile(quarantines[0])
    assert claim_path.read_bytes() == original_body
    aliases[0].write_bytes(b'{"external_alias_mutation":true}\n')
    assert claim_path.read_bytes() == original_body
    assert not list((bridge / "work_queue" / "done").glob("*.json"))


@pytest.mark.parametrize("operation_name", ["force", "heartbeat", "release"])
def test_normal_mutation_rejects_destination_swap_before_final_verify(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation_name: str,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    task_id = f"final-verify-swap-{operation_name}"
    claim_task(
        agent="claude-1",
        task_id=task_id,
        summary="authorized original",
        bridge_root=bridge,
    )
    claim_path = bridge / "work_queue" / "claims" / f"{task_id}.json"
    original_body = claim_path.read_bytes()
    done_dir = bridge / "work_queue" / "done"
    foreign_body = b'{"foreign_final_verify_swap":true}\n'
    original_verify = (
        work_queue_module._verify_single_link_regular_file_identity
    )
    intended_recoveries: list[Path] = []
    injected = False

    def replace_destination_before_final_verify(
        path: Path,
        *,
        expected_sha256: str,
        expected_size: int,
        label: str,
    ) -> tuple[bool, str | None]:
        nonlocal injected
        if not injected and _normal_mutation_publish_target(
            operation_name=operation_name,
            claim_path=claim_path,
            done_dir=done_dir,
            candidate=path,
        ):
            intended = path.with_name(path.name + ".intended-recovery")
            fresh = path.with_name(path.name + ".fresh-foreign")
            os.link(path, intended)
            fresh.write_bytes(foreign_body)
            os.replace(fresh, path)
            intended_recoveries.append(intended)
            injected = True
        return original_verify(
            path,
            expected_sha256=expected_sha256,
            expected_size=expected_size,
            label=label,
        )

    monkeypatch.setattr(
        work_queue_module,
        "_verify_single_link_regular_file_identity",
        replace_destination_before_final_verify,
    )

    with pytest.raises(WorkQueueError, match="publish failed"):
        _run_normal_claim_mutation(
            operation_name=operation_name,
            bridge=bridge,
            task_id=task_id,
        )

    assert injected
    assert len(intended_recoveries) == 1
    assert intended_recoveries[0].is_file()
    _assert_failed_normal_mutation_preserved_foreign_destination(
        operation_name=operation_name,
        bridge=bridge,
        claim_path=claim_path,
        original_body=original_body,
        foreign_body=foreign_body,
    )


def test_create_new_claim_rejects_external_publish_hardlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    task_id = "create-new-external-temp-link"
    claim_path = bridge / "work_queue" / "claims" / f"{task_id}.json"
    original_verify = (
        work_queue_module._verify_single_link_regular_file_identity
    )
    aliases: list[Path] = []

    def add_external_alias_before_final_verify(
        path: Path,
        *,
        expected_sha256: str,
        expected_size: int,
        label: str,
    ) -> tuple[bool, str | None]:
        if not aliases and path == claim_path:
            alias = path.with_name(path.name + ".external-alias")
            os.link(path, alias)
            aliases.append(alias)
        return original_verify(
            path,
            expected_sha256=expected_sha256,
            expected_size=expected_size,
            label=label,
        )

    monkeypatch.setattr(
        work_queue_module,
        "_verify_single_link_regular_file_identity",
        add_external_alias_before_final_verify,
    )

    with pytest.raises(WorkQueueError, match="surviving hard-link alias"):
        claim_task(
            agent="claude-1",
            task_id=task_id,
            summary="must fail closed",
            bridge_root=bridge,
        )

    assert len(aliases) == 1
    assert aliases[0].is_file()
    assert claim_path.is_file()
    assert aliases[0].samefile(claim_path)


def test_create_new_claim_preserves_atomic_foreign_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    task_id = "create-new-atomic-foreign-replacement"
    claim_path = bridge / "work_queue" / "claims" / f"{task_id}.json"
    foreign_body = b'{"fresh_foreign_generation":true}\n'
    original_verify = (
        work_queue_module._verify_single_link_regular_file_identity
    )
    intended_recoveries: list[Path] = []

    def replace_claim_before_final_verify(
        path: Path,
        *,
        expected_sha256: str,
        expected_size: int,
        label: str,
    ) -> tuple[bool, str | None]:
        if not intended_recoveries and path == claim_path:
            intended = path.with_name(path.name + ".intended-recovery")
            fresh = path.with_name(path.name + ".fresh-foreign")
            os.link(path, intended)
            fresh.write_bytes(foreign_body)
            os.replace(fresh, path)
            intended_recoveries.append(intended)
        return original_verify(
            path,
            expected_sha256=expected_sha256,
            expected_size=expected_size,
            label=label,
        )

    monkeypatch.setattr(
        work_queue_module,
        "_verify_single_link_regular_file_identity",
        replace_claim_before_final_verify,
    )

    with pytest.raises(WorkQueueError, match="canonical destination was preserved"):
        claim_task(
            agent="claude-1",
            task_id=task_id,
            summary="must preserve fresh generation",
            bridge_root=bridge,
        )

    assert claim_path.read_bytes() == foreign_body
    assert len(intended_recoveries) == 1
    assert intended_recoveries[0].is_file()


def test_release_rejects_wrong_agent(tmp_path: Path) -> None:
    bridge = tmp_path / ".agent-bridge"
    claim_task(
        agent="claude-1",
        task_id="task-001",
        summary="x",
        bridge_root=bridge,
    )
    with pytest.raises(WorkQueueError, match="held by claude-1"):
        release_task(
            agent="codex-1",
            task_id="task-001",
            bridge_root=bridge,
        )


def test_release_refuses_missing_claim(tmp_path: Path) -> None:
    bridge = tmp_path / ".agent-bridge"
    with pytest.raises(WorkQueueError, match="no active claim"):
        release_task(
            agent="claude-1",
            task_id="nonexistent",
            bridge_root=bridge,
        )


def test_heartbeat_updates_last_seen(tmp_path: Path) -> None:
    bridge = tmp_path / ".agent-bridge"
    initial_time = datetime(2026, 5, 18, 7, 0, tzinfo=timezone.utc)
    claim_task(
        agent="claude-1",
        task_id="task-001",
        summary="x",
        bridge_root=bridge,
        now_utc=initial_time,
    )
    later_time = datetime(2026, 5, 18, 7, 30, tzinfo=timezone.utc)
    refreshed = heartbeat(
        agent="claude-1",
        task_id="task-001",
        bridge_root=bridge,
        now_utc=later_time,
    )
    assert refreshed.last_heartbeat_utc == "2026-05-18T07:30:00Z"
    assert refreshed.claimed_at_utc == "2026-05-18T07:00:00Z"


def test_heartbeat_rejects_wrong_agent(tmp_path: Path) -> None:
    bridge = tmp_path / ".agent-bridge"
    claim_task(
        agent="claude-1",
        task_id="task-001",
        summary="x",
        bridge_root=bridge,
    )
    with pytest.raises(WorkQueueError):
        heartbeat(
            agent="codex-1",
            task_id="task-001",
            bridge_root=bridge,
        )


def test_list_claims_returns_empty_when_no_claims(tmp_path: Path) -> None:
    bridge = tmp_path / ".agent-bridge"
    assert list_claims(bridge_root=bridge) == []


def test_list_claims_fails_closed_on_unreadable_claim_file(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    claims_dir = bridge / "work_queue" / "claims"
    claims_dir.mkdir(parents=True)
    unreadable = claims_dir / "foreign-held-task.json"
    unreadable.write_text("{not-json\n", encoding="utf-8")

    with pytest.raises(
        WorkQueueError,
        match=r"active claim file could not be read.*foreign-held-task\.json",
    ):
        list_claims(bridge_root=bridge)


def test_list_claims_defaults_to_agent_bridge_runtime_root_env(
    tmp_path: Path, monkeypatch
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

    claims = list_claims()

    assert [claim.task_id for claim in claims] == ["runtime-task"]


def test_list_claims_returns_all_active(tmp_path: Path) -> None:
    bridge = tmp_path / ".agent-bridge"
    for i in range(3):
        claim_task(
            agent=f"claude-{i + 1}",
            task_id=f"task-{i + 1:03d}",
            summary=f"work {i}",
            bridge_root=bridge,
        )
    claims = list_claims(bridge_root=bridge)
    assert len(claims) == 3
    agents = {c.agent for c in claims}
    assert agents == {"claude-1", "claude-2", "claude-3"}


def test_detect_stale_claims_returns_old_ones(tmp_path: Path) -> None:
    bridge = tmp_path / ".agent-bridge"
    old_time = datetime(2026, 5, 18, 0, 0, tzinfo=timezone.utc)
    claim_task(
        agent="claude-1",
        task_id="old-task",
        summary="x",
        bridge_root=bridge,
        now_utc=old_time,
    )
    fresh_time = datetime(2026, 5, 18, 9, 50, tzinfo=timezone.utc)
    claim_task(
        agent="claude-2",
        task_id="fresh-task",
        summary="y",
        bridge_root=bridge,
        now_utc=fresh_time,
    )
    # The old claim is expired; the fresh claim remains inside its stored
    # default 900-second lease.
    now = datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)
    stale = detect_stale_claims(
        bridge_root=bridge,
        now_utc=now,
        max_age_seconds=3600,  # 1h
    )
    stale_ids = {c.task_id for c in stale}
    assert "old-task" in stale_ids
    assert "fresh-task" not in stale_ids


def test_scope_overlap_detected_for_write_mode(tmp_path: Path) -> None:
    bridge = tmp_path / ".agent-bridge"
    claim_task(
        agent="claude-1",
        task_id="task-001",
        summary="edit foo",
        mode="write",
        write_scope=["tools/foo.py"],
        bridge_root=bridge,
    )
    overlapping = check_scope_overlap(
        bridge_root=bridge,
        write_scope=["tools/foo.py"],
    )
    assert len(overlapping) == 1
    assert overlapping[0].agent == "claude-1"


def test_scope_overlap_detects_parent_child_paths(tmp_path: Path) -> None:
    bridge = tmp_path / ".agent-bridge"
    claim_task(
        agent="claude-1",
        task_id="task-001",
        summary="edit tools tree",
        mode="write",
        write_scope=["tools"],
        bridge_root=bridge,
    )
    overlapping = check_scope_overlap(
        bridge_root=bridge,
        write_scope=["tools/foo.py"],
    )
    assert len(overlapping) == 1
    assert overlapping[0].task_id == "task-001"


def test_scope_overlap_detects_wildcard_scope(tmp_path: Path) -> None:
    bridge = tmp_path / ".agent-bridge"
    claim_task(
        agent="claude-1",
        task_id="task-001",
        summary="edit everything",
        mode="write",
        write_scope=["*"],
        bridge_root=bridge,
    )
    overlapping = check_scope_overlap(
        bridge_root=bridge,
        write_scope=["tools/foo.py"],
    )
    assert len(overlapping) == 1
    assert overlapping[0].agent == "claude-1"


def test_scope_overlap_empty_when_disjoint(tmp_path: Path) -> None:
    bridge = tmp_path / ".agent-bridge"
    claim_task(
        agent="claude-1",
        task_id="task-001",
        summary="edit foo",
        mode="write",
        write_scope=["tools/foo.py"],
        bridge_root=bridge,
    )
    overlapping = check_scope_overlap(
        bridge_root=bridge,
        write_scope=["tools/bar.py"],
    )
    assert overlapping == []


def test_scope_overlap_ignores_read_only_claims(tmp_path: Path) -> None:
    bridge = tmp_path / ".agent-bridge"
    claim_task(
        agent="claude-1",
        task_id="task-001",
        summary="read foo",
        mode="read-only",
        write_scope=["tools/foo.py"],  # read-only claims with scope shouldn't conflict
        bridge_root=bridge,
    )
    overlapping = check_scope_overlap(
        bridge_root=bridge,
        write_scope=["tools/foo.py"],
    )
    assert overlapping == []


def test_claim_round_trip_preserves_write_scope(tmp_path: Path) -> None:
    bridge = tmp_path / ".agent-bridge"
    claim = claim_task(
        agent="claude-1",
        task_id="task-001",
        summary="x",
        mode="write",
        write_scope=["tools/a.py", "tests/test_a.py"],
        bridge_root=bridge,
    )
    assert claim.write_scope == ("tools/a.py", "tests/test_a.py")
    listed = list_claims(bridge_root=bridge)
    assert listed[0].write_scope == ("tools/a.py", "tests/test_a.py")


@pytest.mark.parametrize(
    "task_id",
    [
        "../escape",
        "task/../escape",
        "task//escape",
        "task/",
        "task\\escape",
    ],
)
def test_claim_invalid_task_id_refused(tmp_path: Path, task_id: str) -> None:
    bridge = tmp_path / ".agent-bridge"
    with pytest.raises(WorkQueueError):
        claim_task(
            agent="claude-1",
            task_id=task_id,
            summary="x",
            bridge_root=bridge,
        )


def test_release_status_propagated_to_done_file(tmp_path: Path) -> None:
    bridge = tmp_path / ".agent-bridge"
    claim_task(
        agent="claude-1",
        task_id="task-001",
        summary="x",
        bridge_root=bridge,
    )
    record = release_task(
        agent="claude-1",
        task_id="task-001",
        release_status="blocked",
        release_message="missing dependency",
        bridge_root=bridge,
    )
    assert record.release_status == "blocked"
    assert record.release_message == "missing dependency"


def test_default_lease_seconds_applied(tmp_path: Path) -> None:
    bridge = tmp_path / ".agent-bridge"
    claim = claim_task(
        agent="claude-1",
        task_id="task-001",
        summary="x",
        bridge_root=bridge,
    )
    assert claim.lease_seconds == DEFAULT_LEASE_SECONDS
    assert claim.claim_lease_expires_utc
    expires = datetime.fromisoformat(
        claim.claim_lease_expires_utc.replace("Z", "+00:00")
    )
    claimed = datetime.fromisoformat(claim.claimed_at_utc.replace("Z", "+00:00"))
    assert expires - claimed == timedelta(seconds=DEFAULT_LEASE_SECONDS)


@pytest.mark.parametrize(
    "lease_seconds",
    [0, -1, True, 1.0, 2**31],
)
def test_claim_rejects_non_positive_int32_lease_without_write(
    tmp_path: Path,
    lease_seconds: object,
) -> None:
    bridge = tmp_path / ".agent-bridge"

    with pytest.raises(WorkQueueError, match="positive Int32"):
        claim_task(
            agent="claude-1",
            task_id="invalid-lease",
            summary="must fail before runtime write",
            lease_seconds=lease_seconds,  # type: ignore[arg-type]
            bridge_root=bridge,
        )

    assert not bridge.exists()


def test_heartbeat_rejects_over_int32_lease_without_write(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    claim_task(
        agent="claude-1",
        task_id="heartbeat-invalid-lease",
        summary="preserve this claim",
        bridge_root=bridge,
    )
    claim_path = (
        bridge
        / "work_queue"
        / "claims"
        / "heartbeat-invalid-lease.json"
    )
    before = claim_path.read_bytes()

    with pytest.raises(WorkQueueError, match="positive Int32"):
        heartbeat(
            agent="claude-1",
            task_id="heartbeat-invalid-lease",
            lease_seconds=2**31,
            bridge_root=bridge,
        )

    assert claim_path.read_bytes() == before
