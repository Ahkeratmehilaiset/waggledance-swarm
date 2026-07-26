from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import waggledance.core.work_queue as work_queue_module
from waggledance.core.work_queue import (
    ArchivedClaim,
    Claim,
    PRIVILEGED_AGENTS,
    WorkQueueError,
    archive_stale_claims,
    claim_task as _claim_task,
    heartbeat,
    list_claims,
)


def _now() -> datetime:
    return datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc)


def _stale_now() -> datetime:
    return _now() + timedelta(hours=1)


def claim_task(*args, now_utc: datetime | None = None, **kwargs):
    return _claim_task(*args, now_utc=now_utc or _now(), **kwargs)


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


def test_dry_run_on_missing_root_creates_no_lock_or_queue_files(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / "missing-bridge"

    archived = archive_stale_claims(
        bridge_root=bridge,
        now_utc=_stale_now(),
        max_age_seconds=60,
        apply=False,
    )

    assert archived == []
    assert not bridge.exists()


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


def test_stale_move_failure_leaves_original_active_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    claim_task(
        agent="claude-1",
        task_id="task-stale-move-failure",
        summary="archive me safely",
        bridge_root=bridge,
    )
    claim_path = (
        bridge / "work_queue" / "claims" / "task-stale-move-failure.json"
    )
    before = claim_path.read_bytes()

    def fail_move(_source: Path, _target: Path) -> None:
        raise WorkQueueError("simulated stale terminal target collision")

    monkeypatch.setattr(work_queue_module, "_move_no_replace", fail_move)
    with pytest.raises(WorkQueueError, match="target collision"):
        archive_stale_claims(
            bridge_root=bridge,
            now_utc=_stale_now(),
            max_age_seconds=60,
            apply=True,
        )

    assert claim_path.read_bytes() == before
    assert list((bridge / "work_queue" / "done").glob("*.json")) == []


def test_stale_metadata_failure_reports_committed_terminal_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    claim_task(
        agent="claude-1",
        task_id="task-stale-metadata-failure",
        summary="terminal transition wins",
        bridge_root=bridge,
    )

    def fail_metadata_write(*_args, **_kwargs) -> None:
        raise OSError("simulated stale metadata failure")

    monkeypatch.setattr(
        work_queue_module,
        "_write_json_file",
        fail_metadata_write,
    )
    with pytest.warns(RuntimeWarning, match="stale release committed"):
        archived = archive_stale_claims(
            bridge_root=bridge,
            now_utc=_stale_now(),
            max_age_seconds=60,
            apply=True,
        )

    assert len(archived) == 1
    assert archived[0].applied is True
    assert list((bridge / "work_queue" / "claims").glob("*.json")) == []
    terminal = list((bridge / "work_queue" / "done").glob("*.json"))
    assert len(terminal) == 1
    payload = json.loads(terminal[0].read_text(encoding="utf-8"))
    assert payload["task_id"] == "task-stale-metadata-failure"
    assert "release_status" not in payload


def test_archive_stale_legacy_claim_honors_environment_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    claim_task(
        agent="claude-1",
        task_id="legacy-archive-env",
        summary="legacy fallback",
        bridge_root=bridge,
    )
    claim_path = bridge / "work_queue" / "claims" / "legacy-archive-env.json"
    payload = json.loads(claim_path.read_text(encoding="utf-8"))
    payload.pop("lease_seconds")
    payload.pop("claim_lease_expires_utc")
    claim_path.write_text(json.dumps(payload), encoding="utf-8")
    now = _now() + timedelta(seconds=500)

    monkeypatch.setenv("AGENT_BRIDGE_STALE_LEASE_SECONDS", "1000")
    assert archive_stale_claims(
        bridge_root=bridge,
        now_utc=now,
        apply=False,
    ) == []

    monkeypatch.setenv("AGENT_BRIDGE_STALE_LEASE_SECONDS", "100")
    archived = archive_stale_claims(
        bridge_root=bridge,
        now_utc=now,
        apply=False,
    )
    assert [record.claim.task_id for record in archived] == [
        "legacy-archive-env"
    ]


def test_apply_archives_legacy_powershell_namespaced_claim_file(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    task_id = "codex-tools-1/legacy-stale-claim"
    claim_task(
        agent="codex-tools-1",
        task_id=task_id,
        summary="legacy archive",
        bridge_root=bridge,
    )
    claims_dir = bridge / "work_queue" / "claims"
    preferred_path = next(claims_dir.glob("*.json"))
    legacy_path = claims_dir / "codex-tools-1_legacy-stale-claim.json"
    preferred_path.rename(legacy_path)

    archived = archive_stale_claims(
        bridge_root=bridge,
        now_utc=_stale_now(),
        max_age_seconds=60,
        apply=True,
    )

    assert len(archived) == 1
    assert archived[0].claim.task_id == task_id
    assert archived[0].archived_path.exists()
    assert not legacy_path.exists()
    assert list_claims(bridge_root=bridge) == []


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


def test_both_timestamps_unparseable_are_not_destructively_archived(
    tmp_path: Path,
) -> None:
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
    assert archived == []
    assert claim_file.exists()


def test_per_claim_lease_replaces_shorter_global_threshold(tmp_path: Path) -> None:
    bridge = tmp_path / ".agent-bridge"
    claim_task(
        agent="claude-1",
        task_id="task-long-lease",
        summary="long lease",
        bridge_root=bridge,
        lease_seconds=1_000,
    )

    archived = archive_stale_claims(
        bridge_root=bridge,
        now_utc=_now() + timedelta(seconds=500),
        max_age_seconds=60,
        apply=False,
    )

    assert archived == []


def test_later_explicit_lease_expiry_extends_effective_expiry(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    claim_task(
        agent="claude-1",
        task_id="task-explicit-expiry",
        summary="explicit expiry",
        bridge_root=bridge,
        lease_seconds=60,
    )
    claim_file = bridge / "work_queue" / "claims" / "task-explicit-expiry.json"
    payload = json.loads(claim_file.read_text(encoding="utf-8"))
    payload["claim_lease_expires_utc"] = (
        _now() + timedelta(seconds=600)
    ).isoformat().replace("+00:00", "Z")
    claim_file.write_text(json.dumps(payload), encoding="utf-8")

    before_expiry = archive_stale_claims(
        bridge_root=bridge,
        now_utc=_now() + timedelta(seconds=599),
        max_age_seconds=30,
        apply=False,
    )
    at_expiry = archive_stale_claims(
        bridge_root=bridge,
        now_utc=_now() + timedelta(seconds=600),
        max_age_seconds=30,
        apply=False,
    )

    assert before_expiry == []
    assert [record.claim.task_id for record in at_expiry] == [
        "task-explicit-expiry"
    ]


def test_legacy_claim_without_lease_fields_uses_global_threshold(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    claim_task(
        agent="claude-1",
        task_id="task-legacy-lease",
        summary="legacy lease",
        bridge_root=bridge,
    )
    claim_file = bridge / "work_queue" / "claims" / "task-legacy-lease.json"
    payload = json.loads(claim_file.read_text(encoding="utf-8"))
    payload.pop("lease_seconds")
    payload.pop("claim_lease_expires_utc")
    claim_file.write_text(json.dumps(payload), encoding="utf-8")

    before_expiry = archive_stale_claims(
        bridge_root=bridge,
        now_utc=_now() + timedelta(seconds=59),
        max_age_seconds=60,
        apply=False,
    )
    at_expiry = archive_stale_claims(
        bridge_root=bridge,
        now_utc=_now() + timedelta(seconds=60),
        max_age_seconds=60,
        apply=False,
    )

    assert before_expiry == []
    assert [record.claim.task_id for record in at_expiry] == [
        "task-legacy-lease"
    ]


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
