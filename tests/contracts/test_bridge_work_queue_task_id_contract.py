# SPDX-License-Identifier: BUSL-1.1
"""Cross-surface task_id contract for bridge events and work_queue claims."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from waggledance.core.bridge_event_schema import validate_event
from waggledance.core.work_queue import claim_task, release_task


@pytest.fixture(autouse=True)
def _valid_work_queue_owner_context(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_valid_owner_context(monkeypatch)


def _set_valid_owner_context(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_BRIDGE_AGENT", raising=False)
    monkeypatch.delenv("AGENT_BRIDGE_RUN_ID", raising=False)
    monkeypatch.setenv("AGENT_BRIDGE_SESSION_ID", "pytest-session")
    monkeypatch.setenv("AGENT_BRIDGE_OWNER_SESSION_ID", "pytest-session")
    monkeypatch.setenv("AGENT_BRIDGE_OWNER_TOKEN", "a" * 64)
    monkeypatch.setenv("AGENT_BRIDGE_OWNER_PID", str(os.getpid()))
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


def _bridge_event(task_id: str) -> dict[str, object]:
    return {
        "ts_utc": "2026-06-13T04:20:00Z",
        "agent": "codex-tools-1",
        "type": "claim",
        "task_id": task_id,
        "status": "active",
        "severity": "",
        "to": "",
        "message": "Claim bridge-compatible task.",
        "paths": [],
        "write_scope": [],
        "run_id": "codex-tools-1-20260601T113511Z",
        "pid": 12345,
        "cwd": "C:\\Python\\project2-master",
        "payload": {},
    }


def test_bridge_namespaced_task_id_is_work_queue_claimable(
    tmp_path: Path,
) -> None:
    task_id = "codex-tools-1/magma-share-admission-status-bridge-template-20260613"
    bridge = tmp_path / ".agent-bridge"

    event = validate_event(_bridge_event(task_id))
    claim = claim_task(
        agent="codex-tools-1",
        task_id=event.task_id,
        summary="same task id as bridge event",
        bridge_root=bridge,
    )
    release = release_task(
        agent="codex-tools-1",
        task_id=event.task_id,
        release_status="done",
        bridge_root=bridge,
    )

    assert claim.task_id == task_id
    assert release.task_id == task_id
    assert not (bridge / "work_queue" / "claims" / "codex-tools-1").exists()
