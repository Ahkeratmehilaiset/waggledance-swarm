from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from waggledance.core.work_queue import (
    DEFAULT_LEASE_SECONDS,
    Claim,
    WorkQueueError,
    archive_stale_claims,
    check_scope_overlap,
    claim_task,
    current_owner_identity,
    detect_stale_claims,
    heartbeat,
    list_claims,
    release_task,
)

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
    """Every test runs as one known B7 owner, bound to no agent label.

    A lane shell carries its own owner identity and label; without this the
    suite's result would depend on who runs it.
    """
    for name in _IDENTITY_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AGENT_BRIDGE_OWNER_SESSION_ID", "test-session")
    monkeypatch.setenv("AGENT_BRIDGE_OWNER_TOKEN", "test-token")


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


def test_claim_refreshable_by_same_agent(tmp_path: Path) -> None:
    bridge = tmp_path / ".agent-bridge"
    claim_task(
        agent="claude-1",
        task_id="task-001",
        summary="first",
        bridge_root=bridge,
    )
    refreshed = claim_task(
        agent="claude-1",
        task_id="task-001",
        summary="refresh",
        bridge_root=bridge,
    )
    assert refreshed.summary == "refresh"


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
    fresh_time = datetime(2026, 5, 18, 9, 30, tzinfo=timezone.utc)
    claim_task(
        agent="claude-2",
        task_id="fresh-task",
        summary="y",
        bridge_root=bridge,
        now_utc=fresh_time,
    )
    # Check against now=10h after old, well past 1h stale window
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


# -- B7 owner parity (P2) -------------------------------------------------
# Each refusal has a same-owner or same-label success twin, so a refusal
# cannot pass just because the operation is broken outright.

TEST_TOKEN_SHA = hashlib.sha256(b"test-token").hexdigest()


def _as_session(monkeypatch: pytest.MonkeyPatch, session: str, token: str) -> None:
    monkeypatch.setenv("AGENT_BRIDGE_OWNER_SESSION_ID", session)
    monkeypatch.setenv("AGENT_BRIDGE_OWNER_TOKEN", token)


def _without_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("AGENT_BRIDGE_OWNER_SESSION_ID", "AGENT_BRIDGE_OWNER_TOKEN", "AGENT_BRIDGE_RUN_ID"):
        monkeypatch.delenv(name, raising=False)


def _claims_dir(bridge: Path) -> Path:
    path = bridge / "work_queue" / "claims"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_raw_claim(path: Path, **fields: object) -> None:
    now = "2026-09-28T00:00:00Z"
    payload: dict[str, object] = {
        "agent": "codex",
        "summary": "raw claim",
        "mode": "read-only",
        "write_scope": [],
        "run_id": "",
        "claimed_at_utc": now,
        "last_heartbeat_utc": now,
        "lease_seconds": 900,
    }
    payload.update(fields)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _task_ids(claims: Path) -> list[str]:
    return sorted(json.loads(p.read_text(encoding="utf-8"))["task_id"] for p in claims.glob("*.json"))


def test_claim_records_owner_identity_hash_not_token(tmp_path: Path) -> None:
    bridge = tmp_path / ".agent-bridge"
    claim_task(agent="codex", task_id="own-fields", summary="x", bridge_root=bridge)
    data = json.loads((_claims_dir(bridge) / "own-fields.json").read_text(encoding="utf-8"))
    assert data["owner_session_id"] == "test-session"
    assert data["owner_token_sha256"] == TEST_TOKEN_SHA
    assert "owner_identity" not in data
    assert "test-token" not in json.dumps(data)


def test_claim_without_identity_is_marked_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _without_identity(monkeypatch)
    bridge = tmp_path / ".agent-bridge"
    claim_task(agent="codex", task_id="no-id", summary="x", bridge_root=bridge)
    data = json.loads((_claims_dir(bridge) / "no-id.json").read_text(encoding="utf-8"))
    assert data["owner_identity"] == "none"
    assert "owner_session_id" not in data and "owner_token_sha256" not in data


def test_owner_identity_precedence_matches_powershell() -> None:
    token = {"AGENT_BRIDGE_OWNER_TOKEN": "t"}
    sha = hashlib.sha256(b"t").hexdigest()
    own = current_owner_identity({**token, "AGENT_BRIDGE_OWNER_SESSION_ID": "own"})
    run = current_owner_identity({**token, "AGENT_BRIDGE_RUN_ID": "run"})
    same = current_owner_identity({**token, "AGENT_BRIDGE_OWNER_SESSION_ID": "s", "AGENT_BRIDGE_RUN_ID": "s"})
    assert own is not None and own.owner_session_id == "own"
    assert run is not None and run.owner_session_id == "run"
    assert same is not None and same.owner_token_sha256 == sha
    assert current_owner_identity({**token, "AGENT_BRIDGE_OWNER_SESSION_ID": "a", "AGENT_BRIDGE_RUN_ID": "b"}) is None
    assert current_owner_identity({"AGENT_BRIDGE_OWNER_SESSION_ID": "own"}) is None


def test_same_label_other_session_cannot_refresh_or_force_take_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bridge = tmp_path / ".agent-bridge"
    claim_task(agent="codex", task_id="o1", summary="owner A", bridge_root=bridge)
    _as_session(monkeypatch, "session-b", "token-b")
    for force in (False, True):
        with pytest.raises(WorkQueueError, match="held by another session"):
            claim_task(agent="codex", task_id="o1", summary="takeover", bridge_root=bridge, force=force)
    data = json.loads((_claims_dir(bridge) / "o1.json").read_text(encoding="utf-8"))
    assert data["summary"] == "owner A" and data["owner_token_sha256"] == TEST_TOKEN_SHA

    _as_session(monkeypatch, "test-session", "test-token")
    refreshed = claim_task(agent="codex", task_id="o1", summary="refresh by A", bridge_root=bridge, force=True)
    assert refreshed.summary == "refresh by A"
    assert refreshed.owner_token_sha256 == TEST_TOKEN_SHA


def test_bound_session_cannot_act_under_another_label(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bridge = tmp_path / ".agent-bridge"
    monkeypatch.setenv("AGENT_BRIDGE_AGENT", "codex-2")
    claim_task(agent="codex-2", task_id="o3", summary="own label", bridge_root=bridge)

    monkeypatch.setenv("AGENT_BRIDGE_AGENT", "codex-lead-1")
    with pytest.raises(WorkQueueError, match="identity_mismatch"):
        claim_task(agent="codex-2", task_id="o3-other", summary="x", bridge_root=bridge)
    with pytest.raises(WorkQueueError, match="identity_mismatch"):
        heartbeat(agent="codex-2", task_id="o3", bridge_root=bridge)
    with pytest.raises(WorkQueueError, match="identity_mismatch"):
        release_task(agent="codex-2", task_id="o3", bridge_root=bridge)
    assert _task_ids(_claims_dir(bridge)) == ["o3"]

    monkeypatch.setenv("AGENT_BRIDGE_AGENT", "codex-2")
    heartbeat(agent="codex-2", task_id="o3", bridge_root=bridge)
    release_task(agent="codex-2", task_id="o3", bridge_root=bridge)
    assert _task_ids(_claims_dir(bridge)) == []


def test_reserved_labels_need_a_bound_session_and_never_take_over(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bridge = tmp_path / ".agent-bridge"
    claim_task(agent="codex", task_id="o2", summary="owner A", bridge_root=bridge)
    with pytest.raises(WorkQueueError, match="reserved agent 'operator'"):
        claim_task(agent="operator", task_id="o2-op", summary="x", bridge_root=bridge)

    monkeypatch.setenv("AGENT_BRIDGE_AGENT", "operator")
    claim_task(agent="operator", task_id="o2-op", summary="bound operator", bridge_root=bridge)
    with pytest.raises(WorkQueueError, match="force claim across agents refused"):
        claim_task(agent="operator", task_id="o2", summary="takeover", bridge_root=bridge, force=True)
    assert json.loads((_claims_dir(bridge) / "o2.json").read_text(encoding="utf-8"))["agent"] == "codex"

    monkeypatch.setenv("AGENT_BRIDGE_AGENT", "system")
    with pytest.raises(WorkQueueError, match="no public bridge authority"):
        claim_task(agent="system", task_id="o2-sys", summary="x", bridge_root=bridge)


def test_heartbeat_extends_only_the_owning_sessions_claim_and_keeps_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bridge = tmp_path / ".agent-bridge"
    path = _claims_dir(bridge) / "o5.json"
    _write_raw_claim(
        path,
        task_id="o5",
        owner_session_id="test-session",
        owner_token_sha256=TEST_TOKEN_SHA,
        resources=["repo:tests/x"],
        git_branch="feature/x",
        owner_pid=1234,
    )
    before = path.read_text(encoding="utf-8")

    _as_session(monkeypatch, "session-b", "token-b")
    with pytest.raises(WorkQueueError, match="only the owning session"):
        heartbeat(agent="codex", task_id="o5", bridge_root=bridge)
    _without_identity(monkeypatch)
    with pytest.raises(WorkQueueError, match="only the owning session"):
        heartbeat(agent="codex", task_id="o5", bridge_root=bridge)
    assert path.read_text(encoding="utf-8") == before

    _as_session(monkeypatch, "test-session", "test-token")
    later = datetime(2026, 9, 28, 1, 0, tzinfo=timezone.utc)
    refreshed = heartbeat(agent="codex", task_id="o5", bridge_root=bridge, now_utc=later)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert refreshed.last_heartbeat_utc == "2026-09-28T01:00:00Z"
    assert data["last_heartbeat_utc"] == "2026-09-28T01:00:00Z"
    assert data["owner_session_id"] == "test-session"
    assert data["owner_token_sha256"] == TEST_TOKEN_SHA
    assert data["resources"] == ["repo:tests/x"]
    assert data["git_branch"] == "feature/x"
    assert data["owner_pid"] == 1234


def test_identityless_claim_is_never_extended(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _without_identity(monkeypatch)
    bridge = tmp_path / ".agent-bridge"
    claim_task(agent="codex", task_id="no-id-beat", summary="x", bridge_root=bridge)
    with pytest.raises(WorkQueueError, match="only the owning session"):
        heartbeat(agent="codex", task_id="no-id-beat", bridge_root=bridge)


def test_owned_claim_is_released_only_by_its_owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bridge = tmp_path / ".agent-bridge"
    claim_task(agent="codex", task_id="o5-rel", summary="owner A", bridge_root=bridge)
    _as_session(monkeypatch, "session-b", "token-b")
    with pytest.raises(WorkQueueError, match="owned by another session"):
        release_task(agent="codex", task_id="o5-rel", bridge_root=bridge, allow_legacy_unowned_claim=True)
    _without_identity(monkeypatch)
    with pytest.raises(WorkQueueError, match="owned by another session"):
        release_task(agent="codex", task_id="o5-rel", bridge_root=bridge, allow_legacy_unowned_claim=True)
    assert _task_ids(_claims_dir(bridge)) == ["o5-rel"]

    _as_session(monkeypatch, "test-session", "test-token")
    release_task(agent="codex", task_id="o5-rel", bridge_root=bridge)
    assert _task_ids(_claims_dir(bridge)) == []


def test_none_marked_claim_label_release_only_for_identityless_caller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bridge = tmp_path / ".agent-bridge"
    _without_identity(monkeypatch)
    claim_task(agent="codex", task_id="none-a", summary="x", bridge_root=bridge)
    claim_task(agent="codex", task_id="none-b", summary="x", bridge_root=bridge)

    _as_session(monkeypatch, "test-session", "test-token")
    with pytest.raises(WorkQueueError, match="no owner identity"):
        release_task(agent="codex", task_id="none-a", bridge_root=bridge)
    release_task(agent="codex", task_id="none-b", bridge_root=bridge, allow_legacy_unowned_claim=True)

    _without_identity(monkeypatch)
    release_task(agent="codex", task_id="none-a", bridge_root=bridge)
    assert _task_ids(_claims_dir(bridge)) == []


def test_unmarked_pre_b7_claim_needs_explicit_adoption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _without_identity(monkeypatch)
    bridge = tmp_path / ".agent-bridge"
    path = _claims_dir(bridge) / "legacy.json"
    _write_raw_claim(path, task_id="legacy")
    with pytest.raises(WorkQueueError, match="pre-B7 claim"):
        release_task(agent="codex", task_id="legacy", bridge_root=bridge)
    assert path.exists()
    release_task(agent="codex", task_id="legacy", bridge_root=bridge, allow_legacy_unowned_claim=True)
    assert not path.exists()


def test_colliding_task_id_never_touches_another_tasks_claim(tmp_path: Path) -> None:
    bridge = tmp_path / ".agent-bridge"
    ps_path = _claims_dir(bridge) / "own_9.json"  # the PowerShell name for task own/9
    _write_raw_claim(ps_path, task_id="own/9", owner_session_id="test-session", owner_token_sha256=TEST_TOKEN_SHA)
    before = ps_path.read_text(encoding="utf-8")

    with pytest.raises(WorkQueueError, match="no active claim"):
        release_task(agent="codex", task_id="own_9", bridge_root=bridge)
    with pytest.raises(WorkQueueError, match="no active claim"):
        heartbeat(agent="codex", task_id="own_9", bridge_root=bridge)
    assert ps_path.read_text(encoding="utf-8") == before

    for force in (False, True):
        claim_task(agent="codex", task_id="own_9", summary="underscore task", bridge_root=bridge, force=force)
    assert _task_ids(_claims_dir(bridge)) == ["own/9", "own_9"]
    assert ps_path.read_text(encoding="utf-8") == before

    release_task(agent="codex", task_id="own/9", bridge_root=bridge)
    release_task(agent="codex", task_id="own_9", bridge_root=bridge)
    assert _task_ids(_claims_dir(bridge)) == []


def _write_session_beat(bridge: Path, session: str, token_sha: str, beat_utc: str, *, raw: str | None = None) -> Path:
    digest = hashlib.sha256(f"{session}\n{token_sha}".encode("utf-8")).hexdigest()
    path = bridge / "work_queue" / "heartbeats" / f"{digest}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if raw is not None:
        path.write_text(raw, encoding="utf-8")
    else:
        path.write_text(
            json.dumps({
                "owner_session_id": session,
                "owner_token_sha256": token_sha,
                "last_beat_utc": beat_utc,
                "ttl_seconds": 180,
            }),
            encoding="utf-8",
        )
    return path


SWEEP_NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)


def _owned_expired_claim(bridge: Path, task: str) -> Path:
    path = _claims_dir(bridge) / f"{task}.json"
    old = "2026-09-28T10:00:00Z"
    _write_raw_claim(
        path,
        task_id=task,
        claimed_at_utc=old,
        last_heartbeat_utc=old,
        lease_seconds=60,
        claim_lease_expires_utc="2026-09-28T10:01:00Z",
        owner_session_id="test-session",
        owner_token_sha256=TEST_TOKEN_SHA,
    )
    return path


@pytest.mark.parametrize(
    ("beat", "kept"),
    [
        ("live", True),
        ("unreadable", True),
        ("other-owner", True),
        ("stale", False),
        ("absent", False),
        ("future", False),
    ],
)
def test_python_sweeper_keeps_owned_claims_it_cannot_prove_abandoned(
    tmp_path: Path, beat: str, kept: bool
) -> None:
    bridge = tmp_path / ".agent-bridge"
    path = _owned_expired_claim(bridge, "sweep-owned")
    if beat == "live":
        _write_session_beat(bridge, "test-session", TEST_TOKEN_SHA, "2026-09-28T11:59:00Z")
    elif beat == "unreadable":
        _write_session_beat(bridge, "test-session", TEST_TOKEN_SHA, "", raw="{not json")
    elif beat == "other-owner":
        beat_path = _write_session_beat(bridge, "test-session", TEST_TOKEN_SHA, "2026-09-28T11:00:00Z")
        beat_path.write_text(
            json.dumps({
                "owner_session_id": "someone-else",
                "owner_token_sha256": TEST_TOKEN_SHA,
                "last_beat_utc": "2026-09-28T11:00:00Z",
            }),
            encoding="utf-8",
        )
    elif beat == "stale":
        _write_session_beat(bridge, "test-session", TEST_TOKEN_SHA, "2026-09-28T11:00:00Z")
    elif beat == "future":
        _write_session_beat(bridge, "test-session", TEST_TOKEN_SHA, "2026-09-28T13:00:00Z")

    archived = archive_stale_claims(bridge_root=bridge, now_utc=SWEEP_NOW, max_age_seconds=60, apply=True)
    assert path.exists() is kept
    assert len(archived) == (0 if kept else 1)


def test_python_sweeper_keeps_owned_claim_whose_lease_has_not_expired(tmp_path: Path) -> None:
    bridge = tmp_path / ".agent-bridge"
    path = _claims_dir(bridge) / "sweep-lease.json"
    _write_raw_claim(
        path,
        task_id="sweep-lease",
        claimed_at_utc="2026-09-28T10:00:00Z",
        last_heartbeat_utc="2026-09-28T10:00:00Z",
        lease_seconds=60,
        claim_lease_expires_utc="2026-09-28T13:00:00Z",
        owner_session_id="test-session",
        owner_token_sha256=TEST_TOKEN_SHA,
    )
    assert archive_stale_claims(bridge_root=bridge, now_utc=SWEEP_NOW, max_age_seconds=60, apply=True) == []
    assert path.exists()


def test_python_sweeper_removes_only_the_exact_stale_claim_file(tmp_path: Path) -> None:
    bridge = tmp_path / ".agent-bridge"
    stale = _claims_dir(bridge) / "own_7.json"  # the PowerShell name for task own/7
    _write_raw_claim(
        stale,
        task_id="own/7",
        claimed_at_utc="2026-09-28T10:00:00Z",
        last_heartbeat_utc="2026-09-28T10:00:00Z",
        owner_identity="none",
    )
    claim_task(agent="codex", task_id="own_7", summary="fresh", bridge_root=bridge, now_utc=SWEEP_NOW)
    archived = archive_stale_claims(bridge_root=bridge, now_utc=SWEEP_NOW, max_age_seconds=60, apply=True)
    assert [a.claim.task_id for a in archived] == ["own/7"]
    assert not stale.exists()
    assert _task_ids(_claims_dir(bridge)) == ["own_7"]


def test_python_sweeper_never_deletes_a_successor_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import waggledance.core.work_queue as wq

    bridge = tmp_path / ".agent-bridge"
    path = _claims_dir(bridge) / "succ.json"
    _write_raw_claim(
        path,
        task_id="succ",
        claimed_at_utc="2026-09-28T10:00:00Z",
        last_heartbeat_utc="2026-09-28T10:00:00Z",
        owner_identity="none",
    )
    snapshot = wq._list_claim_entries(_claims_dir(bridge))
    _write_raw_claim(path, task_id="succ", summary="successor", owner_identity="none")
    monkeypatch.setattr(wq, "_list_claim_entries", lambda claims_dir: snapshot)

    assert archive_stale_claims(bridge_root=bridge, now_utc=SWEEP_NOW, max_age_seconds=60, apply=True) == []
    assert json.loads(path.read_text(encoding="utf-8"))["summary"] == "successor"


def _powershell() -> str | None:
    return shutil.which("pwsh") or shutil.which("powershell")


def _python_find(claims: Path, task_id: str) -> Path | None:
    import waggledance.core.work_queue as wq

    found = wq._find_claim(claims, task_id)
    return None if found is None else found[0]


@pytest.mark.skipif(_powershell() is None, reason="PowerShell is not available")
def test_heartbeat_path_and_claim_lookup_match_powershell(tmp_path: Path) -> None:
    bridge = tmp_path / ".agent-bridge"
    claims = _claims_dir(bridge)
    _write_raw_claim(claims / "own_9.json", task_id="own/9")
    helper = Path(__file__).resolve().parents[2] / ".agent-bridge" / "bin" / "ClaimLeaseHeartbeat.ps1"
    script = (
        f". '{helper}'\n"
        f"$hb = Get-BridgeSessionHeartbeatPath -Root '{bridge}' -SessionId 'wd-run' -TokenSha256 '{TEST_TOKEN_SHA}'\n"
        f"$hit = Find-BridgeClaimFile -ClaimsDir '{claims}' -TaskId 'own/9'\n"
        f"$miss = Find-BridgeClaimFile -ClaimsDir '{claims}' -TaskId 'own_9'\n"
        "[pscustomobject]@{ hb = $hb; hit = $hit; miss = [string]$miss } | ConvertTo-Json -Compress\n"
    )
    completed = subprocess.run(
        [_powershell(), "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    expected = hashlib.sha256(f"wd-run\n{TEST_TOKEN_SHA}".encode("utf-8")).hexdigest()
    assert Path(result["hb"]) == bridge / "work_queue" / "heartbeats" / f"{expected}.json"
    assert Path(result["hit"]) == claims / "own_9.json"
    assert result["miss"] == ""
    assert _python_find(claims, "own/9") == claims / "own_9.json"
    assert _python_find(claims, "own_9") is None
