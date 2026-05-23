from __future__ import annotations

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


def test_claim_invalid_task_id_refused(tmp_path: Path) -> None:
    bridge = tmp_path / ".agent-bridge"
    with pytest.raises(WorkQueueError):
        claim_task(
            agent="claude-1",
            task_id="../escape",
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
