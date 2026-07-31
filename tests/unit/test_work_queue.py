from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

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

    with pytest.raises(WorkQueueError, match="write claims require"):
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
def test_malformed_stored_owner_field_is_legacy_tokenless(
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

    with pytest.raises(
        WorkQueueError,
        match="claim_owner_legacy_tokenless",
    ):
        heartbeat(
            agent="claude-1",
            task_id=task_id,
            bridge_root=bridge,
        )

    assert claim_path.read_bytes() == original


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
        (["tests/bar.py"], "WRITE"),
    ],
    ids=[
        "comma-packed",
        "empty",
        "commas-only",
        "null",
        "nonstring",
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

    with pytest.raises(WorkQueueError, match="write-scope conflict"):
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

    with pytest.raises(WorkQueueError, match="write-scope conflict"):
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


def test_release_rolls_back_archive_when_active_claim_delete_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    task_id = "release-delete-denied"
    claim_task(
        agent="claude-1",
        task_id=task_id,
        summary="release rollback fixture",
        bridge_root=bridge,
    )
    claim_path = bridge / "work_queue" / "claims" / f"{task_id}.json"
    before = claim_path.read_bytes()
    original_unlink = Path.unlink

    def deny_claim_unlink(path: Path, *args, **kwargs) -> None:
        if path == claim_path:
            raise PermissionError("injected active claim delete failure")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", deny_claim_unlink)

    with pytest.raises(
        PermissionError,
        match="injected active claim delete failure",
    ):
        release_task(
            agent="claude-1",
            task_id=task_id,
            bridge_root=bridge,
        )

    assert claim_path.read_bytes() == before
    assert not list((bridge / "work_queue" / "done").glob("*.json"))


def test_release_reports_source_and_rollback_double_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    task_id = "release-double-delete-denied"
    claim_task(
        agent="claude-1",
        task_id=task_id,
        summary="release double rollback failure fixture",
        bridge_root=bridge,
    )
    claim_path = bridge / "work_queue" / "claims" / f"{task_id}.json"
    done_dir = bridge / "work_queue" / "done"
    before = claim_path.read_bytes()
    original_unlink = Path.unlink

    def deny_source_and_archive_unlink(
        path: Path,
        *args,
        **kwargs,
    ) -> None:
        if path == claim_path:
            raise PermissionError("injected active claim delete failure")
        if path.parent == done_dir and ".tmp." not in path.name:
            raise PermissionError("injected release archive rollback failure")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", deny_source_and_archive_unlink)

    with pytest.raises(
        WorkQueueError,
        match=(
            "release active-claim delete failed and archive rollback failed"
        ),
    ) as exc_info:
        release_task(
            agent="claude-1",
            task_id=task_id,
            bridge_root=bridge,
        )

    assert "injected active claim delete failure" in str(exc_info.value)
    assert "injected release archive rollback failure" in str(exc_info.value)
    assert claim_path.read_bytes() == before
    assert len(list(done_dir.glob("*.json"))) == 1
    assert not list(done_dir.glob("*.tmp.*"))


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
