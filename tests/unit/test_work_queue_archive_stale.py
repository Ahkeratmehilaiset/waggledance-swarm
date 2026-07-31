from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

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


def test_legacy_tokenless_expiry_anchors_to_claimed_at_and_lease(
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
                "summary": "legacy expiry anchor",
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
                "lease_seconds": 60,
                "claim_lease_expires_utc": "2099-01-01T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    archived = archive_stale_claims(
        bridge_root=bridge,
        now_utc=_now(),
        max_age_seconds=3600,
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

    listed = list_claims(bridge_root=bridge)
    assert len(listed) == 1
    assert listed[0].lease_seconds == 0

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

    claims = list_claims(bridge_root=bridge)
    assert len(claims) == 1
    assert claims[0].lease_seconds == 0
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

    claims = list_claims(bridge_root=bridge)
    assert len(claims) == 1
    assert claims[0].owner_pid == 0
    archived = archive_stale_claims(
        bridge_root=bridge,
        now_utc=_now(),
        max_age_seconds=3600,
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

    listed = list_claims(bridge_root=bridge)
    assert len(listed) == 1
    assert listed[0].write_scope == ()
    assert listed[0].capabilities == ()
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
    original_write_text = Path.write_text

    def fail_archive_temp_write(
        path: Path,
        data: str,
        *args,
        **kwargs,
    ) -> int:
        if (
            path.parent.name == "done"
            and ".stale_lease.json.tmp." in path.name
        ):
            with path.open("w", encoding="utf-8") as handle:
                handle.write(data[:20])
            raise OSError("injected archive temp write failure")
        return original_write_text(path, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_archive_temp_write)

    with pytest.raises(OSError, match="injected archive temp write failure"):
        archive_stale_claims(
            bridge_root=bridge,
            now_utc=_stale_now(),
            max_age_seconds=60,
            apply=True,
        )

    assert claim_path.is_file()
    done_dir = bridge / "work_queue" / "done"
    assert not list(done_dir.glob("*.stale_lease.json"))
    assert not list(done_dir.glob("*.tmp.*"))


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
