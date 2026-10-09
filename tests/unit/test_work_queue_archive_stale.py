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
    WorkQueueIOError,
    archive_stale_claims,
    claim_task as _claim_task,
    heartbeat,
    list_claims,
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
def _hermetic_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test starts identity-less and bound to no agent label.

    A lane shell carries its own label and owner identity; without this the
    suite's result would depend on who runs it. Tests that need an owner or a
    bound privileged label set it explicitly.
    """
    for name in _IDENTITY_ENV:
        monkeypatch.delenv(name, raising=False)


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


def test_fresh_heartbeat_is_not_archived(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # B7: only the claim's owner may heartbeat it, so the claim needs one.
    monkeypatch.setenv("AGENT_BRIDGE_OWNER_SESSION_ID", "test-session")
    monkeypatch.setenv("AGENT_BRIDGE_OWNER_TOKEN", "test-token")
    bridge = tmp_path / ".agent-bridge"
    claim = claim_task(
        agent="claude-1",
        task_id="task-fresh",
        summary="recent",
        bridge_root=bridge,
    )
    assert claim.owner_session_id == "test-session"
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


def test_operator_and_system_claims_are_never_archived(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bridge = tmp_path / ".agent-bridge"
    assert PRIVILEGED_AGENTS == frozenset({"operator", "system"})
    # A reserved label needs a session explicitly bound to it.
    monkeypatch.setenv("AGENT_BRIDGE_AGENT", "operator")
    claim_task(
        agent="operator",
        task_id="task-priv-op",
        summary="operator owned",
        bridge_root=bridge,
    )
    # system has no public claim authority even when bound, so the fixture
    # relabels a writer-produced claim file instead.
    monkeypatch.setenv("AGENT_BRIDGE_AGENT", "system")
    with pytest.raises(WorkQueueError, match="no public bridge authority"):
        claim_task(
            agent="system",
            task_id="task-priv-sys",
            summary="system owned",
            bridge_root=bridge,
        )
    monkeypatch.setenv("AGENT_BRIDGE_AGENT", "claude-1")
    claim_task(
        agent="claude-1",
        task_id="task-priv-sys",
        summary="system owned",
        bridge_root=bridge,
    )
    system_file = bridge / "work_queue" / "claims" / "task-priv-sys.json"
    payload = json.loads(system_file.read_text(encoding="utf-8"))
    payload["agent"] = "system"
    system_file.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.delenv("AGENT_BRIDGE_AGENT")
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


@pytest.mark.parametrize("claimed_age, archived", [(10, False), (600, True)], ids=["fresh_claimed_at", "stale_claimed_at"])
def test_out_of_range_heartbeat_is_unparseable_and_falls_back_to_claimed_at(
    tmp_path: Path, claimed_age: int, archived: bool
) -> None:
    # RCO2 35B5527F P1: year 1 at +14:00 leaves the datetime range; it was an OverflowError that no reader caught.
    bridge = tmp_path / ".agent-bridge"
    claim_task(agent="claude-1", task_id="task-overflow", summary="out-of-range heartbeat", bridge_root=bridge)
    claim_file = bridge / "work_queue" / "claims" / "task-overflow.json"
    payload = json.loads(claim_file.read_text(encoding="utf-8"))
    payload["last_heartbeat_utc"] = "0001-01-01T00:00:00+14:00"
    payload["claimed_at_utc"] = (_stale_now() - timedelta(seconds=claimed_age)).isoformat().replace("+00:00", "Z")
    claim_file.write_text(json.dumps(payload), encoding="utf-8")
    planned = archive_stale_claims(bridge_root=bridge, now_utc=_stale_now(), max_age_seconds=60, apply=False)
    assert [a.claim.task_id for a in planned] == (["task-overflow"] if archived else [])
    assert claim_file.exists()                                     # a dry run writes nothing


# -- QB-L1-a (RCO2 FF7BC9A6): a failed claim unlink keeps the completed archives and rolls back only its own ------

def _deny_unlink(monkeypatch: pytest.MonkeyPatch, *fragments: str) -> None:
    """The writer's own I/O error: unlink of a path containing any fragment fails as Windows does for a read-only file."""
    real = Path.unlink

    def unlink(self: Path, missing_ok: bool = False) -> None:
        if any(fragment in self.name for fragment in fragments):
            raise PermissionError(13, "Access is denied", str(self))
        real(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", unlink)


def _three_stale(bridge: Path) -> list:
    for task in ("task-a", "task-b", "task-c"):
        claim_task(agent="claude-1", task_id=task, summary="stale " + task, bridge_root=bridge)
    planned = archive_stale_claims(bridge_root=bridge, now_utc=_stale_now(), max_age_seconds=60)
    assert len(planned) == 3
    return planned


@pytest.mark.parametrize("failing", [0, 1, 2], ids=["zero_completed", "one_completed", "many_completed"])
def test_a_failed_claim_unlink_rolls_back_its_archive_and_reports_the_completed_ones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failing: int
) -> None:
    bridge = tmp_path / ".agent-bridge"
    planned = _three_stale(bridge)
    bad = planned[failing]
    _deny_unlink(monkeypatch, bad.claim.task_id + ".json", bad.claim.task_id + "-")
    with pytest.raises(WorkQueueIOError) as raised:
        archive_stale_claims(bridge_root=bridge, now_utc=_stale_now(), max_age_seconds=60, apply=True)
    error = raised.value
    assert isinstance(error, OSError) and error.errno == 13 and isinstance(error.__cause__, PermissionError)
    assert [a.claim.task_id for a in error.completed] == [a.claim.task_id for a in planned[:failing]]
    assert all(a.applied and a.archived_path.exists() for a in error.completed)
    assert (error.applied, error.rollback_errors, error.residual) == (failing > 0, [], [])
    assert not bad.archived_path.exists()                     # no done record beside the still-active claim
    assert {c.task_id for c in list_claims(bridge_root=bridge)} == {a.claim.task_id for a in planned[failing:]}
    for later in planned[failing + 1:]:
        assert not later.archived_path.exists()               # never reached, nothing written


def test_a_failed_rollback_leaves_the_outcome_unknown_and_names_the_residual(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bridge = tmp_path / ".agent-bridge"
    planned = _three_stale(bridge)
    bad = planned[1]
    _deny_unlink(monkeypatch, bad.claim.task_id)              # the claim AND its new archive refuse removal
    with pytest.raises(WorkQueueIOError) as raised:
        archive_stale_claims(bridge_root=bridge, now_utc=_stale_now(), max_age_seconds=60, apply=True)
    error = raised.value
    assert error.applied is None and error.residual == [str(bad.archived_path)]
    assert len(error.rollback_errors) == 1 and error.rollback_errors[0].startswith("PermissionError: ")
    assert [a.claim.task_id for a in error.completed] == [planned[0].claim.task_id]
    assert bad.archived_path.exists()                         # reported, not hidden


def test_an_existing_archive_is_a_collision_never_deleted_and_the_claim_stays_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bridge = tmp_path / ".agent-bridge"
    planned = _three_stale(bridge)
    bad = planned[1]
    bad.archived_path.parent.mkdir(parents=True, exist_ok=True)   # a dry run writes nothing
    bad.archived_path.write_bytes(b"an earlier, unknown record")
    with pytest.raises(WorkQueueIOError) as raised:
        archive_stale_claims(bridge_root=bridge, now_utc=_stale_now(), max_age_seconds=60, apply=True)
    error = raised.value
    assert isinstance(error.__cause__.__cause__, FileExistsError)
    assert (error.applied, error.rollback_errors, error.residual) == (True, [], [])
    assert [a.claim.task_id for a in error.completed] == [planned[0].claim.task_id]
    assert bad.archived_path.read_bytes() == b"an earlier, unknown record"
    assert bad.claim.task_id in {c.task_id for c in list_claims(bridge_root=bridge)}
