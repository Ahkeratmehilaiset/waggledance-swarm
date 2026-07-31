from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import waggledance.core.work_queue as work_queue_module
from waggledance.core.work_queue import (
    ArchivedClaim,
    Claim,
    PRIVILEGED_AGENTS,
    WorkQueueError,
    _claim_mutation_lock,
    archive_stale_claims,
    claim_task as _claim_task,
    heartbeat,
    list_claims,
)

OWNER_TOKEN = "a" * 64
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _valid_owner_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_BRIDGE_AGENT", raising=False)
    monkeypatch.delenv("AGENT_BRIDGE_STALE_LEASE_SECONDS", raising=False)
    monkeypatch.setenv("AGENT_BRIDGE_SESSION_ID", "pytest-work-queue-session")
    monkeypatch.setenv(
        "AGENT_BRIDGE_OWNER_SESSION_ID",
        "pytest-owner-session",
    )
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


def _now() -> datetime:
    return datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc)


def _stale_now() -> datetime:
    return _now() + timedelta(hours=1)


def claim_task(*args, now_utc: datetime | None = None, **kwargs):
    return _claim_task(*args, now_utc=now_utc or _now(), **kwargs)


def _raw_claim_payload(
    task_id: str,
    *,
    agent: str = "claude-1",
    claimed_at: datetime | None = None,
    heartbeat_at: datetime | None = None,
    lease_seconds: object = 300,
) -> dict[str, object]:
    claimed = claimed_at or (_now() - timedelta(seconds=301))
    heartbeat = heartbeat_at or claimed
    return {
        "agent": agent,
        "task_id": task_id,
        "summary": "raw stale fixture",
        "mode": "read-only",
        "write_scope": [],
        "run_id": "raw-stale-run",
        "claimed_at_utc": claimed.isoformat().replace("+00:00", "Z"),
        "last_heartbeat_utc": heartbeat.isoformat().replace("+00:00", "Z"),
        "lease_seconds": lease_seconds,
        "claim_lease_expires_utc": "",
        "owner_session_id": "pytest-owner-session",
        "owner_token_sha256": hashlib.sha256(
            OWNER_TOKEN.encode("utf-8")
        ).hexdigest(),
        "owner_pid": 4242,
        "owner_process_start_utc": "2026-07-28T00:00:00Z",
    }


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
    digest = hashlib.sha256(task_id.encode("utf-8")).hexdigest()[:12]
    assert archived[0].archived_path.name.startswith(
        f"codex-tools-1_legacy-stale-claim-{digest}."
    )
    assert not legacy_path.exists()
    assert list_claims(bridge_root=bridge) == []


def test_legacy_tokenless_expiry_ignores_forged_lease_extension(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    claims_dir = bridge / "work_queue" / "claims"
    claims_dir.mkdir(parents=True)
    task_id = "legacy-tokenless-expiry"
    claimed_at = _now() - timedelta(seconds=120)
    claim_path = claims_dir / f"{task_id}.json"
    claim_path.write_text(
        json.dumps(
            {
                "agent": "claude-1",
                "task_id": task_id,
                "summary": "legacy expiry cannot self-extend",
                "mode": "read-only",
                "write_scope": [],
                "run_id": "legacy-run",
                "claimed_at_utc": claimed_at.isoformat().replace(
                    "+00:00",
                    "Z",
                ),
                "last_heartbeat_utc": (
                    _now() + timedelta(hours=1)
                ).isoformat().replace("+00:00", "Z"),
                "lease_seconds": 999999,
                "claim_lease_expires_utc": "2099-01-01T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    archived = archive_stale_claims(
        bridge_root=bridge,
        now_utc=_now(),
        max_age_seconds=60,
        apply=True,
    )

    assert len(archived) == 1
    assert archived[0].age_seconds == 120
    assert "legacy tokenless claim claimed_at_utc" in archived[0].release_reason
    assert not claim_path.exists()


def test_owned_claim_uses_positive_stored_lease_before_global_threshold(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    claim_task(
        agent="claude-1",
        task_id="owned-stored-lease",
        summary="stored 900 second lease",
        bridge_root=bridge,
        now_utc=_now(),
        lease_seconds=900,
    )

    archived = archive_stale_claims(
        bridge_root=bridge,
        now_utc=_now() + timedelta(seconds=301),
        max_age_seconds=300,
        apply=True,
    )

    assert archived == []
    assert (
        bridge / "work_queue" / "claims" / "owned-stored-lease.json"
    ).exists()


def test_owned_claim_honors_later_explicit_lease_expiry(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    claim_task(
        agent="claude-1",
        task_id="owned-later-expiry",
        summary="later explicit expiry",
        bridge_root=bridge,
        now_utc=_now(),
        lease_seconds=60,
    )
    claim_path = bridge / "work_queue" / "claims" / "owned-later-expiry.json"
    payload = json.loads(claim_path.read_text(encoding="utf-8"))
    payload["claim_lease_expires_utc"] = (
        _now() + timedelta(seconds=900)
    ).isoformat().replace("+00:00", "Z")
    claim_path.write_text(json.dumps(payload), encoding="utf-8")

    still_fresh = archive_stale_claims(
        bridge_root=bridge,
        now_utc=_now() + timedelta(seconds=301),
        max_age_seconds=60,
        apply=False,
    )
    expired = archive_stale_claims(
        bridge_root=bridge,
        now_utc=_now() + timedelta(seconds=901),
        max_age_seconds=60,
        apply=False,
    )

    assert still_fresh == []
    assert len(expired) == 1
    assert expired[0].age_seconds == 901
    assert "lease threshold 900s" in expired[0].release_reason


def test_owned_claim_honors_far_future_expiry_without_int32_overflow(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    claims_dir = bridge / "work_queue" / "claims"
    claims_dir.mkdir(parents=True)
    payload = _raw_claim_payload(
        "owned-far-future-expiry",
        claimed_at=_now() - timedelta(seconds=400),
        heartbeat_at=_now() - timedelta(seconds=400),
    )
    payload["claim_lease_expires_utc"] = "2099-01-01T00:00:00Z"
    (claims_dir / "owned-far-future-expiry.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )

    assert archive_stale_claims(
        bridge_root=bridge,
        now_utc=_now(),
        max_age_seconds=300,
        apply=False,
    ) == []


@pytest.mark.parametrize(
    ("lease_value", "task_id"),
    [
        (None, "legacy-missing-lease"),
        ("not-a-lease", "legacy-malformed-lease"),
    ],
)
def test_legacy_missing_or_malformed_lease_uses_global_threshold(
    tmp_path: Path,
    lease_value: object,
    task_id: str,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    claims_dir = bridge / "work_queue" / "claims"
    claims_dir.mkdir(parents=True)
    claim_path = claims_dir / f"{task_id}.json"
    payload = {
        "agent": "claude-1",
        "task_id": task_id,
        "summary": "legacy lease fallback",
        "mode": "read-only",
        "write_scope": [],
        "run_id": "legacy-run",
        "claimed_at_utc": (
            _now() - timedelta(seconds=301)
        ).isoformat().replace("+00:00", "Z"),
        "last_heartbeat_utc": (
            _now() + timedelta(hours=1)
        ).isoformat().replace("+00:00", "Z"),
        "claim_lease_expires_utc": "2099-01-01T00:00:00Z",
    }
    if lease_value is not None:
        payload["lease_seconds"] = lease_value
    claim_path.write_text(json.dumps(payload), encoding="utf-8")
    before = claim_path.read_bytes()

    with pytest.raises(WorkQueueError, match="lease_seconds"):
        list_claims(bridge_root=bridge)
    assert claim_path.read_bytes() == before

    archived = archive_stale_claims(
        bridge_root=bridge,
        now_utc=_now(),
        max_age_seconds=300,
        apply=True,
    )

    assert len(archived) == 1
    assert archived[0].age_seconds == 301
    assert "lease threshold 300s" in archived[0].release_reason
    assert not claim_path.exists()


@pytest.mark.parametrize("lease_value", [2**31, "9" * 5000])
def test_over_int32_or_huge_stored_lease_uses_fallback_safely(
    tmp_path: Path,
    lease_value: object,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    claims_dir = bridge / "work_queue" / "claims"
    claims_dir.mkdir(parents=True)
    task_id = f"invalid-large-lease-{type(lease_value).__name__}"
    claim_path = claims_dir / f"{task_id}.json"
    claim_path.write_text(
        json.dumps(_raw_claim_payload(task_id, lease_seconds=lease_value)),
        encoding="utf-8",
    )
    before = claim_path.read_bytes()

    with pytest.raises(WorkQueueError, match="lease_seconds"):
        list_claims(bridge_root=bridge)
    assert claim_path.read_bytes() == before
    archived = archive_stale_claims(
        bridge_root=bridge,
        now_utc=_now(),
        max_age_seconds=300,
        apply=True,
    )

    assert len(archived) == 1
    assert "lease threshold 300s" in archived[0].release_reason
    assert not claim_path.exists()


def test_default_stale_fallback_is_300_seconds_for_missing_lease(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    claims_dir = bridge / "work_queue" / "claims"
    claims_dir.mkdir(parents=True)
    task_id = "default-300-missing-lease"
    payload = _raw_claim_payload(task_id)
    payload.pop("lease_seconds")
    for owner_field in (
        "owner_session_id",
        "owner_token_sha256",
        "owner_pid",
        "owner_process_start_utc",
    ):
        payload.pop(owner_field)
    (claims_dir / f"{task_id}.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )

    archived = archive_stale_claims(
        bridge_root=bridge,
        now_utc=_now(),
        apply=False,
    )

    assert len(archived) == 1
    assert "lease threshold 300s" in archived[0].release_reason


def test_default_stale_fallback_honors_bridge_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    claims_dir = bridge / "work_queue" / "claims"
    claims_dir.mkdir(parents=True)
    task_id = "environment-stale-fallback"
    payload = _raw_claim_payload(task_id)
    payload.pop("lease_seconds")
    for owner_field in (
        "owner_session_id",
        "owner_token_sha256",
        "owner_pid",
        "owner_process_start_utc",
    ):
        payload.pop(owner_field)
    (claims_dir / f"{task_id}.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_BRIDGE_STALE_LEASE_SECONDS", "1000")

    archived = archive_stale_claims(
        bridge_root=bridge,
        now_utc=_now(),
        apply=False,
    )

    assert archived == []


@pytest.mark.parametrize(
    ("owner_field", "invalid_value"),
    [
        ("owner_process_start_utc", "July 30, 2026 12:00:00"),
        ("owner_process_start_utc", "2026-07-28T00:00:00Z\n"),
        ("owner_process_start_utc", "2026-07-28T00:00:00+15:00"),
        ("owner_process_start_utc", "0001-01-01T00:00:00+14:00"),
        ("owner_pid", 1.0),
        ("owner_pid", "4242"),
        ("owner_pid", " 4242 "),
        ("owner_session_id", 123),
    ],
)
def test_noncanonical_owner_record_is_legacy_and_ignores_future_heartbeat(
    tmp_path: Path,
    owner_field: str,
    invalid_value: object,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    claims_dir = bridge / "work_queue" / "claims"
    claims_dir.mkdir(parents=True)
    task_id = f"legacy-invalid-{owner_field}"
    payload = _raw_claim_payload(
        task_id,
        heartbeat_at=_now() + timedelta(days=1),
    )
    payload[owner_field] = invalid_value
    claim_path = claims_dir / f"{task_id}.json"
    claim_path.write_text(json.dumps(payload), encoding="utf-8")

    archived = archive_stale_claims(
        bridge_root=bridge,
        now_utc=_now(),
        max_age_seconds=300,
        apply=True,
    )

    assert len(archived) == 1
    assert "legacy tokenless claim claimed_at_utc" in (
        archived[0].release_reason
    )
    assert not claim_path.exists()


def test_max_timestamp_plus_lease_is_retained_without_overflow(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    claims_dir = bridge / "work_queue" / "claims"
    claims_dir.mkdir(parents=True)
    task_id = "max-timestamp-lease"
    payload = _raw_claim_payload(task_id)
    payload["claimed_at_utc"] = "9999-12-31T23:59:59Z"
    payload["last_heartbeat_utc"] = "9999-12-31T23:59:59Z"
    payload["claim_lease_expires_utc"] = ""
    (claims_dir / f"{task_id}.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )

    archived = archive_stale_claims(
        bridge_root=bridge,
        now_utc=_now(),
        max_age_seconds=300,
        apply=False,
    )

    assert archived == []


def test_invalid_utf8_claim_does_not_block_valid_stale_claim(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    claim_task(
        agent="claude-1",
        task_id="valid-after-invalid-utf8",
        summary="valid stale claim",
        bridge_root=bridge,
        now_utc=_now() - timedelta(hours=1),
    )
    claims_dir = bridge / "work_queue" / "claims"
    (claims_dir / "00-invalid-utf8.json").write_bytes(b"\xff\xfe\xfa")

    archived = archive_stale_claims(
        bridge_root=bridge,
        now_utc=_now(),
        max_age_seconds=60,
        apply=True,
    )

    assert [record.claim.task_id for record in archived] == [
        "valid-after-invalid-utf8"
    ]
    assert (claims_dir / "00-invalid-utf8.json").is_file()


def test_owned_invalid_heartbeat_reports_claimed_at_fallback_anchor(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    claims_dir = bridge / "work_queue" / "claims"
    claims_dir.mkdir(parents=True)
    task_id = "owned-claimed-at-fallback"
    payload = _raw_claim_payload(task_id)
    payload["last_heartbeat_utc"] = "not-a-canonical-time"
    (claims_dir / f"{task_id}.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )

    archived = archive_stale_claims(
        bridge_root=bridge,
        now_utc=_now(),
        max_age_seconds=300,
        apply=False,
    )

    assert len(archived) == 1
    assert archived[0].release_reason.startswith(
        "claimed_at_utc was 301s old"
    )


def test_malformed_owner_pid_is_legacy_and_remains_sweepable(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    claims_dir = bridge / "work_queue" / "claims"
    claims_dir.mkdir(parents=True)
    task_id = "malformed-owner-pid"
    claim_path = claims_dir / f"{task_id}.json"
    claim_path.write_text(
        json.dumps(
            {
                "agent": "claude-1",
                "task_id": task_id,
                "summary": "malformed stored owner pid",
                "mode": "read-only",
                "write_scope": [],
                "run_id": "legacy-run",
                "claimed_at_utc": (
                    _now() - timedelta(seconds=120)
                ).isoformat().replace("+00:00", "Z"),
                "last_heartbeat_utc": _now().isoformat().replace(
                    "+00:00",
                    "Z",
                ),
                "lease_seconds": 60,
                "owner_session_id": "pytest-owner-session",
                "owner_token_sha256": hashlib.sha256(
                    OWNER_TOKEN.encode("utf-8")
                ).hexdigest(),
                "owner_pid": "not-a-pid",
                "owner_process_start_utc": "2026-07-28T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    before = claim_path.read_bytes()

    with pytest.raises(WorkQueueError, match="owner_pid"):
        list_claims(bridge_root=bridge)
    assert claim_path.read_bytes() == before
    archived = archive_stale_claims(
        bridge_root=bridge,
        now_utc=_now(),
        max_age_seconds=60,
        apply=True,
    )

    assert len(archived) == 1
    assert not claim_path.exists()


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
    for privileged_agent, task_id in (
        ("operator", "task-priv-op"),
        ("system", "task-priv-sys"),
    ):
        claim_task(
            agent="claude-1",
            task_id=task_id,
            summary=f"{privileged_agent} owned",
            bridge_root=bridge,
        )
        claim_path = bridge / "work_queue" / "claims" / f"{task_id}.json"
        payload = json.loads(claim_path.read_text(encoding="utf-8"))
        payload["agent"] = privileged_agent
        claim_path.write_text(json.dumps(payload), encoding="utf-8")
    archived = archive_stale_claims(
        bridge_root=bridge,
        now_utc=_stale_now(),
        max_age_seconds=60,
        apply=True,
    )
    assert archived == []
    surviving = {claim.task_id for claim in list_claims(bridge_root=bridge)}
    assert surviving == {"task-priv-op", "task-priv-sys"}


def test_mixed_case_operator_spelling_is_not_privileged(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    claims_dir = bridge / "work_queue" / "claims"
    claims_dir.mkdir(parents=True)
    task_id = "mixed-case-operator"
    claim_path = claims_dir / f"{task_id}.json"
    claim_path.write_text(
        json.dumps(
            _raw_claim_payload(
                task_id,
                agent="Operator",
                lease_seconds=1,
            )
        ),
        encoding="utf-8",
    )

    archived = archive_stale_claims(
        bridge_root=bridge,
        now_utc=_now(),
        max_age_seconds=1,
        apply=True,
    )

    assert [record.claim.task_id for record in archived] == [task_id]
    assert not claim_path.exists()


def test_null_optional_schema_is_normalized_without_aborting_sweep(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    claims_dir = bridge / "work_queue" / "claims"
    claims_dir.mkdir(parents=True)
    task_id = "null-optional-schema"
    payload = _raw_claim_payload(task_id, lease_seconds=1)
    payload.update(
        {
            "write_scope": None,
            "capabilities": None,
            "owner_token": OWNER_TOKEN,
            "unknown_field": "must not persist",
        }
    )
    claim_path = claims_dir / f"{task_id}.json"
    claim_path.write_text(json.dumps(payload), encoding="utf-8")
    before = claim_path.read_bytes()

    with pytest.raises(WorkQueueError, match="write_scope"):
        list_claims(bridge_root=bridge)
    assert claim_path.read_bytes() == before
    archived = archive_stale_claims(
        bridge_root=bridge,
        now_utc=_now(),
        max_age_seconds=1,
        apply=True,
    )
    archived_payload = json.loads(
        archived[0].archived_path.read_text(encoding="utf-8")
    )

    assert archived_payload["write_scope"] == []
    assert "capabilities" not in archived_payload
    assert "owner_token" not in archived_payload
    assert "unknown_field" not in archived_payload


def test_apply_duplicate_exact_tasks_refuses_before_any_archive_write(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    claims_dir = bridge / "work_queue" / "claims"
    claims_dir.mkdir(parents=True)
    task_id = "duplicate-stale-task"
    payload = json.dumps(
        _raw_claim_payload(task_id, lease_seconds=1)
    ).encode()
    paths = (
        claims_dir / "duplicate-stale-task.json",
        claims_dir / "duplicate-stale-shadow.json",
    )
    for path in paths:
        path.write_bytes(payload)
    before = {path.name: path.read_bytes() for path in paths}

    with pytest.raises(
        WorkQueueError,
        match="duplicate active claim records for exact task_id",
    ):
        archive_stale_claims(
            bridge_root=bridge,
            now_utc=_now(),
            max_age_seconds=1,
            apply=True,
        )

    assert {path.name: path.read_bytes() for path in paths} == before
    assert not (bridge / "work_queue" / "done").exists()


def test_apply_case_insensitive_archive_collision_is_zero_write(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    claims_dir = bridge / "work_queue" / "claims"
    claims_dir.mkdir(parents=True)
    paths = (
        claims_dir / "legacy-case-upper.json",
        claims_dir / "legacy-case-lower.json",
    )
    paths[0].write_text(
        json.dumps(_raw_claim_payload("CaseTask", lease_seconds=1)),
        encoding="utf-8",
    )
    paths[1].write_text(
        json.dumps(_raw_claim_payload("casetask", lease_seconds=1)),
        encoding="utf-8",
    )
    before = {path.name: path.read_bytes() for path in paths}

    with pytest.raises(
        WorkQueueError,
        match="stale archive destination collision",
    ):
        archive_stale_claims(
            bridge_root=bridge,
            now_utc=_now(),
            max_age_seconds=1,
            apply=True,
        )

    assert {path.name: path.read_bytes() for path in paths} == before
    assert not (bridge / "work_queue" / "done").exists()


def test_dry_run_does_not_create_mutation_lock(tmp_path: Path) -> None:
    bridge = tmp_path / ".agent-bridge"
    claims_dir = bridge / "work_queue" / "claims"
    claims_dir.mkdir(parents=True)
    claim_path = claims_dir / "dry-run-no-lock.json"
    claim_path.write_text(
        json.dumps(_raw_claim_payload("dry-run-no-lock", lease_seconds=1)),
        encoding="utf-8",
    )

    planned = archive_stale_claims(
        bridge_root=bridge,
        now_utc=_now(),
        max_age_seconds=1,
        apply=False,
    )

    assert len(planned) == 1
    assert claim_path.is_file()
    assert not (bridge / "work_queue" / ".claims.mutation.lock").exists()


def test_apply_retains_nonstring_task_identity_without_inventing_name(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    claims_dir = bridge / "work_queue" / "claims"
    claims_dir.mkdir(parents=True)
    claim_path = claims_dir / "malformed-nonstring-task.json"
    payload = _raw_claim_payload("placeholder", lease_seconds=1)
    payload["task_id"] = 1.0
    claim_path.write_text(json.dumps(payload), encoding="utf-8")
    before = claim_path.read_bytes()

    archived = archive_stale_claims(
        bridge_root=bridge,
        now_utc=_now(),
        max_age_seconds=1,
        apply=True,
    )

    assert archived == []
    assert claim_path.read_bytes() == before
    assert not (bridge / "work_queue" / "done").exists()


@pytest.mark.parametrize(
    "task_id",
    ["invalid-\U0001f600", "valid/../escape"],
    ids=["non-bmp-unicode", "pathlike"],
)
def test_apply_retains_invalid_string_task_identity_without_inventing_name(
    tmp_path: Path,
    task_id: str,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    claims_dir = bridge / "work_queue" / "claims"
    claims_dir.mkdir(parents=True)
    claim_path = claims_dir / f"malformed-{task_id.encode().hex()}.json"
    payload = _raw_claim_payload("placeholder", lease_seconds=1)
    payload["task_id"] = task_id
    claim_path.write_text(json.dumps(payload), encoding="utf-8")
    before = claim_path.read_bytes()

    archived = archive_stale_claims(
        bridge_root=bridge,
        now_utc=_now(),
        max_age_seconds=1,
        apply=True,
    )

    assert archived == []
    assert claim_path.read_bytes() == before
    assert not (bridge / "work_queue" / "done").exists()


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
    payload.pop("lease_seconds")
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
    payload["lease_seconds"] = 60
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


def test_apply_archive_includes_original_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    monkeypatch.setenv("AGENT_BRIDGE_ROLE", "lead_impl")
    monkeypatch.setenv(
        "AGENT_BRIDGE_AGENT_UUID",
        "d3c9d1d1-96a9-4eb8-a8e2-6f05f9d1a101",
    )
    monkeypatch.setenv(
        "AGENT_BRIDGE_CAPABILITIES",
        "implementation,work_queue",
    )
    claim_task(
        agent="claude-1",
        task_id="task-metadata",
        summary="metadata check",
        mode="write",
        write_scope=["tools/foo.py", "tools/bar.py"],
        run_id="run-abc",
        bridge_root=bridge,
    )
    claim_path = bridge / "work_queue" / "claims" / "task-metadata.json"
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
    assert payload["session_id"] == "pytest-work-queue-session"
    assert payload["owner_session_id"] == "pytest-owner-session"
    assert payload["owner_token_sha256"] == hashlib.sha256(
        OWNER_TOKEN.encode("utf-8")
    ).hexdigest()
    assert payload["owner_pid"] == 4242
    assert payload["owner_process_start_utc"] == "2026-07-28T00:00:00Z"
    assert payload["role"] == "lead_impl"
    assert payload["agent_uuid"] == (
        "d3c9d1d1-96a9-4eb8-a8e2-6f05f9d1a101"
    )
    assert payload["capabilities"] == ["implementation", "work_queue"]
    assert payload["writer_pid"] == 9876
    assert payload["writer_pid_semantics"] == "diagnostic_only"
    assert payload["cwd"] == r"C:\Python\project2"
    assert payload["git_branch"] == "codex-lead-1/example"
    assert "owner_token" not in payload


def test_apply_does_not_publish_partial_archive_when_temp_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    claim_task(
        agent="claude-1",
        task_id="partial-archive-write",
        summary="archive write must be atomic",
        bridge_root=bridge,
    )
    claim_path = (
        bridge / "work_queue" / "claims" / "partial-archive-write.json"
    )
    original_publish = work_queue_module._publish_prepared_file_create_new

    def fail_archive_temp_write(
        prepared_body: bytes,
        destination_path: Path,
        *,
        expected_sha256: str,
        expected_size: int,
        operation: str,
    ) -> None:
        if operation == "stale archive recovery temp":
            with destination_path.open("xb") as handle:
                handle.write(prepared_body[:20])
            raise WorkQueueError("injected archive temp write failure")
        original_publish(
            prepared_body,
            destination_path,
            expected_sha256=expected_sha256,
            expected_size=expected_size,
            operation=operation,
        )

    monkeypatch.setattr(
        work_queue_module,
        "_publish_prepared_file_create_new",
        fail_archive_temp_write,
    )

    with pytest.raises(
        WorkQueueError,
        match="injected archive temp write failure",
    ):
        archive_stale_claims(
            bridge_root=bridge,
            now_utc=_stale_now(),
            max_age_seconds=60,
            apply=True,
        )

    assert claim_path.is_file()
    done_dir = bridge / "work_queue" / "done"
    partial_temps = list(done_dir.glob("*.tmp.*"))
    assert len(partial_temps) == 1
    assert partial_temps[0].read_text(encoding="utf-8")
    assert not list(
        claim_path.parent.glob(".*.stale-backup.*")
    )


def test_apply_preparation_failure_on_second_record_is_zero_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    task_ids = ("prepare-first", "prepare-second")
    for task_id in task_ids:
        claim_task(
            agent="claude-1",
            task_id=task_id,
            summary="batch preparation must be all-or-nothing",
            bridge_root=bridge,
        )
    claims_dir = bridge / "work_queue" / "claims"
    claim_paths = tuple(claims_dir / f"{task_id}.json" for task_id in task_ids)
    before = {path: path.read_bytes() for path in claim_paths}
    original_publish = work_queue_module._publish_prepared_file_create_new
    archive_temp_writes = 0

    def fail_second_archive_temp_write(
        prepared_body: bytes,
        destination_path: Path,
        *,
        expected_sha256: str,
        expected_size: int,
        operation: str,
    ) -> None:
        nonlocal archive_temp_writes
        if operation == "stale archive recovery temp":
            archive_temp_writes += 1
            if archive_temp_writes == 2:
                with destination_path.open("xb") as handle:
                    handle.write(prepared_body[:20])
                raise WorkQueueError(
                    "injected second archive preparation failure"
                )
        original_publish(
            prepared_body,
            destination_path,
            expected_sha256=expected_sha256,
            expected_size=expected_size,
            operation=operation,
        )

    monkeypatch.setattr(
        work_queue_module,
        "_publish_prepared_file_create_new",
        fail_second_archive_temp_write,
    )

    with pytest.raises(
        WorkQueueError,
        match="injected second archive preparation failure",
    ) as exc_info:
        archive_stale_claims(
            bridge_root=bridge,
            now_utc=_stale_now(),
            max_age_seconds=60,
            apply=True,
        )

    assert "archive temp identity mismatch" in str(
        exc_info.value
    )
    assert {path: path.read_bytes() for path in claim_paths} == before
    partial_temps = list(
        (bridge / "work_queue" / "done").glob("*.tmp.*")
    )
    assert len(partial_temps) == 2
    assert len(list(claims_dir.glob(".*.stale-backup.*"))) == 1


def test_apply_commit_failure_on_second_record_rolls_back_whole_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    task_ids = ("commit-first", "commit-second")
    for task_id in task_ids:
        claim_task(
            agent="claude-1",
            task_id=task_id,
            summary="batch commit must be all-or-nothing",
            bridge_root=bridge,
        )
    claims_dir = bridge / "work_queue" / "claims"
    claim_paths = tuple(claims_dir / f"{task_id}.json" for task_id in task_ids)
    before = {path: path.read_bytes() for path in claim_paths}
    original_rename = work_queue_module._rename_file_create_new

    def fail_second_source_quarantine(
        source: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        destination: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        *args,
        **kwargs,
    ) -> None:
        if Path(source) == claim_paths[1]:
            raise PermissionError("injected second source quarantine failure")
        original_rename(source, destination, *args, **kwargs)

    monkeypatch.setattr(
        work_queue_module,
        "_rename_file_create_new",
        fail_second_source_quarantine,
    )

    with pytest.raises(
        WorkQueueError,
        match="injected second source quarantine failure",
    ) as exc_info:
        archive_stale_claims(
            bridge_root=bridge,
            now_utc=_stale_now(),
            max_age_seconds=60,
            apply=True,
        )

    message = str(exc_info.value)
    assert "archive rollback skipped to avoid an unbound pathname deletion" in message
    assert "source=retained" in message
    assert "archive=retained" in message
    assert {path: path.read_bytes() for path in claim_paths} == before
    assert len(
        list((bridge / "work_queue" / "done").glob("*.stale_lease.json"))
    ) == 2
    assert len(list(claims_dir.glob(".*.stale-backup.*"))) == 2


def test_apply_commit_retains_recovery_artifacts_without_unlinking(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    task_id = "committed-cleanup-warning"
    claim_task(
        agent="claude-1",
        task_id=task_id,
        summary="cleanup residue must not hide a committed release",
        bridge_root=bridge,
    )
    claims_dir = bridge / "work_queue" / "claims"
    claim_path = claims_dir / f"{task_id}.json"
    before = claim_path.read_bytes()
    original_unlink = Path.unlink

    def reject_destructive_recovery_cleanup(
        path: Path,
        *args,
        **kwargs,
    ) -> None:
        if any(
            marker in path.name
            for marker in (".stale-backup.", ".stale-quarantine.", ".tmp.")
        ):
            raise AssertionError("recovery artifacts must not be unlinked")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", reject_destructive_recovery_cleanup)

    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        archived = archive_stale_claims(
            bridge_root=bridge,
            now_utc=_stale_now(),
            max_age_seconds=60,
            apply=True,
        )

    assert [record.claim.task_id for record in archived] == [task_id]
    assert archived[0].applied is True
    assert not claim_path.exists()
    assert archived[0].archived_path.is_file()
    backups = list(claims_dir.glob(".*.stale-backup.*"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == before
    quarantines = list(claims_dir.glob(".*.stale-quarantine.*"))
    assert len(quarantines) == 1
    assert quarantines[0].read_bytes() == before
    archive_temps = list(archived[0].archived_path.parent.glob("*.tmp.*"))
    assert len(archive_temps) == 1


def test_apply_cleanup_warning_errors_and_delivery_failure_still_return_commit(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    task_id = "committed-cleanup-warning-delivery-failure"
    claim_task(
        agent="claude-1",
        task_id=task_id,
        summary="warning policy must not hide a committed release",
        bridge_root=bridge,
    )
    claims_dir = bridge / "work_queue" / "claims"
    claim_path = claims_dir / f"{task_id}.json"
    original_verify = work_queue_module._verify_stale_source_identity

    def fail_backup_validation(
        path: Path,
        plan,
        *,
        label: str,
    ) -> tuple[bool, str | None]:
        if label == "source backup cleanup":
            return False, "injected warning-error cleanup validation failure"
        return original_verify(path, plan, label=label)

    def fail_warning_delivery(*args, **kwargs) -> None:
        del args, kwargs
        raise OSError("injected warning delivery failure")

    monkeypatch.setattr(
        work_queue_module,
        "_verify_stale_source_identity",
        fail_backup_validation,
    )
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        with monkeypatch.context() as warning_patch:
            warning_patch.setattr(
                warnings,
                "showwarning",
                fail_warning_delivery,
            )
            archived = archive_stale_claims(
                bridge_root=bridge,
                now_utc=_stale_now(),
                max_age_seconds=60,
                apply=True,
            )

    captured = capsys.readouterr()
    assert [record.claim.task_id for record in archived] == [task_id]
    assert archived[0].applied is True
    assert not claim_path.exists()
    assert archived[0].archived_path.is_file()
    assert "RuntimeWarning: stale claim batch committed" in captured.err
    assert "injected warning-error cleanup validation failure" in captured.err


def test_apply_ambiguous_move_failure_never_restores_eligibility_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    task_id = "restore-denied"
    claim_task(
        agent="claude-1",
        task_id=task_id,
        summary="failed restore must retain every recovery copy",
        bridge_root=bridge,
    )
    claims_dir = bridge / "work_queue" / "claims"
    claim_path = claims_dir / f"{task_id}.json"
    before = claim_path.read_bytes()
    original_rename = work_queue_module._rename_file_create_new

    def quarantine_source_then_report_failure(
        source: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        destination: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        *args,
        **kwargs,
    ) -> None:
        original_rename(source, destination, *args, **kwargs)
        if Path(source) == claim_path:
            raise PermissionError(
                "injected source quarantine failure after move"
            )

    monkeypatch.setattr(
        work_queue_module,
        "_rename_file_create_new",
        quarantine_source_then_report_failure,
    )
    with pytest.raises(
        WorkQueueError,
        match="injected source quarantine failure after move",
    ) as exc_info:
        archive_stale_claims(
            bridge_root=bridge,
            now_utc=_stale_now(),
            max_age_seconds=60,
            apply=True,
        )

    message = str(exc_info.value)
    done_dir = bridge / "work_queue" / "done"
    archives = list(done_dir.glob("*.stale_lease.json"))
    backups = list(claims_dir.glob(".*.stale-backup.*"))
    assert "no immutable bytes were captured after quarantine" in message
    assert "eligibility backup is not write authority" in message
    assert "archive rollback skipped" in message
    assert len(archives) == 1
    assert len(backups) == 1
    assert str(archives[0]) in message
    assert str(backups[0]) in message
    assert backups[0].read_bytes() == before
    assert not claim_path.exists()
    assert "source=missing" in message
    assert "archive=retained" in message
    assert "backup=retained" in message
    assert len(list(done_dir.glob("*.tmp.*"))) == 1


def test_apply_rollback_preserves_foreign_archive_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    task_id = "foreign-archive-replacement"
    claim_task(
        agent="claude-1",
        task_id=task_id,
        summary="rollback must not delete a foreign archive replacement",
        bridge_root=bridge,
    )
    claims_dir = bridge / "work_queue" / "claims"
    claim_path = claims_dir / f"{task_id}.json"
    done_dir = bridge / "work_queue" / "done"
    foreign_body = b"foreign replacement must survive rollback\n"
    original_rename = work_queue_module._rename_file_create_new
    replaced_archive: Path | None = None

    def replace_archive_after_source_quarantine(
        source: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        destination: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        *args,
        **kwargs,
    ) -> None:
        nonlocal replaced_archive
        original_rename(source, destination, *args, **kwargs)
        if Path(source) == claim_path:
            archives = list(done_dir.glob("*.stale_lease.json"))
            assert len(archives) == 1
            replaced_archive = archives[0]
            foreign_temp = done_dir / "foreign-replacement.tmp"
            foreign_temp.write_bytes(foreign_body)
            foreign_temp.replace(replaced_archive)
            raise PermissionError(
                "injected source quarantine failure after foreign replacement"
            )

    monkeypatch.setattr(
        work_queue_module,
        "_rename_file_create_new",
        replace_archive_after_source_quarantine,
    )

    with pytest.raises(
        WorkQueueError,
        match="injected source quarantine failure after foreign replacement",
    ) as exc_info:
        archive_stale_claims(
            bridge_root=bridge,
            now_utc=_stale_now(),
            max_age_seconds=60,
            apply=True,
        )

    message = str(exc_info.value)
    assert replaced_archive is not None
    assert not claim_path.exists()
    assert replaced_archive.read_bytes() == foreign_body
    assert "archive rollback skipped to avoid an unbound pathname deletion" in message
    assert "retained archive" in message
    assert "source=missing" in message
    assert "archive=retained" in message
    assert len(list(done_dir.glob("*.tmp.*"))) == 1
    assert len(list(claims_dir.glob(".*.stale-backup.*"))) == 1


def test_apply_rollback_preserves_recovery_after_foreign_source_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    task_id = "foreign-source-replacement"
    claim_task(
        agent="claude-1",
        task_id=task_id,
        summary="rollback must not trust a foreign active source",
        bridge_root=bridge,
    )
    claims_dir = bridge / "work_queue" / "claims"
    claim_path = claims_dir / f"{task_id}.json"
    done_dir = bridge / "work_queue" / "done"
    before = claim_path.read_bytes()
    foreign_body = b"foreign source replacement must not verify\n"
    original_rename = work_queue_module._rename_file_create_new

    def replace_source_then_report_failure(
        source: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        destination: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        *args,
        **kwargs,
    ) -> None:
        original_rename(source, destination, *args, **kwargs)
        if Path(source) == claim_path:
            claim_path.write_bytes(foreign_body)
            raise PermissionError(
                "injected failure after foreign source replacement"
            )

    monkeypatch.setattr(
        work_queue_module,
        "_rename_file_create_new",
        replace_source_then_report_failure,
    )

    with pytest.raises(
        WorkQueueError,
        match="injected failure after foreign source replacement",
    ) as exc_info:
        archive_stale_claims(
            bridge_root=bridge,
            now_utc=_stale_now(),
            max_age_seconds=60,
            apply=True,
        )

    message = str(exc_info.value)
    archives = list(done_dir.glob("*.stale_lease.json"))
    backups = list(claims_dir.glob(".*.stale-backup.*"))
    assert claim_path.read_bytes() == foreign_body
    assert len(archives) == 1
    assert len(backups) == 1
    assert backups[0].read_bytes() == before
    assert "active source identity mismatch" in message
    assert "archive rollback skipped" in message
    assert "archive=retained" in message
    assert "backup=retained" in message
    assert len(list(done_dir.glob("*.tmp.*"))) == 1


def test_apply_rollback_retains_recovery_when_source_verification_read_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    task_id = "source-verification-read-failure"
    claim_task(
        agent="claude-1",
        task_id=task_id,
        summary="unreadable rollback source must remain unverified",
        bridge_root=bridge,
    )
    claims_dir = bridge / "work_queue" / "claims"
    claim_path = claims_dir / f"{task_id}.json"
    done_dir = bridge / "work_queue" / "done"
    before = claim_path.read_bytes()
    original_open = Path.open
    verification_started = False

    def fail_source_verification_read(
        path: Path,
        mode: str = "r",
        *args,
        **kwargs,
    ):
        if path == claim_path and verification_started and mode == "rb":
            raise PermissionError(
                "injected source identity verification read failure"
            )
        return original_open(path, mode, *args, **kwargs)

    original_rename = work_queue_module._rename_file_create_new

    def guarded_quarantine(
        source: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        destination: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        *args,
        **kwargs,
    ) -> None:
        nonlocal verification_started
        if Path(source) == claim_path:
            verification_started = True
            raise PermissionError("injected source quarantine failure")
        original_rename(source, destination, *args, **kwargs)

    monkeypatch.setattr(
        work_queue_module,
        "_rename_file_create_new",
        guarded_quarantine,
    )
    monkeypatch.setattr(Path, "open", fail_source_verification_read)

    with pytest.raises(
        WorkQueueError,
        match="injected source quarantine failure",
    ) as exc_info:
        archive_stale_claims(
            bridge_root=bridge,
            now_utc=_stale_now(),
            max_age_seconds=60,
            apply=True,
        )
    verification_started = False

    message = str(exc_info.value)
    archives = list(done_dir.glob("*.stale_lease.json"))
    backups = list(claims_dir.glob(".*.stale-backup.*"))
    assert claim_path.read_bytes() == before
    assert len(archives) == 1
    assert len(backups) == 1
    assert backups[0].read_bytes() == before
    assert "active source identity verification failed" in message
    assert "injected source identity verification read failure" in message
    assert "archive rollback skipped" in message
    assert len(list(done_dir.glob("*.tmp.*"))) == 1


def test_apply_rollback_rejects_foreign_source_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    task_id = "foreign-source-backup"
    claim_task(
        agent="claude-1",
        task_id=task_id,
        summary="rollback must authenticate its exact-byte backup",
        bridge_root=bridge,
    )
    claims_dir = bridge / "work_queue" / "claims"
    claim_path = claims_dir / f"{task_id}.json"
    done_dir = bridge / "work_queue" / "done"
    foreign_body = b"foreign backup must not become the active claim\n"
    original_rename = work_queue_module._rename_file_create_new

    def replace_backup_then_report_failure(
        source: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        destination: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        *args,
        **kwargs,
    ) -> None:
        original_rename(source, destination, *args, **kwargs)
        if Path(source) == claim_path:
            backups = list(claims_dir.glob(".*.stale-backup.*"))
            assert len(backups) == 1
            foreign_temp = claims_dir / ".foreign-backup.tmp"
            foreign_temp.write_bytes(foreign_body)
            foreign_temp.replace(backups[0])
            raise PermissionError(
                "injected failure after foreign backup replacement"
            )

    monkeypatch.setattr(
        work_queue_module,
        "_rename_file_create_new",
        replace_backup_then_report_failure,
    )

    with pytest.raises(
        WorkQueueError,
        match="injected failure after foreign backup replacement",
    ) as exc_info:
        archive_stale_claims(
            bridge_root=bridge,
            now_utc=_stale_now(),
            max_age_seconds=60,
            apply=True,
        )

    message = str(exc_info.value)
    archives = list(done_dir.glob("*.stale_lease.json"))
    backups = list(claims_dir.glob(".*.stale-backup.*"))
    quarantines = list(claims_dir.glob(".*.stale-quarantine.*"))
    assert not claim_path.exists()
    assert len(archives) == 1
    assert len(backups) == 1
    assert len(quarantines) == 1
    assert backups[0].read_bytes() == foreign_body
    assert quarantines[0].read_bytes() != foreign_body
    assert "eligibility backup is not write authority" in message
    assert "archive rollback skipped" in message
    assert "archive=retained" in message
    assert "backup=retained" in message
    assert len(list(done_dir.glob("*.tmp.*"))) == 1


def test_apply_rollback_retains_verified_quarantine_when_backup_is_foreign(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    task_ids = ("verified-quarantine-first", "verified-quarantine-second")
    for task_id in task_ids:
        claim_task(
            agent="claude-1",
            task_id=task_id,
            summary="verified quarantine must remain an exact recovery copy",
            bridge_root=bridge,
        )
    claims_dir = bridge / "work_queue" / "claims"
    claim_paths = tuple(claims_dir / f"{task_id}.json" for task_id in task_ids)
    original_first = claim_paths[0].read_bytes()
    foreign_backup = b"foreign backup must never destroy exact quarantine\n"
    original_rename = work_queue_module._rename_file_create_new

    def fail_second_after_replacing_first_backup(
        source: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        destination: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        *args,
        **kwargs,
    ) -> None:
        if Path(source) == claim_paths[1]:
            backups = list(
                claims_dir.glob(
                    f".{claim_paths[0].name}.stale-backup.*"
                )
            )
            assert len(backups) == 1
            backups[0].write_bytes(foreign_backup)
            raise PermissionError(
                "injected second quarantine failure after backup replacement"
            )
        original_rename(source, destination, *args, **kwargs)

    monkeypatch.setattr(
        work_queue_module,
        "_rename_file_create_new",
        fail_second_after_replacing_first_backup,
    )

    with pytest.raises(WorkQueueError) as exc_info:
        archive_stale_claims(
            bridge_root=bridge,
            now_utc=_stale_now(),
            max_age_seconds=60,
            apply=True,
        )

    message = str(exc_info.value)
    quarantines = list(
        claims_dir.glob(f".{claim_paths[0].name}.stale-quarantine.*")
    )
    assert claim_paths[0].read_bytes() == original_first
    assert claim_paths[1].is_file()
    assert len(quarantines) == 1
    assert quarantines[0].read_bytes() == original_first
    assert "archive rollback skipped" in message


def test_apply_rollback_retains_exact_recovery_after_restored_source_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    task_ids = ("post-restore-swap-first", "post-restore-swap-second")
    for task_id in task_ids:
        claim_task(
            agent="claude-1",
            task_id=task_id,
            summary="rollback cleanup must not trust a stale verification",
            bridge_root=bridge,
        )
    claims_dir = bridge / "work_queue" / "claims"
    claim_paths = tuple(claims_dir / f"{task_id}.json" for task_id in task_ids)
    original_first = claim_paths[0].read_bytes()
    foreign_body = b"foreign active generation after restore verification\n"
    original_rename = work_queue_module._rename_file_create_new
    original_verify = (
        work_queue_module._verify_single_link_regular_file_identity
    )
    restored_verify_hits = 0

    def fail_second_source_quarantine(
        source: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        destination: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        *args,
        **kwargs,
    ) -> None:
        if Path(source) == claim_paths[1]:
            raise PermissionError("injected second source quarantine failure")
        original_rename(source, destination, *args, **kwargs)

    def replace_after_restored_source_verification(
        path: Path,
        *,
        expected_sha256: str,
        expected_size: int,
        label: str,
    ) -> tuple[bool, str | None]:
        nonlocal restored_verify_hits
        result = original_verify(
            path,
            expected_sha256=expected_sha256,
            expected_size=expected_size,
            label=label,
        )
        if (
            label == "restored captured active source"
            and Path(path) == claim_paths[0]
            and result[0]
        ):
            restored_verify_hits += 1
            foreign_temp = claim_paths[0].with_name(".foreign-active.tmp")
            foreign_temp.write_bytes(foreign_body)
            foreign_temp.replace(claim_paths[0])
        return result

    monkeypatch.setattr(
        work_queue_module,
        "_rename_file_create_new",
        fail_second_source_quarantine,
    )
    monkeypatch.setattr(
        work_queue_module,
        "_verify_single_link_regular_file_identity",
        replace_after_restored_source_verification,
    )

    with pytest.raises(
        WorkQueueError,
        match="injected second source quarantine failure",
    ):
        archive_stale_claims(
            bridge_root=bridge,
            now_utc=_stale_now(),
            max_age_seconds=60,
            apply=True,
        )

    assert restored_verify_hits == 1
    assert claim_paths[0].read_bytes() == foreign_body
    exact_recoveries = [
        path
        for path in bridge.rglob("*")
        if path.is_file() and path.read_bytes() == original_first
    ]
    assert exact_recoveries


def test_apply_rollback_restores_capture_after_quarantine_path_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    task_ids = ("post-quarantine-capture-first", "post-quarantine-capture-second")
    for task_id in task_ids:
        claim_task(
            agent="claude-1",
            task_id=task_id,
            summary="rollback must restore the immutable quarantine capture",
            bridge_root=bridge,
        )
    claims_dir = bridge / "work_queue" / "claims"
    claim_paths = tuple(claims_dir / f"{task_id}.json" for task_id in task_ids)
    original_first = claim_paths[0].read_bytes()
    foreign_body = b"foreign quarantine generation after captured read\n"
    original_rename = work_queue_module._rename_file_create_new
    original_verify = (
        work_queue_module._verify_single_link_regular_file_identity
    )
    quarantine_verify_hits = 0

    def fail_second_source_quarantine(
        source: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        destination: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        *args,
        **kwargs,
    ) -> None:
        if Path(source) == claim_paths[1]:
            raise PermissionError("injected second source quarantine failure")
        original_rename(source, destination, *args, **kwargs)

    def replace_quarantine_after_captured_verification(
        path: Path,
        *,
        expected_sha256: str,
        expected_size: int,
        label: str,
    ) -> tuple[bool, str | None]:
        nonlocal quarantine_verify_hits
        result = original_verify(
            path,
            expected_sha256=expected_sha256,
            expected_size=expected_size,
            label=label,
        )
        if (
            label == "quarantined active source"
            and f".{claim_paths[0].name}.stale-quarantine." in Path(path).name
            and result[0]
        ):
            quarantine_verify_hits += 1
            foreign_temp = Path(path).with_name(
                ".foreign-quarantine-after-capture.tmp"
            )
            foreign_temp.write_bytes(foreign_body)
            foreign_temp.replace(path)
        return result

    monkeypatch.setattr(
        work_queue_module,
        "_rename_file_create_new",
        fail_second_source_quarantine,
    )
    monkeypatch.setattr(
        work_queue_module,
        "_verify_single_link_regular_file_identity",
        replace_quarantine_after_captured_verification,
    )

    with pytest.raises(
        WorkQueueError,
        match="injected second source quarantine failure",
    ):
        archive_stale_claims(
            bridge_root=bridge,
            now_utc=_stale_now(),
            max_age_seconds=60,
            apply=True,
        )

    assert quarantine_verify_hits == 1
    assert claim_paths[0].read_bytes() == original_first
    quarantines = list(
        claims_dir.glob(f".{claim_paths[0].name}.stale-quarantine.*")
    )
    assert len(quarantines) == 1
    assert quarantines[0].read_bytes() == foreign_body


def test_apply_committed_cleanup_does_not_delete_postverify_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    task_id = "committed-postverify-swap"
    claim_task(
        agent="claude-1",
        task_id=task_id,
        summary="cleanup must retain a replacement it cannot authenticate",
        bridge_root=bridge,
    )
    claims_dir = bridge / "work_queue" / "claims"
    claim_path = claims_dir / f"{task_id}.json"
    original_claim = claim_path.read_bytes()
    foreign_body = b"foreign quarantine generation after verification\n"
    original_verify = work_queue_module._verify_stale_source_identity
    cleanup_verify_hits = 0

    def replace_after_cleanup_verification(
        path: Path,
        plan,
        *,
        label: str,
    ) -> tuple[bool, str | None]:
        nonlocal cleanup_verify_hits
        result = original_verify(path, plan, label=label)
        if (
            label == "source quarantine cleanup"
            and ".stale-quarantine." in Path(path).name
            and result[0]
        ):
            cleanup_verify_hits += 1
            foreign_temp = Path(path).with_name(".foreign-quarantine.tmp")
            foreign_temp.write_bytes(foreign_body)
            foreign_temp.replace(path)
        return result

    monkeypatch.setattr(
        work_queue_module,
        "_verify_stale_source_identity",
        replace_after_cleanup_verification,
    )

    archived = archive_stale_claims(
        bridge_root=bridge,
        now_utc=_stale_now(),
        max_age_seconds=60,
        apply=True,
    )

    assert cleanup_verify_hits == 1
    assert archived[0].applied is True
    assert archived[0].archived_path.is_file()
    assert not claim_path.exists()
    retained = [path for path in claims_dir.iterdir() if path.is_file()]
    assert any(path.read_bytes() == foreign_body for path in retained)
    assert any(path.read_bytes() == original_claim for path in retained)


def test_apply_committed_archive_has_no_mutable_temp_hardlink_alias(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    task_id = "committed-archive-no-temp-alias"
    claim_task(
        agent="claude-1",
        task_id=task_id,
        summary="committed archive must not share an inode with recovery temp",
        bridge_root=bridge,
    )

    archived = archive_stale_claims(
        bridge_root=bridge,
        now_utc=_stale_now(),
        max_age_seconds=60,
        apply=True,
    )

    archive_path = archived[0].archived_path
    expected_archive = archive_path.read_bytes()
    archive_temps = list(archive_path.parent.glob("*.tmp.*"))
    assert len(archive_temps) == 1
    assert not archive_temps[0].samefile(archive_path)
    assert archive_path.stat().st_nlink == 1

    archive_temps[0].write_bytes(b"mutated retained archive temp\n")
    assert archive_path.read_bytes() == expected_archive


def test_apply_reports_source_and_rollback_double_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    task_id = "stale-double-delete-denied"
    claim_task(
        agent="claude-1",
        task_id=task_id,
        summary="stale double rollback failure fixture",
        bridge_root=bridge,
    )
    claim_path = bridge / "work_queue" / "claims" / f"{task_id}.json"
    done_dir = bridge / "work_queue" / "done"
    before = claim_path.read_bytes()
    original_rename = work_queue_module._rename_file_create_new

    def deny_source_quarantine(
        source: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        destination: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        *args,
        **kwargs,
    ) -> None:
        if Path(source) == claim_path:
            raise PermissionError("injected stale claim quarantine failure")
        original_rename(source, destination, *args, **kwargs)

    monkeypatch.setattr(
        work_queue_module,
        "_rename_file_create_new",
        deny_source_quarantine,
    )

    with pytest.raises(
        WorkQueueError,
        match="stale claim batch apply failed",
    ) as exc_info:
        archive_stale_claims(
            bridge_root=bridge,
            now_utc=_stale_now(),
            max_age_seconds=60,
            apply=True,
    )

    assert "injected stale claim quarantine failure" in str(exc_info.value)
    assert "archive rollback skipped to avoid an unbound pathname deletion" in str(
        exc_info.value
    )
    assert "source=retained" in str(exc_info.value)
    assert "archive=retained" in str(exc_info.value)
    assert claim_path.read_bytes() == before
    assert len(list(done_dir.glob("*.stale_lease.json"))) == 1
    assert len(list(done_dir.glob("*.tmp.*"))) == 1
    assert len(list(claim_path.parent.glob(".*.stale-backup.*"))) == 1


def test_apply_waits_for_mutation_lock_and_rechecks_fresh_claim(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    task_id = "freshened-before-locked-sweep"
    claim_task(
        agent="claude-1",
        task_id=task_id,
        summary="lock race fixture",
        bridge_root=bridge,
        now_utc=_now(),
    )
    claim_path = bridge / "work_queue" / "claims" / f"{task_id}.json"

    command = [
        sys.executable,
        "-m",
        "tools.work_queue_sweep_stale",
        "--bridge-root",
        str(bridge),
        "--max-age-seconds",
        "1",
        "--apply",
        "--json",
    ]
    with _claim_mutation_lock(bridge):
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        time.sleep(0.2)
        assert process.poll() is None
        refreshed_at = datetime.now(timezone.utc)
        payload = json.loads(claim_path.read_text(encoding="utf-8"))
        payload["last_heartbeat_utc"] = refreshed_at.isoformat().replace(
            "+00:00",
            "Z",
        )
        payload["claim_lease_expires_utc"] = (
            refreshed_at + timedelta(seconds=900)
        ).isoformat().replace("+00:00", "Z")
        claim_path.write_text(json.dumps(payload), encoding="utf-8")

    stdout, stderr = process.communicate(timeout=15)
    assert process.returncode == 0, stderr
    assert json.loads(stdout)["archived"] == []
    assert claim_path.is_file()
    assert not (bridge / "work_queue" / "done").exists()


@pytest.mark.parametrize(
    ("artifact", "pattern", "warning_fragment"),
    [
        (
            "archive-temp",
            "work_queue/done/*.tmp.*",
            "archive temp cleanup identity mismatch",
        ),
        (
            "source-backup",
            "work_queue/claims/*.stale-backup.*",
            "source backup cleanup identity mismatch",
        ),
    ],
)
def test_committed_cleanup_retains_foreign_artifact_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    artifact: str,
    pattern: str,
    warning_fragment: str,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    task_id = f"committed-foreign-{artifact}"
    claim_task(
        agent="claude-1",
        task_id=task_id,
        summary="committed cleanup must authenticate artifacts",
        bridge_root=bridge,
    )
    claim_path = bridge / "work_queue" / "claims" / f"{task_id}.json"
    foreign_body = f"foreign {artifact} must survive\n".encode()
    original_rename = work_queue_module._rename_file_create_new
    original_unlink = Path.unlink
    replaced_path: Path | None = None

    def replace_artifact_during_source_quarantine(
        source: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        destination: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        *args,
        **kwargs,
    ) -> None:
        nonlocal replaced_path
        original_rename(source, destination, *args, **kwargs)
        if Path(source) == claim_path:
            candidates = list(bridge.glob(pattern))
            assert len(candidates) == 1
            replaced_path = candidates[0]
            original_unlink(replaced_path)
            replaced_path.write_bytes(foreign_body)

    monkeypatch.setattr(
        work_queue_module,
        "_rename_file_create_new",
        replace_artifact_during_source_quarantine,
    )

    with pytest.warns(RuntimeWarning, match=warning_fragment):
        archived = archive_stale_claims(
            bridge_root=bridge,
            now_utc=_stale_now(),
            max_age_seconds=60,
            apply=True,
        )

    assert [record.claim.task_id for record in archived] == [task_id]
    assert replaced_path is not None
    assert replaced_path.read_bytes() == foreign_body
    assert not claim_path.exists()
    assert archived[0].archived_path.is_file()


@pytest.mark.parametrize(
    ("artifact", "pattern", "error_fragment"),
    [
        (
            "archive-temp",
            "work_queue/done/*.tmp.*",
            "archive temp identity mismatch",
        ),
        (
            "source-backup",
            "work_queue/claims/*.stale-backup.*",
            "eligibility backup is not write authority",
        ),
    ],
)
def test_rollback_cleanup_retains_foreign_artifact_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    artifact: str,
    pattern: str,
    error_fragment: str,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    task_id = f"rollback-foreign-{artifact}"
    claim_task(
        agent="claude-1",
        task_id=task_id,
        summary="rollback cleanup must authenticate artifacts",
        bridge_root=bridge,
    )
    claim_path = bridge / "work_queue" / "claims" / f"{task_id}.json"
    foreign_body = f"foreign rollback {artifact} must survive\n".encode()
    original_rename = work_queue_module._rename_file_create_new
    original_unlink = Path.unlink
    replaced_path: Path | None = None

    def replace_artifact_then_fail_source_quarantine(
        source: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        destination: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        *args,
        **kwargs,
    ) -> None:
        nonlocal replaced_path
        original_rename(source, destination, *args, **kwargs)
        if Path(source) == claim_path:
            candidates = list(bridge.glob(pattern))
            assert len(candidates) == 1
            replaced_path = candidates[0]
            original_unlink(replaced_path)
            replaced_path.write_bytes(foreign_body)
            raise PermissionError(
                f"injected source failure after {artifact} replacement"
            )

    monkeypatch.setattr(
        work_queue_module,
        "_rename_file_create_new",
        replace_artifact_then_fail_source_quarantine,
    )

    with pytest.raises(WorkQueueError) as exc_info:
        archive_stale_claims(
            bridge_root=bridge,
            now_utc=_stale_now(),
            max_age_seconds=60,
            apply=True,
        )

    assert replaced_path is not None
    assert replaced_path.read_bytes() == foreign_body
    assert not claim_path.exists()
    assert error_fragment in str(exc_info.value)


def test_apply_rejects_fresh_source_replacement_before_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    task_id = "fresh-before-stale-backup"
    claim_task(
        agent="claude-1",
        task_id=task_id,
        summary="old stale snapshot",
        bridge_root=bridge,
    )
    claim_path = bridge / "work_queue" / "claims" / f"{task_id}.json"
    old_body = claim_path.read_bytes()
    fresh_payload = json.loads(old_body)
    fresh_payload["summary"] = "fresh replacement"
    fresh_payload["last_heartbeat_utc"] = _stale_now().isoformat().replace(
        "+00:00",
        "Z",
    )
    fresh_body = json.dumps(fresh_payload, indent=2, sort_keys=True).encode()
    original_copy = work_queue_module._copy_file_create_new
    replaced = False

    def replace_before_backup(
        source: Path,
        destination: Path,
    ) -> tuple[str, int]:
        nonlocal replaced
        if source == claim_path and not replaced:
            replaced = True
            replacement = claim_path.with_name(".fresh-replacement.tmp")
            replacement.write_bytes(fresh_body)
            replacement.replace(claim_path)
        return original_copy(source, destination)

    monkeypatch.setattr(
        work_queue_module,
        "_copy_file_create_new",
        replace_before_backup,
    )

    with pytest.raises(
        WorkQueueError,
        match="changed since eligibility snapshot",
    ):
        archive_stale_claims(
            bridge_root=bridge,
            now_utc=_stale_now(),
            max_age_seconds=60,
            apply=True,
        )

    assert replaced
    assert claim_path.read_bytes() == fresh_body
    assert not list(
        (bridge / "work_queue" / "done").glob("*.stale_lease.json")
    )


def test_apply_quarantines_and_restores_fresh_commit_time_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    task_id = "fresh-before-stale-quarantine"
    claim_task(
        agent="claude-1",
        task_id=task_id,
        summary="old stale snapshot",
        bridge_root=bridge,
    )
    claim_path = bridge / "work_queue" / "claims" / f"{task_id}.json"
    fresh_payload = json.loads(claim_path.read_text(encoding="utf-8"))
    fresh_payload["summary"] = "fresh commit-time replacement"
    fresh_payload["last_heartbeat_utc"] = _stale_now().isoformat().replace(
        "+00:00",
        "Z",
    )
    fresh_body = json.dumps(fresh_payload, indent=2, sort_keys=True).encode()
    original_rename = work_queue_module._rename_file_create_new
    replaced = False

    def replace_before_quarantine(
        source: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        destination: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        *args,
        **kwargs,
    ) -> None:
        nonlocal replaced
        if Path(source) == claim_path and not replaced:
            replaced = True
            replacement = claim_path.with_name(".fresh-commit-replacement.tmp")
            replacement.write_bytes(fresh_body)
            replacement.replace(claim_path)
        original_rename(source, destination, *args, **kwargs)

    monkeypatch.setattr(
        work_queue_module,
        "_rename_file_create_new",
        replace_before_quarantine,
    )

    with pytest.raises(
        WorkQueueError,
        match="quarantined active source identity mismatch",
    ):
        archive_stale_claims(
            bridge_root=bridge,
            now_utc=_stale_now(),
            max_age_seconds=60,
            apply=True,
        )

    assert replaced
    assert claim_path.read_bytes() == fresh_body
    quarantines = list(
        claim_path.parent.glob(f".{claim_path.name}.stale-quarantine.*")
    )
    assert len(quarantines) == 1
    assert not quarantines[0].samefile(claim_path)
    assert claim_path.stat().st_nlink == 1
    quarantines[0].write_bytes(b"mutated retained foreign quarantine\n")
    assert claim_path.read_bytes() == fresh_body


def test_apply_restores_captured_quarantine_not_postverify_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    task_id = "stale-quarantine-capture-once"
    claim_task(
        agent="claude-1",
        task_id=task_id,
        summary="old stale snapshot",
        bridge_root=bridge,
    )
    claim_path = bridge / "work_queue" / "claims" / f"{task_id}.json"
    fresh_payload = json.loads(claim_path.read_text(encoding="utf-8"))
    fresh_payload["summary"] = "fresh commit-time generation A"
    fresh_payload["last_heartbeat_utc"] = _stale_now().isoformat().replace(
        "+00:00",
        "Z",
    )
    fresh_body = json.dumps(fresh_payload, indent=2, sort_keys=True).encode()
    later_body = b'{"foreign_postverify_generation":"B"}\n'
    original_rename = work_queue_module._rename_file_create_new
    original_verify = (
        work_queue_module._verify_single_link_regular_file_identity
    )
    source_replaced = False
    quarantine_replaced = False

    def replace_before_quarantine(
        source: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        destination: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        *args,
        **kwargs,
    ) -> None:
        nonlocal source_replaced
        if Path(source) == claim_path and not source_replaced:
            source_replaced = True
            replacement = claim_path.with_name(".fresh-generation-a.tmp")
            replacement.write_bytes(fresh_body)
            replacement.replace(claim_path)
        original_rename(source, destination, *args, **kwargs)

    def replace_after_quarantine_verification(
        path: Path,
        *,
        expected_sha256: str,
        expected_size: int,
        label: str,
    ) -> tuple[bool, str | None]:
        nonlocal quarantine_replaced
        result = original_verify(
            path,
            expected_sha256=expected_sha256,
            expected_size=expected_size,
            label=label,
        )
        if (
            not quarantine_replaced
            and label == "quarantined active source"
            and result[0]
        ):
            quarantine_replaced = True
            replacement = Path(path).with_name(".fresh-generation-b.tmp")
            replacement.write_bytes(later_body)
            replacement.replace(path)
        return result

    monkeypatch.setattr(
        work_queue_module,
        "_rename_file_create_new",
        replace_before_quarantine,
    )
    monkeypatch.setattr(
        work_queue_module,
        "_verify_single_link_regular_file_identity",
        replace_after_quarantine_verification,
    )

    with pytest.raises(
        WorkQueueError,
        match="quarantined active source identity mismatch",
    ):
        archive_stale_claims(
            bridge_root=bridge,
            now_utc=_stale_now(),
            max_age_seconds=60,
            apply=True,
        )

    assert source_replaced
    assert quarantine_replaced
    assert claim_path.read_bytes() == fresh_body
    quarantines = list(
        claim_path.parent.glob(f".{claim_path.name}.stale-quarantine.*")
    )
    assert len(quarantines) == 1
    assert quarantines[0].read_bytes() == later_body
    quarantines[0].write_bytes(b'{"mutated_retained_generation":"C"}\n')
    assert claim_path.read_bytes() == fresh_body


def test_apply_rejects_external_stale_quarantine_hardlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    task_id = "stale-quarantine-external-hardlink"
    claim_task(
        agent="claude-1",
        task_id=task_id,
        summary="stale quarantine must remain single-link",
        bridge_root=bridge,
    )
    claim_path = bridge / "work_queue" / "claims" / f"{task_id}.json"
    original_body = claim_path.read_bytes()
    original_verify = (
        work_queue_module._verify_single_link_regular_file_identity
    )
    aliases: list[Path] = []

    def add_alias_before_quarantine_verification(
        path: Path,
        *,
        expected_sha256: str,
        expected_size: int,
        label: str,
    ) -> tuple[bool, str | None]:
        if (
            not aliases
            and label == "quarantined active source"
            and ".stale-quarantine." in Path(path).name
        ):
            alias = Path(path).with_name(Path(path).name + ".external-alias")
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
        add_alias_before_quarantine_verification,
    )

    with pytest.raises(WorkQueueError, match="surviving hard-link alias"):
        archive_stale_claims(
            bridge_root=bridge,
            now_utc=_stale_now(),
            max_age_seconds=60,
            apply=True,
        )

    assert len(aliases) == 1
    quarantines = [
        path
        for path in claim_path.parent.glob(
            f".{claim_path.name}.stale-quarantine.*"
        )
        if not path.name.endswith(".external-alias")
    ]
    assert len(quarantines) == 1
    assert aliases[0].samefile(quarantines[0])
    assert claim_path.read_bytes() == original_body
    assert claim_path.stat().st_nlink == 1
    aliases[0].write_bytes(b'{"external_alias_mutation":true}\n')
    assert claim_path.read_bytes() == original_body


@pytest.mark.parametrize(
    "failure_mode",
    [
        "captured_restore_write_failure",
        "capture_read_failure",
        "pre_move_source_loss",
    ],
)
def test_stale_rollback_never_restores_backup_after_generation_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_mode: str,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    task_id = f"stale-backup-revoked-{failure_mode}"
    claim_task(
        agent="claude-1",
        task_id=task_id,
        summary="eligibility generation A",
        bridge_root=bridge,
    )
    claim_path = bridge / "work_queue" / "claims" / f"{task_id}.json"
    eligibility_body = claim_path.read_bytes()
    fresh_payload = json.loads(eligibility_body)
    fresh_payload["summary"] = "fresh commit-time generation B"
    fresh_payload["last_heartbeat_utc"] = _stale_now().isoformat().replace(
        "+00:00",
        "Z",
    )
    fresh_body = json.dumps(fresh_payload, indent=2, sort_keys=True).encode()
    original_rename = work_queue_module._rename_file_create_new
    original_capture = work_queue_module._capture_raw_file_snapshot
    original_publish = work_queue_module._publish_prepared_file_create_new
    source_replaced = False
    failure_injected = False

    def replace_source_before_quarantine(
        source: Path,
        destination: Path,
    ) -> None:
        nonlocal failure_injected, source_replaced
        if (
            not source_replaced
            and Path(source) == claim_path
            and ".stale-quarantine." in Path(destination).name
        ):
            replacement = claim_path.with_name(".fresh-generation-b.tmp")
            replacement.write_bytes(fresh_body)
            replacement.replace(claim_path)
            source_replaced = True
            if failure_mode == "pre_move_source_loss":
                claim_path.unlink()
                failure_injected = True
                raise PermissionError("injected pre-move fresh source loss")
        original_rename(source, destination)

    def fail_quarantine_capture(path: Path) -> tuple[bytes, str, int]:
        nonlocal failure_injected
        if (
            failure_mode == "capture_read_failure"
            and not failure_injected
            and ".stale-quarantine." in Path(path).name
        ):
            failure_injected = True
            raise WorkQueueError("injected quarantined B capture failure")
        return original_capture(path)

    def fail_first_captured_restore(
        prepared_body: bytes,
        destination_path: Path,
        *,
        expected_sha256: str,
        expected_size: int,
        operation: str,
    ) -> None:
        nonlocal failure_injected
        if (
            failure_mode == "captured_restore_write_failure"
            and not failure_injected
            and Path(destination_path) == claim_path
            and operation == "captured active source restore"
        ):
            failure_injected = True
            raise WorkQueueError("injected captured B restore failure")
        original_publish(
            prepared_body,
            destination_path,
            expected_sha256=expected_sha256,
            expected_size=expected_size,
            operation=operation,
        )

    monkeypatch.setattr(
        work_queue_module,
        "_rename_file_create_new",
        replace_source_before_quarantine,
    )
    monkeypatch.setattr(
        work_queue_module,
        "_capture_raw_file_snapshot",
        fail_quarantine_capture,
    )
    monkeypatch.setattr(
        work_queue_module,
        "_publish_prepared_file_create_new",
        fail_first_captured_restore,
    )

    with pytest.raises(WorkQueueError) as raised:
        archive_stale_claims(
            bridge_root=bridge,
            now_utc=_stale_now(),
            max_age_seconds=60,
            apply=True,
        )

    assert source_replaced
    assert failure_injected
    quarantines = list(
        claim_path.parent.glob(f".{claim_path.name}.stale-quarantine.*")
    )
    backups = list(
        claim_path.parent.glob(f".{claim_path.name}.stale-backup.*")
    )
    assert len(backups) == 1
    assert backups[0].read_bytes() == eligibility_body
    if failure_mode == "captured_restore_write_failure":
        assert len(quarantines) == 1
        assert quarantines[0].read_bytes() == fresh_body
        assert claim_path.read_bytes() == fresh_body
        assert claim_path.stat().st_nlink == 1
        quarantines[0].write_bytes(b'{"mutated_recovery":"C"}\n')
        assert claim_path.read_bytes() == fresh_body
    else:
        if failure_mode == "capture_read_failure":
            assert len(quarantines) == 1
            assert quarantines[0].read_bytes() == fresh_body
        else:
            assert not quarantines
        assert not claim_path.exists()
        assert "eligibility backup is not write authority" in str(raised.value)


def test_rename_file_create_new_preserves_existing_destination(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.json"
    destination = tmp_path / "destination.json"
    source_body = b"source generation\n"
    destination_body = b"foreign destination generation\n"
    source.write_bytes(source_body)
    destination.write_bytes(destination_body)

    with pytest.raises(OSError):
        work_queue_module._rename_file_create_new(source, destination)

    assert source.read_bytes() == source_body
    assert destination.read_bytes() == destination_body


def test_apply_quarantine_collision_preserves_foreign_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    task_id = "stale-quarantine-create-new-collision"
    claim_task(
        agent="claude-1",
        task_id=task_id,
        summary="quarantine rename must never replace a foreign destination",
        bridge_root=bridge,
    )
    claim_path = bridge / "work_queue" / "claims" / f"{task_id}.json"
    original_claim = claim_path.read_bytes()
    foreign_body = b"foreign preexisting quarantine destination\n"
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

    with pytest.raises(WorkQueueError, match="FileExistsError"):
        archive_stale_claims(
            bridge_root=bridge,
            now_utc=_stale_now(),
            max_age_seconds=60,
            apply=True,
        )

    assert collision_path is not None
    assert collision_path.read_bytes() == foreign_body
    assert claim_path.read_bytes() == original_claim
