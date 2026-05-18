from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from waggledance.core.work_queue import (
    ArchivedClaim,
    Claim,
    PRIVILEGED_AGENTS,
    WorkQueueError,
    archive_stale_claims,
    claim_task,
    heartbeat,
    list_claims,
)


def _now() -> datetime:
    return datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc)


def _stale_now() -> datetime:
    return _now() + timedelta(hours=1)


def test_dry_run_returns_planned_archives_without_mutating_fs(tmp_path: Path) -> None:
    bridge = tmp_path / ".agent-bridge"
    claim_task(
        agent="claude-1",
        task_id="task-stale-1",
        summary="will go stale",
        bridge_root=bridge,
    )
    archived = archive_stale_claims(
        bridge_root=bridge,
        now_utc=_stale_now(),
        max_age_seconds=60,
        apply=False,
    )
    assert len(archived) == 1
    record = archived[0]
    assert record.applied is False
    assert record.claim.task_id == "task-stale-1"
    assert record.age_seconds >= 60
    # Dry run: original claim still present, no archive on disk.
    assert (bridge / "work_queue" / "claims" / "task-stale-1.json").exists()
    assert not record.archived_path.exists()


def test_apply_archives_stale_claim_and_unlinks_original(tmp_path: Path) -> None:
    bridge = tmp_path / ".agent-bridge"
    claim_task(
        agent="claude-1",
        task_id="task-stale-2",
        summary="archive me",
        bridge_root=bridge,
    )
    archived = archive_stale_claims(
        bridge_root=bridge,
        now_utc=_stale_now(),
        max_age_seconds=60,
        apply=True,
    )
    assert len(archived) == 1
    record = archived[0]
    assert record.applied is True
    assert record.archived_path.exists()
    payload = json.loads(record.archived_path.read_text(encoding="utf-8"))
    assert payload["release_status"] == "stale_lease"
    assert "lease threshold" in payload["release_reason"]
    assert payload["released_at_utc"].endswith("Z")
    # Original claim file is gone.
    assert not (bridge / "work_queue" / "claims" / "task-stale-2.json").exists()


def test_fresh_heartbeat_is_not_archived(tmp_path: Path) -> None:
    bridge = tmp_path / ".agent-bridge"
    claim_task(
        agent="claude-1",
        task_id="task-fresh",
        summary="recent",
        bridge_root=bridge,
    )
    heartbeat(
        agent="claude-1",
        task_id="task-fresh",
        bridge_root=bridge,
        now_utc=_stale_now() - timedelta(seconds=30),
    )
    archived = archive_stale_claims(
        bridge_root=bridge,
        now_utc=_stale_now(),
        max_age_seconds=300,
        apply=True,
    )
    assert archived == []
    assert (bridge / "work_queue" / "claims" / "task-fresh.json").exists()


def test_operator_and_system_claims_are_never_archived(tmp_path: Path) -> None:
    bridge = tmp_path / ".agent-bridge"
    assert PRIVILEGED_AGENTS == frozenset({"operator", "system"})
    claim_task(
        agent="operator",
        task_id="task-priv-op",
        summary="operator owned",
        bridge_root=bridge,
    )
    claim_task(
        agent="system",
        task_id="task-priv-sys",
        summary="system owned",
        bridge_root=bridge,
    )
    archived = archive_stale_claims(
        bridge_root=bridge,
        now_utc=_stale_now(),
        max_age_seconds=60,
        apply=True,
    )
    assert archived == []
    surviving = {claim.task_id for claim in list_claims(bridge_root=bridge)}
    assert surviving == {"task-priv-op", "task-priv-sys"}


def test_empty_claims_dir_returns_empty_list(tmp_path: Path) -> None:
    bridge = tmp_path / ".agent-bridge"
    archived = archive_stale_claims(
        bridge_root=bridge,
        now_utc=_stale_now(),
        max_age_seconds=60,
        apply=True,
    )
    assert archived == []


def test_archive_path_uses_safe_task_name_and_utc_stamp(tmp_path: Path) -> None:
    bridge = tmp_path / ".agent-bridge"
    claim_task(
        agent="claude-1",
        task_id="task.with-mixed.chars",
        summary="archive me",
        bridge_root=bridge,
    )
    now = _stale_now()
    archived = archive_stale_claims(
        bridge_root=bridge,
        now_utc=now,
        max_age_seconds=60,
        apply=True,
    )
    assert len(archived) == 1
    record = archived[0]
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    assert record.archived_path.name == f"task.with-mixed.chars.{stamp}.stale_lease.json"


def test_apply_creates_done_directory_when_missing(tmp_path: Path) -> None:
    bridge = tmp_path / ".agent-bridge"
    claim_task(
        agent="claude-1",
        task_id="task-done-create",
        summary="archive me",
        bridge_root=bridge,
    )
    done_dir = bridge / "work_queue" / "done"
    if done_dir.exists():
        for child in done_dir.iterdir():
            child.unlink()
        done_dir.rmdir()
    archive_stale_claims(
        bridge_root=bridge,
        now_utc=_stale_now(),
        max_age_seconds=60,
        apply=True,
    )
    assert done_dir.exists()


def test_both_timestamps_unparseable_falls_back_to_max_age(tmp_path: Path) -> None:
    bridge = tmp_path / ".agent-bridge"
    claim_task(
        agent="claude-1",
        task_id="task-bad-ts",
        summary="bad ts",
        bridge_root=bridge,
    )
    claim_file = bridge / "work_queue" / "claims" / "task-bad-ts.json"
    payload = json.loads(claim_file.read_text(encoding="utf-8"))
    payload["last_heartbeat_utc"] = "not-a-timestamp"
    payload["claimed_at_utc"] = "also-bad"
    claim_file.write_text(json.dumps(payload), encoding="utf-8")

    archived = archive_stale_claims(
        bridge_root=bridge,
        now_utc=_stale_now(),
        max_age_seconds=60,
        apply=False,
    )
    assert len(archived) == 1
    assert archived[0].age_seconds == 60


def test_unparseable_heartbeat_falls_back_to_fresh_claimed_at(tmp_path: Path) -> None:
    bridge = tmp_path / ".agent-bridge"
    claim_task(
        agent="claude-1",
        task_id="task-fallback",
        summary="heartbeat broken but claimed_at fresh",
        bridge_root=bridge,
    )
    claim_file = bridge / "work_queue" / "claims" / "task-fallback.json"
    payload = json.loads(claim_file.read_text(encoding="utf-8"))
    fresh_claimed_at = (_stale_now() - timedelta(seconds=10)).isoformat().replace(
        "+00:00", "Z"
    )
    payload["last_heartbeat_utc"] = "garbage-not-a-timestamp"
    payload["claimed_at_utc"] = fresh_claimed_at
    claim_file.write_text(json.dumps(payload), encoding="utf-8")

    archived = archive_stale_claims(
        bridge_root=bridge,
        now_utc=_stale_now(),
        max_age_seconds=60,
        apply=False,
    )
    assert archived == []
    assert claim_file.exists()


def test_unparseable_heartbeat_falls_back_to_stale_claimed_at(tmp_path: Path) -> None:
    bridge = tmp_path / ".agent-bridge"
    claim_task(
        agent="claude-1",
        task_id="task-fallback-stale",
        summary="heartbeat broken and claimed_at stale",
        bridge_root=bridge,
    )
    claim_file = bridge / "work_queue" / "claims" / "task-fallback-stale.json"
    payload = json.loads(claim_file.read_text(encoding="utf-8"))
    stale_claimed_at = (_stale_now() - timedelta(seconds=600)).isoformat().replace(
        "+00:00", "Z"
    )
    payload["last_heartbeat_utc"] = "garbage-not-a-timestamp"
    payload["claimed_at_utc"] = stale_claimed_at
    claim_file.write_text(json.dumps(payload), encoding="utf-8")

    archived = archive_stale_claims(
        bridge_root=bridge,
        now_utc=_stale_now(),
        max_age_seconds=60,
        apply=False,
    )
    assert len(archived) == 1
    assert 590 <= archived[0].age_seconds <= 610


def test_negative_max_age_seconds_raises(tmp_path: Path) -> None:
    bridge = tmp_path / ".agent-bridge"
    claim_task(
        agent="claude-1",
        task_id="task-negative",
        summary="must not be archived under negative threshold",
        bridge_root=bridge,
        now_utc=_stale_now() - timedelta(seconds=30),
    )
    with pytest.raises(WorkQueueError, match="max_age_seconds must be positive"):
        archive_stale_claims(
            bridge_root=bridge,
            now_utc=_stale_now(),
            max_age_seconds=-1,
            apply=True,
        )
    # Fresh claim untouched.
    assert (bridge / "work_queue" / "claims" / "task-negative.json").exists()


def test_zero_max_age_seconds_raises(tmp_path: Path) -> None:
    bridge = tmp_path / ".agent-bridge"
    claim_task(
        agent="claude-1",
        task_id="task-zero",
        summary="zero threshold also refused",
        bridge_root=bridge,
    )
    with pytest.raises(WorkQueueError, match="max_age_seconds must be positive"):
        archive_stale_claims(
            bridge_root=bridge,
            now_utc=_stale_now(),
            max_age_seconds=0,
            apply=True,
        )
    assert (bridge / "work_queue" / "claims" / "task-zero.json").exists()


def test_apply_archive_includes_original_metadata(tmp_path: Path) -> None:
    bridge = tmp_path / ".agent-bridge"
    claim_task(
        agent="claude-1",
        task_id="task-metadata",
        summary="metadata check",
        mode="write",
        write_scope=["tools/foo.py", "tools/bar.py"],
        run_id="run-abc",
        bridge_root=bridge,
    )
    archived = archive_stale_claims(
        bridge_root=bridge,
        now_utc=_stale_now(),
        max_age_seconds=60,
        apply=True,
    )
    payload = json.loads(archived[0].archived_path.read_text(encoding="utf-8"))
    assert payload["agent"] == "claude-1"
    assert payload["mode"] == "write"
    assert payload["write_scope"] == ["tools/foo.py", "tools/bar.py"]
    assert payload["run_id"] == "run-abc"
    assert payload["summary"] == "metadata check"
