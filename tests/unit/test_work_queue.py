from __future__ import annotations

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
    archive_stale_claims,
    check_scope_overlap,
    claim_task,
    detect_stale_claims,
    heartbeat,
    list_claims,
    release_task,
)


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


@pytest.mark.skipif(os.name != "nt", reason="uses the Windows WorkQueueV1 mutex")
def test_claim_fails_closed_when_mutex_construction_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    monkeypatch.setenv(
        "AGENT_BRIDGE_TEST_WORK_QUEUE_MUTEX_CONSTRUCTION_FAILURE",
        "1",
    )

    with pytest.raises(WorkQueueError, match="mutex construction failure"):
        claim_task(
            agent="claude-1",
            task_id="test-task-mutex-failure",
            summary="must not write unlocked",
            bridge_root=bridge,
        )

    assert not (bridge / "work_queue" / "claims").exists()


@pytest.mark.skipif(os.name != "nt", reason="uses the Windows WorkQueueV1 mutex")
@pytest.mark.parametrize(
    "operation",
    ["claim", "force", "release", "heartbeat", "stale-sweep"],
)
def test_every_python_mutator_refuses_an_unlocked_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    initial = datetime(2026, 5, 18, 7, 0, tzinfo=timezone.utc)
    claim_task(
        agent="claude-1",
        task_id="locked-existing",
        summary="existing generation",
        mode="write",
        write_scope=["src/existing"],
        bridge_root=bridge,
        now_utc=initial,
    )
    before = {
        path.relative_to(bridge): path.read_bytes()
        for path in bridge.rglob("*")
        if path.is_file()
    }
    monkeypatch.setenv(
        "AGENT_BRIDGE_TEST_WORK_QUEUE_MUTEX_CONSTRUCTION_FAILURE",
        "1",
    )

    with pytest.raises(WorkQueueError, match="mutex construction failure"):
        if operation == "claim":
            claim_task(
                agent="claude-1",
                task_id="locked-new",
                summary="must not claim unlocked",
                bridge_root=bridge,
            )
        elif operation == "force":
            claim_task(
                agent="claude-1",
                task_id="locked-existing",
                summary="must not refresh unlocked",
                mode="write",
                write_scope=["src/existing"],
                bridge_root=bridge,
                force=True,
            )
        elif operation == "release":
            release_task(
                agent="claude-1",
                task_id="locked-existing",
                bridge_root=bridge,
            )
        elif operation == "heartbeat":
            heartbeat(
                agent="claude-1",
                task_id="locked-existing",
                bridge_root=bridge,
            )
        else:
            archive_stale_claims(
                bridge_root=bridge,
                now_utc=initial + timedelta(hours=1),
                max_age_seconds=1,
                apply=True,
            )

    after = {
        path.relative_to(bridge): path.read_bytes()
        for path in bridge.rglob("*")
        if path.is_file()
    }
    assert after == before


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


def test_claim_does_not_overwrite_colliding_legacy_claim_file(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    slash_task_id = "codex-tools-1/collision"
    underscore_task_id = "codex-tools-1_collision"

    claim_task(
        agent="codex-tools-1",
        task_id=slash_task_id,
        summary="slash namespace",
        bridge_root=bridge,
    )
    claims_dir = bridge / "work_queue" / "claims"
    preferred = next(claims_dir.glob("*.json"))
    legacy = claims_dir / "codex-tools-1_collision.json"
    preferred.rename(legacy)

    with pytest.raises(WorkQueueError, match="claim path collision"):
        claim_task(
            agent="codex-tools-1",
            task_id=underscore_task_id,
            summary="must not replace slash namespace",
            bridge_root=bridge,
            force=True,
        )

    claims = list_claims(bridge_root=bridge)
    assert len(claims) == 1
    assert claims[0].task_id == slash_task_id
    assert claims[0].summary == "slash namespace"


def test_release_fails_closed_on_duplicate_exact_task_claim_files(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    task_id = "codex-tools-1/duplicate-exact-task"

    claim_task(
        agent="codex-tools-1",
        task_id=task_id,
        summary="preferred claim",
        bridge_root=bridge,
    )
    claims_dir = bridge / "work_queue" / "claims"
    preferred = next(claims_dir.glob("*.json"))
    duplicate = claims_dir / "codex-tools-1_duplicate-exact-task.json"
    duplicate.write_bytes(preferred.read_bytes())

    with pytest.raises(WorkQueueError, match="multiple active claims"):
        release_task(
            agent="codex-tools-1",
            task_id=task_id,
            release_status="done",
            bridge_root=bridge,
        )

    assert len(list(claims_dir.glob("*.json"))) == 2
    assert list((bridge / "work_queue" / "done").glob("*.json")) == []


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


def test_heartbeat_legacy_lookup_fails_closed_on_malformed_claim_files(
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

    before = legacy_path.read_bytes()
    with pytest.raises(WorkQueueError, match="unreadable claim file"):
        heartbeat(
            agent="codex-tools-1",
            task_id=task_id,
            bridge_root=bridge,
        )

    assert legacy_path.exists()
    assert legacy_path.read_bytes() == before


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


def test_release_move_failure_leaves_original_active_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    claim_task(
        agent="claude-1",
        task_id="task-move-failure",
        summary="original",
        bridge_root=bridge,
    )
    claim_path = bridge / "work_queue" / "claims" / "task-move-failure.json"
    before = claim_path.read_bytes()
    original_move = work_queue_module._move_no_replace

    def fail_move(_source: Path, _target: Path) -> None:
        raise WorkQueueError("simulated terminal target collision")

    monkeypatch.setattr(work_queue_module, "_move_no_replace", fail_move)
    with pytest.raises(WorkQueueError, match="target collision"):
        release_task(
            agent="claude-1",
            task_id="task-move-failure",
            bridge_root=bridge,
        )

    assert claim_path.read_bytes() == before
    assert list((bridge / "work_queue" / "done").glob("*.json")) == []
    monkeypatch.setattr(work_queue_module, "_move_no_replace", original_move)
    refreshed = heartbeat(
        agent="claude-1",
        task_id="task-move-failure",
        bridge_root=bridge,
    )
    assert refreshed.task_id == "task-move-failure"


def test_release_metadata_failure_reports_committed_terminal_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    claim_task(
        agent="claude-1",
        task_id="task-metadata-failure",
        summary="terminal transition wins",
        bridge_root=bridge,
    )

    def fail_metadata_write(*_args, **_kwargs) -> None:
        raise OSError("simulated terminal metadata failure")

    monkeypatch.setattr(
        work_queue_module,
        "_write_json_file",
        fail_metadata_write,
    )
    with pytest.warns(RuntimeWarning, match="release committed"):
        record = release_task(
            agent="claude-1",
            task_id="task-metadata-failure",
            release_status="done",
            bridge_root=bridge,
        )

    assert record.release_status == "done"
    assert list((bridge / "work_queue" / "claims").glob("*.json")) == []
    terminal = list((bridge / "work_queue" / "done").glob("*.json"))
    assert len(terminal) == 1
    payload = json.loads(terminal[0].read_text(encoding="utf-8"))
    assert payload["task_id"] == "task-metadata-failure"
    assert "release_status" not in payload


def test_terminal_markers_in_active_claim_fail_closed(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    claim_task(
        agent="claude-1",
        task_id="task-terminal-active",
        summary="must not resurrect",
        bridge_root=bridge,
    )
    claim_path = bridge / "work_queue" / "claims" / "task-terminal-active.json"
    payload = json.loads(claim_path.read_text(encoding="utf-8"))
    payload["released_at_utc"] = "2026-05-18T08:00:00Z"
    payload["release_status"] = "done"
    claim_path.write_text(json.dumps(payload), encoding="utf-8")
    before = claim_path.read_bytes()

    with pytest.raises(WorkQueueError, match="terminal markers"):
        heartbeat(
            agent="claude-1",
            task_id="task-terminal-active",
            bridge_root=bridge,
        )

    assert claim_path.read_bytes() == before


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
        lease_seconds=3600,
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


def test_detect_stale_legacy_claim_honors_environment_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    claimed_at = datetime(2026, 5, 18, 9, 0, tzinfo=timezone.utc)
    now = claimed_at + timedelta(seconds=500)
    claim_task(
        agent="claude-1",
        task_id="legacy-stale-env",
        summary="legacy fallback",
        bridge_root=bridge,
        now_utc=claimed_at,
    )
    claim_path = bridge / "work_queue" / "claims" / "legacy-stale-env.json"
    payload = json.loads(claim_path.read_text(encoding="utf-8"))
    payload.pop("lease_seconds")
    payload.pop("claim_lease_expires_utc")
    claim_path.write_text(json.dumps(payload), encoding="utf-8")

    monkeypatch.setenv("AGENT_BRIDGE_STALE_LEASE_SECONDS", "1000")
    assert detect_stale_claims(bridge_root=bridge, now_utc=now) == []

    monkeypatch.setenv("AGENT_BRIDGE_STALE_LEASE_SECONDS", "100")
    assert [
        claim.task_id
        for claim in detect_stale_claims(bridge_root=bridge, now_utc=now)
    ] == ["legacy-stale-env"]


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


def test_default_lease_seconds_applied(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    monkeypatch.delenv("AGENT_BRIDGE_STALE_LEASE_SECONDS", raising=False)
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


def test_claim_uses_positive_environment_lease_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    monkeypatch.setenv("AGENT_BRIDGE_STALE_LEASE_SECONDS", "1000")

    claim = claim_task(
        agent="claude-1",
        task_id="task-env-lease",
        summary="environment lease",
        bridge_root=bridge,
    )

    assert claim.lease_seconds == 1000


@pytest.mark.parametrize("configured", ["", "0", "-5", "not-an-int"])
def test_claim_ignores_invalid_environment_lease_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    configured: str,
) -> None:
    bridge = tmp_path / f".agent-bridge-{configured or 'empty'}"
    monkeypatch.setenv("AGENT_BRIDGE_STALE_LEASE_SECONDS", configured)

    claim = claim_task(
        agent="claude-1",
        task_id="task-invalid-env-lease",
        summary="fallback lease",
        bridge_root=bridge,
    )

    assert claim.lease_seconds == DEFAULT_LEASE_SECONDS


def test_explicit_claim_lease_overrides_environment_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    monkeypatch.setenv("AGENT_BRIDGE_STALE_LEASE_SECONDS", "1000")

    claim = claim_task(
        agent="claude-1",
        task_id="task-explicit-lease",
        summary="explicit lease",
        lease_seconds=45,
        bridge_root=bridge,
    )

    assert claim.lease_seconds == 45


def test_legacy_heartbeat_materializes_environment_lease_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    initial = datetime(2026, 5, 18, 7, 0, tzinfo=timezone.utc)
    refreshed_at = initial + timedelta(minutes=1)
    claim_task(
        agent="claude-1",
        task_id="task-legacy-env-lease",
        summary="legacy lease",
        bridge_root=bridge,
        now_utc=initial,
    )
    claim_path = bridge / "work_queue" / "claims" / "task-legacy-env-lease.json"
    payload = json.loads(claim_path.read_text(encoding="utf-8"))
    payload.pop("lease_seconds")
    payload.pop("claim_lease_expires_utc")
    claim_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("AGENT_BRIDGE_STALE_LEASE_SECONDS", "1000")

    refreshed = heartbeat(
        agent="claude-1",
        task_id="task-legacy-env-lease",
        bridge_root=bridge,
        now_utc=refreshed_at,
    )

    assert refreshed.lease_seconds == 1000
    expiry = datetime.fromisoformat(
        refreshed.claim_lease_expires_utc.replace("Z", "+00:00")
    )
    assert expiry - refreshed_at == timedelta(seconds=1000)
